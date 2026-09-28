"""Тождества прохода клетки: FCFF, путь долга, требования, вероятности, щит."""

from __future__ import annotations

import copy
import dataclasses
import datetime as dt
import math

import pytest

from model.core import Context, run_cell
from model.facts import core_facts
from model.grid import LAYERS, evaluate

REL = 1e-12


def _close(a, b, rel=REL):
    return abs(a - b) <= rel * max(1.0, abs(a), abs(b))


def test_fcff_is_sum_of_published_lines(grid):
    for c in grid.cells:
        for r in c.result.rows:
            rebuilt = (r.adj_ebitda - r.lti - r.tax_unlevered - r.capex - r.nwc_change
                       - r.opcash_change + r.lease + r.proceeds)
            assert _close(rebuilt, r.fcff), (c.key, r.period)
            assert _close(r.adj_ebitda, r.revenue * r.margin)
            assert _close(r.ebit, r.adj_ebitda - r.da)
            assert _close(r.shield, r.tax_unlevered - r.tax_actual)
        for h in c.result.terminal.halves:
            rebuilt = (h.adj_ebitda - h.lti - h.tax - h.capex - h.nwc_change - h.opcash_change
                       + h.lease + h.proceeds)
            assert _close(rebuilt, h.fcff), (c.key, h.period)


def test_debt_path_identity(grid):
    cf = grid.ctx.facts
    for c in grid.cells:
        nd = cf.net_debt + cf.dividends_payable
        for r in c.result.rows:
            assert _close(r.net_debt_pre, nd - (r.fcff + r.shield - r.interest)), (c.key, r.period)
            assert _close(r.net_debt, r.net_debt_pre + r.dividends)
            assert r.dividends >= 0
            nd = r.net_debt


def test_dividends_keep_leverage_at_or_below_target(grid, book):
    """Дивиденд модели возвращает долг КОМПАНИИ (ND модели без прироста операционной
    кассы, §4.9) ровно на целевой рычаг."""
    target = book["financing"]["target_leverage"]
    opc0 = grid.ctx.opcash_anchor
    for c in grid.cells:
        for r in c.result.rows:
            if r.dividends > 0:
                assert _close(r.net_debt - (r.opcash - opc0), target * r.ebitda_rep_ltm, 1e-10)


def test_ev_is_sum_of_parts(grid):
    """EV = PV FCFF + PV щита + PV терминала − вычеты финансирования (§5)."""
    for c in grid.cells:
        r = c.result
        end = grid.ctx.world(r.cell.world).df_end
        assert _close(r.ev, r.pv_fcff + r.pv_shield + r.pv_terminal - r.pv_issuance
                      - r.pv_excess_spread - r.pv_buffer_carry)
        assert _close(r.pv_terminal, (r.terminal.tv_flow + r.terminal.tv_shield) * end)
        rows = r.rows
        for name, attr, tv in (("issuance_cost", "pv_issuance", r.terminal.tv_issuance),
                               ("excess_spread", "pv_excess_spread", r.terminal.tv_excess_spread),
                               ("buffer_carry", "pv_buffer_carry", r.terminal.tv_buffer_carry)):
            pv = math.fsum(getattr(x, name) * x.fraction * x.df for x in rows) + tv * end
            assert _close(getattr(r, attr), pv), name
        net = (r.terminal.tv_flow + r.terminal.tv_shield - r.terminal.tv_financing) * end
        assert _close(r.terminal_share, net / r.ev)
    for L in grid.layers.values():
        assert _close(L.v0, math.fsum(x for _, x in L.ev_parts))


