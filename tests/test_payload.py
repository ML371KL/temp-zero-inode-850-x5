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


def test_required_blocks_are_the_documented_ones():
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
                                                       "price_fallback"}
    assert len(R["inputs"]["rows"]) == 5 and R["book"]["key_judgements"]
    assert len(R["fair_value"]["by_lambda"]) == 21


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


def test_hash_ignores_time_and_links_but_not_content(book_release):
    R = copy.deepcopy(book_release)
    digest = P.content_digest(R)
    R["meta"]["generated_at"] = "2030-01-01T00:00:00Z"
    R["meta"]["previous_sha256"] = "f" * 64
    R["live"]["fetched_at"] = "2030-01-01T00:00:00+00:00"
    R["changes"] = {"vs_previous": {"rows": [1, 2, 3]}}
    for e in R["journal"]["entries"]:
        e["release_sha"] = "x"
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


def test_changes_add_up_to_the_point_move(book_release, live_release):
    payload, _ = live_release
    ch = payload["changes"]["vs_previous"]
    assert ch["previous_sha"] == book_release["meta"]["payload_sha256"]
    assert [r["component"] for r in ch["rows"]] == ["residual", "market_price", "valuation_date"]
    total = payload["fair_value"]["central"] - book_release["fair_value"]["central"]
    assert math.fsum(r["rub"] for r in ch["rows"]) == pytest.approx(ch["total_rub"], abs=1e-6)
    assert ch["total_rub"] == pytest.approx(total, abs=0.02)
    assert abs(ch["rows"][0]["rub"]) < 0.02, "книга, факты и код те же — остаток нулевой"
    assert ch["rows"][1]["rub"] == 0.0, "цена рынка в точку не входит"


# ---------------------------------------------------------- строгая сборка


def test_strict_build_stops_on_an_unexplained_gate(book, facts):
    tight = copy.deepcopy(book)
    tight["checks"]["margin_range"] = [0.5, 0.6]          # сработает во всех клетках
    with pytest.raises(P.ReleaseBlocked) as err:
        P.build_payload(book=tight, facts=facts, fast=True, n_workers=1, explanations={})
    assert "margin_range" in str(err.value)
    assert get_node(book, "checks.margin_range") != [0.5, 0.6]
