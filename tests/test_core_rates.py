"""Конвенции ставок (docs/MODEL.md §0, §4.9, §6): начисление за полугодие по ногам долга.

Простые ставки (купон «старого» фикса, ключевая + спред, издержки размещения, доход
кассы, ключевая кэрри) начисляются r/2; эффективная доходность нового фикса (кривая +
G-спред) входит в ставку купоном облигации по номиналу с m = `fixed_coupon_freq`
выплатами в год; корень `half_rate` — только у эффективных величин.
"""

from __future__ import annotations

import copy
import math

import pytest

from model.book import coupon_rate, half_rate
from model.core import Context
from model.facts import core_facts

YIELDS = (0.0, 0.05, 0.1325, 0.25)


@pytest.mark.parametrize("y", YIELDS)
def test_coupon_rate_is_par_coupon_of_effective_yield(y):
    """cpn(y) = m((1 + y)^(1/m) − 1): m купонов cpn/m в год дают эффективную доходность y."""
    for m in (1, 2, 4, 12):
        c = coupon_rate(y, m)
        assert (1 + c / m) ** m == pytest.approx(1 + y, rel=1e-14, abs=1e-15)
    assert coupon_rate(y, 1) == pytest.approx(y, rel=1e-15, abs=1e-15)
    # полугодовой купон начисляет ровно корень — прежнюю запись §0
    assert coupon_rate(y, 2) / 2 == pytest.approx(half_rate(y), rel=1e-14, abs=1e-15)
    if y > 0:
        # чаще купон — ниже номинальная ставка: ежемесячный купон начисляет меньше корня,
        # корень — меньше простой половины
        assert coupon_rate(y, 12) < coupon_rate(y, 4) < coupon_rate(y, 2) < coupon_rate(y, 1)
        assert coupon_rate(y, 12) / 2 < half_rate(y) < y / 2
        assert coupon_rate(y, 12) > math.log1p(y)


def test_half_rates_are_halves_of_simple_rates(grid, book):
    """Все полугодовые ставки клетки — половина простых годовых (§0, §4.9)."""
    ctx = grid.ctx
    k = float(book["financing"]["cash_yield_k"])
    for w in ("N", "H", "M"):
        for credit in ("base", "stress"):
            rp = ctx.rates(w, credit)
            key = ctx.world(w).key
            for i, r in enumerate(rp.debt):
                j = min(i, ctx.N - 1)
                assert rp.half_debt[i] == r / 2
                assert rp.half_key[i] == key[j] / 2
                assert rp.half_yield[i] == k * key[j] / 2
                assert rp.half_clean[i] <= rp.half_debt[i]
            if credit == "base":
                assert rp.half_fair == rp.half_debt


def _ctx(book, facts, **financing):
    """Контекст на копии книги с заменой ключей financing (траектория {"LT": x} — x везде)."""
    A = copy.deepcopy(book)
    A["financing"].update(financing)
    return Context(A, core_facts(facts, A))


def test_legacy_leg_accrues_half_of_its_coupon(book, facts):
    """Весь долг — «старый» фикс: начисление за полугодие — legacy_rate / 2, не корень."""
    ctx = _ctx(book, facts, fixed_share={"LT": 1.0}, legacy_weight={"LT": 1.0})
    legacy = float(book["financing"]["legacy_rate"])
    for w in ("N", "M"):
        rp = ctx.rates(w, "base")
        assert all(h == pytest.approx(legacy / 2, rel=1e-15) for h in rp.half_debt)
        assert legacy / 2 > half_rate(legacy)


def test_floating_leg_accrues_half_of_key_plus_spread(book, facts):
    """Весь долг плавающий: (ключевая + спред + ic) / 2 по полугодиям, в терминале — последняя КС."""
    FN = book["financing"]
    ctx = _ctx(book, facts, fixed_share={"LT": 0.0})
    for w, credit in (("N", "base"), ("M", "stress")):
        rp, key = ctx.rates(w, credit), ctx.world(w).key
        for i, h in enumerate(rp.half_debt):
            j = min(i, ctx.N - 1)
            want = (key[j] + float(FN["spread_float"][credit]) + float(FN["issuance_cost"])) / 2
            assert h == pytest.approx(want, rel=1e-15)


@pytest.mark.parametrize("freq", [12, 4, 2, 1])
def test_new_fixed_leg_accrues_half_of_par_coupon(book, facts, freq):
    """Весь долг — новый фикс без издержек: cpn(z_fix + s_fix) / 2; при m = 2 — корень."""
    FN = book["financing"]
    ctx = _ctx(book, facts, fixed_share={"LT": 1.0}, legacy_weight={"LT": 0.0}, issuance_cost=0.0,
               fixed_coupon_freq=freq)
    for w, credit in (("H", "base"), ("M", "stress")):
        W, rp = ctx.world(w), ctx.rates(w, credit)
        s = float(FN["spread_fixed"][credit])
        ys = [W.z_fix[i] + s for i in range(ctx.N)] + [W.z_lt + s]
        for h, y in zip(rp.half_debt, ys):
            assert h == pytest.approx(freq * ((1 + y) ** (1 / freq) - 1) / 2, rel=1e-14)
            if freq == 2:
                assert h == pytest.approx(half_rate(y), rel=1e-14)
            if freq == 1:
                assert h == pytest.approx(y / 2, rel=1e-14)


def test_coupon_frequency_touches_only_the_new_fixed_leg(book, facts):
    """Частота купона меняет только ногу нового фикса: без неё (весь долг — старый фикс или
    плавающий) ставки от m не зависят; с ней — реже купон, выше начисление."""
    for legs in ({"fixed_share": {"LT": 1.0}, "legacy_weight": {"LT": 1.0}}, {"fixed_share": {"LT": 0.0}}):
        a = _ctx(book, facts, fixed_coupon_freq=12, **legs).rates("N", "base")
        b = _ctx(book, facts, fixed_coupon_freq=2, **legs).rates("N", "base")
        assert a == b
    monthly = _ctx(book, facts, fixed_coupon_freq=12).rates("N", "base")
    semi = _ctx(book, facts, fixed_coupon_freq=2).rates("N", "base")
    assert all(m < s for m, s in zip(monthly.half_debt, semi.half_debt))
