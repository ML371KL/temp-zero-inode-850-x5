"""Налог и щит терминала (docs/MODEL.md §6, «Налог терминала TV_tax», «Щит терминала»).

* хвост налога `positive_part_pv` — прямой сумме max(0, a·(1+x)^n − b·(1+y)^n)·(1+r)^(−n) при
  любых знаках a, b, при x > y, x < y, x = y и x, y почти равных; непрерывен, когда точка смены
  знака n_c проходит через целое число лет;
* TV_tax и щит TV_S ядра — прямой сумме τ·max(0, base) по полугодиям 800 лет (D&A — правило
  когорт §4.5, продолженное capex терминала) до 1e-6 млрд: на книге и на книгах, где база
  терминала меняет знак (в первом году, в хвосте после n_c, в явных 2L полугодиях);
* цена клетки непрерывна на порогах base_h = 0 первого года терминала во всех 36 клетках
  (прежняя линейная запись с индикатором [base_h > 0] рвала её на 6–38 ₽);
* при g ≥ π и положительных базах — прежняя линейная запись (налог Гордонами g- и π-частей,
  переходный член D&A, щит Gordon_g(S_T)) до машинной точности; при g < π точный налог не
  выше линейного — прежняя запись завышала стоимость.
"""

from __future__ import annotations

import math
import random

import pytest

from model.book import override
from model.core import Context, positive_part_pv, run_cell
from model.grid import evaluate

YEARS = 800                 # прямой суммы хватает: (1 + g)/(1 + r) ≤ 0,95 в клетках ниже


def _direct_tail(a, b, x, y, r, n0, years=4000):
    return math.fsum(max(0.0, a * (1 + x) ** n - b * (1 + y) ** n) * (1 + r) ** -n
                     for n in range(n0, years))


@pytest.mark.parametrize("a, b", [(50.0, 80.0), (80.0, 50.0), (-50.0, -80.0), (-80.0, -50.0),
                                  (50.0, -30.0), (-50.0, 30.0), (0.0, 40.0), (0.0, -40.0),
                                  (40.0, 0.0), (-40.0, 0.0), (0.0, 0.0), (60.0, 60.0)])
@pytest.mark.parametrize("x, y", [(0.06, 0.04), (0.04, 0.06), (0.05, 0.05), (0.05, 0.05 + 1e-13),
                                  (-0.01, 0.03)])
@pytest.mark.parametrize("n0", [0, 7])
def test_tail_is_the_direct_sum(a, b, x, y, n0):
    r = 0.12
    got = positive_part_pv(a, b, x, y, r, n0)
    want = _direct_tail(a, b, x, y, r, n0)
    assert got == pytest.approx(want, rel=1e-11, abs=1e-11)
    assert got >= 0.0


def test_tail_random_against_direct_sum():
    rnd = random.Random(20260930)
    for _ in range(300):
        a, b = rnd.uniform(-100, 100), rnd.uniform(-100, 100)
        x, y = rnd.uniform(-0.05, 0.12), rnd.uniform(-0.05, 0.12)
        r = max(x, y) + rnd.uniform(0.02, 0.2)
        n0 = rnd.randrange(0, 9)
        want = _direct_tail(a, b, x, y, r, n0, years=int(60 / (r - max(x, y))) + 200)
        assert positive_part_pv(a, b, x, y, r, n0) == pytest.approx(want, rel=1e-10, abs=1e-10)


@pytest.mark.parametrize("x, y", [(0.07, 0.04), (0.03, 0.06)])
def test_tail_is_continuous_when_the_sign_change_crosses_a_whole_year(x, y):
    """a·ρ^n = b ровно в целом n = 12: слагаемое этого года — ноль с обеих сторон."""
    r, b, n = 0.14, 100.0, 12
    a_star = b * ((1 + y) / (1 + x)) ** n
    lo = positive_part_pv(a_star * (1 - 1e-12), b, x, y, r, 0)
    hi = positive_part_pv(a_star * (1 + 1e-12), b, x, y, r, 0)
    assert abs(hi - lo) < 1e-8
    assert positive_part_pv(a_star, b, x, y, r, 0) == pytest.approx(lo, abs=1e-8)


