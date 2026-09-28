"""Цена ошибки суждений области: точка (MODEL §8) на итоговой книге (assumptions.yaml) против той же книги со
значениями черновика по сети и выручке, точка на концах осей области и чувствительности.

Концы осей — те же числа, что «Суждения по цене ошибки» в data/assumptions/run_output.txt (точка при low и high
оси). Раздел 3 печатает все цены, на которые ссылается текст раздела sections/network-revenue.md, — после
пересборки книги скрипт перезапускается и числа раздела берутся отсюда.
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
    base = gA.point.central
    R.h(f"1. Точка: книга {A['meta']['version']} против той же книги со значениями черновика по сети и выручке")
    rows = []
    for name, g in (("черновик (сеть и выручка)", gD), (f"книга {A['meta']['version']}", gA)):
        pt = g.point
        rows.append([name, round(pt.low), round(pt.high), round(pt.central), f"{100 * pt.gap_point:+.1f}"])
    R.table(["книга", "низ (M), ₽", "верх (свой взгляд), ₽", "точка, ₽", "EV к рынку, %"], rows)
    R.p("Разложение перехода «черновик → книга» (блоки заменяются по очереди, точка после каждого шага):")
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

    R.h("2. Точка на концах осей области и чувствительности (остальное — книга)")
    fr = yaml.safe_load((ASSUMPTIONS / "fragments" / "network-revenue.yaml").read_text(encoding="utf-8"))
    rows, axes = [], {}
    for ax in fr["axes_proposals"]:
        lo = evaluate(override(A, ax["paths"], ax["kind"], ax["low"]), facts).point.central
        hi = evaluate(override(A, ax["paths"], ax["kind"], ax["high"]), facts).point.central
        rows.append([ax["name"], ax["low"], ax["high"], round(lo), round(hi), round(abs(hi - lo))])
        axes[ax["name"]] = {"low": r6(lo), "high": r6(hi)}
    R.table(["ось", "низ", "верх", "точка при низе, ₽", "точка при верхе, ₽", "размах, ₽"], rows)
    lt_paths = [f"revenue.ticket_shift.{s}.LT" for s in ("bear", "base", "bull")]
    tr_paths = [f"revenue.traffic.{s}" for s in ("bear", "base", "bull")]
    tr_lt = [f"revenue.traffic.{s}.LT" for s in ("bear", "base", "bull")]
    sens = {"сдвиг чека LT +0,1 п.п.": evaluate(override(A, lt_paths, "shift", 0.001), facts).point.central - base,
            "сдвиг чека LT −0,1 п.п.": evaluate(override(A, lt_paths, "shift", -0.001), facts).point.central - base,
            "трафик всех лет +0,1 п.п.": evaluate(override(A, tr_paths, "shift", 0.001), facts).point.central - base,
            "трафик всех лет −0,1 п.п.": evaluate(override(A, tr_paths, "shift", -0.001), facts).point.central - base,
            "трафик LT −0,1 п.п.": evaluate(override(A, tr_lt, "shift", -0.001), facts).point.central - base}
    R.p("Чувствительность точки: " + "; ".join(f"{k} → {round(x):+d} ₽" for k, x in sens.items()) + ".")
    alt = copy.deepcopy(A)
    alt["joint"]["stress_growth"] = "mid"
    p_alt = evaluate(alt, facts).point.central
    R.p(f"Альтернатива stress_growth = mid: точка {round(p_alt)} ₽ ({round(p_alt - base):+d} ₽ к книге {round(base)} ₽).")

    R.h("3. Цены для текста раздела sections/network-revenue.md")
    net, traf = axes["Чистый рост площади 2027–2030 (сдвиг всех тарифов)"], axes["Трафик LFL (сдвиг всех состояний)"]
    dd_, kk = axes["Плотность новой площади"], axes["Продуктивность закрываемой площади"]
    R.p(f"A-R7 «Обоснование», рост сети: ось −1,0…+0,8 п.п. меняет точку на {abs(net['high'] - net['low']):.0f} ₽.")
    R.p(f"A-P4w/g: stress_growth = mid — точка {p_alt - base:+.0f} ₽.")
    R.p(f"A-R5d: ось d {dd_['low']:.0f}…{dd_['high']:.0f} ₽ (размах {abs(dd_['high'] - dd_['low']):.0f}).")
    R.p(f"A-R6 «Чем может ошибаться»: ось κ 0,4…0,8 меняет точку на {abs(kk['high'] - kk['low']):.0f} ₽.")
    R.p(f"A-R2 «Чем может ошибаться»: сдвиг чека LT ±0,1 п.п. — {sens['сдвиг чека LT +0,1 п.п.']:+.0f} / "
        f"{sens['сдвиг чека LT −0,1 п.п.']:+.0f} ₽; трафик всех лет ±0,1 п.п. — {sens['трафик всех лет +0,1 п.п.']:+.0f} / "
        f"{sens['трафик всех лет −0,1 п.п.']:+.0f} ₽.")
    R.p(f"A-R4 «Чем может ошибаться»: ось трафика −0,8…+0,4 п.п. — {traf['low']:.0f}…{traf['high']:.0f} ₽ точки.")
    R.data = {"point_book": r6(base), "point_draft": r6(gD.point.central), "axes": axes, "stress_mid": r6(p_alt),
              "steps": steps, "sensitivity": {k: r6(x) for k, x in sens.items()}}
    R.save("valuation_effect")


if __name__ == "__main__":
    main()