def test_financing_deductions_by_row(grid):
    """C_iss, X, K полугодия — по формулам §4.9 из опубликованных строк."""
    ctx = grid.ctx
    FN = ctx.A["financing"]
    for c in grid.cells:
        cell = c.result.cell
        rp = ctx.rates(cell.world, cell.credit)
        buf = ctx.buffer_anchor
        for i, r in enumerate(c.result.rows):
            debt = max(0.0, r.gross_debt_start)
            assert _close(r.issuance_cost, debt * (rp.half_debt[i] - rp.half_clean[i]))
            assert _close(r.excess_spread, debt * max(0.0, rp.half_debt[i] - rp.half_fair[i]))
            assert _close(r.buffer_carry, buf * (rp.half_key[i] - rp.half_yield[i]))
            if cell.credit == "base":
                assert r.excess_spread == 0.0
            assert r.issuance_cost >= 0.0 and r.buffer_carry >= 0.0
            buf = r.buffer
        assert float(FN["cash_buffer_pct"]) == 0 or c.result.pv_buffer_carry > 0


def test_claims_composition(grid):
    ctx = grid.ctx
    cf, T = ctx.facts, ctx.timing
    for c in grid.cells:
        rows = c.result.rows
        rolled = math.fsum([*(r.fcff + r.shield - r.interest for r in rows[:T.closed]),
                            T.elapsed * (rows[T.closed].fcff + rows[T.closed].shield
                                         - rows[T.closed].interest)])
        bridge = math.fsum(b.amount for b in cf.bridge if b.included)
        expect = cf.net_debt + ctx.opcash_anchor + bridge - rolled + ctx.dividends_declared
        assert _close(c.result.claims.total, expect)
        assert _close(c.result.equity, c.result.ev - c.result.claims.total)


def test_declared_dividend_enters_claims_on_ex_date(book, facts):
    """Объявленный дивиденд — требование с даты отсечки, если деньги были в компании."""
    from model.facts import DeclaredDividend

    cf = core_facts(facts, book)
    ex = dt.date.fromisoformat(book["meta"]["valuation_date"]) - dt.timedelta(days=3)
    amount = cf.dividends_payable
    cf = dataclasses.replace(cf, dividends=(
        DeclaredDividend(id="в компании", amount=amount, ex_date=ex, in_company=True),
        DeclaredDividend(id="выплачен до якоря", amount=amount, ex_date=ex, in_company=False),
        DeclaredDividend(id="без даты", amount=amount, ex_date=None, in_company=True)))
    before = Context(book, cf, valuation_date=ex - dt.timedelta(days=1))
    on = Context(book, cf, valuation_date=ex)
    assert before.dividends_declared == 0.0
    assert on.dividends_declared == amount
    cell = on.cell("H", "floor", "base")
    assert _close(run_cell(on, cell).claims.dividends, amount)


def test_layer_probabilities_sum_to_one(grid):
    for layer in LAYERS:
        assert abs(math.fsum(c.p[layer] for c in grid.cells) - 1.0) <= 1e-12
    assert abs(math.fsum(grid.regime_posterior.values()) - 1.0) <= 1e-12
    assert len(grid.cells) == 36


def test_regimes_same_in_all_layers(grid, book):
    capex_p = book["joint"]["capex_prob_given_regime"]
    for c in grid.cells:
        cell = c.result.cell
        for layer in LAYERS:
            w = grid.layers[layer].world_weights[cell.world]
            if w:
                implied = c.p[layer] / (w * capex_p[cell.regime][cell.capex])
                assert _close(implied, grid.regime_posterior[cell.regime])


