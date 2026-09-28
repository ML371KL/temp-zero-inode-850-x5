"""Цена ошибки суждений области: точка (MODEL §8) на книге «черновик + фрагмент «маржа» + этот фрагмент»
против той же книги со значениями черновика по сети и выручке, и точка на концах предложенных осей.

Это справка к тексту раздела (порядок величин); итоговые числа печатает выпуск на собранной книге.
Запуск: python -B valuation_effect.py (нужны PyYAML и пакет model/ репозитория). Выход: valuation_effect_out.*
"""
from __future__ import annotations

import copy
import sys
import warnings

import yaml

from check_2026h2 import ASSUMPTIONS, REPO, build_book
from common import Report, r6

sys.path.insert(0, str(REPO))
from model.book import override  # noqa: E402
from model.facts import load_facts  # noqa: E402
from model.grid import evaluate  # noqa: E402


def main() -> None:
    R = Report()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        facts = load_facts(REPO / "data" / "facts")
    A, D = build_book(True), build_book(False)
    gA, gD = evaluate(A, facts), evaluate(D, facts)
    R.h("1. Точка: книга с фрагментом против значений черновика по сети и выручке (прочее — черновик + «маржа»)")
    rows = []
    for name, g in (("черновик (сеть и выручка)", gD), ("фрагмент network-revenue", gA)):
        pt = g.point
        rows.append([name, round(pt.low), round(pt.high), round(pt.central), f"{100 * pt.gap_point:+.1f}"])
    R.table(["книга", "низ (M), ₽", "верх (свой взгляд), ₽", "точка, ₽", "EV к рынку, %"], rows)
    R.p("Цены — справка к порядку величин: остальные области книги ещё черновые.")
    R.p("Разложение перехода «черновик → фрагмент» (блоки заменяются по очереди, точка после каждого шага):")
    blocks = [("revenue.vat_effect (в черновике −0,6 п.п. навсегда: ключ без LT)", [("revenue", "vat_effect")]),
              ("revenue.ticket_k", [("revenue", "ticket_k")]), ("revenue.ticket_shift", [("revenue", "ticket_shift")]),
              ("revenue.traffic", [("revenue", "traffic")]), ("revenue.other_growth", [("revenue", "other_growth")]),
              ("network.net_growth + close_rate", [("network", "net_growth"), ("network", "close_rate")]),
              ("network: d, μ, κ", [("network", "new_space_density"), ("network", "maturity_curve"), ("network", "closed_productivity")])]
    cur, prev, steps = copy.deepcopy(D), gD.point.central, {}
    drows = []
    for name, keys in blocks:
        for a, b in keys:
            cur[a][b] = copy.deepcopy(A[a][b])
        pnt = evaluate(cur, facts).point.central
        drows.append([name, round(pnt), f"{round(pnt - prev):+d}"])
        steps[name] = r6(pnt - prev)
        prev = pnt
    R.table(["шаг", "точка, ₽", "изменение, ₽"], drows)

    R.h("2. Точка на концах предложенных осей (остальное — книга с фрагментом)")
    fr = yaml.safe_load((ASSUMPTIONS / "fragments" / "network-revenue.yaml").read_text(encoding="utf-8"))
    rows, data = [], {}
    base = gA.point.central
    for ax in fr["axes_proposals"]:
        lo = evaluate(override(A, ax["paths"], ax["kind"], ax["low"]), facts).point.central
        hi = evaluate(override(A, ax["paths"], ax["kind"], ax["high"]), facts).point.central
        rows.append([ax["name"], ax["low"], ax["high"], round(lo), round(hi), round(abs(hi - lo))])
        data[ax["name"]] = {"low": r6(lo), "high": r6(hi)}
    R.table(["ось", "низ", "верх", "точка при низе, ₽", "точка при верхе, ₽", "размах, ₽"], rows)
    lt_paths = [f"revenue.ticket_shift.{s}.LT" for s in ("bear", "base", "bull")]
    tr_paths = [f"revenue.traffic.{s}" for s in ("bear", "base", "bull")]
    sens = {"сдвиг чека LT +0,1 п.п.": evaluate(override(A, lt_paths, "shift", 0.001), facts).point.central - base,
            "сдвиг чека LT −0,1 п.п.": evaluate(override(A, lt_paths, "shift", -0.001), facts).point.central - base,
            "трафик всех лет −0,1 п.п.": evaluate(override(A, tr_paths, "shift", -0.001), facts).point.central - base}
    R.p("Чувствительность точки: " + "; ".join(f"{k} → {round(x):+d} ₽" for k, x in sens.items()) + ".")
    # привязка стресса к тарифу mid (альтернатива)
    alt = yaml.safe_load(yaml.safe_dump(A))
    alt["joint"]["stress_growth"] = "mid"
    p_alt = evaluate(alt, facts).point.central
    R.p(f"Альтернатива stress_growth = mid: точка {round(p_alt)} ₽ ({round(p_alt - base):+d} ₽ к книге {round(base)} ₽).")
    R.data = {"point_fragment": r6(base), "point_draft": r6(gD.point.central), "axes": data, "stress_mid": r6(p_alt),
              "steps": steps, "sensitivity": {k: r6(x) for k, x in sens.items()}}
    R.save("valuation_effect")


if __name__ == "__main__":
    main()
