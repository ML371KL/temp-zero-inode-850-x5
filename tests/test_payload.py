"""Выпуск `x5-v1` (model/payload.py): контракт docs/PAYLOAD.md, все блоки, проверки `validate`.

Два выпуска на малой полосе (40 прогонов, подвыборка 10 — в выпуск такие не
идут, контракт тот же): на входах книги и на живых входах из фикстур ISS/ЦБ с
первым как прошлым выпуском (атрибуция, журнал, плитки).
"""

from __future__ import annotations

import copy
import datetime as dt
import json
import math
import re
from pathlib import Path

import pytest

from indicators import live as live_mod
from indicators.http import Response
from model import payload as P
from model import uncertainty as U
from model.book import get_node, open_period
from model.checks import GATES, round_to_step

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures"
APP = ROOT / "web" / "app.js"
PAYLOAD_DOC = ROOT / "docs" / "PAYLOAD.md"
SMALL = {"FAST_DRAWS": 40, "FAST_SUBSAMPLE": 10}


def _fixture_getter(url, *, source, name, **kw):
    return Response(url, 200, (FIX / source / name).read_bytes(), "2026-09-28T10:00:00+00:00")


def fixture_live(previous=None) -> dict:
    """Живые входы из сохранённых ответов ISS и ЦБ за 28.09.2026."""
    return live_mod.collect_live(previous, today=dt.date(2026, 9, 28), getter=_fixture_getter)


def _small_build(**kw) -> dict:
    with pytest.MonkeyPatch.context() as mp:
        for k, v in SMALL.items():
            mp.setattr(U, k, v)
        return P.build_payload(fast=True, strict=False, n_workers=1, **kw)


@pytest.fixture(scope="module")
def book_release(book, facts):
    return _small_build(book=book, facts=facts)


@pytest.fixture(scope="module")
def live_release(book, facts, book_release):
    live = fixture_live(book_release)
    return _small_build(book=book, facts=facts, live=live, previous=book_release), live


def _doc_blocks() -> list[str]:
    text = PAYLOAD_DOC.read_text(encoding="utf-8")
    match = re.search(r"REQUIRED_TOP_LEVEL`\):\s*`([^`]+)`", text, re.S)
    assert match, "в docs/PAYLOAD.md нет списка REQUIRED_TOP_LEVEL"
    return [b.strip() for b in match.group(1).replace("\n", " ").split(",") if b.strip()]


# --------------------------------------------------------------- контракт


@pytest.mark.docs
def test_required_blocks_are_the_documented_ones():
    """Сверка документа: блоки контракта в docs/PAYLOAD.md — те же, что REQUIRED_TOP_LEVEL."""
    assert list(P.REQUIRED_TOP_LEVEL) == _doc_blocks()


@pytest.mark.docs
def test_frontend_reads_only_declared_blocks():
    """Витрина читает только блоки REQUIRED_TOP_LEVEL (обращения `d.<блок>` в web/app.js)."""
    if not APP.exists():
        pytest.skip("web/app.js ещё нет")
    used = set(re.findall(r"(?<![\w$.])d\.([A-Za-z_]\w*)", APP.read_text(encoding="utf-8")))
    assert used, "витрина не читает ни одного блока через d.<блок>"
    assert used <= set(P.REQUIRED_TOP_LEVEL), f"необъявленные блоки: {sorted(used - set(P.REQUIRED_TOP_LEVEL))}"


def test_book_release_follows_the_contract(book_release):
    assert list(book_release) == list(P.REQUIRED_TOP_LEVEL)
    assert book_release["schema"] == "x5-v1"
    assert P.validate(book_release) == []


def test_live_release_follows_the_contract(live_release):
    payload, _ = live_release
    assert P.validate(payload) == []


