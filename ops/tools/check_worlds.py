"""Сверка миров книги X5 с книгой Магнита на теге (общая макро-основа двух панелей).

Миры ставок (N, H, M: ключевая, ИПЦ, продовольственный ИПЦ, бескупонная кривая,
долгосрочная инфляция) у X5 и Магнита общие: X5 берёт их из книги Магнита на теге
(`worlds.source.book`, `worlds.source.tag_commit`). Схема книги (`model/book_schema.py`)
проверяет только целостность миров самой книги X5 — хэш `worlds.source.kernel_sha256`
от её собственного содержимого. Эта команда проверяет происхождение: читает книгу
Магнита на теге из соседнего репозитория (только чтение, `git show`, без сети),
оставляет в её мирах поля схемы X5 и сверяет поле за полем и тем же хэшем.

    python ops/tools/check_worlds.py                    # репозиторий Магнита — ../magnit-850oa
    python ops/tools/check_worlds.py --magnit ПУТЬ      # или переменная X5_MAGNIT_REPO

Выход 0 — миры совпадают (или репозитория Магнита рядом нет: пропуск с сообщением),
1 — расхождение (список полей), 2 — репозиторий есть, но тег или файл не читаются.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from model.book_schema import WORLDS, _WORLD, kernel_sha256  # noqa: E402

BOOK = ROOT / "data" / "assumptions" / "assumptions.yaml"
MAGNIT_BOOK = "data/assumptions/assumptions.yaml"


def project(world: dict) -> dict:
    """Мир Магнита в полях схемы X5: ключи кривой — строками (как в JSON-хэше X5)."""
    out = {}
    for field, kind in _WORLD.items():
        value = world[field]
        if isinstance(kind, dict):
            value = {k: value[k] for k in kind}
        elif isinstance(value, dict):
            value = {str(k): v for k, v in value.items()}
        out[field] = value
    return out


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          encoding="utf-8")
    if done.returncode != 0:
        raise RuntimeError(done.stderr.strip() or f"git {' '.join(args)}: код {done.returncode}")
    return done.stdout


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Сверка миров книги X5 с книгой Магнита на теге")
    ap.add_argument("--magnit", default=os.environ.get("X5_MAGNIT_REPO"),
                    help="репозиторий Магнита (по умолчанию ../magnit-850oa рядом с x5-850)")
    ap.add_argument("--book", default=str(BOOK), help="книга X5 (YAML)")
    a = ap.parse_args(argv)

    X = yaml.safe_load(Path(a.book).read_text(encoding="utf-8"))["worlds"]
    src = X["source"]
    tag = src["book"].split()[-1]                   # «magnit-850oa book-1.6» → book-1.6
    repo = Path(a.magnit) if a.magnit else ROOT.parent / "magnit-850oa"
    if not (repo / ".git").exists():
        print(f"пропуск: репозитория Магнита нет ({repo}) — миры сверяются только хэшем "
              f"собственного содержимого (схема книги)")
        return 0

    try:
        commit = git(repo, "rev-list", "-n", "1", tag).strip()
        blob = git(repo, "rev-parse", f"{tag}:{MAGNIT_BOOK}").strip()
        text = git(repo, "show", f"{tag}:{MAGNIT_BOOK}")
    except RuntimeError as exc:
        print(f"ошибка: книга Магнита на теге {tag} не читается: {exc}")
        return 2
    M = yaml.safe_load(text)["worlds"]
    P = {w: project(M[w]) for w in WORLDS}

    problems = []
    if commit != src["tag_commit"]:
        problems.append(f"тег {tag}: коммит {commit[:12]}…, в книге X5 — {src['tag_commit'][:12]}…")
    for w in WORLDS:
        for field in _WORLD:
            if P[w][field] != X[w][field]:
                problems.append(f"worlds.{w}.{field}: X5 ≠ Магнит {tag}")
    h_magnit, h_x5 = kernel_sha256(P), kernel_sha256(X)
    if h_magnit != src["kernel_sha256"]:
        problems.append(f"хэш миров Магнита {h_magnit[:12]}… ≠ worlds.source.kernel_sha256 "
                        f"{src['kernel_sha256'][:12]}…")

    print(f"Магнит {tag}: коммит {commit}, файл книги (blob) {blob}")
    print(f"хэш миров по правилу X5: Магнит {h_magnit}, X5 {h_x5}, в книге {src['kernel_sha256']}")
    if problems:
        print("РАСХОЖДЕНИЯ:")
        for p in problems:
            print(f"  {p}")
        return 1
    print(f"миры X5 = миры Магнита {tag} (поля {', '.join(_WORLD)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
