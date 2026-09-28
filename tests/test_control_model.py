"""Контрольная модель против ядра (docs/MODEL.md §16).

* Сверка с таблицами ядра `data/assumptions/results.json` в допусках: строки путей ≤ 10 %,
  V0 слоёв и EV клеток ≤ 3 %, требования ≤ 1 %, точка ≤ 3 %. Пока results.json нет —
  пропуск с меткой `docs`.
* Сверка на сценариях: изменённые книги (наблюдения маржи, закрытые полугодия, сезонность,
  защита g ≥ r, …) прогоняются через ядро (`model.book_results.book_results`) и контрольную
  модель — ветки, которые книга держит выключенными.
* Независимость: контрольная модель не импортирует ядро и не держит чисел книги; её EV и V0 не
  совпадают с ядром бит в бит (совпадение до машинной точности при этом ожидаемо — см.
  docs/CONTROL-MODEL.md, «Независимость»).
* docs/CONTROL-MODEL.md соответствует текущим книге, фактам и results.json.
"""

from __future__ import annotations

import ast
import math

import pytest
import yaml

from tests import independent_model as cm

CONTROL_SRC = cm.ROOT / "tests" / "independent_model.py"

# Модули, которые может импортировать контрольная модель: стандартная библиотека и PyYAML.
ALLOWED_IMPORTS = {"__future__", "argparse", "copy", "datetime", "json", "math", "sys", "pathlib",
                   "yaml"}

# Числа-литералы контрольной модели и почему они не допущения (вне блока SCENARIOS и
# индексов строк периода).
STRUCTURAL = {
    0: "ноль", 1: "единица", 2: "полугодий в году", 3: "узел 3 года кривой (§4.9); знаки печати",
    4: "знаков печати", 6: "длина ключа полугодия «2026H2»", 7: "месяц начала 2-го полугодия",
    15: "срок схода кривой к LT, лет (§0.2)", 0.5: "половина", 0.25: "четверть",
    0.75: "три четверти (Гордон, §6)", 1000: "млрд → млн (цена на акцию)", 100: "проценты",
    0.0001: "защита g = r − 0,0001 (§6)", 1e-12: "x → 0 в ratio (§6); порог печати",
    1e-06: "порог печати", 0.01: "1 % EV (§7.3); допуск требований",
    0.03: "допуск V0 и точки", 0.1: "допуск строк; пол знаменателя FCFF",
}


def _core_results():
    core = cm.load_results()
    if core is None:
        pytest.skip("data/assumptions/results.json ещё нет: ядро не выпустило таблицы книги "
                    "(python -B -m model.book_results) — сверять контрольную модель не с чем")
    return core


@pytest.fixture(scope="module")
def control_default():
    return cm.evaluate(cm.load_book(), cm.load_facts())


# ------------------------------------------------------------------ сама контрольная модель


def test_control_model_imports_nothing_from_core():
    tree = ast.parse(CONTROL_SRC.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add((node.module or "").split(".")[0])
        elif isinstance(node, ast.Call) and getattr(node.func, "id", "") == "__import__":
            names.add("__import__")
    assert names <= ALLOWED_IMPORTS, f"контрольная модель импортирует {sorted(names - ALLOWED_IMPORTS)}"

    for path in sorted((cm.ROOT / "model").glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            mods = ([a.name for a in node.names] if isinstance(node, ast.Import)
                    else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            assert not any(m.split(".")[0] == "tests" for m in mods), \
                f"{path.name} импортирует контрольную модель"


def test_control_model_has_no_book_or_fact_numbers():
    tree = ast.parse(CONTROL_SRC.read_text(encoding="utf-8"))
    skip = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript):
            skip.update(id(n) for n in ast.walk(node.slice))
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "SCENARIOS"
                                                for t in node.targets):
            skip.update(id(n) for n in ast.walk(node.value))
    found = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, (int, float))
                and not isinstance(node.value, bool) and id(node) not in skip):
            found.setdefault(abs(node.value), []).append(node.lineno)
    extra = {v: lines for v, lines in found.items() if v not in STRUCTURAL}
    assert not extra, f"числа в контрольной модели вне структурных констант: {extra}"


