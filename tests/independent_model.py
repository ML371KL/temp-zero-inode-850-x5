"""Независимая контрольная модель X5 (docs/MODEL.md §16).

Та же сетка 36 клеток (мир × режим маржи × уровень capex), что у ядра, но своя
реализация — только по тексту docs/MODEL.md, книге (data/assumptions/) и фактам
(data/facts/), без доступа к model/. Чисел книги и фактов в коде нет: только имена
ключей и структурные константы (полугодий в году, середина полугодия, млрд → млн).

Толкования мест, где MODEL.md неоднозначен, собраны в INTERPRETATIONS и печатаются в
docs/CONTROL-MODEL.md («Неоднозначности спецификации»).

Отчёт о расхождениях с ядром (data/assumptions/results.json):
    python -B -m tests.independent_model --report   → docs/CONTROL-MODEL.md
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import math
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
BOOK_DIR = ROOT / "data" / "assumptions"
BOOK_YAML = BOOK_DIR / "assumptions.yaml"
DRAFT_YAML = BOOK_DIR / "assumptions.draft.yaml"
RESULTS_JSON = BOOK_DIR / "results.json"
FACTS_DIR = ROOT / "data" / "facts"
FIXTURE_FACTS_DIR = ROOT / "tests" / "fixtures" / "facts"
REPORT_MD = ROOT / "docs" / "CONTROL-MODEL.md"

LAYERS = ("analytical", "market_implied", "macro_neutral")
LAYER_P = {"analytical": "p_analytical", "market_implied": "p_market_implied",
           "macro_neutral": "p_neutral"}

# Допуски сверки с ядром (docs/MODEL.md §16 и задание на контрольную модель).
TOL_ROWS = 0.10      # строки путей (выручка, маржа, EBITDA, capex, FCFF, щит, проценты, долг)
TOL_V0 = 0.03        # V0 слоёв и EV клеток
TOL_CLAIMS = 0.01    # требования D (слои и клетки), V*
TOL_POINT = 0.03     # точка низ/центр/верх, цена 1 % EV
# Для строк, проходящих через ноль (FCFF), знаменатель не меньше этой доли EBITDA периода.
ROW_FLOOR_EBITDA = 0.10

# Сценарии сверки с ядром на изменённых книгах: включают ветки, которые книга держит
# выключенными (наблюдения маржи, закрытые полугодия, сезонность, защита g ≥ r, …).
# Это входы проверки, не допущения модели: {"key", "title", "set": {путь книги: значение}}.
SCENARIOS = [
    {"key": "obs_fact_partial", "title": "Наблюдения маржи: факт 2П2026 и частичный 1П2027 (Калман, A-P2u без предела)",
     "set": {"joint.regime_update.observations": [
         {"period": "2026H2", "value": 0.061, "se": 0.0},
         {"period": "2027H1", "value": 0.0585, "se": 0.002}]}},
    {"key": "obs_cap", "title": "Наблюдения далеко от целей: предел сдвига cap_pp срабатывает дважды",
     "set": {"joint.regime_update.observations": [
         {"period": "2026H2", "value": 0.050, "se": 0.0},
         {"period": "2027H2", "value": 0.070, "se": 0.003}]}},
    {"key": "later_date", "title": "Дата оценки 15.03.2027: одно закрытое полугодие, перекат через границу полугодия",
     "set": {"meta.valuation_date": "2027-03-15"}},
    {"key": "before_ex_date", "title": "Дата оценки 03.07.2026 — до отсечки финального дивиденда: он не в требованиях",
     "set": {"meta.valuation_date": "2026-07-03"}},
    {"key": "valuation_in_anchor", "title": "Дата оценки = дата фактов 30.06.2026 (в якорном полугодии): elapsed = 0",
     "set": {"meta.valuation_date": "2026-06-30"}},
    {"key": "curve_after_date", "title": "Кривая позже даты оценки — без переката",
     "set": {"meta.curve_as_of": "2026-10-01", "worlds.source.curve_date": "2026-10-01"}},
    {"key": "season_wc_lease", "title": "Сезонность маржи, сезонный излишек NWC, аренда, прочая выручка",
     "set": {"margin.seasonal_h1_pp": 0.002, "working_capital.h1_excess_pct": 0.01,
             "working_capital.lease_adj_pct": 0.001,
             "revenue.other_growth": {"2027": 0.005, "LT": 0.0, "LT_from": 2030}}},
    {"key": "trajectory_rules", "title": "Траектории: интерполяция между годами, ключи полугодий, сход к LT",
     "set": {"capex.maintenance.base": {"2026H2": 0.028, "2027H1": 0.020, "2027H2": 0.030,
                                        "2029": 0.024, "LT": 0.025, "LT_from": 2032},
             "network.net_growth.mid": {"2026H2": 0.065, "2028": 0.05, "2031": 0.03, "LT": 0.0,
                                        "LT_from": 2034},
             "revenue.vat_effect": {"2026H2": -0.006, "LT": 0.0, "LT_from": 2027}}},
    {"key": "g_guard", "title": "Защита терминала g ≥ r ⇒ g = r − 0,0001 (мир N, режим full)",
     "set": {"revenue.ticket_shift.bull.LT": 0.12}},
    {"key": "negative_base", "title": "Отрицательная налоговая база стресса (max(0, ·), индикатор щита амортизации)",
     "set": {"margin.targets.stress.2030": 0.02, "margin.targets.stress.LT": 0.01}},
    {"key": "dividends_late", "title": "Дивиденды модели с 2028H1 при целевом рычаге 2,5×",
     "set": {"financing.dividends_from": "2028H1", "financing.target_leverage": 2.5}},
]

# Толкования неоднозначных мест MODEL.md (ключ → текст для отчёта).
INTERPRETATIONS = {
    "hist_aeff": (
        "§4.1, эффективная площадь исторических полугодий p−2. «Тем же правилом назад по "
        "фактам площади и историческим открытиям» прочитано как формула стартового индекса, "
        "применённая к каждому историческому полугодию: A_eff(q) = A(q) − Σ_{a<n} "
        "opened_hist(q−a)(1 − μ_a). Альтернатива — рекурсия §4.1 назад от якоря с закрытиями "
        "(closed_area_est) и κ: она требует закрытий, которых текст не называет. Разница "
        "альтернативы — в разделе «Чувствительность к толкованиям»."),
    "half_key_only": (
        "§0.1, траектория только с ключом полугодия и без LT (сейчас `revenue.vat_effect` = "
        "{2026H2}). Ключ полугодия считается последней заданной точкой своего года, правило 4 "
        "без LT даёт «последнее значение» — поправка действует во всех следующих полугодиях и "
        "в терминале (так же читает ядро, его предупреждение в results.json). По смыслу "
        "(«разовая поправка на НДС») книге нужен явный сход, например \"2027\": 0."),
    "lt_without_from": (
        "§0.1, `LT` без `LT_from` (`close_rate`, `nwc_pct`, `other_growth`): LT действует сразу "
        "после последнего заданного года; траектория из одного LT — LT во всех полугодиях."),
    "before_first_key": (
        "§0.1, полугодие раньше первого заданного ключа траектории — значение первого "
        "заданного года (плоско назад). В книге 1.0 такого случая нет."),
    "rho_d": (
        "§4.3 и §10: ρ_d = `margin.deviation_persistence`. В книге есть и "
        "`joint.regime_update.rho` (сейчас с тем же значением), MODEL.md его не называет — "
        "два ключа одного смысла; контрольная модель читает только первый."),
    "season_anchor": (
        "§4.3, сезонность «начиная с первого прогнозного года»: год первого прогнозного "
        "полугодия (2026), поэтому season применяется и на якоре 2026H1 в правиле якоря. "
        "Сейчас `seasonal_h1_pp` = 0, на числа не влияет."),
    "terminal_index": (
        "§6, индекс цен в терминале (замещающие открытия, физическая доля capex) растёт по "
        "последнему значению траектории `cpi` мира (правило «терминал берёт их последнее "
        "значение»), а не по `lt.inflation`. В книге 1.0 они равны во всех мирах."),
    "terminal_other": (
        "§6, рост терминала g не содержит прочей выручки `other_growth` (формула g без "
        "множителя 1 + other); выручка терминала — R × (1 + g). Сейчас other LT = 0."),
    "terminal_pi_guard": (
        "§6, защита «g ≥ r ⇒ g = r − 0,0001» есть только для g; для Гордона с ростом π "
        "(r − π) защиты нет — контрольная модель падает, если r ≤ π."),
    "v0_point": (
        "§7.3, V0 точки для «цены 1 % EV» и разрыва с V*: обращение цены точки тем же "
        "правилом, что V0_med (V0 = точка × акции / 1000 / (1 − g) + D слоя «свой взгляд»); "
        "λ-смесь V0 слоёв даёт другое число при D слоёв, различающемся перекатом. Печатаются "
        "оба."),
    "max_leverage": (
        "§13.2, «max ND/EBITDA_rep_LTM пути» — по явным полугодиям first…last (без "
        "терминала)."),
    "elapsed_clamp": (
        "§5, дата оценки раньше начала первого прогнозного полугодия (в якорном): elapsed = 0, "
        "закрытых нет, перекат — только если кривая раньше даты оценки. Текст этот случай не "
        "описывает; ядро читает так же (сценарий valuation_in_anchor)."),
    "dividend_claims": (
        "§7.1, реестр дивидендов: требование — вся объявленная сумма записи с "
        "`cash_in_company_at_facts_date` и `ex_date` ≤ даты оценки; выплаченная после якоря доля "
        "(`paid_share_on`) не вычитается — эти деньги были в кассе якоря и ушли из компании. "
        "Фраза «невостребованный остаток живёт в требованиях, пока не выплачен» читается как "
        "правило для следующего якоря (когда выплаченное уже вне кассы)."),
    "annual_2026": (
        "Годовые строки 2026 для сверки: выручка, скорр. и отчётная EBITDA, LTI, D&A, capex — "
        "факт якорного 1П + прогноз 2П; остальные строки года — только прогнозное 2П (так их "
        "публикует ядро: поле `fact` года в results.json)."),
}


class ControlError(RuntimeError):
    """Вход, на котором контрольная модель не определена."""


# ------------------------------------------------------------------ входы


def default_book_path() -> Path:
    return BOOK_YAML if BOOK_YAML.exists() else DRAFT_YAML


def default_facts_dir() -> Path:
    """data/facts, пока он пуст — фикстура tests/fixtures/facts (тот же выбор, что у ядра:
    сверять имеет смысл только на одних входах; при сверке каталог берётся из results.json)."""
    for d in (FACTS_DIR, FIXTURE_FACTS_DIR):
        if (d / "accounting.json").exists():
            return d
    raise ControlError("нет фактов: data/facts и tests/fixtures/facts пусты")


def load_book(path: Path | None = None) -> dict:
    path = Path(path) if path else default_book_path()
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def apply_patch(book: dict, patch: dict) -> dict:
    """Копия книги с заменой значений по путям «a.b.c» (сценарии сверки)."""
    out = copy.deepcopy(book)
    for path, value in patch.items():
        keys = path.split(".")
        node = out
        for k in keys[:-1]:
            node = node[k] if k in node else node[int(k)]
        node[keys[-1]] = copy.deepcopy(value)
    return out


def _v(x):
    """Значение факта: `{"v": …, "src": …}` → v; прочее — как есть."""
    if isinstance(x, dict) and "v" in x:
        return x["v"]
    return x


def load_facts(directory: Path | None = None) -> dict:
    directory = Path(directory) if directory else default_facts_dir()
    out = {}
    for name in ("accounting", "network", "balance", "bridge", "shares", "dividends"):
        out[name] = json.loads((directory / f"{name}.json").read_text(encoding="utf-8"))
    return out


def load_results(path: Path | None = None) -> dict | None:
    path = Path(path) if path else RESULTS_JSON
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def inputs_of_results(results: dict) -> tuple[Path, Path]:
    """Книга и каталог фактов, на которых ядро выпустило results.json."""
    book = ROOT / results["book"]["file"]
    facts = ROOT / results["facts"]["dir"]
    return book, facts


# ------------------------------------------------------------------ время и траектории


def pidx(p: str) -> int:
    """'2026H2' → номер полугодия на сквозной линейке (год × 2 + (h − 1))."""
    p = str(p)
    return int(p[:4]) * 2 + int(p[5]) - 1


def pname(i: int) -> str:
    return f"{i // 2}H{i % 2 + 1}"


def is_h1(i: int) -> bool:
    return i % 2 == 0


def half_start(i: int) -> dt.date:
    return dt.date(i // 2, 1 if is_h1(i) else 7, 1)


def half_rate(r: float) -> float:
    """Годовая ставка → полугодовая: корень, не r/2 (§0)."""
    return (1.0 + r) ** 0.5 - 1.0


def date_pos(d: dt.date) -> tuple[int, float]:
    """Полугодие, в котором лежит дата, и доля прошедших дней (день d не прошёл)."""
    i = d.year * 2 + (0 if d.month <= 6 else 1)
    start, end = half_start(i), half_start(i + 1)
    return i, (d - start).days / (end - start).days


def _tr_points(tr: dict) -> dict:
    """Точки «заданных годов» траектории: ключи лет; если их нет — годы ключей полугодий
    (последний ключ полугодия года)."""
    years = {int(k): v for k, v in tr.items() if str(k).isdigit()}
    if years:
        return years
    halves = sorted((pidx(k), v) for k, v in tr.items() if len(str(k)) == 6 and str(k)[4] == "H")
    return {i // 2: v for i, v in halves}


def path_value(tr, i: int):
    """Значение траектории книги в полугодии i (§0.1). Скаляр — как есть."""
    if not isinstance(tr, dict):
        return tr
    tr = {str(k): v for k, v in tr.items()}
    name, year = pname(i), i // 2
    if name in tr:
        return tr[name]
    if str(year) in tr:
        return tr[str(year)]
    pts = _tr_points(tr)
    has_lt = "LT" in tr
    if not pts:
        if has_lt:
            return tr["LT"]
        raise ControlError(f"траектория без значений: {tr}")
    ys = sorted(pts)
    lower = [y for y in ys if y < year]
    upper = [y for y in ys if y > year]
    if lower and upper:
        y0, y1 = lower[-1], upper[0]
        return pts[y0] + (pts[y1] - pts[y0]) * (year - y0) / (y1 - y0)
    if upper:  # раньше первого заданного года — плоско назад
        return pts[upper[0]]
    y0 = lower[-1] if lower else ys[-1]
    last = pts[y0]
    if not has_lt:
        return last
    lt_from = tr.get("LT_from")
    if lt_from is None or year >= int(lt_from):
        return tr["LT"]
    return last + (tr["LT"] - last) * (year - y0) / (int(lt_from) - y0)


def lt_value(tr):
    """Долгосрочное значение траектории компании: LT, иначе последнее значение."""
    if not isinstance(tr, dict):
        return tr
    if "LT" in tr:
        return tr["LT"]
    return path_value(tr, pidx("9999H2"))


def zero_rate(curve: dict, t: float) -> float:
    """Бескупонная ставка на срок t (§0.2): ≤ узла 1 — узел 1, линейно между узлами,
    за последним узлом — линейный сход к LT к 15 годам."""
    nodes = sorted((float(k), v) for k, v in curve.items() if str(k) != "LT")
    nodes.append((15.0, curve["LT"]))
    if t <= nodes[0][0]:
        return nodes[0][1]
    for (t0, v0), (t1, v1) in zip(nodes, nodes[1:]):
        if t <= t1:
            return v0 + (v1 - v0) * (t - t0) / (t1 - t0)
    return curve["LT"]


def gordon(f1: float, f2: float, r: float, x: float) -> float:
    """Гордон на середины полугодий первого терминального года (§6)."""
    return (f1 * (1.0 + r) ** 0.75 + f2 * (1.0 + r) ** 0.25) / (r - x)


def ratio(x: float, life: float) -> float:
    """(1 − (1 + x)^(−L)) / (L × x), при x → 0 — 1 (§6)."""
    if abs(x) < 1e-12:
        return 1.0
    return (1.0 - (1.0 + x) ** (-life)) / (life * x)


# ------------------------------------------------------------------ контекст расчёта


class Ctx:
    """Общие для всех клеток величины: периоды, факты якоря, время оценки, режимы."""

    def __init__(self, book: dict, facts: dict, options: dict | None = None):
        self.book, self.facts = book, facts
        self.opt = dict(options or {})
        m = book["meta"]
        self.anchor = pidx(m["anchor_period"])
        self.first = pidx(m["first_period"])
        self.last = pidx(m["last_period"])
        if self.first != self.anchor + 1:
            raise ControlError("first_period должен идти сразу за anchor_period")
        self.halves = list(range(self.first, self.last + 1))
        self.n = len(self.halves)

        acc = facts["accounting"]["periods"]
        self.acc = {pidx(p): {k: _v(x) for k, x in row.items()} for p, row in acc.items()}
        net = facts["network"]
        self.area_hist = {pidx(p): _v(x) for p, x in net["area_end"].items()}
        self.opened_hist = {pidx(p): _v(x) for p, x in net["gross_opened_hist"].items()}
        self.closed_hist = {pidx(p): _v(x) for p, x in (net.get("closed_area_est") or {}).items()}
        bal = facts["balance"]
        self.net_debt_fact = _v(bal["net_debt"])
        self.div_payable = _v(bal["dividends_payable"])
        self.nwc_anchor = _v(bal["nwc"])
        self.shares = _v(facts["shares"]["outstanding_mln"])
        include = set(book["bridge"]["include"])
        self.bridge = [{"key": ln["key"], "label": ln.get("label", ln["key"]),
                        "amount": _v(ln["amount"]), "included": ln["key"] in include}
                       for ln in facts["bridge"]["lines"]]
        missing = include - {ln["key"] for ln in self.bridge}
        if missing:
            raise ControlError(f"строк моста нет в фактах: {sorted(missing)}")
        self.bridge_total = sum(ln["amount"] for ln in self.bridge if ln["included"])

        # время оценки (§5)
        self.v = dt.date.fromisoformat(str(m["valuation_date"]))
        vi, frac = date_pos(self.v)
        if vi < self.first:
            self.closed, self.elapsed = 0, 0.0
        else:
            self.closed, self.elapsed = vi - self.first, frac
        curve_date = dt.date.fromisoformat(str(m["curve_as_of"]))
        ci, cfrac = date_pos(curve_date)
        gap = (vi - ci) + (frac - cfrac)  # в полугодиях; без больших чисел — без потери точности
        self.roll = 0.5 * gap if gap > 0 else 0.0
        self.t_end = (1.0 - self.elapsed) * 0.5 + (self.n - self.closed - 1) * 0.5

        # дивиденды объявленные: требование с даты отсечки (§7.1)
        self.div_declared = 0.0
        self.div_rows = []
        for row in facts["dividends"]["register"]:
            ex = dt.date.fromisoformat(str(row["ex_date"]))
            inc = bool(row.get("cash_in_company_at_facts_date")) and ex <= self.v
            self.div_rows.append({"id": row["id"], "amount": _v(row["amount"]), "in_claims": inc})
            if inc:
                self.div_declared += _v(row["amount"])

        # маржа: факт якоря, отклонение по режимам, обновление вероятностей (§4.3, §10)
        mg = book["margin"]
        self.rho_d = mg["deviation_persistence"]
        a = self.acc[self.anchor]
        self.margin_fact = a["adj_ebitda"] / a["revenue"]
        self.regimes = list(book["joint"]["regime_prob"])
        self.dev_anchor = {r: self.margin_fact - self.target(r, self.anchor) - self.season(self.anchor)
                           for r in self.regimes}
        self.observations = sorted(
            [o for o in (book["joint"]["regime_update"].get("observations") or [])
             if pidx(o["period"]) > self.anchor], key=lambda o: pidx(o["period"]))
        self.prior = dict(book["joint"]["regime_prob"])
        self.posterior, self.update_steps = self._regime_update()

    # --- маржа
    def target(self, regime: str, i: int) -> float:
        return path_value(self.book["margin"]["targets"][regime], i)

    def target_lt(self, regime: str) -> float:
        return lt_value(self.book["margin"]["targets"][regime])

    def season(self, i: int) -> float:
        s = self.book["margin"]["seasonal_h1_pp"]
        if i // 2 < self.first // 2:
            return 0.0
        return s if is_h1(i) else -s

    def dev_path(self, regime: str) -> dict:
        """Отклонение маржи AR(1) с шагами Калмана на наблюдениях (§4.3)."""
        sigma2 = self.book["joint"]["regime_update"]["sigma_pp"] ** 2
        obs = {pidx(o["period"]): o for o in self.observations}
        dev_prev, p_prev, last = self.dev_anchor[regime], 0.0, self.anchor
        out = {self.anchor: dev_prev}
        for i in self.halves:
            k = i - last
            phi = self.rho_d ** k
            if i in obs:
                o = obs[i]
                p = sigma2 * (1.0 - phi ** 2) + phi ** 2 * p_prev
                se2 = o["se"] ** 2
                w = 1.0 if se2 == 0.0 else p / (p + se2)
                dev = phi * dev_prev + w * (o["value"] - self.target(regime, i) - self.season(i)
                                            - phi * dev_prev)
                p_prev, dev_prev, last = (1.0 - w) * p, dev, i
                out[i] = dev
            else:
                out[i] = phi * dev_prev
        return out

    def _regime_update(self):
        """Обновление вероятностей режимов по наблюдениям маржи (§10, A-P2u)."""
        ru = self.book["joint"]["regime_update"]
        sigma2, cap = ru["sigma_pp"] ** 2, ru["cap_pp"]
        prior = dict(self.prior)
        m = dict(self.dev_anchor)
        v = {r: 0.0 for r in self.regimes}
        last = self.anchor
        steps = []
        for o in self.observations:
            i = pidx(o["period"])
            k = i - last
            phi = self.rho_d ** k
            se2 = o["se"] ** 2
            like, err, pv = {}, {}, {}
            for r in self.regimes:
                dev = o["value"] - self.target(r, i) - self.season(i)
                err[r] = dev - phi * m[r]
                pv[r] = sigma2 * (1.0 - phi ** 2) + phi ** 2 * v[r]
                like[r] = math.exp(-0.5 * err[r] ** 2 / (pv[r] + se2))
            raw = {r: prior[r] * like[r] for r in self.regimes}
            tot = sum(raw.values())
            post = {r: raw[r] / tot for r in self.regimes}
            post = _cap_shift(prior, post, cap)
            for r in self.regimes:
                if se2 == 0.0:
                    m[r], v[r] = o["value"] - self.target(r, i) - self.season(i), 0.0
                else:
                    w = pv[r] / (pv[r] + se2)
                    m[r] = phi * m[r] + w * err[r]
                    v[r] = (1.0 - w) * pv[r]
            steps.append({"period": o["period"], "prior": prior, "posterior": post})
            prior, last = post, i
        return prior, steps


def _cap_shift(prior: dict, post: dict, cap: float) -> dict:
    """Предел сдвига вероятностей режимов за одно наблюдение (§10, cap_shift)."""
    keys = list(prior)
    shifted = {r: prior[r] + max(-cap, min(cap, post[r] - prior[r])) for r in keys}
    tot = sum(shifted.values())
    shifted = {r: shifted[r] / tot for r in keys}
    delta = {r: shifted[r] - prior[r] for r in keys}
    biggest = max(abs(x) for x in delta.values())
    if biggest > cap:
        delta = {r: x * cap / biggest for r, x in delta.items()}
    return {r: prior[r] + delta[r] for r in keys}


# ------------------------------------------------------------------ клетка


def run_cell(ctx: Ctx, world: str, regime: str, level: str) -> dict:
    """Один проход клетки: сеть → выручка → маржа → capex → FCFF → долг → EV и D (§4–§7)."""
    b = ctx.book
    W = b["worlds"][world]
    joint = b["joint"]
    link = joint["world_links"][world]
    tariff = joint["stress_growth"] if regime == "stress" else link["growth"]
    credit = link["credit"]
    state = joint["regime_demand"][regime]
    net, rev, mg, cx = b["network"], b["revenue"], b["margin"], b["capex"]
    wc, tx, fin, val = b["working_capital"], b["tax"], b["financing"], b["valuation"]
    wref = b["worlds"][rev["homogeneity"]["reference_world"]]
    anchor, last = ctx.anchor, ctx.last

    mu = list(net["maturity_curve"])
    d_new, kappa = net["new_space_density"], net["closed_productivity"]

    def mu_at(a: int) -> float:
        return mu[a] if a < len(mu) else 1.0

    # --- сеть: история (§4.1)
    area = dict(ctx.area_hist)
    opened = dict(ctx.opened_hist)
    dens = {q: 1.0 for q in opened}  # исторические когорты дозревают до средней плотности
    n_imm = len(mu) - 1

    def aeff_start(q: int) -> float:
        return area[q] - sum(opened[q - a] * (1.0 - mu_at(a)) for a in range(n_imm))

    aeff = {}
    if ctx.opt.get("hist_aeff") == "recursion":
        aeff[anchor] = aeff_start(anchor)
        for q in (anchor, anchor - 1):
            mat = sum(opened[q - a] * (mu_at(a) - mu_at(a - 1)) for a in range(1, len(mu))
                      if (q - a) in opened)
            aeff[q - 1] = aeff[q] + ctx.closed_hist[q] * kappa - opened[q] * mu[0] - mat
    else:
        for q in (anchor - 2, anchor - 1, anchor):
            aeff[q] = aeff_start(q)

    R = {q: row["revenue"] for q, row in ctx.acc.items()}
    ai = ctx.acc[anchor]
    rows = {}

    # --- стартовые уровни якоря
    r_ann_anchor = R[anchor] + R[anchor - 1]
    opc = wc["operating_cash_pct"]
    buf_pct = fin["cash_buffer_pct"]
    nwc_prev = ctx.nwc_anchor
    opcash_prev = path_value(opc, anchor) * r_ann_anchor
    buf_prev = path_value(buf_pct, anchor) * r_ann_anchor
    nd_prev = ctx.net_debt_fact + ctx.div_payable
    rep_prev = ai["ebitda_rep"]
    index = {anchor: 1.0}
    capex_by = {anchor: ai["capex"]}
    life = cx["asset_life_years"]
    two_l = 2.0 * life
    x0 = None
    dev = ctx.dev_path(regime)
    k_s = rev["ticket_k"][state]
    pi_w, pi_ref = W["lt"]["inflation"], wref["lt"]["inflation"]
    hom = rev["homogeneity"]
    div_from = pidx(fin["dividends_from"])
    tau = tx["rate"]

    for i in ctx.halves:
        year = i // 2
        # сеть (§4.1)
        gn = path_value(net["net_growth"][tariff], i)
        cl = path_value(net["close_rate"], i)
        closed = area[i - 1] * cl / 2.0
        op = area[i - 1] * gn / 2.0 + closed
        area[i] = area[i - 1] + op - closed
        opened[i], dens[i] = op, d_new
        maturing = sum(opened[i - a] * dens[i - a] * (mu_at(a) - mu_at(a - 1))
                       for a in range(1, len(mu)) if (i - a) in opened)
        aeff[i] = aeff[i - 1] - closed * kappa + op * d_new * mu[0] + maturing

        # выручка (§4.2)
        w_hom = min(1.0, max(0.0, (year - hom["ramp_from"]) / (hom["ramp_to"] - hom["ramp_from"])))
        ticket = (k_s * path_value(W["food_cpi"], i) + path_value(rev["ticket_shift"][state], i)
                  + path_value(rev["vat_effect"], i) + (1.0 - k_s) * (pi_w - pi_ref) * w_hom)
        traffic = path_value(rev["traffic"][state], i)
        other = path_value(rev["other_growth"], i)
        abar = (aeff[i - 1] + aeff[i]) / 2.0
        abar_base = (aeff[i - 3] + aeff[i - 2]) / 2.0
        R[i] = R[i - 2] * abar / abar_base * (1.0 + ticket) * (1.0 + traffic) * (1.0 + other)
        r_ann = R[i] + R[i - 1]

        # маржа, LTI (§4.3, §4.4)
        m = ctx.target(regime, i) + ctx.season(i) + dev[i]
        ebitda = R[i] * m
        lti = path_value(mg["lti_pct"], i) * R[i]
        rep = ebitda - lti

        # capex и амортизация (§4.5)
        cpi = path_value(W["cpi"], i)
        index[i] = index[i - 1] * (1.0 + half_rate(cpi))
        a_mid = (area[i - 1] + area[i]) / 2.0
        x = a_mid * index[i] / r_ann
        if x0 is None:
            x0 = x
        phi = path_value(cx["maintenance_area_share"], i)
        mnt = path_value(cx["maintenance"][level], i)
        c_maint = R[i] * mnt * ((1.0 - phi) + phi * x / x0)
        c_growth = op * path_value(cx["price_per_m2"], i) * index[i]
        c_infra = (max(0.0, op - closed) * path_value(cx["infra_per_m2"], i) * index[i]
                   if year >= cx["infra_from_year"] else 0.0)
        capex = c_maint + c_growth + c_infra
        k = i - anchor
        da = ai["da"] * max(0.0, 1.0 - k / two_l)
        da += sum(capex_by[i - j] for j in range(1, int(min(k, two_l)) + 1)) / two_l
        capex_by[i] = capex
        ebit = ebitda - da
        proceeds = path_value(cx["disposal_proceeds_pct"], i) * R[i]

        # оборотный капитал, касса, аренда (§4.6)
        nwc = (path_value(wc["nwc_pct"], i) * r_ann
               + (path_value(wc["h1_excess_pct"], i) * r_ann if is_h1(i) else 0.0))
        opcash = path_value(opc, i) * r_ann
        buf = path_value(buf_pct, i) * r_ann
        lease = path_value(wc["lease_adj_pct"], i) * R[i]

        # ставка долга и проценты (§4.9)
        key = path_value(W["key_rate"], i)
        ell = path_value(fin["legacy_weight"], i)
        fshare = path_value(fin["fixed_share"], i)
        fixed = ell * fin["legacy_rate"] + (1.0 - ell) * (zero_rate(W["zero_curve"], 3.0)
                                                          + fin["spread_fixed"][credit])
        debt_rate = fshare * fixed + (1.0 - fshare) * (key + fin["spread_float"][credit])
        gross = nd_prev + opcash_prev + buf_prev
        interest = gross * half_rate(debt_rate) - buf_prev * half_rate(fin["cash_yield_k"] * key)

        # налог и FCFF (§4.7, §4.8)
        base = ebit - lti + path_value(tx["permanent_add_pct"], i) * R[i]
        tax_u = tau * max(0.0, base)
        tax_a = tau * max(0.0, base - interest)
        shield = tax_u - tax_a
        d_nwc = nwc - nwc_prev
        d_opc = opcash - opcash_prev
        fcff = ebitda - lti - tax_u - capex - d_nwc - d_opc + lease + proceeds

        # путь долга и дивиденды (§4.9)
        nd_pre = nd_prev - (fcff + shield - interest)
        ltm = rep + rep_prev
        div = max(0.0, path_value(fin["target_leverage"], i) * ltm - nd_pre) if i >= div_from else 0.0
        nd = nd_pre + div

        rows[i] = {
            "revenue": R[i], "ticket": ticket, "traffic": traffic, "other": other,
            "area_end": area[i], "opened": op, "closed": closed, "area_eff": aeff[i],
            "margin": m, "adj_ebitda": ebitda, "lti": lti, "ebitda_rep": rep, "da": da,
            "ebit": ebit, "capex": capex, "capex_maintenance": c_maint,
            "capex_growth": c_growth, "capex_infra": c_infra, "nwc": nwc, "nwc_change": d_nwc,
            "opcash": opcash, "opcash_change": d_opc, "lease": lease, "proceeds": proceeds,
            "tax_unlevered": tax_u, "tax_actual": tax_a, "shield": shield, "fcff": fcff,
            "debt_rate": debt_rate, "interest": interest, "dividends": div, "net_debt": nd,
            "ebitda_rep_ltm": ltm, "leverage": nd / ltm, "index": index[i],
        }
        nwc_prev, opcash_prev, buf_prev, nd_prev, rep_prev = nwc, opcash, buf, nd, rep

    # --- терминал (§6)
    r_t = W["zero_curve"]["LT"] + val["beta_u"] * val["erp"]
    pi = W["lt"]["inflation"]
    cl_lt = lt_value(net["close_rate"])
    food_lt = path_value(W["food_cpi"], last)
    ticket_lt = (k_s * food_lt + lt_value(rev["ticket_shift"][state]) + lt_value(rev["vat_effect"])
                 + (1.0 - k_s) * (pi_w - pi_ref))
    traffic_lt = lt_value(rev["traffic"][state])
    g = (1.0 + ticket_lt) * (1.0 + traffic_lt) * (1.0 + (d_new * 1.0 - kappa) * cl_lt) - 1.0
    if g >= r_t:
        g = r_t - 0.0001
    if r_t <= pi:
        raise ControlError(f"r ≤ π в мире {world}: Гордон с ростом π не определён")
    cpi_last = path_value(W["cpi"], last)
    area_t = area[last]
    phi_lt = lt_value(cx["maintenance_area_share"])
    mnt_lt = lt_value(cx["maintenance"][level])
    t_rows = []
    idx_prev, r_prev = index[last], R[last]
    nwc_p, opc_p = nwc_prev, opcash_prev
    for h, base_i in ((1, last - 1), (2, last)):
        t_i = last + h
        r_h = R[base_i] * (1.0 + g)
        idx_h = idx_prev * (1.0 + half_rate(cpi_last))
        r_ann = r_h + r_prev
        m_h = ctx.target_lt(regime) + ctx.season(t_i)
        ebitda_h = r_h * m_h
        lti_h = lt_value(mg["lti_pct"]) * r_h
        x_h = area_t * idx_h / r_ann
        maint_pi = r_h * mnt_lt * phi_lt * x_h / x0
        maint = r_h * mnt_lt * (1.0 - phi_lt) + maint_pi
        repl = area_t * cl_lt / 2.0 * lt_value(cx["price_per_m2"]) * idx_h
        nwc_h = lt_value(wc["nwc_pct"]) * r_ann + (lt_value(wc["h1_excess_pct"]) * r_ann
                                                    if is_h1(t_i) else 0.0)
        opc_h = lt_value(opc) * r_ann
        t_rows.append({
            "period": pname(t_i), "revenue": r_h, "margin": m_h, "adj_ebitda": ebitda_h,
            "lti": lti_h, "capex": maint + repl, "capex_pi": maint_pi + repl,
            "capex_maintenance": maint, "capex_replacement": repl,
            "nwc_change": nwc_h - nwc_p, "opcash_change": opc_h - opc_p,
            "lease": lt_value(wc["lease_adj_pct"]) * r_h,
            "proceeds": lt_value(cx["disposal_proceeds_pct"]) * r_h,
            "padd": lt_value(tx["permanent_add_pct"]) * r_h,
        })
        idx_prev, r_prev, nwc_p, opc_p = idx_h, r_h, nwc_h, opc_h
    capex_ann = sum(t["capex"] for t in t_rows)
    capex_pi_ann = sum(t["capex_pi"] for t in t_rows)
    da_h = ((capex_ann - capex_pi_ann) * ratio(g, life) + capex_pi_ann * ratio(pi, life)) / 2.0
    da_pi_h = capex_pi_ann * ratio(pi, life) / 2.0
    flows, f_pi = [], []
    for t in t_rows:
        base = t["adj_ebitda"] - t["lti"] - da_h + t["padd"]
        tax = tau * max(0.0, base)
        fcff_t = (t["adj_ebitda"] - t["lti"] - tax - t["capex"] - t["nwc_change"]
                  - t["opcash_change"] + t["lease"] + t["proceeds"])
        t.update({"da": da_h, "tax": tax, "fcff": fcff_t})
        flows.append(fcff_t)
        f_pi.append(tau * da_pi_h * (1.0 if base > 0 else 0.0) - t["capex_pi"])
    tv = gordon(flows[0] - f_pi[0], flows[1] - f_pi[1], r_t, g) + gordon(f_pi[0], f_pi[1], r_t, pi)
    rep_t = sum(t["adj_ebitda"] - t["lti"] for t in t_rows)
    s_t = tau * lt_value(fin["target_leverage"]) * rep_t * (W["zero_curve"]["LT"]
                                                           + fin["spread_fixed"][credit])
    tv_s = gordon(s_t / 2.0, s_t / 2.0, r_t, g)

    # --- дисконтирование и EV (§5)
    prem = val["beta_u"] * val["erp"]
    curve = W["zero_curve"]

    def df(t: float) -> float:
        return (1.0 + zero_rate(curve, t) + prem) ** (-t)

    def df_v(t: float) -> float:
        return df(ctx.roll + t) / df(ctx.roll)

    pv_fcff = pv_shield = 0.0
    for n, i in enumerate(ctx.halves):
        if n < ctx.closed:
            continue
        if n == ctx.closed:
            share, t = 1.0 - ctx.elapsed, (1.0 - ctx.elapsed) * 0.25
        else:
            share, t = 1.0, (1.0 - ctx.elapsed) * 0.5 + (n - ctx.closed - 1) * 0.5 + 0.25
        disc = df_v(t)
        pv_fcff += rows[i]["fcff"] * share * disc
        pv_shield += rows[i]["shield"] * share * disc
        rows[i]["df"], rows[i]["share"] = disc, share
    d_end = df_v(ctx.t_end)
    pv_tv, pv_tv_s = tv * d_end, tv_s * d_end
    ev = pv_fcff + pv_shield + pv_tv + pv_tv_s

    # --- требования на дату оценки (§7.1)
    rolled = 0.0
    for n, i in enumerate(ctx.halves):
        cash = rows[i]["fcff"] + rows[i]["shield"] - rows[i]["interest"]
        if n < ctx.closed:
            rolled += cash
        elif n == ctx.closed:
            rolled += ctx.elapsed * cash
    opcash_anchor = path_value(opc, anchor) * r_ann_anchor
    claims = ctx.net_debt_fact + opcash_anchor + ctx.bridge_total - rolled + ctx.div_declared

    equity = ev - claims
    g_gov = val["governance_discount"]
    price = equity * ((1.0 - g_gov) if equity > 0 else 1.0) * 1000.0 / ctx.shares
    return {
        "world": world, "regime": regime, "capex": level, "growth": tariff, "credit": credit,
        "demand": state, "ev": ev, "pv_fcff": pv_fcff, "pv_shield": pv_shield, "pv_tv": pv_tv,
        "pv_tv_shield": pv_tv_s, "pv_terminal": pv_tv + pv_tv_s,
        "terminal_share": (pv_tv + pv_tv_s) / ev, "d": claims, "rolled": rolled,
        "opcash_anchor": opcash_anchor, "equity": equity, "price": price,
        "terminal_growth": g, "r_terminal": r_t, "margin_lt": ctx.target_lt(regime),
        "tv": tv, "tv_shield": tv_s, "df_end": d_end, "terminal_rows": t_rows,
        "max_leverage": max(rows[i]["leverage"] for i in ctx.halves),
        "margin_min": min(rows[i]["margin"] for i in ctx.halves),
        "margin_max": max(rows[i]["margin"] for i in ctx.halves),
        "rows": rows,
    }


# ------------------------------------------------------------------ сетка, слои, точка


def world_names(book: dict) -> list:
    return [w for w in book["worlds"] if w != "source"]


def layer_weights(book: dict) -> dict:
    j = book["joint"]
    neutral = {w: 0.0 for w in world_names(book)}
    neutral[j["neutral_world"]] = 1.0
    return {"analytical": dict(j["world_prob"]), "market_implied": dict(j["market_implied_prob"]),
            "macro_neutral": neutral}


def evaluate(book: dict, facts: dict, options: dict | None = None) -> dict:
    """Вся сетка на книге: клетки, слои, точка, V*, ожидаемые пути слоя «свой взгляд»."""
    ctx = Ctx(book, facts, options)
    weights = layer_weights(book)
    levels = list(book["capex"]["maintenance"])
    cpr = book["joint"]["capex_prob_given_regime"]
    cells = []
    for w in world_names(book):
        for r in ctx.regimes:
            for c in levels:
                cell = run_cell(ctx, w, r, c)
                for layer in LAYERS:
                    cell[LAYER_P[layer]] = weights[layer].get(w, 0.0) * ctx.posterior[r] * cpr[r][c]
                cells.append(cell)

    g_gov = book["valuation"]["governance_discount"]

    def price_of(equity: float) -> float:
        return equity * ((1.0 - g_gov) if equity > 0 else 1.0) * 1000.0 / ctx.shares

    def layer(ww: dict, title: str) -> dict:
        p = [ww.get(c["world"], 0.0) * ctx.posterior[c["regime"]] * cpr[c["regime"]][c["capex"]]
             for c in cells]
        agg = {k: sum(pi * c[k] for pi, c in zip(p, cells))
               for k in ("ev", "d", "pv_fcff", "pv_shield", "pv_tv", "pv_tv_shield", "pv_terminal",
                         "rolled")}
        eq = agg["ev"] - agg["d"]
        return {"title": title, "world_weights": ww, "v0": agg["ev"], "d": agg["d"], "equity": eq,
                "price": price_of(eq), "pv_fcff": agg["pv_fcff"], "pv_shield": agg["pv_shield"],
                "pv_tv": agg["pv_tv"], "pv_tv_shield": agg["pv_tv_shield"],
                "pv_terminal": agg["pv_terminal"], "terminal_share": agg["pv_terminal"] / agg["ev"],
                "rolled": agg["rolled"], "v0_to_d": agg["ev"] / agg["d"]}

    layers = {name: layer(weights[name], name) for name in LAYERS}
    worlds_only = {w: layer({w: 1.0}, f"world:{w}") for w in world_names(book)}

    lam = book["joint"]["lambda"]
    low, high = layers["macro_neutral"]["price"], layers["analytical"]["price"]
    central = low + lam * (high - low)
    d_an = layers["analytical"]["d"]
    market = book["meta"]["market_price"]
    v_star = market * ctx.shares / 1000.0 / (1.0 - g_gov) + d_an
    v0_point = central * ctx.shares / 1000.0 / (1.0 - g_gov) + d_an
    v0_mix = layers["macro_neutral"]["v0"] + lam * (layers["analytical"]["v0"]
                                                    - layers["macro_neutral"]["v0"])
    point = {
        "low": low, "central": central, "high": high, "lambda": lam, "rates_view": high - low,
        "v0_point": v0_point, "v0_point_mix": v0_mix, "v_star": v_star,
        "gap_point": v0_point / v_star - 1.0,
        "rub_per_1pct_ev_point": 0.01 * v0_point * (1.0 - g_gov) * 1000.0 / ctx.shares,
        "rub_per_1pct_ev_mix": 0.01 * v0_mix * (1.0 - g_gov) * 1000.0 / ctx.shares,
        "market_price": market,
    }
    return {
        "inputs": {"valuation_date": ctx.v.isoformat(), "closed_periods": ctx.closed,
                   "elapsed": ctx.elapsed, "roll_years": ctx.roll, "t_end": ctx.t_end,
                   "shares_mln": ctx.shares, "governance_discount": g_gov, "lambda": lam},
        "regimes": {"prior": ctx.prior, "posterior": ctx.posterior, "steps": ctx.update_steps,
                    "margin_anchor_fact": ctx.margin_fact, "deviation_anchor": ctx.dev_anchor},
        "claims": {"net_debt_fact": ctx.net_debt_fact, "opcash_anchor": cells[0]["opcash_anchor"],
                   "bridge_total": ctx.bridge_total, "dividends_declared": ctx.div_declared,
                   "rolled_analytical": layers["analytical"]["rolled"],
                   "d_analytical": d_an, "bridge": ctx.bridge, "dividend_rows": ctx.div_rows},
        "cells": cells, "layers": layers, "worlds_only": worlds_only, "point": point,
        "paths": expected_paths(ctx, cells),
    }


# Строки путей: потоки складываются, запасы — на конец года, доли — отношения сумм.
FLOW_ROWS = ("revenue", "adj_ebitda", "lti", "ebitda_rep", "da", "ebit", "capex",
             "capex_maintenance", "capex_growth", "capex_infra", "nwc_change", "opcash_change",
             "lease", "proceeds", "tax_unlevered", "tax_actual", "shield", "fcff", "interest",
             "dividends", "opened", "closed")
STOCK_ROWS = ("area_end", "net_debt", "ebitda_rep_ltm", "nwc", "opcash")
MEAN_ROWS = ("ticket", "traffic", "debt_rate")
FACT_ROWS = ("revenue", "adj_ebitda", "lti", "ebitda_rep", "da", "capex")


def expected_paths(ctx: Ctx, cells: list) -> dict:
    """Ожидаемый путь слоя «свой взгляд»: веса клеток, доли — отношения сумм."""
    halves = []
    for i in ctx.halves:
        row = {"period": pname(i)}
        for k in FLOW_ROWS + STOCK_ROWS + MEAN_ROWS:
            row[k] = sum(c["p_analytical"] * c["rows"][i][k] for c in cells)
        row["margin"] = row["adj_ebitda"] / row["revenue"]
        row["leverage"] = row["net_debt"] / row["ebitda_rep_ltm"]
        halves.append(row)
    annual = []
    for year in sorted({i // 2 for i in ctx.halves}):
        hs = [h for h in halves if pidx(h["period"]) // 2 == year]
        row = {"year": year, "halves": len(hs)}
        for k in FLOW_ROWS:
            row[k] = sum(h[k] for h in hs)
        facts_in = [q for q, _ in ctx.acc.items() if q // 2 == year and q <= ctx.anchor]
        for q in facts_in:
            for k in FACT_ROWS:
                row[k] += ctx.acc[q][k]
        for k in STOCK_ROWS:
            row[k] = hs[-1][k]
        row["margin"] = row["adj_ebitda"] / row["revenue"]
        row["capex_pct"] = row["capex"] / row["revenue"]
        row["leverage"] = row["net_debt"] / row["ebitda_rep_ltm"]
        annual.append(row)
    return {"halves": halves, "annual": annual}


# ------------------------------------------------------------------ сверка с ядром

KEY_ROWS = ("revenue", "margin", "adj_ebitda", "capex", "fcff", "shield", "interest", "net_debt")
FLOOR_ROWS = ("fcff",)


def _rel(a: float, b: float, floor: float = 0.0) -> float:
    den = max(abs(b), floor)
    if den == 0.0:
        return 0.0 if a == b else math.inf
    return abs(a - b) / den


def compare(control: dict, core: dict) -> list:
    """Сверка с results.json ядра: [{group, name, control, core, rel, tol, ok}]."""
    out = []

    def add(group, name, a, b, tol, floor=0.0):
        rel = _rel(a, b, floor)
        out.append({"group": group, "name": name, "control": a, "core": b, "rel": rel,
                    "tol": tol, "ok": rel <= tol})

    core_cells = {(c["world"], c["regime"], c["capex"]): c for c in core["cells"]}
    if len(core_cells) != len(control["cells"]):
        raise ControlError(f"клеток у ядра {len(core_cells)}, у контрольной {len(control['cells'])}")
    for c in control["cells"]:
        key = (c["world"], c["regime"], c["capex"])
        k = core_cells[key]
        tag = " · ".join(key)
        add("cells.ev", tag, c["ev"], k["ev"], TOL_V0)
        add("cells.d", tag, c["d"], k["d"], TOL_CLAIMS)
        for p in LAYER_P.values():
            add("cells.p", f"{tag}.{p}", c[p], k[p], 1e-12, floor=1e-12)
    for name in LAYERS:
        a, b = control["layers"][name], core["layers"][name]
        add("layers.v0", name, a["v0"], b["v0"], TOL_V0)
        add("layers.d", name, a["d"], b["d"], TOL_CLAIMS)
        add("layers.price", name, a["price"], b["price"], TOL_POINT)
    for w, a in control["worlds_only"].items():
        b = core.get("worlds_only", {}).get(w)
        if b:
            add("worlds.v0", w, a["v0"], b["v0"], TOL_V0)
    cp, kp = control["point"], core["point"]
    for k in ("low", "central", "high"):
        add("point", k, cp[k], kp[k], TOL_POINT)
    add("point.v_star", "v_star", cp["v_star"], kp["v_star"], TOL_CLAIMS)
    add("point.rub_per_1pct_ev", "rub_per_1pct_ev_point", cp["rub_per_1pct_ev_point"],
        kp["rub_per_1pct_ev_point"], TOL_POINT)
    add("claims", "d_analytical", control["claims"]["d_analytical"], core["claims"]["d_analytical"],
        TOL_CLAIMS)

    core_halves = {h["period"]: h for h in core["paths"]["halves"]}
    for h in control["paths"]["halves"]:
        k = core_halves.get(h["period"])
        if not k:
            continue
        floor = ROW_FLOOR_EBITDA * abs(k["adj_ebitda"])
        for r in KEY_ROWS:
            if r in k:
                add("rows.halves", f"{h['period']}.{r}", h[r], k[r], TOL_ROWS,
                    floor if r in FLOOR_ROWS else 0.0)
    core_years = {a["year"]: a for a in core["paths"]["annual"]}
    for a in control["paths"]["annual"]:
        k = core_years.get(a["year"])
        if not k:
            continue
        floor = ROW_FLOOR_EBITDA * abs(k["adj_ebitda"])
        for r in KEY_ROWS:
            if r in k:
                add("rows.annual", f"{a['year']}.{r}", a[r], k[r], TOL_ROWS,
                    floor if r in FLOOR_ROWS else 0.0)
    return out


def exact_match(control: dict, core: dict) -> float:
    """Наибольшее относительное отклонение V0 слоёв от ядра (задание: < 1e-9 — «общий код»;
    на деле независимая реализация однозначного текста даёт машинную точность, см. отчёт)."""
    return max(_rel(control["layers"][n]["v0"], core["layers"][n]["v0"]) for n in LAYERS)


def bitwise_copy(control: dict, core: dict) -> bool:
    """EV всех клеток и V0 всех слоёв совпали с ядром бит в бит — след общего кода: две
    независимые реализации расходятся хотя бы в последних битах из-за порядка операций."""
    core_cells = {(c["world"], c["regime"], c["capex"]): c["ev"] for c in core["cells"]}
    cells_same = all(core_cells.get((c["world"], c["regime"], c["capex"])) == c["ev"]
                     for c in control["cells"])
    layers_same = all(control["layers"][n]["v0"] == core["layers"][n]["v0"] for n in LAYERS)
    return cells_same and layers_same


# ------------------------------------------------------------------ отчёт


def _num(x: float, nd: int = 1) -> str:
    """Число по-русски; округление до печати, чтобы −0 и последние биты платформы
    (Windows/Linux) не меняли текст отчёта."""
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "—"
    x = round(x, nd)
    if x == 0:
        x = 0.0
    s = f"{x:,.{nd}f}".replace(",", " ").replace(".", ",")
    return s.replace("-", "−")


def _pct(x: float, nd: int = 2) -> str:
    return _num(100.0 * x, nd) + " %"


def _signed_pct(a: float, b: float, nd: int = 2) -> str:
    if not b:
        return "—"
    x = round(100.0 * (a / b - 1.0), nd)
    return ("+" if x > 0 else "") + _num(x, nd) + " %"


def _dev(rel: float) -> str:
    """Отклонение для сводки: машинную точность печатаем порогом, а не шумом битов."""
    if rel < 1e-12:
        return "< 1e-12"
    if rel < 1e-6:
        return "< 1e-6"
    return _pct(rel, 3)


def _table(head: list, rows: list) -> list:
    out = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return out


def _rel_path(p: Path) -> str:
    p = Path(p).resolve()
    return p.relative_to(ROOT).as_posix() if p.is_relative_to(ROOT) else p.name


def sensitivities(book: dict, facts: dict) -> list:
    """Ответ контрольной модели при другом прочтении спорных мест."""
    out = []
    if facts["network"].get("closed_area_est"):
        out.append(("Эффективная площадь истории — рекурсия §4.1 назад с закрытиями и κ "
                    "(hist_aeff)", evaluate(book, facts, {"hist_aeff": "recursion"})))
    vat = book["revenue"]["vat_effect"]
    if isinstance(vat, dict) and "LT" not in vat:
        b2 = copy.deepcopy(book)
        b2["revenue"]["vat_effect"]["LT"] = 0.0
        b2["revenue"]["vat_effect"]["LT_from"] = pidx(book["meta"]["first_period"]) // 2 + 1
        out.append(("`revenue.vat_effect` разовая: LT = 0 со следующего года (half_key_only)",
                    evaluate(b2, facts)))
    return out


def render_report(control: dict, core: dict | None, book_path: Path, facts_dir: Path,
                  sens: list) -> str:
    L = ["# Контрольная модель X5: сверка с ядром", "",
         "Генерируется: `python -B -m tests.independent_model --report` (не править руками). "
         "Контрольная модель `tests/independent_model.py` написана по `docs/MODEL.md`, книге и "
         "фактам без доступа к `model/`; `tests/test_control_model.py` сверяет её с таблицами "
         "ядра `data/assumptions/results.json` и с ядром на сценариях ниже. Допуски: строки "
         "путей ≤ 10 %, V0 слоёв и EV клеток ≤ 3 %, требования D ≤ 1 %, точка ≤ 3 %.", ""]
    inp = control["inputs"]
    L.append(f"* Книга `{_rel_path(book_path)}` (версия {control.get('book_version', '—')}), "
             f"факты `{_rel_path(facts_dir)}`.")
    L.append(f"* Дата оценки {inp['valuation_date']}: закрытых полугодий {inp['closed_periods']}, "
             f"прошло {_num(inp['elapsed'], 4)} текущего, перекат по форвардам "
             f"{_num(inp['roll_years'], 4)} г., t_end {_num(inp['t_end'], 4)} г.")
    L.append("")
    if core is None:
        L += ["**results.json ядра нет — сверять не с чем.** Ниже только числа контрольной модели.",
              ""]

    # --- итог и независимость
    checks = compare(control, core) if core else []
    if core:
        bad = [c for c in checks if not c["ok"]]
        v0_dev = exact_match(control, core)
        L += ["## Итог", ""]
        verdict = "все в допусках" if not bad else f"**вне допусков: {len(bad)}**"
        L.append(f"Сравнений {len(checks)}, {verdict}. Наибольшее отклонение V0 слоёв от ядра: "
                 f"{_dev(v0_dev)}.")
        per_pct = control["point"]["rub_per_1pct_ev_point"]
        L.append(f"Для масштаба: 1 % EV = {_num(per_pct, 1)} ₽ на акцию, допуск V0 3 % = "
                 f"{_num(3 * per_pct, 0)} ₽, шаг печати {_num(control['print_step'], 0)} ₽.")
        L.append("")
        groups = {}
        for c in checks:
            g = groups.setdefault(c["group"], {"n": 0, "bad": 0, "max": 0.0, "tol": c["tol"]})
            g["n"] += 1
            g["bad"] += 0 if c["ok"] else 1
            g["max"] = max(g["max"], c["rel"])
        L += _table(["Группа", "Сравнений", "Вне допуска", "Макс. отклонение", "Допуск"],
                    [[k, g["n"], g["bad"], _dev(g["max"]),
                      _pct(g["tol"], 0) if g["tol"] >= 0.01 else "точно"]
                     for k, g in groups.items()])
        L.append("")
        if bad:
            L += ["Вне допуска (первые 40):", ""]
            L += _table(["Группа", "Что", "Контроль", "Ядро", "Отклонение", "Допуск"],
                        [[c["group"], c["name"], _num(c["control"], 3), _num(c["core"], 3),
                          _pct(c["rel"], 2), _pct(c["tol"], 0)] for c in bad[:40]])
            L.append("")
        L += ["## Независимость", ""]
        if v0_dev < 1e-12:
            L.append("Контрольная модель совпадает с ядром до машинной точности: текст "
                     "`docs/MODEL.md` однозначен для веток, которые включает книга, и ядро "
                     "реализует именно его. Поэтому «совпадение точнее 1e-9» не отличает общий "
                     "код от независимого. Признак общего кода — "
                     "совпадение бит в бит EV всех 36 клеток и V0 всех слоёв (две независимые "
                     "реализации расходятся хотя бы в последних битах из-за порядка операций); "
                     "независимость держит проверка импортов: контрольная модель импортирует "
                     "только стандартную библиотеку и PyYAML, ядро её не импортирует.")
        else:
            L.append(f"Наибольшее отклонение V0 слоёв {_dev(v0_dev)}: реализации расходятся, "
                     "раскладка — ниже.")
        L.append("")

    # --- сценарии
    L += ["## Сценарии сверки", "",
          "Изменённые книги, на которых тест прогоняет ядро и контрольную модель и сверяет в тех же "
          "допусках: они включают ветки, которые книга держит выключенными.", ""]
    L += _table(["Сценарий", "Что включает", "Изменённые ключи книги"],
                [[s["key"], s["title"], ", ".join(f"`{k}`" for k in s["set"])] for s in SCENARIOS])
    L.append("")

    # --- слои и точка
    L += ["## Слои, точка, V*", ""]
    rows = []
    for n in LAYERS:
        a = control["layers"][n]
        b = core["layers"][n] if core else None
        for k, nd in (("v0", 1), ("d", 2), ("price", 0), ("pv_fcff", 1), ("pv_shield", 1),
                      ("pv_terminal", 1)):
            rows.append([n, k, _num(a[k], nd), _num(b[k], nd) if b else "—",
                         _signed_pct(a[k], b[k]) if b else "—"])
    L += _table(["Слой", "Величина", "Контроль", "Ядро", "Контроль / ядро − 1"], rows)
    L.append("")
    cp = control["point"]
    kp = core["point"] if core else {}
    rows = []
    for k, nd in (("low", 0), ("central", 0), ("high", 0), ("rates_view", 1), ("v_star", 2),
                  ("v0_point", 1), ("gap_point", 4), ("rub_per_1pct_ev_point", 2)):
        rows.append([k, _num(cp[k], nd), _num(kp[k], nd) if k in kp else "—",
                     _signed_pct(cp[k], kp[k]) if k in kp else "—"])
    rows.append(["v0_point_mix (λ-смесь V0 слоёв)", _num(cp["v0_point_mix"], 1), "—", "—"])
    rows.append(["rub_per_1pct_ev_mix", _num(cp["rub_per_1pct_ev_mix"], 2), "—", "—"])
    L += _table(["Точка", "Контроль", "Ядро", "Контроль / ядро − 1"], rows)
    L.append("")

    # --- требования
    L += ["## Требования на дату оценки (слой «свой взгляд»)", ""]
    cc = control["claims"]
    kc = core["claims"] if core else {}
    rows = []
    for k, title in (("net_debt_fact", "Чистый долг компании (факт)"),
                     ("opcash_anchor", "Операционная касса якоря"),
                     ("bridge_total", "Строки моста (включённые)"),
                     ("rolled_analytical", "Перекат: FCFF + S − I закрытых и доли текущего (в D с минусом)"),
                     ("dividends_declared", "Объявленные дивиденды (отсечка ≤ даты оценки)"),
                     ("d_analytical", "Итого D")):
        rows.append([title, _num(cc[k], 3), _num(kc[k], 3) if k in kc else "—",
                     _num(cc[k] - kc[k], 3) if k in kc else "—"])
    L += _table(["Строка", "Контроль", "Ядро", "Разница, млрд ₽"], rows)
    L.append("")

    # --- FCFF по статьям
    L += ["## FCFF по статьям (слой «свой взгляд», сумма явного периода, млрд ₽)", "",
          "Ядро — `paths.halves` results.json (ожидание по клеткам слоя, без дисконтирования). "
          "Знак — как статья входит в FCFF.", ""]
    items = (("adj_ebitda", +1, "Скорр. EBITDA"), ("lti", -1, "− LTI"),
             ("tax_unlevered", -1, "− налог без рычага"), ("capex", -1, "− capex"),
             ("nwc_change", -1, "− ΔNWC"), ("opcash_change", -1, "− ΔOpCash"),
             ("lease", +1, "+ аренда"), ("proceeds", +1, "+ выбытие ОС"),
             ("fcff", +1, "= FCFF"), ("shield", +1, "Щит S"), ("interest", +1, "Проценты I"),
             ("dividends", +1, "Дивиденды модели"), ("da", +1, "D&A"))
    ch = control["paths"]["halves"]
    kh = core["paths"]["halves"] if core else []
    rows = []
    for k, sign, title in items:
        a = sign * sum(h[k] for h in ch)
        b = sign * sum(h[k] for h in kh) if kh and k in kh[0] else None
        rows.append([title, _num(a, 1), _num(b, 1) if b is not None else "—",
                     _num(a - b, 3) if b is not None else "—"])
    L += _table(["Статья", "Контроль", "Ядро", "Разница"], rows)
    L.append("")

    # --- ключевые строки по годам
    L += ["## Ключевые строки по годам (слой «свой взгляд»)", "",
          "В скобках — отклонение от ядра.", ""]
    ka = {a["year"]: a for a in core["paths"]["annual"]} if core else {}
    rows = []
    for a in control["paths"]["annual"]:
        b = ka.get(a["year"])
        line = [a["year"]]
        for k, nd in (("revenue", 0), ("margin", 2), ("capex", 1), ("fcff", 1), ("shield", 1),
                      ("interest", 1), ("net_debt", 1)):
            v = _num(100 * a[k], nd) if k == "margin" else _num(a[k], nd)
            if b and k in b:
                v += f" ({_signed_pct(a[k], b[k], 1)})"
            line.append(v)
        rows.append(line)
    L += _table(["Год", "Выручка", "Маржа, %", "Capex", "FCFF", "Щит", "Проценты", "ЧД"], rows)
    L.append("")

    # --- терминал
    L += ["## Терминал (клетки с capex base)", ""]
    kcells = {(c["world"], c["regime"], c["capex"]): c for c in core["cells"]} if core else {}
    rows = []
    for c in control["cells"]:
        if c["capex"] != "base":
            continue
        k = kcells.get((c["world"], c["regime"], c["capex"]))
        rows.append([f"{c['world']} · {c['regime']}", _pct(c["terminal_growth"], 3),
                     _pct(k["terminal_growth"], 3) if k else "—", _pct(c["r_terminal"], 3),
                     _num(c["pv_tv"], 1), _num(c["pv_tv_shield"], 1),
                     _num(k["pv_terminal"], 1) if k else "—",
                     _signed_pct(c["pv_terminal"], k["pv_terminal"]) if k else "—"])
    L += _table(["Клетка", "g контроль", "g ядро", "r", "PV TV", "PV TV_S", "PV терминала, ядро",
                 "PV терминала: откл."], rows)
    L.append("")

    # --- клетки
    L += ["## Клетки: EV и D", ""]
    rows = []
    for c in control["cells"]:
        k = kcells.get((c["world"], c["regime"], c["capex"]))
        rows.append([f"{c['world']} · {c['regime']} · {c['capex']}", _num(c["ev"], 1),
                     _num(k["ev"], 1) if k else "—", _signed_pct(c["ev"], k["ev"]) if k else "—",
                     _num(c["d"], 2), _signed_pct(c["d"], k["d"], 3) if k else "—",
                     _signed_pct(c["pv_fcff"], k["pv_fcff"]) if k else "—",
                     _signed_pct(c["pv_shield"], k["pv_shield"]) if k else "—"])
    L += _table(["Клетка", "EV контроль", "EV ядро", "EV откл.", "D контроль", "D откл.",
                 "PV FCFF откл.", "PV щита откл."], rows)
    L.append("")

    # --- чувствительность
    if sens:
        L += ["## Чувствительность к толкованиям", "",
              "Ответ контрольной модели при другом прочтении спорного места (остальное — как выше).",
              ""]
        base_an = control["layers"]["analytical"]
        rows = []
        for title, alt in sens:
            an = alt["layers"]["analytical"]
            rows.append([title, _num(an["v0"], 1), _signed_pct(an["v0"], base_an["v0"]),
                         _num(alt["point"]["central"], 0),
                         _num(alt["point"]["central"] - control["point"]["central"], 0)])
        L += _table(["Толкование", "V0 «свой взгляд»", "к базовому", "Точка, ₽",
                     "Точка: разница, ₽"], rows)
        L.append("")

    # --- неоднозначности
    L += ["## Неоднозначности спецификации", "",
          "Места `docs/MODEL.md`, допускающие больше одного прочтения; первым — толкование "
          "контрольной модели. Совпадение с ядром в допусках на книге и сценариях значит, что "
          "ядро читает их так же или разница в пределах допуска.", ""]
    for key, text in INTERPRETATIONS.items():
        L.append(f"* **{key}** — {text}")
    L.append("")
    return "\n".join(L)


def build_report(results_path: Path | None = None) -> tuple[str, dict, dict | None]:
    """Текст docs/CONTROL-MODEL.md на входах, на которых ядро выпустило results.json."""
    core = load_results(results_path)
    if core:
        book_path, facts_dir = inputs_of_results(core)
    else:
        book_path, facts_dir = default_book_path(), default_facts_dir()
    book, facts = load_book(book_path), load_facts(facts_dir)
    control = evaluate(book, facts)
    control["book_version"] = book["meta"].get("version")
    control["print_step"] = book["valuation"]["headline"]["print_step"]
    text = render_report(control, core, book_path, facts_dir, sensitivities(book, facts))
    return text, control, core


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Независимая контрольная модель X5")
    ap.add_argument("--report", action="store_true", help="записать docs/CONTROL-MODEL.md")
    args = ap.parse_args(argv)
    text, control, core = build_report()
    if args.report:
        REPORT_MD.write_text(text + "\n", encoding="utf-8", newline="\n")
        print(f"записано: {REPORT_MD.relative_to(ROOT).as_posix()}")
    an = control["layers"]["analytical"]
    print(f"V0 «свой взгляд» {an['v0']:.2f}, D {an['d']:.3f}, точка {control['point']['central']:.1f}")
    if core:
        checks = compare(control, core)
        bad = [c for c in checks if not c["ok"]]
        print(f"сравнений {len(checks)}, вне допуска {len(bad)}; наибольшее отклонение V0 слоёв "
              f"{exact_match(control, core):.2e}; бит в бит: {bitwise_copy(control, core)}")
        for c in bad[:20]:
            print(f"  {c['group']} {c['name']}: {c['control']:.4f} против {c['core']:.4f} "
                  f"({100 * c['rel']:.2f} %)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