def test_zero_debt_gives_zero_shield(book, facts):
    """Нет долга и кассы, дивиденды держат ЧД = 0 ⇒ проценты и щит — ровно ноль."""
    B = copy.deepcopy(book)
    FN = B["financing"]
    FN["target_leverage"] = 0.0
    FN["cash_buffer_pct"] = 0.0
    FN["dividends_from"] = B["meta"]["first_period"]
    B["working_capital"]["operating_cash_pct"] = 0.0
    cf = core_facts(facts, B)
    cf = dataclasses.replace(cf, net_debt=-cf.dividends_payable)
    ctx = Context(B, cf)
    checked = 0
    for world in ("H", "M"):
        for regime in ("floor", "partial", "full"):
            res = run_cell(ctx, ctx.cell(world, regime, "base"))
            for r in res.rows:
                if r.gross_debt_start == 0.0:
                    assert r.interest == 0.0 and r.shield == 0.0
                    assert r.issuance_cost == r.excess_spread == r.buffer_carry == 0.0
                    checked += 1
            for h in res.terminal.halves:
                assert h.gross_debt_start == 0.0 and h.shield == 0.0
            T = res.terminal
            assert T.tv_shield == T.tv_issuance == T.tv_excess_spread == T.tv_buffer_carry == 0.0
            if all(r.gross_debt_start == 0.0 for r in res.rows):
                assert res.pv_shield == 0.0 and res.pv_financing == 0.0
    assert checked > 0


def test_price_uses_governance_only_on_positive_equity(grid, book, facts):
    """Цена (§7.2): [(EV − D) + n·k·P_рынок/1000]·(1 − g)·1000/(N + n); без (1 − g) при ≤ 0."""
    ctx = grid.ctx
    g, N = ctx.governance, ctx.facts.shares_mln
    n = facts.data["shares"]["treasury"]
    T = n * book["valuation"]["treasury_sale_price_k"] * ctx.market_price / 1000
    assert _close(ctx.treasury_value, T)
    for c in grid.cells:
        e = c.result.equity + T
        want = (e * (1 - g) if e > 0 else e) * 1000 / (N + n)
        assert _close(c.result.price, want)
    L = grid.layers["analytical"]
    e = L.v0 - L.d + T
    assert _close(L.price, e * (1 - g) * 1000 / (N + n))


def test_no_treasury_gives_the_old_price_formula():
    """n = 0 ⇒ цена = капитал·(1 − g)·1000/N (формула книги 1.0)."""
    from model.core import price_of_equity, rub_per_1pct_ev, v0_from_price

    for e in (-50.0, 0.0, 950.0):
        want = (e * (1 - 0.03) if e > 0 else e) * 1000 / 245.0
        assert _close(price_of_equity(e, 0.03, 245.0), want)
        assert _close(price_of_equity(e, 0.03, 245.0, 0.0, 0.0), want)
    assert _close(v0_from_price(3800.0, 400.0, 0.03, 245.0), 3800 * 245 / 1000 / 0.97 + 400)
    assert _close(rub_per_1pct_ev(1400.0, 0.03, 245.0), 14.0 * 0.97 * 1000 / 245)


def test_treasury_sale_at_value_is_neutral():
    """Продажа пакета по стоимости акции нейтральна; ниже стоимости — размывание (§7.2)."""
    from model.core import price_of_equity

    N, n, g, equity = 246.0, 25.6, 0.02, 950.0
    value = price_of_equity(equity, g, N) / (1 - g)            # стоимость акции до дисконта
    at_value = price_of_equity(equity, g, N, n, n * value / 1000)
    assert _close(at_value, price_of_equity(equity, g, N))
    cheap = price_of_equity(equity, g, N, n, n * 0.5 * value / 1000)
    assert _close(cheap / at_value, 1 - n / (N + n) * (1 - 0.5))


def test_point_is_between_layers(grid, book):
    P = grid.point
    lam = book["joint"]["lambda"]
    assert _close(P.central, P.low + lam * (P.high - P.low))
    assert _close(P.low, grid.layers["macro_neutral"].price)
    assert _close(P.high, grid.layers["analytical"].price)


def test_v_star_inverts_price_function(grid):
    ctx, P = grid.ctx, grid.point
    d = grid.layers["analytical"].d
    assert _close(ctx.price_of(P.v_star - d), ctx.market_price)
    assert _close(ctx.price_of(P.v0_point - d), P.central)
    # цена 1 % EV — наклон функции «EV → цена»
    assert _close(ctx.price_of(1.01 * P.v0_point - d) - P.central, P.rub_per_1pct_ev_point, 1e-9)


