"""Отклонение маржи (§4.3) и обновление вероятностей режимов A-P2u (§10)."""

from __future__ import annotations

import math

import pytest

from model.book import add_observation, next_period
from model.book_schema import REGIMES
from model.core import Context
from model.grid import cap_shift, evaluate, regime_updates


def test_no_observations_keeps_prior(book, facts):
    assert not book["joint"]["regime_update"]["observations"]
    grid = evaluate(book, facts)
    assert grid.regime_posterior == grid.regime_prior


def test_deviation_decays_from_anchor_fact(book, facts):
    ctx = Context(book, facts)
    rho = book["margin"]["deviation_persistence"]
    for r in REGIMES:
        RG = ctx.regime(r)
        assert RG.deviation_anchor == pytest.approx(
            ctx.facts.margin_anchor - RG.target_anchor - RG.season_anchor, abs=1e-16)
        for k, dev in enumerate(RG.deviation, start=1):
            assert dev == pytest.approx(rho ** k * RG.deviation_anchor, abs=1e-15)


def test_fact_replaces_deviation(book, facts):
    first = book["meta"]["first_period"]
    B = add_observation(book, first, 0.061)
    ctx = Context(B, facts)
    rho = book["margin"]["deviation_persistence"]
    for r in REGIMES:
        RG = ctx.regime(r)
        assert RG.deviation[0] == 0.061 - RG.target[0] - RG.season[0]
        assert RG.target[0] + RG.season[0] + RG.deviation[0] == pytest.approx(0.061, abs=1e-15)
        assert RG.deviation[1] == pytest.approx(rho * RG.deviation[0], abs=1e-15)


def test_noisy_observation_moves_less(book, facts):
    first = book["meta"]["first_period"]
    fact = Context(add_observation(book, first, 0.066), facts).regime("floor").deviation[0]
    noisy = Context(add_observation(book, first, 0.066, 0.01), facts).regime("floor").deviation[0]
    prior = Context(book, facts).regime("floor").deviation[0]
    assert abs(noisy - prior) < abs(fact - prior)
    assert min(prior, fact) < noisy < max(prior, fact)


def test_low_margin_fact_shifts_to_stress_within_cap(book, facts):
    first = book["meta"]["first_period"]
    low = min(book["margin"]["targets"][r]["LT"] for r in REGIMES) - 0.005
    grid = evaluate(add_observation(book, first, low), facts)
    prior, post = grid.regime_prior, grid.regime_posterior
    cap = book["joint"]["regime_update"]["cap_pp"]
    assert post["stress"] > prior["stress"]
    assert max(abs(post[r] - prior[r]) for r in REGIMES) <= cap + 1e-12
    assert math.fsum(post.values()) == pytest.approx(1.0, abs=1e-12)


def test_cap_applies_after_each_observation(book, facts):
    first = book["meta"]["first_period"]
    low = min(book["margin"]["targets"][r]["LT"] for r in REGIMES) - 0.005
    B = add_observation(add_observation(book, first, low), next_period(first), low)
    ctx = Context(B, facts)
    post, steps = regime_updates(ctx)
    cap = book["joint"]["regime_update"]["cap_pp"]
    assert len(steps) == 2
    for s in steps:
        assert max(abs(s.posterior[r] - s.prior[r]) for r in REGIMES) <= cap + 1e-12
    assert steps[1].prior == steps[0].posterior
    assert post["stress"] - book["joint"]["regime_prob"]["stress"] > cap


def test_huge_se_barely_moves(book, facts):
    first = book["meta"]["first_period"]
    grid = evaluate(add_observation(book, first, 0.03, 1.0), facts)
    for r in REGIMES:
        assert grid.regime_posterior[r] == pytest.approx(grid.regime_prior[r], abs=1e-4)


def test_cap_shift_rules():
    prior = {"a": 0.25, "b": 0.40, "c": 0.30, "d": 0.05}
    post = {"a": 0.90, "b": 0.05, "c": 0.04, "d": 0.01}
    out = cap_shift(prior, post, 0.10)
    assert math.fsum(out.values()) == pytest.approx(1.0, abs=1e-15)
    assert max(abs(out[k] - prior[k]) for k in prior) <= 0.10 + 1e-12
    assert out["a"] > prior["a"] and out["b"] < prior["b"]
    same = cap_shift(prior, prior, 0.10)
    assert same == pytest.approx(prior)


def test_negative_persistence_is_valid_ar1(book, facts):
    """ρ_d < 0 (чередование знака отклонения) — законный AR(1): схема принимает, фильтр считает."""
    import copy

    from model.book_schema import validate_book

    B = copy.deepcopy(book)
    B["margin"]["deviation_persistence"] = -0.4
    B["joint"]["regime_update"]["rho"] = -0.4
    validate_book(B)
    ctx = Context(B, facts)
    RG = ctx.regime("floor")
    assert RG.deviation[0] == pytest.approx(-0.4 * RG.deviation_anchor, abs=1e-16)
    assert RG.deviation[1] == pytest.approx(0.16 * RG.deviation_anchor, abs=1e-16)
    first = B["meta"]["first_period"]
    post, _ = regime_updates(Context(add_observation(B, first, 0.058, 0.002), facts))
    assert math.fsum(post.values()) == pytest.approx(1.0, abs=1e-12)
