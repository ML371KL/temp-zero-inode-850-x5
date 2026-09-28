"""`collect_live()` целиком на фикстурах ISS/ЦБ и CLI `python -m indicators.live`."""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from indicators import live  # noqa: E402
from indicators.http import FetchError, Response  # noqa: E402

FIX = ROOT / "tests" / "fixtures"
TODAY = date(2026, 9, 28)
PREVIOUS = {"meta": {"payload_sha256": "a" * 64},
            "market": {"price": 1808.5, "price_date": "2026-09-25", "price_status": "live"}}


@pytest.fixture(autouse=True)
def no_run_summary(monkeypatch):
    """Внутри Actions тесты не пишут в сводку самого прогона."""
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)


def getter_with(failures: dict | None = None):
    failures = failures or {}

    def getter(url, *, source, name, **kw):
        if name in failures:
            raise failures[name]
        return Response(url, 200, (FIX / source / name).read_bytes(), "2026-09-28T10:00:00+00:00")
    return getter


def test_collect_live_full_shape():
    result = live.collect_live(PREVIOUS, today=TODAY, getter=getter_with())
    assert result["schema"] == "x5-live-v1"
    assert result["run_date"] == "2026-09-28"
    assert result["errors"] == {}
    price = result["price"]
    assert price["accepted"] and price["status"] == "live"
    assert (price["value"], price["date"], price["time"], price["source"]) == \
        (1802.5, "2026-09-28", "12:45:05", "ISS TQBR")
    assert result["curve"]["nodes"] == {"1": 0.136441, "3": 0.156189, "5": 0.162421,
                                        "10": 0.167439}
    assert (result["key_rate"]["value"], result["key_rate"]["date"]) == (0.14, "2026-09-28")
    assert set(result["peers"]) == {"MGNT", "LENT", "FIXR", "OKEY"}
    assert result["peers"]["MGNT"] == {"price": 1628.5, "date": "2026-09-28",
                                       "time": "12:45:01", "kind": "last"}
    assert len(result["bonds"]) == 16
    assert len(result["price_history"]) == 253
    json.dumps(result, allow_nan=False)  # строгий JSON


def test_quotes_down_price_from_history_close():
    result = live.collect_live(PREVIOUS, today=TODAY, getter=getter_with(
        {"quotes_tqbr.json": FetchError("u", 503, "HTTP 503")}))
    price = result["price"]
    assert price["accepted"]
    assert (price["value"], price["date"], price["kind"]) == (1808.5, "2026-09-25", "history_close")
    assert "quotes" in result["errors"]
    assert result["peers"] == {}


def test_secondary_sources_down_do_not_stop_the_release():
    failures = {name: FetchError("u", 503, "HTTP 503") for name in
                ("zcyc.json", "key_rate_soap.xml", "key_rate_page.html", "emitter_securities.json")}
    result = live.collect_live(PREVIOUS, today=TODAY, getter=getter_with(failures))
    assert result["price"]["accepted"]
    assert result["curve"] is None and result["key_rate"] is None and result["bonds"] == []
    assert set(result["errors"]) == {"curve", "key_rate", "bonds"}


def test_parser_breakage_is_recorded_not_raised():
    def getter(url, *, source, name, **kw):
        body = b"<html>502</html>" if name == "zcyc.json" else (FIX / source / name).read_bytes()
        return Response(url, 200, body, "2026-09-28T10:00:00+00:00")
    result = live.collect_live(PREVIOUS, today=TODAY, getter=getter)
    assert result["curve"] is None and "curve" in result["errors"]


def test_iss_down_without_previous_is_a_failure():
    failures = {name: FetchError("u", None, "timeout") for name in
                ("quotes_tqbr.json", "history_X5_0.json")}
    with pytest.raises(live.PriceUnavailable):
        live.collect_live(None, today=TODAY, getter=getter_with(failures))


def test_iss_down_with_previous_falls_back():
    failures = {name: FetchError("u", None, "timeout") for name in
                ("quotes_tqbr.json", "history_X5_0.json")}
    result = live.collect_live(PREVIOUS, today=TODAY, getter=getter_with(failures))
    assert result["price"]["status"] == "fallback"
    assert result["price"]["value"] == 1808.5
    assert "timeout" in result["price"]["reason"]


def test_cli_writes_file_and_says_done(tmp_path, monkeypatch, capsys):
    real = live.collect_live
    monkeypatch.setattr(live, "collect_live",
                        lambda previous, today=None: real(previous, today=TODAY, getter=getter_with()))
    previous = tmp_path / "latest.json"
    previous.write_text(json.dumps(PREVIOUS), encoding="utf-8")
    out = tmp_path / "live.json"
    assert live.main(["--previous", str(previous), "--out", str(out)]) == 0
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["price"]["value"] == 1802.5
    assert capsys.readouterr().out.strip().splitlines()[-1].startswith("готово:")


def test_cli_price_failure_is_nonzero(tmp_path, monkeypatch, capsys):
    def boom(previous, today=None):
        raise live.PriceUnavailable("цена X5 не собрана; запасной цены нет")
    monkeypatch.setattr(live, "collect_live", boom)
    out = tmp_path / "live.json"
    assert live.main(["--out", str(out)]) == 1
    assert not out.exists()
    assert "ПРОВАЛ на шаге сбор живых входов" in capsys.readouterr().err


def test_run_summary_gets_fallback_price_source_failures_and_the_result(tmp_path, monkeypatch):
    """Сводка прогона: цена (здесь ЗАПАСНАЯ с причиной), отказы источников и
    итоговая строка шага; провал сбора — строка ПРОВАЛ."""
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    failures = {name: FetchError("u", None, "timeout") for name in
                ("quotes_tqbr.json", "history_X5_0.json", "zcyc.json")}
    real = live.collect_live
    monkeypatch.setattr(live, "collect_live", lambda previous, today=None: real(
        previous, today=TODAY, getter=getter_with(failures)))
    previous = tmp_path / "latest.json"
    previous.write_text(json.dumps(PREVIOUS), encoding="utf-8")
    assert live.main(["--previous", str(previous), "--out", str(tmp_path / "live.json")]) == 0
    lines = summary.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("- цена X5: ЗАПАСНАЯ 1808.5 ₽ от 2026-09-25 — цена X5 не собрана")
    assert "- ОТКАЗ ИСТОЧНИКА curve: FetchError: u: timeout" in lines
    assert lines[-1].startswith("- готово: живые входы записаны")

    def boom(previous, today=None):
        raise live.PriceUnavailable("цена X5 не собрана; запасной цены нет")
    monkeypatch.setattr(live, "collect_live", boom)
    assert live.main(["--out", str(tmp_path / "x.json")]) == 1
    assert summary.read_text(encoding="utf-8").splitlines()[-1] == \
        "- ПРОВАЛ на шаге сбор живых входов: цена X5 не собрана; запасной цены нет"


def test_cli_unreadable_previous_is_nonzero(tmp_path, capsys):
    bad = tmp_path / "latest.json"
    bad.write_text('{"meta": NaN}', encoding="utf-8")
    assert live.main(["--previous", str(bad), "--out", str(tmp_path / "x.json")]) == 1
    assert "ПРОВАЛ на шаге чтение прошлого выпуска" in capsys.readouterr().err
