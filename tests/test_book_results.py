"""Регрессия таблиц книги: свежий расчёт = data/assumptions/results.json (rel 1e-12, пол 1e-10).

Файлы пишет `python -B -m model.book_results`; после правки книги, фактов или
ядра их перевыпускают тем же изменением. Раздел `band` (полоса 2 000 прогонов,
обратный DCF, «что даст отчёт») сверяется долгим тестом `ci_only`; остальное —
в каждом прогоне тестов.
"""

from __future__ import annotations

import json

import pytest

from model.book import BOOK_DIR, default_book_path, load_book
from model.book_results import book_results, render_run_output
from model.facts import load_facts

REL, FLOOR = 1e-12, 1e-10


def _compare(a, b, where: str, out: list[str]) -> None:
    if isinstance(a, dict) and isinstance(b, dict):
        if set(a) != set(b):
            out.append(f"{where}: ключи {sorted(set(a) ^ set(b))}")
        for k in set(a) & set(b):
            _compare(a[k], b[k], f"{where}.{k}", out)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{where}: длина {len(a)} ≠ {len(b)}")
        for i, (x, y) in enumerate(zip(a, b)):
            _compare(x, y, f"{where}[{i}]", out)
    elif (isinstance(a, (int, float)) and isinstance(b, (int, float))
          and not isinstance(a, bool) and not isinstance(b, bool)):
        if abs(a - b) > max(FLOOR, REL * max(abs(a), abs(b))):
            out.append(f"{where}: {a!r} ≠ {b!r}")
    elif a != b:
        out.append(f"{where}: {a!r} ≠ {b!r}")


def _fresh_and_stored(band: bool):
    path = default_book_path()
    fresh = json.loads(json.dumps(book_results(load_book(path), load_facts(), book_path=path,
                                               band=band), ensure_ascii=False))
    stored = json.loads((BOOK_DIR / "results.json").read_text(encoding="utf-8"))
    return fresh, stored


def test_fresh_results_match_file():
    fresh, stored = _fresh_and_stored(band=False)
    assert "band" in stored, "в results.json нет раздела band — перевыпустите таблицы книги"
    stored.pop("band")
    diffs: list[str] = []
    _compare(fresh, stored, "results", diffs)
    assert not diffs, ("results.json устарел — перевыпустите `python -B -m model.book_results`: "
                       + "; ".join(diffs[:10]))


@pytest.mark.ci_only
def test_fresh_band_matches_file():
    fresh, stored = _fresh_and_stored(band=True)
    diffs: list[str] = []
    _compare(fresh["band"], stored["band"], "results.band", diffs)
    assert not diffs, ("раздел band в results.json устарел — перевыпустите "
                       "`python -B -m model.book_results`: " + "; ".join(diffs[:10]))


def test_run_output_is_rendered_from_results():
    stored = json.loads((BOOK_DIR / "results.json").read_text(encoding="utf-8"))
    text = (BOOK_DIR / "run_output.txt").read_text(encoding="utf-8")
    assert render_run_output(stored) == text
