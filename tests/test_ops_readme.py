"""`ops/README.md` не расходится с конвейером: расписание, команды, метки."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "ops" / "README.md"

pytestmark = pytest.mark.docs


def pipeline() -> dict:
    data = yaml.safe_load((ROOT / ".github" / "workflows" / "pipeline.yml").read_text(encoding="utf-8"))
    data["on"] = data.pop(True, data.get("on"))
    return data


def test_schedule_in_readme_matches_cron():
    minute, hour, *_ = pipeline()["on"]["schedule"][0]["cron"].split()
    assert f"{int(hour)}:{int(minute):02d} UTC" in README.read_text(encoding="utf-8")


def test_commands_in_readme():
    text = README.read_text(encoding="utf-8")
    for command in ("gh workflow run pipeline.yml",
                    "python ops/publish.py --rollback --note",
                    "python ops/publish.py --reopen",
                    "gh workflow disable pipeline.yml",
                    "npx wrangler@4.135.0 pages deploy web --project-name tzi-850-x5 --branch main",
                    "data/facts/actuals.json",
                    '-m "not network and not ci_only and not docs"',
                    ".github/workflows/docs.yml"):
        assert command in text, command
    # Ручного отката коммитом в data нет: публикация стёрла бы его без следа в history.json.
    assert "cp previous.json latest.json" not in text


def test_publication_command_is_the_pipeline_one():
    steps = next(iter(pipeline()["jobs"].values()))["steps"]
    publish = next(step["run"] for step in steps if "publish.py --release" in (step.get("run") or ""))
    assert publish.strip() in README.read_text(encoding="utf-8")


def test_tick_marker_expression_is_the_pipeline_one():
    steps = next(iter(pipeline()["jobs"].values()))["steps"]
    runs = " ".join(step.get("run") or "" for step in steps)
    assert '-m "not network and not ci_only and not docs"' in runs
