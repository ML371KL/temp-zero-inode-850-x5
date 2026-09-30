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
import random
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
FACT_FILES = ("accounting", "network", "balance", "bridge", "shares", "dividends", "history")

LAYERS = ("analytical", "market_implied", "macro_neutral")
LAYER_P = {"analytical": "p_analytical", "market_implied": "p_market_implied",
           "macro_neutral": "p_neutral"}
# Кредитное состояние справедливого спреда (§4.9): имя состояния из текста методики.
FAIR_CREDIT = "base"

# Допуски сверки с ядром (docs/MODEL.md §16 и задание на контрольную модель).
TOL_ROWS = 0.10      # строки путей и слагаемые разложения EV
TOL_V0 = 0.03        # V0 слоёв и EV клеток
TOL_CLAIMS = 0.01    # требования D (слои и клетки), V*, выручка от пакета
TOL_POINT = 0.03     # точка низ/центр/верх, цена 1 % EV, доля капитала
# Для строк, проходящих через ноль (FCFF, дивиденды), знаменатель не меньше этой доли EBITDA периода.
ROW_FLOOR_EBITDA = 0.10

# Сценарии сверки с ядром на изменённых книгах: включают ветки, которые книга держит
# выключенными (наблюдения маржи, закрытые полугодия, сезонность, защита g ≥ r, чистая касса,
# короткий явный участок, …). Это входы проверки, не допущения модели:
# {"key", "title", "set": {путь книги: значение}}.
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
    {"key": "curve_after_date", "title": "Кривая позже даты оценки — без переката; форвард фикса от поздней даты кривой",
     "set": {"meta.curve_as_of": "2026-10-01", "worlds.source.curve_date": "2026-10-01"}},
    {"key": "season_wc_lease", "title": "Сезонность маржи, сезонный излишек NWC, аренда, прочая выручка",
     "set": {"margin.seasonal_h1_pp": 0.002, "working_capital.h1_excess_pct": 0.01,
             "working_capital.lease_adj_pct": 0.001,
             "revenue.other_growth": {"2027": 0.005, "LT": 0.0, "LT_from": 2030}}},
    {"key": "trajectory_rules", "title": "Траектории: интерполяция между годами, ключи полугодий, сход к LT",
     "set": {"capex.maintenance.base": {"2026H2": 0.028, "2027H1": 0.020, "2027H2": 0.030,
                                        "2029": 0.024, "LT": 0.025, "LT_from": 2032},
             "network.net_growth.mid": {"2026H2": 0.065, "2027": 0.058, "2028": 0.05, "2029": 0.045,
                                        "2030": 0.04, "LT": 0.0, "LT_from": 2034},
             "revenue.vat_effect": {"2026H2": -0.006, "LT": 0.0, "LT_from": 2027}}},
    {"key": "g_guard", "title": "Защита терминала g ≥ r ⇒ g = r − 0,0001 (мир N, режим full)",
     "set": {"revenue.ticket_shift.bull.LT": 0.12}},
    {"key": "negative_base", "title": "Отрицательная налоговая база стресса: max(0, ·) в явном участке, в терминале при g < π налога нет ни в одном полугодии",
     "set": {"margin.targets.stress.2030": 0.02, "margin.targets.stress.LT": 0.01}},
    {"key": "terminal_late_tax", "title": "Цель маржи full LT 2 %: g > π, база первого года терминала < 0 — налог хвоста только после смены знака в n_c (5–100 лет)",
     "set": {"margin.targets.full.LT": 0.02}},
    {"key": "dividends_late", "title": "Дивиденды модели с 2028H1 при целевом рычаге 2,5×",
     "set": {"financing.dividends_from": "2028H1", "financing.target_leverage": 2.5}},
    {"key": "net_cash", "title": "Дивиденды модели только с 2036H2: долг уходит в чистую кассу, G⁺ = 0 (нет вычетов C_iss и X), щит отрицателен",
     "set": {"financing.dividends_from": "2036H2"}},
    {"key": "stress_credit_costs", "title": "Кредитное состояние stress во всех мирах и издержки размещения ×5: вычеты X и C_iss во всех клетках",
     "set": {"joint.world_links.N.credit": "stress", "joint.world_links.H.credit": "stress",
             "financing.issuance_cost": 0.0045}},
    {"key": "short_horizon", "title": "Явный участок до 2029H2 (7 полугодий < 2L): база D&A якоря живёт в терминале — в явной части налога TV_tax",
     "set": {"meta.last_period": "2029H2"}},
    {"key": "lt_inflation_gap", "title": "Долгосрочная инфляция мира M ниже последнего ИПЦ траектории: индекс цен терминала — по π, а не по ИПЦ",
     "set": {"worlds.M.lt.inflation": 0.085}},
    {"key": "half_year_life", "title": "Срок службы 7,5 года (2L = 15, нечётное): в окне D&A терминала T1 на когорту второго полугодия больше, у T2 — первого; база якоря — 15 когорт",
     "set": {"capex.asset_life_years": 7.5}},
]

