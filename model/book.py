"""Книга допущений: загрузка, линейка полугодий, траектории, кривая, подмены.

Книга (`data/assumptions/assumptions.yaml`; пока её нет —
`assumptions.draft.yaml`) читается целиком и проверяется закрытой схемой
(`model/book_schema.py`). Ни одно число книги не переписывается в код: ядро
берёт всё по именам ключей.

Книга после загрузки — только для чтения. Подмены для осей полосы и обратного
DCF делает `override`: он копирует блоки по пути подмены, остальные делит с
исходной книгой (копия дешёвая, 2 000 прогонов полосы не копируют книгу
целиком). Поэтому править словарь книги на месте нельзя — только `override`.
"""

from __future__ import annotations

import datetime as dt
import math
import os
import warnings
from pathlib import Path
from typing import Any, Iterable

import yaml

from model.book_schema import BookError, get_node, is_number, is_trajectory, validate_book

__all__ = ["BookError", "ROOT", "BOOK_DIR", "BOOK_YAML", "DRAFT_YAML", "default_book_path",
           "load_book", "today", "periods", "prev_period", "next_period", "prev_same_half",
           "period_index", "period_start", "period_end", "path_value", "trajectory",
           "interp_curve", "half_rate", "override", "blend_weights", "add_observation",
           "book_warnings", "get_node", "open_period", "period_of"]

ROOT = Path(__file__).resolve().parents[1]
BOOK_DIR = ROOT / "data" / "assumptions"
BOOK_YAML = BOOK_DIR / "assumptions.yaml"
DRAFT_YAML = BOOK_DIR / "assumptions.draft.yaml"

# Месяц начала второго полугодия (календарь, не допущение).
H2_START_MONTH = 7
# Московское время — UTC+3 круглый год (перехода на летнее время нет с 2014 г.).
MOSCOW = dt.timezone(dt.timedelta(hours=3), "MSK")
# Срок, к которому бескупонная кривая сходится к LT после последнего узла,
# лет (docs/MODEL.md §0.2: «за 10 лет — линейный сход к LT к 15 годам»).
CURVE_LT_TENOR = 15


class BookFallbackWarning(UserWarning):
    """Книги `assumptions.yaml` нет — читается черновик."""


def default_book_path() -> Path:
    """`assumptions.yaml`, а пока его нет — черновик (с предупреждением)."""
    if BOOK_YAML.exists():
        return BOOK_YAML
    warnings.warn(f"книги {BOOK_YAML.relative_to(ROOT).as_posix()} нет — читается черновик "
                  f"{DRAFT_YAML.relative_to(ROOT).as_posix()}", BookFallbackWarning, stacklevel=2)
    return DRAFT_YAML


def load_book(path: Path | str | None = None) -> dict:
    """Читает книгу (YAML) и проверяет её закрытой схемой; отказ — `BookError`."""
    path = Path(path) if path is not None else default_book_path()
    A = yaml.safe_load(path.read_text(encoding="utf-8"))
    validate_book(A)
    return A


def today() -> dt.date:
    """Сегодняшний день по Москве (UTC+3 без перехода на летнее время — фиксированное
    смещение, без базы часовых поясов): дата не зависит от пояса машины, на которой идёт
    выпуск. `FAKE_TODAY=ГГГГ-ММ-ДД` подменяет его (прогон «в будущем»)."""
    fake = os.environ.get("FAKE_TODAY")
    if fake:
        return dt.date.fromisoformat(fake)
    return dt.datetime.now(MOSCOW).date()


# ------------------------------------------------------------ линейка полугодий


def period_index(p: str) -> int:
    """Сквозной номер полугодия: соседние полугодия отличаются на 1."""
    return 2 * int(p[:4]) + int(p[5]) - 1


def _from_index(i: int) -> str:
    return f"{i // 2}H{i % 2 + 1}"


def periods(first: str, last: str) -> list[str]:
    """Полугодия от `first` до `last` включительно."""
    return [_from_index(i) for i in range(period_index(first), period_index(last) + 1)]


def prev_period(p: str) -> str:
    return _from_index(period_index(p) - 1)


def next_period(p: str) -> str:
    return _from_index(period_index(p) + 1)


def prev_same_half(p: str) -> str:
    """То же полугодие год назад (p − 2)."""
    return _from_index(period_index(p) - 2)


def period_start(p: str) -> dt.date:
    """Первый день полугодия."""
    return dt.date(int(p[:4]), 1 if p[5] == "1" else H2_START_MONTH, 1)


def period_end(p: str) -> dt.date:
    """Последний день полугодия."""
    return period_start(next_period(p)) - dt.timedelta(days=1)


