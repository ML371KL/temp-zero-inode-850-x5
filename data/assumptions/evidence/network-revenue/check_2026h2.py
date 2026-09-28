"""Сквозная проверка: выручка 2П2026 г/г по формуле MODEL §4.2 на книжных значениях области против прогноза
компании (+12–16 % за 2026 г. при 1П +10,5 %) и ретро-оценки X5-indicators (3 кв. 2026: +9,4…+10,7 %).

Книга для прогона = черновик (assumptions.draft.yaml) + фрагмент «маржа» (вероятности режимов и спрос) +
этот фрагмент (оси — заменяют одноимённые оси черновика). Книга проверяется закрытой схемой ядра
(model.book_schema.validate_book), выручка считается ЯДРОМ (model.core.Context.revenue) на фактах
репозитория (data/facts); параллельно — независимой реализацией §4.1–4.2 этого листа (сверка).

Печатает: выручку 2П2026 по мирам × тарифам × спросу, ожидание слоя «свой взгляд» и её разложение
(эффективная площадь, чек, трафик, НДС, прочее), год 2026, путь 2027–2030 против рынка, то же на значениях
черновика. Выход: check_2026h2_out.txt / check_2026h2_out.json.
Запуск: python -B check_2026h2.py (нужны PyYAML и пакет model/ репозитория).
"""
from __future__ import annotations

import copy
import json
import sys
import warnings

import yaml

from common import HERE, Report, eff_model_rule, forward_network, halves, load, path_value, pc, r6

REPO = HERE.parents[3]
sys.path.insert(0, str(REPO))
from model.book_schema import validate_book  # noqa: E402
from model.core import Context  # noqa: E402
from model.facts import load_facts  # noqa: E402

ASSUMPTIONS = REPO / "data" / "assumptions"
WORLDS, REGIMES, TARIFFS, DEMANDS = ("N", "H", "M"), ("stress", "floor", "partial", "full"), ("low", "mid", "high"), ("bear", "base", "bull")


def deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, x in over.items():
        if isinstance(x, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], x)
        else:
            out[k] = copy.deepcopy(x)
    return out


def build_book(with_fragment: bool = True) -> dict:
    A = yaml.safe_load((ASSUMPTIONS / "assumptions.draft.yaml").read_text(encoding="utf-8"))
    mg = yaml.safe_load((ASSUMPTIONS / "fragments" / "margin.yaml").read_text(encoding="utf-8"))
    for k in ("axes_proposals", "reverse_dcf_proposals"):
        mg.pop(k, None)
    A = deep_merge(A, mg)
    if not with_fragment:
        return A
    fr = yaml.safe_load((ASSUMPTIONS / "fragments" / "network-revenue.yaml").read_text(encoding="utf-8"))
    axes, raxes = fr.pop("axes_proposals"), fr.pop("reverse_dcf_proposals")
    A = deep_merge(A, fr)
    replaced = {"Чистый рост площади (сдвиг всех тарифов)", "Трафик LFL (сдвиг всех состояний)",
                "Плотность новой площади", "Продуктивность закрываемой площади"}
    ua = [a for a in A["valuation"]["uncertainty"]["axes"] if a["name"] not in replaced | {x["name"] for x in axes}]
    A["valuation"]["uncertainty"]["axes"] = ua + axes
    ra = [a for a in A["valuation"]["reverse_dcf"]["axes"] if a["name"] not in replaced | {x["name"] for x in raxes}]
    A["valuation"]["reverse_dcf"]["axes"] = ra + raxes
    return A


def tariff_of(A: dict, W: str, reg: str) -> str:
    J = A["joint"]
    return J["stress_growth"] if reg == "stress" else J["world_links"][W]["growth"]


def expected(ctx: Context, A: dict, i: int, attr: str = "revenue") -> float:
    J = A["joint"]
    tot = 0.0
    for W, pw in J["world_prob"].items():
        for reg, pr in J["regime_prob"].items():
            rv = ctx.revenue(W, tariff_of(A, W, reg), J["regime_demand"][reg])
            tot += pw * pr * getattr(rv, attr)[i]
    return tot


