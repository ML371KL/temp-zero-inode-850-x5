# X5 — модель справедливой стоимости и дашборд

Модель справедливой стоимости акции ПАО «Корпоративный центр ИКС 5» (MOEX: X5) отвечает на три вопроса. **Сколько стоит акция:** медиана распределения по суждениям книги допущений с полосами 80 % и 50 %; стоимость бизнеса считается первой, цена акции — последним шагом. **Что заложено в рыночную цену:** обратный DCF по суждениям книги и EV модели против рыночного V\*. **Что даст ближайший отчёт:** ожидание модели против прогноза компании и наивных эталонов, неизменяемый журнал прогнозов (к цене не подключён).

Витрина — https://tzi-850-x5.pages.dev, выпуск данных — `/api/model`. Репозиторий публичный, ветка по умолчанию — `main`; выпуски публикует конвейер GitHub Actions в орфан-ветку `data`. **Начинать с [`docs/MANUAL.md`](docs/MANUAL.md)** — справочника владельца: суть, карта, доступы, конвейер, процедуры.

## Что печатает модель

На входах книги (версия и дата оценки — `data/assumptions/results.json`, блоки `book` и `inputs`; рынок <!--=inputs.market_price r1-->1 808,5<!--/--> ₽):

- крупно — **медиана по суждениям книги ≈<!--=band.printed.median r0-->3 150<!--/--> ₽**, полоса 80 % — <!--=band.printed.p10 r0-->2 400<!--/-->–<!--=band.printed.p90 r0-->3 900<!--/--> ₽, 50 % — <!--=band.printed.p25 r0-->2 750<!--/-->–<!--=band.printed.p75 r0-->3 600<!--/--> ₽;
- рядом — **точка при центральных значениях всех суждений ≈<!--=point.printed.central r0-->3 250<!--/--> ₽** и вероятность, что справедливая цена ниже рыночной, — <!--=band.stats.p_below p2-->0,75<!--/--> %;
- стоимость бизнеса — **EV медианы против рыночного V\*: <!--=band.center_ev.gap_median sp1-->+42,6<!--/--> %**; что должно измениться, чтобы рынок оказался прав, — обратный DCF (справочник, раздел 1).

Живой выпуск считает на сегодняшней цене и печатает свои числа — экран «Оценка» и `/api/model`. Числа выше стоят в метках и перерисовываются из `data/assumptions/results.json` (`python -B ops/tools/render_numbers.py`).

## Структура

Корень:

| Файл | Что |
|---|---|
| `README.md` | этот обзор |
| `requirements.txt` | зависимости Python — точные версии всего дерева (PyYAML, pytest и его зависимости), только ASCII |
| `pytest.ini` | корень тестов, `pythonpath`, метки `network`, `ci_only`, `docs`, `slow` (`--strict-markers`) |
| `.gitattributes` | текст — LF (`* text=auto eol=lf`), PNG, PDF, XLSX — двоичные |
| `.gitignore` | `.venv/`, `var/`, `.wrangler/`, кэши Python и pytest |

`.github/workflows/`:

| Файл | Что |
|---|---|
| `pipeline.yml` | конвейер: по будням 16:50 UTC, при push кода и книги в `main`, вручную (только с `main`) — сбор живых входов → тесты такта → сборка → контракт → публикация в ветку `data` → сверка через `/api/model` → артефакт → keepalive расписания (`ops/README.md`) |
| `ci.yml` | push и PR в `main` (кроме коммитов из одних `*.md`): тесты `-m "not network"`, сборка на входах книги `--book --fast`, проверка контракта |
| `docs.yml` | push и PR в `main`, где менялся `*.md`: тесты документов `-m "docs and not network"` |

`model/` — ядро (методика — `docs/MODEL.md`, API — `model/README.md`):

| Файл | Что |
|---|---|
| `book.py` | книга: загрузка и схема, линейка полугодий, траектории §0.1, кривая §0.2, подмены `override`, наблюдения `add_observation`, открытое полугодие `open_period`, `today()` — день по Москве, подмена `FAKE_TODAY` |
| `book_schema.py` | закрытая схема книги: незнакомый ключ, пропуск, неверный тип — `BookError`; хэш миров `kernel_sha256` (целостность миров книги) |
| `facts.py` | факты `data/facts/*.json`: узлы `{v, src\|calc}`, `null` → `None`, проверенные факты прохода клетки `core_facts` |
| `core.py` | проход клетки: сеть, выручка, маржа, capex, оборотный капитал, налог, долг и дивиденды, вычеты финансирования, терминал, требования на дату оценки |
| `grid.py` | сетка 36 клеток, A-P2u, слои, точка, цена с казначейским пакетом, V\*, цена 1 % EV, ожидаемый путь |
| `checks.py` | инварианты, гейты с массой и объяснениями, флаги, округление печати |
| `uncertainty.py` | полоса A-V9, вклады осей, суждения по цене ошибки, обратный DCF, «что даст отчёт», ожидание модели |
| `attribution.py` | «что изменилось с прошлого выпуска» и опорные числа защиты заголовка |
| `journal.py` | журнал прогнозов: запись, факт (узел с источником), неизменяемость, эталоны, правило допуска |
| `payload.py` | выпуск `x5-v1`: `build_payload`, `validate`, хэш содержания |
| `book_results.py` | таблицы книги `results.json` и `run_output.txt` |
| `sample_release.py` | образец выпуска `var/release/sample.json` на входах книги |