def period_of(day: dt.date) -> str:
    """Полугодие, в котором лежит дата."""
    return f"{day.year}H{1 if day.month < H2_START_MONTH else 2}"


# ------------------------------------------------------------------ траектории

# Память значений траекторий: ключ — СОДЕРЖИМОЕ траектории и периоды, поэтому
# подменённая копия книги промахивается, а неизменённые блоки попадают.
_MEMO: dict[tuple, tuple[float, ...]] = {}
_MEMO_LIMIT = 50_000


def path_value(spec: dict, p: str) -> float:
    """Значение траектории в полугодии `p` (docs/MODEL.md §0.1).

    1) точный ключ полугодия; 2) ключ года; 3) линейная интерполяция между
    ближайшими заданными годами; 4) после последнего заданного года — линейный
    сход к `LT` к году `LT_from` (с `LT_from` — `LT`); без `LT` — последнее
    значение. До первого заданного года — значение первого года. Без ключей
    годов: `LT`, если есть, иначе значение последнего ключа полугодия.
    """
    if p in spec:
        return float(spec[p])
    year = int(p[:4])
    ykey = str(year)
    if ykey in spec:
        return float(spec[ykey])
    points = sorted((int(k), float(v)) for k, v in spec.items() if len(k) == len(ykey)
                    and k.isdigit())
    lt = spec.get("LT")
    if not points:
        if lt is not None:
            return float(lt)
        halves = sorted((k for k in spec if k not in ("LT", "LT_from")), key=period_index)
        return float(spec[halves[-1]])
    if year <= points[0][0]:
        return points[0][1]
    last_year, last_value = points[-1]
    if year > last_year:
        if lt is None:
            return last_value
        lt_from = int(spec.get("LT_from", last_year + 1))
        if year >= lt_from:
            return float(lt)
        return last_value + (float(lt) - last_value) * (year - last_year) / (lt_from - last_year)
    for (y0, v0), (y1, v1) in zip(points, points[1:]):
        if y0 <= year <= y1:
            return v0 + (v1 - v0) * (year - y0) / (y1 - y0)
    raise AssertionError("недостижимо")  # pragma: no cover


def trajectory(spec: dict, P: Iterable[str]) -> tuple[float, ...]:
    """Значения траектории на полугодиях `P` (с памятью по содержимому)."""
    P = tuple(P)
    key = (tuple(spec.items()), P)
    hit = _MEMO.get(key)
    if hit is None:
        if len(_MEMO) > _MEMO_LIMIT:
            _MEMO.clear()
        hit = _MEMO[key] = tuple(path_value(spec, p) for p in P)
    return hit


def interp_curve(curve: dict, tenor: float) -> float:
    """Бескупонная ставка мира на срок `tenor` лет (docs/MODEL.md §0.2).

    До первого узла — первый узел; между узлами — линейно по сроку; за
    последним узлом — линейный сход к `LT` к `CURVE_LT_TENOR` годам, дальше `LT`.
    """
    nodes = sorted((float(k), float(v)) for k, v in curve.items() if k != "LT")
    if tenor <= nodes[0][0]:
        return nodes[0][1]
    for (t0, z0), (t1, z1) in zip(nodes, nodes[1:]):
        if tenor <= t1:
            return z0 + (z1 - z0) * (tenor - t0) / (t1 - t0)
    t_last, z_last = nodes[-1]
    lt = float(curve["LT"])
    if tenor >= CURVE_LT_TENOR:
        return lt
    return z_last + (lt - z_last) * (tenor - t_last) / (CURVE_LT_TENOR - t_last)


def half_rate(annual: float) -> float:
    """Годовая ставка в полугодовую: (1 + r)^0,5 − 1 (корень, не r/2)."""
    return math.sqrt(1.0 + annual) - 1.0


# ------------------------------------------------------------------- подмены

KINDS = ("value", "shift", "dict")


def _replace(node: Any, parts: list[str], fn, path: str) -> Any:
    """Копия `node` с заменой узла по пути `parts` на `fn(узел)`; остальное — общее."""
    if not parts:
        return fn(node)
    if not isinstance(node, dict) or parts[0] not in node:
        raise BookError(f"подмена: пути {path} нет в книге")
    out = dict(node)
    out[parts[0]] = _replace(node[parts[0]], parts[1:], fn, path)
    return out


def _shifted(node: Any, delta: float, path: str) -> Any:
    if is_number(node):
        return float(node) + delta
    if is_trajectory(node):
        return {k: (v if k == "LT_from" else float(v) + delta) for k, v in node.items()}
    raise BookError(f"подмена shift: путь {path} ведёт не к числу и не к траектории")


