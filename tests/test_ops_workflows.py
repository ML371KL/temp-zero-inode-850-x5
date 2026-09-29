"""Workflow GitHub Actions: расписание, права, порядок шагов, закреплённые
действия, отсутствие секретов. Решения ведущего — в тексте теста."""

from __future__ import annotations

import os
import re
import subprocess
import sys
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


ALL = ("ci.yml", "docs.yml", "pipeline.yml")


def test_probe_workflow_is_gone():
    assert not (WORKFLOWS / "probe.yml").exists()
    assert sorted(p.name for p in WORKFLOWS.glob("*.yml")) == list(ALL)


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
             run_index(s, "git ls-remote"),
             run_index(s, "publish.py --release"),
             run_index(s, "publish.py --verify"),
             run_index(s, "/actions/workflows/pipeline.yml/enable")]
    assert order == sorted(order)


def test_pipeline_tick_tests_exclude_exactly_three_markers():
    s = steps("pipeline.yml")
    assert '-m "not network and not ci_only and not docs"' in s[run_index(s, "pytest")]["run"]


def test_skip_reasons_are_printed():
    """Причина пропуска видна: `-rs` у pytest в конвейере и CI; такт пишет строки пропусков
    в сводку (на раннере так виден пропуск теста пересборки фактов с меткой primary)."""
    tick = steps("pipeline.yml")[run_index(steps("pipeline.yml"), "pytest")]["run"]
    assert "pytest -q -rs " in tick and "set -o pipefail" in tick
    assert "^SKIPPED" in tick and "$GITHUB_STEP_SUMMARY" in tick
    assert "pytest -q -rs " in steps("ci.yml")[run_index(steps("ci.yml"), "pytest")]["run"]


