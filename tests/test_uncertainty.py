"""Полоса §9, обратный DCF §11, «что даст отчёт» §12 (model/uncertainty.py).

* латинский гиперкуб — ровно алгоритм docs/MODEL.md §9 (порядок случайных чисел);
* треугольное распределение: медиана каждого суждения = значению книги;
* прогон при s = 0 — книга бит в бит; концы оси — low и high книги;
* детерминизм: числа не зависят от числа процессов и от повтора;
* обратный DCF и нейтральная маржа попадают в свою цель на своей подвыборке.
"""

from __future__ import annotations

import math
import random
import statistics

import pytest

from model import uncertainty as U
from model.book import get_node, open_period, trajectory
from model.facts import core_facts
from model.grid import evaluate, layer_prices


@pytest.fixture(scope="module")
def cf(book, facts):
    return core_facts(facts, book)


@pytest.fixture(scope="module")
def small(book, cf, grid):
    """Малая полоса и всё на её гиперкубе: 24 прогона, подвыборка 12."""
    return U.distribution(book, cf, grid, period=open_period(book), draws=24, subsample=12,
                          n_workers=1)


# ------------------------------------------------------------ выборка


def _lhs_by_the_text(n: int, k: int, seed: int) -> list[list[float]]:
    """Алгоритм §9 словами документа: для каждой оси j по порядку книги —
    perm = list(range(n)); rng.shuffle(perm); для i = 0..n−1 —
    u[i][j] = (perm[i] + rng.random()) / n."""
    rng = random.Random(seed)
    u = [[None] * k for _ in range(n)]
    for j in range(k):
        perm = list(range(n))
        rng.shuffle(perm)
        for i in range(n):
            u[i][j] = (perm[i] + rng.random()) / n
    return u


def test_lhs_is_exactly_the_documented_algorithm(book):
    seed = book["valuation"]["uncertainty"]["seed"]
    k = len(book["valuation"]["uncertainty"]["axes"])
    assert U.lhs(97, k, seed) == _lhs_by_the_text(97, k, seed)


def test_lhs_has_one_point_per_stratum():
    n, k = 50, 4
    u = U.lhs(n, k, 12345)
    for j in range(k):
        strata = sorted(math.floor(row[j] * n) for row in u)
        assert strata == list(range(n))


def test_triangular_positions():
    assert U.tri_s(0.5) == 0.0
    assert U.tri_s(0.0) == -1.0 and U.tri_s(1.0) == 1.0
    for u in (0.01, 0.2, 0.37, 0.49):
        assert U.tri_s(u) == pytest.approx(-U.tri_s(1 - u), abs=1e-15)
    # CDF треугольного распределения: P(s ≤ x) = (1 + x)²/2 при x ≤ 0.
    for x in (-0.8, -0.5, -0.1):
        assert U.tri_s((1 + x) ** 2 / 2) == pytest.approx(x, abs=1e-12)


def test_judgement_median_is_the_book_value(book):
    """Медиана каждого суждения по прогонам — значение книги (§9)."""
    U_ = book["valuation"]["uncertainty"]
    n = U_["draws"]
    axes = U.band_axes(book)
    s = U.draw_positions(n, len(axes), U_["seed"])
    for j, ax in enumerate(axes):
        med = statistics.median(row[j] for row in s)
        assert abs(med) <= 1.0 / n, ax.name
        if ax.kind == "value":
            values = [float(get_node(U.trial_book(book, (ax,), (row[j],)), ax.paths[0]))
                      for row in s]
            width = abs(float(ax.high) - float(ax.low))
            assert abs(statistics.median(values) - float(get_node(book, ax.paths[0]))) <= width / n


def test_zero_positions_reproduce_the_book(book, cf, grid):
    axes = U.band_axes(book)
    B = U.trial_book(book, axes, [0.0] * len(axes))
    low, high = layer_prices(B, cf)
    assert (low, high) == (grid.layers["macro_neutral"].price, grid.layers["analytical"].price)


def test_axis_ends_are_book_low_and_high(book):
    for ax in U.band_axes(book):
        for end, s in (("low", -1.0), ("high", 1.0)):
            B = U.at_end(book, ax, end)
            target = getattr(ax, end)
            path = ax.paths[0]
            if ax.kind == "value":
                assert float(get_node(B, path)) == pytest.approx(float(target), abs=1e-15)
            elif ax.kind == "shift":
                node, base = get_node(B, path), get_node(book, path)
                if isinstance(base, dict):
                    P = [book["meta"]["first_period"], book["meta"]["last_period"]]
                    for a, b in zip(trajectory(node, P), trajectory(base, P)):
                        assert a - b == pytest.approx(float(target), abs=1e-15)
                else:
                    assert float(node) - float(base) == pytest.approx(float(target), abs=1e-15)
            else:
                total = math.fsum(target.values())
                got = get_node(B, path)
                for w in target:
                    assert got[w] == pytest.approx(target[w] / total, abs=1e-15)


# ----------------------------------------------------------- детерминизм


def test_band_is_repeatable(book, cf):
    a = U.band(book, cf, draws=16, n_workers=1)
    b = U.band(book, cf, draws=16, n_workers=1)
    assert (a.low, a.high) == (b.low, b.high)


