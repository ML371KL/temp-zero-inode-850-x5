"""В коде ядра нет чисел книги и фактов — только имена ключей (docs/MODEL.md, преамбула).

Собираются числа-литералы `model/*.py` (кроме индексов и срезов строк — `p[:4]`,
`p[5]`) и все числа книги и фактов. Пересечение, кроме структурных констант
ниже, — провал: число книги переписано в код руками.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import yaml

from model.book import BOOK_YAML, DRAFT_YAML, ROOT
from model.facts import FACTS_DIR, FIXTURE_DIR

# Структурные константы и почему они не допущения.
STRUCTURAL = {
    0: "ноль", 1: "единица", 2: "полугодий в году / удвоение", 0.5: "половина", 0.25: "четверть",
    0.75: "три четверти (Гордон на середины полугодий, §6)", 1000: "млрд → млн (цена на акцию)",
    3: "знаков после запятой в печати run_output",
    7: "месяц начала второго полугодия (календарь)",
    15: "срок схода бескупонной кривой к LT, лет (§0.2)",
    100: "процент (цена 1 % EV, §7.3)",
    0.0001: "защита терминала g = r − 0,0001 (§6)",
    1e-9: "допуск тождеств и суммы вероятностей книги",
    1e-12: "допуск инварианта вероятностей (§13.1)",
    10_000: "базисных пунктов в единице ставки (флаг book_update)",
    50_000: "размер памяти траекторий",
    500_000: "потолок выпуска, байт (§13.1)",
    # Часть 2 ядра (полоса, обратный DCF, «что даст отчёт», журнал): константы методики.
    0.1: "квантиль P10 полосы (§9); порог скачка V0 10 % (§13.4)",
    0.9: "квантиль P90 полосы (§9)",
    40: "не больше 40 шагов бисекции (§11, §12); подвыборка и корзины быстрой сборки",
    5: "стоп бисекции обратного DCF 5 ₽ (§11); допуск записки о скачке, %",
    0.01: "отрезок нейтральной маржи: демо-значения ± 0,01 (§12)",
    0.001: "0,1 п.п. маржи — единица наклона медианы (§12)",
    200: "прогонов полосы быстрой сборки (не в выпуск)",
    4: "полугодий зачёта журнала до допуска (правило 850oa)",
}


def _code_literals() -> dict:
    found: dict = {}
    for path in sorted((ROOT / "model").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        skip = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Subscript):
                skip.update(id(n) for n in ast.walk(node.slice))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, (int, float))
                    and not isinstance(node.value, bool) and id(node) not in skip):
                found.setdefault(abs(node.value), set()).add(f"{path.name}:{node.lineno}")
    return found


def _numbers(node, out: set) -> None:
    if isinstance(node, bool):
        return
    if isinstance(node, (int, float)):
        out.add(abs(node))
    elif isinstance(node, dict):
        for v in node.values():
            _numbers(v, out)
    elif isinstance(node, list):
        for v in node:
            _numbers(v, out)


def _data_numbers() -> set:
    out: set = set()
    for path in (BOOK_YAML, DRAFT_YAML):
        if path.exists():
            _numbers(yaml.safe_load(path.read_text(encoding="utf-8")), out)
    for root in (FACTS_DIR, FIXTURE_DIR):
        for path in sorted(Path(root).glob("*.json")):
            _numbers(json.loads(path.read_text(encoding="utf-8")), out)
    return out


def test_no_book_or_fact_numbers_in_model_code():
    data = _data_numbers()
    assert data, "не нашлось ни одного числа книги и фактов — тест ничего не проверяет"
    code = _code_literals()
    leaks = {value: sorted(where) for value, where in code.items()
             if value in data and value not in STRUCTURAL}
    assert not leaks, f"числа книги/фактов литералами в model/: {leaks}"


def test_structural_constants_are_used():
    """Список исключений не разрастается впрок: каждое исключение встречается в коде."""
    code = _code_literals()
    unused = [k for k in STRUCTURAL if k not in code]
    assert not unused, f"исключения без употребления в model/: {unused}"
