"""`ops/publish.py`: орфан-ветка data на локальном «удалённом» репозитории,
неизменяемость журнала, откат, состояние, сверка через HTTP-дверь."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

# Публикация — git-операции в песочнице (секунды–десятки секунд на тест): обвязка, а не число
# выпуска; такт её не гоняет, CI — да.
pytestmark = pytest.mark.ci_only

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops import publish  # noqa: E402


def git(*args, cwd=None) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          check=True, encoding="utf-8").stdout.strip()


@pytest.fixture(autouse=True)
def no_run_summary(monkeypatch):
    """Внутри Actions тесты не пишут в сводку самого прогона."""
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)


@pytest.fixture
def remote(tmp_path, monkeypatch) -> str:
    path = tmp_path / "remote.git"
    git("init", "-q", "--bare", str(path))
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    return str(path)


def entry(i: int, actual=None) -> dict:
    return {"id": f"x5.adj_margin:2026H{i}", "target": "x5.adj_margin", "period": f"2026H{i}",
            "recorded_at": "2026-09-28", "release_sha": "s" * 64, "forecast": 0.0612,
            "benchmarks": {"год назад": 0.0598}, "actual": actual, "errors": None}


def release(tmp_path: Path, sha_char: str, entries: list[dict], name: str | None = None) -> Path:
    body = {"schema": "x5-v1",
            "meta": {"payload_sha256": sha_char * 64, "valuation_date": "2026-09-28",
                     "generated_at": "2026-09-28T16:55:00+00:00", "bytes": 1234},
            "market": {"price": 1802.5, "price_status": "live"},
            "headline": {"printed_central": 2150, "printed_band": [1700, 2600]},
            "journal": {"entries": entries, "rule": "правило", "status": "копит"}}
    path = tmp_path / (name or f"release_{sha_char}.json")
    path.write_text(json.dumps(body, ensure_ascii=False, separators=(",", ":")) + "\n",
                    encoding="utf-8", newline="\n")
    return path


def branch_files(remote: str) -> dict[str, str]:
    names = git("--git-dir", remote, "ls-tree", "--name-only", "data").splitlines()
    return {n: git("--git-dir", remote, "show", f"data:{n}") for n in names}


def head(remote: str) -> str:
    return git("--git-dir", remote, "rev-parse", "data")


def test_first_and_second_publication(tmp_path, remote):
    first = release(tmp_path, "a", [entry(1)])
    line = publish.publish(first, remote)
    assert line.startswith("готово: выпуск aaaaaaaaaaaa опубликован")
    files = branch_files(remote)
    assert set(files) == {"latest.json", "journal.json", "history.json"}
    assert files["latest.json"] + "\n" == first.read_text(encoding="utf-8")
    assert json.loads(files["journal.json"])["entries"] == [entry(1)]
    rows = json.loads(files["history.json"])
    assert len(rows) == 1 and rows[0]["payload_sha256"] == "a" * 64
    assert rows[0]["previous_sha256"] is None and rows[0]["event"] == "publish"
    assert git("--git-dir", remote, "log", "-1", "--format=%an <%ae>", "data") == \
        f"{publish.BOT_NAME} <{publish.BOT_EMAIL}>"

    second = release(tmp_path, "b", [entry(1), entry(2)])
    publish.publish(second, remote)
    files = branch_files(remote)
    assert set(files) == set(publish.FILES)
    assert json.loads(files["previous.json"])["meta"]["payload_sha256"] == "a" * 64
    assert json.loads(files["latest.json"])["meta"]["payload_sha256"] == "b" * 64
    rows = json.loads(files["history.json"])
    assert [r["payload_sha256"][0] for r in rows] == ["a", "b"]
    assert rows[1]["previous_sha256"] == "a" * 64
    # Ветка без истории: один коммит без родителя.
    assert git("--git-dir", remote, "rev-list", "--count", "data") == "1"


def test_same_release_is_not_republished(tmp_path, remote):
    publish.publish(release(tmp_path, "a", [entry(1)]), remote)
    publish.publish(release(tmp_path, "b", [entry(1)]), remote)
    before = head(remote)
    line = publish.publish(release(tmp_path, "b", [entry(1)], name="again.json"), remote)
    assert "уже лежит" in line
    assert head(remote) == before
    assert json.loads(branch_files(remote)["previous.json"])["meta"]["payload_sha256"] == "a" * 64


@pytest.mark.parametrize("entries,problem", [
    ([], "пропала"),
    ([dict(entry(1), forecast=0.07)], "forecast"),
    ([dict(entry(1), recorded_at="2026-09-29")], "recorded_at"),
    ([entry(1), entry(1)], "повторяется"),
])
def test_journal_rewrite_is_refused_and_branch_untouched(tmp_path, remote, entries, problem):
    publish.publish(release(tmp_path, "a", [entry(1)]), remote)
    before = head(remote)
    with pytest.raises(publish.PublishError) as caught:
        publish.publish(release(tmp_path, "b", entries), remote)
    assert caught.value.step == "проверка журнала"
    assert problem in caught.value.reason
    assert head(remote) == before


def test_actual_may_be_filled_once(tmp_path, remote):
    publish.publish(release(tmp_path, "a", [entry(1)]), remote)
    filled = dict(entry(1), actual=0.0605, errors={"forecast": 0.0007})
    publish.publish(release(tmp_path, "b", [filled]), remote)
    with pytest.raises(publish.PublishError, match="actual"):
        publish.publish(release(tmp_path, "c", [dict(filled, actual=0.061)]), remote)


def test_release_without_journal_is_refused(tmp_path, remote):
    path = release(tmp_path, "a", [])
    body = json.loads(path.read_text(encoding="utf-8"))
    del body["journal"]
    path.write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(publish.PublishError, match="journal"):
        publish.publish(path, remote)


def test_rollback_puts_previous_back(tmp_path, remote):
    publish.publish(release(tmp_path, "a", [entry(1)]), remote)
    publish.publish(release(tmp_path, "b", [entry(1), entry(2)]), remote)
    line = publish.rollback(remote)
    assert line.startswith("готово: откат — в latest.json снова aaaaaaaaaaaa")
    files = branch_files(remote)
    assert files["latest.json"] == files["previous.json"]
    assert json.loads(files["latest.json"])["meta"]["payload_sha256"] == "a" * 64
    rows = json.loads(files["history.json"])
    assert rows[-1]["event"] == "rollback" and rows[-1]["previous_sha256"] == "b" * 64
    # Журнал — запись того, что было опубликовано: откат его не трогает.
    assert len(json.loads(files["journal.json"])["entries"]) == 2
    assert "откатывать некуда" in publish.rollback(remote)


def test_fetch_state(tmp_path, remote):
    target = tmp_path / "state"
    target.mkdir()
    (target / "latest.json").write_text("stale", encoding="utf-8")
    assert "ещё нет" in publish.fetch_state(target, remote)
    assert not (target / "latest.json").exists()
    assert (target / ".commit").read_text(encoding="utf-8") == ""
    publish.publish(release(tmp_path, "a", [entry(1)]), remote)
    line = publish.fetch_state(target, remote)
    assert line.startswith("готово: состояние ветки data")
    assert sorted(p.name for p in target.iterdir()) == [".commit", "history.json", "journal.json",
                                                        "latest.json"]
    assert (target / ".commit").read_text(encoding="utf-8") == head(remote)


def test_rollback_during_the_run_fails_the_publication(tmp_path, remote):
    """Сценарий аудита: состояние прочитано, во время прогона откат — выпуск,
    собранный от снятого B, не затирает восстановленный A."""
    publish.publish(release(tmp_path, "a", [entry(1)]), remote)
    publish.publish(release(tmp_path, "b", [entry(1)]), remote)
    state = tmp_path / "state"
    publish.fetch_state(state, remote)
    publish.rollback(remote)
    after_rollback = head(remote)
    expect = (state / ".commit").read_text(encoding="utf-8")
    with pytest.raises(publish.PublishError) as caught:
        publish.publish(release(tmp_path, "c", [entry(1)]), remote, expect=expect)
    assert "изменилась во время прогона" in caught.value.reason
    assert head(remote) == after_rollback
    assert json.loads(branch_files(remote)["latest.json"])["meta"]["payload_sha256"] == "a" * 64
    # Следующий прогон начинает с отката.
    publish.fetch_state(state, remote)
    publish.publish(release(tmp_path, "c", [entry(1)]), remote,
                    expect=(state / ".commit").read_text(encoding="utf-8"))
    files = branch_files(remote)
    assert json.loads(files["previous.json"])["meta"]["payload_sha256"] == "a" * 64


def test_expect_commit_on_the_first_release(tmp_path, remote):
    state = tmp_path / "state"
    publish.fetch_state(state, remote)
    expect = (state / ".commit").read_text(encoding="utf-8")
    assert expect == ""
    publish.publish(release(tmp_path, "a", [entry(1)]), remote, expect=expect)
    # Ветку создали, пока шёл прогон, ждавший первого выпуска, — тоже провал.
    with pytest.raises(publish.PublishError, match="ветки не было"):
        publish.publish(release(tmp_path, "b", [entry(1)]), remote, expect=expect)


def test_cli_passes_the_expected_commit(tmp_path, remote, capsys):
    publish.publish(release(tmp_path, "a", [entry(1)]), remote)
    stale = head(remote)
    publish.publish(release(tmp_path, "b", [entry(1)]), remote)
    argv = ["--release", str(release(tmp_path, "c", [entry(1)])), "--remote", remote]
    assert publish.main([*argv, "--expect-commit", stale + "\n"]) == 1
    assert "изменилась во время прогона" in capsys.readouterr().err
    assert publish.main([*argv, "--expect-commit", head(remote)]) == 0


def test_rollback_note_goes_to_history(tmp_path, remote):
    publish.publish(release(tmp_path, "a", [entry(1)]), remote)
    publish.publish(release(tmp_path, "b", [entry(1)]), remote)
    assert publish.main(["--rollback", "--note", "скачок медианы по ошибке фактов",
                         "--remote", remote]) == 0
    row = json.loads(branch_files(remote)["history.json"])[-1]
    assert row["event"] == "rollback" and row["note"] == "скачок медианы по ошибке фактов"
    assert "note" not in json.loads(branch_files(remote)["history.json"])[0]


def test_reopen_clears_actual_and_records_the_reason(tmp_path, remote):
    publish.publish(release(tmp_path, "a", [entry(1)]), remote)
    filled = dict(entry(1), actual=0.0605, errors={"forecast": 0.0007})
    publish.publish(release(tmp_path, "b", [filled, entry(2)]), remote)
    latest_before = branch_files(remote)["latest.json"]
    line = publish.reopen("x5.adj_margin:2026H1", "факт 2П взят из неверной строки", remote)
    assert line.startswith("готово: запись x5.adj_margin:2026H1 переоткрыта")
    files = branch_files(remote)
    entries = json.loads(files["journal.json"])["entries"]
    assert entries[0] == entry(1) and entries[1] == entry(2)
    assert files["latest.json"] == latest_before
    row = json.loads(files["history.json"])[-1]
    assert (row["event"], row["entry"], row["actual_was"], row["note"]) == \
        ("reopen", "x5.adj_margin:2026H1", 0.0605, "факт 2П взят из неверной строки")
    # Следующий выпуск закрывает запись исправленным фактом — журнал это принимает.
    publish.publish(release(tmp_path, "c", [dict(entry(1), actual=0.0612,
                                                 errors={"forecast": 0.0}), entry(2)]), remote)
    assert json.loads(branch_files(remote)["journal.json"])["entries"][0]["actual"] == 0.0612


@pytest.mark.parametrize("entry_id,note,problem", [
    ("x5.adj_margin:2026H1", "", "нужна причина"),
    ("x5.adj_margin:2030H1", "почему", "записи x5.adj_margin:2030H1 в журнале нет"),
    ("x5.adj_margin:2026H2", "почему", "не закрыта"),
])
def test_reopen_refusals_leave_the_branch(tmp_path, remote, entry_id, note, problem):
    filled = dict(entry(1), actual=0.0605, errors={"forecast": 0.0007})
    publish.publish(release(tmp_path, "a", [filled, entry(2)]), remote)
    before = head(remote)
    with pytest.raises(publish.PublishError, match=problem):
        publish.reopen(entry_id, note, remote)
    assert head(remote) == before


def test_lease_protects_a_concurrent_write(tmp_path, remote):
    publish.publish(release(tmp_path, "a", [entry(1)]), remote)
    work = tmp_path / "work"
    work.mkdir()
    stale = publish.Git(work, remote)
    lease, files = publish.read_branch(stale, "data")
    publish.publish(release(tmp_path, "b", [entry(1)]), remote)  # кто-то успел раньше
    with pytest.raises(publish.PublishError):
        publish.write_branch(stale, "data", files, "устаревшая запись", lease)
    assert json.loads(branch_files(remote)["latest.json"])["meta"]["payload_sha256"] == "b" * 64


def test_token_goes_only_to_github_and_never_into_messages(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghs_secret_token_value")
    assert publish._auth_args(str(tmp_path)) == []
    args = publish._auth_args("https://github.com/ML371KL/temp-zero-inode-850-x5")
    assert args[0] == "-c" and args[1].startswith("http.https://github.com/.extraheader=")
    with pytest.raises(publish.PublishError) as caught:
        publish.Git(tmp_path, "https://github.com/x/y").run("no-such-subcommand", network=True)
    assert "ghs_secret" not in str(caught.value) and "basic" not in str(caught.value)


def test_cli_publish_and_failure_lines(tmp_path, remote, capsys):
    first = release(tmp_path, "a", [entry(1)])
    assert publish.main(["--release", str(first), "--remote", remote]) == 0
    assert capsys.readouterr().out.strip().splitlines()[-1].startswith("готово:")
    bad = release(tmp_path, "b", [])
    assert publish.main(["--release", str(bad), "--remote", remote]) == 1
    assert capsys.readouterr().err.strip().splitlines()[-1].startswith(
        "ПРОВАЛ на шаге проверка журнала")


# ------------------------------------------------------------------ сверка

class Door:
    """Боевая дверь в миниатюре: отдаёт по очереди заданные ответы."""

    def __init__(self):
        self.replies: list[tuple[int, str, bytes, dict]] = []
        self.user_agents: list[str] = []
        door = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                door.user_agents.append(self.headers.get("User-Agent", ""))
                status, ctype, body, extra = (door.replies.pop(0) if len(door.replies) > 1
                                              else door.replies[0])
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                for key, value in extra.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/api/model"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def json(self, sha: str, modified: bool = True):
        extra = {"Last-Modified": "Mon, 28 Sep 2026 16:55:00 GMT"} if modified else {}
        body = json.dumps({"meta": {"payload_sha256": sha}}).encode()
        self.replies.append((200, "application/json; charset=utf-8", body, extra))


@pytest.fixture
def door():
    d = Door()
    yield d
    d.server.shutdown()


def test_verify_passes_after_the_cache_expires(tmp_path, door):
    local = release(tmp_path, "b", [])
    door.json("a" * 64)
    door.json("b" * 64)
    line = publish.verify(door.url, local, attempts=3, pause=0)
    assert line.startswith("готово: сверка пройдена с попытки 2")
    assert all(ua == publish.USER_AGENT for ua in door.user_agents)


@pytest.mark.parametrize("reply,problem", [
    ((200, "text/html", b"<html>index</html>", {}), "content-type"),
    ((503, "application/json", b'{"error":"not published yet"}', {}), "HTTP 503"),
    ((200, "application/json", b'{"meta": {"payload_sha256": NaN}}', {}), "строгий JSON"),
])
def test_verify_fails_on_wrong_door(tmp_path, door, reply, problem):
    door.replies.append(reply)
    with pytest.raises(publish.PublishError) as caught:
        publish.verify(door.url, release(tmp_path, "b", []), attempts=2, pause=0)
    assert caught.value.step == "сверка" and problem in caught.value.reason


def test_verify_stale_release_fails_via_cli(tmp_path, door, capsys):
    door.json("a" * 64)
    local = release(tmp_path, "b", [])
    assert publish.main(["--verify", door.url, "--release", str(local),
                         "--attempts", "2", "--pause", "0"]) == 1
    assert "отдаётся выпуск aaaaaaaaaaaa вместо bbbbbbbbbbbb" in capsys.readouterr().err


def test_verify_warns_without_last_modified(tmp_path, door, capsys):
    door.json("b" * 64, modified=False)
    assert publish.verify(door.url, release(tmp_path, "b", []), attempts=1, pause=0)
    assert "без Last-Modified" in capsys.readouterr().out
