"""Первое прогнозное полугодие клетки — вручную по формулам docs/MODEL.md §4.

Независимый пересчёт строки (без кэшей ядра): ловит сдвиг индексов когорт,
базы «год к году», D&A, процентов и дивидендов.
"""

from __future__ import annotations

import copy

import pytest

from model.book import half_rate, path_value, prev_period
from model.core import Context, run_cell
from model.facts import core_facts

WORLD, REGIME, CAPEX = "H", "floor", "base"


def test_first_half_year_by_hand(book, facts):
    A, cf = book, core_facts(facts, book)
    p1 = A["meta"]["first_period"]
    anchor, back1 = A["meta"]["anchor_period"], prev_period(A["meta"]["anchor_period"])
    back2 = prev_period(back1)
    J, NW, R, M, C = A["joint"], A["network"], A["revenue"], A["margin"], A["capex"]
    WC, TX, FN = A["working_capital"], A["tax"], A["financing"]
    W = A["worlds"][WORLD]
    tariff = J["world_links"][WORLD]["growth"]
    credit = J["world_links"][WORLD]["credit"]
    demand = J["regime_demand"][REGIME]

    # §4.1 сеть
    mu = NW["maturity_curve"]
    n = len(mu) - 1
    O = cf.gross_opened

    def back(q, a):
        for _ in range(a):
            q = prev_period(q)
        return q

    def eff_hist(q):
        return cf.area_end[q] - sum(O[back(q, a)] * (1 - mu[a]) for a in range(n))

    a0 = cf.area_end[anchor]
    closed = a0 * path_value(NW["close_rate"], p1) / 2
    opened = a0 * path_value(NW["net_growth"][tariff], p1) / 2 + closed
    d, kappa = NW["new_space_density"], NW["closed_productivity"]
    maturing = sum(O[back(anchor, a - 1)] * (mu[a] - mu[a - 1]) for a in range(1, n + 1))
    eff1 = eff_hist(anchor) - closed * kappa + opened * d * mu[0] + maturing
    avg1 = (eff_hist(anchor) + eff1) / 2
    avg_base = (eff_hist(back2) + eff_hist(back1)) / 2

    # §4.2 выручка
    k = R["ticket_k"][demand]
    H = R["homogeneity"]
    w = min(1, max(0, (int(p1[:4]) - H["ramp_from"]) / (H["ramp_to"] - H["ramp_from"])))
    hom = (1 - k) * (W["lt"]["inflation"] - A["worlds"][H["reference_world"]]["lt"]["inflation"]) * w
    ticket = (k * W["food_cpi"][p1] + path_value(R["ticket_shift"][demand], p1)
              + path_value(R["vat_effect"], p1) + hom)
    traffic = path_value(R["traffic"][demand], p1)
    rev = (cf.revenue[back1] * avg1 / avg_base * (1 + ticket) * (1 + traffic)
           * (1 + path_value(R["other_growth"], p1)))
    rev_ann = rev + cf.revenue[anchor]

    # §4.3 маржа
    s = M["seasonal_h1_pp"]
    season = s if p1[5] == "1" else -s          # год first_period — сезонность действует
    season_a = s if anchor[5] == "1" else -s
    target_a = path_value(M["targets"][REGIME], anchor)
    dev = M["deviation_persistence"] * (cf.margin_anchor - target_a - season_a)
    margin = path_value(M["targets"][REGIME], p1) + season + dev
    ebitda = rev * margin
    lti = M["lti_pct"] * rev

    # §4.5 capex и D&A (в первом полугодии физический множитель = 1)
    idx = 1 + half_rate(W["cpi"][p1])
    maint = rev * path_value(C["maintenance"][CAPEX], p1)
    growth = opened * C["price_per_m2"] * idx
    infra = (max(0, opened - closed) * C["infra_per_m2"] * idx
             if int(p1[:4]) >= C["infra_from_year"] else 0.0)
    capex = maint + growth + infra
    L2 = 2 * C["asset_life_years"]
    da = cf.da_anchor * (1 - 1 / L2) + cf.capex_anchor / L2

    # §4.6–4.8
    ltm_a = cf.revenue[back1] + cf.revenue[anchor]
    nwc = path_value(WC["nwc_pct"], p1) * rev_ann + (WC["h1_excess_pct"] * rev_ann if p1[5] == "1" else 0)
    opc, opc_a = WC["operating_cash_pct"] * rev_ann, WC["operating_cash_pct"] * ltm_a
    base = ebitda - da - lti + TX["permanent_add_pct"] * rev
    tax_u = TX["rate"] * max(0, base)
    fcff = (ebitda - lti - tax_u - capex - (nwc - cf.nwc) - (opc - opc_a)
            + WC["lease_adj_pct"] * rev + C["disposal_proceeds_pct"] * rev)

    # §4.9 проценты, щит, долг
    lw, fs = path_value(FN["legacy_weight"], p1), path_value(FN["fixed_share"], p1)
    fixed = lw * FN["legacy_rate"] + (1 - lw) * (W["zero_curve"]["3"] + FN["spread_fixed"][credit])
    rate = fs * fixed + (1 - fs) * (W["key_rate"][p1] + FN["spread_float"][credit])
    buf_a = FN["cash_buffer_pct"] * ltm_a
    nd_a = cf.net_debt + cf.dividends_payable
    interest = ((nd_a + opc_a + buf_a) * half_rate(rate)
                - buf_a * half_rate(FN["cash_yield_k"] * W["key_rate"][p1]))
    shield = tax_u - TX["rate"] * max(0, base - interest)
    nd_pre = nd_a - (fcff + shield - interest)
    ltm = (ebitda - lti) + cf.ebitda_rep[anchor]
    pays = p1 >= FN["dividends_from"]
    div = max(0, FN["target_leverage"] * ltm - nd_pre) if pays else 0.0

    ctx = Context(A, cf)
    row = run_cell(ctx, ctx.cell(WORLD, REGIME, CAPEX)).rows[0]
    expect = dict(revenue=rev, ticket=ticket, eff_area_avg=avg1, margin=margin, adj_ebitda=ebitda,
                  capex_maintenance=maint, capex_growth=growth, capex_infra=infra, da=da,
                  nwc=nwc, opcash=opc, tax_unlevered=tax_u, fcff=fcff, debt_rate=rate,
                  interest=interest, shield=shield, dividends=div, net_debt=nd_pre + div)
    for name, value in expect.items():
        assert getattr(row, name) == pytest.approx(value, rel=1e-12, abs=1e-12), name


