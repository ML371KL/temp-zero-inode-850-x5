# -*- coding: utf-8 -*-
"""Приём версии миров ставок семейства 850 в эту модель — одной командой по манифесту модели.

    python -B ops/tools/adopt_worlds.py --version 1.7 [--source DIR] [--dry-run | --branch] [--report FILE]
            [--from-step ID] [--until-step ID] [--skip ID,...] [--no-quick] [--no-push] [--ci record|wait|off]
            [--workers N]
    python -B ops/tools/adopt_worlds.py --check [--source DIR]     # книга стоит на своей версии: хэши, ключи, правила

Источник миров — общий репозиторий семейства `temp-zero-inode-850-worlds` (`--source`, иначе переменная
`WORLDS_SOURCE_DIR`, иначе каталог `850-worlds` выше по дереву): индекс `versions.json` и каталог версии
`versions/<v>/` (запись, входы, рецепт, описание рецепта, надстройка банков). Модель держит побайтовые копии и
сверяет их хэши с индексом; версию, на которой стоит книга, и хэши её файлов команда пишет в
`data/assumptions/worlds_state.json` (руками не правится; его читают тесты и сборщики).

Манифест — `data/assumptions/worlds_dependents.yaml` (правится руками, идёт с образцом кода): что копировать из
версии, какие ключи книги пишутся из индекса, какие числа книги зависят от миров и каким правилом модели они
пересчитываются, какие команды модели пересобирают книгу, быстрые числа, таблицы, тексты, эталоны тестов, журналы.
Команда сама правил модели не знает: шаг — это файл, ключ или команда, названные манифестом. Шаги идут по порядку;
провал шага — остановка с отчётом.

Виды шагов манифеста (`kind`):
  install   файлы версии → дерево: побайтово (`verify: sha256`), со своими строками (`keep_lines`, `own_lines`),
            своей шапкой из комментариев (`keep_head`), подстановками (`subst`: `<<book_gen>>` → имя книги);
  state     записать `worlds_state.json` (версия, хэши записи, надстройки и скопированных файлов);
  set       скаляры YAML из индекса (`values: {ключ: "{record.sha256}"}`), замены подписей (`replace`);
  rule      функция модели `module:function(ctx)` → числа книги по правилу; пишутся точечно, с повторным разбором;
  run       команда модели (`cmd`), код возврата из `ok`;
  quick     команда быстрых чисел → JSON (`out`): точка, медиана, печать, низ, верх, миры — до и после приёма;
  gates     сработавшие гейты из JSON таблиц (`file`, `path`; `cmd` — команда, которая его пишет) — до и после;
  journal   записи журналов по шаблону с числами отчёта (`after` — после якоря, `before` — перед ним, иначе в конец).

Шаг с `when: change` идёт только при смене версии (книга не на `--version`): номер книги, прогулка, журналы.
На неподвижной точке (книга уже на версии) он пропускается — прогон обязан не сдвинуть ни байта дерева.
В значениях, командах и путях — поля индекса (`{record.sha256}`, `{origin}`, `{version}`, `{from.<поле>}`) и книги:
`{book.version}` — номер книги в шаблоне на начало шага (`template`, ключ `book_version_key`, по умолчанию
`meta.version`), `{book.from_version}` — на начало прогона.

Режимы:
  --dry-run (по умолчанию)  все шаги во временной копии дерева (`git worktree`, `var/adopt/<v>/tree`), в рабочую
                            копию не пишется ничего; отчёт — `var/adopt/<v>/report.json` и `.md`, в нём — файлы,
                            которые прогон изменил (`tree.changed`; на неподвижной точке — красный флаг, если не пусто);
  --branch                  в рабочей копии: ветка `worlds-<v>` (создаётся от текущего коммита или продолжается, если
                            уже текущая; дерево обязано быть чистым), коммит после каждого шага, который что-то изменил;
                            в конце — push ветки (без `--no-push`) и прогон CI (`--ci record` — номер прогона, `wait` —
                            дождаться итога). Слияние в main и выкладка — не здесь: по «да» владельца;
  --check                   ничего не пишет: копии файлов версии из `worlds_state.json` равны источнику, ключи
                            происхождения — индексу, правила дают числа шаблона (сторож тестов).

Отчёт — схема `adopt-worlds-report-v1`: числа «было → стало», сдвиг в шагах печати, гейты до и после, изменённые
числа книги, красные флаги, коммиты, CI. Его собирает сводка источника (`tools/rollout.py`).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

import yaml

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "data" / "assumptions" / "worlds_dependents.yaml"
STATE_REL = "data/assumptions/worlds_state.json"
MANIFEST_SCHEMA = "worlds-dependents-v1"
STATE_SCHEMA = "worlds-state-v1"
REPORT_SCHEMA = "adopt-worlds-report-v1"
INDEX_SCHEMA = "850-worlds-index-v1"
OK, FAILED, BAD_INPUT = 0, 1, 2
CO_AUTHOR = "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
NUM_IN_COMMENT = re.compile(r"\d[\d\s]*[.,]\d")


class Stop(Exception):
    """Шаг не прошёл: команда останавливается, отчёт пишется с причиной."""


# ----------------------------------------------------------------------------------------------- источник миров
def find_source(arg: str | None) -> Path:
    for cand in (arg, os.environ.get("WORLDS_SOURCE_DIR")):
        if cand:
            return Path(cand).resolve()
    for up in ROOT.parents:
        if (up / "850-worlds" / "versions.json").exists():
            return up / "850-worlds"
    raise Stop("источник миров не найден: --source или WORLDS_SOURCE_DIR")


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class Source:
    def __init__(self, path: Path):
        self.path = path
        idx = json.loads((path / "versions.json").read_text(encoding="utf-8"))
        if idx.get("schema") != INDEX_SCHEMA:
            raise Stop(f"индекс источника: схема {idx.get('schema')!r} ≠ {INDEX_SCHEMA}")
        self.index = idx

    def entry(self, v: str) -> dict:
        try:
            return self.index["versions"][v]
        except KeyError:
            raise Stop(f"версии {v} нет в индексе источника {self.path}") from None

    def file(self, v: str, rel: str) -> bytes:
        e = self.entry(v)
        if rel not in e["files"]:
            raise Stop(f"в версии {v} нет файла {rel}")
        data = (self.path / "versions" / v / rel).read_bytes()
        if sha256_bytes(data) != e["files"][rel]:
            raise Stop(f"источник испорчен: {v}/{rel} — хэш не равен индексу")
        return data

    def commit(self) -> str | None:
        p = subprocess.run(["git", "-C", str(self.path), "rev-parse", "--short=12", "HEAD"], capture_output=True, text=True)
        return p.stdout.strip() or None


def flat(d: Any, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(d, dict):
        for k, v in d.items():
            out.update(flat(v, f"{prefix}{k}."))
    elif isinstance(d, list) and d:                       # элементы списка — по номеру (адрес `ключ.0` точечной записи)
        for i, v in enumerate(d):
            out.update(flat(v, f"{prefix}{i}."))
    else:
        out[prefix[:-1]] = d
    return out


def ru_date(iso: str) -> str:
    try:
        return dt.date.fromisoformat(str(iso)).strftime("%d.%m.%Y")
    except ValueError:
        return str(iso)


def expand(text: str, ctx: dict[str, Any]) -> str:
    """`{record.sha256}`, `{origin}`, `{version}`, `{from.origin}`, `{book_gen}` … — поля индекса и манифеста."""
    def sub(m: re.Match) -> str:
        key = m.group(1)
        if key not in ctx:
            raise Stop(f"в значении нет поля {{{key}}}")
        return str(ctx[key])
    return re.sub(r"\{([\w.]+)\}", sub, text)


# ----------------------------------------------------------------------------------------------- точечная запись YAML
def _path(p: Any) -> tuple:
    return tuple(p) if isinstance(p, (list, tuple)) else tuple(str(p).split("."))


def _dotted(p: tuple) -> str:
    return ".".join(map(str, p))


def locate(root: yaml.Node, path: tuple) -> yaml.ScalarNode:
    node = root
    for part in path:
        if isinstance(node, yaml.MappingNode):
            for k, v in node.value:
                if str(k.value) == str(part):
                    node = v
                    break
            else:
                raise Stop(f"в YAML нет ключа {_dotted(path)} (нет «{part}»)")
        elif isinstance(node, yaml.SequenceNode):
            node = node.value[int(part)]
        else:
            raise Stop(f"{_dotted(path)}: «{part}» под скаляром")
    if not isinstance(node, yaml.ScalarNode):
        raise Stop(f"{_dotted(path)}: не число и не строка — команда пишет только скаляры")
    return node


def scalar_text(value: Any, style: str | None, decimals: int | None) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise Stop(f"число {value} не пишется в книгу")
        s = f"{value:.{decimals if decimals is not None else 12}f}".rstrip("0")     # фиксированный формат: 9e-05 YAML
        s = s + "0" if s.endswith(".") else s                                       # прочёл бы строкой
        return "0.0" if s in ("-0.0", "-0") else s
    s = str(value)
    if style == "'":
        return "'" + s.replace("'", "''") + "'"
    if style == '"':
        return json.dumps(s, ensure_ascii=False)
    return s


def get(obj: Any, path: tuple) -> Any:
    for part in path:
        if isinstance(obj, list):
            obj = obj[int(part)]
        else:
            obj = obj[next((k for k in obj if str(k) == str(part)), part)]
    return obj


def same(a: Any, b: Any, tol: float = 0.0) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        try:
            return abs(float(a) - float(b)) <= max(tol, 1e-12 * max(1.0, abs(float(a))))
        except (TypeError, ValueError):
            return False
    return a == b


def _needs_quotes(s: str) -> bool:
    try:
        return not isinstance(yaml.safe_load(s), str) or s.strip() != s or ": " in s or " #" in s or s == ""
    except yaml.YAMLError:
        return True


def set_values(path: Path, values: dict[tuple, Any], decimals: dict[tuple, int] | None = None) -> dict[str, tuple]:
    """Пишет скаляры по адресам, не трогая остальной текст; сверяет повторным разбором. → {адрес: (было, стало)}."""
    text = path.read_text(encoding="utf-8")
    before = yaml.safe_load(text)
    root = yaml.compose(text)
    edits, changed = [], {}
    for p, new in values.items():
        node = locate(root, p)
        old = get(before, p)
        if same(old, new) and type(old) is type(new):
            continue
        if isinstance(new, str):
            style = node.style if node.style in ("'", '"') else ('"' if _needs_quotes(new) else "")
        else:
            style = None
        edits.append((node.start_mark.index, node.end_mark.index, scalar_text(new, style, (decimals or {}).get(p))))
        changed[_dotted(p)] = (old, new)
    for a, b, s in sorted(edits, reverse=True):
        text = text[:a] + s + text[b:]
    after = yaml.safe_load(text)
    fb, fa = flat(before), flat(after)
    want = {_dotted(p): v for p, v in values.items()}
    bad = [k for k in fa if k not in want and not same(fa.get(k), fb.get(k))]
    bad += [k for k, v in want.items() if not same(fa.get(k), v, 10.0 ** -((decimals or {}).get(_path(k), 12)))]
    if bad or set(fa) != set(fb):
        raise Stop(f"{path.name}: точечная запись разошлась с повторным разбором: {bad[:8]}")
    if edits:
        path.write_text(text, encoding="utf-8", newline="\n")
    return changed


def numbers_in_comments(path: Path, keys: list[str]) -> list[str]:
    """Ключи, у строк которых (и в строках-продолжениях комментария под ними) есть числа: этот текст пишется руками —
    после записи числа он мог устареть. Красный флаг для сверки, не ошибка."""
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    root = yaml.compose(text)
    out = []
    for k in keys:
        try:
            node = locate(root, _path(k))
        except Stop:
            continue
        i = node.start_mark.line
        chunk = [lines[i].split("#", 1)[1] if "#" in lines[i] else ""]
        ind = len(lines[i]) - len(lines[i].lstrip())
        j = i + 1
        while j < len(lines) and lines[j].lstrip().startswith("#") and len(lines[j]) - len(lines[j].lstrip()) > ind:
            chunk.append(lines[j])
            j += 1
        if NUM_IN_COMMENT.search(" ".join(chunk)):
            out.append(k)
    return out


# ----------------------------------------------------------------------------------------------- файлы версии
def split_head(text: str) -> tuple[str, str]:
    lines = text.split("\n")
    i = 0
    while i < len(lines) and lines[i].startswith("#"):
        i += 1
    return "\n".join(lines[:i]), "\n".join(lines[i:])


def transform(f: dict, data: bytes, current: bytes | None, ctx: dict) -> bytes:
    """Байты файла версии → байты копии модели (свои строки, своя шапка, подстановки, концы строк)."""
    keep, subst, eol = f.get("keep_lines") or [], f.get("subst") or {}, f.get("eol", "as_source")
    if not (keep or subst or f.get("keep_head") or eol in ("lf", "crlf")):
        return data
    text = data.decode("utf-8").replace("\r\n", "\n")
    own = current.decode("utf-8").replace("\r\n", "\n") if current is not None else None
    if f.get("keep_head") and own is not None:
        head = split_head(own)[0]
        text = (head + "\n" if head else "") + split_head(text)[1]
    if keep:
        lines, own_lines = text.split("\n"), (own or "").split("\n")
        for n in keep:
            spec = (f.get("own_lines") or {}).get(n, (f.get("own_lines") or {}).get(str(n)))
            lines[n - 1] = expand(spec, ctx) if spec is not None else own_lines[n - 1]
        text = "\n".join(lines)
    for a, b in subst.items():
        text = text.replace(a, expand(b, ctx))
    return (text.replace("\n", "\r\n") if eol == "crlf" else text).encode("utf-8")


# ----------------------------------------------------------------------------------------------- ход
def read_state(tree: Path) -> dict | None:
    p = tree / STATE_REL
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


class Run:
    def __init__(self, args: argparse.Namespace, manifest: dict, source: Source, tree: Path, say: Callable[[str], None]):
        self.a, self.m, self.src, self.tree, self.say = args, manifest, source, tree, say
        self.v = args.version
        self.entry = source.entry(self.v)
        state = read_state(tree)
        if not state:
            raise Stop(f"нет {STATE_REL}: на какой версии миров стоит книга — неизвестно")
        self.state = state
        self.from_v = str(state["version"])
        self.from_entry = source.entry(self.from_v) if self.from_v in source.index["versions"] else None
        self.fixed = self.from_v == self.v                # книга уже на версии: прогон — проверка неподвижной точки
        self.ctx = context(manifest, self.entry, self.from_entry, self.v, self.from_v)
        self.ctx["book.from_version"] = self.book_version()
        self.ctx["book.version"] = self.ctx["book.from_version"]
        self.report: dict[str, Any] = {
            "schema": REPORT_SCHEMA, "model": manifest["model"], "version": self.v, "from_version": self.from_v,
            "mode": "branch" if args.branch else "dry-run", "status": "running",
            "source": {"path": str(source.path), "commit": source.commit()},
            "started": dt.datetime.now().isoformat(timespec="seconds"), "steps": [], "numbers": {},
            "gates": {}, "dependents": {}, "red_flags": [], "git": {},
        }
        self.commits: list[str] = []
        self.installed: dict[str, str] = {}
        self.start_commit = git(tree, "rev-parse", "HEAD").strip()   # книга до приёма (прогулка: стартовая книга)

    def book_version(self) -> str | None:
        """Номер книги в шаблоне сейчас (`{book.version}` значений, команд и путей)."""
        tpl = self.m.get("template")
        if not tpl or not (self.tree / tpl).exists():
            return None
        doc = yaml.safe_load((self.tree / tpl).read_text(encoding="utf-8"))
        try:
            return str(get(doc, _path(self.m.get("book_version_key", "meta.version"))))
        except (KeyError, IndexError, TypeError):
            return None

    def env(self) -> dict:
        env = dict(os.environ, PYTHONIOENCODING="utf-8", WORLDS_SOURCE_DIR=str(self.src.path),
                   WORLDS_VERSION=self.v, WORLDS_FROM_VERSION=self.from_v, WORLDS_START_COMMIT=self.start_commit)
        w = self.a.workers or self.m.get("workers")
        for name in self.m.get("workers_env", []):
            if w:
                env[name] = str(w)
        for k, v in (self.m.get("env") or {}).items():
            env[k] = expand(str(v), self.ctx)
        return env

    def cmd(self, spec: Any) -> list[str]:
        parts = spec if isinstance(spec, list) else str(spec).split()
        return [sys.executable if p == "{python}" else expand(str(p), self.ctx) for p in parts]

    def sh(self, spec: Any, *, timeout: float | None = None, ok: tuple = (0,), log: str | None = None) -> str:
        c = self.cmd(spec)
        self.say(f"    $ {' '.join(c[1:] if c[0] == sys.executable else c)}")
        p = subprocess.run(c, cwd=self.tree, env=self.env(), capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
        out = (p.stdout or "") + (p.stderr or "")
        if log:
            (self.var() / f"{log}.log").write_text(out, encoding="utf-8")
        if p.returncode not in ok:
            tail = "\n".join(out.strip().splitlines()[-15:])
            raise Stop(f"команда вернула {p.returncode}: {' '.join(c[-3:])}\n{tail}")
        return out

    def var(self) -> Path:
        d = ROOT / "var" / "adopt" / self.v
        d.mkdir(parents=True, exist_ok=True)
        return d

    # --- виды шагов
    def step_install(self, s: dict) -> dict:
        changed = []
        for f in s["files"]:
            data = self.src.file(self.v, f["src"])
            dst = self.tree / f["dst"]
            old = dst.read_bytes() if dst.exists() else None
            new = transform(f, data, old, self.ctx)
            if f.get("verify", "sha256") == "sha256" and sha256_bytes(new) != self.entry["files"][f["src"]]:
                raise Stop(f"{f['dst']}: копия не равна {self.v}/{f['src']} источника побайтово")
            if old != new:
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(new)
                changed.append(f["dst"])
            self.installed[f["dst"]] = f["src"]
        return {"changed": changed}

    def step_state(self, s: dict) -> dict:
        new = make_state(self.tree, self.m, self.v, self.entry, self.src.commit())
        old = read_state(self.tree) or {}
        if {k: v for k, v in old.items() if k != "source_commit"} == {k: v for k, v in new.items() if k != "source_commit"}:
            new["source_commit"] = old.get("source_commit")   # те же файлы версии: коммит источника не двигается
        self.state = new
        p = self.tree / STATE_REL
        p.write_text(json.dumps(self.state, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
        return {"changed": [STATE_REL]}

    def step_set(self, s: dict) -> dict:
        path = self.tree / s["file"]
        values = {_path(k): (expand(e, self.ctx) if isinstance(e, str) else e) for k, e in (s.get("values") or {}).items()}
        changed = set_values(path, values) if values else {}
        labels = 0
        for old_t, new_t in (s.get("replace") or {}).items():
            a, b = expand(old_t, self.ctx), expand(new_t, self.ctx)
            if a == b:
                continue
            for g in s.get("replace_in", [s["file"]]):
                for p in sorted(self.tree.glob(g)):
                    t = p.read_text(encoding="utf-8")
                    if a in t:
                        labels += t.count(a)
                        p.write_text(t.replace(a, b), encoding="utf-8", newline="\n")
        return {"values": {k: list(v) for k, v in changed.items()}, "labels": labels}

    def step_rule(self, s: dict) -> dict:
        try:
            res = call_rule(self.tree, s, RuleContext(self))
        except Stop:
            raise
        except Exception as e:                            # правило отказалось (суждение) или упало — остановка
            raise Stop(f"правило {s['function']}: {type(e).__name__}: {e}") from e
        out, flags = {}, []
        for target in res:
            path = self.tree / target["file"]
            vals = {_path(k): v for k, v in target["values"].items()}
            ch = set_values(path, vals, {_path(k): d for k, d in (target.get("decimals") or {}).items()})
            out.update({k: list(v) for k, v in ch.items()})
            if ch and s.get("comments_flag", True) and path.suffix in (".yaml", ".yml"):
                hits = numbers_in_comments(path, list(ch))
                if hits:
                    flags.append(f"{s['id']}: у {len(hits)} изменённых ключей в комментарии шаблона есть числа — "
                                 f"сверить текст: {', '.join(hits[:6])}{' …' if len(hits) > 6 else ''}")
        self.report["dependents"].update(out)
        self.report["red_flags"] += flags
        return {"values": out, "count": len(out)}

    def step_run(self, s: dict) -> dict:
        self.sh(s["cmd"], timeout=s.get("timeout"), ok=tuple(s.get("ok", [0])), log=s["id"])
        return {}

    def step_quick(self, s: dict, phase: str) -> dict:
        out = self.tree / expand(s["out"], self.ctx)
        if out.exists():
            out.unlink()
        self.sh(s["cmd"], timeout=s.get("timeout"), log=f"{s['id']}-{phase}")
        if not out.exists():
            raise Stop(f"быстрые числа не записали {s['out']}")
        nums = json.loads(out.read_text(encoding="utf-8"))
        self.report["numbers"][phase] = nums
        return {"numbers": nums}

    def step_gates(self, s: dict, phase: str) -> dict:
        if s.get("cmd"):
            self.sh(s["cmd"], timeout=s.get("timeout"), log=f"{s['id']}-{phase}")
        p = self.tree / expand(s["file"], self.ctx)
        if not p.exists():
            return {}
        data = json.loads(p.read_text(encoding="utf-8"))
        fired = get(data, _path(s["path"])) if s.get("path") else data
        flag, name = s.get("flag", "fired"), s.get("name", "name")
        if isinstance(fired, list):                       # список имён или записей {name, fired}
            names = sorted(g[name] if isinstance(g, dict) else g for g in fired
                           if not isinstance(g, dict) or g.get(flag))
        else:
            names = sorted(k for k, v in fired.items() if (v.get(flag) if isinstance(v, dict) else v))
        self.report["gates"][phase] = names
        return {"fired": names}

    def step_journal(self, s: dict) -> dict:
        changed = []
        for j in s["entries"]:
            p = self.tree / expand(j["file"], self.ctx)
            text = p.read_text(encoding="utf-8") if p.exists() else ""
            block = expand(j["text"], {**self.ctx, **self.summary_ctx()})
            if block.strip() in text:
                continue
            anchor, before = j.get("after"), j.get("before")
            if anchor and anchor in text:
                i = text.index(anchor) + len(anchor)
                text = text[:i] + "\n" + block.rstrip("\n") + text[i:]
            elif before and before in text:                # перед первым вхождением (запись истории — над прежними)
                i = text.index(before)
                text = text[:i] + block.rstrip("\n") + "\n\n" + text[i:]
            else:
                text = text.rstrip("\n") + "\n\n" + block.rstrip("\n") + "\n"
            p.write_text(text, encoding="utf-8", newline="\n")
            changed.append(j["file"])
        return {"changed": changed}

    def summary_ctx(self) -> dict:
        n = self.report["numbers"]
        b, a = n.get("before") or {}, n.get("after") or {}

        def r(x, nd=2):
            return "—" if x is None else f"{x:.{nd}f}".replace(".", ",")
        out = {"n.point_before": r(b.get("point")), "n.point_after": r(a.get("point")),
               "n.median_before": r(b.get("median")), "n.median_after": r(a.get("median")),
               "n.print_before": b.get("print", "—"), "n.print_after": a.get("print", "—")}
        for k in ("low", "high"):
            out[f"n.{k}_before"], out[f"n.{k}_after"] = r(b.get(k)), r(a.get(k))
        return out

    def steps(self) -> list[dict]:
        out, started = [], self.a.from_step is None
        for s in self.m["steps"]:
            if not started and s["id"] == self.a.from_step:
                started = True
            if started and s["id"] not in (self.a.skip or []):
                out.append(s)
            if self.a.until_step and s["id"] == self.a.until_step:
                break
        return out

    def execute(self) -> int:
        quick = next((s for s in self.m["steps"] if s["kind"] == "quick"), None)
        gates = next((s for s in self.m["steps"] if s["kind"] == "gates"), None)
        if self.a.from_step is None:
            if gates:
                self.step_gates(gates, "before")
            if quick and not self.a.no_quick:
                self.say("быстрые числа до приёма")
                t0 = time.time()
                try:
                    self.step_quick(quick, "before")
                except Stop as e:
                    self.report["red_flags"].append(f"быстрые числа до приёма не посчитаны: {str(e).splitlines()[0]}")
                self.report["steps"].append({"id": f"{quick['id']}-before", "status": "ok",
                                             "seconds": round(time.time() - t0)})
        for s in self.steps():
            if s["kind"] == "quick" and self.a.no_quick:
                continue
            self.ctx["book.version"] = self.book_version()
            if s.get("when", "always") == "change" and self.fixed:
                self.say(f"шаг {s['id']}: пропущен — книга уже на мирах {self.v} (шаг только при смене версии)")
                self.report["steps"].append({"id": s["id"], "kind": s["kind"], "status": "skipped", "seconds": 0})
                continue
            self.say(f"шаг {s['id']}: {expand(s.get('title', s['kind']), self.ctx)}")
            t0 = time.time()
            rec = {"id": s["id"], "kind": s["kind"], "title": s.get("title", "")}
            try:
                if s["kind"] == "quick":
                    res = self.step_quick(s, "after")
                elif s["kind"] == "gates":
                    res = self.step_gates(s, "after")
                else:
                    res = getattr(self, f"step_{s['kind']}")(s)
                rec.update(status="ok", **{k: v for k, v in res.items() if k != "numbers"})
            except Stop as e:
                rec.update(status="stopped", reason=str(e), seconds=round(time.time() - t0))
                self.report["steps"].append(rec)
                self.report["red_flags"].insert(0, f"остановка на шаге {s['id']}: {str(e).splitlines()[0]}")
                self.report["status"] = "stopped"
                return FAILED
            rec["seconds"] = round(time.time() - t0)
            self.report["steps"].append(rec)
            if self.a.branch:
                self.commit(s)
        self.ctx["book.version"] = self.book_version()
        self.report["book"] = {"from_version": self.ctx["book.from_version"], "version": self.ctx["book.version"]}
        self.report["status"] = "ok"
        return OK

    def tree_diff(self) -> None:
        """Сухой прогон: какие файлы дерева прогон изменил (git, с нормализацией концов строк по .gitattributes)."""
        st = git(self.tree, "status", "--porcelain", "--untracked-files=all")
        changed = [ln[3:].strip().strip('"') for ln in st.splitlines() if ln.strip()]
        changed = [p for p in changed if not p.startswith("var/")]
        self.report["tree"] = {"changed": changed}
        if changed:
            (self.var() / "tree.diff").write_text(git(self.tree, "diff", "--stat") + "\n" + git(self.tree, "diff"),
                                                  encoding="utf-8")
        if self.fixed and changed:
            self.report["red_flags"].append(f"неподвижная точка нарушена: прогон на книге, уже стоящей на {self.v}, "
                                            f"изменил {len(changed)} файлов: {', '.join(changed[:8])}")

    def commit(self, s: dict) -> None:
        st = git(self.tree, "status", "--porcelain", "--untracked-files=all")
        paths = [ln[3:].strip().strip('"').split(" -> ")[-1] for ln in st.splitlines() if ln.strip()]
        paths = [p for p in paths if not p.startswith("var/")]
        if not paths:
            return
        git(self.tree, "add", "--", *paths)
        msg = (f"Миры {self.v}, шаг {s['id']}: {expand(s.get('title', s['kind']), self.ctx)}\n\n"
               f"Команда приёма ops/tools/adopt_worlds.py --version {self.v} (источник миров {self.src.commit() or '?'}).\n\n"
               f"{CO_AUTHOR}")
        ident = self.m.get("git_identity") or {}
        flags = [x for k in ("name", "email") if ident.get(k) for x in ("-c", f"user.{k}={ident[k]}")]
        git(self.tree, *flags, "commit", "-q", "-m", msg)
        self.commits.append(git(self.tree, "rev-parse", "--short", "HEAD").strip())

    def finish(self) -> None:
        n = self.report["numbers"]
        b, a = n.get("before") or {}, n.get("after") or {}
        step = self.m.get("print_step")
        if b.get("median") is not None and a.get("median") is not None and step:
            ps = abs(a["median"] - b["median"]) / step
            n["print_steps"] = round(ps, 2)
            lim = (self.m.get("stop") or {}).get("median_print_steps", 2)
            if ps > lim:
                self.report["red_flags"].append(f"сдвиг медианы {a['median'] - b['median']:+.2f} — {ps:.1f} шага "
                                                f"печати (порог {lim}): назвать причину до выкладки")
        g = self.report["gates"]
        if "before" in g and "after" in g:
            g["new"] = sorted(set(g["after"]) - set(g["before"]))
            g["gone"] = sorted(set(g["before"]) - set(g["after"]))
            if g["new"]:
                self.report["red_flags"].append(f"новые гейты: {', '.join(g['new'])} — связать с мирами или стоп")
        self.report["git"]["commits"] = self.commits
        self.report["finished"] = dt.datetime.now().isoformat(timespec="seconds")


def context(manifest: dict, entry: dict, from_entry: dict | None, v: str, from_v: str) -> dict:
    ctx = {"version": v, "from_version": from_v, **flat(entry)}
    for k in ("record.asof", "record.curve_date", "cbr_decision"):
        if ctx.get(k):
            ctx[f"{k}_ru"] = ru_date(ctx[k])
    if from_entry:
        ctx.update({f"from.{k}": val for k, val in flat(from_entry).items()})
    for k in ("model", "title", "book_gen"):
        if k in manifest:
            ctx[k] = manifest[k]
    ctx["today"] = dt.date.today().isoformat()
    ctx["today_ru"] = dt.date.today().strftime("%d.%m.%Y")
    return ctx


def make_state(tree: Path, manifest: dict, v: str, entry: dict, source_commit: str | None) -> dict:
    files = {}
    for s in manifest["steps"]:
        if s["kind"] == "install":
            for f in s["files"]:
                files[f["dst"]] = {"src": f["src"], "sha256": sha256_bytes((tree / f["dst"]).read_bytes())}
    st = {"schema": STATE_SCHEMA, "version": v, "origin": entry["origin"], "title": entry["title"],
          "record_sha256": entry["record"]["sha256"], "record_csv_sha256": entry["record"]["csv_sha256"],
          "record_asof": entry["record"]["asof"], "curve_date": entry["record"]["curve_date"],
          "overlay_sha256": (entry.get("overlay") or {}).get("sha256"), "source_commit": source_commit, "files": files}
    return st


class RuleContext:
    """Что видит правило модели: запись и надстройка версии из дерева, индекс версии, корень дерева."""

    def __init__(self, run: "Run | None" = None, *, tree: Path | None = None, entry: dict | None = None,
                 version: str | None = None, say: Callable[[str], None] = print, manifest: dict | None = None):
        self.root = run.tree if run else tree
        self.version = run.v if run else version
        self.entry = run.entry if run else entry
        m = run.m if run else (manifest or {})
        self.say = run.say if run else say
        rec = self.root / m.get("record", "data/assumptions/worlds_source.json")
        self.record = json.loads(rec.read_bytes().decode("utf-8"))
        ov = self.root / m.get("overlay", "data/assumptions/worlds_bank.json")
        self.overlay = json.loads(ov.read_text(encoding="utf-8")) if ov.exists() else None

    def yaml(self, rel: str) -> Any:
        return yaml.safe_load((self.root / rel).read_text(encoding="utf-8"))

    def module(self, rel: str, name: str | None = None):
        return load_module(self.root / rel, name or f"adopt_{Path(rel).stem}")


def call_rule(tree: Path, s: dict, ctx: RuleContext) -> list[dict]:
    mod = load_module(tree / s["module"], f"worlds_rule_{s['id'].replace('-', '_')}")
    res = getattr(mod, s["function"])(ctx)
    return res if isinstance(res, list) else [res]


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    keep, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = keep
    return mod


def git(cwd: Path, *args: str) -> str:
    p = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, encoding="utf-8")
    if p.returncode != 0:
        raise Stop(f"git {' '.join(a for a in args if not a.startswith('user.'))[:60]}: {p.stderr.strip()[:300]}")
    return p.stdout


def load_manifest(tree: Path = ROOT) -> dict:
    m = yaml.safe_load((tree / "data" / "assumptions" / "worlds_dependents.yaml").read_text(encoding="utf-8"))
    if m.get("schema") != MANIFEST_SCHEMA:
        raise Stop(f"манифест: схема {m.get('schema')!r} ≠ {MANIFEST_SCHEMA}")
    ids = [s["id"] for s in m["steps"]]
    if len(ids) != len(set(ids)):
        raise Stop("манифест: повторяются id шагов")
    bad = [s["id"] for s in m["steps"] if s["kind"] not in
           ("install", "state", "set", "rule", "run", "quick", "gates", "journal")]
    if bad:
        raise Stop(f"манифест: неизвестный вид шага у {bad}")
    return m


# ----------------------------------------------------------------------------------------------- сторож (--check)
def check(manifest: dict, source: Source | None, tree: Path = ROOT, say: Callable[[str], None] = print) -> list[str]:
    """Книга стоит на версии своего состояния: копии файлов равны источнику (или хэшам состояния, если источника
    нет), ключи происхождения — индексу, правила манифеста дают числа шаблона."""
    state = read_state(tree)
    if not state:
        return [f"нет {STATE_REL}"]
    v = str(state["version"])
    bad = []
    for dst, f in state["files"].items():
        if sha256_bytes((tree / dst).read_bytes()) != f["sha256"]:
            bad.append(f"{dst}: хэш не равен состоянию версии {v} (файл правили руками?)")
    entry = source.entry(v) if source else None
    if entry:
        if state["record_sha256"] != entry["record"]["sha256"] or state.get("overlay_sha256") != (entry.get("overlay") or {}).get("sha256"):
            bad.append(f"{STATE_REL}: хэши записи или надстройки не равны индексу источника для {v}")
        ctx = context(manifest, entry, None, v, v)
        for s in manifest["steps"]:
            if not s.get("check", True):
                continue
            if s["kind"] == "install":
                for f in s["files"]:
                    dst = tree / f["dst"]
                    want = transform(f, source.file(v, f["src"]), dst.read_bytes(), ctx)
                    if dst.read_bytes() != want:
                        bad.append(f"{f['dst']}: не равен {v}/{f['src']} источника (с правилами манифеста)")
            elif s["kind"] == "set":
                doc = yaml.safe_load((tree / s["file"]).read_text(encoding="utf-8"))
                for k, e in (s.get("values") or {}).items():
                    want = expand(e, ctx) if isinstance(e, str) else e
                    if not same(get(doc, _path(k)), want):
                        bad.append(f"{s['file']}: {k} = {get(doc, _path(k))!r}, индекс версии {v} даёт {want!r}")
    rc = RuleContext(tree=tree, entry=entry, version=v, say=say, manifest=manifest)
    for s in manifest["steps"]:
        if s["kind"] != "rule" or not s.get("check", True):
            continue
        tol = float(s.get("tol", 0.0))
        for target in call_rule(tree, s, rc):
            doc = yaml.safe_load((tree / target["file"]).read_text(encoding="utf-8"))
            for k, want in target["values"].items():
                have = get(doc, _path(k))
                if not same(have, want, tol):
                    bad.append(f"{target['file']}: {k} = {have!r}, правило {s['id']} даёт {want!r}")
    return bad


# ----------------------------------------------------------------------------------------------- вход
def write_report(rep: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str) + "\n", encoding="utf-8")
    md = [f"# Приём миров {rep['version']} — {rep['model']} ({rep['mode']}): {rep['status']}", ""]
    if rep["red_flags"]:
        md += ["## Красные флаги", ""] + [f"- {f}" for f in rep["red_flags"]] + [""]
    n = rep.get("numbers") or {}
    if n.get("before") or n.get("after"):
        md += ["## Числа", "", "| | было | стало |", "|---|---:|---:|"]
        for k in ("point", "median", "print", "low", "high"):
            md.append(f"| {k} | {(n.get('before') or {}).get(k, '—')} | {(n.get('after') or {}).get(k, '—')} |")
        md.append("")
    g = rep.get("gates") or {}
    if g:
        md += ["## Гейты", ""] + [f"- {k}: {', '.join(v) if v else '—'}" for k, v in g.items()] + [""]
    if rep.get("dependents"):
        md += ["## Числа книги по правилам (было → стало)", ""] + [
            f"- {k}: {v[0]} → {v[1]}" for k, v in list(rep["dependents"].items())[:60]] + [""]
    if "tree" in rep:
        ch = rep["tree"]["changed"]
        md += ["## Дерево", "", f"изменено файлов: {len(ch)}" + (": " + ", ".join(ch[:20]) if ch else ""), ""]
    md += ["## Шаги", ""] + [f"- {s['id']}: {s['status']} ({s.get('seconds', '?')} с)"
                             + (f" — {s['reason'].splitlines()[0]}" if s.get("reason") else "") for s in rep["steps"]]
    path.with_suffix(".md").write_text("\n".join(md) + "\n", encoding="utf-8")


def ci_run(cwd: Path, branch: str, head: str, wait: bool) -> dict:
    """Прогоны CI ветки на этом коммите (gh); `wait` — дождаться итога."""
    for _ in range(12):
        p = subprocess.run(["gh", "run", "list", "--branch", branch, "--limit", "10", "--json",
                            "databaseId,headSha,status,conclusion,url,workflowName"],
                           cwd=cwd, capture_output=True, text=True, encoding="utf-8")
        runs = [r for r in json.loads(p.stdout or "[]") if r["headSha"] == head]
        if runs:
            if wait:
                for r in runs:
                    subprocess.run(["gh", "run", "watch", str(r["databaseId"]), "--exit-status"], cwd=cwd,
                                   capture_output=True)
                return ci_run(cwd, branch, head, wait=False)
            return {"runs": runs, "status": ",".join(sorted({r["status"] for r in runs})),
                    "conclusion": ",".join(sorted({r["conclusion"] or "" for r in runs})) or None,
                    "url": runs[0]["url"]}
        time.sleep(10)
    return {"status": "not-found"}


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--version")
    ap.add_argument("--source")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--branch", action="store_true")
    g.add_argument("--check", action="store_true")
    ap.add_argument("--report")
    ap.add_argument("--from-step")
    ap.add_argument("--until-step")
    ap.add_argument("--skip", type=lambda s: s.split(","), default=[])
    ap.add_argument("--no-quick", action="store_true")
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--ci", default="record", choices=("record", "wait", "off"))
    ap.add_argument("--workers", type=int)
    a = ap.parse_args(argv)
    say = lambda line="": print(line, flush=True)  # noqa: E731
    try:
        manifest = load_manifest()
        if a.check:
            try:
                source = Source(find_source(a.source))
            except Stop:
                source = None
                say("источник миров не найден: сверка по хэшам состояния и правилам манифеста")
            bad = check(manifest, source, ROOT, say)
            st = read_state(ROOT) or {}
            say(f"миры книги = версия {st.get('version')}" if not bad else "РАСХОЖДЕНИЯ:\n  " + "\n  ".join(bad))
            return OK if not bad else FAILED
        if not a.version:
            ap.error("--version обязателен (кроме --check)")
        source = Source(find_source(a.source))
        st = read_state(ROOT)
        if st and str(st["version"]) == a.version and not a.from_step:
            say(f"книга уже на мирах {a.version}: прогон — проверка неподвижной точки (числа обязаны не сдвинуться)")
    except Stop as e:
        say(f"ОТКАЗ: {e}")
        return BAD_INPUT
    report_path = Path(a.report) if a.report else ROOT / "var" / "adopt" / a.version / "report.json"
    tree, worktree, run = ROOT, None, None
    try:
        if a.branch:
            if git(ROOT, "status", "--porcelain", "--untracked-files=no").strip():
                raise Stop("в рабочей копии есть незакоммиченные правки — приём только с чистого дерева")
            branch = f"worlds-{a.version}"
            if git(ROOT, "branch", "--show-current").strip() != branch:
                git(ROOT, "switch", "-c", branch)
        else:
            worktree = ROOT / "var" / "adopt" / a.version / "tree"
            if worktree.exists():
                subprocess.run(["git", "-C", str(ROOT), "worktree", "remove", "--force", str(worktree)], capture_output=True)
                shutil.rmtree(worktree, ignore_errors=True)
            git(ROOT, "worktree", "add", "--detach", str(worktree), "HEAD")
            tree = worktree
        run = Run(a, manifest, source, tree, say)
        code = run.execute()
        if worktree:
            run.tree_diff()
        run.finish()
        if a.branch and code == OK and not a.no_push:
            git(ROOT, "push", "-q", "-u", "origin", f"worlds-{a.version}")
            head = git(ROOT, "rev-parse", "HEAD").strip()
            run.report["git"].update(branch=f"worlds-{a.version}", pushed=True, head=head)
            if a.ci != "off":
                run.report["git"]["ci"] = ci_run(ROOT, f"worlds-{a.version}", head, wait=a.ci == "wait")
        write_report(run.report, report_path)
        say(f"отчёт: {report_path}")
        for f in run.report["red_flags"]:
            say(f"  ФЛАГ: {f}")
        return code
    except Stop as e:
        say(f"ОТКАЗ: {e}")
        if run:
            run.report["status"] = "stopped"
            run.report["red_flags"].insert(0, str(e).splitlines()[0])
            write_report(run.report, report_path)
        return FAILED
    finally:
        if worktree and not os.environ.get("ADOPT_KEEP_TREE"):
            subprocess.run(["git", "-C", str(ROOT), "worktree", "remove", "--force", str(worktree)], capture_output=True)


if __name__ == "__main__":
    sys.exit(main())
