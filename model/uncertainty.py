"""Полоса неопределённости (§9), суждения по цене ошибки, обратный DCF (§11),
«что даст отчёт» и нейтральная маржа (§12) — docs/MODEL.md.

**Полоса.** Оси — суждения книги `valuation.uncertainty.axes` (`value`,
`shift`, `dict`). Положение оси s — треугольное на [−1; 1] с модой 0
(`tri_s`); значение оси: книга + |s|·(конец − книга), конец — `high` при s > 0,
`low` при s < 0. Выборка — латинский гиперкуб по точному алгоритму §9
(`lhs`): для каждой оси по порядку книги перестановка страт, затем n чисел
`rng.random()` этой оси. Каждый прогон — вся сетка на изменённой книге: низ и
верх (`model.grid.layer_prices`), центр = низ + λ·(верх − низ).

**Детерминизм.** Прогон — чистая функция книги, фактов и своей строки s.
Пул процессов (`X5_WORKERS`, по умолчанию — ядра машины, не больше
`MAX_WORKERS`) считает прогоны кусками по порядку и склеивает в том же
порядке, поэтому числа бит в бит не зависят от числа процессов. Рабочий —
чистый интерпретатор (`spawn`) с кодом с диска.

**Подвыборка и уточнение (§11, §12).** Поиск значения суждения (обратный DCF,
нейтральная маржа) — бисекцией на первых `valuation.reverse_dcf.subsample`
прогонах того же гиперкуба (общие случайные числа) плюс сдвиг δ = медиана
полной полосы − медиана подвыборки на книге; затем 1–2 шага секущей на ПОЛНОЙ
полосе (первый — по наклону подвыборки в конце бисекции). Печатается
достигнутая невязка полной полосы. Таблица «что даст отчёт» — медианы полной
полосы.

Единицы: цена — ₽ на акцию; ставки и доли — доли единицы.
"""

from __future__ import annotations

import atexit
import datetime as dt
import math
import os
import pickle
import random
import sys
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from multiprocessing import get_context, parent_process
from typing import Any, Callable

from model.book import add_observation, blend_weights, get_node, override
from model.facts import CoreFacts
from model.grid import Grid, evaluate, layer_prices

fsum = math.fsum

# Квантили печати полосы (§9): 10/25/50/75/90.
QUANTILES = {"p10": 0.10, "p25": 0.25, "median": 0.50, "p75": 0.75, "p90": 0.90}
# Бисекция обратного DCF и нейтральной маржи (§11, §12): предел шагов и стоп по цене, ₽.
BISECTION_STEPS = 40
REVERSE_TOL_RUB = 5
NEUTRAL_TOL_RUB = 2
# Уточнение на полной полосе (§11, §12): шагов после первой проверки решения подвыборки.
SECANT_STEPS = 2
# Отрезок нейтральной маржи — демонстрационные значения ± 0,01 (§12).
NEUTRAL_PAD = 0.01
# Наклон медианы «на 0,1 п.п. маржи» (§12).
PER_TENTH_PP = 0.001
# Быстрая сборка (не в выпуск): прогонов полосы и подвыборки.
FAST_DRAWS = 200
FAST_SUBSAMPLE = 40

WORKERS_ENV = "X5_WORKERS"
MAX_WORKERS = 16
# Меньше прогонов в вызове — последовательно: пересылка дороже выигрыша.
PARALLEL_MIN_DRAWS = 64
# Кусков на рабочего: ядра неравные, мелкие куски выравнивают нагрузку.
CHUNKS_PER_WORKER = 3


# ------------------------------------------------------------------- оси


@dataclass(frozen=True)
class Axis:
    """Ось полосы (§9): суждение книги с диапазоном."""

    index: int
    name: str
    kind: str                   # value | shift | dict
    paths: tuple
    low: Any
    high: Any


