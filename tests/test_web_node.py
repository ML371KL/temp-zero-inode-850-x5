"""Витрина и функции Pages под Node: синтаксис и поведение двери данных.

`node --check` — для web/app.js (обычный скрипт) и функций (модули ES; на
вход подаются через stdin с --input-type=module: так проверка не зависит от
того, умеет ли установленный Node распознавать модули по содержимому).
Поведение `functions/api/model.js` и `functions/_middleware.js` — сценарием
tests/web_functions_check.mjs с подменой fetch, Cache API и ASSETS.
Нужен Node.js 18+ (на раннерах GitHub он есть); пропуска нет — без Node тест
падает с понятной причиной.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


def _run(args, stdin=None):
    assert NODE, "нужен Node.js 18+ в PATH: им проверяются web/app.js и функции Pages"
    return subprocess.run([NODE, *args], input=stdin, capture_output=True, text=True, encoding="utf-8", cwd=ROOT, timeout=120)


def test_app_js_parses():
    result = _run(["--check", str(ROOT / "web" / "app.js")])
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("rel", ["functions/api/model.js", "functions/_middleware.js"])
def test_functions_parse_as_modules(rel):
    source = (ROOT / rel).read_text(encoding="utf-8")
    result = _run(["--input-type=module", "--check"], stdin=source)
    assert result.returncode == 0, result.stderr


def test_functions_behave():
    major = int(_run(["-p", "process.versions.node.split('.')[0]"]).stdout.strip() or 0)
    assert major >= 18, f"нужен Node 18+ (fetch, Request, Response), а стоит {major}"
    result = _run([str(ROOT / "tests" / "web_functions_check.mjs")])
    assert result.returncode == 0, result.stdout + result.stderr