def test_every_block_is_filled(book, book_release):
    R = book_release
    n_axes = len(book["valuation"]["uncertainty"]["axes"])
    assert len(R["grid"]["cells"]) == 36
    assert set(R["layers"]) == {"analytical", "market_implied", "macro_neutral"}
    assert {"N", "H", "M"} <= set(R["worlds"]) and R["worlds"]["source"]
    assert all(R["regimes"][r]["target"] for r in ("stress", "floor", "partial", "full"))
    assert R["regimes"]["history"] and R["regimes"]["annual_history"]
    assert all(R["capex_levels"][c]["maintenance"] for c in ("low", "base", "high"))
    years = sorted({int(p[:4]) for p in (book["meta"]["first_period"], book["meta"]["last_period"])})
    assert [a["year"] for a in R["paths"]["annual"]] == list(range(years[0], years[-1] + 1))
    assert R["paths"]["halves"][0]["period"] == book["meta"]["anchor_period"]
    assert R["debt"]["bonds"] and R["debt"]["bridge"]["lines"] and R["debt"]["wall"]
    rows = R["debt"]["bridge"]["rows_at_valuation"]
    assert math.fsum(r["amount"] for r in rows) == pytest.approx(R["debt"]["bridge"]["total"], abs=1e-6)
    assert R["dividends"]["register"] and R["dividends"]["history"] and R["dividends"]["model"]
    assert R["history"]["annual"] and R["history"]["halves"]
    assert len(R["reverse_dcf"]["rows"]) == len(book["valuation"]["reverse_dcf"]["axes"])
    assert len(R["judgements"]["rows"]) == n_axes
    swings = [r["swing"] for r in R["judgements"]["rows"]]
    assert swings == sorted(swings, reverse=True)
    contrib = R["uncertainty"]["contributions"]
    assert len(contrib) == n_axes and math.fsum(c["share"] for c in contrib) == pytest.approx(1.0, abs=1e-6)
    edges = R["uncertainty"]["histogram_bins"]
    centers = [a + R["headline"]["lambda"] * (b - a)
               for a, b in zip(R["fair_value"]["draws_low"], R["fair_value"]["draws_high"])]
    assert edges[0] <= min(centers) and max(centers) <= edges[-1]
    NR = R["next_report"]
    assert NR["period"] == open_period(book)
    assert len(NR["table"]) == len(book["valuation"]["next_report"]["demo_values"])
    assert NR["expectation"]["margin"] and len(NR["benchmarks"]) == 3
    assert R["calendar"]["events"] and R["checks"]["invariants"] and len(R["checks"]["gates"]) == len(GATES)
    assert all(i["ok"] for i in R["checks"]["invariants"])
    assert {f["name"] for f in R["checks"]["flags"]} == {"book_update", "dividend_register",
                                                       "report_fact", "price_fallback"}
    closing = NR["closing"]
    assert closing == {"date": P.closing_event(NR["period"])["date"],
                       "title": P.closing_event(NR["period"])["title"],
                       "confirmed": P.closing_event(NR["period"])["confirmed"], "published": False}
    assert max(e["date"] for e in NR["events"]) == closing["date"]
    assert any(e["date"] == closing["date"] and e["kind"] == "ifrs" for e in NR["events"])
    assert len(R["inputs"]["rows"]) == 5 and R["book"]["key_judgements"]
    assert len(R["fair_value"]["by_lambda"]) == 21


# Край п. 8 аудита 30.09.2026: годовое МСФО за 2П 2026 вышло (≈19.03.2027), а книга ещё
# не закрыла полугодие. Закрывающее МСФО — по `covers` календаря и по всему календарю;
# МСФО за 1 кв. 2027 закрывающим 2П 2026 не становится.
REPORT_DATES = [
    # дата оценки, закрывающее МСФО, вышло ли оно, последнее событие окна
    ("2026-09-29", "2027-03-19", False, "2027-03-19"),
    ("2026-10-20", "2027-03-19", False, "2027-03-19"),
    ("2026-11-01", "2027-03-19", False, "2027-03-19"),
    ("2027-03-18", "2027-03-19", False, "2027-03-19"),
    ("2027-03-19", "2027-03-19", True, None),
    ("2027-03-20", "2027-03-19", True, None),
    ("2027-03-22", "2027-03-19", True, None),
]


@pytest.mark.parametrize("day, date, published, last", REPORT_DATES)
def test_closing_report_of_the_open_half(day, date, published, last):
    v = dt.date.fromisoformat(day)
    events, closing = P.report_events("2026H2", v)
    assert closing["date"] == date and closing["published"] is published
    assert "2026 г. (МСФО)" in closing["title"]
    assert all(e["date"] >= day for e in events)
    if published:
        # окно — обычное: 12 месяцев календаря; «закрывающего» МСФО в нём нет
        assert max(e["date"] for e in events) > "2027-04-29"
        assert not any(e["kind"] == "ifrs" and e.get("covers") == "2026H2" and e["date"] > date
                       for e in events)
    else:
        assert max(e["date"] for e in events) == last
    q1 = [e for e in events if e["kind"] == "ifrs" and "1 кв." in e["title"]]
    assert all(e.get("covers") is None for e in q1)


def _calendar_copy(tmp_path, name, change) -> Path:
    """Копия календаря репозитория с правкой событий `change(events)`."""
    raw = json.loads((ROOT / "data" / "calendar.json").read_text(encoding="utf-8"))
    raw["events"] = change(raw["events"])
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return path


