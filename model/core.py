"""Ядро: один проход клетки сетки (docs/MODEL.md §4–§7.1).

Клетка = мир ставок × режим маржи × уровень capex. Проход: сеть и
эффективная площадь → выручка «год к году» → маржа (цель режима + сезонность +
затухающее отклонение с фильтром Калмана) → LTI, capex, D&A, оборотный
капитал, операционная касса, налог → FCFF → проценты, путь долга и дивиденды
модели → дисконт от даты оценки с перекатом по форвардам → терминал с
разделением Гордона → EV клетки → требования D на дату оценки → цена клетки.

Всё, что не зависит от клетки целиком, считается один раз на книгу в
`Context`: траектории миров и дисконт-факторы, пути отклонения маржи режимов,
сеть по тарифам роста, выручка по (мир, тариф, спрос). Числа — только из книги
и фактов, по именам ключей. Суммы — `math.fsum` (одинаково на Python 3.11 и 3.12).

Единицы: деньги — млрд ₽, площадь — тыс. м², ставки и доли — доли единицы,
цена — ₽ на акцию.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field, fields
from functools import cached_property

from model.book import (half_rate, interp_curve, next_period, path_value, period_end,
                        period_index, period_of, period_start, periods, prev_period,
                        trajectory)
from model.facts import CoreFacts, Facts, core_facts

fsum = math.fsum

# Защита терминала: вечный рост не выше ставки (docs/MODEL.md §6: g = r − 0,0001).
GROWTH_GUARD = 0.0001


# -------------------------------------------------------------------- клетка


@dataclass(frozen=True)
class Cell:
    """Клетка сетки и её привязки (docs/MODEL.md §3)."""

    world: str
    regime: str
    capex: str
    growth: str     # тариф роста сети
    credit: str     # кредитное состояние (спреды)
    demand: str     # состояние спроса (чек и трафик)

    @property
    def key(self) -> str:
        return f"{self.world}|{self.regime}|{self.capex}"


def make_cell(A: dict, world: str, regime: str, capex: str) -> Cell:
    """Клетка с привязками: рост сети и кредит — от мира (в стрессе рост —
    `joint.stress_growth`), спрос — от режима."""
    J = A["joint"]
    link = J["world_links"][world]
    growth = J["stress_growth"] if regime == "stress" else link["growth"]
    return Cell(world=world, regime=regime, capex=capex, growth=growth,
                credit=link["credit"], demand=J["regime_demand"][regime])


# ------------------------------------------------------- время и перекат (§5)


def grid_position(P: list[str], day: dt.date) -> tuple[int, float]:
    """(закрытых прогнозных полугодий; доля прошедших дней текущего) на дату `day`.

    День `day` прошедшим не считается: elapsed = (day − начало) / длина.
    Дата до начала прогноза — (0; 0); после конца — (N − 1; 1).
    """
    closed = sum(1 for p in P if period_end(p) < day)
    if closed >= len(P):
        return len(P) - 1, 1.0
    start = period_start(P[closed])
    length = (period_start(next_period(P[closed])) - start).days
    return closed, min(1.0, max(0.0, (day - start).days / length))


def ruler(first: str, day: dt.date) -> float:
    """Положение даты на линейке полугодий: номер полугодия от `first` + доля."""
    p = period_of(day)
    start = period_start(p)
    length = (period_start(next_period(p)) - start).days
    return period_index(p) - period_index(first) + (day - start).days / length


@dataclass(frozen=True)
class Timing:
    """Дата оценки на сетке и сроки потоков (docs/MODEL.md §5)."""

    valuation_date: dt.date
    curve_as_of: dt.date
    closed: int
    elapsed: float
    roll: float                 # Δ, лет; 0 — без переката
    fraction: tuple             # доля потока полугодия в EV
    t_mid: tuple                # срок потока, лет от даты оценки
    t_end: float                # срок терминала


def make_timing(P: list[str], valuation_date: dt.date, curve_as_of: dt.date) -> Timing:
    closed, elapsed = grid_position(P, valuation_date)
    shift = 0.5 * (ruler(P[0], valuation_date) - ruler(P[0], curve_as_of))
    rest = 1.0 - elapsed
    fraction, t_mid = [], []
    for i in range(len(P)):
        if i < closed:
            fraction.append(0.0)
            t_mid.append(0.0)
        elif i == closed:
            fraction.append(rest)
            t_mid.append(rest * 0.25)
        else:
            fraction.append(1.0)
            t_mid.append(rest * 0.5 + (i - closed - 1) * 0.5 + 0.25)
    return Timing(valuation_date=valuation_date, curve_as_of=curve_as_of, closed=closed,
                  elapsed=elapsed, roll=shift if shift > 0 else 0.0,
                  fraction=tuple(fraction), t_mid=tuple(t_mid),
                  t_end=rest * 0.5 + (len(P) - closed - 1) * 0.5)


def discount_factor(curve: dict, premium: float, t: float) -> float:
    """df(t) = (1 + z(t) + β_u·ERP)^(−t)."""
    return (1.0 + interp_curve(curve, t) + premium) ** (-t)


def rolled_discount(curve: dict, premium: float, roll: float, t: float) -> float:
    """Дисконт от даты оценки по форвардам кривой книги: df(Δ + t) / df(Δ)."""
    if roll <= 0:
        return discount_factor(curve, premium, t)
    return discount_factor(curve, premium, roll + t) / discount_factor(curve, premium, roll)


# ------------------------------------------------------ пути, общие для клеток


@dataclass(frozen=True)
class WorldPaths:
    """Траектории мира на полугодиях сетки и дисконт по срокам потоков."""

    name: str
    key: tuple
    cpi: tuple
    food: tuple
    index: tuple                # Π(1 + half(cpi)) от якоря
    pi_lt: float                # lt.inflation
    food_last: float            # food_cpi последнего полугодия (для LT)
    z3: float                   # узел 3 года
    z_lt: float                 # узел LT
    premium: float              # β_u × ERP
    r_terminal: float           # z_LT + β_u × ERP
    df: tuple                   # по полугодиям (0 у закрытых)
    df_end: float


@dataclass(frozen=True)
class RegimePaths:
    """Цель маржи режима, сезонность и затухающее отклонение (§4.3)."""

    name: str
    target: tuple
    season: tuple
    deviation: tuple
    target_anchor: float
    season_anchor: float
    deviation_anchor: float
    target_lt: float
    season_terminal: tuple      # T1, T2


@dataclass(frozen=True)
class NetworkPaths:
    """Сеть тарифа роста: открытия, закрытия, площадь, эффективный индекс (§4.1)."""

    tariff: str
    opened: tuple
    closed: tuple
    area_end: tuple
    area_mid: tuple
    eff_avg: tuple              # Ā_eff(p)
    eff_avg_hist: dict          # Ā_eff исторических p − 2 первых двух полугодий
    close_lt: float


@dataclass(frozen=True)
class RevenuePaths:
    """Выручка (мир, тариф, спрос) (§4.2)."""

    revenue: tuple
    revenue_annual: tuple       # R(p) + R(p − 1)
    ticket: tuple
    traffic: tuple
    ticket_lt: float
    traffic_lt: float


def season_of(A: dict, p: str) -> float:
    """Сезонность маржи: +s в первом полугодии, −s во втором, с года first_period."""
    if int(p[:4]) < int(A["meta"]["first_period"][:4]):
        return 0.0
    s = float(A["margin"]["seasonal_h1_pp"])
    return s if p[5] == "1" else -s


def homogeneity(A: dict, world: str, demand: str, year: int | None) -> float:
    """Добавка однородности чека (§4.2): (1 − k)(π_LT(W) − π_LT(W_ref))·w(год).
    `year=None` — полная добавка (терминал)."""
    H = A["revenue"]["homogeneity"]
    k = float(A["revenue"]["ticket_k"][demand])
    if year is None:
        weight = 1.0
    else:
        weight = (year - H["ramp_from"]) / (H["ramp_to"] - H["ramp_from"])
        weight = min(1.0, max(0.0, weight))
    worlds = A["worlds"]
    gap = float(worlds[world]["lt"]["inflation"]) - float(worlds[H["reference_world"]]["lt"]["inflation"])
    return (1.0 - k) * gap * weight


def annuity_ratio(x: float, life: float) -> float:
    """ratio(x, L) = (1 − (1 + x)^(−L)) / (L·x); при x = 0 — 1 (§6)."""
    if x == 0:
        return 1.0
    return -math.expm1(-life * math.log1p(x)) / (life * x)


def observations(A: dict) -> list[tuple[str, float, float]]:
    """Наблюдения маржи A-P2u по порядку полугодий: (период, значение, se)."""
    raw = A["joint"]["regime_update"]["observations"]
    return sorted(((o["period"], float(o["value"]), float(o["se"])) for o in raw),
                  key=lambda o: period_index(o[0]))


# ------------------------------------------------------------------ контекст


class Context:
    """Всё, что общее у клеток одной книги на одну дату оценки.

    `valuation_date` и `market_price` — живые входы; по умолчанию —
    `meta.valuation_date` и `meta.market_price` книги.
    """

    def __init__(self, A: dict, facts: Facts | CoreFacts, *,
                 valuation_date: dt.date | str | None = None,
                 market_price: float | None = None):
        self.A = A
        self.facts = facts if isinstance(facts, CoreFacts) else core_facts(facts, A)
        M = A["meta"]
        self.P = periods(M["first_period"], M["last_period"])
        self.N = len(self.P)
        self.anchor = M["anchor_period"]
        v = valuation_date if valuation_date is not None else M["valuation_date"]
        v = v if isinstance(v, dt.date) else dt.date.fromisoformat(v)
        self.market_price = float(market_price if market_price is not None else M["market_price"])
        self.timing = make_timing(self.P, v, dt.date.fromisoformat(M["curve_as_of"]))
        V = A["valuation"]
        self.premium = float(V["beta_u"]) * float(V["erp"])
        self.governance = float(V["governance_discount"])
        self.year0 = int(self.P[0][:4])
        self._worlds: dict[str, WorldPaths] = {}
        self._regimes: dict[str, RegimePaths] = {}
        self._networks: dict[str, NetworkPaths] = {}
        self._revenues: dict[tuple, RevenuePaths] = {}
        self._maintenance: dict[str, tuple] = {}
        C, WC, FN, R = A["capex"], A["working_capital"], A["financing"], A["revenue"]
        # Траектории компании, не зависящие от клетки.
        self.vat = trajectory(R["vat_effect"], self.P)
        self.other = trajectory(R["other_growth"], self.P)
        self.nwc_pct = trajectory(WC["nwc_pct"], self.P)
        self.fixed_share = trajectory(FN["fixed_share"], self.P)
        self.legacy_weight = trajectory(FN["legacy_weight"], self.P)
        self.close = trajectory(A["network"]["close_rate"], self.P)
        cf = self.facts
        self.revenue_ltm_anchor = cf.revenue[prev_period(self.anchor)] + cf.revenue[self.anchor]
        self.opcash_anchor = float(WC["operating_cash_pct"]) * self.revenue_ltm_anchor
        self.buffer_anchor = float(FN["cash_buffer_pct"]) * self.revenue_ltm_anchor
        self.bridge_total = fsum(line.amount for line in cf.bridge if line.included)
        v = self.timing.valuation_date
        self.dividends_declared = fsum(d.amount for d in cf.dividends
                                       if d.in_company and d.ex_date is not None and d.ex_date <= v)
        self.half_life = int(round(2 * float(C["asset_life_years"])))
        pays_from = period_index(FN["dividends_from"])
        self.pays = tuple(period_index(p) >= pays_from for p in self.P)
        self.is_h1 = tuple(p[5] == "1" for p in self.P)
        self.infra_on = tuple(int(p[:4]) >= int(C["infra_from_year"]) for p in self.P)
        self._rates: dict[tuple, tuple] = {}

    # ---------------------------------------------------------------- мир
    def world(self, name: str) -> WorldPaths:
        hit = self._worlds.get(name)
        if hit is None:
            hit = self._worlds[name] = self._make_world(name)
        return hit

    def _make_world(self, name: str) -> WorldPaths:
        W = self.A["worlds"][name]
        key, cpi, food = (trajectory(W[k], self.P) for k in ("key_rate", "cpi", "food_cpi"))
        index, level = [], 1.0
        for c in cpi:
            level *= 1.0 + half_rate(c)
            index.append(level)
        curve, T = W["zero_curve"], self.timing
        df = tuple(rolled_discount(curve, self.premium, T.roll, t) if f else 0.0
                   for f, t in zip(T.fraction, T.t_mid))
        z_lt = float(curve["LT"])
        return WorldPaths(
            name=name, key=key, cpi=cpi, food=food, index=tuple(index),
            pi_lt=float(W["lt"]["inflation"]), food_last=food[-1], z3=float(curve["3"]),
            z_lt=z_lt, premium=self.premium, r_terminal=z_lt + self.premium, df=df,
            df_end=rolled_discount(curve, self.premium, T.roll, T.t_end))

    # ---------------------------------------------------------------- режим
    def regime(self, name: str) -> RegimePaths:
        hit = self._regimes.get(name)
        if hit is None:
            hit = self._regimes[name] = self._make_regime(name)
        return hit

    def _make_regime(self, name: str) -> RegimePaths:
        A, P = self.A, self.P
        spec = A["margin"]["targets"][name]
        target = trajectory(spec, P)
        season = tuple(season_of(A, p) for p in P)
        t_anchor = path_value(spec, self.anchor)
        s_anchor = season_of(A, self.anchor)
        d_anchor = self.facts.margin_anchor - t_anchor - s_anchor
        rho = float(A["margin"]["deviation_persistence"])
        sigma2 = float(A["joint"]["regime_update"]["sigma_pp"]) ** 2
        seen = {p: (value, se) for p, value, se in observations(A)}
        # §4.3: AR(1) от якоря (дисперсия 0), наблюдение — шаг фильтра Калмана.
        dev_last, var_last, last = d_anchor, 0.0, period_index(self.anchor)
        deviation = []
        for i, p in enumerate(P):
            k = period_index(p) - last
            predicted = rho ** k * dev_last
            if p not in seen:
                deviation.append(predicted)
                continue
            value, se = seen[p]
            observed = value - target[i] - season[i]
            if se == 0:
                dev, var = observed, 0.0
            else:
                prior = sigma2 * (1.0 - rho ** (2 * k)) + rho ** (2 * k) * var_last
                weight = prior / (prior + se * se)
                dev = predicted + weight * (observed - predicted)
                var = (1.0 - weight) * prior
            deviation.append(dev)
            dev_last, var_last, last = dev, var, period_index(p)
        terminal_year = int(P[-1][:4]) + 1
        return RegimePaths(
            name=name, target=target, season=season, deviation=tuple(deviation),
            target_anchor=t_anchor, season_anchor=s_anchor, deviation_anchor=d_anchor,
            target_lt=target[-1],
            season_terminal=(season_of(A, f"{terminal_year}H1"), season_of(A, f"{terminal_year}H2")))

    # ---------------------------------------------------------------- сеть
    def network(self, tariff: str) -> NetworkPaths:
        hit = self._networks.get(tariff)
        if hit is None:
            hit = self._networks[tariff] = self._make_network(tariff)
        return hit

    def _make_network(self, tariff: str) -> NetworkPaths:
        A, P, cf = self.A, self.P, self.facts
        NW = A["network"]
        mu = [float(x) for x in NW["maturity_curve"]]
        n = len(mu) - 1
        d = float(NW["new_space_density"])
        kappa = float(NW["closed_productivity"])
        growth = trajectory(NW["net_growth"][tariff], P)

        def immature(q: str) -> float:
            # Незрелая часть последних n исторических когорт на конец q (d = 1).
            out, p = [], q
            for a in range(n):
                out.append(cf.gross_opened[p] * (1.0 - mu[a]))
                p = prev_period(p)
            return fsum(out)

        back1 = prev_period(self.anchor)
        back2 = prev_period(back1)
        eff_hist = {q: cf.area_end[q] - immature(q) for q in (back2, back1, self.anchor)}
        eff_avg_hist = {back1: (eff_hist[back2] + eff_hist[back1]) / 2.0,
                        self.anchor: (eff_hist[back1] + eff_hist[self.anchor]) / 2.0}
        # Когорты: исторические (плотность 1) и прогнозные (плотность d), от старых к новым.
        cohorts = [(cf.gross_opened[q], 1.0) for q in cf.gross_opened]
        area, eff = cf.area_end[self.anchor], eff_hist[self.anchor]
        opened_l, closed_l, end_l, mid_l, avg_l = [], [], [], [], []
        for i in range(len(P)):
            closed = area * self.close[i] / 2.0
            opened = area * growth[i] / 2.0 + closed
            maturing = fsum(cohorts[-a][0] * cohorts[-a][1] * (mu[a] - mu[a - 1])
                            for a in range(1, min(n, len(cohorts)) + 1))
            eff_new = eff - closed * kappa + opened * d * mu[0] + maturing
            new_area = area + opened - closed
            opened_l.append(opened)
            closed_l.append(closed)
            end_l.append(new_area)
            mid_l.append((area + new_area) / 2.0)
            avg_l.append((eff + eff_new) / 2.0)
            cohorts.append((opened, d))
            area, eff = new_area, eff_new
        return NetworkPaths(tariff=tariff, opened=tuple(opened_l), closed=tuple(closed_l),
                            area_end=tuple(end_l), area_mid=tuple(mid_l), eff_avg=tuple(avg_l),
                            eff_avg_hist=eff_avg_hist, close_lt=self.close[-1])

    # -------------------------------------------------------------- выручка
    def revenue(self, world: str, tariff: str, demand: str) -> RevenuePaths:
        key = (world, tariff, demand)
        hit = self._revenues.get(key)
        if hit is None:
            hit = self._revenues[key] = self._make_revenue(world, tariff, demand)
        return hit

    def _make_revenue(self, world: str, tariff: str, demand: str) -> RevenuePaths:
        A, P, cf = self.A, self.P, self.facts
        W, G = self.world(world), self.network(tariff)
        R = A["revenue"]
        k = float(R["ticket_k"][demand])
        shift = trajectory(R["ticket_shift"][demand], P)
        traffic = trajectory(R["traffic"][demand], P)
        back1 = prev_period(self.anchor)
        rev_hist = {back1: cf.revenue[back1], self.anchor: cf.revenue[self.anchor]}
        revenue, annual, ticket = [], [], []
        for i, p in enumerate(P):
            tk = (k * W.food[i] + shift[i] + self.vat[i]
                  + homogeneity(A, world, demand, int(p[:4])))
            if i >= 2:
                base, eff_base = revenue[i - 2], G.eff_avg[i - 2]
            else:                       # p − 2 — факт: якорь − 1 и якорь
                q = self.anchor if i == 1 else back1
                base, eff_base = rev_hist[q], G.eff_avg_hist[q]
            r = (base * (G.eff_avg[i] / eff_base) * (1.0 + tk) * (1.0 + traffic[i])
                 * (1.0 + self.other[i]))
            previous = revenue[i - 1] if i else rev_hist[self.anchor]
            revenue.append(r)
            annual.append(r + previous)
            ticket.append(tk)
        ticket_lt = (k * W.food_last + shift[-1] + self.vat[-1]
                     + homogeneity(A, world, demand, None))
        return RevenuePaths(revenue=tuple(revenue), revenue_annual=tuple(annual),
                            ticket=tuple(ticket), traffic=traffic, ticket_lt=ticket_lt,
                            traffic_lt=traffic[-1])

    def rates(self, world: str, credit: str) -> tuple:
        """Ставки полугодий (§4.9): (half(debt_rate), half(cash_yield_k·key), debt_rate)."""
        key = (world, credit)
        hit = self._rates.get(key)
        if hit is None:
            FN, W = self.A["financing"], self.world(world)
            legacy = float(FN["legacy_rate"])
            s_fixed = float(FN["spread_fixed"][credit])
            s_float = float(FN["spread_float"][credit])
            yield_k = float(FN["cash_yield_k"])
            debt = []
            for i in range(self.N):
                lw, fs = self.legacy_weight[i], self.fixed_share[i]
                fixed_rate = lw * legacy + (1.0 - lw) * (W.z3 + s_fixed)
                debt.append(fs * fixed_rate + (1.0 - fs) * (W.key[i] + s_float))
            hit = self._rates[key] = (tuple(half_rate(r) for r in debt),
                                      tuple(half_rate(yield_k * k) for k in W.key), tuple(debt))
        return hit

    def maintenance(self, level: str) -> tuple:
        hit = self._maintenance.get(level)
        if hit is None:
            hit = self._maintenance[level] = trajectory(self.A["capex"]["maintenance"][level],
                                                        self.P)
        return hit

    def cell(self, world: str, regime: str, capex: str) -> Cell:
        return make_cell(self.A, world, regime, capex)


# ------------------------------------------------------------- проход клетки


@dataclass(frozen=True, slots=True)
class HalfRow:
    """Строка полугодия явного участка."""

    period: str
    revenue: float
    revenue_annual: float
    ticket: float
    traffic: float
    area_end: float
    eff_area_avg: float
    opened: float
    closed: float
    margin: float
    target: float
    deviation: float
    adj_ebitda: float
    lti: float
    ebitda_rep: float
    da: float
    ebit: float
    capex: float
    capex_maintenance: float
    capex_growth: float
    capex_infra: float
    price_index: float
    nwc: float
    nwc_change: float
    opcash: float
    opcash_change: float
    buffer: float
    lease: float
    proceeds: float
    tax_base: float
    tax_unlevered: float
    tax_actual: float
    shield: float
    fcff: float
    debt_rate: float
    gross_debt_start: float
    interest: float
    net_debt_pre: float
    dividends: float
    net_debt: float
    ebitda_rep_ltm: float
    leverage: float
    fraction: float
    t: float
    df: float


@dataclass(frozen=True, slots=True)
class TerminalHalf:
    """Полугодие первого терминального года (§6)."""

    period: str
    revenue: float
    revenue_annual: float
    margin: float
    adj_ebitda: float
    lti: float
    capex: float
    capex_maintenance: float
    capex_replacement: float
    capex_pi: float
    price_index: float
    da: float
    tax_base: float
    tax: float
    nwc: float
    nwc_change: float
    opcash: float
    opcash_change: float
    lease: float
    proceeds: float
    fcff: float
    f_pi: float


@dataclass(frozen=True)
class Terminal:
    """Терминальная стоимость клетки (§6); TV и TV_S — на конец явного участка."""

    growth: float
    rate: float
    pi: float
    halves: tuple
    da_half: float
    da_pi_half: float
    tv_flow: float
    tv_shield: float
    shield_annual: float
    ebitda_rep_annual: float


@dataclass(frozen=True)
class Claims:
    """Требования D на дату оценки (§7.1)."""

    net_debt_fact: float
    opcash_anchor: float
    bridge: float
    rolled: float           # денежный результат закрытых и прошедшей части текущего
    dividends: float        # объявленные с отсечкой ≤ даты оценки
    total: float


@dataclass(frozen=True)
class CellResult:
    cell: Cell
    row_data: tuple = field(repr=False)     # кортежи строк в порядке полей HalfRow
    terminal: Terminal = field(repr=False)
    ev: float = 0.0
    pv_fcff: float = 0.0
    pv_shield: float = 0.0
    pv_terminal: float = 0.0
    terminal_share: float = 0.0
    claims: Claims | None = None
    equity: float = 0.0
    price: float = 0.0
    ebitda_ntm: float = 0.0
    ev_ebitda_fwd: float | None = None
    max_leverage: float = 0.0
    margin_min: float = 0.0
    margin_max: float = 0.0
    capex_pct_years: tuple = ()             # ((год, capex/выручка), …)

    @cached_property
    def rows(self) -> tuple:
        """Строки полугодий (`HalfRow`) — собираются при первом обращении."""
        return tuple(HalfRow(*t) for t in self.row_data)

    @property
    def d(self) -> float:
        return self.claims.total

    @property
    def r_terminal(self) -> float:
        return self.terminal.rate


def price_of_equity(equity: float, governance: float, shares_mln: float) -> float:
    """Цена из капитала (§7.2): дисконт за управление — только у положительного."""
    return (equity * (1.0 - governance) if equity > 0 else equity) * 1000.0 / shares_mln


def run_cell(ctx: Context, cell: Cell) -> CellResult:
    """Полный проход клетки: строки полугодий, терминал, EV, D, цена."""
    A, P, cf, T = ctx.A, ctx.P, ctx.facts, ctx.timing
    W = ctx.world(cell.world)
    RG = ctx.regime(cell.regime)
    G = ctx.network(cell.growth)
    RV = ctx.revenue(cell.world, cell.growth, cell.demand)
    mnt = ctx.maintenance(cell.capex)
    half_debt, half_yield, debt_rates = ctx.rates(cell.world, cell.credit)
    C, WC, TX, FN = A["capex"], A["working_capital"], A["tax"], A["financing"]
    phi = float(C["maintenance_area_share"])
    price_m2, infra_m2 = float(C["price_per_m2"]), float(C["infra_per_m2"])
    disposal = float(C["disposal_proceeds_pct"])
    lti_pct = float(A["margin"]["lti_pct"])
    h1x, opc_pct = float(WC["h1_excess_pct"]), float(WC["operating_cash_pct"])
    lease_pct = float(WC["lease_adj_pct"])
    tau, padd = float(TX["rate"]), float(TX["permanent_add_pct"])
    buf_pct = float(FN["cash_buffer_pct"])
    lt = float(FN["target_leverage"])
    H = ctx.half_life
    target, season, deviation = RG.target, RG.season, RG.deviation
    revenue, revenue_annual = RV.revenue, RV.revenue_annual
    opened_l, closed_l, area_mid, index = G.opened, G.closed, G.area_mid, W.index
    fraction, t_mid, df = T.fraction, T.t_mid, W.df

    nwc_prev, opc_prev, buf_prev = cf.nwc, ctx.opcash_anchor, ctx.buffer_anchor
    nd_prev = cf.net_debt + cf.dividends_payable
    rep_prev = cf.ebitda_rep[ctx.anchor]
    capex_hist = [cf.capex_anchor]
    x0 = area_mid[0] * index[0] / revenue_annual[0]
    data, pv_f, pv_s, margins, leverage = [], [], [], [], []
    for i, p in enumerate(P):
        R, R_ann = revenue[i], revenue_annual[i]
        margin = target[i] + season[i] + deviation[i]
        ebitda = R * margin
        lti = lti_pct * R
        ebitda_rep = ebitda - lti
        # capex (§4.5): поддерживающий с физической долей, открытия, инфраструктура
        idx = index[i]
        opened, closed = opened_l[i], closed_l[i]
        maint = R * mnt[i] * ((1.0 - phi) + phi * (area_mid[i] * idx / R_ann) / x0)
        growth_capex = opened * price_m2 * idx
        infra = max(0.0, opened - closed) * infra_m2 * idx if ctx.infra_on[i] else 0.0
        capex = maint + growth_capex + infra
        # D&A: база якоря линейно за 2L полугодий + когорты capex по 1/(2L)
        k = i + 1
        da = (cf.da_anchor * max(0.0, 1.0 - k / H)
              + fsum(capex_hist[k - min(k, H):k]) / H)
        ebit = ebitda - da
        # оборотный капитал, касса, аренда (§4.6)
        nwc = ctx.nwc_pct[i] * R_ann + (h1x * R_ann if ctx.is_h1[i] else 0.0)
        opc = opc_pct * R_ann
        buf = buf_pct * R_ann
        lease = lease_pct * R
        proceeds = disposal * R
        # налог без рычага (§4.7) и FCFF (§4.8)
        base = ebit - lti + padd * R
        tax_u = tau * max(0.0, base)
        d_nwc, d_opc = nwc - nwc_prev, opc - opc_prev
        fcff = ebitda - lti - tax_u - capex - d_nwc - d_opc + lease + proceeds
        # проценты от долга и кассы начала полугодия (§4.9)
        gross = nd_prev + opc_prev + buf_prev
        interest = gross * half_debt[i] - buf_prev * half_yield[i]
        tax_a = tau * max(0.0, base - interest)
        shield = tax_u - tax_a
        # путь долга и дивиденды модели
        nd_pre = nd_prev - (fcff + shield - interest)
        ltm = ebitda_rep + rep_prev
        div = max(0.0, lt * ltm - nd_pre) if ctx.pays[i] else 0.0
        nd = nd_pre + div
        lev = nd / ltm if ltm > 0 else math.inf
        # порядок — порядок полей HalfRow
        data.append((p, R, R_ann, RV.ticket[i], RV.traffic[i], G.area_end[i], G.eff_avg[i],
                     opened, closed, margin, target[i], deviation[i], ebitda, lti, ebitda_rep,
                     da, ebit, capex, maint, growth_capex, infra, idx, nwc, d_nwc, opc, d_opc,
                     buf, lease, proceeds, base, tax_u, tax_a, shield, fcff, debt_rates[i],
                     gross, interest, nd_pre, div, nd, ltm, lev, fraction[i], t_mid[i], df[i]))
        if fraction[i]:
            pv_f.append(fcff * fraction[i] * df[i])
            pv_s.append(shield * fraction[i] * df[i])
        margins.append(margin)
        leverage.append(lev)
        nwc_prev, opc_prev, buf_prev, nd_prev, rep_prev = nwc, opc, buf, nd, ebitda_rep
        capex_hist.append(capex)

    terminal = _terminal(ctx, cell, W, RG, G, RV, x0, data[-2], data[-1])
    pv_fcff, pv_shield = fsum(pv_f), fsum(pv_s)
    pv_terminal = (terminal.tv_flow + terminal.tv_shield) * W.df_end
    ev = pv_fcff + pv_shield + pv_terminal

    # требования на дату оценки (§7.1): денежный результат закрытых полугодий и
    # прошедшей части текущего (FCFF + щит − проценты)
    closed = T.closed
    cash = [row[_FCFF] + row[_SHIELD] - row[_INTEREST] for row in data[:closed + 1]]
    cash[-1] *= T.elapsed
    rolled = fsum(cash)
    total = fsum([cf.net_debt, ctx.opcash_anchor, ctx.bridge_total, -rolled,
                  ctx.dividends_declared])
    claims = Claims(net_debt_fact=cf.net_debt, opcash_anchor=ctx.opcash_anchor,
                    bridge=ctx.bridge_total, rolled=rolled, dividends=ctx.dividends_declared,
                    total=total)
    equity = ev - total
    ahead = (data[closed + 1][_EBITDA] if closed + 1 < len(data)
             else terminal.halves[0].adj_ebitda)
    ntm = data[closed][_EBITDA] + ahead
    return CellResult(
        cell=cell, row_data=tuple(data), terminal=terminal, ev=ev, pv_fcff=pv_fcff,
        pv_shield=pv_shield, pv_terminal=pv_terminal,
        terminal_share=pv_terminal / ev if ev else math.inf, claims=claims, equity=equity,
        price=price_of_equity(equity, ctx.governance, cf.shares_mln), ebitda_ntm=ntm,
        ev_ebitda_fwd=ev / ntm if ntm > 0 else None, max_leverage=max(leverage),
        margin_min=min(margins), margin_max=max(margins),
        capex_pct_years=_capex_by_year(cf, P, data))


# Номера полей HalfRow в кортеже строки.
_FIELDS = [f.name for f in fields(HalfRow)]
_EBITDA, _CAPEX, _REVENUE = (_FIELDS.index(n) for n in ("adj_ebitda", "capex", "revenue"))
_FCFF, _SHIELD, _INTEREST = (_FIELDS.index(n) for n in ("fcff", "shield", "interest"))
_INDEX, _AREA, _NWC, _OPCASH = (_FIELDS.index(n) for n in ("price_index", "area_end", "nwc",
                                                             "opcash"))


def _capex_by_year(cf: CoreFacts, P: list[str], data: list) -> tuple:
    """capex / выручка по календарным годам пути (год якоря — с фактом якоря)."""
    years: dict[int, list] = {}
    if cf.anchor[:4] == P[0][:4]:
        years[int(P[0][:4])] = [[cf.capex_anchor], [cf.revenue[cf.anchor]]]
    for row in data:
        acc = years.setdefault(int(row[0][:4]), [[], []])
        acc[0].append(row[_CAPEX])
        acc[1].append(row[_REVENUE])
    return tuple((y, fsum(c) / fsum(r)) for y, (c, r) in sorted(years.items()))


def _terminal(ctx: Context, cell: Cell, W: WorldPaths, RG: RegimePaths, G: NetworkPaths,
              RV: RevenuePaths, x0: float, last_h1: tuple, last: tuple) -> Terminal:
    """Терминал (§6): два полугодия года после last_period, разделение Гордона."""
    A = ctx.A
    NW, C, WC, TX, FN = A["network"], A["capex"], A["working_capital"], A["tax"], A["financing"]
    tau, padd = float(TX["rate"]), float(TX["permanent_add_pct"])
    phi = float(C["maintenance_area_share"])
    price_m2 = float(C["price_per_m2"])
    life = float(C["asset_life_years"])
    lti_pct = float(A["margin"]["lti_pct"])
    h1x, opc_pct = float(WC["h1_excess_pct"]), float(WC["operating_cash_pct"])
    lease_pct, disposal = float(WC["lease_adj_pct"]), float(C["disposal_proceeds_pct"])
    nwc_lt = ctx.nwc_pct[-1]
    mnt_lt = ctx.maintenance(cell.capex)[-1]
    r = W.r_terminal
    d = float(NW["new_space_density"])
    kappa = float(NW["closed_productivity"])
    cl = G.close_lt
    mu_end = float(NW["maturity_curve"][-1])
    g = (1.0 + RV.ticket_lt) * (1.0 + RV.traffic_lt) * (1.0 + (d * mu_end - kappa) * cl) - 1.0
    if g >= r:
        g = r - GROWTH_GUARD
    pi = W.pi_lt if W.pi_lt < r else r - GROWTH_GUARD

    area = last[_AREA]
    year = int(ctx.P[-1][:4]) + 1
    idx = last[_INDEX]
    prev_rev, nwc_prev, opc_prev = last[_REVENUE], last[_NWC], last[_OPCASH]
    parts = []
    for h, src in ((1, last_h1), (2, last)):
        idx *= 1.0 + half_rate(pi)
        R = src[_REVENUE] * (1.0 + g)
        R_ann = R + prev_rev
        prev_rev = R
        margin = RG.target_lt + RG.season_terminal[h - 1]
        physical = R * mnt_lt * phi * (area * idx / R_ann) / x0
        maint = R * mnt_lt * (1.0 - phi) + physical
        replacement = area * cl / 2.0 * price_m2 * idx
        nwc = nwc_lt * R_ann + (h1x * R_ann if h == 1 else 0.0)
        opc = opc_pct * R_ann
        parts.append(dict(period=f"{year}H{h}", R=R, R_ann=R_ann, margin=margin, idx=idx,
                          ebitda=R * margin, lti=lti_pct * R, maint=maint,
                          replacement=replacement, capex=maint + replacement,
                          capex_pi=replacement + physical, nwc=nwc, d_nwc=nwc - nwc_prev,
                          opc=opc, d_opc=opc - opc_prev, lease=lease_pct * R,
                          proceeds=disposal * R))
        nwc_prev, opc_prev = nwc, opc
    capex_ann = parts[0]["capex"] + parts[1]["capex"]
    capex_pi_ann = parts[0]["capex_pi"] + parts[1]["capex_pi"]
    da_pi = capex_pi_ann * annuity_ratio(pi, life) / 2.0
    da_half = ((capex_ann - capex_pi_ann) * annuity_ratio(g, life)
               + capex_pi_ann * annuity_ratio(pi, life)) / 2.0

    halves = []
    for q in parts:
        base = q["ebitda"] - q["lti"] - da_half + padd * q["R"]
        tax = tau * max(0.0, base)
        fcff = (q["ebitda"] - q["lti"] - tax - q["capex"] - q["d_nwc"] - q["d_opc"]
                + q["lease"] + q["proceeds"])
        f_pi = (tau * da_pi if base > 0 else 0.0) - q["capex_pi"]
        halves.append(TerminalHalf(
            period=q["period"], revenue=q["R"], revenue_annual=q["R_ann"], margin=q["margin"],
            adj_ebitda=q["ebitda"], lti=q["lti"], capex=q["capex"],
            capex_maintenance=q["maint"], capex_replacement=q["replacement"],
            capex_pi=q["capex_pi"], price_index=q["idx"], da=da_half, tax_base=base, tax=tax,
            nwc=q["nwc"], nwc_change=q["d_nwc"], opcash=q["opc"], opcash_change=q["d_opc"],
            lease=q["lease"], proceeds=q["proceeds"], fcff=fcff, f_pi=f_pi))

    def gordon(f1: float, f2: float, x: float) -> float:
        return (f1 * (1.0 + r) ** 0.75 + f2 * (1.0 + r) ** 0.25) / (r - x)

    h1, h2 = halves
    tv = gordon(h1.fcff - h1.f_pi, h2.fcff - h2.f_pi, g) + gordon(h1.f_pi, h2.f_pi, pi)
    rep_annual = (h1.adj_ebitda - h1.lti) + (h2.adj_ebitda - h2.lti)
    shield = (tau * float(FN["target_leverage"]) * rep_annual
              * (W.z_lt + float(FN["spread_fixed"][cell.credit])))
    tv_shield = gordon(shield / 2.0, shield / 2.0, g)
    return Terminal(growth=g, rate=r, pi=pi, halves=tuple(halves), da_half=da_half,
                    da_pi_half=da_pi, tv_flow=tv, tv_shield=tv_shield, shield_annual=shield,
                    ebitda_rep_annual=rep_annual)
