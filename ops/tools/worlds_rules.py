# -*- coding: utf-8 -*-
"""Правила книги X5 для чисел, зависящих от миров ставок (шаги `rule` манифеста
`data/assumptions/worlds_dependents.yaml`, команда `ops/tools/adopt_worlds.py`).

Правило — функция `f(ctx) -> {"file", "values", "decimals"}` (или список таких); `ctx` — контекст движка
(`ctx.record` — запись миров дерева, `ctx.entry` — запись индекса источника или None, `ctx.root`, `ctx.yaml(rel)`).
Сторож `tests/test_worlds_dependents.py` (через `adopt_worlds.py --check`) сверяет числа правил с файлами.

* `project_worlds` — миры книги и черновика: проекция записи миров (`worlds_source.json`) в поля схемы X5
  (`model/book_schema.py::_WORLD`) той же математикой, что у сборщика книги-источника (850oa
  `build_assumptions.py`): ряды записи / 100, 5 знаков; узлы кривой — `fair_zero_curve_today`, уровень в терминале —
  блок `curve_lt`, π в долгую — `lt_inflation`. Миры X5 — функция записи: приём не ждёт выпуска книги Магнита
  (`ops/tools/check_worlds.py` сверяет их с книгой Магнита на теге, когда тег выпущен). Происхождение:
  `worlds.source.book` — книга-источник записи (`origin` индекса), `curve_date` и `meta.curve_as_of` — дата снимка,
  `kernel_sha256` — хэш содержимого миров; `tag_commit` правило не пишет (коммит тега книги-источника — не поле записи).
* `ev_ebitda` — коридор `checks.ev_ebitda` книги и фрагмента = выход листа гейтов (`financing-valuation/gates.py`:
  верх мира H — интерполяция между N и M по узлу LT кривой миров, округление до 0,5×). Смена коридора — красный
  флаг команды (число в комментарии фрагмента): суждение о коридоре смотрит ведущий.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

BOOK = "data/assumptions/assumptions.yaml"
DRAFT = "data/assumptions/assumptions.draft.yaml"
FV = "data/assumptions/evidence/financing-valuation"
WORLDS = ("N", "H", "M")
SERIES = {"key_rate": "key_avg", "cpi": "cpi_yoy_avg", "food_cpi": "food_cpi_yoy_avg"}
ND = 5


def projection(record: dict) -> dict:
    """Миры записи в полях схемы X5 (без `name`: имена миров — константы книги)."""
    if not record.get("curve_lt") or not record.get("lt_inflation"):
        raise ValueError("запись без блоков curve_lt / lt_inflation — уровень терминала и π в долгую не по правилу")
    out = {}
    for w in WORLDS:
        rows = [r for r in record["rows"] if r["world"] == w]
        d = {f: {r["period"]: round(r[src] * 0.01, ND) for r in rows} for f, src in SERIES.items()}
        fair = record["fair_zero_curve_today"][w]
        curve = {str(k): round(v / 100, ND) for k, v in sorted(fair.items(), key=lambda kv: float(kv[0]))}
        curve["LT"] = round(float(record["curve_lt"][w]) / 100.0, ND)
        d["zero_curve"] = curve
        d["lt"] = {"inflation": round(float(record["lt_inflation"][w]) / 100.0, ND)}
        out[w] = d
    return out


def curve_date(record: dict) -> str:
    return str((record.get("m_inflation") or {}).get("curve_date") or record["asof"])


def project_worlds(ctx) -> list[dict]:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from model.book_schema import kernel_sha256

    P = projection(ctx.record)
    book = ctx.yaml(BOOK)
    kernel = {w: {"name": book["worlds"][w]["name"], **P[w]} for w in WORLDS}
    values: dict = {}
    for w in WORLDS:
        for f in SERIES:
            for period, x in P[w][f].items():
                values[f"worlds.{w}.{f}.{period}"] = x
        for node, x in P[w]["zero_curve"].items():
            values[f"worlds.{w}.zero_curve.{node}"] = x
        values[f"worlds.{w}.lt.inflation"] = P[w]["lt"]["inflation"]
    day = curve_date(ctx.record)
    values.update({"worlds.source.curve_date": day, "worlds.source.kernel_sha256": kernel_sha256(kernel),
                   "meta.curve_as_of": day})
    if ctx.entry:
        values["worlds.source.book"] = ctx.entry["origin"]
    decimals = {k: ND for k, v in values.items() if isinstance(v, float)}
    return [{"file": BOOK, "values": values, "decimals": decimals},
            {"file": DRAFT, "values": dict(values), "decimals": dict(decimals)}]


def ev_ebitda(ctx) -> list[dict]:
    out = json.loads((ctx.root / FV / "gates_out.json").read_text(encoding="utf-8"))["ev_ebitda"]["book"]
    values = {f"checks.ev_ebitda.{w}.{i}": float(out[w][i]) for w in WORLDS for i in (0, 1)}
    dec = {k: 2 for k in values}
    return [{"file": BOOK, "values": values, "decimals": dec},
            {"file": f"{FV}/fragment.yaml", "values": dict(values), "decimals": dict(dec)}]