def test_closing_report_without_covers_or_without_the_event(tmp_path, monkeypatch):
    v = dt.date(2027, 3, 22)
    # календарь прежнего формата (без covers) — первое МСФО после конца полугодия по всему
    # календарю, без нижней границы по дате оценки: вышедшее годовое МСФО видно
    monkeypatch.setattr(P, "CALENDAR", _calendar_copy(
        tmp_path, "bare", lambda ev: [{k: x for k, x in e.items() if k != "covers"} for e in ev]))
    events, closing = P.report_events("2026H2", v)
    assert closing["date"] == "2027-03-19" and closing["published"] is True
    # годового МСФО в календаре нет — закрывающего нет, МСФО за 1 кв. (covers: null) им не
    # становится; окно — обычное
    monkeypatch.setattr(P, "CALENDAR", _calendar_copy(
        tmp_path, "no-fy", lambda ev: [e for e in ev if e.get("covers") != "2026H2"]))
    events, closing = P.report_events("2026H2", v)
    assert closing is None and max(e["date"] for e in events) > "2027-04-29"
    assert P.closing_event("2026H2") is None
    assert P.closing_event(None) is None
    # у события календаря covers доходит до выпуска; у не-МСФО поля нет
    monkeypatch.setattr(P, "CALENDAR", _calendar_copy(tmp_path, "same", lambda ev: ev))
    rows = P._events(dt.date(2026, 9, 29))
    assert all(("covers" in e) == (e["kind"] == "ifrs") for e in rows)


def test_live_release_uses_live_price_and_observations(book, live_release):
    payload, live = live_release
    price = live["price"]
    assert payload["market"]["price"] == price["value"]
    assert payload["market"]["price_status"] == "live"
    want = max(dt.date.fromisoformat(price["date"]), dt.date.fromisoformat(book["meta"]["date"]))
    assert payload["meta"]["valuation_date"] == want.isoformat()
    assert payload["live"]["price"]["last_accepted"] == price["last_accepted"]
    assert payload["live"]["curve"]["shift_bp"].keys() == {"5", "10"}
    assert {t["id"] for t in payload["indicators"]["tiles"]} >= {"x5.price", "cbr.key_rate",
                                                                   "ofz.5y", "ofz.10y"}
    assert all(len(t["history"]) <= P.TILE_POINTS for t in payload["indicators"]["tiles"])
    peers = {r["ticker"]: r for r in payload["market"]["peers"]["rows"]}
    assert peers["X5"]["price"] == price["value"] and peers["MGNT"]["price"] is not None
    assert payload["market"]["price_history"]
    quoted = {b["isin"] for b in live["bonds"]}
    assert any(b["isin"] in quoted for b in payload["debt"]["bonds"])


def test_valuation_date_is_never_before_the_book_date(book):
    old = {"price": {"value": 1700.0, "date": "2026-01-05", "status": "fallback",
                     "accepted": False, "reason": "тест"}}
    got = P.live_inputs(book, old)
    assert got["valuation_date"] == dt.date.fromisoformat(book["meta"]["date"])
    assert got["status"] == "fallback" and got["market_price"] == 1700.0
    none = P.live_inputs(book, None)
    assert none["valuation_date"].isoformat() == book["meta"]["valuation_date"]
    assert none["market_price"] == float(book["meta"]["market_price"])


# --------------------------------------------------------- печать и полоса


def test_printed_numbers_are_rounded_exact_ones(book_release):
    H, F = book_release["headline"], book_release["fair_value"]
    step = H["print_step"]
    assert H["printed_central"] == round_to_step(H["central"], step)
    assert H["printed_band"] == [round_to_step(x, step) for x in H["band"]]
    assert F["printed"]["central"] == round_to_step(F["central"], step)


def test_slider_rule_reproduces_headline_from_release_draws(book_release):
    """Заголовок — статистика прогонов в том виде, в каком они в выпуске."""
    H, F = book_release["headline"], book_release["fair_value"]
    s = U.band_stats(F["draws_low"], F["draws_high"], H["lambda"], H["market_price"])
    half_cent = 0.0051                 # заголовок — до копейки
    assert abs(s["median"] - H["central"]) <= half_cent
    assert abs(s["p10"] - H["band"][0]) <= half_cent and abs(s["p90"] - H["band"][1]) <= half_cent
    assert s["p_below"] == H["p_central_below_market"]
    row = next(r for r in F["by_lambda"] if abs(r["lambda"] - H["lambda"]) < 1e-12)
    assert abs(row["median"] - H["central"]) <= half_cent
    assert row["point"] == pytest.approx(F["central"], abs=half_cent)
    assert all(round(x, 1) == x for x in F["draws_low"] + F["draws_high"])