def test_immature_space_matures_to_physical(book, facts):
    """Без роста и закрытий эффективная площадь за n полугодий дозревает до физической."""
    B = copy.deepcopy(book)
    for t in B["network"]["net_growth"]:
        B["network"]["net_growth"][t] = {"LT": 0.0}
    B["network"]["close_rate"] = {"LT": 0.0}
    ctx = Context(B, facts)
    net = ctx.network("mid")
    n = len(B["network"]["maturity_curve"]) - 1
    area = ctx.facts.area_end[ctx.anchor]
    assert all(a == area for a in net.area_end)
    assert net.eff_avg[n] == pytest.approx(area, rel=1e-14)
    assert net.eff_avg[0] < area


def test_terminal_by_hand(book, facts):
    """Терминал §6 вручную по двум последним строкам клетки."""
    A = book
    ctx = Context(A, facts)
    res = run_cell(ctx, ctx.cell(WORLD, REGIME, CAPEX))
    last_h1, last = res.rows[-2], res.rows[-1]
    J, NW, R, M, C = A["joint"], A["network"], A["revenue"], A["margin"], A["capex"]
    WC, TX, FN = A["working_capital"], A["tax"], A["financing"]
    W = A["worlds"][WORLD]
    lastp = A["meta"]["last_period"]
    demand = J["regime_demand"][REGIME]
    credit = J["world_links"][WORLD]["credit"]
    tau, L = TX["rate"], C["asset_life_years"]
    r = W["zero_curve"]["LT"] + A["valuation"]["beta_u"] * A["valuation"]["erp"]
    pi = W["lt"]["inflation"]
    k = R["ticket_k"][demand]
    ref = A["worlds"][R["homogeneity"]["reference_world"]]["lt"]["inflation"]
    ticket = (k * W["food_cpi"][lastp] + path_value(R["ticket_shift"][demand], lastp)
              + path_value(R["vat_effect"], lastp) + (1 - k) * (pi - ref))
    traffic = path_value(R["traffic"][demand], lastp)
    cl = path_value(NW["close_rate"], lastp)
    g = (1 + ticket) * (1 + traffic) * (1 + (NW["new_space_density"] - NW["closed_productivity"]) * cl) - 1
    g = min(g, r - 0.0001)
    first = res.rows[0]
    area_mid0 = (ctx.facts.area_end[ctx.anchor] + first.area_end) / 2
    x0 = area_mid0 * first.price_index / first.revenue_annual
    mnt = path_value(C["maintenance"][CAPEX], lastp)
    phi, area = C["maintenance_area_share"], last.area_end
    idx, prev_rev = last.price_index, last.revenue
    nwc_prev, opc_prev = last.nwc, last.opcash
    target = path_value(M["targets"][REGIME], lastp)
    halves = []
    for h, src in ((1, last_h1), (2, last)):
        idx *= 1 + half_rate(pi)
        rev = src.revenue * (1 + g)
        ann = rev + prev_rev
        prev_rev = rev
        physical = rev * mnt * phi * (area * idx / ann) / x0
        repl = area * cl / 2 * C["price_per_m2"] * idx
        capex = rev * mnt * (1 - phi) + physical + repl
        nwc = path_value(WC["nwc_pct"], lastp) * ann + (WC["h1_excess_pct"] * ann if h == 1 else 0)
        opc = WC["operating_cash_pct"] * ann
        s = M["seasonal_h1_pp"]
        ebitda = rev * (target + (s if h == 1 else -s))
        halves.append(dict(rev=rev, ebitda=ebitda, lti=M["lti_pct"] * rev, capex=capex,
                           cpi=repl + physical, dn=nwc - nwc_prev, do=opc - opc_prev,
                           extra=(WC["lease_adj_pct"] + C["disposal_proceeds_pct"]) * rev))
        nwc_prev, opc_prev = nwc, opc

    def ratio(x):
        return 1.0 if x == 0 else (1 - (1 + x) ** (-L)) / (L * x)

    cap = sum(h["capex"] for h in halves)
    cpi_ann = sum(h["cpi"] for h in halves)
    da_h = ((cap - cpi_ann) * ratio(g) + cpi_ann * ratio(pi)) / 2
    da_pi = cpi_ann * ratio(pi) / 2
    F, f = [], []
    for h in halves:
        base = h["ebitda"] - h["lti"] - da_h + TX["permanent_add_pct"] * h["rev"]
        F.append(h["ebitda"] - h["lti"] - tau * max(0, base) - h["capex"] - h["dn"] - h["do"]
                 + h["extra"])
        f.append((tau * da_pi if base > 0 else 0) - h["cpi"])

    def gordon(a, b, x):
        return (a * (1 + r) ** 0.75 + b * (1 + r) ** 0.25) / (r - x)

    tv = gordon(F[0] - f[0], F[1] - f[1], g) + gordon(f[0], f[1], pi)
    rep = sum(h["ebitda"] - h["lti"] for h in halves)
    s_t = tau * FN["target_leverage"] * rep * (W["zero_curve"]["LT"] + FN["spread_fixed"][credit])
    T = res.terminal
    assert T.growth == pytest.approx(g, rel=1e-12)
    assert T.tv_flow == pytest.approx(tv, rel=1e-10)
    assert T.tv_shield == pytest.approx(gordon(s_t / 2, s_t / 2, g), rel=1e-10)
