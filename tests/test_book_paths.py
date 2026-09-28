"""Траектории §0.1, кривая §0.2, полугодовая ставка, подмены книги."""

from __future__ import annotations

import copy
import math

import pytest

from model.book import (BookError, add_observation, blend_weights, book_warnings, get_node,
                        half_rate, interp_curve, override, path_value, period_end, period_start,
                        periods, prev_same_half)

SPEC = {"2026H2": 0.4, "2027": 0.3, "2029": 0.1, "LT": 0.0, "LT_from": 2033}


def test_exact_period_then_year():
    assert path_value(SPEC, "2026H2") == 0.4
    assert path_value(SPEC, "2027H1") == path_value(SPEC, "2027H2") == 0.3


def test_interpolation_between_years():
    assert path_value(SPEC, "2028H1") == pytest.approx(0.2, abs=1e-15)
    assert path_value(SPEC, "2028H2") == path_value(SPEC, "2028H1")


def test_convergence_to_lt_by_lt_from():
    # 2029: 0,1 → LT 0 к 2033: по 0,025 в год
    assert path_value(SPEC, "2031H1") == pytest.approx(0.05, abs=1e-15)
    assert path_value(SPEC, "2033H1") == 0.0
    assert path_value(SPEC, "2040H2") == 0.0


def test_without_lt_last_value_holds():
    spec = {"2027": 0.2, "2028": 0.1}
    assert path_value(spec, "2035H2") == 0.1
    assert path_value({"LT": 0.02}, "2031H1") == 0.02
    assert path_value({"2026H2": -0.006}, "2030H1") == -0.006   # §0.1 буквально


def test_lt_without_lt_from_starts_next_year():
    spec = {"2027": 0.2, "LT": 0.0}
    assert path_value(spec, "2028H1") == 0.0


def test_before_first_year_takes_first_year():
    assert path_value({"2028": 0.3, "LT": 0.1}, "2026H2") == 0.3


def test_curve_nodes_and_convergence():
    curve = {"1": 0.10, "3": 0.12, "5": 0.13, "10": 0.14, "LT": 0.09}
    assert interp_curve(curve, 0.25) == 0.10
    assert interp_curve(curve, 2.0) == pytest.approx(0.11, abs=1e-15)
    assert interp_curve(curve, 10.0) == 0.14
    assert interp_curve(curve, 12.5) == pytest.approx(0.115, abs=1e-15)
    assert interp_curve(curve, 15.0) == 0.09
    assert interp_curve(curve, 30.0) == 0.09


def test_half_rate_is_root_not_half():
    assert half_rate(0.21) == pytest.approx(0.1, abs=1e-15)
    assert (1 + half_rate(0.16)) ** 2 == pytest.approx(1.16, abs=1e-15)


def test_period_ruler(book):
    P = periods(book["meta"]["first_period"], book["meta"]["last_period"])
    assert len(P) == len(set(P))
    for a, b in zip(P, P[1:]):
        assert (period_start(b) - period_end(a)).days == 1
    assert prev_same_half(P[2]) == P[0]


def test_override_value_shift_dict(book):
    B = override(book, ["valuation.beta_u"], "value", 0.8)
    assert B["valuation"]["beta_u"] == 0.8
    assert book["valuation"]["beta_u"] != 0.8 or B is not book
    C = override(book, ["capex.maintenance.base"], "shift", 0.01)
    base = book["capex"]["maintenance"]["base"]
    for k, v in C["capex"]["maintenance"]["base"].items():
        assert v == (v if k == "LT_from" else pytest.approx(base[k] + 0.01, abs=1e-15))
    assert C["capex"]["maintenance"]["base"]["LT_from"] == base["LT_from"]
    D = override(book, ["margin.targets.stress.LT", "margin.targets.full.LT"], "shift", -0.002)
    assert D["margin"]["targets"]["stress"]["LT"] == pytest.approx(
        book["margin"]["targets"]["stress"]["LT"] - 0.002, abs=1e-15)
    assert D["margin"]["targets"]["floor"] is book["margin"]["targets"]["floor"]
    E = override(book, ["joint.world_prob"], "dict", {"N": 2.0, "H": 1.0, "M": 1.0})
    assert E["joint"]["world_prob"] == {"N": 0.5, "H": 0.25, "M": 0.25}


def test_override_does_not_touch_source(book):
    before = copy.deepcopy(book)
    override(book, ["worlds.M.cpi", "worlds.M.lt.inflation"], "shift", 0.01)
    override(book, ["joint.world_prob"], "dict", {"N": 1.0, "H": 0.0, "M": 0.0})
    assert book == before


def test_override_refuses_unknown_path_and_kind(book):
    with pytest.raises(BookError):
        override(book, ["valuation.beta"], "value", 0.7)
    with pytest.raises(BookError):
        override(book, ["capex.maintenance"], "shift", 0.01)
    with pytest.raises(BookError):
        override(book, ["valuation.beta_u"], "scale", 2.0)


def test_book_axes_resolve(book):
    for block in ("uncertainty", "reverse_dcf"):
        for axis in book["valuation"][block]["axes"]:
            for path in axis["paths"]:
                get_node(book, path)


def test_blend_weights():
    w = blend_weights({"N": 0.2, "H": 0.8}, {"N": 0.6, "H": 0.4}, 0.5)
    assert w == pytest.approx({"N": 0.4, "H": 0.6})


def test_add_observation_extends_not_replaces(book):
    first = book["meta"]["first_period"]
    B = add_observation(book, first, 0.06)
    obs = B["joint"]["regime_update"]["observations"]
    assert obs[-1] == {"period": first, "value": 0.06, "se": 0.0}
    assert len(obs) == len(book["joint"]["regime_update"]["observations"]) + 1
    with pytest.raises(BookError):
        add_observation(B, first, 0.05)


def test_book_warnings_name_literal_reading(book):
    first = book["meta"]["first_period"]
    B = copy.deepcopy(book)
    B["revenue"]["vat_effect"] = {first: -0.006}
    assert any(w.startswith("revenue.vat_effect") for w in book_warnings(B))
    B["revenue"]["vat_effect"] = {first: -0.006, str(int(first[:4]) + 1): 0.0, "LT": 0.0}
    assert not any(w.startswith("revenue.vat_effect") for w in book_warnings(B))
    B["capex"]["maintenance"]["low"] = {"2027": 0.02, "LT": 0.03, "LT_from": 2090}
    assert any(w.startswith("capex.maintenance.low") for w in book_warnings(B))


def test_open_period(book):
    from model.book import open_period

    first = book["meta"]["first_period"]
    assert open_period(book) == first
    B = add_observation(book, first, 0.06)
    assert open_period(B) == periods(first, book["meta"]["last_period"])[1]
    C = add_observation(book, first, 0.06, 0.004)
    assert open_period(C) == first