def test_center_ev_maps_the_median(book_release):
    ce = book_release["fair_value"]["center_ev"]
    m = book_release["market"]
    assert ce["v_star"] == pytest.approx(m["market_ev"], rel=1e-9)
    assert ce["gap_median"] == pytest.approx(ce["v0_median"] / ce["v_star"] - 1.0, abs=1e-8)


# ------------------------------------------------------------ validate


def _broken(payload, fn):
    p = copy.deepcopy(payload)
    fn(p)
    return P.validate(p)


def test_validate_catches_breakage(book_release):
    R = book_release
    assert any("нет блоков" in x for x in _broken(R, lambda p: p.pop("journal")))
    assert any("необъявленные" in x for x in _broken(R, lambda p: p.update(extra=1)))
    assert any("неконечные" in x for x in _broken(
        R, lambda p: p["layers"]["analytical"].update(v0=float("nan"))))
    assert any("печать" in x for x in _broken(
        R, lambda p: p["headline"].update(printed_central=p["headline"]["printed_central"] + 50)))
    assert any("draws" in x for x in _broken(R, lambda p: p["fair_value"]["draws_low"].pop()))
    assert any("by_lambda" in x for x in _broken(R, lambda p: p["fair_value"]["by_lambda"].pop()))
    assert any("payload_sha256" in x for x in _broken(
        R, lambda p: p["market"].update(price=p["market"]["price"] + 1)))
    assert any("размер" in x for x in _broken(
        R, lambda p: p["history"].update(pad="я" * P.PAYLOAD_MAX_BYTES)))
    assert any("клеток" in x for x in _broken(R, lambda p: p["grid"]["cells"].pop()))
    assert any("статистикой прогонов" in x for x in _broken(
        R, lambda p: p["fair_value"]["draws_high"].__setitem__(0, 1e6)))


def test_derived_values_match_their_sources(book_release, live_release):
    """Производные выпуска сходятся с прогонами, слоями и сеткой (`derived_problems`): на
    выпуске книги и на живом (другая цена рынка) — ни одного нарушения."""
    assert P.derived_problems(book_release) == []
    assert P.derived_problems(live_release[0]) == []


def _heaviest_cell(p):
    return max(p["grid"]["cells"], key=lambda c: c["p_analytical"])


def _set_central(p, value):
    p["fair_value"]["central"] = value
    p["fair_value"]["printed"]["central"] = round_to_step(value, p["headline"]["print_step"])


DERIVED_BREAKS = [
    ("p-below-99", lambda p: p["headline"].update(
        p_central_below_market=0.01 if p["headline"]["p_central_below_market"] == 0.99 else 0.99),
     "headline.p_central_below_market"),
    ("p-below-one-draw", lambda p: p["headline"].update(
        p_central_below_market=p["headline"]["p_central_below_market"] + 1 / p["headline"]["draws"]),
     "headline.p_central_below_market"),
    ("layer-price", lambda p: p["layers"]["analytical"].update(price=99999.0), "layers.analytical.price"),
    ("mean", lambda p: p["headline"].update(mean=p["headline"]["mean"] * 2), "headline.mean"),
    ("by-lambda-p-below", lambda p: p["fair_value"]["by_lambda"][10].update(p_below=0.99),
     "by_lambda[λ=0.50].p_below"),
    ("by-lambda-median", lambda p: p["fair_value"]["by_lambda"][3].update(
        median=p["fair_value"]["by_lambda"][3]["median"] + 1), "by_lambda[λ=0.15].median"),
    ("by-lambda-point", lambda p: p["fair_value"]["by_lambda"][3].update(
        point=p["fair_value"]["by_lambda"][3]["point"] + 1), "by_lambda[λ=0.15].point"),
    ("market-price", lambda p: p["market"].update(price=99999.0), "market.price"),
    ("point-central", lambda p: _set_central(p, 9999.0), "fair_value.central ="),
    ("gap-median", lambda p: p["fair_value"]["center_ev"].update(gap_median=5.0), "gap_median"),
    ("v0-median", lambda p: p["fair_value"]["center_ev"].update(
        v0_median=p["fair_value"]["center_ev"]["v0_median"] * 1.05), "center_ev.v0_median"),
    ("invariant-ok", lambda p: p["checks"]["invariants"][0].update(ok=False), "checks.invariants"),
    ("cell-ev", lambda p: _heaviest_cell(p).update(ev=_heaviest_cell(p)["ev"] * 2), "Σ p·EV"),
    ("layer-equity", lambda p: p["layers"]["macro_neutral"].update(
        equity=p["layers"]["macro_neutral"]["equity"] + 100), "layers.macro_neutral.equity"),
    ("pv-column", lambda p: p["layers"]["market_implied"].update(
        pv_fcff=p["layers"]["market_implied"]["pv_fcff"] + 1), "layers.market_implied.v0"),
    ("bridge-total", lambda p: p["debt"]["bridge"].update(total=p["debt"]["bridge"]["total"] + 1),
     "debt.bridge.total"),
    ("layer-column-missing", lambda p: p["layers"]["analytical"].pop("pv_fcff"),
     "layers.analytical: v0, d, equity"),
    ("contribution-share", lambda p: p["uncertainty"]["contributions"][0].update(
        share=p["uncertainty"]["contributions"][0]["share"] + 0.01),
     "uncertainty.contributions: Σ share"),
    ("contribution-rank-corr", lambda p: p["uncertainty"]["contributions"][1].update(
        rank_corr=p["uncertainty"]["contributions"][1]["rank_corr"] * 1.1),
     "rank_corr² / Σ rank_corr²"),
]