def test_layer_prices_shortcut_matches_grid(book, facts, grid):
    from model.grid import layer_prices

    assert layer_prices(book, facts) == (grid.point.low, grid.point.high)


def test_v0_from_price_both_signs(grid):
    ctx = grid.ctx
    for price in (-500.0, 0.0, 1234.5):
        v0 = ctx.v0_of(price, 400.0)
        assert _close(ctx.price_of(v0 - 400.0), price)


def test_claims_roll_over_closed_half_years(book, facts):
    """Дата оценки в третьем полугодии: два закрытых полугодия целиком + доля текущего."""
    from model.book import period_start, periods

    P = periods(book["meta"]["first_period"], book["meta"]["last_period"])
    v = period_start(P[2]) + dt.timedelta(days=40)
    grid = evaluate(book, facts, valuation_date=v)
    T = grid.ctx.timing
    assert T.closed == 2 and 0 < T.elapsed < 1
    for c in grid.cells:
        rows = c.result.rows
        cash = [r.fcff + r.shield - r.interest for r in rows[:2]]
        cash.append(T.elapsed * (rows[2].fcff + rows[2].shield - rows[2].interest))
        assert _close(c.result.claims.rolled, math.fsum(cash))
        pv = math.fsum((r.fcff + r.shield) * r.fraction * r.df for r in rows)
        assert rows[0].fraction == rows[1].fraction == 0.0
        assert _close(c.result.pv_fcff + c.result.pv_shield, pv)
        # вычеты финансирования закрытых полугодий в EV тоже не входят
        end = grid.ctx.world(c.result.cell.world).df_end
        assert _close(c.result.pv_issuance,
                      math.fsum(r.issuance_cost * r.fraction * r.df for r in rows)
                      + c.result.terminal.tv_issuance * end)


def _cell_ev(book, facts, key):
    ctx = Context(book, facts)
    return run_cell(ctx, ctx.cell(*key.split("|"))).ev


def test_stress_spread_does_not_raise_ev(book, facts):
    """Рост спреда stress (сверх справедливого) не повышает EV: щит растёт на τ·ΔI, вычет X
    — на ΔI (урок Магнита 1.2 #4, A-F1f)."""
    from model.book import override

    stressed = [w for w, link in book["joint"]["world_links"].items() if link["credit"] == "stress"]
    assert stressed, "в книге нет мира с кредитным состоянием stress"
    up = override(book, ["financing.spread_fixed.stress", "financing.spread_float.stress"],
                  "shift", 0.05)
    for w in stressed:
        for regime in ("stress", "floor", "full"):
            key = f"{w}|{regime}|base"
            assert _cell_ev(up, facts, key) < _cell_ev(book, facts, key), key


def test_issuance_cost_lowers_ev(book, facts):
    """Издержки размещения — деньги третьим лицам: рост ic снижает EV (нетто щита)."""
    from model.book import override

    up = override(book, ["financing.issuance_cost"], "value",
                  float(book["financing"]["issuance_cost"]) + 0.002)
    for w in ("N", "H", "M"):
        key = f"{w}|floor|base"
        assert _cell_ev(up, facts, key) < _cell_ev(book, facts, key), key


def test_cash_buffer_carry_has_the_right_sign(book, facts):
    """Подушка в долг под доходность ниже ключевой стоит ≤ 0; выше доходность — выше цена."""
    from model.book import override

    base = evaluate(book, facts).point.central
    k = float(book["financing"]["cash_yield_k"])
    buf = float(book["financing"]["cash_buffer_pct"])
    more = evaluate(override(book, ["financing.cash_buffer_pct"], "value", buf + 0.01), facts)
    richer = evaluate(override(book, ["financing.cash_yield_k"], "value", min(1.0, k + 0.05)), facts)
    assert more.point.central < base
    assert richer.point.central > base