def test_control_invariants(control_default):
    res = control_default
    for layer in cm.LAYERS:
        p = sum(c[cm.LAYER_P[layer]] for c in res["cells"])
        assert abs(p - 1.0) < 1e-12, layer
    base_d = None
    for c in res["cells"]:
        prev_nd = None
        for i in sorted(c["rows"]):
            r = c["rows"][i]
            fcff = (r["adj_ebitda"] - r["lti"] - r["tax_unlevered"] - r["capex"] - r["nwc_change"]
                    - r["opcash_change"] + r["lease"] + r["proceeds"])
            assert math.isclose(fcff, r["fcff"], rel_tol=1e-12, abs_tol=1e-9)
            assert math.isclose(r["capex"], r["capex_maintenance"] + r["capex_growth"]
                                + r["capex_infra"], rel_tol=1e-12)
            if prev_nd is not None:
                nd = prev_nd - (r["fcff"] + r["shield"] - r["interest"]) + r["dividends"]
                assert math.isclose(nd, r["net_debt"], rel_tol=1e-12, abs_tol=1e-9)
            prev_nd = r["net_debt"]
        # D различается по клеткам только перекатом (§7.2)
        d0 = c["d"] + c["rolled"]
        base_d = d0 if base_d is None else base_d
        assert math.isclose(d0, base_d, rel_tol=1e-12)


# ------------------------------------------------------------------ сверка с ядром


@pytest.mark.docs
def test_control_matches_core_results():
    core = _core_results()
    book_path, facts_dir = cm.inputs_of_results(core)
    control = cm.evaluate(cm.load_book(book_path), cm.load_facts(facts_dir))
    checks = cm.compare(control, core)
    groups = {c["group"] for c in checks}
    assert {"cells.ev", "cells.d", "layers.v0", "layers.d", "point", "rows.halves",
            "rows.annual"} <= groups, f"в results.json не нашлось частей для сверки: {groups}"
    bad = [c for c in checks if not c["ok"]]
    assert not bad, "контрольная модель вне допусков: " + "; ".join(
        f"{c['group']} {c['name']}: {c['control']:.4f} против {c['core']:.4f} "
        f"({100 * c['rel']:.2f} % > {100 * c['tol']:.0f} %)" for c in bad[:15])


@pytest.mark.docs
def test_control_is_not_a_bitwise_copy_of_core():
    core = _core_results()
    book_path, facts_dir = cm.inputs_of_results(core)
    control = cm.evaluate(cm.load_book(book_path), cm.load_facts(facts_dir))
    assert not cm.bitwise_copy(control, core), (
        "EV всех клеток и V0 всех слоёв совпали с ядром бит в бит — похоже на общий код")


@pytest.fixture(scope="module")
def core_api():
    """Публичный вход ядра (тот же, что у tests/test_book_results.py)."""
    from model.book import load_book
    from model.book_results import book_results
    from model.facts import load_facts

    return load_book, book_results, load_facts


@pytest.fixture(scope="module")
def base_core_v0(core_api):
    load_book, book_results, load_facts = core_api
    path = cm.default_book_path()
    res = book_results(load_book(path), load_facts(cm.default_facts_dir()), book_path=path)
    return res["layers"]["analytical"]["v0"]


@pytest.mark.parametrize("scenario", cm.SCENARIOS, ids=[s["key"] for s in cm.SCENARIOS])
def test_control_matches_core_on_scenario(scenario, core_api, base_core_v0, tmp_path):
    load_book, book_results, load_facts = core_api
    facts_dir = cm.default_facts_dir()
    book = cm.apply_patch(cm.load_book(), scenario["set"])
    path = tmp_path / f"book-{scenario['key']}.yaml"
    path.write_text(yaml.safe_dump(book, allow_unicode=True, sort_keys=False), encoding="utf-8")
    core = book_results(load_book(path), load_facts(facts_dir), book_path=path)
    assert abs(core["layers"]["analytical"]["v0"] / base_core_v0 - 1.0) > 1e-9, \
        "сценарий не изменил V0 ядра — изменение книги не дошло до расчёта"
    control = cm.evaluate(cm.load_book(path), cm.load_facts(facts_dir))
    bad = [c for c in cm.compare(control, core) if not c["ok"]]
    assert not bad, f"{scenario['key']}: " + "; ".join(
        f"{c['group']} {c['name']}: {c['control']:.4f} против {c['core']:.4f} "
        f"({100 * c['rel']:.2f} %)" for c in bad[:15])


# ------------------------------------------------------------------ документ


@pytest.mark.docs
def test_control_report_is_fresh():
    _core_results()
    text, _, _ = cm.build_report()
    stored = cm.REPORT_MD.read_text(encoding="utf-8") if cm.REPORT_MD.exists() else ""
    assert stored.rstrip("\n") == text.rstrip("\n"), (
        "docs/CONTROL-MODEL.md устарел — перевыпустите: python -B -m tests.independent_model --report")