def own_revenue(A: dict, facts, W: str, t: str, s: str) -> float:
    """Независимая реализация §4.1–4.2 (правило ядра для истории) — выручка 2П2026, млрд ₽."""
    nf = load("network_facts.json")
    Ah = {p: x["v"] for p, x in nf["area_end"].items()}
    Oh = {p: x["v"] for p, x in nf["gross_opened_hist"].items()}
    NW, RV = A["network"], A["revenue"]
    P = ["2026H2"]
    Af, Of, Cf = forward_network(Ah["2026H1"], P, NW["net_growth"][t], NW["close_rate"])
    eff = eff_model_rule("2026H1", P, Ah, Oh, Of, Cf, NW["maturity_curve"], NW["new_space_density"], NW["closed_productivity"])
    k = RV["ticket_k"][s]
    hom = RV["homogeneity"]
    wgt = min(1.0, max(0.0, (2026 - hom["ramp_from"]) / (hom["ramp_to"] - hom["ramp_from"])))
    ticket = (k * A["worlds"][W]["food_cpi"]["2026H2"] + path_value(RV["ticket_shift"][s], "2026H2")
              + path_value(RV["vat_effect"], "2026H2")
              + (1 - k) * (A["worlds"][W]["lt"]["inflation"] - A["worlds"][hom["reference_world"]]["lt"]["inflation"]) * wgt)
    base = facts.data["accounting"]["periods"]["2025H2"]["revenue"]
    return base * (1 + eff["yoy"]["2026H2"]) * (1 + ticket) * (1 + path_value(RV["traffic"][s], "2026H2")) \
        * (1 + path_value(RV["other_growth"], "2026H2"))


