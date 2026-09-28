"""Числа результатов книги в документах — подстановкой из `results.json`.

Число, которое модель считает (медиана, полосы, точка, V*, σ, доли разброса,
«что заложено в цену»…), в документе не пишется руками: оно стоит между
невидимыми метками, и значение между ними переписывает эта команда.

    <!--=headline.printed_central r0-->1 350<!--/--> ₽

Сначала путь в `data/assumptions/results.json`, через пробел — формат. Знака
`|` в метке нет: в строке таблицы markdown он разрезал бы ячейку. Метка не
открывает строку: строка, начатая с `<!--`, по CommonMark — HTML-блок, и метка
стала бы видна, а абзац и **жирный** порвались бы.
Источник — только результаты книги, а не живой выпуск: иначе документы
устаревали бы с каждым тактом.

    python ops/tools/render_numbers.py           # переписать значения в документах
    python ops/tools/render_numbers.py --check   # выход 1, если документ устарел

Путь: имена через точку; `[3]` — элемент списка; `[axis=ERP]` — единственный
элемент списка, у которого поле равно значению (порядок строк от версии к
версии меняется, имя — нет); поле может быть вложенным, условия — через `&`
(`[cell.world=M&cell.capex=high]`); `["0.50"]` — ключ с точкой; `|` в ключе
пишется как `\\|` (`by_world_regime["N\\|stress"]`).

Формат — буквы и число знаков после запятой (0–4): `r` — число, `p` — доля в
процентах (×100), `s` — число со знаком «+»/«−», `sp` — проценты со знаком
(у нуля знака нет); например `sp1` для «−2,8 %». Разряды — обычным пробелом,
дробная часть — запятой, минус — типографский, как в тексте документов.
Метка внутри `кода` или блока кода — пример синтаксиса, её команда не трогает.

Тест `tests/test_render_numbers.py`: каждый документ с метками равен своей
перерисовке, каждая метка указывает на существующий путь.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "data" / "assumptions" / "results.json"

OPEN, CLOSE = "<!--=", "<!--/-->"
# Метка не переходит строку: так её не собьёт ни перенос абзаца, ни таблица.
MARK = re.compile(r"<!--=(.+?)-->(.*?)<!--/-->")
TOKEN = re.compile(r'(\.)?([^.\[\]"]+)|\["([^"]*)"\]|\[(\d+)\]|\[([^\]=]+=[^\]]*)\]')
FORMAT = re.compile(r"(r|p|s|sp)([0-4])")
CODE = re.compile(r"(`+).*?\1")
FENCE = re.compile(r"\s*(```|~~~)")
# Начало строки до содержимого: отступ, `>` цитаты, маркеры списка.
LEAD = re.compile(r"(?:[ \t]*(?:>|(?:[-+*]|\d{1,9}[.)])(?=[ \t])))*[ \t]*")
PIPE = "\\|"          # `|` в ключе пути: экранированный, он не режет ячейку таблицы
# Каталоги, где нет документов проекта: состояние тестов, окружения, git.
SKIP_DIRS = {".git", "var", ".venv", "node_modules", "__pycache__"}


def _field(item, dotted: str):
    """Поле элемента строкой (`a.b` — вложенное) или None, если его нет."""
    for part in dotted.split("."):
        if not isinstance(item, dict) or part not in item:
            return None
        item = item[part]
    return str(item)


def resolve(data, path: str):
    """Значение по пути; KeyError с понятной причиной, если пути нет."""
    node, pos = data, 0
    while pos < len(path):
        m = TOKEN.match(path, pos)
        if not m or (m.group(2) is not None and pos and not m.group(1)):
            raise KeyError(f"путь «{path}» не разобран с позиции {pos}")
        _, name, raw, index, where = m.groups()
        if name is not None or raw is not None:
            key = name if name is not None else raw.replace(PIPE, "|")
            if not isinstance(node, dict) or key not in node:
                raise KeyError(f"нет ключа «{key}» в «{path[:pos] or 'results.json'}»")
            node = node[key]
        elif index is not None:
            if not isinstance(node, list) or int(index) >= len(node):
                raise KeyError(f"нет элемента [{index}] в «{path[:pos]}»")
            node = node[int(index)]
        else:
            conds = [c.partition("=") for c in where.split("&")]
            if not all(eq for _, eq, _ in conds):
                raise KeyError(f"[{where}]: условие без «=»")
            hits = [item for item in (node if isinstance(node, list) else [])
                    if all(_field(item, field) == want for field, _, want in conds)]
            if len(hits) != 1:
                raise KeyError(f"[{where}] в «{path[:pos]}»: строк {len(hits)}, нужна одна")
            node = hits[0]
        pos = m.end()
    return node


def number(value: float, digits: int, signed: bool = False) -> str:
    text = f"{abs(value):,.{digits}f}".replace(",", " ").replace(".", ",")
    if not float(f"{abs(value):.{digits}f}"):
        return text                       # у округлённого нуля знака нет: не «−0,0»
    if value < 0:
        return "−" + text
    return "+" + text if signed else text


def formatted(value, fmt: str) -> str:
    m = FORMAT.fullmatch(fmt)
    if not m:
        raise ValueError(f"неизвестный формат «{fmt}»")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"формат {fmt} ждёт число, а в пути {value!r}")
    kind, digits = m.groups()
    return number(value * 100 if "p" in kind else value, int(digits), "s" in kind)


def _marks(segment: str, results: dict, where: str, errors: list[str]) -> str:
    marks = MARK.findall(segment)
    if segment.count(OPEN) != len(marks) or segment.count(CLOSE) != len(marks):
        errors.append(f"{where}: метка не закрыта на той же строке")
        return segment
    for spec, _ in marks:
        if "|" in spec.replace(PIPE, ""):
            errors.append(f"{where}: {spec}: «|» в метке режет ячейку таблицы — писать «{PIPE}»")
            return segment

    def put(m):
        path, _, fmt = m.group(1).rpartition(" ")
        try:
            value = formatted(resolve(results, path), fmt)
        except (KeyError, ValueError) as exc:
            errors.append(f"{where}: {m.group(1)}: {exc.args[0]}")
            return m.group(0)
        return f"{OPEN}{m.group(1)}-->{value}{CLOSE}"

    return MARK.sub(put, segment)


def render(text: str, results: dict) -> tuple[str, list[str]]:
    """Текст с переписанными значениями и список ошибок меток (строка: причина)."""
    errors = []
    lines = text.split("\n")
    fenced = False
    for index, line in enumerate(lines):
        if FENCE.match(line):
            fenced = not fenced
        if fenced or (OPEN not in line and CLOSE not in line):
            continue
        if line.startswith((OPEN, CLOSE), LEAD.match(line).end()):
            errors.append(f"строка {index + 1}: метка в начале строки — CommonMark прочтёт "
                          "строку как HTML-блок; перенести перевод строки выше")
        # Метка в `коде` или в блоке кода — пример синтаксиса в тексте, а не метка.
        pieces, pos = [], 0
        for code in CODE.finditer(line):
            pieces += [_marks(line[pos:code.start()], results, f"строка {index + 1}", errors), code.group(0)]
            pos = code.end()
        pieces.append(_marks(line[pos:], results, f"строка {index + 1}", errors))
        lines[index] = "".join(pieces)
    return "\n".join(lines), errors


def documents(root: Path = ROOT):
    """Документы `*.md` с метками: путь и текст (байты как есть, с их переводами строк)."""
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            if name.endswith(".md"):
                path = Path(folder) / name
                text = path.read_bytes().decode("utf-8")
                if OPEN in text or CLOSE in text:
                    yield path, text


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true",
                        help="ничего не писать; выход 1, если документ устарел или метка сломана")
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    results = json.loads((args.root / RESULTS.relative_to(ROOT)).read_text(encoding="utf-8"))
    bad = 0
    for path, text in documents(args.root):
        name = path.relative_to(args.root).as_posix()
        new, errors = render(text, results)
        for error in errors:
            print(f"{name}: {error}", file=sys.stderr)
        bad += bool(errors)
        if new == text:
            continue
        if args.check:
            print(f"устарел: {name}", file=sys.stderr)
            bad += 1
        else:
            path.write_bytes(new.encode("utf-8"))
            print(f"записано: {name}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
