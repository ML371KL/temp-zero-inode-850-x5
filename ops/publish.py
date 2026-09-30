"""Публикация выпуска X5 в орфан-ветку `data` и сверка через боевую дверь.

    python ops/publish.py --fetch-state var/state              скачать ветку data
                                                               (+ её коммит в var/state/.commit)
    python ops/publish.py --release var/release/latest.json \
                          --expect-commit "$(cat var/state/.commit)"   опубликовать
    python ops/publish.py --verify https://tzi-850-x5.pages.dev/api/model \
                          --release var/release/latest.json    сверить
    python ops/publish.py --rollback [--note "почему"]         откат на previous.json
    python ops/publish.py --reopen <id записи> --note "почему" переоткрыть запись журнала

Ветка `data` публичного репозитория — четыре файла:

* `latest.json`   — текущий выпуск (его читает Pages-функция `/api/model`);
* `previous.json` — прежний `latest.json` (откат — вернуть его на место);
* `journal.json`  — блок `journal` текущего выпуска (журнал прогнозов);
* `history.json`  — по строке на каждый выпуск, откат и переоткрытие записи.

Ветка переписывается ОДНИМ коммитом без истории (временный каталог, `git init`,
файлы, коммит от github-actions[bot], принудительный push) — репозиторий не
растёт, коммиты ветки не хранятся: объяснение правки — строкой history.json
(`--note`). Конвейер передаёт в публикацию коммит, прочитанный шагом
«Состояние из ветки data» (`--expect-commit`): любая запись в ветку после
этого шага (откат, переоткрытие) — провал, ветка не тронута. Push идёт с
`--force-with-lease` на тот же коммит.

Журнал неизменяем: каждая прошлая запись обязана остаться с теми же полями;
меняться могут только `actual` и `errors`, и только пока `actual` был null
(факт внесли после отчёта). Нарушение — провал, ветка не тронута. Ошибочный
факт исправляется переоткрытием записи (`--reopen`: `actual` и `errors` →
null, строка в history.json); следующий выпуск закроет её фактом из
`data/facts/actuals.json`.

В ветку попадает только то, что дверь `/api/model` сочтёт выпуском: объект с
объектами `meta` и `fair_value` и `meta.payload_sha256` из 64 шестнадцатеричных
символов (`release_problem` — те же ворота, что в `functions/api/model.js`);
полный контракт проверяет сборка.

Выпуск с тем же `payload_sha256`, что уже лежит в `latest.json`, не
публикуется повторно: иначе `previous.json` затёрся бы копией текущего.

Доступ к GitHub: в Actions — `GITHUB_TOKEN` из окружения (заголовок
авторизации передаётся git через `-c`, на диск и в журнал не пишется); на
ноутбуке — обычные учётные данные git.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from indicators.http import USER_AGENT, strict_json  # noqa: E402
from indicators.runlog import report  # noqa: E402

BRANCH = "data"
FILES = ("latest.json", "previous.json", "journal.json", "history.json")
# Коммит ветки, прочитанный --fetch-state (пусто — ветки не было).
STATE_COMMIT = ".commit"
BOT_NAME = "github-actions[bot]"
BOT_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"
DEFAULT_RELEASE = ROOT / "var" / "release" / "latest.json"
PUBLIC_URL = "https://tzi-850-x5.pages.dev/api/model"
# raw.githubusercontent.com кэширует до 5 мин, край Pages — ещё сверху:
# 20 попыток × 30 с ≈ 10 мин. Кэш не обходится параметром — проверялось бы не
# то, что видит браузер.
VERIFY_ATTEMPTS = 20
VERIFY_PAUSE = 30.0
JOURNAL_MUTABLE = ("actual", "errors")
# Хэш выпуска — 64 шестнадцатеричных символа (как ворота двери functions/api/model.js).
SHA256_HEX = re.compile(r"[0-9a-fA-F]{64}")


class PublishError(Exception):
    def __init__(self, step: str, reason: str):
        super().__init__(f"{step}: {reason}")
        self.step, self.reason = step, reason


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ----------------------------------------------------------------- git

def _auth_args(remote: str) -> list[str]:
    """Заголовок авторизации для github.com из `GITHUB_TOKEN` (как у
    actions/checkout): только в аргументах процесса, не в конфиге на диске."""
    token = os.environ.get("GITHUB_TOKEN")
    if not token or not remote.startswith("https://github.com/"):
        return []
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return ["-c", f"http.https://github.com/.extraheader=AUTHORIZATION: basic {basic}"]


class Git:
    def __init__(self, workdir: Path, remote: str):
        self.workdir, self.remote = workdir, remote
        self.auth = _auth_args(remote)

    def run(self, *args: str, network: bool = False, check: bool = True,
            step: str = "git") -> subprocess.CompletedProcess:
        cmd = ["git", "-c", "core.autocrlf=false", "-c", "init.defaultBranch=publish",
               *(self.auth if network else []), *args]
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        result = subprocess.run(cmd, cwd=self.workdir, capture_output=True, env=env)
        if check and result.returncode != 0:
            # Команда печатается без заголовка авторизации.
            shown = " ".join(args)
            error = result.stderr.decode("utf-8", errors="replace").strip()[-800:]
            raise PublishError(step, f"git {shown} → код {result.returncode}: {error}")
        return result

    def text(self, *args: str, **kw) -> str:
        return self.run(*args, **kw).stdout.decode("utf-8", errors="replace").strip()


def default_remote() -> str:
    try:
        out = subprocess.run(["git", "remote", "get-url", "origin"], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise PublishError("настройка", "не задан --remote, а у репозитория нет origin") from exc
    if not out:
        raise PublishError("настройка", "не задан --remote, а у репозитория нет origin")
    return out


def read_branch(git: Git, branch: str) -> tuple[str | None, dict[str, bytes]]:
    """(коммит ветки или None, {файл: байты}) — текущее состояние ветки."""
    step = "чтение ветки data"
    git.run("init", "-q", step=step)
    heads = git.text("ls-remote", "--heads", git.remote, f"refs/heads/{branch}",
                     network=True, step=step)
    if not heads:
        return None, {}
    git.run("fetch", "-q", "--depth", "1", "--no-tags", git.remote, f"refs/heads/{branch}",
            network=True, step=step)
    commit = git.text("rev-parse", "FETCH_HEAD", step=step)
    names = git.text("ls-tree", "--name-only", commit, step=step).splitlines()
    files = {name: git.run("show", f"{commit}:{name}", step=step).stdout
             for name in FILES if name in names}
    return commit, files


def write_branch(git: Git, branch: str, files: dict[str, bytes], message: str,
                 lease: str | None) -> str:
    """Один коммит без родителя с ровно этими файлами → force-with-lease push."""
    step = "публикация в ветку data"
    for name in FILES:
        path = git.workdir / name
        if name in files:
            path.write_bytes(files[name])
        elif path.exists():
            path.unlink()
    git.run("add", "--", *[name for name in FILES if name in files], step=step)
    git.run("-c", f"user.name={BOT_NAME}", "-c", f"user.email={BOT_EMAIL}",
            "commit", "-q", "-m", message, step=step)
    commit = git.text("rev-parse", "HEAD", step=step)
    git.run("push", "-q", f"--force-with-lease=refs/heads/{branch}:{lease or ''}",
            git.remote, f"HEAD:refs/heads/{branch}", network=True, step=step)
    return commit


# ----------------------------------------------------------------- журнал и история

def journal_problems(old: dict | None, new) -> list[str]:
    """Неизменяемость журнала: прошлые записи на месте и не переписаны."""
    if not isinstance(new, dict) or not isinstance(new.get("entries"), list):
        return ["в выпуске нет блока journal с entries"]
    problems, seen = [], {}
    for entry in new["entries"]:
        key = entry.get("id") if isinstance(entry, dict) else None
        if key is None:
            problems.append(f"запись журнала без id: {str(entry)[:120]}")
        elif key in seen:
            problems.append(f"запись {key} повторяется")
        else:
            seen[key] = entry
    if not old:
        return problems
    if not isinstance(old, dict) or not isinstance(old.get("entries", []), list):
        return problems + ["journal.json в ветке data — не объект с entries"]
    for entry in old.get("entries") or []:
        key = entry.get("id")
        now = seen.get(key)
        if now is None:
            problems.append(f"запись {key} пропала из журнала")
            continue
        frozen = set(entry) | set(now)
        if entry.get("actual") is None:
            frozen -= set(JOURNAL_MUTABLE)
        for field in sorted(frozen):
            if entry.get(field) != now.get(field):
                problems.append(f"запись {key}: поле {field} изменено "
                                f"({json.dumps(entry.get(field), ensure_ascii=False)[:80]} → "
                                f"{json.dumps(now.get(field), ensure_ascii=False)[:80]})")
    return problems


def _get(payload: dict, *path):
    node = payload
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def history_row(payload: dict, *, event: str, previous_sha: str | None,
                note: str | None = None) -> dict:
    """Строка заголовка выпуска для history.json (`note` — объяснение оператора)."""
    row = {"published_at": now_utc(), "event": event,
            "payload_sha256": _get(payload, "meta", "payload_sha256"),
            "previous_sha256": previous_sha,
            "generated_at": _get(payload, "meta", "generated_at"),
            "valuation_date": _get(payload, "meta", "valuation_date"),
            "facts_date": _get(payload, "meta", "facts_date"),
            "book_version": _get(payload, "meta", "book_version"),
            "engine_commit": _get(payload, "meta", "engine_commit"),
            "price": _get(payload, "market", "price"),
            "price_status": _get(payload, "market", "price_status"),
            "printed_central": _get(payload, "headline", "printed_central"),
            "printed_band": _get(payload, "headline", "printed_band"),
            "bytes": _get(payload, "meta", "bytes")}
    if note:
        row["note"] = note
    return row


def dump_history(rows: list[dict]) -> bytes:
    """По строке на выпуск — дифф и чтение глазами."""
    body = ",\n".join(json.dumps(row, ensure_ascii=False, allow_nan=False) for row in rows)
    return ("[\n" + body + "\n]\n").encode("utf-8")


def dump_json(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=1, allow_nan=False) + "\n").encode("utf-8")


def _load(step: str, name: str, raw: bytes | None):
    if raw is None:
        return None
    try:
        return strict_json(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise PublishError(step, f"{name} в ветке data не читается: {exc}") from exc


def _sha(payload) -> str | None:
    return _get(payload, "meta", "payload_sha256") if isinstance(payload, dict) else None


def release_problem(payload) -> str | None:
    """Те же лёгкие ворота, что у двери данных (`functions/api/model.js`, releaseProblem):
    выпуск — объект с объектами `meta` и `fair_value` и `meta.payload_sha256` из 64
    шестнадцатеричных символов. Файл, который дверь выпуском не сочтёт, в ветку не
    попадает. Полный контракт — дело сборки (`model.payload.validate`, шаг «Проверка
    контракта»): publish от ядра не зависит. None — годен."""
    if not isinstance(payload, dict):
        return "не объект"
    if not isinstance(payload.get("meta"), dict):
        return "нет объекта meta"
    if not isinstance(payload.get("fair_value"), dict):
        return "нет объекта fair_value"
    sha = payload["meta"].get("payload_sha256")
    if not sha:
        return "нет meta.payload_sha256"
    if not isinstance(sha, str) or not SHA256_HEX.fullmatch(sha):
        return "meta.payload_sha256 — не 64 шестнадцатеричных символа"
    return None


# ----------------------------------------------------------------- команды

def publish(release: Path, remote: str, branch: str = BRANCH, *,
            expect: str | None = None) -> str:
    """Выпуск в ветку. `expect` — коммит ветки на начало прогона (`--fetch-state`,
    пусто — ветки не было): ветка с тех пор изменилась — провал, ветка не тронута.
    None — без этой проверки (lease на коммит, прочитанный здесь же)."""
    step = "чтение выпуска"
    try:
        body = release.read_bytes()
        payload = strict_json(body.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise PublishError(step, f"{release}: {exc}") from exc
    problem = release_problem(payload)
    if problem:
        raise PublishError(step, problem)
    sha = _sha(payload)

    with tempfile.TemporaryDirectory(prefix="x5-data-", ignore_cleanup_errors=True) as tmp:
        git = Git(Path(tmp), remote)
        lease, files = read_branch(git, branch)
        if expect is not None and (lease or "") != expect:
            raise PublishError("чтение ветки data",
                               f"ветка {branch} изменилась во время прогона (в начале "
                               f"{expect[:12] or 'ветки не было'}, сейчас "
                               f"{lease[:12] if lease else 'ветки нет'}) — выпуск не "
                               "опубликован, следующий прогон начнёт с нового состояния")
        old = _load("чтение ветки data", "latest.json", files.get("latest.json"))
        old_sha = _sha(old)
        if old_sha == sha:
            return (f"готово: выпуск {sha[:12]} уже лежит в ветке {branch} — "
                    "ветка не тронута")

        problems = journal_problems(
            _load("чтение ветки data", "journal.json", files.get("journal.json")),
            payload.get("journal"))
        if problems:
            for problem in problems:
                print(f"  ЖУРНАЛ: {problem}", file=sys.stderr)
            raise PublishError("проверка журнала",
                               f"нарушений {len(problems)}: {problems[0]}")

        history = _load("чтение ветки data", "history.json", files.get("history.json")) or []
        if not isinstance(history, list):
            raise PublishError("чтение ветки data", "history.json — не список")
        history.append(history_row(payload, event="publish", previous_sha=old_sha))
        new_files = {"latest.json": body, "journal.json": dump_json(payload["journal"]),
                     "history.json": dump_history(history)}
        if "latest.json" in files:
            new_files["previous.json"] = files["latest.json"]
        message = (f"выпуск {sha[:12]} · дата оценки "
                   f"{_get(payload, 'meta', 'valuation_date') or '—'}")
        commit = write_branch(git, branch, new_files, message, lease)
    return (f"готово: выпуск {sha[:12]} опубликован в ветку {branch} (коммит {commit[:12]}); "
            f"прежний {old_sha[:12] if old_sha else 'нет'} → previous.json; "
            f"строк истории {len(history)}")


def rollback(remote: str, branch: str = BRANCH, *, note: str | None = None) -> str:
    with tempfile.TemporaryDirectory(prefix="x5-data-", ignore_cleanup_errors=True) as tmp:
        git = Git(Path(tmp), remote)
        lease, files = read_branch(git, branch)
        if "previous.json" not in files or "latest.json" not in files:
            raise PublishError("откат", f"в ветке {branch} нет пары latest.json/previous.json")
        latest = _load("откат", "latest.json", files["latest.json"])
        previous = _load("откат", "previous.json", files["previous.json"])
        if _sha(latest) == _sha(previous):
            return f"готово: откатывать некуда — latest.json уже равен previous.json ({_sha(latest)[:12]})"
        history = _load("откат", "history.json", files.get("history.json")) or []
        history.append(history_row(previous, event="rollback", previous_sha=_sha(latest),
                                   note=note))
        new_files = dict(files, **{"latest.json": files["previous.json"],
                                   "history.json": dump_history(history)})
        commit = write_branch(git, branch, new_files,
                              f"откат: latest.json := previous.json ({_sha(previous)[:12]})", lease)
    return (f"готово: откат — в latest.json снова {_sha(previous)[:12]} (коммит {commit[:12]}); "
            f"снятый {_sha(latest)[:12]} остаётся в history.json")


def reopen(entry_id: str, note: str | None, remote: str, branch: str = BRANCH) -> str:
    """Исправление факта: закрытая запись журнала снова открыта (`actual` и
    `errors` → null), причина — строкой в history.json. Следующий выпуск
    закроет запись фактом из `data/facts/actuals.json` и пересчитает ошибки."""
    step = "переоткрытие записи журнала"
    if not (note or "").strip():
        raise PublishError(step, "нужна причина: --note \"<почему исправляется факт>\"")
    with tempfile.TemporaryDirectory(prefix="x5-data-", ignore_cleanup_errors=True) as tmp:
        git = Git(Path(tmp), remote)
        lease, files = read_branch(git, branch)
        journal = _load(step, "journal.json", files.get("journal.json"))
        entries = journal.get("entries") if isinstance(journal, dict) else None
        if not isinstance(entries, list):
            raise PublishError(step, f"в ветке {branch} нет journal.json с entries")
        entry = next((e for e in entries if isinstance(e, dict) and e.get("id") == entry_id), None)
        if entry is None:
            raise PublishError(step, f"записи {entry_id} в журнале нет; есть: "
                               + ", ".join(str(e.get("id")) for e in entries if isinstance(e, dict)))
        if entry.get("actual") is None:
            raise PublishError(step, f"запись {entry_id} не закрыта — переоткрывать нечего")
        was = entry["actual"]
        entry["actual"], entry["errors"] = None, None
        history = _load(step, "history.json", files.get("history.json")) or []
        if not isinstance(history, list):
            raise PublishError(step, "history.json — не список")
        history.append({"published_at": now_utc(), "event": "reopen", "entry": entry_id,
                        "actual_was": was, "note": note.strip(),
                        "payload_sha256": _sha(_load(step, "latest.json", files.get("latest.json")))})
        new_files = dict(files, **{"journal.json": dump_json(journal),
                                   "history.json": dump_history(history)})
        commit = write_branch(git, branch, new_files,
                              f"переоткрыта запись журнала {entry_id} (было {was})", lease)
    return (f"готово: запись {entry_id} переоткрыта (actual {was} → null, коммит {commit[:12]}); "
            "следующий выпуск закроет её фактом из data/facts/actuals.json")


def fetch_state(target: Path, remote: str, branch: str = BRANCH) -> str:
    """Файлы ветки в `target` и её коммит в `target/.commit` (пусто — ветки нет):
    публикация сверит с ним голову ветки (`--expect-commit`)."""
    target.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="x5-data-", ignore_cleanup_errors=True) as tmp:
        commit, files = read_branch(Git(Path(tmp), remote), branch)
    for name in FILES:
        path = target / name
        if name in files:
            path.write_bytes(files[name])
        elif path.exists():
            path.unlink()
    (target / STATE_COMMIT).write_text(commit or "", encoding="utf-8", newline="\n")
    if commit is None:
        return f"готово: ветки {branch} ещё нет — первый выпуск, состояния нет"
    return (f"готово: состояние ветки {branch} ({commit[:12]}) в {target}: "
            + ", ".join(name for name in FILES if name in files))


def _served(url: str, want: str) -> tuple[list[str], str | None]:
    """(проблемы, Last-Modified) одного чтения боевой двери."""
    request = urllib.request.Request(url, headers={"Accept": "application/json",
                                                   "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            ctype = response.headers.get("Content-Type") or ""
            modified = response.headers.get("Last-Modified")
            text = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return [f"HTTP {exc.code}"], None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return [f"нет ответа: {exc}"], None
    if "json" not in ctype.lower():
        return [f"content-type {ctype!r}: вместо данных отдаётся страница"], modified
    try:
        served = strict_json(text)
    except ValueError as exc:
        return [f"ответ не строгий JSON: {exc}"], modified
    got = _sha(served)
    if got != want:
        return [f"отдаётся выпуск {str(got)[:12]} вместо {want[:12]}"], modified
    return [], modified


def verify(url: str, release: Path, *, attempts: int = VERIFY_ATTEMPTS,
           pause: float = VERIFY_PAUSE) -> str:
    try:
        want = _sha(strict_json(release.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        raise PublishError("сверка", f"{release}: {exc}") from exc
    if not want:
        raise PublishError("сверка", f"{release}: нет meta.payload_sha256")
    problems: list[str] = []
    for attempt in range(1, attempts + 1):
        problems, modified = _served(url, want)
        if not problems:
            if not modified:
                print(f"  ВНИМАНИЕ: {url} без Last-Modified — сторож свежести его не увидит")
            return (f"готово: сверка пройдена с попытки {attempt}: {url} отдаёт {want[:12]}"
                    + (f", Last-Modified {modified}" if modified else ""))
        if attempt < attempts:
            print(f"  попытка {attempt}: {problems[0]}; жду {pause:g} с")
            time.sleep(pause)
    raise PublishError("сверка", f"{url} за {attempts} попыток: {problems[0]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Публикация выпуска X5 в ветку data")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--verify", metavar="URL", help="сверить боевую дверь с выпуском")
    mode.add_argument("--fetch-state", metavar="DIR", help="скачать файлы ветки data в каталог")
    mode.add_argument("--rollback", action="store_true", help="latest.json := previous.json")
    mode.add_argument("--reopen", metavar="ID",
                      help="переоткрыть закрытую запись журнала (исправление факта)")
    parser.add_argument("--note", help="причина — в history.json (для --reopen обязательна)")
    parser.add_argument("--expect-commit", metavar="COMMIT",
                        help="коммит ветки data на начале прогона (var/state/.commit; "
                             "пусто — ветки не было): изменилась — провал")
    parser.add_argument("--release", default=str(DEFAULT_RELEASE), help="файл выпуска")
    parser.add_argument("--remote", help="адрес репозитория (по умолчанию origin)")
    parser.add_argument("--branch", default=BRANCH)
    parser.add_argument("--attempts", type=int, default=VERIFY_ATTEMPTS)
    parser.add_argument("--pause", type=float, default=VERIFY_PAUSE)
    args = parser.parse_args(argv)
    try:
        if args.verify:
            line = verify(args.verify, Path(args.release), attempts=args.attempts,
                          pause=args.pause)
        else:
            remote = args.remote or default_remote()
            if args.fetch_state:
                line = fetch_state(Path(args.fetch_state), remote, args.branch)
            elif args.rollback:
                line = rollback(remote, args.branch, note=args.note)
            elif args.reopen:
                line = reopen(args.reopen, args.note, remote, args.branch)
            else:
                line = publish(Path(args.release), remote, args.branch,
                               expect=None if args.expect_commit is None
                               else args.expect_commit.strip())
    except PublishError as exc:
        report(f"ПРОВАЛ на шаге {exc.step}: {exc.reason}")
        return 1
    report(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