def band_axes(A: dict) -> tuple[Axis, ...]:
    """Оси полосы в порядке книги."""
    return tuple(Axis(index=j, name=ax["name"], kind=ax["kind"], paths=tuple(ax["paths"]),
                      low=ax["low"], high=ax["high"])
                 for j, ax in enumerate(A["valuation"]["uncertainty"]["axes"]))


def tri_s(u: float) -> float:
    """Положение оси: треугольное на [−1; 1] с модой 0 (§9)."""
    return math.sqrt(2.0 * u) - 1.0 if u < 0.5 else 1.0 - math.sqrt(2.0 * (1.0 - u))


def lhs(n: int, k: int, seed: int) -> list[list[float]]:
    """Латинский гиперкуб u[i][j] ровно по §9: для каждой оси j по порядку книги
    перестановка страт `rng.shuffle`, затем для i = 0..n−1 — `rng.random()`."""
    rng = random.Random(seed)
    u = [[0.0] * k for _ in range(n)]
    for j in range(k):
        perm = list(range(n))
        rng.shuffle(perm)
        for i in range(n):
            u[i][j] = (perm[i] + rng.random()) / n
    return u


def draw_positions(n: int, k: int, seed: int) -> list[tuple[float, ...]]:
    """Положения осей s[i][j] прогонов полосы."""
    return [tuple(tri_s(x) for x in row) for row in lhs(n, k, seed)]


def axis_book(A: dict, axis: Axis) -> Any:
    """Значение оси в книге: число (`value`, по первому пути), 0 (`shift`), веса (`dict`)."""
    if axis.kind == "shift":
        return 0.0
    node = get_node(A, axis.paths[0])
    return dict(node) if axis.kind == "dict" else float(node)


def _book_values(A: dict, axis: Axis) -> list:
    """Значения книги по каждому пути оси (до любых подмен прогона)."""
    if axis.kind == "shift":
        return [0.0] * len(axis.paths)
    return [get_node(A, p) for p in axis.paths]


def _apply(B: dict, axis: Axis, book: list, s: float) -> dict:
    """Книга `B` с осью в положении s: книга + |s|·(конец − книга)."""
    end = axis.high if s > 0 else axis.low
    w = abs(s)
    if axis.kind == "shift":
        return override(B, axis.paths, "shift", w * float(end))
    for path, base in zip(axis.paths, book):
        if axis.kind == "dict":
            B = override(B, [path], "dict", blend_weights(base, end, w))
        else:
            B = override(B, [path], "value", float(base) + w * (float(end) - float(base)))
    return B


def trial_book(A: dict, axes, positions) -> dict:
    """Книга прогона: все оси в своих положениях (значения книги — из `A`)."""
    books = [_book_values(A, ax) for ax in axes]
    B = A
    for ax, book, s in zip(axes, books, positions):
        B = _apply(B, ax, book, s)
    return B


def at_end(A: dict, axis: Axis, end: str) -> dict:
    """Книга с осью на конце `low` или `high`, остальное — книга."""
    return trial_book(A, (axis,), (-1.0 if end == "low" else 1.0,))


# -------------------------------------------------------------- прогоны


def _run_rows(B: dict, cf: CoreFacts, axes, rows, valuation_date, market_price
              ) -> list[tuple[float, float]]:
    """(низ, верх) прогонов по порядку строк `rows`."""
    return [layer_prices(trial_book(B, axes, s), cf, valuation_date=valuation_date,
                         market_price=market_price) for s in rows]


def workers() -> int:
    """Число процессов полосы: `X5_WORKERS` (целое ≥ 1 или `auto`); auto — ядра
    машины, не больше `MAX_WORKERS`. 1 — последовательно."""
    raw = os.environ.get(WORKERS_ENV, "").strip().lower()
    if raw in ("", "auto"):
        return max(1, min(os.cpu_count() or 1, MAX_WORKERS))
    if not raw.isdigit() or int(raw) < 1:
        raise ValueError(f"{WORKERS_ENV}={raw!r}: нужно целое ≥ 1 или auto")
    return min(int(raw), MAX_WORKERS)


