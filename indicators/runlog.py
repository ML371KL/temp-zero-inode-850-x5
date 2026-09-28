"""Строки прогона: в журнал шага и в сводку GitHub Actions (`GITHUB_STEP_SUMMARY`).

Итог шага — одна строка `готово: …` или `ПРОВАЛ на шаге <шаг>: <причина>`
(`report`); важные для владельца строки по ходу шага — запасная цена, отказ
источника — тоже попадают в сводку (`summary`). Вне Actions сводки нет.
"""

from __future__ import annotations

import os
import sys


def summary(line: str) -> None:
    """Строка в сводку прогона (если она есть)."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"- {line}\n")


def report(line: str) -> None:
    """Итоговая строка шага — в журнал прогона и в сводку."""
    sys.stdout.flush()  # строки шага — раньше итоговой и при буферизации
    print(line, file=sys.stderr if line.startswith("ПРОВАЛ") else sys.stdout, flush=True)
    summary(line)