def main() -> None:
    R = Report()
    A = build_book(True)
    validate_book(A)
    D = build_book(False)
    validate_book(D)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        facts = load_facts(REPO / "data" / "facts")
    acc = facts.data["accounting"]["periods"]
    r25h1, r25h2, r26h1 = acc["2025H1"]["revenue"], acc["2025H2"]["revenue"], acc["2026H1"]["revenue"]
    ctx, ctxd = Context(A, facts), Context(D, facts)
    R.p("Книга (черновик + фрагмент «маржа» + этот фрагмент) прошла закрытую схему ядра (validate_book).")

    R.h("1. Выручка 2П2026 г/г по мирам × тарифам × спросу (ядро), %")
    rows, grid = [], {}
    for W in WORLDS:
        for t in TARIFFS:
            row = [W, t]
            for s in DEMANDS:
                g = ctx.revenue(W, t, s).revenue[0] / r25h2 - 1
                grid[f"{W}|{t}|{s}"] = g
                row.append(pc(g))
            rows.append(row)
    R.table(["мир", "тариф", "bear", "base", "bull"], rows)
    own = own_revenue(A, facts, "H", "mid", "base")
    core = ctx.revenue("H", "mid", "base").revenue[0]
    R.p(f"Сверка с независимой реализацией листа (H, mid, base): ядро {core:.3f}, лист {own:.3f} млрд ₽ "
        f"(расхождение {100 * (own / core - 1):+.4f} %).")

    R.h("2. Ожидание слоя «свой взгляд» и разложение (2П2026 к 2П2025)")
    J = A["joint"]
    e_rev = expected(ctx, A, 0)
    g_e = e_rev / r25h2 - 1
    # разложение ожидания: эффективная площадь, чек, трафик, НДС, прочее (средние по весам клеток)
    parts = {"eff": 0.0, "ticket_ex_vat": 0.0, "traffic": 0.0}
    for W, pw in J["world_prob"].items():
        for reg, pr in J["regime_prob"].items():
            t, s = tariff_of(A, W, reg), J["regime_demand"][reg]
            G, rv = ctx.network(t), ctx.revenue(W, t, s)
            parts["eff"] += pw * pr * (G.eff_avg[0] / G.eff_avg_hist["2025H2"] - 1)
            parts["ticket_ex_vat"] += pw * pr * (rv.ticket[0] - path_value(A["revenue"]["vat_effect"], "2026H2"))
            parts["traffic"] += pw * pr * rv.traffic[0]
    vat = path_value(A["revenue"]["vat_effect"], "2026H2")
    oth = path_value(A["revenue"]["other_growth"], "2026H2")
    dens = json.loads((HERE / "density_out.json").read_text(encoding="utf-8"))
    m = dens["forecast_mid"]["2026H2"]["m"]
    R.table(["составляющая", "вклад, %"],
            [["эффективная площадь (правило ядра)", pc(parts["eff"])],
             ["  из неё вне отчётного LFL (NL, тариф mid, density.py)", pc(dens["forecast_mid"]["2026H2"]["nl"])],
             ["  созревание, в отчётном LFL (m)", pc(m)],
             ["LFL-чек с НДС (k × прод. ИПЦ + сдвиг)", pc(parts["ticket_ex_vat"])],
             ["клин НДС", pc(vat)],
             ["LFL-трафик зрелой сети", pc(parts["traffic"])],
             ["прочая выручка", pc(oth)],
             ["итого рост выручки 2П2026", pc(g_e)]])
    rep_lfl = (1 + parts["ticket_ex_vat"]) * (1 + parts["traffic"] + m) - 1
    R.p(f"Отчётный LFL (с НДС), который даёт книга: ≈ {pc(rep_lfl)} % (чек + трафик зрелой сети + m); "
        f"вклад не-LFL в отчётном смысле ≈ {pc((1 + g_e) / ((1 + rep_lfl) * (1 + vat)) - 1)} %.")
    g26 = (r26h1 + e_rev) / (r25h1 + r25h2) - 1
    R.p(f"Выручка 2П2026: {e_rev:.1f} млрд ₽ (2П2025 {r25h2:.1f}); год 2026: {r26h1 + e_rev:.1f} млрд ₽, рост {pc(g26)} % "
        f"(1П2026 {pc(r26h1 / r25h1 - 1)} %).")
    lo_c, hi_c = min(grid.values()), max(grid.values())
    R.p(f"Разброс клеток 2П2026: {pc(lo_c)} … {pc(hi_c)} %.")

    R.h("3. Против прогноза компании и ретро-оценки")
    gd = facts.data["guidance"]
    need = gd["implied_2026"]["revenue_2026H2_growth"]
    pl = load("plans.json")
    retro = pl["retro_q3_2026_revenue_growth"]["v"]
    R.p(f"Прогноз компании на 2026 г.: +{pc(gd['revenue_growth'][0], 0)}…+{pc(gd['revenue_growth'][1], 0)} % → 2П2026 нужно "
        f"+{pc(need[0], 1)}…+{pc(need[1], 1)} % г/г (data/facts/guidance.json). Книга: +{pc(g_e, 1)} % — ниже нижней границы "
        f"на {pc(need[0] - g_e, 1)} п.п.; год — +{pc(g26, 1)} %.")
    R.p(f"Ретро-оценка 3 кв. 2026 (X5-indicators §4, разностная форма без подгонки): +{pc(retro[0], 1)}…+{pc(retro[1], 1)} %, "
        f"RMSE метода 2,5 п.п. Книга 2П2026 +{pc(g_e, 1)} % — внутри.")
    nf = load("network_facts.json")
    Ah = {p: x["v"] for p, x in nf["area_end"].items()}
    a26 = sum(J["world_prob"][W] * J["regime_prob"][reg] * ctx.network(tariff_of(A, W, reg)).area_end[0]
              for W in WORLDS for reg in REGIMES)
    g_area_h2 = ((Ah["2026H1"] + a26) / 2) / ((Ah["2025H1"] + Ah["2025H2"]) / 2) - 1
    g_area_h2_25 = ((Ah["2025H1"] + Ah["2025H2"]) / 2) / ((Ah["2024H1"] + Ah["2024H2"]) / 2) - 1
    nl_rep = (1 + g_e) / ((1 + rep_lfl) * (1 + vat)) - 1
    need_lfl = (1 + need[0]) / ((1 + nl_rep) * (1 + vat) * (1 + oth)) - 1
    from common import food_yoy
    cut = 1 - (Ah["2026H1"] - Ah["2025H2"]) / (Ah["2025H1"] - Ah["2024H2"])
    R.p("Почему книга ниже компании: (1) продовольственная инфляция упала сильнее, чем было видно в день прогноза "
        f"(20.03.2026): январь–февраль 2026 {pc(food_yoy('2026H1', ['2026-01', '2026-02']), 1)} % г/г, 2 кв. "
        f"{pc(food_yoy('2026Q2'), 1)} %, июль–август {pc(food_yoy('2026H2', ['2026-07', '2026-08']), 1)} % против "
        f"{pc(food_yoy('2025H2'), 1)} % во 2П2025; 1 п.п. инфляции — {A['revenue']['ticket_k']['base']:.2f} п.п. роста чека; "
        f"(2) чистый прирост площади 1П2026 на {pc(cut, 0)} % ниже г/г ({Ah['2026H1'] - Ah['2025H2']:.0f} против "
        f"{Ah['2025H1'] - Ah['2024H2']:.0f} тыс. м²): рост средней площади 2П2026 ≈ +{pc(g_area_h2, 1)} % против +{pc(g_area_h2_25, 1)} % во 2П2025; "
        f"(3) клин НДС {pc(vat, 2)} п.п.: LFL растёт с НДС, выручка — без. Чтобы выйти на +{pc(need[0], 1)} % при той же площади, "
        f"отчётный LFL 2П должен быть ≈ {pc(need_lfl, 1)} % — вдвое выше 2 кв. 2026 (4,2 %). В 2025 г. компания тоже "
        "промахнулась по выручке (+18,8 % против ≈20 %, X3 §1.3).")
    R.data["need_lfl_for_guidance_low"] = r6(need_lfl)
    R.data["area_avg_growth_2026H2"] = r6(g_area_h2)

    R.h("4. Путь выручки слоя «свой взгляд» 2026–2030 против рынка (INFOLine, прогноз в годовом отчёте X5)")
    P = ctx.P
    years = {}
    for i, p in enumerate(P):
        y = int(p[:4])
        years.setdefault(y, 0.0)
        years[y] += expected(ctx, A, i)
    years[2026] += r26h1
    mk = pl["grocery_market_trn_rub"]
    rows = []
    prev = r25h1 + r25h2
    for y in range(2026, 2031):
        g = years[y] / prev - 1
        mg = (mk[str(y)] / mk[str(y - 1)] - 1) if str(y) in mk and str(y - 1) in mk else None
        rows.append([y, round(years[y], 0), pc(g), pc(mg) if mg is not None else "—"])
        prev = years[y]
    R.table(["год", "выручка, млрд ₽", "рост X5, %", "рост рынка, %"], rows)
    share = pl["market_share_x5"]["2025"]
    R.p(f"Доля X5 2025 г. {pc(share, 1)} % (INFOLine); при пути книги и прогнозе рынка к 2029 г. — "
        f"≈{pc(share * (years[2029] / (r25h1 + r25h2)) / (mk['2029'] / mk['2025']), 1)} % (рост доли медленнее +1 п.п. в год 2022–2025).")

    R.h("5. То же на значениях черновика (для сравнения)")
    e_d = expected(ctxd, D, 0)
    R.p(f"Черновик: 2П2026 +{pc(e_d / r25h2 - 1)} %, год 2026 +{pc((r26h1 + e_d) / (r25h1 + r25h2) - 1)} % "
        "(черновик: чистый рост 5,5–7 %, d 0,95, сдвиг чека +1,5…+3 п.п., НДС −0,6 п.п. навсегда — ключ без LT).")
    R.p(f"Черновик: vat_effect без LT → {pc(path_value(D['revenue']['vat_effect'], '2030H1'))} % в 2030H1 — ошибка черновика "
        "(поправка на НДС — разовая); во фрагменте LT = 0.")

    R.data.update({"expected_2026H2_growth": r6(g_e), "expected_2026H2_revenue": r6(e_rev), "year_2026_growth": r6(g26),
              "parts": {k: r6(x) for k, x in parts.items()}, "reported_lfl_implied": r6(rep_lfl),
              "grid": {k: r6(x) for k, x in grid.items()}, "cells_range": [r6(lo_c), r6(hi_c)],
              "own_vs_core": r6(own / core - 1), "draft_2026H2_growth": r6(e_d / r25h2 - 1),
              "path": {str(y): r6(x) for y, x in years.items()}})
    R.save("check_2026h2")


if __name__ == "__main__":
    main()
