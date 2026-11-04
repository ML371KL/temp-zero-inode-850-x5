# -*- coding: utf-8 -*-
"""Шаги книги X5 при приёме версии миров ставок (их зовёт `ops/tools/adopt_worlds.py` по манифесту
`data/assumptions/worlds_dependents.yaml`).

    python -B ops/tools/worlds_book.py quick --out var/adopt/quick.json   # числа книги из таблиц → JSON отчёта
    python -B ops/tools/worlds_book.py bump                               # номер книги +1 в младшем разряде, дата
    python -B ops/tools/worlds_book.py walk                               # прогулка «книга до приёма → книга»
    python -B ops/tools/worlds_book.py freeze ФАЙЛ…                       # метки чисел → текст (журналы прежней версии)

`quick` — точка (низ / центр / верх), миры, медиана полосы и её печать из `results.json` (2 000 прогонов, зерно
книги): перед приёмом — таблицы книги до приёма, после шага таблиц — новые (формат — `850-worlds/model-kit/README.md`).

`bump` (только при смене версии миров): `meta.version` (младший разряд + 1), `meta.date` — день приёма по Москве
(от него считается возраст книги в гейте `book_update`), строка 1 книги. Цена, дата оценки, зерно — без изменений.

`freeze` — метки `<!--=путь формат-->число<!--/-->` в журналах прежней версии книги (её `V<версия>-CHANGES.md` и
запись истории `docs/CHANGELOG.md`) — текстом, числа остаются: иначе перерисовка меток после таблиц новой версии
молча переписала бы прошлое (так делает `render_numbers.py --freeze` книг «Ленты» и Магнита).

`walk` — точка ядра на стартовой книге (машинная книга коммита до приёма, `WORLDS_START_COMMIT`) с заменой ключей
по одному, накопительно: номер и дата книги, дата кривых, мир N, мир H, мир M (с `worlds.source`), коридоры
`checks` (подпись шага миров — `labels.walk_step` индекса источника). Пишет
`data/assumptions/evidence/worlds/walk-<версия>.json` и `.txt` (LF). Код 1, если последний шаг ≠ прямому расчёту
книги или книга после шагов ≠ конечной.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BOOK_DIR = ROOT / "data" / "assumptions"
BOOK = BOOK_DIR / "assumptions.yaml"
MSK = dt.timezone(dt.timedelta(hours=3))
REL = 1e-12


def worlds_entry() -> tuple[str, dict]:
    v, src = os.environ.get("WORLDS_VERSION"), os.environ.get("WORLDS_SOURCE_DIR")
    if not v or not src:
        sys.exit("нет WORLDS_VERSION / WORLDS_SOURCE_DIR: шаг зовёт команда приёма миров")
    return v, json.loads((Path(src) / "versions.json").read_text(encoding="utf-8"))["versions"][v]


# ----------------------------------------------------------------------------------------------- quick
def quick(out: Path) -> int:
    r = json.loads((BOOK_DIR / "results.json").read_text(encoding="utf-8"))
    p, b = r["point"], r["band"]
    nums = {"point": p["central"], "median": b["stats"]["median"], "low": p["low"], "high": p["high"],
            "print": b["printed"]["median"], "worlds": {w: r["worlds_only"][w]["price"] for w in ("N", "H", "M")},
            "runs": b["draws"], "seed": b["seed"], "book_version": r["book"]["version"],
            "band": [b["stats"]["p10"], b["stats"]["p90"]], "source": "data/assumptions/results.json"}
    out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(nums, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"книга {nums['book_version']}: медиана {nums['median']:.1f} (печать {nums['print']:.0f}), точка "
          f"{nums['low']:.1f} / {nums['point']:.1f} / {nums['high']:.1f} ₽")
    return 0


# ----------------------------------------------------------------------------------------------- bump
def bump() -> int:
    import yaml

    text = BOOK.read_text(encoding="utf-8")
    old = str(yaml.safe_load(text)["meta"]["version"])
    parts = old.split(".")
    new = ".".join(parts[:-1] + [str(int(parts[-1]) + 1)])
    today = dt.datetime.now(MSK).date().isoformat()
    lines = text.split("\n")
    head = re.compile(rf"^(# Книга допущений X5 ){re.escape(old)}( — машинный вид.*)$")
    if not head.match(lines[0]):
        sys.exit(f"строка 1 книги не по образцу: {lines[0][:80]}")
    lines[0] = head.sub(rf"\g<1>{new}\g<2>", lines[0])
    i = lines.index("meta:")
    for j in range(i + 1, i + 6):
        if lines[j] == f"  version: '{old}'":
            lines[j] = f"  version: '{new}'"
        elif lines[j].startswith("  date: '"):
            lines[j] = f"  date: '{today}'"
    BOOK.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    A = yaml.safe_load(BOOK.read_text(encoding="utf-8"))
    if (str(A["meta"]["version"]), str(A["meta"]["date"])) != (new, today):
        sys.exit("после правки meta.version / meta.date книги не читаются новыми значениями")
    print(f"книга {old} → {new}, дата книги {today}")
    return 0


# ----------------------------------------------------------------------------------------------- walk
def walk() -> int:
    sys.path.insert(0, str(ROOT))
    from model.book import load_book
    from model.facts import load_facts
    from model.grid import evaluate

    wv, entry = worlds_entry()
    start_commit = os.environ.get("WORLDS_START_COMMIT")
    if not start_commit:
        sys.exit("нет WORLDS_START_COMMIT: стартовую книгу знает команда приёма миров")
    old_text = subprocess.run(["git", "-C", str(ROOT), "show", f"{start_commit}:data/assumptions/assumptions.yaml"],
                              capture_output=True, check=True).stdout
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "a.yaml"
        p.write_bytes(old_text)
        start = load_book(p)
    end = load_book(BOOK)
    facts = load_facts()

    def pt(A):
        P = evaluate(A, facts).point
        return {"low": P.low, "central": P.central, "high": P.high}

    label = entry["labels"]["walk_step"]
    v0, v1 = str(start["meta"]["version"]), str(end["meta"]["version"])
    steps = []
    if (v0, start["meta"]["date"]) != (v1, end["meta"]["date"]):
        steps.append((f"Версия {v1} от {end['meta']['date']} (номер и дата книги)", [("meta", "version"), ("meta", "date")]))
    steps += [(f"{label}: дата кривых {end['meta']['curve_as_of']}", [("meta", "curve_as_of")]),
              (f"{label}: мир N", [("worlds", "N"), ("worlds", "source")]),
              (f"{label}: мир H", [("worlds", "H")]),
              (f"{label}: мир M", [("worlds", "M")]),
              ("коридоры гейтов checks (лист financing-valuation/gates.py)", [("checks",)])]
    A = copy.deepcopy(start)
    rows = [dict(step=f"книга {v0} (старт)", **pt(A))]
    for name, keys in steps:
        for k in keys:
            node_a, node_e = A, end
            for part in k[:-1]:
                node_a, node_e = node_a[part], node_e[part]
            node_a[k[-1]] = copy.deepcopy(node_e[k[-1]])
        rows.append(dict(step=name, **pt(A)))
    direct = pt(end)
    consistent = all(abs(rows[-1][k] - v) <= REL * max(1.0, abs(v)) for k, v in direct.items())
    res = {"from": v0, "to": v1, "worlds_version": wv, "rows": rows, "direct": direct,
           "consistent": consistent, "same_book": A == end}
    lines = [f"{'шаг':<64}{'низ':>9}{'центр':>9}{'верх':>9}{'Δцентр':>9}"]
    prev = None
    for r in rows:
        d = "" if prev is None else f"{r['central'] - prev['central']:+9.1f}"
        lines.append(f"{r['step'][:63]:<64}{r['low']:>9.1f}{r['central']:>9.1f}{r['high']:>9.1f}{d:>9}")
        prev = r
    lines += ["", f"последний шаг = прямой расчёт конечной книги: {'ДА' if consistent else 'НЕТ'} "
                  f"(прямой: {direct['low']:.1f} / {direct['central']:.1f} / {direct['high']:.1f})",
              f"книга после шагов = конечная книга целиком: {'ДА' if res['same_book'] else 'НЕТ'}"]
    out = BOOK_DIR / "evidence" / "worlds"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"walk-{v1}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n", encoding="utf-8",
                                         newline="\n")
    (out / f"walk-{v1}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print("\n".join(lines))
    return 0 if consistent and res["same_book"] else 1


def freeze(files: list[Path]) -> int:
    mark = re.compile(r"<!--=(.+?)-->(.*?)<!--/-->")
    for f in files:
        p = ROOT / f
        text = p.read_bytes().decode("utf-8")
        new, n = mark.subn(lambda m: m.group(2), text)
        if n:
            p.write_bytes(new.encode("utf-8"))
        print(f"{f}: меток снято: {n}")
    return 0


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("quick")
    q.add_argument("--out", type=Path, default=Path("var/adopt/quick.json"))
    sub.add_parser("bump")
    sub.add_parser("walk")
    fz = sub.add_parser("freeze")
    fz.add_argument("files", nargs="+", type=Path)
    a = ap.parse_args(argv)
    if a.cmd == "freeze":
        return freeze(a.files)
    return quick(a.out) if a.cmd == "quick" else bump() if a.cmd == "bump" else walk()


if __name__ == "__main__":
    sys.exit(main())
