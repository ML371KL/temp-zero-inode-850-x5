# -*- coding: utf-8 -*-
"""Сторож приёма миров ставок: книга стоит на версии своего состояния, а манифест знает всех, кто читает миры.

ЗАЧЕМ. Мир ставок семейства 850 пересобирается один раз в общем источнике (`temp-zero-inode-850-worlds`), а книга
принимает версию командой `ops/tools/adopt_worlds.py` по манифесту `data/assumptions/worlds_dependents.yaml`. Команда
пересчитывает только то, что названо в манифесте. Поэтому здесь сторожится:
  * копии файлов версии (запись, рецепт, входы, описание, надстройка) равны хэшам состояния `worlds_state.json`, а при
    доступном источнике — его файлам с правилами манифеста; ключи происхождения шаблона — индексу версии;
  * правила манифеста на мирах книги дают ровно числа шаблона (числа, зависящие от миров, — не литералы тестов, а выход
    правил: запись сменили, а числа нет — красный тест);
  * каждый лист и инструмент дерева, который читает запись миров, надстройку или их копии, назван в манифесте
    (`readers`): новый читатель миров без строки манифеста — красный тест.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ops" / "tools"))
import adopt_worlds as A  # noqa: E402

READS_WORLDS = re.compile(r"worlds_source|worlds_bank\.json|bank_overlay_draft|world_paths|worlds_state")


@pytest.fixture(scope="module")
def manifest():
    return A.load_manifest(ROOT)


@pytest.fixture(scope="module")
def source():
    try:
        return A.Source(A.find_source(None))
    except (A.Stop, OSError):
        return None                                   # CI: источника рядом нет — сверка по хэшам состояния и правилам


def test_the_book_stands_on_the_version_of_its_state(manifest, source):
    assert A.check(manifest, source, ROOT) == []


def test_the_engine_is_the_family_engine_of_the_state():
    """Движок приёма один на семейство: копия `ops/tools/adopt_worlds.py` побайтово равна образцу источника
    (`model-kit/adopt_worlds.py`) на коммите, с которого принята версия миров, — его хэш команда пишет в состояние.
    Правка движка — только в источнике; модель получает её копией (без сети: сверка с хэшем состояния)."""
    state = A.read_state(ROOT)
    assert state.get("engine_sha256"), "в состоянии нет хэша образца движка: версию миров принимала прежняя команда"
    own = hashlib.sha256((ROOT / "ops" / "tools" / "adopt_worlds.py").read_bytes()).hexdigest()
    assert own == state["engine_sha256"], "копия движка не равна образцу источника на коммите приёма (правили руками?)"


def test_the_template_names_the_record_of_the_state(manifest):
    """Миры книги X5 — проекция записи состояния: запись — побайтовая копия (sha256 состояния), дата кривых книги и
    `worlds.source.curve_date` — дата снимка записи (числа миров сверяет правило project_worlds в --check)."""
    state = A.read_state(ROOT)
    tpl = yaml.safe_load((ROOT / manifest["template"]).read_text(encoding="utf-8"))
    record = (ROOT / manifest["record"]).read_bytes()
    assert hashlib.sha256(record).hexdigest() == state["record_sha256"]
    assert str(tpl["meta"]["curve_as_of"]) == str(tpl["worlds"]["source"]["curve_date"]) == state["curve_date"]


def test_every_reader_of_the_worlds_is_in_the_manifest(manifest):
    files = subprocess.run(["git", "-C", str(ROOT), "ls-files", "*.py"], capture_output=True, text=True,
                           encoding="utf-8").stdout.split()
    readers = [f for f in files if not f.startswith("tests/")
               and READS_WORLDS.search((ROOT / f).read_text(encoding="utf-8", errors="ignore"))]
    missing = sorted(set(readers) - set(manifest["readers"]))
    assert not missing, f"читают миры, но не названы в манифесте (readers): {missing}"
    gone = sorted(f for f in manifest["readers"] if not (ROOT / f).exists())
    assert not gone, f"в манифесте названы несуществующие файлы: {gone}"


def test_the_manifest_steps_name_what_exists(manifest):
    ids = {s["id"] for s in manifest["steps"]}
    for s in manifest["steps"]:
        if s["kind"] == "rule":
            mod = A.load_module(ROOT / s["module"], f"guard_{s['id'].replace('-', '_')}")
            assert callable(getattr(mod, s["function"], None)), s["id"]
        if s["kind"] in ("set",):
            assert (ROOT / s["file"]).exists(), s["id"]
    for f, r in manifest["readers"].items():
        assert not r.get("step") or r["step"] in ids, (f, r.get("step"))
