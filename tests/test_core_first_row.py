"""Первое прогнозное полугодие клетки — вручную по формулам docs/MODEL.md §4.

Независимый пересчёт строки (без кэшей ядра): ловит сдвиг индексов когорт,
базы «год к году», D&A, процентов и дивидендов.
"""

from __future__ import annotations

import copy
import dataclasses
import datetime as dt
import math

import pytest

from model.book import (half_rate, interp_curve, next_period, path_value, period_index,
                        period_start, prev_period)
from model.core import Context, run_cell
from model.facts import core_facts

WORLD, REGIME, CAPEX = "H", "floor", "base"


@pytest.mark.parametrize("world", ["H", "M"])
def test_first_half_year_by_hand(book, facts, world):
    """Мир H — кредитное состояние base (X = 0), M — stress (проценты сверх спреда)."""
    WORLD = world
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

    # §4.1 сеть: индекс от конца S — зрелая площадь S с плотностью 1 плюс последние n когорт
    # с d × μ_возраста, дальше рекурсия с закрытиями (κ) по историческим полугодиям до якоря
    mu = NW["maturity_curve"]
    n = len(mu) - 1
    O = cf.gross_opened
    d, kappa = NW["new_space_density"], NW["closed_productivity"]

    def back(q, a):
        for _ in range(a):
            q = prev_period(q)
        return q

    S = cf.eff_start
    eff_hist = {S: cf.area_end[S] - sum(O[back(S, a)] * (1 - d * mu[a]) for a in range(n))}
    q = S
    while q != anchor:
        q = next_period(q)
        mat = sum(O[back(q, a)] * d * (mu[a] - mu[a - 1]) for a in range(1, n + 1))
        eff_hist[q] = eff_hist[prev_period(q)] - cf.closed_area[q] * kappa + O[q] * d * mu[0] + mat

    a0 = cf.area_end[anchor]
    closed = a0 * path_value(NW["close_rate"], p1) / 2
    opened = a0 * path_value(NW["net_growth"][tariff], p1) / 2 + closed
    maturing = sum(O[back(anchor, a - 1)] * d * (mu[a] - mu[a - 1]) for a in range(1, n + 1))
    eff1 = eff_hist[anchor] - closed * kappa + opened * d * mu[0] + maturing
    avg1 = (eff_hist[anchor] + eff1) / 2
    avg_base = (eff_hist[back2] + eff_hist[back1]) / 2

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

    # §4.5 capex и D&A: физическая доля — рубли якоря × средняя площадь × индекс цен
    idx = 1 + half_rate(W["cpi"][p1])
    ltm_a = cf.revenue[back1] + cf.revenue[anchor]
    a_mid = (a0 + (a0 + opened - closed)) / 2
    phi = C["maintenance_area_share"]
    maint = path_value(C["maintenance"][CAPEX], p1) * ((1 - phi) * rev
                                                       + phi * ltm_a / 2 * a_mid * idx / a0)
    growth = opened * C["price_per_m2"] * idx
    infra = (max(0, opened - closed) * C["infra_per_m2"] * idx
             if int(p1[:4]) >= C["infra_from_year"] else 0.0)
    capex = maint + growth + infra
    # база D&A якоря выбывает по когортам: в первом полугодии уходит когорта якорь − 2L
    L2 = int(2 * C["asset_life_years"])
    hist = {h["period"]: h["revenue"] * h["capex_pct"] for h in facts.data["history"]["halves"]}
    cohorts = [hist[back(anchor, j)] for j in range(1, L2 + 1)]
    da = cf.da_anchor * (1 - cohorts[-1] / sum(cohorts)) + cf.capex_anchor / L2

    # §4.6–4.8
    nwc = path_value(WC["nwc_pct"], p1) * rev_ann + (WC["h1_excess_pct"] * rev_ann if p1[5] == "1" else 0)
    opc, opc_a = WC["operating_cash_pct"] * rev_ann, WC["operating_cash_pct"] * ltm_a
    base = ebitda - da - lti + TX["permanent_add_pct"] * rev
    tax_u = TX["rate"] * max(0, base)
    fcff = (ebitda - lti - tax_u - capex - (nwc - cf.nwc) - (opc - opc_a)
            + WC["lease_adj_pct"] * rev + C["disposal_proceeds_pct"] * rev)

    # §4.9 проценты, щит, долг. Дата кривой внутри первого полугодия ⇒ s = 0: форвард
    # трёхлетнего фикса = узел 3 года.
    assert dt.date.fromisoformat(A["meta"]["curve_as_of"]) >= period_start(p1)
    lw, fs = path_value(FN["legacy_weight"], p1), path_value(FN["fixed_share"], p1)
    key = W["key_rate"][p1]

    def rate_of(state, ic):
        fixed = lw * FN["legacy_rate"] + (1 - lw) * (W["zero_curve"]["3"] + FN["spread_fixed"][state] + ic)
        return fs * fixed + (1 - fs) * (key + FN["spread_float"][state] + ic)

    ic = FN["issuance_cost"]
    rate = rate_of(credit, ic)
    buf_a = FN["cash_buffer_pct"] * ltm_a
    nd_a = cf.net_debt + cf.dividends_payable
    gross = nd_a + opc_a + buf_a
    interest = gross * half_rate(rate) - buf_a * half_rate(FN["cash_yield_k"] * key)
    shield = tax_u - TX["rate"] * max(0, base - interest)
    issuance = max(0, gross) * (half_rate(rate) - half_rate(rate_of(credit, 0.0)))
    excess = max(0, gross) * max(0, half_rate(rate) - half_rate(rate_of("base", ic)))
    carry = buf_a * (half_rate(key) - half_rate(FN["cash_yield_k"] * key))
    nd_pre = nd_a - (fcff + shield - interest)
    ltm = (ebitda - lti) + cf.ebitda_rep[anchor]
    pays = p1 >= FN["dividends_from"]
    div = max(0, FN["target_leverage"] * ltm - nd_pre) if pays else 0.0

    ctx = Context(A, cf)
    row = run_cell(ctx, ctx.cell(WORLD, REGIME, CAPEX)).rows[0]
    expect = dict(revenue=rev, ticket=ticket, eff_area_avg=avg1, margin=margin, adj_ebitda=ebitda,
                  capex_maintenance=maint, capex_growth=growth, capex_infra=infra, da=da,
                  nwc=nwc, opcash=opc, tax_unlevered=tax_u, fcff=fcff, debt_rate=rate,
                  interest=interest, shield=shield, dividends=div, net_debt=nd_pre + div,
                  issuance_cost=issuance, excess_spread=excess, buffer_carry=carry)
    for name, value in expect.items():
        assert getattr(row, name) == pytest.approx(value, rel=1e-12, abs=1e-12), name