@pytest.mark.parametrize("fn, words", [pytest.param(fn, words, id=name)
                                       for name, fn, words in DERIVED_BREAKS])
def test_validate_catches_derived_breakage_under_a_fresh_hash(book_release, fn, words):
    """Подмена производного с пересчитанными хэшем и размером (как если бы её собрал
    код сборки) — нарушение контракта: P ниже рынка 1 % → 99 % при тех же прогонах, цена
    слоя → 99 999, строка таблицы λ, точка, пара «модель — рынок», тождества слоёв,
    сетка, флаг инварианта."""
    p = copy.deepcopy(book_release)
    fn(p)
    P._seal(p, [])
    problems = P.validate(p)
    assert not any("payload_sha256" in x or "meta.bytes" in x for x in problems), problems
    assert any(words in x for x in problems), problems


@pytest.mark.ci_only
def test_derived_checks_hold_at_another_lambda_and_price(book, facts, book_release):
    """Допуски сверок — от округления выпуска, а не от чисел книги: λ вне сетки таблицы и
    цена рынка у медианы (P ниже рынка не 0 и не 1) — ни одного нарушения."""
    B = copy.deepcopy(book)
    B["joint"]["lambda"] = 0.35
    price = round(book_release["headline"]["central"], 1)
    live = {"price": {"value": price, "date": book["meta"]["date"], "status": "fallback",
                      "accepted": False, "reason": "тест"}}
    R = _small_build(book=B, facts=facts, live=live)
    assert R["headline"]["lambda"] == 0.35 and R["market"]["price"] == price
    assert 0 < R["headline"]["p_central_below_market"] < 1
    assert P.derived_problems(R) == []
    assert P.validate(R) == []


def test_hash_ignores_time_and_links_but_not_content(book_release):
    R = copy.deepcopy(book_release)
    digest = P.content_digest(R)
    R["meta"]["generated_at"] = "2030-01-01T00:00:00Z"
    R["meta"]["previous_sha256"] = "f" * 64
    R["live"]["fetched_at"] = "2030-01-01T00:00:00+00:00"
    R["changes"] = {"vs_previous": {"rows": [1, 2, 3]}}
    for e in R["journal"]["entries"]:
        e["release_sha"] = "x"
    R["journal"]["releases"] = {"x": {"book_version": "0.1", "generated_at": None}}
    assert P.content_digest(R) == digest
    R["headline"]["mean"] += 0.01
    assert P.content_digest(R) != digest


def test_headline_protection(book_release, tmp_path):
    R = copy.deepcopy(book_release)
    empty = tmp_path / "none.yaml"
    empty.write_text("notes: []\n", encoding="utf-8")
    ref = {"printed_central": R["headline"]["printed_central"] / 2,
           "v0_median": R["fair_value"]["center_ev"]["v0_median"],
           "book_version": R["meta"]["book_version"], "facts_date": R["meta"]["facts_date"],
           "fast": False}
    R["meta"]["fast"] = False
    R["changes"] = {"vs_previous": {"reference": ref}}
    assert any("скачок" in x for x in P.headline_problems(R, notes_path=empty))
    # новая книга объясняет скачок
    R2 = copy.deepcopy(R)
    R2["changes"]["vs_previous"]["reference"]["book_version"] = "0.9"
    assert P.headline_problems(R2, notes_path=empty) == []
    # действующая записка с верным предсказанием — объясняет; неверным или просроченной — нет
    today = dt.date(2026, 9, 28)
    note = tmp_path / "notes.yaml"
    central = R["headline"]["central"]

    def write(expected, until):
        note.write_text(json.dumps({"notes": [{"date": "2026-09-28", "reason": "тест",
                                               "expected_median": expected, "tolerance_pct": 5,
                                               "valid_until": until}]}), encoding="utf-8")

    write(central * 1.01, "2026-12-31")
    assert P.headline_problems(R, notes_path=note, today=today) == []
    write(central * 1.5, "2026-12-31")
    assert P.headline_problems(R, notes_path=note, today=today)
    write(central, "2026-09-01")
    assert P.headline_problems(R, notes_path=note, today=today)
    # V0 медианы — свой порог
    R3 = copy.deepcopy(R)
    R3["changes"]["vs_previous"]["reference"].update(
        printed_central=R["headline"]["printed_central"],
        v0_median=R["fair_value"]["center_ev"]["v0_median"] * 1.2)
    assert any("V0" in x for x in P.headline_problems(R3, notes_path=empty))