`indicators/` — живые входы (`ops/README.md`, «Живые входы»):

| Файл | Что |
|---|---|
| `http.py` | HTTP-клиент: повторы, пауза к хосту, свой User-Agent, сырой ответ в `var/raw/` до разбора |
| `iss.py` | MOEX ISS: цена X5 и аналогов, история закрытий, бескупонная кривая, облигации ООО «ИКС 5 ФИНАНС» |
| `cbr.py` | ключевая ставка ЦБ: SOAP `KeyRate`, запасной путь — страница `hd_base/KeyRate` |
| `live.py` | `python -m indicators.live`: живые входы `x5-live-v1`, правила годности цены, запасная цена |
| `runlog.py` | итоговые строки шагов в журнал и в сводку прогона GitHub |

`ops/` — сборка, публикация, эксплуатация (`ops/README.md`):

| Файл | Что |
|---|---|
| `build_release.py` | сборка выпуска: `--live`, `--live-file`, `--book`, `--check`, `--fast`; итог — «готово: …» или «ПРОВАЛ на шаге …» |
| `publish.py` | публикация в ветку `data` (`--expect-commit` — ветка не изменилась с начала прогона), проверка неизменяемости журнала, `--verify`, `--fetch-state`, `--rollback --note`, `--reopen <id> --note` |
| `tools/render_numbers.py` | числа результатов книги в документах по меткам; `--check` |
| `tools/build_facts.py` | сборщик фактов `data/facts/*.json` и `data/calendar.json` из первички (нужен openpyxl) |
| `tools/check_worlds.py` | сверка миров книги с книгой Магнита на теге (соседний репозиторий, только чтение) |
| `tools/devserver.py` | локальный предпросмотр витрины с выпуском из файла |
| `tools/shots.mjs` | снимки витрины headless-Chrome по CDP с замерами вёрстки |
| `tools/mock_payload.py` | мок выпуска `tests/fixtures/sample_payload.json` для проверки вёрстки |

`web/` и `functions/` — витрина и функции Cloudflare Pages (`docs/DASHBOARD.md`):

| Файл | Что |
|---|---|
| `web/index.html` | каркас, шесть вкладок, инлайн-скрипт темы (разрешён в CSP по хэшу) |
| `web/app.js` | шесть экранов, ползунок λ, графики на своём SVG |
| `web/styles.css` | токены, сетка, компоненты, светлая и тёмная темы |
| `web/_headers`, `web/_routes.json` | CSP и заголовки статики; функции — только на `/api/*` |
| `web/404.html`, `web/favicon.svg` | страница 404 и знак |
| `functions/api/model.js` | дверь данных `/api/model`: `latest.json` ветки `data` с raw.githubusercontent.com, запасные копии |
| `functions/_middleware.js` | граница `/api/` (только `model`) и заголовки безопасности ответов функций |

`data/`:

| Путь | Что |
|---|---|
| `assumptions/` | книга допущений — единственный экземпляр; канон — `assumptions.yaml` вместе с текстом `ASSUMPTIONS-BOOK.md`, журнал версии — `V1.1-CHANGES.md`; состав и порядок новой версии — `data/assumptions/README.md` |
| `facts/` | отчётные факты на якорь книги, у каждого числа источник: `accounting`, `network`, `balance`, `bridge`, `shares`, `dividends` (читает ядро), `debt_register`, `history`, `peers`, `brokers`, `guidance` (читает выпуск), `actuals` (факты журнала); поля — `data/facts/SCHEMA.md`, первичка и сверка — `docs/FACTS.md`; собирает `ops/tools/build_facts.py` |
| `calendar.json` | события на 12 месяцев: отчёты X5, дивиденды, заседания ЦБ; `confirmed` и прецедент в `note` |

`tests/`:

