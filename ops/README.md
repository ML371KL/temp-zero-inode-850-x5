# Эксплуатация X5: конвейер, публикация, откат

Конвейер живёт в GitHub Actions публичного репозитория
`ML371KL/temp-zero-inode-850-x5` (минуты бесплатны, секретов нет — только
`GITHUB_TOKEN` задания). Витрина https://tzi-850-x5.pages.dev читает выпуск через
Pages-функцию `/api/model`, а та — файл `latest.json` ветки `data` с
raw.githubusercontent.com.

## Расписание

| Когда | Что | Файл |
|---|---|---|
| по будням 16:50 UTC (19:50 МСК, после основной сессии MOEX) | полный конвейер | `.github/workflows/pipeline.yml` |
| push в `main`, если менялись `model/`, `data/`, `indicators/`, `ops/`, `requirements.txt` (кроме `*.md`) | тот же конвейер на новом коде | там же |
| вручную | тот же конвейер | `gh workflow run pipeline.yml` |
| push и PR в `main` (кроме коммитов из одних `*.md`) | тесты без сети + сборка на входах книги + контракт | `.github/workflows/ci.yml` |

Одновременно идёт один конвейер (`concurrency: pipeline`), идущий не
отменяется. GitHub может запустить расписание с опозданием (особенно в начале
часа) — это нормально.

## Шаги конвейера

| Шаг | Команда | Провал значит |
|---|---|---|
| состояние из ветки `data` | `python ops/publish.py --fetch-state var/state` | GitHub не ответил |
| сбор живых входов | `python -m indicators.live --previous var/state/latest.json --out var/live/live.json` | нет годной цены X5 и нет запасной |
| тесты такта | `python -m pytest -q -m "not network and not ci_only and not docs"` | сломан код или данные |
| сборка выпуска | `python ops/build_release.py --live-file var/live/live.json --previous var/state/latest.json --journal var/state/journal.json --out var/release/latest.json` | ядро упало, инвариант, необъяснённый гейт, скачок заголовка, контракт |
| проверка контракта | `python ops/build_release.py --check var/release/latest.json` | записанный файл не проходит `model.payload.validate` |
| публикация | `python ops/publish.py --release var/release/latest.json` | журнал переписан задним числом; гонка записи; GitHub не принял push |
| сверка через боевую дверь | `python ops/publish.py --verify https://tzi-850-x5.pages.dev/api/model --release var/release/latest.json` | за 20 попыток × 30 с дверь не отдала новый `payload_sha256` |
| сырые ответы и выпуск | артефакт `run-<id>-<попытка>` (14 дней) | — (идёт всегда) |
| keepalive расписания | `gh api -X PUT repos/…/actions/workflows/pipeline.yml/enable` | нет прав `actions: write` |

**Главное правило:** любой упавший шаг до публикации — ветка `data` не
трогается, на витрине остаётся прежний выпуск. Красный прогон — письмо GitHub
(получает автор расписания, то есть тот, кто последним менял `cron` или
включал workflow; в Settings → Notifications → Actions удобно оставить «только
неудачные»).

Сверка ждёт до ≈10 минут: raw.githubusercontent.com кэширует ответ до 5 минут,
Pages-функция — ещё 60 с. Кэш не обходится параметром — проверялось бы не то,
что видит браузер.

## Что где лежит

**Ветка `main`** — код, книга, факты, тесты, витрина (`web/`, `functions/`).

**Ветка `data`** — орфан, ОДИН коммит без истории (каждая публикация
переписывает её целиком, репозиторий не растёт):

| Файл | Что | Кто пишет |
|---|---|---|
| `latest.json` | текущий выпуск (контракт `x5-v1`, `docs/PAYLOAD.md`) | публикация |
| `previous.json` | прежний `latest.json` | публикация |
| `journal.json` | блок `journal` текущего выпуска — журнал прогнозов | публикация |
| `history.json` | по строке на каждый выпуск и откат: время, sha, дата оценки, книга, цена, медиана | публикация, откат |

Журнал неизменяем: публикация сверяет новый `journal.json` с лежащим в ветке —
каждая прошлая запись должна остаться с теми же полями; меняться могут только
`actual` и `errors` и только один раз (пока `actual` был `null`). Нарушение —
провал шага «проверка журнала», ветка не тронута. Выпуск с тем же
`payload_sha256`, что уже лежит в `latest.json`, повторно не публикуется
(иначе `previous.json` затёрся бы копией текущего).

