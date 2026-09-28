# Книга допущений X5

Единственный экземпляр книги допущений: ядро читает её отсюда (`model/book.py`, `BOOK_DIR`), версии помечены тегами git `book-<версия>` (`git show book-1.0:data/assumptions/assumptions.yaml`). Книгу меняет только её новая версия; решает о ней владелец или ведущий. Семантика ключей — `docs/MODEL.md`.

## Что здесь

| Файл | Что |
|---|---|
| `assumptions.yaml` | машинная книга — канон, его считает ядро; закрытая схема `model/book_schema.py`: незнакомый ключ, пропуск или не тот тип — отказ `BookError` |
| `ASSUMPTIONS-BOOK.md` | текст книги: суждения A-xx с ключом, значением, диапазоном, классом, обоснованием, ценой ошибки и «чем может ошибаться»; собран из `sections/` |
| `evidence/<область>/` | доказательные листы: скрипты, входы, выводы, `README.md` с порядком перезапуска; области — `network-revenue`, `margin`, `capex-wc-tax`, `financing-valuation` |
| `results.json`, `run_output.txt` | таблицы книги — выпуск ядра на входах книги (`meta.valuation_date`, `meta.market_price`); руками не правятся |
| `sections/*.md`, `fragments/*.yaml` | тексты и машинные фрагменты книги 1.0 по областям — материал её сборки; фрагменты читают доказательные листы (`check_fragment.py`) |
| `assumptions.draft.yaml` | черновик схемы до книги 1.0: запасной путь ядра и контрольной модели, когда `assumptions.yaml` нет, и вход мока витрины (`ops/tools/mock_payload.py`); не канон |

Не книга, а эксплуатация (правятся в любой день, без новой версии): `gate_explanations.yaml` — объяснения сработавших гейтов (текст, коридор массы, срок включительно; формат — шапка файла), `release_notes.yaml` — записки о задуманных скачках заголовка (шапка файла), и этот `README.md`.

## Новая версия книги

Из корня репозитория, на ветке от `origin/main`:

1. **Правка.** В `assumptions.yaml` — `meta.version` и `meta.date` (дата книги: раньше неё дата оценки выпуска не бывает), входы таблиц `meta.valuation_date` и `meta.market_price`, сами значения. В `ASSUMPTIONS-BOOK.md` — текст изменённых суждений; расчёты, на которые он ссылается, — в `evidence/`.
2. **Новый ключ** (новое правило кода) регистрируется в закрытой схеме `model/book_schema.py` тем же изменением, что и код, который его читает; методика — сначала в `docs/MODEL.md`.
3. `python -B -m model.book_results` — пишет `results.json` и `run_output.txt`; массы и срабатывания гейтов — там же, раздел «Гейты». Сработавшему гейту нужна запись в `gate_explanations.yaml` с коридором массы и сроком.
4. `python -B ops/tools/render_numbers.py` — переписывает числа результатов книги во всех `*.md` репозитория (без этого падает `tests/test_render_numbers.py`).
5. `python -B -m tests.independent_model --report` — `docs/CONTROL-MODEL.md` на новой книге (без этого падает `tests/test_control_model.py::test_control_report_is_fresh`). Ядро не подгоняется: несходство контрольной модели или регрессии — находка.
6. Тесты целиком, как CI: `$env:CI=1; python -m pytest -q -m "not network"` (Git Bash: `CI=1 python -m pytest -q -m "not network"`) — регрессия книги с полосой, контрольная модель на сценариях, прогон «в будущем» на +30/+90/+180 дней от даты книги.
7. Запись в `docs/CHANGELOG.md`: что изменилось в книге и сдвиг печатаемых чисел (медиана, полосы, точка).
8. Коммит в `main` — это выкладка: конвейер соберёт и опубликует выпуск на новой книге (защита заголовка новую версию книги пропускает). Затем аннотированный тег на коммите версии: `git tag -a book-<версия> <коммит>` и `git push origin book-<версия>`.

## Миры ставок

Миры N, H, M — общие с книгой Магнита 850oa: у двух панелей владельца одна макро-основа. Происхождение — `worlds.source`: книга Магнита (`book`), дата кривой (`curve_date`), коммит её тега (`tag_commit`) и хэш содержимого миров X5 (`kernel_sha256`, сверяет схема). Общие с Магнитом и суждения о мирах: веса слоёв (`joint.world_prob`, `joint.market_implied_prob`, `joint.neutral_world`), λ (`joint.lambda`), оси полосы «Веса миров» и «Инфляция мира M» (сверка — `evidence/financing-valuation/check_fragment.py`). Своё у X5 — всё, что привязано к миру на стороне компании: рост сети и кредитное состояние (`joint.world_links`), однородность чека, перенос продовольственной инфляции в чек.

**Пересборка — вместе с Магнитом**, после заседания ЦБ со среднесрочным прогнозом (даты — `data/calendar.json`, `cbr`) или по флагу `book_update`: Магнит пересобирает миры своим рецептом (его справочник, §10.4) и выпускает версию книги с тегом, затем — новая версия книги X5:

1. Из `data/assumptions/assumptions.yaml` книги Магнита на её теге (`git -C ../magnit-850oa show book-<версия>:data/assumptions/assumptions.yaml`, только чтение) перенести в `worlds.N`, `worlds.H`, `worlds.M` поля `name`, `key_rate`, `cpi`, `food_cpi`, `zero_curve` (узлы — строками `'1'`, `'3'`, `'5'`, `'10'`, `'LT'`) и `lt`; поля `wage_growth`, `ofz_10y_path`, `tariff_growth` X5 не читает. Траектории миров — ровно полугодия от `meta.first_period` до `meta.last_period` (схема проверяет).
2. Сверить общие веса, λ и оси с книгой Магнита и перенести изменения.
3. `worlds.source`: `book` — `magnit-850oa book-<версия>`, `curve_date` — дата кривой Магнита, `tag_commit` — `git -C ../magnit-850oa rev-list -n 1 book-<версия>`, `kernel_sha256` — `python -B -c "import yaml; from model.book_schema import kernel_sha256; print(kernel_sha256(yaml.safe_load(open('data/assumptions/assumptions.yaml', encoding='utf-8'))['worlds']))"`. `meta.curve_as_of` — та же дата кривой (схема сверяет).
4. Дальше — шаги 3–8 «Новой версии книги».
