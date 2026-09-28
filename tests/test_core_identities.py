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
    for c in grid.cells:
        r = c.result
        assert _close(r.ev, r.pv_fcff + r.pv_shield + r.pv_terminal)
        assert _close(r.pv_terminal, (r.terminal.tv_flow + r.terminal.tv_shield)
                      * grid.ctx.world(r.cell.world).df_end)


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
    for regime in ("floor", "partial", "full"):
        res = run_cell(ctx, ctx.cell("H", regime, "base"))
        for r in res.rows:
            if r.gross_debt_start == 0.0:
                assert r.interest == 0.0 and r.shield == 0.0
                checked += 1
        assert res.terminal.shield_annual == 0.0 and res.terminal.tv_shield == 0.0
        if all(r.gross_debt_start == 0.0 for r in res.rows):
            assert res.pv_shield == 0.0
    assert checked > 0


def test_price_uses_governance_only_on_positive_equity(grid):
    ctx = grid.ctx
    g, shares = ctx.governance, ctx.facts.shares_mln
    for c in grid.cells:
        e = c.result.equity
        want = (e * (1 - g) if e > 0 else e) * 1000 / shares
        assert _close(c.result.price, want)


def test_point_is_between_layers(grid, book):
    P = grid.point
    lam = book["joint"]["lambda"]
    assert _close(P.central, P.low + lam * (P.high - P.low))
    assert _close(P.low, grid.layers["macro_neutral"].price)
    assert _close(P.high, grid.layers["analytical"].price)


def test_v_star_inverts_price_function(grid):
    from model.core import price_of_equity

    ctx, P = grid.ctx, grid.point
    d = grid.layers["analytical"].d
    assert _close(price_of_equity(P.v_star - d, ctx.governance, ctx.facts.shares_mln),
                  ctx.market_price)
    assert _close(price_of_equity(P.v0_point - d, ctx.governance, ctx.facts.shares_mln),
                  P.central)


def test_layer_prices_shortcut_matches_grid(book, facts, grid):
    from model.grid import layer_prices

    assert layer_prices(book, facts) == (grid.point.low, grid.point.high)


def test_v0_from_price_both_signs(grid):
    from model.core import price_of_equity
    from model.grid import v0_from_price

    ctx = grid.ctx
    g, shares = ctx.governance, ctx.facts.shares_mln
    for price in (-500.0, 0.0, 1234.5):
        v0 = v0_from_price(price, 400.0, g, shares)
        assert _close(price_of_equity(v0 - 400.0, g, shares), price)


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
