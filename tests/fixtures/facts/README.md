# Фикстура фактов для тестов ядра

Перенос из `x5-850-handoff/research/facts/*.json` (агент X1) в форму `data/facts/SCHEMA.md`:
у каждого значения `{"v", "src"|"calc"}`; млн ₽ → млрд ₽ делением на 1000 (в `calc`);
валовые открытия площади — оценка по правилу SCHEMA.md (чистый прирост + закрытия по
`network.close_rate` книги). Не выверена агентом фактов: рабочие факты — `data/facts/`.

Ядро читает фикстуру, только когда в `data/facts/` нет `accounting.json`
(`model.facts.default_facts_dir`, предупреждение `FactsFallbackWarning`); тесты
`tests/test_core_facts.py` берут её как образец формы (копируют во временный каталог и портят).