def test_release_notes_template_and_rules(tmp_path):
    assert P.release_notes_problems() == [], "шаблон release_notes.yaml должен быть годен"
    bad = tmp_path / "bad.yaml"
    bad.write_text("notes:\n  - {date: '2026-09-28', reason: x}\n", encoding="utf-8")
    problems = P.release_notes_problems(bad)
    assert any("expected_median" in x for x in problems) and any("valid_until" in x for x in problems)


# --------------------------------------------------- журнал и изменения


def test_new_journal_entries_carry_their_release_sha(book, book_release):
    entries = book_release["journal"]["entries"]
    period = open_period(book)
    assert {(e["target"], e["period"]) for e in entries} == {("x5.adj_margin", period),
                                                           ("x5.revenue_growth", period)}
    assert all(e["release_sha"] == book_release["meta"]["payload_sha256"] for e in entries)
    margin = next(e for e in entries if e["target"] == "x5.adj_margin")
    assert margin["forecast"] == pytest.approx(book_release["next_report"]["expectation"]["margin"])


def test_journal_is_carried_byte_for_byte(book_release, live_release):
    payload, _ = live_release
    from model.journal import canonical

    was = {e["id"]: canonical(e) for e in book_release["journal"]["entries"]}
    now = {e["id"]: canonical(e) for e in payload["journal"]["entries"]}
    assert was == now


def test_journal_names_the_book_of_each_recording_release(book_release, live_release):
    """Книга выпуска, записавшего прогноз, — в `journal.releases` (записи не трогаются)."""
    assert book_release["journal"]["releases"] == {}, "прошлого выпуска нет — и карта пуста"
    payload, _ = live_release
    sha = book_release["meta"]["payload_sha256"]
    assert {e["release_sha"] for e in payload["journal"]["entries"]} == {sha}
    assert payload["journal"]["releases"] == {
        sha: {"book_version": book_release["meta"]["book_version"],
              "generated_at": book_release["meta"]["generated_at"]}}


def test_journal_releases_reach_back_through_the_previous_release():
    """Позапрошлый выпуск — по ссылке прошлого (`changes.vs_previous`); карта прошлого
    журнала переносится; выпуски, на которые записи не ссылаются, в карту не идут."""
    entries = [{"id": "a", "release_sha": "old"}, {"id": "b", "release_sha": "older"},
               {"id": "c", "release_sha": None}]
    previous = {"meta": {"payload_sha256": "prev", "book_version": "1.1", "generated_at": "t1"},
                "changes": {"vs_previous": {"previous_sha": "old", "previous_generated_at": "t0",
                                            "reference": {"book_version": "1.0"}}},
                "journal": {"entries": entries,
                            "releases": {"older": {"book_version": "0.9", "generated_at": "t-1"}}}}
    got = P.journal_releases({"entries": entries}, previous, previous["journal"])
    assert got == {"old": {"book_version": "1.0", "generated_at": "t0"},
                   "older": {"book_version": "0.9", "generated_at": "t-1"}}
    assert P.journal_releases({"entries": entries}, None) == {}


def test_journal_releases_find_the_book_in_release_history():
    """Выпуск, до которого цепочка прошлых выпусков не дотягивается (записал прогноз
    позапозапрошлый выпуск, а прошлый собран кодом без карты), — по строкам публикаций
    `history.json`; откаты и переоткрытия книгу не дают."""
    entries = [{"id": "a", "release_sha": "first"}]
    previous = {"meta": {"payload_sha256": "third", "book_version": "1.1", "generated_at": "t3"},
                "changes": {"vs_previous": {"previous_sha": "second", "previous_generated_at": "t2",
                                            "reference": {"book_version": "1.1"}}},
                "journal": {"entries": entries}}
    assert P.journal_releases({"entries": entries}, previous, previous["journal"]) == {}
    history = [
        {"event": "publish", "payload_sha256": "first", "book_version": "1.0", "generated_at": "t1"},
        {"event": "rollback", "payload_sha256": "first", "book_version": "0.0"},
        {"event": "publish", "payload_sha256": "second", "book_version": "1.1", "generated_at": "t2"},
    ]
    got = P.journal_releases({"entries": entries}, previous, previous["journal"], history)
    assert got == {"first": {"book_version": "1.0", "generated_at": "t1"}}