def test_immature_space_matures_with_density_d(book, facts):
    """Без роста и закрытий эффективный индекс за n полугодий дозревает: к индексу якоря
    добавляется незрелая часть последних n исторических когорт с плотностью d, дальше
    индекс стоит."""
    B = copy.deepcopy(book)
    for t in B["network"]["net_growth"]:
        B["network"]["net_growth"][t] = {"LT": 0.0}
    B["network"]["close_rate"] = {"LT": 0.0}
    ctx = Context(B, facts)
    net = ctx.network("mid")
    NW = B["network"]
    mu, d = NW["maturity_curve"], NW["new_space_density"]
    n = len(mu) - 1
    area = ctx.facts.area_end[ctx.anchor]
    assert all(a == area for a in net.area_end)
    O = list(ctx.facts.gross_opened.values())
    mature = net.eff_hist[ctx.anchor] + sum(O[-1 - a] * d * (1 - mu[a]) for a in range(n))
    assert net.eff_avg[n] == pytest.approx(mature, rel=1e-14)
    assert net.eff_avg[n + 1] == pytest.approx(mature, rel=1e-14)
    assert net.eff_avg[0] < mature < area


def test_history_index_at_unit_density_is_area_minus_immature(book, facts):
    """При d = κ = 1 рекурсия §4.1 по истории (площадь S, открытия, закрытия) даёт в каждом
    полугодии площадь минус незрелую часть последних n когорт: факты сети согласованы
    (открытия = чистый прирост + закрытия), индекс — та же площадь в других единицах."""
    B = copy.deepcopy(book)
    B["network"]["new_space_density"] = 1.0
    B["network"]["closed_productivity"] = 1.0
    ctx = Context(B, facts)
    net = ctx.network("mid")
    mu = B["network"]["maturity_curve"]
    n = len(mu) - 1
    O = ctx.facts.gross_opened
    area = {**facts.data["network"].get("area_end_est", {}), **facts.data["network"]["area_end"]}

    def back(q, a):
        for _ in range(a):
            q = prev_period(q)
        return q

    assert list(net.eff_hist) == sorted(net.eff_hist)
    assert next(iter(net.eff_hist)) == ctx.facts.eff_start
    for q, eff in net.eff_hist.items():
        assert eff == pytest.approx(area[q] - sum(O[back(q, a)] * (1 - mu[a]) for a in range(n)),
                                    abs=1e-5), q