**Рабочий каталог прогона** (`var/`, в git не идёт):

* `var/raw/<источник>/<дата UTC>/ЧЧММСС_<имя>` + `.meta.json` (адрес, время,
  sha256) — сырые ответы ISS и ЦБ, сохранённые ДО разбора;
* `var/live/live.json` — живые входы (`schema: x5-live-v1`);
* `var/release/latest.json` — собранный выпуск;
* `var/state/` — копия ветки `data` на начало прогона.

Всё это уезжает в артефакт прогона.

## Живые входы

`indicators/`: `http.py` (повторы 3/10/30 с на сетевые сбои, 408/425/429 и 5xx;
пауза 1 с между запросами к одному хосту; свой ASCII User-Agent), `iss.py`,
`cbr.py`, `live.py`.

| Вход | Источник | В оценку? |
|---|---|---|
| цена X5 и время сделки | ISS TQBR: `LAST` + `TIME`, дата — `SYSTIME`; вне торгов — `PREVPRICE`/`PREVLEGALCLOSEPRICE` с `PREVDATE`; котировки недоступны — последнее закрытие истории | да (и дата оценки) |
| закрытия за 12 мес. | ISS history, постранично (`LEGALCLOSEPRICE`, нет — `CLOSE`) | нет (график, подтверждение скачка) |
| MGNT, LENT, FIXR, OKEY | ISS TQBR, тот же запрос | нет (аналоги) |
| кривая ОФЗ, узлы 1/3/5/10 лет | ISS `zcyc`, последняя дата, доли | нет (плитки, флаг `book_update`) |
| ключевая ставка | ЦБ SOAP `KeyRate`; запасной путь — страница `hd_base/KeyRate` (к ЦБ — с заголовком `Python-urllib`, см. «Грабли») | нет (плитка) |
| облигации ООО «ИКС 5 ФИНАНС» | ISS: бумаги эмитента 1259 → доска TQCB (цена % номинала, доходность, дюрация, объём, оборот) | нет |

Правила годности цены (`docs/MODEL.md` §13.4): возраст ≤ 7 дней; отклонение
от последней принятой ≤ 30 %; отличие в 10 раз и больше — чужие единицы, не
принимается никогда. Негодная цена заменяется последней принятой из прошлого
выпуска (`price.status = "fallback"`, причина в `price.reason`). После простоя
(эталон старше недели) скачок больше 30 % принимается, если его подтверждает
соседний торговый день истории в пределах 5 %, — с пометкой в `price.reason`.
Нет годной цены и нет прошлого выпуска — провал шага, выпуск не собирается.

Эталон едет в выпуске: `live.price.last_accepted` (ядро переносит блок
`price` живых входов в выпуск). Отказ второстепенного источника — строка в
`errors` живых входов, выпуск выходит.

## Ручные действия

### Запустить конвейер вручную

```
gh workflow run pipeline.yml --repo ML371KL/temp-zero-inode-850-x5
gh run watch --repo ML371KL/temp-zero-inode-850-x5
```

### Прочитать журнал прогона

```
gh run list --workflow pipeline.yml --limit 5 --repo ML371KL/temp-zero-inode-850-x5
gh run view <id> --repo ML371KL/temp-zero-inode-850-x5            # шаги и сводка
gh run view <id> --log-failed --repo ML371KL/temp-zero-inode-850-x5
gh run download <id> --repo ML371KL/temp-zero-inode-850-x5         # артефакт: var/raw, var/live, var/release
```

Каждый шаг кончается одной строкой: `готово: …` или `ПРОВАЛ на шаге <шаг>:
<причина>`; эти же строки собраны в сводке прогона (Summary). Шаг сбора
печатает принятую цену (или «ЗАПАСНАЯ … — причина») и строки `ОТКАЗ ИСТОЧНИКА
<имя>: …`; сборка — строки `КОНТРАКТ: …`; публикация — `ЖУРНАЛ: …`; сверка —
попытки с причиной ожидания.

### Откатить выпуск

Откат — вернуть `previous.json` на место `latest.json` в ветке `data`. Из
рабочей копии на ноутбуке (учётные данные git владельца):

```
python ops/publish.py --rollback
```

