"""Что изменилось с прошлого выпуска: разложение изменения точки (docs/PAYLOAD.md `changes`).

Точка — цена при центральных значениях суждений и λ книги (§8). Между
выпусками меняются живые входы (дата оценки = дата принятой цены, цена рынка)
и книга, факты, код. Разложение — цепочкой пересчётов на ТЕКУЩЕЙ книге,
фактах и коде, от прошлых входов к нынешним:

1. `residual` «книга, факты и код» — точка на прошлых дате и цене минус точка
   прошлого выпуска: всё, что не живые входы (остатком, одним числом);
2. `market_price` «цена рынка» — прошлая дата, нынешняя цена;
3. `valuation_date` «дата оценки (перекат)» — нынешние дата и цена.

Сумма строк = изменению точки. При замороженной книге точка растёт с датой
оценки (дисконт катится по форвардам, требования уменьшаются на денежный
результат, §13.4) — этот вклад печатается отдельно. Цена рынка в точку не
входит (точка — функция книги, фактов и даты), её строка честно считается
пересчётом и выходит нулём, пока это так.
"""

from __future__ import annotations

import datetime as dt

from model.facts import CoreFacts
from model.grid import evaluate

# Порядок шагов разложения — от объемлющего к живым входам.
COMPONENTS = ("residual", "market_price", "valuation_date")
TITLES = {"residual": "книга, факты и код", "market_price": "цена рынка",
          "valuation_date": "дата оценки (перекат)"}


def point_at(A: dict, cf: CoreFacts, valuation_date, market_price: float) -> float:
    """Точка книги `A` на дату оценки и цену рынка (тот же расчёт, что у выпуска)."""
    return evaluate(A, cf, valuation_date=valuation_date, market_price=market_price).point.central


def reference_of(previous: dict) -> dict:
    """Опорные числа прошлого выпуска для защиты заголовка (§13.4)."""
    meta = previous.get("meta") or {}
    head = previous.get("headline") or {}
    fv = previous.get("fair_value") or {}
    return {"printed_central": head.get("printed_central"), "central": head.get("central"),
            "v0_median": (fv.get("center_ev") or {}).get("v0_median"),
            "point": fv.get("central"), "book_version": meta.get("book_version"),
            "facts_date": meta.get("facts_date"), "valuation_date": meta.get("valuation_date"),
            "fast": meta.get("fast")}


def _changed(previous: dict, meta_now: dict) -> list[str]:
    was = previous.get("meta") or {}
    out = []
    for key, title in (("book_version", "книга"), ("facts_date", "факты"),
                       ("engine_commit", "код")):
        a, b = was.get(key), meta_now.get(key)
        if a and b and a != b:
            short = (lambda x: str(x)[:12]) if key == "engine_commit" else str
            out.append(f"{title} {short(a)} → {short(b)}")
    return out


def vs_previous(A: dict, cf: CoreFacts, previous: dict | None, *, valuation_date: dt.date,
                market_price: float, point_now: float, meta_now: dict) -> dict:
    """Блок `changes.vs_previous`: строки разложения, итог и опорные числа прошлого."""
    empty = {"previous_sha": None, "previous_generated_at": None, "rows": [], "total_rub": None,
             "note": "прошлого выпуска нет", "reference": None}
    if not previous:
        return empty
    meta = previous.get("meta") or {}
    out = {**empty, "previous_sha": meta.get("payload_sha256"),
           "previous_generated_at": meta.get("generated_at"),
           "reference": reference_of(previous), "note": None}
    was_point = (previous.get("fair_value") or {}).get("central")
    was_price = (previous.get("market") or {}).get("price")
    try:
        was_date = dt.date.fromisoformat(str(meta.get("valuation_date"))[:10])
    except ValueError:
        was_date = None
    if not isinstance(was_point, (int, float)) or not isinstance(was_price, (int, float)) \
            or was_date is None:
        out["note"] = "в прошлом выпуске нет точки, цены или даты оценки — разложить нельзя"
        return out
    base = point_at(A, cf, was_date, float(was_price))
    priced = point_at(A, cf, was_date, market_price)
    values = {"residual": base - float(was_point), "market_price": priced - base,
              "valuation_date": point_now - priced}
    out["rows"] = [{"component": k, "title": TITLES[k], "rub": values[k]} for k in COMPONENTS]
    out["total_rub"] = point_now - float(was_point)
    days = (valuation_date - was_date).days
    notes = _changed(previous, meta_now)
    notes.append(f"дата оценки {was_date.isoformat()} → {valuation_date.isoformat()} ({days:+d} дн.)")
    out["note"] = "; ".join(notes)
    return out
