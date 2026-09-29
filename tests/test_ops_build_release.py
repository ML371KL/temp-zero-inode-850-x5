"""`ops/build_release.py`: аргументы CLI, шаги, провалы с понятной причиной.

Ядро подменяется модулем-пустышкой: сборщик проверяется сам по себе, без
`model/` (его пишут параллельно; нет ядра — внятный провал)."""

from __future__ import annotations

import json
import math
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops import build_release  # noqa: E402

FAKE = "x5_fake_model_payload"


def payload(**extra) -> dict:
    body = {"schema": "x5-v1",
            "meta": {"payload_sha256": "b" * 64, "valuation_date": "2026-09-28",
                     "generated_at": "2026-09-28T16:55:00+00:00"},
            "market": {"price": 1802.5, "price_status": "live"},
            "headline": {"printed_central": 2150},
            "journal": {"entries": []}}
    body.update(extra)
    return body


@pytest.fixture
def fake_model(monkeypatch):
    """Ядро-пустышка: запоминает, с чем его позвали."""
    module = types.ModuleType(FAKE)
    module.calls = []
    module.result = payload()
    module.problems = []

    def build_payload(*, live=None, previous=None, journal=None, fast=False):
        module.calls.append({"live": live, "previous": previous, "journal": journal, "fast": fast})
        return module.result

    module.build_payload = build_payload
    module.validate = lambda p: list(module.problems)
    monkeypatch.setitem(sys.modules, FAKE, module)
    monkeypatch.setattr(build_release, "MODEL_MODULE", FAKE)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    return module


@pytest.fixture
def inputs(tmp_path):
    live = tmp_path / "live.json"
    live.write_text(json.dumps({"schema": "x5-live-v1", "price": {"value": 1802.5}}), encoding="utf-8")
    previous = tmp_path / "prev.json"
    previous.write_text(json.dumps(payload()), encoding="utf-8")
    journal = tmp_path / "journal.json"
    journal.write_text(json.dumps({"entries": [{"id": "j1"}]}), encoding="utf-8")
    return {"live": live, "previous": previous, "journal": journal, "out": tmp_path / "out" / "latest.json"}


def last_line(captured) -> str:
    return (captured.out + captured.err).strip().splitlines()[-1]


# ------------------------------------------------------------------ аргументы

@pytest.mark.parametrize("argv", [[], ["--live", "--book"], ["--book", "--live-file", "x"],
                                  ["--check", "a", "--live"]])
def test_exactly_one_source_is_required(argv):
    with pytest.raises(SystemExit) as caught:
        build_release.parse_args(argv)
    assert caught.value.code == 2


def test_defaults():
    args = build_release.parse_args(["--book"])
    assert Path(args.out) == build_release.DEFAULT_OUT
    assert not args.fast and args.previous is None and args.journal is None


# ------------------------------------------------------------------ провалы

def test_missing_model_fails_with_a_clear_step(monkeypatch, capsys):
    monkeypatch.setattr(build_release, "MODEL_MODULE", "x5_model_that_does_not_exist")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert build_release.main(["--book", "--fast"]) == 1
    line = last_line(capsys.readouterr())
    assert line.startswith("ПРОВАЛ на шаге импорт модели") and "ядра ещё нет" in line


def test_model_without_validate_fails(fake_model, capsys):
    del fake_model.validate
    assert build_release.main(["--book"]) == 1
    assert "нет функции validate" in last_line(capsys.readouterr())


def test_success_writes_compact_strict_json(fake_model, inputs, capsys):
    argv = ["--live-file", str(inputs["live"]), "--previous", str(inputs["previous"]),
            "--journal", str(inputs["journal"]), "--out", str(inputs["out"]), "--fast"]
    assert build_release.main(argv) == 0
    call = fake_model.calls[-1]
    assert call["live"]["price"]["value"] == 1802.5
    assert call["previous"]["meta"]["payload_sha256"] == "b" * 64
    assert call["journal"] == {"entries": [{"id": "j1"}]}
    assert call["fast"] is True
    text = inputs["out"].read_text(encoding="utf-8")
    assert text.endswith("\n") and "\n" not in text[:-1]
    assert json.loads(text) == fake_model.result
    line = last_line(capsys.readouterr())
    assert line.startswith("готово: выпуск bbbbbbbbbbbb") and "1802.5" in line


def test_contract_problems_block_the_write(fake_model, inputs, capsys):
    fake_model.problems = ["нет блока calendar"]
    assert build_release.main(["--book", "--out", str(inputs["out"])]) == 1
    assert not inputs["out"].exists()
    line = last_line(capsys.readouterr())
    assert line.startswith("ПРОВАЛ на шаге проверка контракта") and "calendar" in line


@pytest.mark.parametrize("bad", [math.nan, math.inf])
def test_non_finite_numbers_block_the_write(fake_model, inputs, capsys, bad):
    fake_model.result = payload(headline={"printed_central": bad})
    assert build_release.main(["--book", "--out", str(inputs["out"])]) == 1
    assert not inputs["out"].exists()
    assert "строгий JSON" in last_line(capsys.readouterr())


def test_oversized_release_is_refused(fake_model, inputs, capsys):
    fake_model.result = payload(padding="x" * (build_release.MAX_PAYLOAD_BYTES + 1))
    assert build_release.main(["--book", "--out", str(inputs["out"])]) == 1
    assert "потолке 500000" in last_line(capsys.readouterr())