def test_terminal_share_follows_from_the_layer_columns(book_release):
    """Доля терминала — чистая: (PV терминала − его вычеты) / V0; вычеты терминала — часть
    всех вычетов финансирования."""
    for name, L in book_release["layers"].items():
        tf = L["pv_terminal_financing"]
        assert -1e-6 <= tf <= L["pv_financing"] + 1e-6, name
        assert (L["pv_terminal"] - tf) / L["v0"] == pytest.approx(L["terminal_share"], rel=1e-6), name


def test_wall_note_reaches_the_release(book_release, facts):
    note = (facts.data.get("debt_register") or {}).get("wall_note")
    assert book_release["debt"]["wall_note"] == (note if isinstance(note, str) else None)
    if all(w["banks"] is None for w in book_release["debt"]["wall"]):
        assert book_release["debt"]["wall_note"], "график кредитов не раскрыт — нужна подпись стены"


def test_changes_add_up_to_the_point_move(book, facts, book_release, live_release):
    payload, _ = live_release
    ch = payload["changes"]["vs_previous"]
    assert ch["previous_sha"] == book_release["meta"]["payload_sha256"]
    assert [r["component"] for r in ch["rows"]] == ["residual", "market_price", "valuation_date"]
    total = payload["fair_value"]["central"] - book_release["fair_value"]["central"]
    assert math.fsum(r["rub"] for r in ch["rows"]) == pytest.approx(ch["total_rub"], abs=1e-6)
    assert ch["total_rub"] == pytest.approx(total, abs=0.02)
    assert abs(ch["rows"][0]["rub"]) < 0.02, "книга, факты и код те же — остаток нулевой"
    # Цена рынка входит в точку только через казначейский пакет (продаётся по рынку):
    # не больше доли пакета в акциях × сдвиг цены; нет пакета — ровно ноль.
    from model.facts import core_facts
    cf = core_facts(facts, book)
    n = getattr(cf, "treasury_mln", 0.0) or 0.0
    move = abs(payload["market"]["price"] - book_release["market"]["price"])
    assert abs(ch["rows"][1]["rub"]) <= move * n / (cf.shares_mln + n) + 1e-9


# ---------------------------------------------------------- строгая сборка


def test_strict_build_stops_on_an_unexplained_gate(book, facts):
    tight = copy.deepcopy(book)
    tight["checks"]["margin_range"] = [0.5, 0.6]          # сработает во всех клетках
    with pytest.raises(P.ReleaseBlocked) as err:
        P.build_payload(book=tight, facts=facts, fast=True, n_workers=1, explanations={})
    assert "margin_range" in str(err.value)
    assert get_node(book, "checks.margin_range") != [0.5, 0.6]


# ------------------------------------------- витрина: поля по аудиту 1.1


def test_next_dividend_is_the_model_payout_of_its_pay_half(book_release, grid):
    """Дивиденд модели «за 9 мес.» — в полугодии выплаты (отсечка в январе → 1П
    следующего года), а не в открытом полугодии, где модель по книге не платит."""
    from model.grid import expected_path

    nxt = book_release["dividends"]["next_expected"]
    assert nxt["record_date_est"] is None or dt.date.fromisoformat(nxt["record_date_est"])
    day = dt.date.fromisoformat(nxt["record_date_est"])
    assert nxt["pay_period"] == f"{day.year}H{1 if day.month <= 6 else 2}"
    halves = {h["period"]: h for h in expected_path(grid)}
    want = halves[nxt["pay_period"]]["dividends"] * 1000.0 / grid.ctx.facts.shares_mln
    assert nxt["dps_model"] == pytest.approx(want, rel=1e-6)
    assert nxt["dps_model"] > 0


def test_dividend_history_runs_forward_in_time(book_release):
    hist = book_release["dividends"]["history"]
    keys = [P._dividend_order(h) for h in hist]
    assert keys == sorted(keys) and all(h.get("label") for h in hist)