Команда пишет строку `rollback` в `history.json`; журнал не трогается (он —
запись того, что было опубликовано). Без Python — то же руками:

```
git clone --depth 1 --branch data https://github.com/ML371KL/temp-zero-inode-850-x5 x5-data
cd x5-data
cp previous.json latest.json
git add latest.json
git commit -m "откат: latest.json := previous.json"
git push origin HEAD:data
```

Проверка (через 1–6 минут, пока истекут кэши):

```
curl -s -A "Mozilla/5.0" https://tzi-850-x5.pages.dev/api/model | python -c "import json,sys; m=json.load(sys.stdin)['meta']; print(m['payload_sha256'][:12], m['valuation_date'])"
```

Если причина не устранена, следующий прогон опубликует новый выпуск поверх
отката — до исправления остановить расписание: `gh workflow disable
pipeline.yml --repo ML371KL/temp-zero-inode-850-x5` (обратно — `gh workflow
enable pipeline.yml …`; письма об отказах пойдут тому, кто включил).

### Внести факт отчёта

После публикации отчёта X5 факт по цели журнала вносится в
`data/facts/actuals.json` (формат — `data/facts/SCHEMA.md`, раздел
`actuals.json`), одна запись на цель и период, с источником:

```
{"target": "x5.adj_margin", "period": "<полугодие, 2026H2>", "value": <доля единицы>,
 "reported_on": "<ГГГГ-ММ-ДД>", "src": "<файл первички › лист!ячейка>"}
```

Коммит в `main` запускает конвейер
(`data/**`): выпуск закроет запись журнала (`actual` из `null` в значение).
Факт вносится ОДИН раз — повторная правка `actual` уже закрытой записи
публикация отвергнет. Ошибочный факт исправляется только вручную в
`journal.json` ветки `data` отдельным коммитом с объяснением и одновременно в
`actuals.json`.

### Выложить витрину

Фронт (`web/`) и Pages-функция (`functions/`) выкладываются с ноутбука, из
корня рабочей копии (OAuth wrangler владельца):

```
npx wrangler@4.135.0 pages deploy web --project-name tzi-850-x5 --branch main
```

Версия wrangler — точная, не `@4`. `--branch main` — production-ветка
проекта; без неё деплой уезжает в preview. Данные этим деплоем не
выкладываются — их приносит конвейер через ветку `data`.

### Проверить сборщики локально

```
python -m indicators.live --out var/live/live.json
python ops/build_release.py --live-file var/live/live.json --fast
python -m pytest -q -m "not network and not ci_only and not docs"
X5_NETWORK=1 python -m pytest -q -m network        # живые ISS и ЦБ
```

На ноутбуке с Python из Microsoft Store системный temp для pytest закрыт —
добавлять `--basetemp var/pytest-tmp`.

## Что держит расписание живым

GitHub отключает расписание публичного репозитория после 60 дней без
активности. Последний шаг конвейера (идёт всегда) вызывает
`PUT repos/{repo}/actions/workflows/pipeline.yml/enable` токеном задания
(`permissions: actions: write`). Отключённое расписание видно по возрасту
выпуска на витрине (`Last-Modified` двери = `meta.generated_at`).

## Грабли

* cbr.ru закрыт DDoS-Guard: незнакомый User-Agent (в том числе свой заголовок
  сборщика) и заголовок, не совпадающий с клиентом (`Mozilla/5.0` из Python),
  получают 403 с JS-проверкой; родной заголовок `Python-urllib/3.x` — ответ
  (28.09.2026). Поэтому запросы к ЦБ идут с ним, остальные — со своим. Если ЦБ
  всё же отказал, ключевая ставка в прогоне `null`, в `errors` строка
  `key_rate`, выпуск выходит (она — наблюдение).
* Бесплатный ISS отдаёт рынок с задержкой 15 минут; вечерняя сессия идёт до
  23:50 МСК — в 19:50 МСК `LAST` — сделка вечерней сессии.
* Push токеном `GITHUB_TOKEN` (в ветку `data`) других workflow не запускает —
  так и задумано.
* Публикация идёт `--force-with-lease` на прочитанный коммит ветки: ручной
  откат, случившийся во время прогона, конвейер не затрёт — упадёт на
  публикации, следующий прогон начнёт с отката.
* rosstat.gov.ru с раннеров GitHub не отвечает (проба 28.09.2026) — сборщиков
  Росстата в конвейере нет.
