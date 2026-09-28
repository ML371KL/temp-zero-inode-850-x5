"""Факты: {"v", "src"|"calc"}, null → None, отказ на числе без источника."""

from __future__ import annotations

import dataclasses
import json
import shutil

import pytest

from model.book import next_period, period_index, periods, prev_period
from model.facts import (CORE_FILES, FIXTURE_DIR, FactsError, core_facts, load_facts, unwrap)


def test_unwrap_values_and_null():
    raw = {"a": {"v": 1.5, "src": "x"}, "b": {"v": None, "calc": "не раскрыто"},
           "c": [{"v": 2.0, "calc": "1 + 1"}], "d": "текст"}
    assert unwrap(raw, "t") == {"a": 1.5, "b": None, "c": [2.0], "d": "текст"}


def test_value_without_source_is_refused():
    with pytest.raises(FactsError, match="без src и без calc"):
        unwrap({"a": {"v": 1.0}}, "t")
    with pytest.raises(FactsError):
        unwrap({"a": {"v": 1.0, "src": ""}}, "t")


def test_fixture_has_source_for_every_value():
    def walk(node, where):
        if isinstance(node, dict):
            if "v" in node:
                assert node.get("src") or node.get("calc"), where
            for k, v in node.items():
                walk(v, f"{where}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{where}[{i}]")
    for name in CORE_FILES:
        walk(json.loads((FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8")), name)


def _copy_fixture(tmp_path):
    root = tmp_path / "facts"
    shutil.copytree(FIXTURE_DIR, root)
    return root


def _fixture_book(book, root):
    """Книга с мостом из строк фикстуры (фикстура не обязана знать новые строки книги)."""
    keys = [ln["key"] for ln in json.loads((root / "bridge.json").read_text(encoding="utf-8"))["lines"]]
    return {**book, "bridge": {"include": [k for k in book["bridge"]["include"] if k in keys]}}


def test_null_is_not_zero(book, tmp_path):
    root = _copy_fixture(tmp_path)
    path = root / "balance.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["net_debt"] = {"v": None, "src": "не раскрыто"}
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    F = load_facts(root)
    assert F.data["balance"]["net_debt"] is None
    with pytest.raises(FactsError, match="null"):
        core_facts(F, _fixture_book(book, root))


def test_capex_history_is_required_for_the_anchor_da_runoff(book, tmp_path):
    """База D&A якоря выбывает по когортам 2L полугодий до якоря (§4.5): нет полугодия — отказ."""
    root = _copy_fixture(tmp_path)
    B = _fixture_book(book, root)
    core_facts(load_facts(root), B)
    path = root / "history.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    L2 = int(2 * book["capex"]["asset_life_years"])
    first_needed = [h["period"] for h in data["halves"] if h["period"] < book["meta"]["anchor_period"]][-L2]
    data["halves"] = [h for h in data["halves"] if h["period"] != first_needed]
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(FactsError, match=first_needed):
        core_facts(load_facts(root), B)


def test_missing_core_file_is_refused(tmp_path):
    root = _copy_fixture(tmp_path)
    (root / "shares.json").unlink()
    with pytest.raises(FactsError, match="shares.json"):
        load_facts(root)


def test_bridge_include_must_exist_in_facts(book, facts):
    B = dict(book)
    B["bridge"] = {"include": [*book["bridge"]["include"], "no_such_line"]}
    with pytest.raises(FactsError, match="no_such_line"):
        core_facts(facts, B)


def test_core_facts_on_default(book, facts):
    cf = core_facts(facts, book)
    assert cf.anchor == book["meta"]["anchor_period"]
    assert cf.shares_mln > 0 and cf.revenue[cf.anchor] > 0
    assert 0 < cf.margin_anchor < 1
    assert all(line.amount is not None for line in cf.bridge if line.included)
    # индекс эффективной площади (§4.1): от первого полугодия с площадью в фактах
    net = facts.data["network"]
    known = sorted({*net["area_end"], *(net.get("area_end_est") or {})}, key=period_index)
    assert cf.eff_start == known[0]
    assert period_index(cf.eff_start) <= period_index(cf.anchor) - 2
    n = len(book["network"]["maturity_curve"]) - 1
    first = cf.eff_start
    for _ in range(n - 1):
        first = prev_period(first)
    assert list(cf.gross_opened) == periods(first, cf.anchor)
    assert list(cf.closed_area) == periods(next_period(cf.eff_start), cf.anchor)
    assert set(cf.area_end) == {cf.eff_start, cf.anchor}
    # capex 2L полугодий до якоря — выручка × capex/выручку из history.json (§4.5)
    L2 = int(2 * book["capex"]["asset_life_years"])
    assert len(cf.capex_hist) == L2 and all(v > 0 for v in cf.capex_hist.values())
    halves = {h["period"]: h for h in facts.data["history"]["halves"]}
    for p, v in cf.capex_hist.items():
        assert v == halves[p]["revenue"] * halves[p]["capex_pct"]
    assert cf.treasury_mln == facts.data["shares"]["treasury"] > 0


def _drop(root, key, period):
    path = root / "network.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    del data[key][period]
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_effective_area_history_is_required(book, tmp_path):
    """Индекс §4.1 ведётся от первого полугодия с площадью: нет закрытий или открытий
    внутри истории — отказ с путём факта; без оценок площади до 2023H2 индекс начинается
    позже, и тогда нужны открытия до 2023H2."""
    root = _copy_fixture(tmp_path)
    B = _fixture_book(book, root)
    cf = core_facts(load_facts(root), B)
    inside = periods(next_period(cf.eff_start), cf.anchor)[1]
    _drop(root, "closed_area_est", inside)
    with pytest.raises(FactsError, match=f"closed_area_est.{inside}"):
        core_facts(load_facts(root), B)

    root = _copy_fixture(tmp_path / "b")
    _drop(root, "gross_opened_hist", cf.eff_start)
    with pytest.raises(FactsError, match=f"gross_opened_hist.{cf.eff_start}"):
        core_facts(load_facts(root), B)

    root = _copy_fixture(tmp_path / "c")
    path = root / "network.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data.pop("area_end_est")
    first = min(data["area_end"], key=period_index)
    for p in [q for q in data["gross_opened_hist"] if period_index(q) < period_index(first)]:
        del data["gross_opened_hist"][p]
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(FactsError, match="gross_opened_hist"):
        core_facts(load_facts(root), B)