def test_stale_code_is_not_published():
    """Повтор прогона (Re-run) идёт на исходном коммите: перед публикацией код прогона
    сверяется с головой main, и если main ушёл вперёд правкой путей push-триггера, шаг
    падает — ветка data не тронута. Пути сверки = пути push-триггера конвейера."""
    s = steps("pipeline.yml")
    guard = s[run_index(s, "git ls-remote")]
    run = guard["run"]
    assert guard["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert "refs/heads/main" in run and '= "$GITHUB_SHA"' in run
    assert "compare/$GITHUB_SHA..." in run and '"$status" != ahead' in run
    assert "exit 1" in run and "не Re-run" in run
    pattern = re.search(r"grep -E '([^']+)'", run).group(1)
    for path in load("pipeline.yml")["on"]["push"]["paths"]:
        if path.startswith("!"):
            assert path == "!**/*.md" and "grep -v '\\.md$'" in run
            continue
        sample = path.replace("**", "x.py")
        assert re.search(pattern, sample), f"путь push-триггера {path} не сверяется"
    for other in ("docs/MANUAL.md", "web/app.js", "tests/test_x.py", "functions/api/model.js"):
        assert not re.search(pattern, other), other


def test_pipeline_verifies_through_the_live_door():
    flow = load("pipeline.yml")
    assert flow["env"]["PUBLIC_DATA_URL"] == "https://tzi-850-x5.pages.dev/api/model"
    s = steps("pipeline.yml")
    assert "$PUBLIC_DATA_URL" in s[run_index(s, "publish.py --verify")]["run"]


def test_keepalive_runs_always_with_the_job_token():
    s = steps("pipeline.yml")
    keep = s[run_index(s, "pipeline.yml/enable")]
    assert keep["if"] == "always()"
    assert keep["env"]["GH_TOKEN"] == "${{ github.token }}"


def test_keepalive_never_reenables_a_disabled_workflow():
    """Отключённый владельцем (или GitHub) workflow шаг не включает: сначала
    читает состояние, PUT …/enable — только при state = active."""
    run = steps("pipeline.yml")[run_index(steps("pipeline.yml"), "pipeline.yml/enable")]["run"]
    lines = [line.strip() for line in run.splitlines() if line.strip()]
    assert lines[0].startswith("state=$(gh api") and lines[0].endswith("--jq .state)")
    assert lines[1] == 'if [ "$state" = active ]; then'
    assert lines[2].startswith("gh api -X PUT") and lines[2].endswith('pipeline.yml/enable"')
    assert run.count("-X PUT") == 1


def test_pipeline_publishes_only_from_main():
    job = load("pipeline.yml")["jobs"]["pipeline"]
    assert job["if"] == "github.ref == 'refs/heads/main'"


def test_publication_expects_the_commit_read_at_the_start():
    """Откат или правка ветки data во время прогона — провал публикации, а не
    тихая перезапись: публикация сверяет голову ветки с коммитом шага состояния."""
    s = steps("pipeline.yml")
    assert "--fetch-state var/state" in s[run_index(s, "publish.py --fetch-state")]["run"]
    assert '--expect-commit "$(cat var/state/.commit)"' in s[run_index(s, "publish.py --release")]["run"]


def test_today_does_not_depend_on_tz():
    """«Сегодня» ядра (`model.book.today`) и сборщиков (`indicators.live.msk_today`) — день по
    Москве, фиксированное UTC+3 в коде: от пояса машины (TZ) не зависит. `TZ: Europe/Moscow`
    в workflow — только для отметок времени инструментов, дату выпуска он не определяет."""
    code = ("import datetime as dt; from model.book import today; from indicators.live import msk_today; "
            "a = dt.datetime.now(dt.timezone.utc); t, m = today(), msk_today(); "
            "b = dt.datetime.now(dt.timezone.utc); h = dt.timedelta(hours=3); "
            "print(t, m, (a + h).date(), (b + h).date())")
    env = {k: x for k, x in os.environ.items() if k not in ("FAKE_TODAY", "TZ")}
    env["PYTHONPATH"] = str(ROOT)
    for tz in ("UTC0", "XYZ-14", "XYZ+12", "Europe/Moscow"):
        out = subprocess.run([sys.executable, "-c", code], env={**env, "TZ": tz}, cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout.split()
        assert out[0] == out[1] and out[0] in out[2:], (tz, out)


def test_no_secrets_and_pinned_actions():
    for name in ALL:
        text = (WORKFLOWS / name).read_text(encoding="utf-8")
        assert "secrets." not in text
        for step in steps(name):
            if "uses" in step:
                assert re.fullmatch(r"actions/[\w-]+@[0-9a-f]{40}", step["uses"]), step["uses"]


def test_scripts_named_in_workflows_exist():
    for name in ALL:
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


def test_requirements_are_pinned_exactly():
    """Конвейер ставит зависимости в каждом прогоне: точные версии всего дерева,
    иначе новая версия pytest может уронить такт без правки кода. Файл — ASCII:
    старый pip на Windows читает его в кодировке системы."""
    raw = (ROOT / "requirements.txt").read_bytes()
    assert raw.isascii()
    lines = [line for line in raw.decode().splitlines() if line.strip()]
    names = set()
    for line in lines:
        spec = line.split(";")[0].strip()
        assert re.fullmatch(r"[A-Za-z0-9_.-]+==[0-9][0-9A-Za-z.]*", spec), line
        names.add(spec.split("==")[0].lower())
    assert {"pyyaml", "pytest", "pluggy", "iniconfig", "packaging", "pygments"} <= names


def test_docs_workflow_checks_md_only_commits():
    """Коммит из одних *.md ci.yml пропускает — его проверяют тесты docs здесь."""
    flow = load("docs.yml")
    for event in ("push", "pull_request"):
        assert flow["on"][event]["branches"] == ["main"]
        assert flow["on"][event]["paths"] == ["**/*.md"]
    assert "workflow_dispatch" in flow["on"]
    assert flow["permissions"] == {"contents": "read"}
    s = steps("docs.yml")
    assert any(re.search(r"pip install .*-r requirements\.txt", st.get("run") or "") for st in s)
    assert '-m "docs and not network"' in s[run_index(s, "pytest")]["run"]