# Толкования неоднозначных мест MODEL.md (ключ → текст для отчёта).
INTERPRETATIONS = {
    "hist_start": (
        "§4.1, начало истории индекса S — «первое полугодие, на конец которого в фактах есть "
        "площадь»: самое раннее полугодие объединения `network.area_end` и `network.area_end_est` "
        "(сейчас 2022H2 из оценки листа). Если в одном полугодии есть и отчётная площадь, и оценка, "
        "берётся отчётная `area_end` (текст: «`area_end` или, для полугодий до 2023H2, оценка»); "
        "граница «до 2023H2» в коде не зашита — оценка нужна там, где нет отчётной площади. "
        "Площадь полугодий между S и якорем в индекс не входит — только открытия и закрытия."),
    "hist_capex": (
        "§4.5, capex когорт до якоря: и выручка R(q), и доля `capex_pct(q)` — из "
        "`facts.history.halves` (текст ссылается на него), даже там, где те же полугодия есть в "
        "`facts.accounting` (расходятся в третьем знаке, форма S(k)/S(0) от этого не меняется). "
        "В `capex_pct` истории — только ОС + НМА: «прочие платежи по инвестиционной деятельности», "
        "которые книга 1.1 считает капитальными и включает в уровень поддерживающего capex, в "
        "когортах до якоря не участвуют. Важна только форма выбывания базы: масштаб — факт D&A "
        "якоря."),
    "fix_forward_pos": (
        "§4.9, срок форварда s_p = max(0; 0,5 × (i_p − pos(curve_as_of))): i_p — номер полугодия "
        "от `first_period` с нуля, pos — номер полугодия даты кривой на той же линейке плюс доля "
        "прошедших дней по правилу §5 (день даты не прошёл). Ставка z_W(t) на сроки меньше года — "
        "узел 1 (§0.2), поэтому при s < 1 знаменатель — (1 + z_W(1))^s."),
    "fair_rate_ic": (
        "§4.9, справедливая ставка `debt_rate_fair` — та же формула со спредами `base` И с "
        "издержками размещения ic: только так «в состоянии base X = 0» (без ic разница ставок "
        "включала бы издержки размещения, и они вычитались бы из EV дважды — в C_iss и в X). "
        "С 28.09.2026 §4.9 говорит это прямо."),
    "pi_guard": (
        "§6, защита π ≥ r ⇒ π = r − 0,0001 — во всём терминале: индекс цен, S_π(a) в D&A, "
        "(1 + π)^n в налоге TV_tax и Гордон части π (так текст уточнён 28.09.2026; прежний "
        "называл только разделение Гордона). В книге π < r во всех мирах, на числа не влияет."),
    "g_guard_scope": (
        "§6, при g ≥ r ⇒ g = r − 0,0001 защищённый g — везде в терминале: выручка T1/T2, "
        "S_g(a) в D&A, (1 + g)^n в налоге TV_tax и щите, Гордон части g и вычетов."),
    "terminal_halves": (
        "§6, T1 и T2 — «первое и второе полугодия года после `last_period`»: для `last_period` во "
        "втором полугодии это два полугодия сразу за явным участком. `last_period` в первом "
        "полугодии текст не определяет (между ним и T1 было бы пропущенное полугодие) — контрольная "
        "модель такую книгу отвергает."),
    "terminal_tax_rows": (
        "§6, строки T1, T2 (налог, FCFF, щит строки) — с установившейся D&A; в TV налог и щит — "
        "суммой TV_tax по всем полугодиям, где первые 2L — с D&A по правилу когорт: доналоговый "
        "поток P_h = FCFF строки + её налог от D&A не зависит, поэтому TV от выбора D&A строки не "
        "зависит. Граничный год хвоста с base = 0 (n_c целое) налога не несёт — в сумму не входит."),
    "terminal_debt": (
        "§6, щит терминала: Lt, f_T, ℓ_T, ключевая, legacy_rate и ic — значения траекторий в "
        "`last_period`; OpCash_1 и Buf_1 — уровни модели на конец `last_period` (opc × R_ann, "
        "cash_buffer_pct × R_ann); EBITDA_rep(T1) = EBITDA(T1) − LTI(T1)."),
    "terminal_lt": (
        "§6 пишет значения траекторий компании в терминале через `LT` (target(ρ, LT), mnt_LT, "
        "cl_LT, «формула §4.2 на значениях LT»), а §0.1 запрещает ключ полугодия на последнем "
        "периоде явного участка, потому что «терминал прочёл бы его как LT». Второе имеет смысл, "
        "только если «LT» терминала — значение траектории в `last_period`: контрольная модель "
        "читает так (как и ядро; оно предупреждает, если траектория к `last_period` не дошла до "
        "`LT`). Буквальное чтение — ключ `LT` — даёт то же число, пока все траектории компании "
        "доходят до `LT` к последнему году явного участка (книга 1.1), и расходится на коротком "
        "горизонте — раздел «Чувствительность к толкованиям». Добавка однородности в g — полная "
        "(так написано), при любом чтении. С 28.09.2026 §6 называет «LT» терминала значением "
        "траектории в `last_period` прямо."),
    "lt_without_from": (
        "§0.1, `LT` без `LT_from`: LT действует сразу после последнего заданного года; траектория "
        "из одного LT — LT во всех полугодиях."),
    "before_first_key": (
        "§0.1, полугодие раньше первого заданного ключа траектории — значение первого заданного "
        "ключа (плоско назад). Ключи полугодий без ключей лет — «заданные точки» своих лет."),
    "rho_d": (
        "§4.3 и §10: ρ_d = `margin.deviation_persistence` — единственный ключ затухания."),
    "season_anchor": (
        "§4.3, сезонность «для полугодий года `first_period` и позже, включая якорь, если он в том "
        "же году»: при якоре 2026H1 season входит и в правило якоря. Сейчас `seasonal_h1_pp` = 0."),
    "terminal_other": (
        "§6, рост терминала g не содержит прочей выручки `other_growth` (формула g без множителя "
        "1 + other); выручка терминала — R × (1 + g). Сейчас other LT = 0."),
    "treasury_n": (
        "§7.2, n = `facts.shares.treasury` — уже вместе с акциями у дочерних обществ (так N + n = "
        "`issued`; строка `treasury_at_subsidiaries` — справочная часть n, к n не прибавляется). "
        "P_рынок в книжном расчёте — `meta.market_price`."),
    "v0_point": (
        "§7.3, V0 точки для «цены 1 % EV» и разрыва с V*: обращение цены точки той же функцией "
        "V0(цена) с D слоя «свой взгляд»; λ-смесь V0 слоёв даёт другое число, так как D слоёв "
        "различается перекатом (печатается справочно). Доля капитала в EV точки (§1) — "
        "(V0 − D) / V0, без выручки от пакета."),
    "band_rounding": (
        "§9 до уточнения 28.09.2026 не говорил, что квантили заголовка берутся по низу и верху "
        "прогонов, округлённым до 0,1 ₽ (это следовало из §11–§12: «низ и верх прогонов округлены "
        "до 0,1 ₽, как у заголовка»); теперь §9 и §11 (медианы подвыборки — без округления) говорят "
        "это прямо. Контрольная модель округляет в полной полосе; медиану подвыборки для δ "
        "(§11) считает без округления — там округление не названо (разница чтений — доли копейки: "
        "медиана первых 200 прогонов на книге 1.1 — 3 118,4500 с округлением и 3 118,4504 без)."),
    "band_axes": (
        "§9, оси: `value` — книжное значение первого пути → конец, одно значение на все пути оси "
        "(у осей книги путь один); `shift` — "
        "сдвиг всех значений траектории, кроме `LT_from`, если путь ведёт к траектории, и самого "
        "значения, если путь ведёт к скаляру или к одному ключу траектории "
        "(`network.net_growth.low.2027`, `worlds.M.lt.inflation`); `dict` — поэлементная "
        "интерполяция к словарю конца (ключа нет в конце — 0), затем нормировка. Цена ошибки "
        "суждения — точка при s = −1 и s = +1."),
    "next_report_open": (
        "§12, открытое полугодие — самое раннее прогнозное без наблюдения с se = 0; факт "
        "добавляется к уже внесённым наблюдениям книги."),
    "ev_ebitda_ntm": (
        "§13.2, «скорр. EBITDA текущего и следующего полугодий» — полугодие даты оценки (первое "
        "незакрытое прогнозное) и следующее за ним."),
    "max_leverage": (
        "§13.2, «max ND_комп/EBITDA_rep_LTM пути» — по явным полугодиям first…last (без "
        "терминала)."),
    "elapsed_clamp": (
        "§5, дата оценки раньше начала первого прогнозного полугодия (в якорном): elapsed = 0, "
        "закрытых нет, перекат — только если кривая раньше даты оценки."),
    "dividend_claims": (
        "§7.1, реестр дивидендов: требование — вся объявленная сумма записи с "
        "`cash_in_company_at_facts_date` и `ex_date` ≤ даты оценки; выплаченная после якоря доля "
        "(`paid_share_on`) не вычитается — эти деньги были в кассе якоря и ушли из компании. "
        "«Невостребованный остаток живёт в требованиях, пока не выплачен» — правило для следующего "
        "якоря (когда выплаченное уже вне кассы)."),
    "annual_2026": (
        "Годовые строки 2026 для сверки: выручка, скорр. и отчётная EBITDA, LTI, D&A, capex — факт "
        "якорного 1П + прогноз 2П; остальные строки года — только прогнозное 2П (так их публикует "
        "ядро: поле `fact` года в results.json)."),
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


def _is_half_key(k) -> bool:
    k = str(k)
    return len(k) == 6 and k[4] == "H" and k[:4].isdigit()


def scenario_book(book: dict, scenario: dict) -> dict:
    """Книга сценария: замены по путям; при смене `meta.last_period` траектории миров
    обрезаются по новому горизонту (§0.1: мир задан полугодиями до last_period включительно)."""
    out = apply_patch(book, scenario["set"])
    last = pidx(out["meta"]["last_period"])
    if last != pidx(book["meta"]["last_period"]):
        for w in [w for w in out["worlds"] if w != "source"]:
            for key, tr in out["worlds"][w].items():
                if isinstance(tr, dict) and any(_is_half_key(k) for k in tr):
                    out["worlds"][w][key] = {k: v for k, v in tr.items()
                                             if not (_is_half_key(k) and pidx(k) > last)}
    return out


def _v(x):
    """Значение факта: `{"v": …, "src": …}` → v; прочее — как есть."""
    if isinstance(x, dict) and "v" in x:
        return x["v"]
    return x


def load_facts(directory: Path | None = None) -> dict:
    directory = Path(directory) if directory else default_facts_dir()
    return {name: json.loads((directory / f"{name}.json").read_text(encoding="utf-8"))
            for name in FACT_FILES}


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
    за последним узлом T — форвард LT: (1 + z(t))^t = (1 + z(T))^T × (1 + LT)^(t − T)."""
    nodes = sorted((float(k), v) for k, v in curve.items() if str(k) != "LT")
    if t <= nodes[0][0]:
        return nodes[0][1]
    for (t0, v0), (t1, v1) in zip(nodes, nodes[1:]):
        if t <= t1:
            return v0 + (v1 - v0) * (t - t0) / (t1 - t0)
    t_n, z_n = nodes[-1]
    return ((1.0 + z_n) ** t_n * (1.0 + curve["LT"]) ** (t - t_n)) ** (1.0 / t) - 1.0


def forward_3y(curve: dict, s: float) -> float:
    """Трёхлетний форвард бескупонной кривой с момента s лет (§4.9, купон нового фикса)."""
    far = (1.0 + zero_rate(curve, s + 3.0)) ** (s + 3.0)
    near = (1.0 + zero_rate(curve, s)) ** s
    return (far / near) ** (1.0 / 3.0) - 1.0


def gordon(f1: float, f2: float, r: float, x: float) -> float:
    """Гордон на середины полугодий первого терминального года (§6)."""
    return (f1 * (1.0 + r) ** 0.75 + f2 * (1.0 + r) ** 0.25) / (r - x)


def disc_sum(x: float, n: int) -> float:
    """S_x(n) = Σ_{k=1..n} (1 + x)^(−k) = (1 − (1 + x)^(−n)) / x, при x → 0 — n (§6)."""
    if abs(x) < 1e-12:
        return float(n)
    return (1.0 - (1.0 + x) ** (-n)) / x


def geo_sum(q: float, n1: int, n2: int | None) -> float:
    """Σ_{n=n1}^{n2} q^n = (q^{n1} − q^{n2+1}) / (1 − q); n2 = None — до бесконечности (§6)."""
    if n2 is not None and n2 < n1:
        return 0.0
    total = q ** n1 / (1.0 - q)
    return total if n2 is None else total - q ** (n2 + 1) / (1.0 - q)


def tax_tail(a: float, b: float, g: float, pi: float, r: float, n0: int) -> float:
    """Хвост налога терминала полугодия (§6) без τ и множителя полугодия:
    Σ [a × q_g^n − b × q_π^n] по годам n ≥ n0, где база a(1 + g)^n − b(1 + π)^n > 0.
    a, b одного знака и g ≠ π — знак меняется в n_c = ln(b/a)/ln ρ, ρ = (1 + g)/(1 + π):
    при a, b > 0 база положительна после n_c, если g > π, и до n_c, если g < π (при a, b < 0 —
    наоборот); иначе знак базы постоянен — знак a − b."""
    rho = (1.0 + g) / (1.0 + pi)
    lo, hi = n0, None
    if ((a > 0.0 and b > 0.0) or (a < 0.0 and b < 0.0)) and rho != 1.0:
        n_c = math.log(b / a) / math.log(rho)
        if (a > 0.0) == (g > pi):
            lo = max(n0, math.floor(n_c) + 1)
        else:
            hi = math.ceil(n_c) - 1
    elif a - b <= 0.0:
        return 0.0
    q_g, q_pi = (1.0 + g) / (1.0 + r), (1.0 + pi) / (1.0 + r)
    return a * geo_sum(q_g, lo, hi) - b * geo_sum(q_pi, lo, hi)


def steady_da(c1: float, c2: float, x: float, life: float, two_l: int) -> list:
    """Установившаяся D&A T1, T2 части с ростом x (§6) — сумма 2L когорт окна полугодия:
    a = ⌊L⌋ когорт первых и вторых полугодий и при e = 2L − 2a = 1 ещё одна, самая старая
    (у T1 — второго полугодия года −(a + 1), у T2 — первого полугодия года −a)."""
    a = math.floor(life)
    e = two_l - 2 * a
    s = disc_sum(x, a)
    return [((c1 + c2) * s + e * c2 * (1.0 + x) ** (-(a + 1))) / two_l,
            ((c1 * (1.0 + x) + c2) * s + e * c1 * (1.0 + x) ** (-a)) / two_l]


# ------------------------------------------------------------------ контекст расчёта


class Ctx:
    """Общие для всех клеток величины: периоды, факты якоря, история сети и capex, время
    оценки, акции, режимы."""

    def __init__(self, book: dict, facts: dict, options: dict | None = None):
        self.book, self.facts = book, facts
        self.opt = dict(options or {})
        m = book["meta"]
        self.anchor = pidx(m["anchor_period"])
        self.first = pidx(m["first_period"])
        self.last = pidx(m["last_period"])
        if self.first != self.anchor + 1:
            raise ControlError("first_period должен идти сразу за anchor_period")
        if is_h1(self.last):
            raise ControlError("last_period в первом полугодии: §6 не определяет T1, T2")
        self.halves = list(range(self.first, self.last + 1))
        self.n = len(self.halves)

        acc = facts["accounting"]["periods"]
        self.acc = {pidx(p): {k: _v(x) for k, x in row.items()} for p, row in acc.items()}
        for q in (self.anchor - 1, self.anchor):
            if q not in self.acc:
                raise ControlError(f"нет фактов полугодия {pname(q)} в accounting")
        self._network_history()
        self._capex_history()

        bal = facts["balance"]
        self.net_debt_fact = _v(bal["net_debt"])
        self.div_payable = _v(bal["dividends_payable"])
        self.nwc_anchor = _v(bal["nwc"])

        # акции и казначейский пакет (§7.2)
        sh = facts["shares"]
        self.shares = _v(sh["outstanding_mln"])
        self.treasury = _v(sh["treasury"])
        self.issued = _v(sh.get("issued"))
        if self.shares is None or self.treasury is None:
            raise ControlError("нет акций в обращении или казначейских в facts.shares")
        self.all_shares = self.shares + self.treasury
        val = book["valuation"]
        self.g_gov = val["governance_discount"]
        self.market = m["market_price"]
        self.treasury_value = self.treasury * val["treasury_sale_price_k"] * self.market / 1000.0

        include = set(book["bridge"]["include"])
        self.bridge = [{"key": ln["key"], "label": ln.get("label", ln["key"]),
                        "amount": _v(ln["amount"]), "included": ln["key"] in include}
                       for ln in facts["bridge"]["lines"]]
        missing = include - {ln["key"] for ln in self.bridge}
        if missing:
            raise ControlError(f"строк моста нет в фактах: {sorted(missing)}")
        if any(ln["amount"] is None for ln in self.bridge if ln["included"]):
            raise ControlError("строка моста без числа (null) включена в мост")
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
        # положение даты кривой на линейке полугодий от first_period (§4.9)
        self.curve_pos = (ci - self.first) + cfrac

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

    # --- сеть: история индекса эффективной площади (§4.1)
    def _network_history(self):
        net_f, net_b = self.facts["network"], self.book["network"]
        area = {pidx(p): _v(x) for p, x in (net_f.get("area_end_est") or {}).items()}
        area.update({pidx(p): _v(x) for p, x in net_f["area_end"].items() if _v(x) is not None})
        area = {q: x for q, x in area.items() if x is not None}
        if self.anchor not in area or pname(self.anchor) not in net_f["area_end"]:
            raise ControlError("нет площади якоря в network.area_end")
        self.area_anchor = _v(net_f["area_end"][pname(self.anchor)])
        self.opened_hist = {pidx(p): _v(x) for p, x in net_f["gross_opened_hist"].items()}
        self.closed_hist = {pidx(p): _v(x) for p, x in (net_f.get("closed_area_est") or {}).items()}
        mu = list(net_b["maturity_curve"])
        n_mu = len(mu) - 1
        d, kappa = net_b["new_space_density"], net_b["closed_productivity"]
        start = min(area)
        if start > self.anchor - 2:
            raise ControlError(f"история площади начинается с {pname(start)} — позже якоря − 2")
        miss = [f"открытия {pname(q)}" for q in range(start - n_mu + 1, self.anchor + 1)
                if self.opened_hist.get(q) is None]
        miss += [f"закрытия {pname(q)}" for q in range(start + 1, self.anchor + 1)
                 if self.closed_hist.get(q) is None]
        if miss:
            raise ControlError("нет истории сети: " + ", ".join(miss))
        o = self.opened_hist
        # §4.1: зрелая площадь S (без последних n когорт) — с плотностью 1, когорты
        # S − n + 1 … S — с d × μ_возраста (к зрелости — d, как у когорт прогноза)
        young = [o[start - a] for a in range(n_mu)]
        aeff = {start: (area[start] - sum(young))
                + sum(x * d * mu[a] for a, x in enumerate(young))}
        for q in range(start + 1, self.anchor + 1):
            ripening = sum(o[q - a] * d * (mu[a] - mu[a - 1]) for a in range(1, n_mu + 1))
            aeff[q] = aeff[q - 1] - self.closed_hist[q] * kappa + o[q] * d * mu[0] + ripening
        self.hist_start, self.aeff_hist = start, aeff

    # --- capex когорт до якоря и выбывание базы D&A якоря (§4.5)
    def _capex_history(self):
        life = self.book["capex"]["asset_life_years"]
        two_l = round(2 * life)
        if abs(2 * life - two_l) > 1e-12 or two_l < 1:
            raise ControlError("asset_life_years: 2L должно быть целым числом полугодий")
        self.life, self.two_l = life, two_l
        hist = {pidx(r["period"]): r for r in self.facts["history"]["halves"]}
        self.capex_hist = {}
        for j in range(1, two_l + 1):
            q = self.anchor - j
            row = hist.get(q)
            rev = _v(row["revenue"]) if row else None
            pct = _v(row["capex_pct"]) if row else None
            if rev is None or pct is None:
                raise ControlError(f"нет выручки или capex/выручки {pname(q)} в history.halves")
            self.capex_hist[q] = rev * pct
        self.s0 = self.cohort_sum(0)
        if self.s0 <= 0.0:
            raise ControlError("сумма когорт capex до якоря не положительна")
        self.da_anchor = self.acc[self.anchor]["da"]
        self.capex_anchor = self.acc[self.anchor]["capex"]

    def cohort_sum(self, k: int) -> float:
        """S(k) — capex когорт якорь − 1 … якорь − (2L − k), живых в k-м прогнозном полугодии."""
        if k >= self.two_l:
            return 0.0
        return sum(self.capex_hist[self.anchor - j] for j in range(1, self.two_l - k + 1))

    def da_rule(self, k: int, capex_seq: list) -> float:
        """D&A k-го полугодия от якоря по правилу когорт (§4.5): capex_seq[0] — якорь."""
        base = self.da_anchor * self.cohort_sum(k) / self.s0
        return base + sum(capex_seq[k - j] for j in range(1, min(k, self.two_l) + 1)) / self.two_l

    def fix_coupon(self, curve: dict, i: int) -> float:
        """Купон нового фикса полугодия i: трёхлетний форвард кривой мира с начала полугодия."""
        s = max(0.0, 0.5 * ((i - self.first) - self.curve_pos))
        return forward_3y(curve, s)

    # --- маржа
    def target(self, regime: str, i: int) -> float:
        return path_value(self.book["margin"]["targets"][regime], i)

    def target_lt(self, regime: str) -> float:
        return self.term(self.book["margin"]["targets"][regime])

    def term(self, tr):
        """Значение «LT» траектории компании в терминале (§6) — её значение в last_period
        (толкование terminal_lt); опция `terminal_values: lt` — ключ LT буквально."""
        if self.opt.get("terminal_values") == "lt":
            return lt_value(tr)
        return path_value(tr, self.last)

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

    # --- цена (§7.2–§7.3)
    def price_of(self, equity: float) -> float:
        """Цена акции по капиталу (V0 − D): пакет продан по k × P_рынок, акций — N + n."""
        total = equity + self.treasury_value
        return total * ((1.0 - self.g_gov) if total > 0 else 1.0) * 1000.0 / self.all_shares

    def v0_of_price(self, price: float, claims: float) -> float:
        """Обратная функция «цена → EV» (§7.3)."""
        k = (1.0 - self.g_gov) if price > 0 else 1.0
        return price * self.all_shares / 1000.0 / k - self.treasury_value + claims

    def rub_per_1pct(self, v0: float) -> float:
        return 0.01 * v0 * (1.0 - self.g_gov) * 1000.0 / self.all_shares


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
    curve = W["zero_curve"]
    tau = tx["rate"]

    mu = list(net["maturity_curve"])
    n_mu = len(mu) - 1
    d_new, kappa = net["new_space_density"], net["closed_productivity"]

    def debt_rate(i: int, c_state: str, with_ic: bool) -> float:
        """Ставка долга полугодия i (§4.9) в кредитном состоянии c_state; ic — или 0."""
        ic = path_value(fin["issuance_cost"], i) if with_ic else 0.0
        ell = path_value(fin["legacy_weight"], i)
        f = path_value(fin["fixed_share"], i)
        fixed = (ell * path_value(fin["legacy_rate"], i)
                 + (1.0 - ell) * (ctx.fix_coupon(curve, i) + fin["spread_fixed"][c_state] + ic))
        floating = path_value(W["key_rate"], i) + fin["spread_float"][c_state] + ic
        return f * fixed + (1.0 - f) * floating

    # --- сеть: стартовые уровни и история индекса (§4.1)
    area = {anchor: ctx.area_anchor}
    opened = dict(ctx.opened_hist)
    aeff = dict(ctx.aeff_hist)

    R = {q: row["revenue"] for q, row in ctx.acc.items()}
    ai = ctx.acc[anchor]
    rows = {}

    # --- стартовые уровни якоря
    r_ann_anchor = R[anchor] + R[anchor - 1]
    opc, buf_pct = wc["operating_cash_pct"], fin["cash_buffer_pct"]
    nwc_prev = ctx.nwc_anchor
    opcash_prev = path_value(opc, anchor) * r_ann_anchor
    opcash_anchor_level = opcash_prev
    buf_prev = path_value(buf_pct, anchor) * r_ann_anchor
    nd_prev = ctx.net_debt_fact + ctx.div_payable
    rep_prev = ai["ebitda_rep"]
    index = {anchor: 1.0}
    capex_seq = [ctx.capex_anchor]  # capex(k): k = 0 — якорь, 1…N — явный участок
    dev = ctx.dev_path(regime)
    k_s = rev["ticket_k"][state]
    pi_w, pi_ref = W["lt"]["inflation"], wref["lt"]["inflation"]
    hom = rev["homogeneity"]
    div_from = pidx(fin["dividends_from"])
    # база физической части поддерживающего capex: годовые рубли на тыс. м² площади якоря,
    # половина — на полугодие (§4.5)
    phys_base = r_ann_anchor / 2.0 / ctx.area_anchor

    for i in ctx.halves:
        year = i // 2
        # сеть (§4.1)
        gn = path_value(net["net_growth"][tariff], i)
        cl = path_value(net["close_rate"], i)
        closed = area[i - 1] * cl / 2.0
        op = area[i - 1] * gn / 2.0 + closed
        area[i] = area[i - 1] + op - closed
        opened[i] = op
        maturing = sum(opened[i - a] * d_new * (mu[a] - mu[a - 1]) for a in range(1, n_mu + 1))
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
        index[i] = index[i - 1] * (1.0 + half_rate(path_value(W["cpi"], i)))
        a_mid = (area[i - 1] + area[i]) / 2.0
        phi = path_value(cx["maintenance_area_share"], i)
        mnt = path_value(cx["maintenance"][level], i)
        c_maint_phys = mnt * phi * phys_base * a_mid * index[i]
        c_maint = mnt * (1.0 - phi) * R[i] + c_maint_phys
        c_growth = op * path_value(cx["price_per_m2"], i) * index[i]
        c_infra = (max(0.0, op - closed) * path_value(cx["infra_per_m2"], i) * index[i]
                   if year >= cx["infra_from_year"] else 0.0)
        capex = c_maint + c_growth + c_infra
        da = ctx.da_rule(i - anchor, capex_seq)
        capex_seq.append(capex)
        ebit = ebitda - da
        proceeds = path_value(cx["disposal_proceeds_pct"], i) * R[i]

        # оборотный капитал, касса, аренда (§4.6)
        nwc = (path_value(wc["nwc_pct"], i) * r_ann
               + (path_value(wc["h1_excess_pct"], i) * r_ann if is_h1(i) else 0.0))
        opcash = path_value(opc, i) * r_ann
        buf = path_value(buf_pct, i) * r_ann
        lease = path_value(wc["lease_adj_pct"], i) * R[i]

        # ставка долга, проценты и вычеты финансирования (§4.9)
        key = path_value(W["key_rate"], i)
        cash_rate = fin["cash_yield_k"] * key
        rate = debt_rate(i, credit, True)
        rate0 = debt_rate(i, credit, False)
        rate_fair = debt_rate(i, FAIR_CREDIT, not ctx.opt.get("fair_without_ic"))
        # валовой долг: ND модели уже содержит накопленный прирост операционной кассы (он вычтен
        # из FCFF), поэтому операционная касса в нём — уровнем якоря
        gross = nd_prev + opcash_anchor_level + buf_prev
        interest = gross * half_rate(rate) - buf_prev * half_rate(cash_rate)
        gross_pos = max(0.0, gross)
        c_iss = gross_pos * (half_rate(rate) - half_rate(rate0))
        x_spread = gross_pos * max(0.0, half_rate(rate) - half_rate(rate_fair))
        k_carry = buf_prev * (half_rate(key) - half_rate(cash_rate))

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
        nd_company_pre = nd_pre - (opcash - opcash_anchor_level)
        div = (max(0.0, path_value(fin["target_leverage"], i) * ltm - nd_company_pre)
               if i >= div_from else 0.0)
        nd = nd_pre + div
        nd_company = nd - (opcash - opcash_anchor_level)

        rows[i] = {
            "revenue": R[i], "ticket": ticket, "traffic": traffic, "other": other,
            "area_end": area[i], "opened": op, "closed": closed, "area_eff": aeff[i],
            "margin": m, "adj_ebitda": ebitda, "lti": lti, "ebitda_rep": rep, "da": da,
            "ebit": ebit, "capex": capex, "capex_maintenance": c_maint,
            "capex_maintenance_phys": c_maint_phys, "capex_growth": c_growth,
            "capex_infra": c_infra, "nwc": nwc, "nwc_change": d_nwc,
            "opcash": opcash, "opcash_change": d_opc, "buffer": buf, "lease": lease,
            "proceeds": proceeds, "tax_unlevered": tax_u, "tax_actual": tax_a, "shield": shield,
            "fcff": fcff, "debt_rate": rate, "gross_debt": gross, "interest": interest,
            "issuance_cost": c_iss, "excess_spread": x_spread, "buffer_carry": k_carry,
            "dividends": div, "net_debt": nd, "ebitda_rep_ltm": ltm, "leverage": nd_company / ltm,
            "net_debt_company": nd_company, "index": index[i],
        }
        nwc_prev, opcash_prev, buf_prev, nd_prev, rep_prev = nwc, opcash, buf, nd, rep

    # --- терминал (§6)
    prem = val["beta_u"] * val["erp"]
    r_t = curve["LT"] + prem
    pi = W["lt"]["inflation"]
    if pi >= r_t:
        pi = r_t - 0.0001  # §6: защищённый π — во всём терминале
    pi_gordon = pi
    cl_lt = ctx.term(net["close_rate"])
    food_lt = path_value(W["food_cpi"], last)
    ticket_lt = (k_s * food_lt + ctx.term(rev["ticket_shift"][state]) + ctx.term(rev["vat_effect"])
                 + (1.0 - k_s) * (pi_w - pi_ref))  # полная добавка однородности (§6)
    traffic_lt = ctx.term(rev["traffic"][state])
    g = (1.0 + ticket_lt) * (1.0 + traffic_lt) * (1.0 + (d_new * 1.0 - kappa) * cl_lt) - 1.0
    if g >= r_t:
        g = r_t - 0.0001
    area_t = area[last]
    phi_lt = ctx.term(cx["maintenance_area_share"])
    mnt_lt = ctx.term(cx["maintenance"][level])
    price_lt = ctx.term(cx["price_per_m2"])
    t_rows = []
    idx_prev, r_prev = index[last], R[last]
    nwc_p, opc_p = nwc_prev, opcash_prev
    for h in (1, 2):
        t_i = last + h
        r_h = R[t_i - 2] * (1.0 + g)
        idx_h = idx_prev * (1.0 + half_rate(pi))
        r_ann = r_h + r_prev
        m_h = ctx.target_lt(regime) + ctx.season(t_i)
        ebitda_h = r_h * m_h
        lti_h = ctx.term(mg["lti_pct"]) * r_h
        maint_phys = mnt_lt * phi_lt * phys_base * area_t * idx_h
        maint = mnt_lt * (1.0 - phi_lt) * r_h + maint_phys
        repl = area_t * cl_lt / 2.0 * price_lt * idx_h
        nwc_h = ctx.term(wc["nwc_pct"]) * r_ann + (ctx.term(wc["h1_excess_pct"]) * r_ann
                                                    if is_h1(t_i) else 0.0)
        opc_h = ctx.term(opc) * r_ann
        t_rows.append({
            "period": pname(t_i), "revenue": r_h, "revenue_ann": r_ann, "margin": m_h,
            "adj_ebitda": ebitda_h, "lti": lti_h, "ebitda_rep": ebitda_h - lti_h,
            "capex": maint + repl, "capex_pi": maint_phys + repl, "capex_maintenance": maint,
            "capex_replacement": repl, "index": idx_h,
            "nwc_change": nwc_h - nwc_p, "opcash": opc_h, "opcash_change": opc_h - opc_p,
            "buffer": ctx.term(buf_pct) * r_ann,
            "lease": ctx.term(wc["lease_adj_pct"]) * r_h,
            "proceeds": ctx.term(cx["disposal_proceeds_pct"]) * r_h,
            "padd": ctx.term(tx["permanent_add_pct"]) * r_h,
        })
        idx_prev, r_prev, nwc_p, opc_p = idx_h, r_h, nwc_h, opc_h

    # D&A терминала: установившаяся D&A правила когорт по частям g и π, T1 и T2 (§6)
    c_pi = [t["capex_pi"] for t in t_rows]
    c_g = [t["capex"] - t["capex_pi"] for t in t_rows]
    da_g = steady_da(c_g[0], c_g[1], g, ctx.life, ctx.two_l)
    da_p = steady_da(c_pi[0], c_pi[1], pi, ctx.life, ctx.two_l)
    pretax, before_da = [], []
    for h, t in enumerate(t_rows):
        da_h = da_g[h] + da_p[h]
        base = t["adj_ebitda"] - t["lti"] - da_h + t["padd"]
        tax = tau * max(0.0, base)
        fcff_t = (t["adj_ebitda"] - t["lti"] - tax - t["capex"] - t["nwc_change"]
                  - t["opcash_change"] + t["lease"] + t["proceeds"])
        t.update({"da": da_h, "da_g": da_g[h], "da_pi": da_p[h], "tax_base": base, "tax": tax,
                  "fcff": fcff_t})
        pretax.append(fcff_t + tax)                                   # P_h
        before_da.append(t["adj_ebitda"] - t["lti"] + t["padd"])      # B_h

    # налог терминала TV_tax (§6): первые 2L полугодий — D&A по правилу когорт (когорты
    # явного участка и база якоря доживают в терминале), дальше — хвост по области base > 0
    n_exp = ctx.n
    seq = list(capex_seq)
    two_l = ctx.two_l
    for j in range(1, two_l + 1):
        yr, h = (j - 1) // 2, (j - 1) % 2
        seq.append(c_g[h] * (1.0 + g) ** yr + c_pi[h] * (1.0 + pi) ** yr)
    da_early = [ctx.da_rule(n_exp + j, seq) for j in range(1, two_l + 1)]
    first_year = [math.ceil(ctx.life), math.floor(ctx.life)]        # n_0,1 и n_0,2

    def tv_tax_of(b_h: list) -> float:
        """TV_tax при базах T_h до D&A b_h (для TV_tax(I) — за вычетом I_T,h)."""
        total = 0.0
        for j in range(1, two_l + 1):
            yr, h = (j - 1) // 2, (j - 1) % 2
            base_j = b_h[h] * (1.0 + g) ** yr - da_early[j - 1]
            total += tau * max(0.0, base_j) * (1.0 + r_t) ** (-(yr + 0.25 + 0.5 * h))
        for h in (0, 1):
            total += (tau * (1.0 + r_t) ** (-(0.25 + 0.5 * h))
                      * tax_tail(b_h[h] - da_g[h], da_p[h], g, pi, r_t, first_year[h]))
        return total

    tv_tax = tv_tax_of(before_da)
    tv = (gordon(pretax[0] + c_pi[0], pretax[1] + c_pi[1], r_t, g)
          + gordon(-c_pi[0], -c_pi[1], r_t, pi_gordon) - tv_tax)

    # щит и вычеты финансирования терминала: долг начала полугодия на целевом рычаге (§6)
    lev_t = path_value(fin["target_leverage"], last)
    f_t = path_value(fin["fixed_share"], last)
    ell_t = path_value(fin["legacy_weight"], last)
    key_t = path_value(W["key_rate"], last)
    ic_t = path_value(fin["issuance_cost"], last)
    legacy_t = path_value(fin["legacy_rate"], last)

    def term_rate(c_state: str, ic: float) -> float:
        fixed = ell_t * legacy_t + (1.0 - ell_t) * (curve["LT"] + fin["spread_fixed"][c_state] + ic)
        return f_t * fixed + (1.0 - f_t) * (key_t + fin["spread_float"][c_state] + ic)

    rt, rt0 = term_rate(credit, ic_t), term_rate(credit, 0.0)
    rt_fair = term_rate(FAIR_CREDIT, 0.0 if ctx.opt.get("fair_without_ic") else ic_t)
    cash_rate_t = fin["cash_yield_k"] * key_t

    def rep_of(q: int) -> float:
        return rows[q]["ebitda_rep"] if q in rows else ctx.acc[q]["ebitda_rep"]

    ltm_t = [rep_of(last - 1) + rep_of(last), rep_of(last) + t_rows[0]["ebitda_rep"]]
    bufs = [rows[last]["buffer"], t_rows[0]["buffer"]]
    opcs = [rows[last]["opcash"], t_rows[0]["opcash"]]
    s_t, c_t, x_t, k_t, g_t, i_t = [], [], [], [], [], []
    for h, t in enumerate(t_rows):
        gross_h = lev_t * ltm_t[h] + opcs[h] + bufs[h]
        int_h = gross_h * half_rate(rt) - bufs[h] * half_rate(cash_rate_t)
        s_t.append(tau * max(0.0, t["tax_base"]) - tau * max(0.0, t["tax_base"] - int_h))
        gp = max(0.0, gross_h)
        c_t.append(gp * (half_rate(rt) - half_rate(rt0)))
        x_t.append(gp * max(0.0, half_rate(rt) - half_rate(rt_fair)))
        k_t.append(bufs[h] * (half_rate(key_t) - half_rate(cash_rate_t)))
        g_t.append(gross_h)
        i_t.append(int_h)
        t.update({"gross_debt": gross_h, "interest": int_h, "shield": s_t[-1],
                  "issuance_cost": c_t[-1], "excess_spread": x_t[-1], "buffer_carry": k_t[-1]})
    # щит терминала: налог, сбережённый процентами I_T,h × (1 + g)^n, без переноса убытков (§6)
    tv_s = tv_tax - tv_tax_of([before_da[h] - i_t[h] for h in (0, 1)])
    tv_c, tv_x, tv_k = (gordon(c_t[0], c_t[1], r_t, g), gordon(x_t[0], x_t[1], r_t, g),
                        gordon(k_t[0], k_t[1], r_t, g))
    tv_fin = tv_c + tv_x + tv_k

    # --- дисконтирование и EV (§5)
    def df(t: float) -> float:
        return (1.0 + zero_rate(curve, t) + prem) ** (-t)

    def df_v(t: float) -> float:
        return df(ctx.roll + t) / df(ctx.roll)

    pv = {"fcff": 0.0, "shield": 0.0, "issuance_cost": 0.0, "excess_spread": 0.0,
          "buffer_carry": 0.0}
    for n, i in enumerate(ctx.halves):
        if n < ctx.closed:
            continue
        if n == ctx.closed:
            share, t = 1.0 - ctx.elapsed, (1.0 - ctx.elapsed) * 0.25
        else:
            share, t = 1.0, (1.0 - ctx.elapsed) * 0.5 + (n - ctx.closed - 1) * 0.5 + 0.25
        disc = df_v(t)
        for k in pv:
            pv[k] += rows[i][k] * share * disc
        rows[i]["df"], rows[i]["share"] = disc, share
    d_end = df_v(ctx.t_end)
    pv_tv, pv_tv_s = tv * d_end, tv_s * d_end
    pv_iss = pv["issuance_cost"] + tv_c * d_end
    pv_x = pv["excess_spread"] + tv_x * d_end
    pv_k = pv["buffer_carry"] + tv_k * d_end
    ev = pv["fcff"] + pv["shield"] + pv_tv + pv_tv_s - pv_iss - pv_x - pv_k

    # --- требования на дату оценки (§7.1)
    rolled = 0.0
    for n, i in enumerate(ctx.halves):
        cash = rows[i]["fcff"] + rows[i]["shield"] - rows[i]["interest"]
        if n < ctx.closed:
            rolled += cash
        elif n == ctx.closed:
            rolled += ctx.elapsed * cash
    claims = ctx.net_debt_fact + opcash_anchor_level + ctx.bridge_total - rolled + ctx.div_declared

    equity = ev - claims
    ntm = [rows[i]["adj_ebitda"] for i in ctx.halves[ctx.closed:ctx.closed + 2]]
    ebitda_ntm = sum(ntm) if len(ntm) == 2 else None
    # capex / выручка календарных лет пути, год якоря — с фактом якоря (гейт capex_range, §13.2)
    by_year = {}
    for q, row in ctx.acc.items():
        if q <= anchor and q // 2 >= ctx.first // 2:
            cy = by_year.setdefault(q // 2, [0.0, 0.0])
            cy[0], cy[1] = cy[0] + row["capex"], cy[1] + row["revenue"]
    for i in ctx.halves:
        cy = by_year.setdefault(i // 2, [0.0, 0.0])
        cy[0], cy[1] = cy[0] + rows[i]["capex"], cy[1] + rows[i]["revenue"]
    capex_years = {y: c / r for y, (c, r) in sorted(by_year.items())}
    return {
        "world": world, "regime": regime, "capex": level, "growth": tariff, "credit": credit,
        "demand": state, "ev": ev, "pv_fcff": pv["fcff"], "pv_shield": pv["shield"],
        "pv_tv": pv_tv, "pv_tv_shield": pv_tv_s, "pv_terminal": pv_tv + pv_tv_s,
        "pv_issuance": pv_iss, "pv_excess_spread": pv_x, "pv_buffer_carry": pv_k,
        "pv_tv_fin": tv_fin * d_end, "tv_tax": tv_tax,
        "terminal_share": (pv_tv + pv_tv_s - tv_fin * d_end) / ev, "d": claims, "rolled": rolled,
        "opcash_anchor": opcash_anchor_level, "equity": equity, "price": ctx.price_of(equity),
        "terminal_growth": g, "r_terminal": r_t, "terminal_debt_rate": rt,
        "margin_lt": ctx.target_lt(regime), "tv": tv, "tv_shield": tv_s, "tv_fin": tv_fin,
        "df_end": d_end, "terminal_rows": t_rows,
        "ebitda_ntm": ebitda_ntm, "ev_ebitda_fwd": ev / ebitda_ntm if ebitda_ntm else None,
        "max_leverage": max(rows[i]["leverage"] for i in ctx.halves),
        "margin_min": min(rows[i]["margin"] for i in ctx.halves),
        "margin_max": max(rows[i]["margin"] for i in ctx.halves),
        "capex_years": capex_years,
        "rows": rows,
    }


def gates(book: dict, cells: list, layers: dict) -> list:
    """Гейты правдоподобия (§13.2): масса — вероятность клеток слоя «свой взгляд», где условие
    нарушено; V0/D слоя ниже предела — масса 1."""
    ch = book["checks"]

    def outside(x, corridor):
        return x < corridor[0] or x > corridor[1]

    def cell_gate(name, broken):
        hit = [c for c in cells if broken(c)]
        return {"name": name, "mass": sum((c["p_analytical"] for c in hit), 0.0),
                "cells": sorted(f"{c['world']}|{c['regime']}|{c['capex']}" for c in hit)}

    val = book["valuation"]
    out = [
        cell_gate("ev_ebitda", lambda c: c["ev_ebitda_fwd"] is not None
                  and outside(c["ev_ebitda_fwd"], ch["ev_ebitda"][c["world"]])),
        cell_gate("margin_range", lambda c: c["margin_min"] < ch["margin_range"][0]
                  or c["margin_max"] > ch["margin_range"][1]),
        cell_gate("capex_range", lambda c: any(outside(x, ch["capex_range"])
                                               for x in c["capex_years"].values())),
        cell_gate("terminal_share", lambda c: outside(c["terminal_share"], ch["terminal_share"])),
        cell_gate("real_rate", lambda c: outside(
            book["worlds"][c["world"]]["zero_curve"]["LT"] + val["beta_u"] * val["erp"]
            - book["worlds"][c["world"]]["lt"]["inflation"], ch["real_rate"])),
        cell_gate("leverage_path", lambda c: c["max_leverage"] > ch["max_leverage"]),
        cell_gate("equity_cushion", lambda c: c["equity"] <= 0.0),
    ]
    if any(layer["v0_to_d"] < ch["min_v0_to_d"] for layer in layers.values()):
        out[-1]["mass"] = 1.0
    for g in out:
        g["fired"] = g["mass"] > 0.0
    return out


# ------------------------------------------------------------------ сетка, слои, точка


def world_names(book: dict) -> list:
    return [w for w in book["worlds"] if w != "source"]


def layer_weights(book: dict) -> dict:
    j = book["joint"]
    neutral = {w: 0.0 for w in world_names(book)}
    neutral[j["neutral_world"]] = 1.0
    return {"analytical": dict(j["world_prob"]), "market_implied": dict(j["market_implied_prob"]),
            "macro_neutral": neutral}


LAYER_SUMS = ("ev", "d", "pv_fcff", "pv_shield", "pv_tv", "pv_tv_shield", "pv_terminal",
              "pv_issuance", "pv_excess_spread", "pv_buffer_carry", "pv_tv_fin", "rolled")


def evaluate(book: dict, facts: dict, options: dict | None = None, light: bool = False) -> dict:
    """Вся сетка на книге: клетки, слои, точка, V*, ожидаемые пути слоя «свой взгляд»
    (light — только клетки, слои и точка: прогоны полосы)."""
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

    def layer(ww: dict, title: str) -> dict:
        p = [ww.get(c["world"], 0.0) * ctx.posterior[c["regime"]] * cpr[c["regime"]][c["capex"]]
             for c in cells]
        agg = {k: sum(pi * c[k] for pi, c in zip(p, cells)) for k in LAYER_SUMS}
        eq = agg["ev"] - agg["d"]
        out = {"title": title, "world_weights": ww, "v0": agg["ev"], "equity": eq,
               "price": ctx.price_of(eq), "treasury_value": ctx.treasury_value,
               "terminal_share": (agg["pv_terminal"] - agg["pv_tv_fin"]) / agg["ev"],
               "v0_to_d": agg["ev"] / agg["d"]}
        out.update({k: agg[k] for k in LAYER_SUMS if k != "ev"})
        return out

    layers = {name: layer(weights[name], name) for name in LAYERS}
    worlds_only = ({} if light else
                   {w: layer({w: 1.0}, f"world:{w}") for w in world_names(book)})

    lam = book["joint"]["lambda"]
    low, high = layers["macro_neutral"]["price"], layers["analytical"]["price"]
    central = low + lam * (high - low)
    d_an = layers["analytical"]["d"]
    v_star = ctx.v0_of_price(ctx.market, d_an)
    v0_point = ctx.v0_of_price(central, d_an)
    v0_mix = layers["macro_neutral"]["v0"] + lam * (layers["analytical"]["v0"]
                                                    - layers["macro_neutral"]["v0"])
    point = {
        "low": low, "central": central, "high": high, "lambda": lam, "rates_view": high - low,
        "v0_point": v0_point, "v0_point_mix": v0_mix, "v_star": v_star,
        "gap_point": v0_point / v_star - 1.0,
        "rub_per_1pct_ev_point": ctx.rub_per_1pct(v0_point),
        "rub_per_1pct_ev_mix": ctx.rub_per_1pct(v0_mix),
        "equity_share_of_ev": (v0_point - d_an) / v0_point,
        "market_price": ctx.market,
    }
    return {
        "inputs": {"valuation_date": ctx.v.isoformat(), "closed_periods": ctx.closed,
                   "elapsed": ctx.elapsed, "roll_years": ctx.roll, "t_end": ctx.t_end,
                   "curve_pos": ctx.curve_pos, "shares_mln": ctx.shares,
                   "treasury_mln": ctx.treasury, "issued_mln": ctx.issued,
                   "treasury_value": ctx.treasury_value, "governance_discount": ctx.g_gov,
                   "lambda": lam, "market_price": ctx.market},
        "network": {"hist_start": pname(ctx.hist_start),
                    "area_eff": {pname(q): v for q, v in sorted(ctx.aeff_hist.items())},
                    "area_anchor": ctx.area_anchor},
        "da_base": {"da_anchor": ctx.da_anchor, "s0": ctx.s0,
                    "shape": [ctx.cohort_sum(k) / ctx.s0 for k in range(ctx.two_l + 1)]},
        "regimes": {"prior": ctx.prior, "posterior": ctx.posterior, "steps": ctx.update_steps,
                    "margin_anchor_fact": ctx.margin_fact, "deviation_anchor": ctx.dev_anchor},
        "claims": {"net_debt_fact": ctx.net_debt_fact, "opcash_anchor": cells[0]["opcash_anchor"],
                   "bridge_total": ctx.bridge_total, "dividends_declared": ctx.div_declared,
                   "rolled_analytical": layers["analytical"]["rolled"],
                   "d_analytical": d_an, "bridge": ctx.bridge, "dividend_rows": ctx.div_rows},
        "cells": cells, "layers": layers, "worlds_only": worlds_only, "point": point,
        "gates": [] if light else gates(book, cells, layers),
        "paths": None if light else expected_paths(ctx, cells),
    }


# Строки путей: потоки складываются, запасы — на конец года, доли — отношения сумм.
FLOW_ROWS = ("revenue", "adj_ebitda", "lti", "ebitda_rep", "da", "ebit", "capex",
             "capex_maintenance", "capex_growth", "capex_infra", "nwc_change", "opcash_change",
             "lease", "proceeds", "tax_unlevered", "tax_actual", "shield", "fcff", "interest",
             "issuance_cost", "excess_spread", "buffer_carry", "dividends", "opened", "closed")
STOCK_ROWS = ("area_end", "net_debt", "net_debt_company", "ebitda_rep_ltm", "nwc", "opcash")
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
        row["leverage"] = row["net_debt_company"] / row["ebitda_rep_ltm"]
        halves.append(row)
    annual = []
    for year in sorted({i // 2 for i in ctx.halves}):
        hs = [h for h in halves if pidx(h["period"]) // 2 == year]
        row = {"year": year, "halves": len(hs)}
        for k in FLOW_ROWS:
            row[k] = sum(h[k] for h in hs)
        facts_in = [q for q in ctx.acc if q // 2 == year and q <= ctx.anchor]
        for q in facts_in:
            for k in FACT_ROWS:
                row[k] += ctx.acc[q][k]
        for k in STOCK_ROWS:
            row[k] = hs[-1][k]
        row["margin"] = row["adj_ebitda"] / row["revenue"]
        row["capex_pct"] = row["capex"] / row["revenue"]
        row["leverage"] = row["net_debt_company"] / row["ebitda_rep_ltm"]
        annual.append(row)
    return {"halves": halves, "annual": annual}


# ------------------------------------------------------------------ полоса и суждения (§9, §11, §12)


def _get(book: dict, path: str):
    node = book
    for k in path.split("."):
        node = node[k] if k in node else node[int(k)]
    return node


def tri_s(u: float) -> float:
    """Треугольное распределение на [−1; 1] с модой 0 по равномерному u (§9)."""
    return math.sqrt(2.0 * u) - 1.0 if u < 0.5 else 1.0 - math.sqrt(2.0 * (1.0 - u))


def hypercube(n: int, n_axes: int, seed) -> list:
    """Латинский гиперкуб §9: порядок случайных чисел — часть определения."""
    rng = random.Random(seed)
    u = [[0.0] * n_axes for _ in range(n)]
    for j in range(n_axes):
        perm = list(range(n))
        rng.shuffle(perm)
        for i in range(n):
            u[i][j] = (perm[i] + rng.random()) / n
    return u


def axis_value(book: dict, axis: dict, s: float):
    """Значение оси при положении s ∈ [−1; 1]: книга + |s| × (конец − книга) (§9)."""
    end = axis["high"] if s > 0 else axis["low"]
    a = abs(s)
    if axis["kind"] == "shift":
        return a * end
    if axis["kind"] == "value":
        base = _get(book, axis["paths"][0])
        return base + a * (end - base)
    if axis["kind"] == "dict":
        base = _get(book, axis["paths"][0])
        w = {k: base[k] + a * (end.get(k, 0.0) - base[k]) for k in base}
        tot = sum(w.values())
        return {k: x / tot for k, x in w.items()}
    raise ControlError(f"ось неизвестного вида: {axis['kind']}")


def set_axis(book: dict, axis: dict, x) -> None:
    """Подставить значение оси в книгу на месте: value и dict — значение ключа, shift — сдвиг
    всех значений траекторий по путям (кроме LT_from) или скаляра."""
    for path in axis["paths"]:
        keys = path.split(".")
        parent = _get(book, ".".join(keys[:-1])) if len(keys) > 1 else book
        last = keys[-1] if keys[-1] in parent else int(keys[-1])
        if axis["kind"] == "shift":
            node = parent[last]
            if isinstance(node, dict):
                parent[last] = {k: (v if str(k) == "LT_from" else v + x) for k, v in node.items()}
            else:
                parent[last] = node + x
        else:
            parent[last] = copy.deepcopy(x)


def book_with_axes(book: dict, axes: list, values: list) -> dict:
    out = copy.deepcopy(book)
    for axis, x in zip(axes, values):
        set_axis(out, axis, x)
    return out


def quantile7(xs: list, p: float) -> float:
    """Квантиль типа 7 (линейная интерполяция)."""
    ys = sorted(xs)
    h = (len(ys) - 1) * p
    lo = math.floor(h)
    hi = min(lo + 1, len(ys) - 1)
    return ys[lo] + (h - lo) * (ys[hi] - ys[lo])


def band_draws(book: dict, facts: dict, n_first: int | None = None,
               rounded: bool = True) -> list:
    """Прогоны полосы §9: [(низ, верх)] первых n_first прогонов гиперкуба (все — None);
    низ и верх округлены до 0,1 ₽, как у заголовка (толкование band_rounding), для подвыборки
    поиска §11 — без округления."""
    unc = book["valuation"]["uncertainty"]
    axes = unc["axes"]
    u = hypercube(unc["draws"], len(axes), unc["seed"])
    out = []
    for row in u[:n_first] if n_first else u:
        vals = [axis_value(book, a, tri_s(x)) for a, x in zip(axes, row)]
        pt = evaluate(book_with_axes(book, axes, vals), facts, light=True)["point"]
        lo, hi = pt["low"], pt["high"]
        out.append((round(lo, 1), round(hi, 1)) if rounded else (lo, hi))
    return out


def open_period(book: dict) -> str:
    """Открытое полугодие §12: самое раннее прогнозное без внесённого факта (se = 0)."""
    facts_in = {pidx(o["period"]) for o in (book["joint"]["regime_update"].get("observations") or [])
                if o["se"] == 0}
    i = pidx(book["meta"]["first_period"])
    while i in facts_in:
        i += 1
    return pname(i)


def book_with_fact(book: dict, period: str, margin: float) -> dict:
    out = copy.deepcopy(book)
    obs = list(out["joint"]["regime_update"].get("observations") or [])
    obs.append({"period": period, "value": margin, "se": 0.0})
    out["joint"]["regime_update"]["observations"] = obs
    return out


# Стоп поиска по цене (§11, §12), ₽ — проверка решений ядра, а не допущение.
STOP_REVERSE_RUB = 5
STOP_NEUTRAL_RUB = 2


def band_checks(book: dict, facts: dict, core: dict, control: dict) -> dict:
    """Сверка частей полосы, которые контрольная модель считает сама за разумное время: цена
    ошибки суждений (точка на концах осей), таблица «что даст отчёт» (точка), медиана первых
    прогонов гиперкуба (подвыборка обратного DCF), V0 медианы; решения ядра для точки
    (обратный DCF, нейтральная маржа) подставляются и проверяются на стоп поиска."""
    kb = core.get("band") or {}
    out = {"checks": [], "judgements": [], "next_report": [], "reverse": [], "subsample": None}
    if not kb:
        return out
    axes = book["valuation"]["uncertainty"]["axes"]
    point0 = control["point"]["central"]
    market = control["point"]["market_price"]

    def add(group, name, a, b, tol, floor=0.0, tol_text=None):
        rel = _rel(a, b, floor)
        out["checks"].append({"group": group, "name": name, "control": a, "core": b, "rel": rel,
                              "tol": tol, "ok": rel <= tol, "tol_text": tol_text})

    def point_of(bk):
        return evaluate(bk, facts, light=True)["point"]["central"]

    # цена ошибки суждения: точка при low и high оси, остальные — книга (§9)
    for j in kb.get("judgements", []):
        axis = axes[j["index"]]
        lo = point_of(book_with_axes(book, [axis], [axis_value(book, axis, -1.0)]))
        hi = point_of(book_with_axes(book, [axis], [axis_value(book, axis, 1.0)]))
        out["judgements"].append({"name": axis["name"], "low": lo, "high": hi,
                                  "core_low": j["price_low"], "core_high": j["price_high"]})
        add("band.judgements", f"{axis['name']} · low", lo, j["price_low"], TOL_POINT)
        add("band.judgements", f"{axis['name']} · high", hi, j["price_high"], TOL_POINT)

    # «что даст отчёт»: точка и вероятности режимов на книге с фактом (§12)
    nr = kb.get("next_report") or {}
    period = open_period(book)
    if nr and nr.get("period") == period:
        for row in nr.get("table", []):
            res = evaluate(book_with_fact(book, period, row["margin"]), facts, light=True)
            pt = res["point"]["central"]
            out["next_report"].append({"margin": row["margin"], "point": pt, "core": row["point"]})
            add("band.next_report", f"{period} = {row['margin']} · точка", pt, row["point"], TOL_POINT)
            post = Ctx(book_with_fact(book, period, row["margin"]), facts).posterior
            for r, v in (row.get("posterior") or {}).items():
                add("band.next_report.p", f"{period} = {row['margin']} · P({r})", post[r], v,
                    1e-12, floor=1.0)
        neutral = (nr.get("neutral") or {}).get("point")
        if neutral is not None:
            gap = point_of(book_with_fact(book, period, neutral)) - point0
            out["neutral_point"] = {"margin": neutral, "gap": gap}
            add("band.solutions", f"нейтральная маржа точки {neutral:.6f}: точка − точка книги",
                gap, 0.0, 1.0, floor=STOP_NEUTRAL_RUB, tol_text=f"≤ {STOP_NEUTRAL_RUB} ₽")

    # обратный DCF для точки: решение ядра даёт рыночную цену в пределах стопа (§11)
    rv_axes = {a["name"]: a for a in book["valuation"].get("reverse_dcf", {}).get("axes", [])}
    for rv in kb.get("reverse_dcf", []):
        axis = rv_axes.get(rv["name"])
        if axis is None or rv.get("point_status") != "solved":
            continue
        pt = point_of(book_with_axes(book, [axis], [rv["point_solved"]]))
        out["reverse"].append({"name": rv["name"], "x": rv["point_solved"], "gap": pt - market})
        add("band.solutions", f"обратный DCF точки · {rv['name']}", pt - market, 0.0, 1.0,
            floor=STOP_REVERSE_RUB, tol_text=f"≤ {STOP_REVERSE_RUB} ₽")

    # медиана первых прогонов гиперкуба против подвыборки ядра: медиана − δ (§9, §11)
    n_sub = kb.get("subsample")
    stats = kb.get("stats") or {}
    lam = book["joint"]["lambda"]
    if n_sub and "median" in stats and kb.get("delta") is not None:
        draws = band_draws(book, facts, n_sub, rounded=False)
        centers = [lo + lam * (hi - lo) for lo, hi in draws]
        med = quantile7(centers, 0.5)
        core_sub = stats["median"] - kb["delta"]
        out["subsample"] = {"n": n_sub, "median": med, "core": core_sub}
        add("band.subsample", f"медиана первых {n_sub} прогонов", med, core_sub, TOL_POINT)

    # V0 печатаемой медианы и цена 1 % EV (§7.3) — обращением медианы ядра
    cev = kb.get("center_ev") or {}
    if "median" in stats and cev:
        ctx = Ctx(book, facts)
        d_an = control["claims"]["d_analytical"]
        v0_med = ctx.v0_of_price(stats["median"], d_an)
        add("band.center_ev", "v0_median", v0_med, cev["v0_median"], TOL_V0)
        add("band.center_ev", "rub_per_1pct_ev_median", ctx.rub_per_1pct(v0_med),
            cev["rub_per_1pct_ev_median"], TOL_POINT)
        add("band.center_ev", "gap_median", v0_med / control["point"]["v_star"] - 1.0,
            cev["gap_median"], TOL_V0)
    return out


def full_band(book: dict, facts: dict) -> dict:
    """Вся полоса §9 контрольной моделью (≈2000 прогонов сетки; только из командной строки)."""
    draws = band_draws(book, facts)
    lam = book["joint"]["lambda"]
    market = book["meta"]["market_price"]
    centers = [lo + lam * (hi - lo) for lo, hi in draws]
    out = {f"p{int(round(100 * q))}": quantile7(centers, q) for q in (0.1, 0.25, 0.75, 0.9)}
    out["median"] = quantile7(centers, 0.5)
    out["mean"] = sum(centers) / len(centers)
    out["p_below"] = sum(c < market for c in centers) / len(centers)
    return out


# ------------------------------------------------------------------ сверка с ядром

# Строки путей для сверки; FLOOR_ROWS проходят через ноль — знаменатель не меньше доли EBITDA.
KEY_ROWS = ("revenue", "margin", "adj_ebitda", "da", "capex", "capex_maintenance", "tax_unlevered",
            "fcff", "shield", "interest", "issuance_cost", "excess_spread", "buffer_carry",
            "dividends", "net_debt", "area_end")
FLOOR_ROWS = ("fcff", "dividends")
# Слагаемые разложения EV слоёв (§5) — допуск строк.
EV_PARTS = ("pv_fcff", "pv_shield", "pv_terminal", "pv_issuance", "pv_excess_spread",
            "pv_buffer_carry")


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
        add("cells.price", tag, c["price"], k["price"], TOL_POINT)
        for p in LAYER_P.values():
            add("cells.p", f"{tag}.{p}", c[p], k[p], 1e-12, floor=1e-12)
        if "pv_terminal" in k:
            add("cells.terminal", f"{tag}.pv_terminal", c["pv_terminal"], k["pv_terminal"], TOL_V0)
        if "terminal_growth" in k:
            add("cells.terminal", f"{tag}.g", c["terminal_growth"], k["terminal_growth"], TOL_ROWS)
        if "terminal_share" in k:
            add("cells.terminal", f"{tag}.terminal_share", c["terminal_share"],
                k["terminal_share"], TOL_V0)
        # слагаемые EV и терминала клетки (§5, §6) — допуск строк; пол — доли EV
        for name in ("pv_fcff", "pv_shield", "pv_issuance", "pv_excess_spread", "pv_buffer_carry",
                     "tv_tax", "terminal_debt_rate"):
            if name in k:
                add("cells.parts", f"{tag}.{name}", c[name], k[name], TOL_ROWS,
                    floor=1e-12 * abs(k["ev"]))
        for name, tol in (("ev_ebitda_fwd", TOL_V0), ("max_leverage", TOL_ROWS),
                          ("margin_min", TOL_ROWS), ("margin_max", TOL_ROWS)):
            if k.get(name) is not None and c.get(name) is not None:
                add("cells.gate_inputs", f"{tag}.{name}", c[name], k[name], tol)
    # гейты (§13.2): масса — сумма вероятностей клеток; сверка точная (до округления сумм)
    core_gates = {g["name"]: g for g in core.get("gates", [])}
    for g in control.get("gates", []):
        k = core_gates.get(g["name"])
        if k is not None:
            add("gates", f"{g['name']}.mass", g["mass"], k["mass"], 1e-12, floor=1.0)
            same = sorted(k.get("cells", [])) == g["cells"] or g["name"] == "equity_cushion"
            add("gates", f"{g['name']}.cells", 0.0 if same else 1.0, 0.0, 1e-12, floor=1.0)
    for name in LAYERS:
        a, b = control["layers"][name], core["layers"][name]
        add("layers.v0", name, a["v0"], b["v0"], TOL_V0)
        add("layers.d", name, a["d"], b["d"], TOL_CLAIMS)
        add("layers.price", name, a["price"], b["price"], TOL_POINT)
        for part in EV_PARTS:
            if part in b:
                add("layers.ev_parts", f"{name}.{part}", a[part], b[part], TOL_ROWS,
                    floor=1e-12 * abs(b["v0"]))
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
    if "equity_share_of_ev" in kp:
        add("point.equity_share", "equity_share_of_ev", cp["equity_share_of_ev"],
            kp["equity_share_of_ev"], TOL_POINT)
    add("claims", "d_analytical", control["claims"]["d_analytical"], core["claims"]["d_analytical"],
        TOL_CLAIMS)
    # слагаемые требований (§7.1): у каждого — свой допуск требований, чтобы строка моста или
    # дивиденд меньше 1 % D не прятались в сумме
    for name in ("net_debt_fact", "opcash_anchor", "bridge_total", "dividends_declared",
                 "rolled_analytical"):
        if name in core["claims"]:
            add("claims", name, control["claims"][name], core["claims"][name], TOL_CLAIMS,
                floor=1e-12 * abs(core["claims"]["d_analytical"]))
    core_bridge = {ln["key"]: ln for ln in core["claims"].get("bridge", [])}
    for ln in control["claims"]["bridge"]:
        k = core_bridge.get(ln["key"])
        if k is not None:
            add("claims", f"bridge.{ln['key']}.included", float(ln["included"]),
                float(bool(k.get("included"))), 1e-12, floor=1.0)
    if "treasury_value" in core.get("inputs", {}):
        add("claims", "treasury_value", control["inputs"]["treasury_value"],
            core["inputs"]["treasury_value"], TOL_CLAIMS)

    def rows(group, ours, theirs):
        scale = abs(theirs["adj_ebitda"])
        for r in KEY_ROWS:
            if r in theirs:
                add(group, f"{ours.get('period', ours.get('year'))}.{r}", ours[r], theirs[r],
                    TOL_ROWS, (ROW_FLOOR_EBITDA if r in FLOOR_ROWS else 1e-12) * scale)

    core_halves = {h["period"]: h for h in core["paths"]["halves"]}
    for h in control["paths"]["halves"]:
        if h["period"] in core_halves:
            rows("rows.halves", h, core_halves[h["period"]])
    core_years = {a["year"]: a for a in core["paths"]["annual"]}
    for a in control["paths"]["annual"]:
        if a["year"] in core_years:
            rows("rows.annual", a, core_years[a["year"]])
    return out


def exact_match(control: dict, core: dict) -> float:
    """Наибольшее относительное отклонение V0 слоёв от ядра."""
    return max(_rel(control["layers"][n]["v0"], core["layers"][n]["v0"]) for n in LAYERS)


def bitwise_copy(control: dict, core: dict) -> bool:
    """EV всех клеток и V0 всех слоёв совпали с ядром бит в бит — след общего кода: две
    независимые реализации расходятся хотя бы в последних битах из-за порядка операций."""
    core_cells = {(c["world"], c["regime"], c["capex"]): c["ev"] for c in core["cells"]}
    cells_same = all(core_cells.get((c["world"], c["regime"], c["capex"])) == c["ev"]
                     for c in control["cells"])
    layers_same = all(control["layers"][n]["v0"] == core["layers"][n]["v0"] for n in LAYERS)
    return cells_same and layers_same


def bitwise_share(control: dict, core: dict) -> tuple[int, int]:
    """Сколько клеток совпали с ядром по EV бит в бит (из скольких)."""
    core_cells = {(c["world"], c["regime"], c["capex"]): c["ev"] for c in core["cells"]}
    same = sum(core_cells.get((c["world"], c["regime"], c["capex"])) == c["ev"]
               for c in control["cells"])
    return same, len(control["cells"])


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
    """Ответ контрольной модели при другом прочтении спорных мест: [(толкование, расчёт,
    опорный расчёт или None — тогда опора — основной расчёт на книге)]."""
    out = []
    # terminal_lt: на книге чтения совпадают; на коротком горизонте — нет
    out.append(("«LT» терминала — ключ LT буквально (terminal_lt), книга",
                evaluate(book, facts, {"terminal_values": "lt"}), None))
    short = [s for s in SCENARIOS if s["key"] == "short_horizon"]
    if short:
        sb = scenario_book(book, short[0])
        out.append((f"То же на книге сценария short_horizon (явный участок до "
                    f"{sb['meta']['last_period']}); опора — значение в last_period",
                    evaluate(sb, facts, {"terminal_values": "lt"}), evaluate(sb, facts)))
    # hist_capex: когорты до якоря из accounting.json там, где полугодия есть в обоих файлах
    acc = facts["accounting"]["periods"]
    f2 = copy.deepcopy(facts)
    touched = False
    for row in f2["history"]["halves"]:
        a = acc.get(row["period"])
        if a and _v(a.get("capex")) is not None and _v(a.get("revenue")):
            row["revenue"] = {"v": _v(a["revenue"])}
            row["capex_pct"] = {"v": _v(a["capex"]) / _v(a["revenue"])}
            touched = True
    if touched:
        out.append(("Когорты capex до якоря из `accounting.json`, где полугодие есть и там "
                    "(hist_capex)", evaluate(book, f2), None))
    # fair_rate_ic: справедливая ставка без издержек размещения (X содержал бы и ic)
    out.append(("Справедливая ставка без издержек размещения (fair_rate_ic)",
                evaluate(book, facts, {"fair_without_ic": True}), None))
    return out


def render_report(control: dict, core: dict | None, book_path: Path, facts_dir: Path,
                  sens: list) -> str:
    L = ["# Контрольная модель X5: сверка с ядром", "",
         "Генерируется: `python -B -m tests.independent_model --report` (не править руками). "
         "Контрольная модель `tests/independent_model.py` написана по `docs/MODEL.md`, книге и "
         "фактам без доступа к `model/`; `tests/test_control_model.py` сверяет её с таблицами "
         "ядра `data/assumptions/results.json` и с ядром на сценариях ниже. Допуски: строки "
         "путей и слагаемые разложения EV ≤ 10 %, V0 слоёв и EV клеток ≤ 3 %, требования D ≤ 1 %, "
         "точка ≤ 3 %.", ""]
    inp = control["inputs"]
    L.append(f"* Книга `{_rel_path(book_path)}` (версия {control.get('book_version', '—')}), "
             f"факты `{_rel_path(facts_dir)}`.")
    L.append(f"* Дата оценки {inp['valuation_date']}: закрытых полугодий {inp['closed_periods']}, "
             f"прошло {_num(inp['elapsed'], 4)} текущего, перекат по форвардам "
             f"{_num(inp['roll_years'], 4)} г., t_end {_num(inp['t_end'], 4)} г.; дата кривой — "
             f"{_num(inp['curve_pos'], 4)} полугодия от первого прогнозного.")
    L.append(f"* Акции: в обращении {_num(inp['shares_mln'], 3)} млн, казначейские "
             f"{_num(inp['treasury_mln'], 3)} млн; выручка от продажи пакета T = "
             f"{_num(inp['treasury_value'], 2)} млрд ₽ по рыночной цене "
             f"{_num(inp['market_price'], 1)} ₽.")
    L.append("")
    if core is None:
        L += ["**results.json ядра нет — сверять не с чем.** Ниже только числа контрольной модели.",
              ""]

    # --- итог и независимость
    checks = compare(control, core) if core else []
    band = control.get("band") or {}
    checks += band.get("checks", [])
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
            g = groups.setdefault(c["group"], {"n": 0, "bad": 0, "max": 0.0, "tol": c["tol"],
                                               "tol_text": c.get("tol_text")})
            g["n"] += 1
            g["bad"] += 0 if c["ok"] else 1
            g["max"] = max(g["max"], c["rel"])
            if c.get("tol_text"):
                g["tol_text"] = "стоп поиска"
        L += _table(["Группа", "Сравнений", "Вне допуска", "Макс. отклонение", "Допуск"],
                    [[k, g["n"], g["bad"],
                      _pct(g["max"], 1) + " стопа" if g["tol_text"] else _dev(g["max"]),
                      g["tol_text"] or (_pct(g["tol"], 0) if g["tol"] >= 0.01 else "точно")]
                     for k, g in groups.items()])
        L.append("")
        if bad:
            L += ["Вне допуска (первые 40):", ""]
            L += _table(["Группа", "Что", "Контроль", "Ядро", "Отклонение", "Допуск"],
                        [[c["group"], c["name"], _num(c["control"], 3), _num(c["core"], 3),
                          _pct(c["rel"], 2), c.get("tol_text") or _pct(c["tol"], 0)]
                         for c in bad[:40]])
            L.append("")
        same, total = bitwise_share(control, core)
        L += ["## Независимость", ""]
        L.append(f"EV всех {total} клеток и V0 всех слоёв бит в бит с ядром: "
                 f"{'**да — признак общего кода**' if bitwise_copy(control, core) else 'нет'} "
                 f"({'все клетки' if same == total else 'часть клеток'} бит в бит; сколько "
                 "именно — зависит от платформы и в отчёт не печатается). Две независимые "
                 "реализации расходятся хотя бы в последних битах из-за порядка операций, поэтому "
                 "полное совпадение бит в бит тест запрещает. Совпадение до машинной точности при "
                 "этом ожидаемо: текст `docs/MODEL.md` однозначен для веток, которые включает "
                 "книга, и ядро реализует именно его. Независимость держит проверка импортов: "
                 "контрольная модель импортирует только стандартную библиотеку и PyYAML, ядро её "
                 "не импортирует.")
        L.append("")

    # --- сценарии
    L += ["## Сценарии сверки", "",
          "Изменённые книги, на которых тест прогоняет ядро и контрольную модель и сверяет в тех же "
          "допусках: они включают ветки, которые книга держит выключенными.", ""]
    L += _table(["Сценарий", "Что включает", "Изменённые ключи книги"],
                [[s["key"], s["title"], ", ".join(f"`{k}`" for k in s["set"])] for s in SCENARIOS])
    L.append("")

    # --- история сети и база D&A
    net = control["network"]
    L += ["## История индекса площади и база D&A якоря", "",
          f"Индекс эффективной площади ведётся от конца {net['hist_start']} (§4.1); площадь "
          f"якоря — {_num(net['area_anchor'], 1)} тыс. м².", ""]
    L += _table(["Полугодие", "A_eff, тыс. м²"],
                [[p, _num(v, 1)] for p, v in net["area_eff"].items()])
    L.append("")
    da = control["da_base"]
    shape = da["shape"]
    L.append(f"База D&A якоря {_num(da['da_anchor'], 3)} млрд ₽ выбывает по когортам S(k)/S(0) "
             f"(S(0) = {_num(da['s0'], 1)} млрд ₽ capex 2L полугодий до якоря): "
             + ", ".join(f"k={k}: {_num(v, 3)}" for k, v in enumerate(shape)) + ".")
    L.append("")

    # --- слои и точка
    L += ["## Слои, точка, V*", ""]
    rows = []
    for n in LAYERS:
        a = control["layers"][n]
        b = core["layers"][n] if core else None
        for k, nd in (("v0", 1), ("d", 2), ("price", 0), ("pv_fcff", 1), ("pv_shield", 1),
                      ("pv_terminal", 1), ("pv_issuance", 2), ("pv_excess_spread", 2),
                      ("pv_buffer_carry", 2)):
            bk = b.get(k) if b else None
            rows.append([n, k, _num(a[k], nd), _num(bk, nd) if bk is not None else "—",
                         _signed_pct(a[k], bk) if bk is not None else "—"])
    L += _table(["Слой", "Величина", "Контроль", "Ядро", "Контроль / ядро − 1"], rows)
    L.append("")
    cp = control["point"]
    kp = core["point"] if core else {}
    rows = []
    for k, nd in (("low", 0), ("central", 0), ("high", 0), ("rates_view", 1), ("v_star", 2),
                  ("v0_point", 1), ("gap_point", 4), ("rub_per_1pct_ev_point", 2),
                  ("equity_share_of_ev", 4)):
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
    L += _table(["Строка моста", "Сумма, млрд ₽", "В мосте"],
                [[ln["label"], _num(ln["amount"], 3), "да" if ln["included"] else "нет"]
                 for ln in cc["bridge"]])
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
             ("issuance_cost", +1, "из них издержки размещения C_iss"),
             ("excess_spread", +1, "из них сверх справедливого спреда X"),
             ("buffer_carry", +1, "Кэрри подушки K"),
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
        for k, nd in (("revenue", 0), ("margin", 2), ("capex", 1), ("da", 1), ("fcff", 1),
                      ("shield", 1), ("interest", 1), ("net_debt", 1)):
            v = _num(100 * a[k], nd) if k == "margin" else _num(a[k], nd)
            if b and k in b:
                v += f" ({_signed_pct(a[k], b[k], 1)})"
            line.append(v)
        rows.append(line)
    L += _table(["Год", "Выручка", "Маржа, %", "Capex", "D&A", "FCFF", "Щит", "Проценты", "ЧД"],
                rows)
    L.append("")

    # --- терминал
    L += ["## Терминал (клетки с capex base)", "",
          "PV терминала — (TV + TV_S) × df(t_end); TV_tax — PV налога терминала без рычага на конец "
          "явного участка (точная сумма τ × max(0, base) по всем полугодиям, §6); "
          "TV_fin — вычеты финансирования терминала.", ""]
    kcells = {(c["world"], c["regime"], c["capex"]): c for c in core["cells"]} if core else {}
    rows = []
    for c in control["cells"]:
        if c["capex"] != "base":
            continue
        k = kcells.get((c["world"], c["regime"], c["capex"]))
        rows.append([f"{c['world']} · {c['regime']}", _pct(c["terminal_growth"], 3),
                     _pct(k["terminal_growth"], 3) if k else "—", _pct(c["r_terminal"], 3),
                     _pct(c["terminal_debt_rate"], 3), _num(c["tv_tax"], 2),
                     _num(k.get("tv_tax"), 2) if k else "—",
                     _num(c["pv_tv"], 1), _num(c["pv_tv_shield"], 1), _num(c["pv_tv_fin"], 2),
                     _signed_pct(c["pv_terminal"], k["pv_terminal"]) if k else "—"])
    L += _table(["Клетка", "g контроль", "g ядро", "r", "r долга", "TV_tax", "TV_tax ядро",
                 "PV TV", "PV TV_S", "PV TV_fin", "PV терминала: откл."], rows)
    L.append("")

    # --- гейты
    L += ["## Гейты правдоподобия (§13.2, слой «свой взгляд»)", ""]
    kg = {g["name"]: g for g in core.get("gates", [])} if core else {}
    rows = []
    for g in control["gates"]:
        k = kg.get(g["name"])
        rows.append([g["name"], _num(g["mass"], 4), _num(k["mass"], 4) if k else "—",
                     ", ".join(x.replace("|", " · ") for x in g["cells"]) or "—",
                     ("да" if sorted(k.get("cells", [])) == g["cells"] else "**нет**") if k else "—"])
    L += _table(["Гейт", "Масса, контроль", "Масса, ядро", "Клетки (контроль)", "Клетки совпали"],
                rows)
    L.append("")

    # --- полоса и суждения
    if band:
        L += ["## Полоса, суждения, «что даст отчёт» (§9, §11, §12)", "",
              "Вся полоса — 2 000 прогонов сетки; контрольная модель в тесте считает те её части, "
              "что укладываются в секунды: точку на концах каждой оси (цена ошибки суждения), точку "
              "на книге с фактом отчёта, медиану первых прогонов того же гиперкуба (подвыборка "
              "обратного DCF: медиана ядра − δ) и подставляет решения поиска ядра для точки. "
              "Полную полосу контрольная модель считает командой "
              "`python -B -m tests.independent_model --band`.", ""]
        rows = [[j["name"], _num(j["low"], 0), _num(j["core_low"], 0), _num(j["high"], 0),
                 _num(j["core_high"], 0)] for j in band["judgements"]]
        L += _table(["Суждение", "Точка при low", "ядро", "Точка при high", "ядро"], rows)
        L.append("")
        if band["next_report"]:
            L += _table(["Маржа открытого полугодия", "Точка, контроль", "Точка, ядро"],
                        [[_pct(r["margin"], 1), _num(r["point"], 1), _num(r["core"], 1)]
                         for r in band["next_report"]])
            L.append("")
        lines = []
        if band.get("neutral_point"):
            npnt = band["neutral_point"]
            lines.append(f"* Нейтральная маржа точки ядра {_pct(npnt['margin'], 3)}: точка на книге "
                         f"с этим фактом отличается от точки книги на {_num(npnt['gap'], 2)} ₽ "
                         f"(стоп поиска {STOP_NEUTRAL_RUB} ₽).")
        for r in band["reverse"]:
            lines.append(f"* Обратный DCF точки, «{r['name']}» = {_num(r['x'], 5)}: точка − рынок "
                         f"= {_num(r['gap'], 2)} ₽ (стоп {STOP_REVERSE_RUB} ₽).")
        if band.get("subsample"):
            sb = band["subsample"]
            lines.append(f"* Медиана первых {sb['n']} прогонов: контроль {_num(sb['median'], 2)} ₽, "
                         f"ядро (медиана − δ) {_num(sb['core'], 2)} ₽.")
        L += lines + [""]

    # --- клетки
    L += ["## Клетки: EV и D", ""]
    rows = []
    for c in control["cells"]:
        k = kcells.get((c["world"], c["regime"], c["capex"]))
        rows.append([f"{c['world']} · {c['regime']} · {c['capex']}", _num(c["ev"], 1),
                     _num(k["ev"], 1) if k else "—", _signed_pct(c["ev"], k["ev"]) if k else "—",
                     _num(c["d"], 2), _signed_pct(c["d"], k["d"], 3) if k else "—",
                     _signed_pct(c["pv_fcff"], k["pv_fcff"]) if k else "—",
                     _signed_pct(c["pv_shield"], k["pv_shield"]) if k else "—",
                     _num(c["pv_issuance"] + c["pv_excess_spread"] + c["pv_buffer_carry"], 2)])
    L += _table(["Клетка", "EV контроль", "EV ядро", "EV откл.", "D контроль", "D откл.",
                 "PV FCFF откл.", "PV щита откл.", "Вычеты финансирования, PV"], rows)
    L.append("")

    # --- чувствительность
    if sens:
        L += ["## Чувствительность к толкованиям", "",
              "Ответ контрольной модели при другом прочтении спорного места (остальное — как выше).",
              ""]
        rows = []
        for title, alt, ref in sens:
            ref = ref or control
            an, base_an = alt["layers"]["analytical"], ref["layers"]["analytical"]
            rows.append([title, _num(an["v0"], 1), _num(base_an["v0"], 1),
                         _signed_pct(an["v0"], base_an["v0"], 3),
                         _num(alt["point"]["central"], 0),
                         _num(alt["point"]["central"] - ref["point"]["central"], 1)])
        L += _table(["Толкование", "V0 «свой взгляд»", "V0 опоры", "к опоре", "Точка, ₽",
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
    control["band"] = band_checks(book, facts, core, control) if core else None
    text = render_report(control, core, book_path, facts_dir, sensitivities(book, facts))
    return text, control, core


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Независимая контрольная модель X5")
    ap.add_argument("--report", action="store_true", help="записать docs/CONTROL-MODEL.md")
    ap.add_argument("--results", help="results.json ядра для сверки (по умолчанию — книги)")
    ap.add_argument("--band", action="store_true",
                    help="посчитать всю полосу §9 (≈2000 прогонов сетки, минуты) и сверить с ядром")
    args = ap.parse_args(argv)
    if args.band:
        core = load_results(Path(args.results) if args.results else None)
        book_path, facts_dir = (inputs_of_results(core) if core
                                else (default_book_path(), default_facts_dir()))
        stats = full_band(load_book(book_path), load_facts(facts_dir))
        kst = (core or {}).get("band", {}).get("stats", {})
        for k, v in stats.items():
            ref = kst.get(k)
            tail = f"  ядро {ref:.4f}  разница {v - ref:+.6f}" if ref is not None else ""
            print(f"{k:8} {v:.4f}{tail}")
        return 0
    text, control, core = build_report(Path(args.results) if args.results else None)
    if args.report:
        REPORT_MD.write_text(text + "\n", encoding="utf-8", newline="\n")
        print(f"записано: {REPORT_MD.relative_to(ROOT).as_posix()}")
    an = control["layers"]["analytical"]
    print(f"V0 «свой взгляд» {an['v0']:.2f}, D {an['d']:.3f}, точка {control['point']['central']:.1f}")
    if core:
        checks = compare(control, core)
        bad = [c for c in checks if not c["ok"]]
        same, total = bitwise_share(control, core)
        print(f"сравнений {len(checks)}, вне допуска {len(bad)}; наибольшее отклонение V0 слоёв "
              f"{exact_match(control, core):.2e}; EV бит в бит: {same} из {total}")
        for c in bad[:20]:
            print(f"  {c['group']} {c['name']}: {c['control']:.4f} против {c['core']:.4f} "
                  f"({100 * c['rel']:.2f} %)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
