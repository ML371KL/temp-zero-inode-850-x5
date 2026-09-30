"""Факты X5 (`data/facts/*.json`, `data/calendar.json`): у каждого числа источник,
даты в ISO, тождества отчётности сходятся, ядро читает факты без отказа.

Описание файлов и источников — `docs/FACTS.md`; схема — `data/facts/SCHEMA.md`.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
from pathlib import Path

import pytest

from model.book import load_book, period_index
from model.facts import core_facts, load_facts

ROOT = Path(__file__).resolve().parents[1]
FACTS = ROOT / "data" / "facts"
FIXTURE = ROOT / "tests" / "fixtures" / "facts"
CALENDAR = ROOT / "data" / "calendar.json"
BUILDER = ROOT / "ops" / "tools" / "build_facts.py"
FILES = ("accounting", "network", "balance", "bridge", "shares", "dividends", "debt_register",
         "history", "peers", "brokers", "actuals", "guidance")
ANCHOR_DATE = "2026-06-30"
COLLECTED_ON = "2026-09-28"
DIVIDEND_STATUSES = {"declared", "paying", "paid", "unclaimed"}
DATE_KEYS = {"as_of", "date", "decided_on", "record_date", "ex_date", "last_cum_date", "pay_until",
             "pay_until_nominee", "reported_on", "published", "collected_on", "put_date", "maturity",
             "repayment_date_for_model"}
EVENT_KINDS = {"trading_update", "ifrs", "dividend", "cbr"}


def load(name: str):
    return json.loads((FACTS / f"{name}.json").read_text(encoding="utf-8"))


def v(node):
    """Значение узла {"v": …} (или сам узел, если это не узел факта)."""
    return node["v"] if isinstance(node, dict) and "v" in node else node


def walk(node, where=""):
    """Все (путь, ключ, значение) дерева JSON."""
    if isinstance(node, dict):
        for k, x in node.items():
            yield f"{where}.{k}", k, x
            yield from walk(x, f"{where}.{k}")
    elif isinstance(node, list):
        for i, x in enumerate(node):
            yield f"{where}[{i}]", None, x
            yield from walk(x, f"{where}[{i}]")


def close(a, b, tol):
    return abs(a - b) <= tol


# --------------------------------------------------------------- форма и источники


def test_files_parse():
    for name in FILES:
        assert isinstance(load(name), dict), name
    assert isinstance(json.loads(CALENDAR.read_text(encoding="utf-8")), dict)


def sourced(node) -> bool:
    """Узел (или строка) с источником: `src` или `calc` — текст хотя бы с одной буквой или
    цифрой (пробел и «?» — не источник)."""
    return isinstance(node, dict) and any(
        isinstance(node.get(k), str) and any(ch.isalnum() for ch in node[k]) for k in ("src", "calc"))


def bare_numbers(node, where="", key=None, row=None):
    """Числа вне узлов `{"v": …}`: (путь, ключ, строка-владелец). Внутри узла число
    подтверждено источником узла; true/false — флаги, не числа."""
    if isinstance(node, dict):
        if "v" in node:
            return
        for k, x in node.items():
            yield from bare_numbers(x, f"{where}.{k}", k, node)
    elif isinstance(node, list):
        for i, x in enumerate(node):
            yield from bare_numbers(x, f"{where}[{i}]", key, row)
    elif isinstance(node, (int, float)) and not isinstance(node, bool):
        yield where, key, row


# Голое число вне узла {v, src|calc} допустимо только в этих полях (data/facts/SCHEMA.md):
# ключ → чем подтверждено. Любое другое голое число — число без источника.
BARE_NUMBER_KEYS = {
    "year": None,                                   # год строки — её ключ, а не факт
    "dps": ("src", "amount"),                       # DPS строки дивиденда: src строки или узел amount
    "target_leverage": ("src",),                    # параметры дивидендной политики: src policy
    "no_pay_above": ("src",),
}


def _check_sources(data, name):
    nodes = [("", data)] + [(p, x) for p, _, x in walk(data)]
    for path, node in nodes:
        if isinstance(node, dict) and "v" in node:
            assert sourced(node), f"{name}{path}: значение без src и calc"
    for path, key, row in bare_numbers(data, name):
        assert key in BARE_NUMBER_KEYS, f"{path}: число без узла {{v, src|calc}}"
        by = BARE_NUMBER_KEYS[key]
        if by:
            assert sourced(row) or ("amount" in by and sourced(row.get("amount"))), \
                f"{path}: у строки нет источника ({' или '.join(by)})"


def test_every_value_has_src_or_calc():
    """У каждого числа фактов — источник: узел `{"v", "src"|"calc"}` с непустым источником;
    голое число — только в полях `BARE_NUMBER_KEYS` (и там строка подтверждена источником).
    Тот же закон — у фикстуры ядра `tests/fixtures/facts`."""
    for name in FILES:
        _check_sources(load(name), name)
    for file in sorted(FIXTURE.glob("*.json")):
        _check_sources(json.loads(file.read_text(encoding="utf-8")), f"фикстура {file.stem}")


def test_dates_are_iso():
    for name in FILES:
        for path, key, x in walk(load(name)):
            if key in DATE_KEYS and isinstance(x, str):
                dt.date.fromisoformat(x)          # ValueError — не ISO
            if key == "paid_share_on":
                for day in x:
                    dt.date.fromisoformat(day)


def test_anchor_dates():
    for name in ("accounting", "network", "balance", "bridge", "shares", "debt_register", "peers"):
        assert load(name)["as_of"] == ANCHOR_DATE, name


def test_calendar_events():
    cal = json.loads(CALENDAR.read_text(encoding="utf-8"))
    start = dt.date.fromisoformat(cal["as_of"])
    events = cal["events"]
    assert events
    days = []
    for e in events:
        assert set(e) >= {"date", "title", "kind", "confirmed", "note", "src"}, e
        day = dt.date.fromisoformat(e["date"])
        assert start <= day <= start + dt.timedelta(days=366), e
        assert e["kind"] in EVENT_KINDS, e
        assert isinstance(e["confirmed"], bool) and e["title"] and e["src"], e
        if not e["confirmed"]:
            assert e["note"], f"оценочная дата без пояснения: {e}"
        assert day.weekday() < 5, f"событие на выходной: {e}"
        days.append(day)
    assert days == sorted(days)
    kinds = {e["kind"] for e in events}
    assert kinds == EVENT_KINDS


# --------------------------------------------------------------- тождества


def test_halves_sum_to_year_2025():
    acc = load("accounting")["periods"]
    year = {r["year"]: r for r in load("history")["annual"]}[2025]
    halves = {r["period"]: r for r in load("history")["halves"]}
    rev = v(acc["2025H1"]["revenue"]) + v(acc["2025H2"]["revenue"])
    assert close(rev, v(year["revenue"]), 0.002)
    assert close(v(halves["2025H1"]["revenue"]) + v(halves["2025H2"]["revenue"]), v(year["revenue"]), 0.002)
    for key, ratio in (("adj_ebitda", "adj_margin"), ("ebitda_rep", "rep_margin"), ("capex", "capex_pct")):
        total = v(acc["2025H1"][key]) + v(acc["2025H2"][key])
        assert close(total / v(year["revenue"]), v(year[ratio]), 1e-6), key


def test_ebitda_bridge_per_half():
    acc = load("accounting")
    for p, row in acc["periods"].items():
        one_off = v(acc["memo"][p]["oneoff_ecl_adj"])
        assert close(v(row["adj_ebitda"]) - v(row["lti"]) - one_off, v(row["ebitda_rep"]), 0.002), p
        memo = acc["memo"][p]
        assert close(v(memo["da_incl_impairment"]) - v(memo["impairment_net"]), v(row["da"]), 1e-9), p


def test_net_debt_identity():
    b = load("balance")
    assert close(v(b["net_debt"]), v(b["total_debt"]) - v(b["cash"]), 0.001)
    assert close(v(b["leasing"]), v(b["total_debt"]) - v(b["borrowings"]), 1e-9)


def test_dividend_register_matches_payable():
    d, b = load("dividends"), load("balance")
    anchor = dt.date.fromisoformat(ANCHOR_DATE)
    liable = [r for r in d["register"] if r["cash_in_company_at_facts_date"]
              and dt.date.fromisoformat(r["decided_on"]) <= anchor]
    assert close(sum(v(r["amount"]) for r in liable), v(b["dividends_payable"]), 1e-9)
    # на дату оценки книги (после отсечки 07.07.2026) все они — требования моста
    book_day = dt.date(2026, 9, 25)
    claims = [r for r in liable if dt.date.fromisoformat(r["ex_date"]) <= book_day]
    assert close(sum(v(r["amount"]) for r in claims), v(b["dividends_payable"]), 1e-9)


def test_dividend_register_statuses():
    """Статус записи реестра — на дату сбора фактов: declared (объявлен) | paying (идёт выплата) |
    paid (срок выплаты истёк) | unclaimed (невостребованный остаток прошлых выплат)."""
    for r in load("dividends")["register"]:
        assert r["status"] in DIVIDEND_STATUSES, r["id"]
        if r["status"] == "unclaimed":
            assert r["dps"] is None, r["id"]
        elif r.get("pay_until"):
            done = r["pay_until"] < COLLECTED_ON
            assert (r["status"] == "paid") == done, f"{r['id']}: {r['status']} при сроке выплаты {r['pay_until']}"


def test_shares_identity():
    s = load("shares")
    assert close(v(s["outstanding_mln"]), v(s["issued"]) - v(s["treasury"]), 1e-9)


def test_ltm_revenue_and_leverage():
    acc, b = load("accounting")["periods"], load("balance")
    year = {r["year"]: r for r in load("history")["annual"]}[2025]
    ltm = v(year["revenue"]) - v(acc["2025H1"]["revenue"]) + v(acc["2026H1"]["revenue"])
    assert close(v(b["revenue_ltm"]), ltm, 0.001)
    lev = v(b["net_debt"]) / v(b["ebitda_rep_ltm"])
    assert close(lev, 1.08, 0.005)
    assert close(lev, v(b["net_debt_to_ebitda"]), 0.001)


def test_nwc_definitions():
    b = load("balance")
    c = {k: (v(x) if "v" in x else x) for k, x in b["nwc_components"].items()}
    trade = c["inventories"] + c["receivables_and_advances"] - c["trade_payables"]
    assert close(v(b["nwc_trade"]), trade, 1e-9)
    like = trade - c["other_taxes_payable"] - c["income_tax_payable"] - c["contract_liabilities_st"]
    assert close(v(b["nwc_magnit_like"]), like, 1e-9)
    op = c["operating_other_liabilities"]
    nwc_op = (trade + c["vat_other_taxes_receivable"] - c["contract_liabilities_st"]
              - sum(v(x) for x in op.values()))
    assert close(v(b["nwc_op"]), nwc_op, 1e-9)
    # определение книги A-W1 — всё, что проходит через операционный поток
    assert v(b["nwc"]) == v(b["nwc_op"])


def test_network_consistency():
    n = load("network")
    periods = sorted(n["area_end"], key=period_index)
    assert periods[0] == "2023H2" and periods[-1] == ANCHOR_DATE[:4] + "H1"
    for p in periods:
        fmt = n["area_end_by_format"][p]
        assert close(sum(v(x) for x in fmt.values()), v(n["area_end"][p]), 1e-6), p
    for prev, p in zip(periods, periods[1:]):
        net = v(n["area_end"][p]) - v(n["area_end"][prev])
        assert close(v(n["net_added"][p]), net, 1e-6), p
        assert v(n["gross_opened_hist"][p]) >= v(n["net_added"][p]), p
    by = n["by_format_2026H1"]
    assert close(sum(v(x["area"]) for x in by.values()), v(n["area_end"]["2026H1"]), 1e-6)
    assert sum(v(x["stores"]) for x in by.values()) == v(n["stores_end"]["2026H1"])


def test_bridge_lines_and_signs():
    lines = {line["key"]: v(line["amount"]) for line in load("bridge")["lines"]}
    assert set(lines) == {"accrued_interest", "nci_put", "lti_liability", "tax_provisions_net",
                          "income_tax_net", "deferred_consideration", "st_investments", "associates"}
    assert lines["st_investments"] <= 0 and lines["associates"] <= 0
    assert all(lines[k] >= 0 for k in ("accrued_interest", "nci_put", "lti_liability", "tax_provisions_net",
                                       "deferred_consideration"))
    # отложенное возмещение по сделкам — строка моста, а не «нераскрытое» вне моста
    assert "deferred_consideration" not in load("bridge")["outside_bridge"]
    # налог на прибыль к уплате − к возмещению: к уплате — та же ячейка, что в балансе
    payable = v(load("balance")["nwc_components"]["income_tax_payable"])
    assert close(payable - lines["income_tax_net"], 5.298, 1e-9)


def test_history_coverage():
    h = load("history")
    assert [r["year"] for r in h["annual"]] == list(range(2011, 2026))
    halves = [r["period"] for r in h["halves"]]
    assert halves[0] == "2018H1" and halves[-1] == "2026H1" and len(halves) == 17


def test_history_da_and_other_investing():
    """D&A в истории — двумя полями: `da_pct` с обесценением (как databook) и
    `da_excl_impairment_pct` без него (где МСФО раскрывает обесценение; в полугодиях
    отчётности — то же, что `accounting.memo.da_pct`); прочие инвестиционные платежи и
    поступления по финансовой аренде (ОДДС) — оттоком и притоком, неотрицательные."""
    h, memo = load("history"), load("accounting")["memo"]
    rows = h["annual"] + h["halves"]
    for r in rows:
        where = r.get("year") or r.get("period")
        excl = v(r["da_excl_impairment_pct"])
        if excl is not None:
            assert 0 < excl <= v(r["da_pct"]), where
        for key in ("other_investing_payments", "finance_lease_receipts"):
            x = v(r[key])
            assert x is None or x >= 0, (where, key)
    halves = {r["period"]: r for r in h["halves"]}
    for p, m in memo.items():
        assert close(v(halves[p]["da_excl_impairment_pct"]), v(m["da_pct"]), 1e-9), p
    assert v(halves["2026H1"]["other_investing_payments"]) > 0


def builder_module():
    spec = importlib.util.spec_from_file_location("build_facts", BUILDER)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    return builder


@pytest.mark.primary
@pytest.mark.filterwarnings("ignore:DrawingML support is incomplete")
def test_builder_reproduces_facts(tmp_path):
    """Сборщик `ops/tools/build_facts.py` на первичке воспроизводит `data/facts/*.json` (кроме
    `actuals.json`) и `data/calendar.json` байт в байт (перевод строки — LF, как их хранит git).

    Метка `primary`: без openpyxl или первички рядом тест пропускается с причиной
    (`tests/conftest.py`) — так на раннерах GitHub; на ноутбуке с первичкой он идёт в любом
    прогоне, в том числе в тестах такта перед push."""
    builder = builder_module()
    written = builder.write(builder.build(builder.sources()), tmp_path)
    got = {p.relative_to(tmp_path).as_posix(): p.read_bytes() for p in written}
    # actuals.json ведёт человек (факты отчётов); сборщик его не перезаписывает
    built = [name for name in FILES if name != "actuals"]
    want = {f"data/facts/{name}.json" for name in built} | {"data/calendar.json"}
    assert set(got) == want
    assert {p.name for p in FACTS.glob("*.json")} == {f"{name}.json" for name in FILES}
    for rel, data in sorted(got.items()):
        assert data == (ROOT / rel).read_bytes().replace(b"\r\n", b"\n"), f"{rel}: пересборка отличается"


def test_builder_never_overwrites_actuals(tmp_path):
    """`actuals.json` ведёт человек: сборщик пишет его шаблон, только если файла нет, внесённые
    факты не трогает и в записанные файлы его не включает. Поэтому факт журнала вносится
    только в `data/facts/actuals.json` — дублировать его в сборщик не нужно."""
    builder = builder_module()
    template = {"description": "формат", "actuals": []}
    out = {"accounting": {"as_of": ANCHOR_DATE}, "actuals": template, "_calendar": {"events": []}}
    facts = tmp_path / "data" / "facts"
    written = builder.write(out, tmp_path)
    assert (facts / "actuals.json").read_bytes() == builder.dump(template)
    assert facts / "actuals.json" not in written
    entered = builder.dump({"description": "формат", "actuals": [
        {"target": "x5.adj_margin", "period": "2026H2", "value": {"v": 0.061, "src": "файл › место"},
         "reported_on": "2027-03-19"}]})
    (facts / "actuals.json").write_bytes(entered)
    written = builder.write(out, tmp_path)
    assert (facts / "actuals.json").read_bytes() == entered
    assert {p.name for p in written} == {"accounting.json", "calendar.json"}


def test_undisclosed_values_carry_calc():
    """Нераскрытое — `{"v": null, "calc": "почему нет числа"}` (`data/facts/SCHEMA.md`):
    пояснение в `calc`, поля `note` у узла без числа нет."""
    for name in FILES:
        for path, _, node in walk(load(name)):
            if isinstance(node, dict) and "v" in node and node["v"] is None:
                assert node.get("calc") and "note" not in node, f"{name}{path}"


def test_peers_and_brokers():
    rows = load("peers")["rows"]
    assert rows[0]["ticker"] == "X5"
    assert {"MGNT", "LENT", "FIXR"} <= {r["ticker"] for r in rows}
    brokers = load("brokers")["rows"]
    assert all(r["broker"] and r["src"] for r in brokers)


def test_guidance_ranges():
    g = load("guidance")
    for key in ("revenue_growth", "capex_pct", "net_debt_to_ebitda"):
        lo, hi = v(g[key])
        assert lo < hi, key
    assert v(g["adj_margin_min"]) > 0 and v(g["openings_min"]) > 0


def test_actuals_entries_follow_the_format():
    """Факты журнала (вносит человек после отчёта): формат записи, цели, полугодия, источник."""
    targets = {"x5.adj_margin", "x5.revenue_growth"}
    seen = set()
    for i, a in enumerate(load("actuals")["actuals"]):
        where = f"actuals[{i}]"
        assert set(a) >= {"target", "period", "value", "reported_on"}, where
        assert a["target"] in targets, where
        assert len(a["period"]) == 6 and a["period"][4] == "H" and a["period"][5] in "12", where
        assert isinstance(a["value"]["v"], (int, float)) and (a["value"].get("src") or a["value"].get("calc")), where
        assert dt.date.fromisoformat(a["reported_on"]), where
        key = (a["target"], a["period"])
        assert key not in seen, f"{where}: повтор {key}"
        seen.add(key)


def test_core_reads_facts():
    F = load_facts(FACTS)
    assert not F.fixture
    cf = core_facts(F, load_book())
    assert cf.anchor == "2026H1"
    assert close(cf.margin_anchor, 0.057, 0.001)
    assert cf.shares_mln > 0 and cf.net_debt > 0
