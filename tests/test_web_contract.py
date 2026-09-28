"""Витрина и контракт выпуска x5-v1 (docs/PAYLOAD.md).

* витрина читает только объявленные блоки верхнего уровня (`d.<блок>` в
  web/app.js — из REQUIRED_TOP_LEVEL документа);
* мок `tests/fixtures/sample_payload.json` держит контракт: все блоки, размер,
  конечные числа, длины прогонов и таблица λ;
* правило единственного пересчёта во фронте (ползунок λ: квантиль тип 7,
  печать половиной к чётному) на прогонах выпуска даёт заголовок выпуска — на
  моке и на выпуске ядра `var/release/sample.json`, когда он есть.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "web" / "app.js"
PAYLOAD_DOC = ROOT / "docs" / "PAYLOAD.md"
FIXTURE = ROOT / "tests" / "fixtures" / "sample_payload.json"
CORE_SAMPLE = ROOT / "var" / "release" / "sample.json"
PAYLOAD_BUDGET = 500_000


def required_top_level() -> list[str]:
    text = PAYLOAD_DOC.read_text(encoding="utf-8")
    match = re.search(r"REQUIRED_TOP_LEVEL`\):\s*`([^`]+)`", text, re.S)
    assert match, "в docs/PAYLOAD.md не найден список REQUIRED_TOP_LEVEL"
    return [name.strip() for name in match.group(1).replace("\n", " ").split(",") if name.strip()]


def blocks_read_by_front() -> set[str]:
    src = APP.read_text(encoding="utf-8")
    return set(re.findall(r"(?<![\w$.])d\.([A-Za-z_]\w*)", src))


def test_frontend_reads_only_declared_blocks():
    declared = set(required_top_level())
    used = blocks_read_by_front()
    assert used, "витрина не читает ни одного блока выпуска через d.<блок>"
    assert used <= declared, f"витрина читает необъявленные блоки: {sorted(used - declared)}"


def _releases():
    # Выпуск ядра проверяется, когда он есть (var/ не в git; пропуск без маркера
    # в этом проекте — ошибка, поэтому параметра просто нет).
    out = [pytest.param(FIXTURE, id="mock")]
    if CORE_SAMPLE.exists():
        out.append(pytest.param(CORE_SAMPLE, id="core-sample"))
    return out


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _finite(node, where="выпуск"):
    if isinstance(node, float):
        assert math.isfinite(node), f"{where}: нечисло"
    elif isinstance(node, dict):
        for k, v in node.items():
            _finite(v, f"{where}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _finite(v, f"{where}[{i}]")


@pytest.mark.parametrize("path", _releases())
def test_release_follows_the_contract(path):
    payload = _load(path)
    assert payload["schema"] == "x5-v1"
    missing = [b for b in required_top_level() if b not in payload]
    assert not missing, f"нет блоков: {missing}"
    compact = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    assert len(compact.encode("utf-8")) <= PAYLOAD_BUDGET
    _finite(payload)
    head, fv = payload["headline"], payload["fair_value"]
    assert len(fv["draws_low"]) == len(fv["draws_high"]) == head["draws"]
    assert len(fv["by_lambda"]) == 21
    assert abs(fv["lambda_step"] * 20 - 1) < 1e-12, "шаг ползунка λ — 0,05 (21 положение)"
    assert [round(r["lambda"], 2) for r in fv["by_lambda"]] == [round(i / 20, 2) for i in range(21)]


# ── правило ползунка λ (web/app.js: centresAt, headlineAt, roundHalfEven) ──

def _q7(sorted_vals, q):
    n = len(sorted_vals)
    h = (n - 1) * q
    lo = math.floor(h)
    hi = min(lo + 1, n - 1)
    return sorted_vals[lo] + (h - lo) * (sorted_vals[hi] - sorted_vals[lo])


def _front_headline(payload, lam):
    fv, head = payload["fair_value"], payload["headline"]
    c = sorted(a + lam * (b - a) for a, b in zip(fv["draws_low"], fv["draws_high"]))
    step = head["print_step"]
    market = head["market_price"]
    return {"central": _q7(c, 0.5), "band": [_q7(c, 0.1), _q7(c, 0.9)], "inner": [_q7(c, 0.25), _q7(c, 0.75)],
            "printed_central": round(_q7(c, 0.5) / step) * step,
            "p_below": sum(1 for v in c if v < market) / len(c), "mean": sum(c) / len(c)}


@pytest.mark.parametrize("path", _releases())
def test_slider_rule_reproduces_the_release_headline(path):
    payload = _load(path)
    head, fv = payload["headline"], payload["fair_value"]
    lam = fv["lambda"]
    got = _front_headline(payload, lam)
    tol = 0.11   # прогоны в выпуске округлены до 0,1 ₽
    assert abs(got["central"] - head["central"]) <= tol
    assert all(abs(a - b) <= tol for a, b in zip(got["band"], head["band"]))
    assert all(abs(a - b) <= tol for a, b in zip(got["inner"], head["inner"]))
    assert got["printed_central"] == head["printed_central"]
    assert abs(got["p_below"] - head["p_central_below_market"]) <= 2 / head["draws"]


@pytest.mark.parametrize("path", _releases())
def test_slider_rule_matches_the_lambda_table(path):
    payload = _load(path)
    for row in payload["fair_value"]["by_lambda"]:
        got = _front_headline(payload, row["lambda"])
        assert abs(got["central"] - row["median"]) <= 0.11, f"λ = {row['lambda']}: медиана"
        assert abs(got["band"][0] - row["p10"]) <= 0.11 and abs(got["band"][1] - row["p90"]) <= 0.11, f"λ = {row['lambda']}: полоса"
        assert abs(got["p_below"] - row["p_below"]) <= 2 / payload["headline"]["draws"], f"λ = {row['lambda']}: P(ниже рынка)"


def test_slider_steps_come_from_the_release():
    src = APP.read_text(encoding="utf-8")
    assert 'step: String(lambdaStep(d))' in src, "шаг ползунка — fair_value.lambda_step выпуска"