def test_band_does_not_depend_on_the_number_of_processes(book, cf):
    """Бит в бит одно и то же последовательно и пулом из трёх процессов."""
    one = U.band(book, cf, draws=30, n_workers=1)
    try:
        many = U.band(book, cf, draws=30, n_workers=3, min_parallel=1)
        assert U._POOL["workers"] == 3 and not U._POOL["failed"], "пул процессов не работал"
    finally:
        U.close_pool()
    assert one.low == many.low and one.high == many.high


# ------------------------------------------------------------ статистика


def test_quantile7_matches_statistics():
    rng = random.Random(7)
    xs = sorted(rng.gauss(0, 1) for _ in range(101))
    ref = statistics.quantiles(xs, n=20, method="inclusive")
    for i, q in enumerate(ref, start=1):
        assert U.quantile7(xs, i / 20) == pytest.approx(q, abs=1e-12)


def test_band_stats_by_hand():
    low, high = [1.0, 2.0, 3.0, 4.0], [3.0, 4.0, 5.0, 6.0]
    s = U.band_stats(low, high, 0.5, 4.5)
    assert s["median"] == 3.5 and s["mean"] == 3.5 and s["p_below"] == 0.75
    assert s["p10"] == pytest.approx(2.3) and s["p90"] == pytest.approx(4.7)


def test_contributions_find_the_axis_that_matters():
    axes = [U.Axis(0, "a", "value", ("x",), 0, 1), U.Axis(1, "b", "value", ("y",), 0, 1)]
    pos = U.draw_positions(200, 2, 3)
    center = [10 * s[0] + 0.01 * math.sin(i) for i, s in enumerate(pos)]
    c = U.contributions(axes, pos, center)
    assert c[0]["share"] > 0.99 and math.fsum(x["share"] for x in c) == pytest.approx(1.0)


def test_bisect_rules():
    root, calls = U.bisect(lambda x: 100 * (x - 0.3), 0.0, 1.0, tol=1.0)
    assert abs(root - 0.3) <= 0.01 and calls <= 2 + U.BISECTION_STEPS
    assert U.bisect(lambda x: x + 5.0, 0.0, 1.0, tol=0.1)[0] is None


# ------------------------------------------------- §11, §12 на малой полосе


def test_subsample_on_the_book_is_the_full_median(book, cf, small):
    b = small.band
    sub = U.Subsample(b, 1234.5, cf, size=12, valuation_date=None, market_price=b.market_price,
                      n_workers=1)
    assert sub.at(book) == pytest.approx(1234.5, abs=1e-9)


def test_reverse_dcf_hits_the_market_price(book, cf, small):
    b = small.band
    sub = U.Subsample(b, small.stats["median"], cf, size=small.subsample,
                      valuation_date=None, market_price=b.market_price, n_workers=1)
    mp = b.market_price
    for row in small.reverse_dcf:
        assert row["status"] in ("solved", "unreachable")
        if row["solved"] is None:
            continue
        B = U.override(book, row["paths"], row["kind"], row["solved"])
        fixed = frozenset(row["fixed_axes"])
        assert abs(sub.at(B, fixed) - mp) <= U.REVERSE_TOL_RUB, row["name"]
        lo, hi = row["range"]
        assert row["in_range"] == (lo <= row["solved"] <= hi)


def test_reverse_axis_replaces_the_same_band_axis(book, small):
    by_paths = {tuple(sorted(a.paths)): a.index for a in small.band.axes}
    for row in small.reverse_dcf:
        want = by_paths.get(tuple(sorted(row["paths"])))
        assert row["fixed_axes"] == ([] if want is None else [want])


def test_next_report_table_and_neutral_margin(book, cf, grid, small):
    N = small.next_report
    assert N["period"] == open_period(book)
    demo = book["valuation"]["next_report"]["demo_values"]
    assert [r["margin"] for r in N["table"]] == [float(x) for x in demo]
    for r in N["table"]:
        assert math.fsum(r["posterior"].values()) == pytest.approx(1.0, abs=1e-12)
        g = evaluate(U.with_fact(book, N["period"], r["margin"]), cf)
        assert r["point"] == g.point.central
        assert r["d_point"] == pytest.approx(r["point"] - grid.point.central, abs=1e-9)
    # Медиана растёт с фактом маржи (сетка монотонна по марже).
    meds = [r["median"] for r in N["table"]]
    assert meds == sorted(meds)
    m = N["neutral"]["point"]
    if m is not None:
        g = evaluate(U.with_fact(book, N["period"], m), cf)
        assert abs(g.point.central - grid.point.central) <= U.NEUTRAL_TOL_RUB


def test_with_fact_replaces_a_partial_observation(book):
    p = open_period(book)
    B = U.with_fact(U.add_observation(book, p, 0.06, 0.004), p, 0.061)
    obs = B["joint"]["regime_update"]["observations"]
    assert [(o["period"], o["value"], o["se"]) for o in obs if o["period"] == p] == [(p, 0.061, 0.0)]
    assert book["joint"]["regime_update"]["observations"] == []


def test_expectation_is_the_probability_weighted_cell_mean(grid, book):
    p = open_period(book)
    e = U.expectation(grid, p)
    i = grid.ctx.P.index(p)
    ref = math.fsum(c.p["analytical"] * c.result.rows[i].margin for c in grid.cells)
    assert e["margin"] == pytest.approx(ref, abs=1e-15)
    by = {r["regime"]: r["margin"] for r in e["by_regime"]}
    post = grid.regime_posterior
    assert math.fsum(post[r] * by[r] for r in by) == pytest.approx(e["margin"], abs=1e-12)