def test_history_cohorts_mature_to_density_d(book, facts):
    """§4.1 (книга 1.1.1, [2] раунда 2): индекс истории в закрытой форме при d и κ книги —
    зрелая площадь S (без последних n когорт) с плотностью 1, минус закрытия с κ, плюс
    КАЖДАЯ когорта с S − n + 1 с d × μ_возраста (к зрелости — d, как когорты прогноза).
    Прежний старт (незрелая часть с d) давал когортам S − n + 1 … S зрелость 1."""
    ctx = Context(book, facts)
    net = ctx.network("mid")
    NW = book["network"]
    mu, d, kappa = NW["maturity_curve"], NW["new_space_density"], NW["closed_productivity"]
    n = len(mu) - 1
    cf = ctx.facts
    S = cf.eff_start
    O = cf.gross_opened
    mature_S = cf.area_end[S] - sum(O[q] for q in list(O)[:n])
    assert list(O)[n - 1] == S
    for q, eff in net.eff_hist.items():
        closed = sum(cf.closed_area[c] for c in cf.closed_area
                     if period_index(c) <= period_index(q))
        cohorts = sum(O[c] * d * mu[min(n, period_index(q) - period_index(c))]
                      for c in O if period_index(c) <= period_index(q))
        assert eff == pytest.approx(mature_S - kappa * closed + cohorts, rel=1e-13), q
    # созревшая когорта S весит d, а не 1: +1 к O(S) при той же A(S) — −(1 − d) на якоре
    assert period_index(ctx.anchor) - period_index(S) >= n
    bumped_facts = dataclasses.replace(cf, gross_opened={**O, S: O[S] + 1.0})
    bumped = Context(book, bumped_facts).network("mid").eff_hist[ctx.anchor]
    assert bumped - net.eff_hist[ctx.anchor] == pytest.approx(-(1.0 - d), abs=1e-9)