@pytest.mark.parametrize("x, y", [(0.1199, 0.04), (0.04, 0.1199)])
def test_tail_at_the_growth_guard(x, y):
    """Защита §6: g, π ≤ r − 0,0001 — q = (1 + x)/(1 + r) < 1, хвост конечен. База
    положительна всегда (a > b > 0, x > y) — ровно два Гордона; при x < y — отрезок до n_c."""
    r = 0.12
    a, b = 90.0, 60.0
    q = [(1 + z) / (1 + r) for z in (x, y)]
    got = positive_part_pv(a, b, x, y, r, 2)
    if x > y:
        assert got == pytest.approx(a * q[0] ** 2 / (1 - q[0]) - b * q[1] ** 2 / (1 - q[1]),
                                    rel=1e-12)
    else:
        n_c = math.log(b / a) / math.log((1 + x) / (1 + y))          # ≈ 5,5 года
        want = math.fsum(a * q[0] ** n - b * q[1] ** n for n in range(2, math.ceil(n_c)))
        assert got == pytest.approx(want, rel=1e-11)
    assert math.isfinite(got) and got > 0.0


def test_tail_close_growth_rates_are_continuous():
    """g → π: хвост непрерывно переходит к случаю g = π (знак a − b), n_c уходит в
    бесконечность; разница — не больше наклона по g (≈ 1,4·10⁴ на единицу g) × |g − π|."""
    r, pi = 0.12, 0.04
    for a, b in ((90.0, 60.0), (60.0, 90.0)):
        at = positive_part_pv(a, b, pi, pi, r, 7)
        for d in (1e-9, -1e-9, 1e-13, -1e-13):
            assert abs(positive_part_pv(a, b, pi + d, pi, r, 7) - at) <= 1e5 * abs(d) + 1e-9


# ------------------------------------------------------------------ терминал клетки


def _direct_terminal_tax(ctx, res, sub=(0.0, 0.0), years=YEARS):
    """PV τ·max(0, base) по полугодиям терминала на конец явного участка: база до D&A T_h
    (минус sub_h) растёт с g, D&A — правило когорт §4.5 (база якоря, capex явного участка и
    терминала c_g·(1 + g)^n + c_π·(1 + π)^n) на всём горизонте — без установившейся формулы."""
    A, cf = ctx.A, ctx.facts
    T = res.terminal
    tau, padd = float(A["tax"]["rate"]), float(A["tax"]["permanent_add_pct"])
    g, pi, r = T.growth, T.pi, T.rate
    H, N = ctx.half_life, ctx.N
    before = [h.adj_ebitda - h.lti + padd * h.revenue - s for h, s in zip(T.halves, sub)]
    cg = [h.capex - h.capex_pi for h in T.halves]
    cp = [h.capex_pi for h in T.halves]
    seq = [cf.capex_anchor] + [row.capex for row in res.rows]
    terms, signs = [], set()
    for j in range(1, 2 * years + 1):
        n, h = divmod(j - 1, 2)
        k = N + j
        anchor = cf.da_anchor * ctx.da_runoff[k - 1] if k - 1 < len(ctx.da_runoff) else 0.0
        base = before[h] * (1 + g) ** n - (anchor + math.fsum(seq[max(0, k - H):k]) / H)
        signs.add(base > 0)
        terms.append(tau * max(0.0, base) * (1 + r) ** -(n + 0.25 + 0.5 * h))
        seq.append(cg[h] * (1 + g) ** n + cp[h] * (1 + pi) ** n)
    return math.fsum(terms), signs


# книга (стресс: g < π, база уходит в минус после n_c); стресс с отрицательной базой (g < π:
# налога нет ни в одном полугодии); full LT 2 % (g > π: налог только после n_c); стресс вблизи
# порога base_h = 0. Ожидание — у каких клеток база меняет знак («mixed») или всегда ≤ 0 («none»).
BOOKS = {"book": ([], "mixed"),
         "stress_negative": ([("margin.targets.stress.LT", "value", 0.01)], "none"),
         "full_late": ([("margin.targets.full.LT", "value", 0.02)], "mixed"),
         "stress_edge": ([("margin.targets.stress.LT", "shift", -0.02)], "mixed")}


@pytest.mark.parametrize("name", list(BOOKS))
def test_terminal_tax_and_shield_are_the_direct_sum(book, facts, name):
    A = book
    changes, expect = BOOKS[name]
    for path, how, v in changes:
        A = override(A, [path], how, v)
    grid = evaluate(A, facts)
    ctx = grid.ctx
    seen = {"mixed": 0, "none": 0}
    for c in grid.cells:
        res = c.result
        T = res.terminal
        assert max(T.growth, T.pi) < T.rate - 0.04          # прямой суммы на 800 лет хватает
        tax, signs = _direct_terminal_tax(ctx, res)
        levered, _ = _direct_terminal_tax(ctx, res, sub=[h.interest for h in T.halves])
        assert T.tv_tax == pytest.approx(tax, abs=1e-6), c.key
        assert T.tv_shield == pytest.approx(tax - levered, abs=1e-6), c.key
        seen["mixed"] += signs == {True, False}
        seen["none"] += signs == {False}
        if signs == {False}:
            assert T.tv_tax == 0.0 and T.tv_shield == 0.0, c.key
    assert seen[expect], f"{name}: нет клеток «{expect}» — книга не проверяет свою ветку"


