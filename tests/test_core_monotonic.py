"""Монотонности EV и однородность чека (docs/MODEL.md §4.2, §5, §6)."""

from __future__ import annotations

import copy

import pytest

from model.book import override
from model.book_schema import DEMANDS, REGIMES, WORLDS
from model.core import annuity_ratio, homogeneity
from model.grid import evaluate


def _evs(book, facts):
    return {c.key: c.result.ev for c in evaluate(book, facts).cells}


def test_higher_lt_margin_raises_ev(book, facts):
    base = _evs(book, facts)
    paths = [f"margin.targets.{r}.LT" for r in REGIMES]
    up = _evs(override(book, paths, "shift", 0.002), facts)
    assert all(up[k] > base[k] for k in base)


def test_higher_beta_lowers_ev(book, facts):
    base = _evs(book, facts)
    beta = book["valuation"]["beta_u"]
    up = _evs(override(book, ["valuation.beta_u"], "value", beta * 1.2 + 0.05), facts)
    assert all(up[k] < base[k] for k in base)


def test_higher_erp_lowers_ev(book, facts):
    base = _evs(book, facts)
    erp = book["valuation"]["erp"]
    up = _evs(override(book, ["valuation.erp"], "value", erp * 1.2 + 0.005), facts)
    assert all(up[k] < base[k] for k in base)


def test_homogeneity_zero_when_full_passthrough(book):
    B = copy.deepcopy(book)
    B["revenue"]["ticket_k"] = {s: 1.0 for s in DEMANDS}
    ramp = B["revenue"]["homogeneity"]
    for w in WORLDS:
        for s in DEMANDS:
            for year in (ramp["ramp_from"] - 1, ramp["ramp_to"], ramp["ramp_to"] + 5, None):
                assert homogeneity(B, w, s, year) == 0.0


def test_homogeneity_ramps_in_and_closes_the_gap(book):
    ramp = book["revenue"]["homogeneity"]
    ref = ramp["reference_world"]
    for w in WORLDS:
        for s in DEMANDS:
            k = book["revenue"]["ticket_k"][s]
            gap = book["worlds"][w]["lt"]["inflation"] - book["worlds"][ref]["lt"]["inflation"]
            assert homogeneity(book, w, s, ramp["ramp_from"]) == 0.0
            assert homogeneity(book, w, s, ramp["ramp_to"]) == pytest.approx((1 - k) * gap)
            assert homogeneity(book, w, s, None) == pytest.approx((1 - k) * gap)
        assert homogeneity(book, ref, DEMANDS[0], None) == 0.0


def test_terminal_ticket_gap_equals_inflation_gap_when_food_matches_lt(book, facts):
    """Однородность: в терминале чек мира отличается от эталонного на k·Δfood + (1−k)·Δπ."""
    grid = evaluate(book, facts)
    ctx = grid.ctx
    ref = book["revenue"]["homogeneity"]["reference_world"]
    for w in WORLDS:
        for s in DEMANDS:
            k = book["revenue"]["ticket_k"][s]
            a = ctx.revenue(w, "mid", s).ticket_lt
            b = ctx.revenue(ref, "mid", s).ticket_lt
            dfood = ctx.world(w).food_last - ctx.world(ref).food_last
            dpi = ctx.world(w).pi_lt - ctx.world(ref).pi_lt
            assert a - b == pytest.approx(k * dfood + (1 - k) * dpi, abs=1e-15)


def test_annuity_ratio_limit_and_continuity():
    assert annuity_ratio(0.0, 7.0) == 1.0
    assert annuity_ratio(1e-12, 7.0) == pytest.approx(1.0, abs=1e-10)
    assert annuity_ratio(-1e-12, 7.0) == pytest.approx(1.0, abs=1e-10)
    assert annuity_ratio(0.05, 10.0) == pytest.approx((1 - 1.05 ** -10) / 0.5, rel=1e-14)