@pytest.mark.parametrize("regime", ["floor", "stress"])
def test_terminal_by_hand(book, facts, regime):
    """Терминал §6 вручную по строкам клетки: D&A по полугодиям и частям g/π, разделение
    Гордона доналогового потока, налог и щит — прямой суммой τ·max(0, base) по полугодиям
    800 лет (D&A — правило когорт §4.5, продолженное capex терминала), вычеты финансирования
    оператором явного участка. Режим floor — g > π, stress — g < π (база терминала со
    временем уходит в минус)."""
    REGIME = regime
    A = book
    cf = core_facts(facts, book)
    ctx = Context(A, cf)
    world = "M"                                  # кредитное состояние stress: все три вычета
    res = run_cell(ctx, ctx.cell(world, REGIME, CAPEX))
    rows = res.rows
    last_h1, last = rows[-2], rows[-1]
    J, NW, R, M, C = A["joint"], A["network"], A["revenue"], A["margin"], A["capex"]
    WC, TX, FN = A["working_capital"], A["tax"], A["financing"]
    W = A["worlds"][world]
    lastp = A["meta"]["last_period"]
    demand = J["regime_demand"][REGIME]
    credit = J["world_links"][world]["credit"]
    tau, L = TX["rate"], C["asset_life_years"]
    L2 = int(2 * L)
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
    anchor = A["meta"]["anchor_period"]
    ltm_a = cf.revenue[prev_period(anchor)] + cf.revenue[anchor]
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
        physical = mnt * phi * ltm_a / 2 * area * idx / cf.area_end[anchor]
        repl = area * cl / 2 * C["price_per_m2"] * idx
        capex = rev * mnt * (1 - phi) + physical + repl
        nwc = path_value(WC["nwc_pct"], lastp) * ann + (WC["h1_excess_pct"] * ann if h == 1 else 0)
        opc = WC["operating_cash_pct"] * ann
        s = M["seasonal_h1_pp"]
        ebitda = rev * (target + (s if h == 1 else -s))
        halves.append(dict(rev=rev, ann=ann, ebitda=ebitda, lti=M["lti_pct"] * rev, capex=capex,
                           cpi=repl + physical, dn=nwc - nwc_prev, do=opc - opc_prev, opc=opc,
                           extra=(WC["lease_adj_pct"] + C["disposal_proceeds_pct"]) * rev))
        nwc_prev, opc_prev = nwc, opc

    def ratio(x):
        return 1.0 if x == 0 else (1 - (1 + x) ** (-L)) / (L * x)

    cg = [h["capex"] - h["cpi"] for h in halves]
    cp = [h["cpi"] for h in halves]
    dg = [(cg[0] + cg[1]) * ratio(g) / 2, (cg[0] * (1 + g) + cg[1]) * ratio(g) / 2]
    dp = [(cp[0] + cp[1]) * ratio(pi) / 2, (cp[0] * (1 + pi) + cp[1]) * ratio(pi) / 2]

    # ставка терминала: последние f, ℓ, key; фиксированная нога — узел LT
    f_T, l_T = path_value(FN["fixed_share"], lastp), path_value(FN["legacy_weight"], lastp)
    key_T = W["key_rate"][lastp]

    def r_T(state, ic):
        fixed = l_T * FN["legacy_rate"] + (1 - l_T) * (W["zero_curve"]["LT"] + FN["spread_fixed"][state] + ic)
        return f_T * fixed + (1 - f_T) * (key_T + FN["spread_float"][state] + ic)

    ic, lt, bp = FN["issuance_cost"], FN["target_leverage"], FN["cash_buffer_pct"]
    rT, rT0, rTf = r_T(credit, ic), r_T(credit, 0.0), r_T("base", ic)
    y_T = half_rate(FN["cash_yield_k"] * key_T)
    rep_T1 = halves[0]["ebitda"] - halves[0]["lti"]
    starts = [(last_h1.ebitda_rep + last.ebitda_rep, last.opcash, bp * last.revenue_annual),
              (last.ebitda_rep + rep_T1, halves[0]["opc"], bp * halves[0]["ann"])]
    F, S, fin, pre, before, interest = [], [], [0.0, 0.0], [], [], []
    for i, h in enumerate(halves):
        B = h["ebitda"] - h["lti"] + TX["permanent_add_pct"] * h["rev"]     # база до D&A
        base = B - dg[i] - dp[i]
        before.append(B)
        F.append(h["ebitda"] - h["lti"] - tau * max(0, base) - h["capex"] - h["dn"] - h["do"]
                 + h["extra"])
        pre.append(F[-1] + tau * max(0, base))                             # до налога
        ltm, opc0, buf0 = starts[i]
        G = lt * ltm + opc0 + buf0
        I = G * half_rate(rT) - buf0 * y_T
        interest.append(I)
        S.append(tau * max(0, base) - tau * max(0, base - I))
        fin[i] = (max(0, G) * (half_rate(rT) - half_rate(rT0))
                  + max(0, G) * max(0, half_rate(rT) - half_rate(rTf))
                  + buf0 * (half_rate(key_T) - y_T))

    # налог терминала: τ·max(0, base) по полугодиям 800 лет; D&A — правило когорт §4.5
    # (база якоря, capex явного участка), продолженное capex терминала; щит — то же с
    # процентами, растущими с g
    hist = {h["period"]: h["revenue"] * h["capex_pct"] for h in facts.data["history"]["halves"]}
    old = []
    q = anchor
    for _ in range(L2):
        q = prev_period(q)
        old.append(hist[q])                     # якорь−1, якорь−2, …
    seq = [cf.capex_anchor] + [row.capex for row in rows]
    N = len(rows)
    tax, tax_lev, signs = [], [], set()
    for j in range(1, 1601):
        n, h = divmod(j - 1, 2)
        kk = N + j
        alive = math.fsum(old[:max(0, L2 - kk)]) / math.fsum(old)
        rule = cf.da_anchor * alive + math.fsum(seq[kk - min(kk, L2):kk]) / L2
        base = before[h] * (1 + g) ** n - rule
        disc = (1 + r) ** -(n + 0.25 + 0.5 * h)
        tax.append(tau * max(0.0, base) * disc)
        tax_lev.append(tau * max(0.0, base - interest[h] * (1 + g) ** n) * disc)
        signs.add(base > 0)
        seq.append(cg[h] * (1 + g) ** n + cp[h] * (1 + pi) ** n)

    def gordon(a, b, x):
        return (a * (1 + r) ** 0.75 + b * (1 + r) ** 0.25) / (r - x)

    tv_tax = math.fsum(tax)
    tv = gordon(pre[0] + cp[0], pre[1] + cp[1], g) + gordon(-cp[0], -cp[1], pi) - tv_tax
    T = res.terminal
    assert T.growth == pytest.approx(g, rel=1e-12)
    assert (g > pi) == (regime == "floor")
    assert signs == ({True} if regime == "floor" else {True, False})
    assert [x.da for x in T.halves] == pytest.approx([dg[0] + dp[0], dg[1] + dp[1]], rel=1e-12)
    assert T.tv_tax == pytest.approx(tv_tax, rel=1e-10)
    assert T.tv_flow == pytest.approx(tv, rel=1e-10)
    assert T.tv_shield == pytest.approx(tv_tax - math.fsum(tax_lev), rel=1e-9)
    if regime == "floor":                       # база с процентами положительна всегда
        assert T.tv_shield == pytest.approx(gordon(S[0], S[1], g), rel=1e-9)
    assert T.tv_financing == pytest.approx(gordon(fin[0], fin[1], g), rel=1e-10)
    assert T.tv_excess_spread > 0 and T.tv_issuance > 0 and T.tv_buffer_carry > 0
