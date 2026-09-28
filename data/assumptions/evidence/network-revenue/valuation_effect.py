"""Цена ошибки суждений области: точка (MODEL §8) на итоговой книге (assumptions.yaml) против той же книги со
значениями черновика по сети и выручке, точка на концах осей области, чувствительности и цена ошибки правила
ядра «исторические когорты с d = 1» (MODEL §4.1).

Концы осей — те же числа, что «Суждения по цене ошибки» в data/assumptions/run_output.txt (точка при low и high
оси). Раздел 4 печатает все цены, на которые ссылается текст раздела sections/network-revenue.md, — после
пересборки книги скрипт перезапускается и числа раздела берутся отсюда.
Запуск: python -B valuation_effect.py (нужны PyYAML и пакет model/ репозитория). Выход: valuation_effect_out.*
"""
from __future__ import annotations

import copy
import json
import sys
import warnings
from math import fsum

import yaml

from check_2026h2 import ASSUMPTIONS, REPO, build_book
from common import HERE, Report, eff_consistent, half_index, network_history, r6

sys.path.insert(0, str(REPO))
import model.core as core  # noqa: E402
from model.book import override  # noqa: E402
from model.facts import load_facts  # noqa: E402
from model.grid import evaluate  # noqa: E402


def consistent_network(self, tariff: str):
    """Сеть ядра со стартовым индексом, как в подборе d (density.py): когорты 2022H1–2026H1 созревают до d,
    закрытия — с κ, индекс от 2022H2 (common.eff_consistent); дальше — правило §4.1 без изменений."""
    A, P, cf = self.A, self.P, self.facts
    NW = A["network"]
    mu = [float(x) for x in NW["maturity_curve"]]
    n = len(mu) - 1
    d, kappa = float(NW["new_space_density"]), float(NW["closed_productivity"])
    growth = core.trajectory(NW["net_growth"][tariff], P)
    H = network_history()
    order = [p for p in H["A"] if p >= "2022H2"]
    ec = eff_consistent(order, H["A"]["2022H2"], H["O"], H["C"], mu, d, kappa)["eff"]
    back1 = core.prev_period(self.anchor)
    back2 = core.prev_period(back1)
    eff_avg_hist = {back1: (ec[back2] + ec[back1]) / 2.0, self.anchor: (ec[back1] + ec[self.anchor]) / 2.0}
    cohorts = [(H["O"][q], d) for q in sorted(H["O"], key=half_index)]
    area, eff = cf.area_end[self.anchor], ec[self.anchor]
    opened_l, closed_l, end_l, mid_l, avg_l = [], [], [], [], []
    for i in range(len(P)):
        closed = area * self.close[i] / 2.0
        opened = area * growth[i] / 2.0 + closed
        maturing = fsum(cohorts[-a][0] * cohorts[-a][1] * (mu[a] - mu[a - 1]) for a in range(1, min(n, len(cohorts)) + 1))
        eff_new = eff - closed * kappa + opened * d * mu[0] + maturing
        new_area = area + opened - closed
        opened_l.append(opened)
        closed_l.append(closed)
        end_l.append(new_area)
        mid_l.append((area + new_area) / 2.0)
        avg_l.append((eff + eff_new) / 2.0)
        cohorts.append((opened, d))
        area, eff = new_area, eff_new
    return core.NetworkPaths(tariff=tariff, opened=tuple(opened_l), closed=tuple(closed_l), area_end=tuple(end_l),
                             area_mid=tuple(mid_l), eff_avg=tuple(avg_l), eff_avg_hist=eff_avg_hist,
                             close_lt=self.close[-1])


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

    R.h("3. Цена ошибки правила ядра «исторические когорты с d = 1» (MODEL §4.1; density.py, разд. 5 и 7)")
    orig = core.Context._make_network
    try:
        core.Context._make_network = consistent_network
        p_cons = evaluate(A, facts).point.central
    finally:
        core.Context._make_network = orig
    dens = json.loads((HERE / "density_out.json").read_text(encoding="utf-8"))
    cr = dens["core_rule"]
    alts = {}
    for name, dd in (("d подобрана в единицах ядра (разд. 7а)", cr["d_fit_anchor_units"]),
                     ("d подобрана правилом ядра по полугодиям (разд. 7б)", cr["d_fit_core_rolling"])):
        alts[name] = {"d": dd, "point": r6(evaluate(override(A, ["network.new_space_density"], "value", dd), facts).point.central)}
    R.p(f"Стартовый индекс ядра как в подборе d (когорты 2022H1–2026H1 с плотностью d, закрытия с κ): точка "
        f"{round(p_cons)} ₽ ({p_cons - base:+.1f} ₽ к книге) — на столько правило ядра занижает стоимость.")
    R.p("Подбор d под правило ядра ошибку не устраняет: " + "; ".join(
        f"{k} — d {x['d']:.3f}, точка {round(x['point'])} ₽ ({x['point'] - base:+.0f} ₽)" for k, x in alts.items())
        + f" — против {p_cons - base:+.0f} ₽ у согласованного индекса.")

    R.h("4. Цены для текста раздела sections/network-revenue.md")
    net, traf = axes["Чистый рост площади 2027–2030 (сдвиг всех тарифов)"], axes["Трафик LFL (сдвиг всех состояний)"]
    dd_, kk = axes["Плотность новой площади"], axes["Продуктивность закрываемой площади"]
    R.p(f"A-R7 «Обоснование», рост сети: ось −1,0…+0,8 п.п. меняет точку на {abs(net['high'] - net['low']):.0f} ₽.")
    R.p(f"A-P4w/g: stress_growth = mid — точка {p_alt - base:+.0f} ₽.")
    R.p(f"A-R5 «Обоснование»: правило ядра занижает точку на {p_cons - base:.0f} ₽.")
    R.p(f"A-R5d: ось d {dd_['low']:.0f}…{dd_['high']:.0f} ₽ (размах {abs(dd_['high'] - dd_['low']):.0f}).")
    R.p(f"A-R6 «Чем может ошибаться»: ось κ 0,4…0,8 меняет точку на {abs(kk['high'] - kk['low']):.0f} ₽.")
    R.p(f"A-R2 «Чем может ошибаться»: сдвиг чека LT ±0,1 п.п. — {sens['сдвиг чека LT +0,1 п.п.']:+.0f} / "
        f"{sens['сдвиг чека LT −0,1 п.п.']:+.0f} ₽; трафик всех лет ±0,1 п.п. — {sens['трафик всех лет +0,1 п.п.']:+.0f} / "
        f"{sens['трафик всех лет −0,1 п.п.']:+.0f} ₽.")
    R.p(f"A-R4 «Чем может ошибаться»: ось трафика −0,8…+0,4 п.п. — {traf['low']:.0f}…{traf['high']:.0f} ₽ точки.")
    R.data = {"point_book": r6(base), "point_draft": r6(gD.point.central), "axes": axes, "stress_mid": r6(p_alt),
              "steps": steps, "sensitivity": {k: r6(x) for k, x in sens.items()},
              "core_rule": {"point_consistent_index": r6(p_cons), "effect": r6(p_cons - base), "d_refits": alts}}
    R.save("valuation_effect")


if __name__ == "__main__":
    main()
