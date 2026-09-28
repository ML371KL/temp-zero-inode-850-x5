"""Общие функции листа «Финансирование, мост, оценка, гейты» (область financing-valuation).

Только стандартная библиотека. Деньги — млрд ₽, ставки и доли — доли единицы (годовые),
если в имени не сказано «pct». Семантика ключей книги — docs/MODEL.md §4.9, §5, §7, §8, §9, §11, §13.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # pragma: no cover
    pass

HERE = Path(__file__).resolve().parent
INPUTS = HERE / "inputs"
REPO = HERE.parents[3]          # корень репозитория x5-850


def load(name: str) -> dict:
    return json.loads((INPUTS / name).read_text(encoding="utf-8"))


def v(node):
    """Значение узла {"v": …, "src": …} или само число."""
    return node["v"] if isinstance(node, dict) and "v" in node else node


def write_json(name: str, obj) -> None:
    (HERE / name).write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n",
                             encoding="utf-8", newline="\n")


class Report:
    """Печать в консоль и в файл `<имя>.out` (вывод листа хранится рядом со скриптом)."""

    def __init__(self, name: str):
        self.name, self.lines = name, []

    def __call__(self, *parts) -> None:
        s = " ".join(str(p) for p in parts)
        self.lines.append(s)
        print(s)

    def h(self, title: str) -> None:
        self("")
        self(title)
        self("-" * len(title))

    def save(self) -> None:
        (HERE / f"{self.name}.out").write_text("\n".join(self.lines) + "\n", encoding="utf-8",
                                               newline="\n")


def d(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def r4(x: float) -> float:
    return round(x, 4)


def r6(x: float) -> float:
    return round(x, 6)


# ---------------------------------------------------------------- полугодия
def half_bounds(p: str) -> tuple[dt.date, dt.date]:
    """Границы полугодия [начало; конец) — конец = первый день следующего."""
    y, h = int(p[:4]), int(p[-1])
    return (dt.date(y, 1, 1), dt.date(y, 7, 1)) if h == 1 else (dt.date(y, 7, 1), dt.date(y + 1, 1, 1))


def halves(first: str, last: str) -> list[str]:
    out, y, h = [], int(first[:4]), int(first[-1])
    while True:
        p = f"{y}H{h}"
        out.append(p)
        if p == last:
            return out
        y, h = (y, 2) if h == 1 else (y + 1, 1)


def overlap_years(a0: dt.date, a1: dt.date, b0: dt.date, b1: dt.date) -> float:
    """Длина пересечения [a0; a1) и [b0; b1) в годах (ACT/365)."""
    lo, hi = max(a0, b0), min(a1, b1)
    return max(0, (hi - lo).days) / 365.0


# ---------------------------------------------------------------- ключевая ставка
def key_rate_fn(changes: list[list]):
    """Ставка на дату по списку изменений [[дата, ставка %], …] (ставка действует с даты)."""
    pts = sorted((d(a), b / 100.0) for a, b in changes)

    def f(day: dt.date) -> float:
        rate = None
        for when, r in pts:
            if when <= day:
                rate = r
        if rate is None:
            raise ValueError(f"ключевая ставка до {pts[0][0]} не задана")
        return rate
    return f


def avg_key(f, a: dt.date, b: dt.date) -> float:
    """Средняя по календарным дням ставка на [a; b)."""
    days = (b - a).days
    return sum(f(a + dt.timedelta(i)) for i in range(days)) / days


# ---------------------------------------------------------------- кривая ОФЗ (КБД MOEX)
def zcyc_fn(p: dict):
    """КБД Московской биржи: Нельсон — Сигель + 9 гауссовых поправок; Y(t) — доля, годовое начисление.

    Формула проверяется против `yearyields` ISS той же даты (`zcyc_check`).
    """
    B1, B2, B3, T1 = p["B1"], p["B2"], p["B3"], p["T1"]
    g = [p[f"G{i}"] for i in range(1, 10)]
    a, b = [0.0, 0.6], [0.6]
    for i in range(1, 8):
        a.append(a[-1] + 0.6 * 1.6 ** i)
    for _ in range(1, 9):
        b.append(b[-1] * 1.6)

    def Y(t: float) -> float:
        G = B1 + (B2 + B3) * (T1 / t) * (1 - math.exp(-t / T1)) - B3 * math.exp(-t / T1)
        G += sum(g[i] * math.exp(-((t - a[i]) ** 2) / (b[i] ** 2)) for i in range(9))
        return math.exp(G / 10000) - 1
    return Y


def zcyc_load(date: str):
    raw = json.loads((INPUTS / "web" / f"zcyc_{date}.json").read_text(encoding="utf-8"))
    p = dict(zip(raw["params"]["columns"], raw["params"]["data"][0]))
    yy = {row[2]: row[3] / 100 for row in raw["yearyields"]["data"]}
    return p, yy


def zcyc_check(date: str) -> float:
    """Максимальное расхождение формулы с узлами ISS той же даты, п.п."""
    p, yy = zcyc_load(date)
    Y = zcyc_fn(p)
    return max(abs(Y(t) - y) for t, y in yy.items()) * 100


# ---------------------------------------------------------------- облигация по номиналу
def par_bond(coupon: float, freq: int, years: float) -> tuple[float, float]:
    """Эффективная доходность и дюрация Маколея (лет) облигации по номиналу до оферты."""
    y_eff = (1 + coupon / freq) ** freq - 1
    n = max(1, round(years * freq))
    c = coupon / freq
    per = (1 + y_eff) ** (1 / freq)
    pv = [(c + (1.0 if k == n else 0.0)) / per ** k for k in range(1, n + 1)]
    dur = sum((k / freq) * x for k, x in enumerate(pv, 1)) / sum(pv)
    return y_eff, dur


# ---------------------------------------------------------------- опцион (ставка 0)
def ncdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def put0(V: float, K: float, sigma: float, T: float) -> float:
    """Европейский пут Блэка — Шоулза при нулевой ставке (кредитный пут кредиторов на активы)."""
    if V <= 0:
        return K
    s = sigma * math.sqrt(T)
    d1 = (math.log(V / K) + 0.5 * s * s) / s
    return K * ncdf(-(d1 - s)) - V * ncdf(-d1)


# ---------------------------------------------------------------- статистика
def mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs)


def median(xs):
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


def quantile(xs, q):
    s = sorted(xs)
    h = (len(s) - 1) * q
    lo = math.floor(h)
    return s[lo] + (h - lo) * (s[min(lo + 1, len(s) - 1)] - s[lo])
