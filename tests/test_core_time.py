"""Дата оценки на сетке, перекат по форвардам и непрерывность точки (docs/MODEL.md §5)."""

from __future__ import annotations

import copy
import datetime as dt

import pytest

from model.book import next_period, period_end, period_start, periods
from model.core import Context, grid_position, make_timing, run_cell
from model.grid import evaluate


def _P(book):
    return periods(book["meta"]["first_period"], book["meta"]["last_period"])


def test_grid_position_at_boundaries(book):
    P = _P(book)
    first_day = period_start(P[0])
    assert grid_position(P, first_day) == (0, 0.0)
    length = (period_start(P[1]) - first_day).days
    assert grid_position(P, period_end(P[0])) == (0, (length - 1) / length)
    assert grid_position(P, period_start(P[1])) == (1, 0.0)
    assert grid_position(P, first_day - dt.timedelta(days=40)) == (0, 0.0)
    assert grid_position(P, period_end(P[-1]) + dt.timedelta(days=1)) == (len(P) - 1, 1.0)


def test_times_of_flows(book):
    P = _P(book)
    v = period_start(P[1]) + dt.timedelta(days=30)
    T = make_timing(P, v, v)
    assert T.closed == 1 and T.fraction[0] == 0.0 and T.fraction[1] == 1 - T.elapsed
    assert T.t_mid[1] == pytest.approx((1 - T.elapsed) * 0.25)
    assert T.t_mid[2] == pytest.approx((1 - T.elapsed) * 0.5 + 0.25)
    assert T.t_end == pytest.approx((1 - T.elapsed) * 0.5 + (len(P) - 2) * 0.5)


def test_valuation_on_curve_date_has_no_roll(book, facts):
    curve = book["meta"]["curve_as_of"]
    ctx = Context(book, facts, valuation_date=curve)
    assert ctx.timing.roll == 0.0
    later = Context(book, facts, valuation_date=dt.date.fromisoformat(curve) + dt.timedelta(days=9))
    assert later.timing.roll > 0.0
    earlier = Context(book, facts, valuation_date=dt.date.fromisoformat(curve) - dt.timedelta(days=9))
    assert earlier.timing.roll == 0.0


def _flat(book, curve_as_of: dt.date):
    B = copy.deepcopy(book)
    B["meta"]["curve_as_of"] = curve_as_of.isoformat()
    for w in ("N", "H", "M"):
        z = B["worlds"][w]["zero_curve"]["LT"]
        B["worlds"][w]["zero_curve"] = {k: z for k in B["worlds"][w]["zero_curve"]}
    return B


def test_flat_curve_roll_changes_nothing(book, facts):
    v = dt.date.fromisoformat(book["meta"]["valuation_date"])
    same = evaluate(_flat(book, v), facts, valuation_date=v)
    rolled = evaluate(_flat(book, v - dt.timedelta(days=60)), facts, valuation_date=v)
    assert rolled.ctx.timing.roll > 0 and same.ctx.timing.roll == 0
    for a, b in zip(same.cells, rolled.cells):
        assert b.result.ev == pytest.approx(a.result.ev, rel=1e-12)
    assert rolled.point.central == pytest.approx(same.point.central, rel=1e-12)


def test_roll_matters_on_sloped_curve(book, facts):
    v = dt.date.fromisoformat(book["meta"]["valuation_date"]) + dt.timedelta(days=60)
    B = copy.deepcopy(book)
    B["meta"]["curve_as_of"] = v.isoformat()
    no_roll = evaluate(B, facts, valuation_date=v)
    B["meta"]["curve_as_of"] = (v - dt.timedelta(days=90)).isoformat()
    roll = evaluate(B, facts, valuation_date=v)
    assert roll.point.central != no_roll.point.central


def _point(book, facts, day):
    return evaluate(book, facts, valuation_date=day).point.central


@pytest.mark.parametrize("index", [0, 1, 2])
def test_point_is_continuous_across_half_year_boundary(book, facts, index):
    """Скачок на границе полугодий — того же порядка, что соседние дневные шаги."""
    P = _P(book)
    last = period_end(P[index])
    days = [last - dt.timedelta(days=1), last, last + dt.timedelta(days=1),
            last + dt.timedelta(days=2)]
    p = [_point(book, facts, d) for d in days]
    step_before, jump, step_after = p[1] - p[0], p[2] - p[1], p[3] - p[2]
    scale = max(abs(step_before), abs(step_after))
    assert abs(jump) <= 3 * scale + 1e-6, (days, p)


def test_anchor_end_equals_first_day(book, facts):
    first = _P(book)[0]
    a = _point(book, facts, period_start(first) - dt.timedelta(days=1))
    b = _point(book, facts, period_start(first))
    assert a == pytest.approx(b, rel=1e-12)


def test_frozen_book_value_grows_with_time(book, facts):
    """При замороженной книге точка через год выше (ожидаемая доходность капитала)."""
    v = dt.date.fromisoformat(book["meta"]["valuation_date"])
    now, later = _point(book, facts, v), _point(book, facts, v + dt.timedelta(days=364))
    assert later > now


def test_discount_by_hand(book, facts):
    """df_v(t) = df(Δ + t) / df(Δ), df(t) = (1 + z(t) + β·ERP)^(−t); EV — сумма PV."""
    from model.book import interp_curve

    ctx = Context(book, facts)
    T = ctx.timing
    assert T.roll > 0
    prem = book["valuation"]["beta_u"] * book["valuation"]["erp"]
    curve = book["worlds"]["N"]["zero_curve"]

    def df(t):
        return (1 + interp_curve(curve, t) + prem) ** (-t)

    res = run_cell(ctx, ctx.cell("N", "partial", "low"))
    pv = 0.0
    for r in res.rows:
        if r.fraction:
            want = df(T.roll + r.t) / df(T.roll)
            assert r.df == pytest.approx(want, rel=1e-13)
            pv += (r.fcff + r.shield) * r.fraction * want
    tv = res.terminal.tv_flow + res.terminal.tv_shield
    pv += tv * df(T.roll + T.t_end) / df(T.roll)
    assert res.ev == pytest.approx(pv, rel=1e-12)
