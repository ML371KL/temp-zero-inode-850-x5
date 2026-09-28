"""Общие настройки тестов X5.

* **Маркеры** (`pytest.ini`, `--strict-markers`): `network` — только при
  `X5_NETWORK=1`; `ci_only` — только в CI (переменная `CI`); `docs`, `slow` —
  для отбора (`-m "not slow"`).
* **Пропуск без маркера — ошибка.** Тест, пропущенный (`skip`/`skipif`/
  `importorskip`) без одного из маркеров выше, считается упавшим: молчаливый
  пропуск прятал бы непроверенное.
* **«Сегодня»** — `FAKE_TODAY=ГГГГ-ММ-ДД` (прогон «в будущем»); код читает его
  через `model.book.today()`, тесты — фикстурой `today`. Даты в тестах берутся
  от даты книги (`book_date`), а не от сегодняшнего дня.
"""

from __future__ import annotations

import datetime as dt
import getpass
import os
import tempfile
from pathlib import Path

import pytest

SKIP_MARKERS = ("network", "ci_only", "docs", "slow")


def _temp_root_usable() -> bool:
    """Каталог временных файлов pytest доступен (на ноутбуке владельца его ACL
    бывает испорчен чужими прогонами — тогда `tmp_path` падал бы PermissionError)."""
    base = Path(tempfile.gettempdir()) / f"pytest-of-{getpass.getuser()}"
    try:
        if base.exists():
            list(os.scandir(base))
        return True
    except OSError:
        return False


if "PYTEST_DEBUG_TEMPROOT" not in os.environ and not _temp_root_usable():
    _root = Path(__file__).resolve().parents[1] / "var" / "pytest-tmp"
    _root.mkdir(parents=True, exist_ok=True)
    os.environ["PYTEST_DEBUG_TEMPROOT"] = str(_root)


def pytest_report_header(config):
    from model.book import BOOK_YAML
    from model.facts import FACTS_DIR

    lines = []
    if not BOOK_YAML.exists():
        lines.append("книга: assumptions.yaml нет — тесты на черновике assumptions.draft.yaml")
    if not (FACTS_DIR / "accounting.json").exists():
        lines.append("факты: data/facts пуст — тесты на фикстуре tests/fixtures/facts")
    if os.environ.get("FAKE_TODAY"):
        lines.append(f"FAKE_TODAY={os.environ['FAKE_TODAY']}")
    return lines


def pytest_collection_modifyitems(config, items):
    network = os.environ.get("X5_NETWORK") == "1"
    ci = bool(os.environ.get("CI"))
    for item in items:
        if item.get_closest_marker("network") and not network:
            item.add_marker(pytest.mark.skip(reason="сеть: X5_NETWORK=1"))
        if item.get_closest_marker("ci_only") and not ci:
            item.add_marker(pytest.mark.skip(reason="только в CI"))


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.skipped and not hasattr(report, "wasxfail"):
        if not any(item.get_closest_marker(m) for m in SKIP_MARKERS):
            report.outcome = "failed"
            report.longrepr = (f"тест пропущен без маркера {'/'.join(SKIP_MARKERS)} — это "
                               f"ошибка: {report.longrepr}")


# ------------------------------------------------------------------ фикстуры


@pytest.fixture(scope="session")
def book():
    """Книга по умолчанию (assumptions.yaml или черновик) — только для чтения."""
    from model.book import load_book

    return load_book()


@pytest.fixture(scope="session")
def facts():
    """Факты по умолчанию (data/facts или фикстура)."""
    from model.facts import load_facts

    return load_facts()


@pytest.fixture(scope="session")
def grid(book, facts):
    """Сетка на входах книги."""
    from model.grid import evaluate

    return evaluate(book, facts)


@pytest.fixture(scope="session")
def book_date(book) -> dt.date:
    return dt.date.fromisoformat(book["meta"]["date"])


@pytest.fixture
def today() -> dt.date:
    from model.book import today as _today

    return _today()