def _valued(node: Any, value: float, path: str) -> float:
    if not is_number(node):
        raise BookError(f"подмена value: путь {path} ведёт не к числу")
    if not is_number(value):
        raise BookError(f"подмена value: {value!r} — не число")
    return float(value)


def _weights(node: Any, value: Any, path: str) -> dict:
    if (not isinstance(node, dict) or not isinstance(value, dict) or set(node) != set(value)
            or not all(is_number(v) and v >= 0 for v in value.values())):
        raise BookError(f"подмена dict: путь {path} и значение — веса с теми же ключами")
    total = math.fsum(float(v) for v in value.values())
    if total <= 0:
        raise BookError(f"подмена dict: веса {value!r} в сумме не положительны")
    return {k: float(v) / total for k, v in value.items()}


def override(A: dict, paths: Iterable[str], kind: str, value: Any) -> dict:
    """Копия книги с подменой по путям (оси полосы и обратного DCF, §9, §11).

    * `value` — число по пути заменяется значением `value`;
    * `shift` — к числу или ко всем значениям траектории (кроме `LT_from`)
      прибавляется `value`;
    * `dict` — веса по пути заменяются словарём `value`, нормированным к 1.

    Путь — ключи через точку: "margin.targets.stress.LT", "capex.maintenance.base",
    "worlds.M.lt.inflation". Нет пути — `BookError` (опечатка не пропускается).
    Книга-источник не меняется; схема на копии не перепроверяется (хэш миров
    после подмены инфляции мира законно другой).
    """
    if kind not in KINDS:
        raise BookError(f"подмена: kind = {kind!r} (известны: {', '.join(KINDS)})")
    fn = {"value": _valued, "shift": _shifted, "dict": _weights}[kind]
    out = A
    for path in paths:
        out = _replace(out, path.split("."), lambda node, p=path: fn(node, value, p), path)
    return out


def blend_weights(book: dict, end: dict, t: float) -> dict:
    """Веса книги, сдвинутые на долю `t` к словарю `end` (ось `dict`, §9)."""
    return {k: float(book[k]) + t * (float(end[k]) - float(book[k])) for k in book}


def add_observation(A: dict, period: str, value: float, se: float = 0.0) -> dict:
    """Копия книги с ещё одним наблюдением маржи (A-P2u) поверх внесённых (§12)."""
    update = A["joint"]["regime_update"]
    if any(o["period"] == period for o in update["observations"]):
        raise BookError(f"наблюдение за {period} уже внесено в книгу")
    first, last = A["meta"]["first_period"], A["meta"]["last_period"]
    if not period_index(first) <= period_index(period) <= period_index(last):
        raise BookError(f"наблюдение за {period} вне прогнозных полугодий {first}…{last}")
    observations = [*update["observations"], {"period": period, "value": float(value),
                                              "se": float(se)}]
    return _replace(A, ["joint", "regime_update", "observations"],
                    lambda _node: observations, "joint.regime_update.observations")


def open_period(A: dict) -> str | None:
    """Открытое полугодие: самое раннее прогнозное без внесённого факта (se = 0).

    None — все прогнозные полугодия уже с фактами (книгу пора перезаякорить).
    """
    facts = {o["period"] for o in A["joint"]["regime_update"]["observations"] if not o["se"]}
    return next((p for p in periods(A["meta"]["first_period"], A["meta"]["last_period"])
                 if p not in facts), None)


# ------------------------------------------------------ несмертельные замечания


def book_warnings(A: dict) -> list[str]:
    """Места, где книга читается по правилу §0.1 не так, как, вероятно, задумана.

    Не отказ, а строки для `run_output.txt`: правило §0.1 однозначно, но
    результат его чтения стоит видеть глазами.
    """
    from model.book_schema import company_trajectories

    last = A["meta"]["last_period"]
    out = []
    for where, spec in company_trajectories(A):
        years = [k for k in spec if k.isdigit()]
        if "LT" not in spec and not years and len(spec) >= 1:
            halves = sorted(spec, key=period_index)
            out.append(f"{where}: задана только полугодием {halves[-1]} без LT — по §0.1 значение "
                       f"{spec[halves[-1]]!r} действует во всех следующих полугодиях и в терминале")
        elif "LT" in spec and path_value(spec, last) != float(spec["LT"]):
            out.append(f"{where}: к {last} траектория не дошла до LT (LT_from или ключ года) — "
                       "терминал читает значение последнего полугодия, а не LT")
    return out