def test_release_without_sha_is_refused(fake_model, inputs, capsys):
    fake_model.result = payload(meta={"valuation_date": "2026-09-28"})
    assert build_release.main(["--book", "--out", str(inputs["out"])]) == 1
    assert "payload_sha256" in last_line(capsys.readouterr())


def test_engine_exception_is_a_failed_step(fake_model, inputs, capsys):
    def broken(**kw):
        raise ZeroDivisionError("деление на ноль в клетке N/stress/high")
    fake_model.build_payload = broken
    assert build_release.main(["--book", "--out", str(inputs["out"])]) == 1
    line = last_line(capsys.readouterr())
    assert line.startswith("ПРОВАЛ на шаге сборка выпуска") and "ZeroDivisionError" in line


def test_unreadable_previous_is_a_failed_step(fake_model, inputs, capsys):
    inputs["previous"].write_text("<html>", encoding="utf-8")
    assert build_release.main(["--book", "--previous", str(inputs["previous"]),
                               "--out", str(inputs["out"])]) == 1
    assert last_line(capsys.readouterr()).startswith("ПРОВАЛ на шаге чтение прошлого выпуска")


def closed(actual):
    return {"entries": [{"id": "2026H2-x5.adj_margin", "target": "x5.adj_margin",
                         "period": "2026H2", "actual": actual, "errors": {"forecast": 0.001}}]}


@pytest.mark.parametrize("facts,ok", [({("x5.adj_margin", "2026H2"): 0.061}, True),
                                      ({("x5.adj_margin", "2026H2"): 0.058}, False),
                                      ({}, False)])
def test_closed_journal_entry_must_match_actuals(fake_model, inputs, monkeypatch, capsys,
                                                 facts, ok):
    """Факт в actuals.json для закрытой записи исправили — сборка не глотает
    это молча, а называет, что делать (переоткрыть запись)."""
    import model.facts
    import model.journal
    monkeypatch.setattr(model.facts, "load_facts", lambda: None)
    monkeypatch.setattr(model.journal, "actuals_of", lambda F: facts)
    fake_model.result = payload(journal=closed(0.061))
    code = build_release.main(["--book", "--out", str(inputs["out"])])
    line = last_line(capsys.readouterr())
    if ok:
        assert code == 0 and line.startswith("готово:")
    else:
        assert code == 1 and not inputs["out"].exists()
        assert line.startswith("ПРОВАЛ на шаге проверка журнала")
        assert "--reopen 2026H2-x5.adj_margin" in line


def test_live_price_failure_stops_before_the_model(fake_model, inputs, monkeypatch, capsys):
    from indicators import live

    def no_price(previous, **kw):
        raise live.PriceUnavailable("цена X5 не собрана; запасной цены нет")
    monkeypatch.setattr(live, "collect_live", no_price)
    assert build_release.main(["--live", "--out", str(inputs["out"])]) == 1
    assert fake_model.calls == []
    assert last_line(capsys.readouterr()).startswith("ПРОВАЛ на шаге сбор живых входов")


# ------------------------------------------------------------------ вызов ядра

def test_input_the_engine_does_not_take_is_a_failure():
    def build_payload(*, previous=None, fast=False):
        return payload()
    with pytest.raises(build_release.StepFailed, match="не принимает live"):
        build_release.call_build_payload(build_payload, live={"x": 1}, previous=None,
                                         journal=None, fast=False)


def test_engine_with_kwargs_gets_everything():
    seen = {}

    def build_payload(**kw):
        seen.update(kw)
        return payload()
    build_release.call_build_payload(build_payload, live=1, previous=2, journal=3, fast=True,
                                     release_history=4)
    assert seen == {"live": 1, "previous": 2, "journal": 3, "fast": True, "release_history": 4}


def test_with_slow_is_the_inverse_of_fast():
    seen = {}

    def build_payload(live=None, with_slow=True):
        seen.update(live=live, with_slow=with_slow)
        return payload()
    build_release.call_build_payload(build_payload, live=None, previous=None, journal=None,
                                     fast=True)
    assert seen == {"live": None, "with_slow": False}


def test_required_unknown_parameter_is_a_failure():
    def build_payload(book, *, live=None):
        return payload()
    with pytest.raises(build_release.StepFailed, match="требует book"):
        build_release.call_build_payload(build_payload, live=None, previous=None, journal=None,
                                         fast=False)


@pytest.mark.parametrize("result,expected", [(None, []), (True, []), ([], []), ("", []),
                                             (False, ["validate() вернул False"]),
                                             (["a", "b"], ["a", "b"]), ("плохо", ["плохо"])])
def test_validate_result_shapes(result, expected):
    assert build_release.contract_problems(lambda p: result, {}) == expected


# ------------------------------------------------------------------ --check

def test_check_passes_a_good_file_and_fails_a_bad_one(fake_model, tmp_path, capsys, monkeypatch):
    good = tmp_path / "latest.json"
    good.write_text(json.dumps(payload()), encoding="utf-8")
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert build_release.main(["--check", str(good)]) == 0
    assert last_line(capsys.readouterr()).startswith("готово: контракт цел")
    assert summary.read_text(encoding="utf-8").startswith("- готово: контракт цел")
    bad = tmp_path / "bad.json"
    bad.write_text('{"meta": {"payload_sha256": "c"}, "x": Infinity}', encoding="utf-8")
    assert build_release.main(["--check", str(bad)]) == 1
    assert last_line(capsys.readouterr()).startswith("ПРОВАЛ на шаге чтение выпуска")