| Файл | Что |
|---|---|
| `conftest.py` | метки, пропуск без метки — ошибка, `FAKE_TODAY`, фикстуры книги, фактов и сетки |
| `test_book_paths.py`, `test_book_schema.py`, `test_core_*.py`, `test_checks.py`, `test_uncertainty.py` | траектории и схема книги, проход клетки вручную по формулам, тождества, монотонности, режимы, время, гейты, полоса и обратный DCF |
| `test_book_results.py` | регрессия таблиц книги (полоса — `ci_only`) |
| `independent_model.py`, `test_control_model.py` | контрольная модель по тексту книги и `docs/MODEL.md`; сверка с ядром, сценарии (и в такте), свежесть `docs/CONTROL-MODEL.md` |
| `test_no_literals.py` | в коде ядра нет чисел книги и фактов |
| `test_facts.py`, `test_core_facts.py` | факты и календарь: источники, даты, согласованность; пересборка фактов байт в байт (`ci_only`) |
| `test_payload.py`, `test_journal.py`, `test_time_travel.py` | выпуск и контракт, журнал, прогон «в будущем» (`ci_only`) |
| `test_live_*.py` | сборщики на сохранённых ответах ISS и ЦБ; `test_live_network.py` — живые источники (`network`) |
| `test_ops_*.py` | сборка, публикация на локальном «удалённом» репозитории (`ci_only`), workflow, `ops/README.md` |
| `test_web_*.py`, `web_functions_check.mjs` | статика и CSP, контракт витрины и ползунок λ, функции Pages под Node |
| `test_render_numbers.py` | числа результатов книги в документах = `results.json` |
| `fixtures/` | ответы ISS и ЦБ 28.09.2026 с манифестом `live_sources.json`, факты для тестов ядра, мок выпуска `sample_payload.json` |

`docs/` — методика, контракт, факты, справочник, витрина, отчёт и журнал, история и решения (раздел «Документы» ниже).

`var/` — рабочий каталог прогонов (сырые ответы, живые входы, выпуски, временные каталоги тестов); в git не идёт.

## Запуск

Python 3.11+ (CI — 3.12), для тестов витрины — Node 18+, для снимков — Node 22+ и Chrome.

```
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Дальше `python` — это `.venv\Scripts\python.exe`.

```
python -m pytest -q -m "not network and not ci_only and not docs"   # тесты такта, как в конвейере
python -m pytest -q -m docs                                          # сверка документов с кодом и results.json
$env:CI=1; python -m pytest -q -m "not network"                      # как CI (Git Bash: CI=1 python -m pytest …)
$env:X5_NETWORK=1; python -m pytest -q -m network                    # живые ISS и ЦБ
```

Если pytest падает на системном временном каталоге (`PermissionError`) — добавить `--basetemp var/pytest-tmp`. Прогон «в будущем» — `FAKE_TODAY=ГГГГ-ММ-ДД` в окружении.

Книга и документы:

```
python -B -m model.book_results                      # results.json и run_output.txt на входах книги (--no-band — без полосы)
python -B ops/tools/render_numbers.py                # числа результатов книги в *.md (--check — только проверить)
python -B -m tests.independent_model --report        # docs/CONTROL-MODEL.md
python -B ops/tools/build_facts.py                   # факты из первички (../x5-850-handoff/reference/primary)
python ops/tools/check_worlds.py                     # миры книги = книга Магнита на теге (../magnit-850oa)
```

Выпуск:

```
python -B ops/build_release.py --live                # живые входы + выпуск → var/release/latest.json
python -B ops/build_release.py --book --fast         # на входах книги, без полной полосы (как CI)
python -B ops/build_release.py --check var/release/latest.json
python -B -m model.sample_release                    # образец var/release/sample.json с полной полосой
python -m indicators.live --out var/live/live.json   # только живые входы
```

Витрина локально и снимки (`docs/DASHBOARD.md`, «Снимки»):

```
python ops/tools/devserver.py 8872 [выпуск.json]     # http://127.0.0.1:8872/; без файла — var/release/sample.json, иначе мок
TAG=<метка> BASE_URL=http://127.0.0.1:8872 OUT_ROOT=../x5-850-handoff/shots node ops/tools/shots.mjs
```

Эксплуатация (ручной запуск конвейера, журнал прогона, откат, факт отчёта и его исправление, выкладка витрины) — `ops/README.md`.

## Документы

- `docs/MANUAL.md` — справочник владельца: с него начинать.
- `docs/MODEL.md` — методика; `docs/PAYLOAD.md` — контракт выпуска `x5-v1`; `model/README.md` — API ядра.
- `data/assumptions/ASSUMPTIONS-BOOK.md` — книга допущений (канон вместе с `assumptions.yaml`); `data/assumptions/V1.1-CHANGES.md` — что изменилось в книге 1.1 и почему; `data/assumptions/README.md` — состав каталога книги и порядок новой версии.
- `docs/FACTS.md` — факты и первичка; `data/facts/SCHEMA.md` — поля фактов.
- `docs/DASHBOARD.md` — витрина; `docs/INDICATORS.md` — ближайший отчёт и журнал прогнозов.
- `docs/CONTROL-MODEL.md` — сверка контрольной модели с ядром (генерируется).
- `ops/README.md` — конвейер, публикация, откат, выкладка.
- `docs/CHANGELOG.md` — история изменений и выкладок; `docs/DECISIONS.md` — решения с обоснованием.
