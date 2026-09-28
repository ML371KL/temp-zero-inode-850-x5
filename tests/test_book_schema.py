"""Закрытая схема книги: незнакомый ключ, пропуск, неверный тип — BookError."""

from __future__ import annotations

import copy
import datetime as dt

import pytest

from model.book import prev_period
from model.book_schema import BookError, kernel_sha256, validate_book


def _broken(book, fn):
    B = copy.deepcopy(book)
    fn(B)
    return B


def _refused(book, fn, *words):
    with pytest.raises(BookError) as exc:
        validate_book(_broken(book, fn))
    for word in words:
        assert word in str(exc.value), str(exc.value)
    return str(exc.value)


def test_book_passes_schema(book):
    validate_book(copy.deepcopy(book))


@pytest.mark.parametrize("path", ["", "margin", "valuation.uncertainty", "joint.regime_update",
                                  "worlds.M", "capex"])
def test_unknown_key_is_refused(book, path):
    def add(B):
        node = B
        for part in filter(None, path.split(".")):
            node = node[part]
        node["unknown_typo"] = 1.0
    _refused(book, add, "незнакомый ключ", "unknown_typo")


def test_unknown_key_in_axis_is_refused(book):
    _refused(book, lambda B: B["valuation"]["uncertainty"]["axes"][0].update(lowx=0.0),
             "незнакомый ключ", "lowx")


def test_missing_key_is_refused(book):
    _refused(book, lambda B: B["capex"].pop("asset_life_years"), "нет обязательного ключа",
             "asset_life_years")
    _refused(book, lambda B: B["joint"]["regime_prob"].pop("full"), "full")


@pytest.mark.parametrize("path,value,word", [
    ("valuation.beta_u", "0.6", "число"),
    ("valuation.erp", True, "число"),
    ("joint.neutral_world", "X", "одно из"),
    ("capex.asset_life_years", 10.3, "кратный половине года"),
    ("joint.regime_update.sigma_pp", 0.0, "положительное"),
    ("valuation.governance_discount", 1.0, "[0; 1)"),
])
def test_wrong_type_is_refused(book, path, value, word):
    def put(B):
        *head, last = path.split(".")
        node = B
        for part in head:
            node = node[part]
        node[last] = value
    _refused(book, put, word)


def test_unquoted_date_is_refused(book):
    day = dt.date.fromisoformat(book["meta"]["valuation_date"])
    _refused(book, lambda B: B["meta"].update(valuation_date=day), "без кавычек")


def test_trajectory_key_must_be_string(book):
    _refused(book, lambda B: B["capex"]["maintenance"]["base"].update({2028: 0.02}),
             "не строка")


def test_half_year_key_on_last_period_is_refused(book):
    last = book["meta"]["last_period"]
    _refused(book, lambda B: B["capex"]["maintenance"]["low"].update({last: 0.02}),
             last, "как LT")
    _refused(book, lambda B: B["margin"]["targets"]["full"].update({last: 0.07}), last)


def test_world_trajectory_must_cover_the_grid(book):
    first = book["meta"]["first_period"]
    _refused(book, lambda B: B["worlds"]["N"]["cpi"].pop(first), "нет", first)


def test_kernel_sha256_is_checked(book):
    assert kernel_sha256(book["worlds"]) == book["worlds"]["source"]["kernel_sha256"]
    msg = _refused(book, lambda B: B["worlds"]["H"]["zero_curve"].update({"LT": 0.12}),
                   "kernel_sha256")
    assert "не совпадает" in msg


def test_probabilities_sum_to_one(book):
    _refused(book, lambda B: B["joint"]["world_prob"].update(N=0.40), "joint.world_prob", "сумме")


def test_rho_must_equal_deviation_persistence(book):
    _refused(book, lambda B: B["joint"]["regime_update"].update(
        rho=book["margin"]["deviation_persistence"] / 2), "rho")


def test_axis_path_must_exist(book):
    _refused(book, lambda B: B["valuation"]["uncertainty"]["axes"][0]["paths"].append(
        "margin.targets.stress.LTX"), "нет в книге")
    _refused(book, lambda B: B["valuation"]["reverse_dcf"]["axes"][0].update(kind="dict"),
             "kind")


def test_observation_outside_forecast_is_refused(book):
    anchor = book["meta"]["anchor_period"]
    _refused(book, lambda B: B["joint"]["regime_update"]["observations"].append(
        {"period": anchor, "value": 0.06, "se": 0.0}), anchor)


def test_meta_periods_are_consistent(book):
    earlier = prev_period(book["meta"]["anchor_period"])
    _refused(book, lambda B: B["meta"].update(anchor_period=earlier), "anchor_period")
    day = dt.date.fromisoformat(book["meta"]["facts_date"]) - dt.timedelta(days=1)
    _refused(book, lambda B: B["meta"].update(facts_date=day.isoformat()), "facts_date")