def test_broker_median_is_the_documented_subset(book_release):
    B = book_release["market"]["brokers"]
    report = dt.date.fromisoformat(B["report_date"])
    for r in B["rows"]:
        assert r["after_report"] == (dt.date.fromisoformat(r["date"]) >= report)
    picked = sorted(r["target"] for r in B["rows"] if r["in_median"])
    assert B["median_n"] == len(picked) > 0
    n = len(picked)
    median = picked[n // 2] if n % 2 else (picked[n // 2 - 1] + picked[n // 2]) / 2
    assert median == pytest.approx(B["median"])


def test_market_multiples_share_a_base(book_release):
    m, ce = book_release["market"], book_release["fair_value"]["center_ev"]
    x5 = next(r for r in m["peers"]["rows"] if r["ticker"] == "X5")
    assert m["ev_ebitda_ltm"] == pytest.approx(x5["ev_ebitda"])
    assert x5["ev"] == pytest.approx(x5["market_cap"] + x5["net_debt"] + x5["dividends_after_balance"])
    ntm = ce["ebitda_ntm"]
    assert ce["ev_ebitda_ntm_market"] == pytest.approx(ce["v_star"] / ntm)
    assert ce["ev_ebitda_ntm_median"] / ce["ev_ebitda_ntm_market"] == pytest.approx(1 + ce["gap_median"])
    assert m["equity_share_of_ev"] == pytest.approx(1 - m["claims"] / m["market_ev"])


def test_bond_outstanding_comes_from_the_register(book_release, facts):
    reg = {b["isin"]: b for b in facts.data["debt_register"]["bonds"]}
    debt = book_release["debt"]
    for b in debt["bonds"]:
        assert b["outstanding"] == P._latest_key(reg[b["isin"]], "outstanding")
    anchor = debt["anchor"]
    bonds = math.fsum(b["outstanding_anchor"] or 0.0 for b in debt["bonds"])
    parts = bonds + debt["bank_loans"]["total"] + (anchor["leasing"] or 0.0)
    assert abs(parts - anchor["total_debt"]) < 1.0, "облигации, банки и лизинг на дату якоря = общий долг"


def test_bridge_lists_add_up(book_release):
    br = book_release["debt"]["bridge"]
    assert math.fsum(r["amount"] for r in br["ev_rows"]) == pytest.approx(br["v0"], abs=1e-4)
    assert math.fsum(r["amount"] for r in br["rows_at_valuation"]) == pytest.approx(br["total"], abs=1e-4)
    assert br["equity"] == pytest.approx(br["v0"] - br["total"], abs=1e-4)
    assert br["v0"] == pytest.approx(book_release["layers"]["analytical"]["v0"])
    # Капитал + строки «капитал → цена» на (акции + казначейский пакет) даёт цену слоя.
    an, meta = book_release["layers"]["analytical"], book_release["meta"]
    total = br["equity"] + math.fsum(r["amount"] for r in br["equity_rows"])
    price = total * (1 - meta["governance_discount"]) * 1000 / (meta["shares_mln"] + br["treasury_mln"])
    assert price == pytest.approx(an["price"], rel=1e-6)
    for r in br["rows_at_valuation"]:
        assert not re.search(r"\d{4}-\d{2}-\d{2}", r["label"]), "даты подписей — ДД.ММ.ГГГГ"


def test_anchor_year_marks_its_forecast_only_flows(book_release, book):
    rows = book_release["paths"]["annual"]
    anchor_year = int(book["meta"]["anchor_period"][:4])
    first = next(r for r in rows if r["year"] == anchor_year)
    assert {"fcff", "ticket", "traffic", "dividends"} <= set(first["forecast_only"])
    assert not set(first["forecast_only"]) & set(first["fact"])
    assert all(not r["forecast_only"] for r in rows if r["year"] != anchor_year)


def test_regime_titles_and_key_judgement_codes_follow_the_book(book_release):
    assert book_release["regimes"]["floor"]["title"] == "Дно"
    # Текст книги живёт только в ASSUMPTIONS-BOOK.md (книга 1.1.1, [42] аудита раунда 2).
    text = (ROOT / "data" / "assumptions" / "ASSUMPTIONS-BOOK.md").read_text(encoding="utf-8")
    parts = re.split(r"\n(?=### )", text)
    for jid, path, _, _ in P.KEY_JUDGEMENTS:
        sec = [s for s in parts if re.match(rf"### {re.escape(jid)}[.\s]", s)]
        assert sec, f"{jid}: нет раздела книги"
        assert ".".join(path.split(".")[:2]) in sec[0], f"{jid}: ключ {path} не из этого раздела"


def test_tiles_measure_change_on_the_full_series(live_release):
    payload, live = live_release
    tiles = {t["id"]: t for t in payload["indicators"]["tiles"]}
    price = tiles["x5.price"]
    closes = [r for r in live["price_history"] if r["date"] < price["date"]]
    assert price["change_from"] == closes[-1]["date"]
    assert price["change"] == pytest.approx(price["value"] - closes[-1]["close"])
    assert price["min"]["value"] == min([r["close"] for r in closes] + [price["value"]])
    key = tiles["cbr.key_rate"]
    assert key["since"] and key["since"] == payload["live"]["key_rate"]["since"]
    assert key["since"] <= key["date"]