_POOL: dict = {"executor": None, "workers": 0, "failed": False}


def _executor(k: int) -> ProcessPoolExecutor:
    if _POOL["executor"] is None or _POOL["workers"] != k:
        close_pool()
        _POOL.update(executor=ProcessPoolExecutor(max_workers=k, mp_context=get_context("spawn")),
                     workers=k)
    return _POOL["executor"]


def close_pool() -> None:
    """Закрывает пул полосы (и при выходе из процесса)."""
    executor, _POOL["executor"], _POOL["workers"] = _POOL["executor"], None, 0
    if executor is not None:
        executor.shutdown(wait=True, cancel_futures=True)


atexit.register(close_pool)


def _chunk(blob: bytes) -> list[tuple[float, float]]:
    """Кусок прогонов в рабочем процессе: входы приходят одним pickle."""
    B, cf, axes, rows, valuation_date, market_price = pickle.loads(blob)
    return _run_rows(B, cf, axes, rows, valuation_date, market_price)


def run_draws(B: dict, cf: CoreFacts, axes, rows, *, valuation_date=None, market_price=None,
              n_workers: int | None = None, min_parallel: int = PARALLEL_MIN_DRAWS
              ) -> list[tuple[float, float]]:
    """(низ, верх) прогонов: пул процессов кусками по порядку строк или здесь же.

    Результат не зависит от числа процессов: каждый прогон — функция своих
    входов, куски склеиваются в порядке строк. Пул недоступен — сообщение в
    stderr и последовательный расчёт до конца процесса.
    """
    rows = [tuple(r) for r in rows]
    k = workers() if n_workers is None else max(1, int(n_workers))
    if k == 1 or len(rows) < min_parallel or parent_process() is not None or _POOL["failed"]:
        return _run_rows(B, cf, axes, rows, valuation_date, market_price)
    size = -(-len(rows) // (k * CHUNKS_PER_WORKER))
    parts = [rows[i:i + size] for i in range(0, len(rows), size)]
    blobs = [pickle.dumps((B, cf, tuple(axes), part, valuation_date, market_price),
                          protocol=pickle.HIGHEST_PROTOCOL) for part in parts]
    try:
        done = list(_executor(k).map(_chunk, blobs))
    except (BrokenProcessPool, OSError, MemoryError, pickle.PicklingError) as exc:
        _POOL["failed"] = True
        close_pool()
        print(f"пул процессов полосы недоступен ({type(exc).__name__}: {exc}) — считаю "
              "последовательно", file=sys.stderr)
        return _run_rows(B, cf, axes, rows, valuation_date, market_price)
    return [x for part in done for x in part]


# ----------------------------------------------------------- статистика


def quantile7(sorted_values, q: float) -> float:
    """Квантиль типа 7 (линейная интерполяция): h = (n − 1)·q."""
    n = len(sorted_values)
    h = (n - 1) * q
    lo = math.floor(h)
    hi = min(lo + 1, n - 1)
    return sorted_values[lo] + (h - lo) * (sorted_values[hi] - sorted_values[lo])


def median(values) -> float:
    return quantile7(sorted(values), QUANTILES["median"])


def centers(low, high, lam: float) -> list[float]:
    """Центр прогона при λ: низ + λ·(верх − низ)."""
    return [a + lam * (b - a) for a, b in zip(low, high)]


def band_stats(low, high, lam: float, market_price: float) -> dict:
    """Квантили 10/25/50/75/90 центра, среднее и P(центр < рынка)."""
    c = centers(low, high, lam)
    s = sorted(c)
    out = {name: quantile7(s, q) for name, q in QUANTILES.items()}
    out["mean"] = fsum(c) / len(c)
    out["p_below"] = sum(1 for x in c if x < market_price) / len(c)
    return out


def ranks(values) -> list[float]:
    """Ранги 1..n, равным — средний ранг."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        mean_rank = (i + j) / 2.0 + 1.0
        for t in range(i, j + 1):
            out[order[t]] = mean_rank
        i = j + 1
    return out


def pearson(x, y) -> float:
    n = len(x)
    mx, my = fsum(x) / n, fsum(y) / n
    sxy = fsum((a - mx) * (b - my) for a, b in zip(x, y))
    sxx = fsum((a - mx) ** 2 for a in x)
    syy = fsum((b - my) ** 2 for b in y)
    return sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else 0.0


def contributions(axes, positions, center) -> list[dict]:
    """Вклад оси (§9): доля квадрата ранговой корреляции (Спирмен) положения оси
    с центром в сумме квадратов по осям. Положение s монотонно по значению оси
    (и единственный скаляр у оси весов), поэтому ранги те же."""
    rc = ranks(center)
    rho = [pearson(ranks([row[j] for row in positions]), rc) for j in range(len(axes))]
    total = fsum(r * r for r in rho)
    return [{"axis": ax.name, "index": ax.index, "rank_corr": r,
             "share": r * r / total if total > 0 else 0.0} for ax, r in zip(axes, rho)]


# ---------------------------------------------------------------- полоса


@dataclass(frozen=True)
class Band:
    """Полоса §9: положения осей и (низ, верх) каждого прогона на книге."""

    n: int
    seed: int
    lam: float
    market_price: float
    axes: tuple
    positions: tuple            # s[i][j]
    low: tuple
    high: tuple

    @property
    def center(self) -> list[float]:
        return centers(self.low, self.high, self.lam)

    def stats(self, lam: float | None = None) -> dict:
        return band_stats(self.low, self.high, self.lam if lam is None else lam, self.market_price)

    def contributions(self) -> list[dict]:
        return contributions(self.axes, self.positions, self.center)


def _date(v) -> dt.date | None:
    if v is None or isinstance(v, dt.date):
        return v
    return dt.date.fromisoformat(str(v))


def band(A: dict, cf: CoreFacts, *, valuation_date=None, market_price=None,
         draws: int | None = None, fast: bool = False, n_workers: int | None = None,
         min_parallel: int = PARALLEL_MIN_DRAWS) -> Band:
    """Полоса §9 на книге `A`: `draws` прогонов (по умолчанию из книги; fast — 200)."""
    U = A["valuation"]["uncertainty"]
    n = int(draws if draws is not None else (FAST_DRAWS if fast else U["draws"]))
    seed = int(U["seed"])
    axes = band_axes(A)
    positions = draw_positions(n, len(axes), seed)
    v = _date(valuation_date) or _date(A["meta"]["valuation_date"])
    mp = float(market_price if market_price is not None else A["meta"]["market_price"])
    pairs = run_draws(A, cf, axes, positions, valuation_date=v, market_price=mp,
                      n_workers=n_workers, min_parallel=min_parallel)
    return Band(n=n, seed=seed, lam=float(A["joint"]["lambda"]), market_price=mp, axes=axes,
                positions=tuple(positions), low=tuple(p[0] for p in pairs),
                high=tuple(p[1] for p in pairs))


# ------------------------------------------------ суждения по цене ошибки


def judgements(A: dict, cf: CoreFacts, band_result: Band, *, valuation_date=None,
               market_price=None) -> list[dict]:
    """Таблица «суждения по цене ошибки» (§9): точка при low и high оси, размах, вклад."""
    v = _date(valuation_date) or _date(A["meta"]["valuation_date"])
    mp = float(market_price if market_price is not None else A["meta"]["market_price"])
    lam = band_result.lam
    share = {c["index"]: c["share"] for c in band_result.contributions()}
    out = []
    for ax in band_result.axes:
        prices = {}
        for end in ("low", "high"):
            lo, hi = layer_prices(at_end(A, ax, end), cf, valuation_date=v, market_price=mp)
            prices[end] = lo + lam * (hi - lo)
        out.append({"index": ax.index, "name": ax.name, "kind": ax.kind, "paths": list(ax.paths),
                    "book": axis_book(A, ax), "low": ax.low, "high": ax.high,
                    "price_low": prices["low"], "price_high": prices["high"],
                    "swing": abs(prices["high"] - prices["low"]), "share": share[ax.index]})
    return out


# ----------------------------------------------------------- бисекция


def bisect(f: Callable[[float], float], a: float, b: float, tol: float
           ) -> tuple[float | None, int, float | None]:
    """Корень f на [a; b]: бисекция ≤ 40 шагов, стоп при |f| ≤ tol (§11, §12).

    Возвращает (корень или None — «недостижимо в поиске», число вычислений f,
    наклон f по последней скобке — хорда (f(b) − f(a))/(b − a)).
    """
    fa, fb = f(a), f(b)
    calls = 2
    slope = (fb - fa) / (b - a) if b != a else None
    if abs(fa) <= tol:
        return a, calls, slope
    if abs(fb) <= tol:
        return b, calls, slope
    if (fa > 0) == (fb > 0):
        return None, calls, slope
    for _ in range(BISECTION_STEPS):
        m = (a + b) / 2.0
        fm = f(m)
        calls += 1
        if abs(fm) <= tol:
            # наклон — хорда от m до конца скобки с другим знаком f
            other, fo = (a, fa) if (fm > 0) != (fa > 0) else (b, fb)
            return m, calls, (fm - fo) / (m - other)
        if (fm > 0) == (fa > 0):
            a, fa = m, fm
        else:
            b, fb = m, fm
    return (a + b) / 2.0, calls, (fb - fa) / (b - a)


def secant_refine(f: Callable[[float], float], x0: float, slope: float | None, lo: float,
                  hi: float, tol: float) -> tuple[float, float, int]:
    """Уточнение корня f от x0 (§11, §12): f(x0); шаг по локальному наклону `slope`;
    затем секущая — всего не больше SECANT_STEPS шагов, стоп при |f| ≤ tol. Шаги
    не выходят из [lo; hi]. Возвращает (лучшая точка, f в ней, вычислений f)."""
    fx = f(x0)
    calls, best = 1, (x0, fx)
    if abs(fx) <= tol or not slope:
        return best[0], best[1], calls
    xa, fa = x0, fx
    xb = min(hi, max(lo, x0 - fx / slope))
    for _ in range(SECANT_STEPS):
        if xb == xa:
            break
        fb = f(xb)
        calls += 1
        if abs(fb) < abs(best[1]):
            best = (xb, fb)
        if abs(fb) <= tol or fb == fa:
            break
        xa, fa, xb = xb, fb, min(hi, max(lo, xb - fb * (xb - xa) / (fb - fa)))
    return best[0], best[1], calls


# ------------------------------------------------------ медиана подвыборки


class Subsample:
    """Медиана центра на первых m прогонах гиперкуба полосы со сдвигом δ (§11) и на
    всех прогонах (уточнение §11, таблица и уточнение §12).

    `at(B, fixed)` — медиана подвыборки на книге `B`, где оси полосы с номерами
    `fixed` не разыгрываются (суждение зафиксировано подменой в `B`), плюс δ.
    На книге без подмен `at` = медиана полной полосы. `full(B, fixed)` — медиана
    всех прогонов (низ и верх округлены, как у заголовка: `round_draws`).
    """

    def __init__(self, band_result: Band, full_median: float, cf: CoreFacts, *,
                 size: int, valuation_date, market_price, n_workers=None,
                 min_parallel: int = PARALLEL_MIN_DRAWS, round_draws: int | None = None):
        self.band = band_result
        self.m = max(1, min(int(size), band_result.n))
        self.cf = cf
        self.v, self.mp = valuation_date, market_price
        self.n_workers, self.min_parallel = n_workers, min_parallel
        self.round_draws = round_draws
        self.base = median(centers(band_result.low[:self.m], band_result.high[:self.m],
                                   band_result.lam))
        self.full_median = full_median
        self.delta = full_median - self.base
        self.evaluations = 0
        self.full_evaluations = 0

    def _median(self, B: dict, fixed: frozenset, rows, digits: int | None) -> float:
        keep = [j for j, ax in enumerate(self.band.axes) if ax.index not in fixed]
        axes = [self.band.axes[j] for j in keep]
        rows = [[row[j] for j in keep] for row in rows]
        pairs = run_draws(B, self.cf, axes, rows, valuation_date=self.v, market_price=self.mp,
                          n_workers=self.n_workers, min_parallel=self.min_parallel)
        low, high = [p[0] for p in pairs], [p[1] for p in pairs]
        if digits is not None:
            low, high = [round(x, digits) for x in low], [round(x, digits) for x in high]
        return median(centers(low, high, self.band.lam))

    def raw(self, B: dict, fixed: frozenset = frozenset()) -> float:
        """Медиана подвыборки без сдвига (прогоны не округляются, как и при расчёте δ)."""
        self.evaluations += 1
        return self._median(B, fixed, self.band.positions[:self.m], None)

    def at(self, B: dict, fixed: frozenset = frozenset()) -> float:
        return self.raw(B, fixed) + self.delta

    def full(self, B: dict, fixed: frozenset = frozenset()) -> float:
        """Медиана всех прогонов полосы на книге `B` (оси `fixed` не разыгрываются)."""
        self.full_evaluations += 1
        return self._median(B, fixed, self.band.positions, self.round_draws)


# ---------------------------------------------------------- обратный DCF


def _matched(band_result: Band, raxis: dict) -> frozenset:
    """Оси полосы, которые обратный DCF фиксирует: те же пути, что у его оси."""
    paths = set(raxis["paths"])
    return frozenset(ax.index for ax in band_result.axes if set(ax.paths) == paths)


def _point(A: dict, cf: CoreFacts, v, mp, lam: float) -> float:
    lo, hi = layer_prices(A, cf, valuation_date=v, market_price=mp)
    return lo + lam * (hi - lo)


def reverse_dcf(A: dict, cf: CoreFacts, sub: Subsample, *, valuation_date=None,
                market_price=None) -> list[dict]:
    """Что заложено в цену (§11): значение одного суждения, при котором медиана равна
    рыночной цене. Поиск — бисекция на подвыборке + δ (`search_value`), затем
    уточнение секущей на полной полосе; `gap_full` — достигнутая невязка полной
    полосы, ₽. Для справки — то же для точки (без полосы)."""
    v = _date(valuation_date) or _date(A["meta"]["valuation_date"])
    mp = float(market_price if market_price is not None else A["meta"]["market_price"])
    lam = sub.band.lam
    out = []
    for raxis in A["valuation"]["reverse_dcf"]["axes"]:
        kind, paths = raxis["kind"], list(raxis["paths"])
        fixed = _matched(sub.band, raxis)
        before, before_full = sub.evaluations, sub.full_evaluations
        a, b = float(raxis["search"][0]), float(raxis["search"][1])

        def book_at(x: float, kind=kind, paths=paths) -> dict:
            return override(A, paths, kind, x)

        search, _, slope = bisect(lambda x: sub.at(book_at(x), fixed) - mp, a, b,
                                  REVERSE_TOL_RUB)
        solved = gap = None
        if search is not None:
            solved, gap, _ = secant_refine(lambda x: sub.full(book_at(x), fixed) - mp, search,
                                           slope, a, b, REVERSE_TOL_RUB)
        point_solved, _, _ = bisect(lambda x: _point(book_at(x), cf, v, mp, lam) - mp, a, b,
                                    REVERSE_TOL_RUB)
        book = 0.0 if kind == "shift" else float(get_node(A, paths[0]))
        lo, hi = (float(x) for x in raxis["range"])
        out.append({"name": raxis["name"], "unit": raxis["unit"], "kind": kind, "paths": paths,
                    "book": book, "solved": solved,
                    "delta": None if solved is None else solved - book,
                    "in_range": solved is not None and lo <= solved <= hi,
                    "range": [lo, hi], "search": [a, b],
                    "status": "solved" if solved is not None else "unreachable",
                    "search_value": search, "gap_full": gap,
                    "point_solved": point_solved,
                    "point_status": "solved" if point_solved is not None else "unreachable",
                    "fixed_axes": sorted(fixed), "evaluations": sub.evaluations - before,
                    "evaluations_full": sub.full_evaluations - before_full})
    return out


# ------------------------------------------------------- что даст отчёт


def with_fact(A: dict, period: str, value: float) -> dict:
    """Книга с фактом маржи полугодия (se = 0); частичное наблюдение того же
    полугодия, если было, заменяется фактом."""
    update = A["joint"]["regime_update"]
    kept = [o for o in update["observations"] if o["period"] != period]
    if len(kept) != len(update["observations"]):
        A = {**A, "joint": {**A["joint"], "regime_update": {**update, "observations": kept}}}
    return add_observation(A, period, value, 0.0)


def expectation(grid: Grid, period: str, layer: str = "analytical") -> dict:
    """Ожидание модели на полугодие (§12): средние маржа, выручка и скорр. EBITDA
    ядра по клеткам слоя (вероятности клеток); маржа по режимам — условная."""
    i = grid.ctx.P.index(period)
    cells = [c for c in grid.cells if c.p[layer]]
    rows = [(c.p[layer], c.result.rows[i], c.result.cell.regime) for c in cells]
    out = {"period": period,
           "margin": fsum(p * r.margin for p, r, _ in rows),
           "revenue": fsum(p * r.revenue for p, r, _ in rows),
           "adj_ebitda": fsum(p * r.adj_ebitda for p, r, _ in rows)}
    by = []
    for regime in grid.regime_posterior:
        part = [(p, r) for p, r, g in rows if g == regime]
        mass = fsum(p for p, _ in part)
        by.append({"regime": regime,
                   "margin": fsum(p * r.margin for p, r in part) / mass if mass > 0 else None})
    out["by_regime"] = by
    return out


def ols_slope(xs, ys) -> float | None:
    n = len(xs)
    if n < 2:
        return None
    mx, my = fsum(xs) / n, fsum(ys) / n
    sxx = fsum((x - mx) ** 2 for x in xs)
    return fsum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx if sxx > 0 else None


def next_report(A: dict, cf: CoreFacts, grid: Grid, sub: Subsample, period: str | None, *,
                valuation_date=None, market_price=None) -> dict:
    """«Что даст отчёт» (§12): точка и медиана полной полосы при факте маржи открытого
    полугодия из `demo_values`, их изменение, вероятности режимов; нейтральная маржа
    медианы (бисекция на подвыборке, уточнение секущей на полной полосе, стоп 2 ₽;
    `neutral_gap` — достигнутая невязка) и точки (бисекция, стоп 2 ₽)."""
    if period is None:
        return {"period": None, "table": [], "neutral": {"median": None, "point": None},
                "neutral_gap": {"median": None}, "rub_per_01pp": None}
    v = _date(valuation_date) or _date(A["meta"]["valuation_date"])
    mp = float(market_price if market_price is not None else A["meta"]["market_price"])
    point0, median0 = grid.point.central, sub.full_median
    before, before_full = sub.evaluations, sub.full_evaluations
    values = [float(x) for x in A["valuation"]["next_report"]["demo_values"]]
    table = []
    for m in values:
        g = evaluate(with_fact(A, period, m), cf, valuation_date=v, market_price=mp)
        med = sub.full(g.ctx.A)
        table.append({"margin": m, "point": g.point.central, "median": med,
                      "d_point": g.point.central - point0, "d_median": med - median0,
                      "posterior": dict(g.regime_posterior)})
    lam = sub.band.lam
    neutral, gap = {"median": None, "point": None}, None
    if values:
        a, b = min(values) - NEUTRAL_PAD, max(values) + NEUTRAL_PAD
        search, _, slope = bisect(lambda m: sub.raw(with_fact(A, period, m)) - sub.base,
                                  a, b, NEUTRAL_TOL_RUB)
        if search is not None:
            neutral["median"], gap, _ = secant_refine(
                lambda m: sub.full(with_fact(A, period, m)) - median0, search, slope, a, b,
                NEUTRAL_TOL_RUB)
        neutral["point"] = bisect(lambda m: _point(with_fact(A, period, m), cf, v, mp, lam) - point0,
                                  a, b, NEUTRAL_TOL_RUB)[0]
    slope = ols_slope([r["margin"] for r in table], [r["median"] for r in table])
    return {"period": period, "table": table, "neutral": neutral, "neutral_gap": {"median": gap},
            "rub_per_01pp": None if slope is None else slope * PER_TENTH_PP,
            "evaluations": sub.evaluations - before,
            "evaluations_full": sub.full_evaluations - before_full}


# ------------------------------------------------------------ всё вместе


@dataclass(frozen=True)
class Distribution:
    """Полоса и всё, что считается на её гиперкубе (для выпуска и таблиц книги)."""

    band: Band
    stats: dict                 # квантили, среднее, P(ниже рынка) при λ книги
    contributions: list
    judgements: list
    reverse_dcf: list
    next_report: dict
    subsample: int
    delta: float


def distribution(A: dict, cf: CoreFacts, grid: Grid, *, valuation_date=None, market_price=None,
                 fast: bool = False, n_workers: int | None = None, period: str | None = None,
                 full_median: float | None = None, round_draws: int | None = None,
                 draws: int | None = None, subsample: int | None = None,
                 min_parallel: int = PARALLEL_MIN_DRAWS) -> Distribution:
    """Полоса §9, суждения, обратный DCF §11 и «что даст отчёт» §12 одним вызовом.

    `round_draws` — знаков после запятой, до которых округляются низ и верх
    прогонов для статистик (как они лежат в выпуске: витрина пересчитывает
    полосу по ним же, и заголовок воспроизводится бит в бит).
    `period` — открытое полугодие (§12). `draws`, `subsample` — размеры вместо
    книги (проверки на малых выборках; в выпуск не идут).
    """
    v = _date(valuation_date) or _date(A["meta"]["valuation_date"])
    mp = float(market_price if market_price is not None else A["meta"]["market_price"])
    b = band(A, cf, valuation_date=v, market_price=mp, fast=fast, n_workers=n_workers,
             draws=draws, min_parallel=min_parallel)
    low, high = b.low, b.high
    if round_draws is not None:
        low = tuple(round(x, round_draws) for x in low)
        high = tuple(round(x, round_draws) for x in high)
    stats = band_stats(low, high, b.lam, mp)
    size = int(A["valuation"]["reverse_dcf"]["subsample"] if subsample is None else subsample)
    if fast:
        size = min(size, FAST_SUBSAMPLE)
    sub = Subsample(b, stats["median"] if full_median is None else full_median, cf, size=size,
                    valuation_date=v, market_price=mp, n_workers=n_workers,
                    min_parallel=min_parallel, round_draws=round_draws)
    return Distribution(
        band=b, stats=stats, contributions=b.contributions(),
        judgements=judgements(A, cf, b, valuation_date=v, market_price=mp),
        reverse_dcf=reverse_dcf(A, cf, sub, valuation_date=v, market_price=mp),
        next_report=next_report(A, cf, grid, sub, period, valuation_date=v, market_price=mp),
        subsample=sub.m, delta=sub.delta)

