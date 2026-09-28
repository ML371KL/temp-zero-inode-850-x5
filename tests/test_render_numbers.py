"""Числа результатов книги в документах — подстановкой (`ops/tools/render_numbers.py`).

Документ с метками обязан быть равен своей перерисовке по
`data/assumptions/results.json`: новая книга без перерисовки документов не
пройдёт CI, а метка, указывающая в пустоту, — тоже.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIX = "python ops/tools/render_numbers.py"


@pytest.fixture(scope="module")
def tool():
    spec = importlib.util.spec_from_file_location("render_numbers_tool",
                                                  ROOT / "ops" / "tools" / "render_numbers.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.docs
def test_documents_show_the_numbers_of_the_book_results(tool):
    results = json.loads(tool.RESULTS.read_text(encoding="utf-8"))
    problems, marked = [], set()
    for path, text in tool.documents():
        name = path.relative_to(ROOT).as_posix()
        marked.add(name)
        new, errors = tool.render(text, results)
        problems += [f"{name}: {error}" for error in errors]
        old_lines, new_lines = text.split("\n"), new.split("\n")
        problems += [f"{name}:{number}: {old.strip()[:160]}"
                     for number, (old, fresh) in enumerate(zip(old_lines, new_lines), 1)
                     if old != fresh]
    assert not problems, (f"документы разошлись с results.json — `{FIX}`:\n"
                          + "\n".join(problems[:40]))
    # Сторож от тихой потери разметки: эти документы печатают числа результатов книги.
    assert {"docs/MODEL.md", "docs/MANUAL.md", "docs/DASHBOARD.md", "docs/INDICATORS.md",
            "README.md", "data/assumptions/ASSUMPTIONS-BOOK.md"} <= marked, sorted(marked)


RESULTS = {"headline": {"printed_central": 1350.0}, "cells": {"N|stress": 7.5},
           "gap": -0.0276, "rows": [{"name": "a", "v": 0.00049}, {"name": "b", "v": 2.5}],
           "q": {"0.50": 1344.17}}


def _root(tmp_path: Path, doc: str) -> Path:
    (tmp_path / "data" / "assumptions").mkdir(parents=True)
    (tmp_path / "data" / "assumptions" / "results.json").write_text(json.dumps(RESULTS), encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "X.md").write_bytes(doc.encode("utf-8"))
    return tmp_path


@pytest.mark.docs
def test_a_stale_number_fails_the_check_and_is_rewritten_in_place(tool, tmp_path, capsys):
    doc = ("| медиана | **≈<!--=headline.printed_central r0-->1 300<!--/--> ₽** |\r\n"
           "разрыв <!--=gap sp1-->−2,8<!--/--> %, P50 <!--=q[\"0.50\"] r1-->1 344,2<!--/-->\r\n")
    root = _root(tmp_path, doc)
    assert tool.main(["--check", "--root", str(root)]) == 1
    assert "устарел: docs/X.md" in capsys.readouterr().err
    assert (root / "docs" / "X.md").read_bytes() == doc.encode("utf-8"), "--check ничего не пишет"
    assert tool.main(["--root", str(root)]) == 0
    # Меняется только значение между метками; переводы строк файла — те же.
    assert (root / "docs" / "X.md").read_bytes() == doc.replace(">1 300<", ">1 350<").encode("utf-8")
    assert tool.main(["--check", "--root", str(root)]) == 0


@pytest.mark.docs
@pytest.mark.parametrize("mark, reason", [
    ("<!--=headline.nope r0-->1<!--/-->", "нет ключа «nope»"),
    ("<!--=headline.printed_central x1-->1<!--/-->", "неизвестный формат"),
    ("<!--=rows[name=c].v r1-->1<!--/-->", "строк 0, нужна одна"),
    ("<!--=rows[5].v r1-->1<!--/-->", "нет элемента [5]"),
    ("<!--=rows r1-->1<!--/-->", "ждёт число"),
    ("<!--=headline.printed_central r0-->1 350", "не закрыта"),
    ("<!--=headline.printed_central|r0-->1<!--/-->", "режет ячейку таблицы"),
])
def test_a_broken_mark_is_named_and_fails_the_check(tool, tmp_path, capsys, mark, reason):
    root = _root(tmp_path, f"текст {mark} текст\n")
    assert tool.main(["--check", "--root", str(root)]) == 1
    assert reason in capsys.readouterr().err


@pytest.mark.docs
@pytest.mark.parametrize("line", [
    "<!--=headline.printed_central r0-->1<!--/--> ₽",
    "   <!--=headline.printed_central r0-->1<!--/--> ₽",
    "- <!--=headline.printed_central r0-->1<!--/--> ₽",
    "> 1. <!--=headline.printed_central r0-->1<!--/--> ₽",
])
def test_a_mark_opening_a_line_would_render_as_html_and_fails(tool, tmp_path, capsys, line):
    # CommonMark читает строку, начатую с `<!--`, как HTML-блок: метка видна, абзац рвётся.
    root = _root(tmp_path, f"абзац\n{line}\n")
    assert tool.main(["--check", "--root", str(root)]) == 1
    assert "строка 2: метка в начале строки" in capsys.readouterr().err


@pytest.mark.docs
def test_a_mark_inside_a_line_or_a_table_cell_is_fine(tool):
    doc = ("| <!--=gap sp1-->0<!--/--> % |\n**<!--=gap sp1-->0<!--/--> %**\n"
           "-<!--=gap sp1-->0<!--/--> %\n")
    new, errors = tool.render(doc, RESULTS)
    assert not errors
    assert new == doc.replace(">0<", ">−2,8<")


@pytest.mark.docs
def test_a_mark_in_code_is_an_example_not_a_mark(tool):
    doc = ("Синтаксис: `<!--=ПУТЬ ФОРМАТ-->значение<!--/-->`, число "
           "<!--=headline.printed_central r0-->1<!--/--> ₽\n```\n<!--=нет r0-->2<!--/-->\n```\n")
    new, errors = tool.render(doc, RESULTS)
    assert not errors
    assert new == doc.replace(">1<", ">1 350<")


@pytest.mark.docs
@pytest.mark.parametrize("value, fmt, text", [
    (1344.1737, "r1", "1 344,2"), (1350.0, "r0", "1 350"), (97.85, "r0", "98"),
    (12345678.9, "r0", "12 345 679"), (0.583, "p0", "58"), (0.0003, "p2", "0,03"),
    (-0.0276, "sp1", "−2,8"), (0.00818, "sp1", "+0,8"), (163.8, "s0", "+164"),
    (-26.93, "s0", "−27"), (0.0000526, "sp3", "+0,005"), (-0.00004, "sp1", "0,0"),
    (-0.2, "r0", "0"), (0.69652, "r3", "0,697"),
])
def test_number_formats_reproduce_the_documents_typography(tool, value, fmt, text):
    assert tool.formatted(value, fmt) == text


@pytest.mark.docs
def test_paths_select_by_name_and_by_raw_key(tool):
    assert tool.resolve(RESULTS, "rows[name=b].v") == 2.5
    assert tool.resolve(RESULTS, 'q["0.50"]') == 1344.17
    assert tool.resolve(RESULTS, "rows[1].v") == 2.5
    assert tool.resolve(RESULTS, r'cells["N\|stress"]') == 7.5
    with pytest.raises(KeyError):
        tool.resolve(RESULTS, "headline[0]")


@pytest.mark.docs
def test_paths_select_by_nested_fields_all_at_once(tool):
    # Клетка сетки — по имени, а не по месту в списке: порядок клеток может смениться.
    grid = {"cells": [{"cell": {"world": w, "capex": c}, "ev": ev}
                      for w, c, ev in [("M", "low", 5.0), ("M", "high", -20.1), ("N", "high", 7.0)]]}
    assert tool.resolve(grid, "cells[cell.world=M&cell.capex=high].ev") == -20.1
    for bad, reason in [("cells[cell.world=M].ev", "строк 2"), ("cells[cell.world=X&cell.capex=high].ev",
                        "строк 0"), ("cells[cell.world=M&capex].ev", "без «=»")]:
        with pytest.raises(KeyError, match=reason):
            tool.resolve(grid, bad)
