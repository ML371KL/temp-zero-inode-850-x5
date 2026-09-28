"""Workflow GitHub Actions: расписание, права, порядок шагов, закреплённые
действия, отсутствие секретов. Решения ведущего — в тексте теста."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def load(name: str) -> dict:
    data = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    # PyYAML (YAML 1.1) читает ключ `on` как True.
    data["on"] = data.pop(True, data.get("on"))
    return data


def steps(name: str) -> list[dict]:
    jobs = load(name)["jobs"]
    assert len(jobs) == 1
    return next(iter(jobs.values()))["steps"]


def run_index(all_steps: list[dict], needle: str) -> int:
    found = [i for i, s in enumerate(all_steps) if needle in (s.get("run") or "")]
    assert found, f"нет шага с {needle!r}"
    return found[0]


def test_probe_workflow_is_gone():
    assert not (WORKFLOWS / "probe.yml").exists()
    assert sorted(p.name for p in WORKFLOWS.glob("*.yml")) == ["ci.yml", "pipeline.yml"]


def test_pipeline_triggers():
    on = load("pipeline.yml")["on"]
    assert on["schedule"] == [{"cron": "50 16 * * 1-5"}]
    assert "workflow_dispatch" in on
    assert on["push"]["branches"] == ["main"]
    paths = on["push"]["paths"]
    for code in ("model/**", "data/**", "indicators/**", "ops/**", "requirements.txt"):
        assert code in paths
    assert paths[-1] == "!**/*.md"


def test_pipeline_permissions_and_concurrency():
    flow = load("pipeline.yml")
    assert flow["permissions"] == {"actions": "write", "contents": "write"}
    assert flow["concurrency"]["cancel-in-progress"] is False
    assert "${{" not in flow["concurrency"]["group"]  # одна группа на все прогоны


def test_pipeline_step_order():
    s = steps("pipeline.yml")
    order = [run_index(s, "publish.py --fetch-state"),
             run_index(s, "indicators.live"),
             run_index(s, "pytest"),
             run_index(s, "--live-file"),
             run_index(s, "build_release.py --check"),
             run_index(s, "publish.py --release"),
             run_index(s, "publish.py --verify"),
             run_index(s, "/actions/workflows/pipeline.yml/enable")]
    assert order == sorted(order)


def test_pipeline_tick_tests_exclude_exactly_three_markers():
    s = steps("pipeline.yml")
    assert '-m "not network and not ci_only and not docs"' in s[run_index(s, "pytest")]["run"]


def test_pipeline_verifies_through_the_live_door():
    flow = load("pipeline.yml")
    assert flow["env"]["PUBLIC_DATA_URL"] == "https://tzi-850-x5.pages.dev/api/model"
    s = steps("pipeline.yml")
    assert "$PUBLIC_DATA_URL" in s[run_index(s, "publish.py --verify")]["run"]


def test_keepalive_runs_always_with_the_job_token():
    s = steps("pipeline.yml")
    keep = s[run_index(s, "pipeline.yml/enable")]
    assert keep["if"] == "always()"
    assert keep["run"].startswith("gh api -X PUT")
    assert keep["env"]["GH_TOKEN"] == "${{ github.token }}"


def test_no_secrets_and_pinned_actions():
    for name in ("pipeline.yml", "ci.yml"):
        text = (WORKFLOWS / name).read_text(encoding="utf-8")
        assert "secrets." not in text
        for step in steps(name):
            if "uses" in step:
                assert re.fullmatch(r"actions/[\w-]+@[0-9a-f]{40}", step["uses"]), step["uses"]


def test_scripts_named_in_workflows_exist():
    for name in ("pipeline.yml", "ci.yml"):
        text = (WORKFLOWS / name).read_text(encoding="utf-8")
        for script in re.findall(r"python ((?:ops|indicators)/[\w/]+\.py)", text):
            assert (ROOT / script).exists(), script
        for module in re.findall(r"python -m ((?:ops|indicators)\.\w+)", text):
            assert (ROOT / (module.replace(".", "/") + ".py")).exists(), module


def test_ci_workflow():
    flow = load("ci.yml")
    for event in ("push", "pull_request"):
        assert flow["on"][event]["branches"] == ["main"]
        assert flow["on"][event]["paths-ignore"] == ["**/*.md"]
    assert flow["permissions"] == {"contents": "read"}
    s = steps("ci.yml")
    python = next(st for st in s if "setup-python" in st.get("uses", ""))
    assert python["with"]["python-version"] == "3.12"
    assert any(re.search(r"pip install .*-r requirements\.txt", st.get("run") or "") for st in s)
    assert '-m "not network"' in s[run_index(s, "pytest")]["run"]
    build = run_index(s, "build_release.py --book --fast")
    assert run_index(s, "build_release.py --check") > build