def test_price_is_continuous_at_every_first_year_threshold(book, facts):
    """Порог base_h = 0 первого года терминала — сдвиг цели маржи LT режима s* = −base_h/R_h
    (база линейна по цели с наклоном R_h); цена клетки при s* ± 1e-9 — в пределах 0,01 ₽."""
    grid = evaluate(book, facts)
    eps, checked = 1e-9, 0
    for c in grid.cells:
        cell = c.result.cell
        for h, half in enumerate(c.result.terminal.halves):
            s_star = -half.tax_base / half.revenue
            side = []
            for e in (-eps, eps):
                A = override(book, [f"margin.targets.{cell.regime}.LT"], "shift", s_star + e)
                ctx = Context(A, facts)
                side.append(run_cell(ctx, ctx.cell(cell.world, cell.regime, cell.capex)))
            lo, hi = side
            assert lo.terminal.halves[h].tax_base < 0.0 < hi.terminal.halves[h].tax_base, c.key
            assert abs(hi.price - lo.price) < 0.01, (c.key, h + 1, hi.price - lo.price)
            checked += 1
    assert checked == 2 * len(grid.cells)


def _linear_terminal(ctx, res):
    """Прежняя (до 30.09.2026) линейная запись §6: налог Гордонами g- и π-частей со щитом D&A
    π-части по индикатору [base_h > 0], переходный член D&A 2L полугодий, щит Gordon_g(S_T)."""
    A, cf = ctx.A, ctx.facts
    T = res.terminal
    tau = float(A["tax"]["rate"])
    g, pi, r = T.growth, T.pi, T.rate
    H, N = ctx.half_life, ctx.N

    def gordon(f1, f2, x):
        return (f1 * (1 + r) ** 0.75 + f2 * (1 + r) ** 0.25) / (r - x)

    hs = T.halves
    pos = [h.tax_base > 0 for h in hs]
    f_pi = [(tau * h.da_pi if p else 0.0) - h.capex_pi for h, p in zip(hs, pos)]
    dg = [h.da - h.da_pi for h in hs]
    cg = [h.capex - h.capex_pi for h in hs]
    seq = [cf.capex_anchor] + [row.capex for row in res.rows]
    tr = []
    for j in range(1, H + 1):
        n, h = divmod(j - 1, 2)
        k = N + j
        rule = cf.da_anchor * ctx.da_runoff[k - 1] + math.fsum(seq[max(0, k - H):k]) / H
        steady = dg[h] * (1 + g) ** n + hs[h].da_pi * (1 + pi) ** n
        if pos[h]:
            tr.append(tau * (rule - steady) * (1 + r) ** -(n + 0.25 + 0.5 * h))
        seq.append(cg[h] * (1 + g) ** n + hs[h].capex_pi * (1 + pi) ** n)
    tv = (gordon(hs[0].fcff - f_pi[0], hs[1].fcff - f_pi[1], g) + gordon(f_pi[0], f_pi[1], pi)
          + math.fsum(tr))
    return tv, gordon(hs[0].shield, hs[1].shield, g)


def test_linear_record_where_it_was_exact_and_below_it_elsewhere(book, facts):
    """g ≥ π и база (с процентами и без) положительна во всех полугодиях — прежняя запись до
    машинной точности (на книге 1.1 — 27 клеток из 36); g < π — точный налог и щит не выше
    линейной записи: её налог далёкого будущего отрицателен."""
    grid = evaluate(book, facts)
    ctx = grid.ctx
    same = lower = 0
    for c in grid.cells:
        res = c.result
        T = res.terminal
        tv, shield = _linear_terminal(ctx, res)
        _, signs = _direct_terminal_tax(ctx, res)
        _, signs_lev = _direct_terminal_tax(ctx, res, sub=[h.interest for h in T.halves])
        if T.growth >= T.pi and signs == signs_lev == {True}:
            assert T.tv_flow == pytest.approx(tv, rel=1e-12, abs=1e-9), c.key
            assert T.tv_shield == pytest.approx(shield, rel=1e-10, abs=1e-9), c.key
            same += 1
        elif T.growth < T.pi:
            assert T.tv_flow <= tv + 1e-9 and T.tv_shield <= shield + 1e-9, c.key
            lower += T.tv_flow + T.tv_shield < tv + shield - 1e-6
    assert same and lower
