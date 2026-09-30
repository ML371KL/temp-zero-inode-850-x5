# Ядро модели X5: API для части 2 и конвейера

Методика — `docs/MODEL.md` (авторитетна); здесь — только как её звать из кода.
Единицы: деньги — млрд ₽, площадь — тыс. м², доли — доли единицы, цена — ₽/акция.
В коде ядра нет чисел книги и фактов (тест `tests/test_no_literals.py`).

## Входы

### Книга — `model/book.py`

| Функция | Что делает |
|---|---|
| `default_book_path() -> Path` | `data/assumptions/assumptions.yaml`, а пока его нет — `assumptions.draft.yaml` (предупреждение `BookFallbackWarning`) |
| `load_book(path=None) -> dict` | читает YAML и проверяет закрытой схемой (`model/book_schema.py::validate_book`); отказ — `BookError` |
| `override(A, paths, kind, value) -> dict` | копия книги с подменой по путям: `kind="value"` — число заменяется; `"shift"` — к числу или ко всем значениям траектории (кроме `LT_from`) прибавляется `value`; `"dict"` — веса заменяются словарём `value`, нормированным к 1. Пути — через точку: `"margin.targets.stress.LT"`, `"capex.maintenance.base"`, `"worlds.M.lt.inflation"`. Копирует только блоки по пути (дёшево), исходную книгу не меняет, схему на копии не перепроверяет (хэш миров после подмены инфляции законно другой). Нет пути — `BookError` |
| `blend_weights(book_w, end_w, t) -> dict` | веса книги, сдвинутые на долю `t ∈ [0, 1]` к `end_w` (ось `dict`, §9); нормировку делает `override(..., "dict", …)` |
| `add_observation(A, period, value, se=0.0) -> dict` | копия книги с ещё одним наблюдением маржи A-P2u поверх внесённых («что даст отчёт», §12); повтор полугодия — `BookError` |
| `open_period(A) -> str \| None` | самое раннее прогнозное полугодие без факта (se = 0) |
| `path_value(spec, p)`, `trajectory(spec, P)` | значение траектории §0.1 в полугодии / на списке полугодий (память по содержимому) |
| `interp_curve(curve, t)`, `half_rate(r)` | кривая §0.2 (за последним узлом форвард = `LT`); (1 + r)^0,5 − 1 |
| `periods`, `prev_period`, `next_period`, `prev_same_half`, `period_index`, `period_start`, `period_end`, `period_of` | линейка полугодий |
| `book_warnings(A) -> list[str]` | несмертельные замечания (например, траектория без `LT`, которую §0.1 продлевает навсегда) |
| `today() -> date` | сегодня по Москве (UTC+3, фиксированное смещение `MOSCOW`); `FAKE_TODAY=ГГГГ-ММ-ДД` подменяет |

Книга после загрузки — только для чтения: словари не правят на месте (память
траекторий и копии `override` делят блоки). Правка — только `override`/`add_observation`.

`model/book_schema.py`: `validate_book(A)`, `kernel_sha256(worlds)` (хэш миров
без `source`: JSON `sort_keys`, без пробелов, UTF-8; схема сверяет его только с мирами
самой книги, происхождение — `ops/tools/check_worlds.py`), `get_node(A, path)`,
имена осей `WORLDS`, `REGIMES`, `CAPEX_LEVELS`, `DEMANDS`, `TARIFFS`, `CREDITS`.

### Факты — `model/facts.py`

| Функция | Что делает |
|---|---|
| `default_facts_dir() -> Path` | `data/facts`, если там есть `accounting.json`, иначе фикстура `tests/fixtures/facts` (предупреждение `FactsFallbackWarning`) |
| `load_facts(path=None) -> Facts` | все `*.json` каталога; узлы `{"v", "src"\|"calc"}` раскрываются в значения; число без `src` и `calc` — `FactsError`; `null` → `None` (не 0). `Facts.raw` — JSON как есть (с источниками, для выпуска), `Facts.data` — раскрытый, `Facts.get("файл.ключ…")`, `Facts.fixture` |
| `core_facts(F, A) -> CoreFacts` | проверенные факты прохода клетки: выручка, скорр. и отчётная EBITDA, D&A и capex якоря, capex 2L полугодий до якоря (`capex_hist`: выручка × capex/выручку из `history.json` — выбывание базы D&A якоря), история индекса эффективной площади (§4.1: `eff_start` = S — первое полугодие с площадью в фактах, `area_end` на конец S и якоря, открытия `gross_opened` S − n + 1 … якорь, закрытия `closed_area` S + 1 … якорь), ЧД, дивиденды к выплате, NWC, строки моста (`BridgeLine`: key, label, amount, included), акции в обращении (`shares_mln`) и казначейские (`treasury_mln`), реестр (`DeclaredDividend`: id, amount, ex_date, in_company). `None` там, где значение нужно, — `FactsError` с путём |

## Расчёт

### Контекст и клетка — `model/core.py`

```python
ctx = Context(A, facts_or_corefacts, valuation_date=None, market_price=None)
res = run_cell(ctx, ctx.cell("H", "floor", "base"))
```

`Context` — всё общее для клеток одной книги на одну дату: `P` (полугодия),
`timing` (`Timing`: `closed`, `elapsed`, `roll` — Δ переката в годах, `fraction`,
`t_mid`, `t_end`), `market_price`, `governance`, `facts` (`CoreFacts`),
`opcash_anchor`, `buffer_anchor`, `bridge_total`, `dividends_declared` (сумма
реестра с отсечкой ≤ даты оценки), `da_runoff` (S(k)/S(0), k = 1…N + 2L — доля
базы D&A якоря, §4.5), `treasury_mln`, `treasury_value` (n·k·P_рынок/1000, §7.2),
цена: `price_of(equity)`, `v0_of(price, d)`, `rub_per_1pct(v0)`; кэши:
`world(w) -> WorldPaths` (key, cpi, food, index, pi_lt, z_fix — трёхлетний
форвард на начало полугодия, z_lt, r_terminal, df, df_end), `regime(r) ->
RegimePaths` (target, season, deviation по полугодиям; значения на якоре;
target_lt), `network(tariff) -> NetworkPaths` (открытия, закрытия, площадь, Ā_eff по полугодиям; `eff_hist` — A_eff на конец S … якорь, `eff_avg_hist` — Ā_eff якоря − 1 и якоря), `revenue(w, tariff, demand) ->
RevenuePaths` (выручка, чек, трафик, ticket_lt, traffic_lt), `rates(w, credit)
-> RatePaths` (debt, half_debt, half_clean — без издержек размещения, half_fair —
со спредами base, half_yield, half_key; индекс N — терминал), `maintenance(level)`.
Живые входы — только `valuation_date` (дата принятой цены) и `market_price`;
по умолчанию `meta.valuation_date` и `meta.market_price`.

`CellResult` клетки: `cell` (`Cell`: world, regime, capex, growth, credit, demand;
`key` = "W|режим|capex"), `rows` (кортеж `HalfRow` по полугодиям; поля — ниже),
`terminal` (`Terminal`: growth g, rate r, pi, debt_rate r_T, halves — два
`TerminalHalf` (с da и da_pi по полугодию, buffer, gross_debt_start, interest,
shield, issuance_cost, excess_spread, buffer_carry), tv_da_transition, tv_flow,
tv_shield, tv_issuance, tv_excess_spread, tv_buffer_carry, tv_financing,
ebitda_rep_annual; TV — на конец явного участка), `ev`, `pv_fcff`, `pv_shield`
(явный участок), `pv_terminal` ((TV + TV_S)·df_end), `pv_issuance`,
`pv_excess_spread`, `pv_buffer_carry` (вычеты: явный участок + терминал;
`pv_financing` — их сумма), `terminal_share` (чистый терминал / EV), `claims`
(`Claims`: net_debt_fact, opcash_anchor, bridge, rolled, dividends, total = D),
`equity` (= EV − D), `price`, `ebitda_ntm` (скорр. EBITDA текущего и следующего
полугодий), `ev_ebitda_fwd`, `max_leverage`, `margin_min/max`, `capex_pct_years`.

`HalfRow`: period, revenue, revenue_annual, ticket, traffic, area_end,
eff_area_avg, opened, closed, margin, target, deviation, adj_ebitda, lti,
ebitda_rep, da, ebit, capex, capex_maintenance, capex_growth, capex_infra,
price_index, nwc, nwc_change, opcash, opcash_change, buffer, lease, proceeds,
tax_base, tax_unlevered, tax_actual, shield, fcff, debt_rate, gross_debt_start,
interest, issuance_cost, excess_spread, buffer_carry, net_debt_pre, dividends,
net_debt, ebitda_rep_ltm, leverage, fraction, t, df.

Прочее: `make_cell(A, w, r, c)`, `grid_position(P, day)`, `ruler(first, day)`,
`make_timing(P, v, curve_as_of)`, `homogeneity(A, w, demand, year|None)`,
`season_of(A, p)`, `annuity_ratio(x, L, n=L)` (S_x(n)/L, §6), `steady_da(c1, c2, x, L)`
(установившаяся D&A T1, T2 — точная сумма 2L когорт и при полуцелом L),
`forward_rate(curve, start, tenor)`, `price_of_equity(equity, g_gov, N, n=0, T=0)`,
`v0_from_price(price, d, g_gov, N, n=0, T=0)`, `rub_per_1pct_ev(v0, g_gov, N, n=0)`
(n = 0 — формула без казначейского пакета).

### Сетка, слои, точка — `model/grid.py`

```python
grid = evaluate(A, facts, valuation_date=None, market_price=None, lam=None)
low, high = layer_prices(A, facts)          # короткий путь для прогонов полосы
```

`Grid`: `ctx`, `cells` (36 × `GridCell`: `result` — `CellResult`, `p` — {слой:
вероятность}, `key`), `regime_prior`, `regime_posterior` (A-P2u),
`regime_steps` (`RegimeUpdate` по наблюдениям), `layers` (`analytical`,
`market_implied`, `macro_neutral` → `LayerResult`: world_weights, v0, d, equity,
price, pv_fcff, pv_shield, pv_terminal, pv_issuance, pv_excess_spread,
pv_buffer_carry, treasury_value, terminal_share, ev_ebitda_fwd, v0_to_d; `ev_parts` —
строки EV со знаком, сумма = V0),
`worlds_only` (N, H, M → `LayerResult` «только этот мир»), `point` (`Point`:
low, high, central, lam, rates_view, v0_point, v_star, gap_point,
rub_per_1pct_ev_point, equity_share_of_ev), `grid.cell(w, r, c)`.

Функции: `regime_updates(ctx) -> (posterior, steps)`, `cap_shift(prior, post, cap)`,
`layer_world_weights(A)`, `layer_of(ctx, name, weights, cell_results, regime_p)`,
`point_of(ctx, layers, lam)` (точка при другом λ без пересчёта клеток — таблица
`by_lambda`), `ctx.v0_of(price, d)` (EV, при котором функция «EV → цена» даёт
цену: V\* для рыночной, V0 медианы для медианы, §7.3), `ctx.rub_per_1pct(v0)`,
`expected_path(grid, layer="analytical")`
(полугодия: средние строк по вероятностям клеток; маржа и рычаг — отношения
средних), `annual_path(grid, halves)` (годы; год якоря — с фактом якоря, поле
`fact`).

Скорость: сетка ≈ 6 мс (36 клеток); контекст дешёвый — неизменённые траектории
берутся из памяти по содержимому, поэтому прогон полосы со всеми осями — ≈ 5 мс,
2 000 прогонов — ≈ 10 с.

### Проверки — `model/checks.py`

* `invariants(grid) -> [Invariant(name, ok, detail)]`: `probabilities` (1e-12),
  `fcff_identity`, `debt_identity`, `capex_identity`, `ev_identity`, `finite`.
  `printed_ok(value, printed, step)`, `round_to_step(value, step)` (половина — вверх),
  `payload_size_ok(n_bytes)` (≤ 500 000).
* `gate_masses(grid) -> [GateResult]` — гейты §13.2 без объяснений: fired, mass
  (слой «свой взгляд»; гейт уровня слоя — масса 1), cells, detail.
  `gates(grid, explanations=None, today=None)` — с объяснениями
  (`data/assumptions/gate_explanations.yaml`, `load_gate_explanations(path)`):
  status `ok | explained | unexplained | expired | mass_outside`, `blocking`.
  `blocking_reasons(invariants, gates) -> [str]` — пусто, значит сборка выходит.
* Флаги §13.3: `flag_book_update(A, live_nodes {"5","10"} | None, today)`,
  `flag_dividend_register(expected_ex_dates, register_rows, facts_date, today)`,
  `flag_price_fallback(status)` → `Flag(name, title, raised, detail)` (деталь словами: живая цена /
  цена книги / запасная цена).
  `stale_release` — забота витрины.

### Таблицы книги — `model/book_results.py`

`python -B -m model.book_results [--book ФАЙЛ] [--facts КАТАЛОГ] [--out КАТАЛОГ]` →
`data/assumptions/results.json` и `run_output.txt` на входах книги.
`book_results(A, facts, book_path=…) -> dict`, `render_run_output(dict) -> str`.
С частью 2 в `results.json` — раздел `band`: полоса (§9), вклады, суждения по цене ошибки,
обратный DCF (§11), «что даст отчёт» (§12); `--no-band` — без него.

## Что нужно части 2

* Полоса (§9): на каждый прогон `B = override(A, axis.paths, axis.kind, значение)`
  по всем осям (ось `dict`: `blend_weights(книга, конец, |s|)`), затем
  `layer_prices(B, core_facts)` → (низ_i, верх_i). `CoreFacts` передавать один
  раз собранным (`core_facts(F, A)`), не `Facts`.
* Медиана/V0 медианы: `grid.ctx.v0_of(median, grid.layers["analytical"].d)`.
* «Что даст отчёт»: `add_observation(A, period, value)`; ожидание модели —
  `expected_path(grid)` в строке открытого полугодия (`open_period(A)`).
* Суждения по цене ошибки: `evaluate(override(A, paths, kind, low|high), F).point.central`.

## Часть 2: полоса, обратный DCF, «что даст отчёт», журнал, выпуск

### Полоса и то, что считается на её гиперкубе — `model/uncertainty.py`

```python
dist = distribution(A, cf, grid, valuation_date=v, market_price=p, period=open_period(A),
                    fast=False, round_draws=1)
```

`Distribution`: `band` (`Band`: n, seed, lam, market_price, axes, positions s[i][j], low,
high — (низ, верх) каждого прогона), `stats` (p10, p25, median, p75, p90, mean, p_below —
по прогонам, округлённым до `round_draws` знаков, как они лежат в выпуске), `contributions`
(вклад оси по Спирмену), `judgements` («суждения по цене ошибки»: точка при low и high оси),
`reverse_dcf` (§11: решение для медианы — `search_value` поиска на подвыборке, `solved` после
уточнения секущей на полной полосе, `gap_full` — невязка полной полосы; решение для точки),
`next_report` (§12: таблица — медианы полной полосы, нейтральная маржа медианы (с невязкой
`neutral_gap`) и точки, ₽ медианы на 0,1 п.п.), `subsample`, `delta` (δ подвыборки).

Кирпичи: `tri_s(u)`, `lhs(n, k, seed)` (точный порядок случайных чисел §9),
`draw_positions`, `band_axes(A)`, `trial_book(A, axes, s)`, `at_end(A, axis, "low"|"high")`,
`band(A, cf, …)`, `band_stats(low, high, λ, рынок)`, `quantile7`, `contributions`, `bisect`
(≤ 40 шагов, стоп по цене; возвращает и наклон последней скобки), `secant_refine` (1–2 шага
секущей на полной полосе), `Subsample` (`at` — медиана первых m прогонов + δ, `full` — медиана
всех прогонов; оси, совпавшие путями с осью обратного DCF, фиксируются), `with_fact(A, p, m)`, `expectation(grid, p)` (ожидание модели
на полугодие: средние маржа, выручка, скорр. EBITDA по клеткам слоя; маржа по режимам).

**Процессы.** `run_draws` считает прогоны пулом `spawn` кусками по порядку строк; результат бит в
бит не зависит от числа процессов (`X5_WORKERS` = целое ≥ 1 или `auto`; по умолчанию — ядра
машины, не больше 16; в рабочем процессе пула и при < 64 прогонах — последовательно). Пул
закрывается при выходе (`close_pool`). Подмены кода в памяти (monkeypatch) рабочим не видны —
тесты с ними зовут `n_workers=1`.

**Быстрая сборка** (`fast=True`): полоса на 200 прогонах, подвыборка ≤ 40 — для проверок, в
выпуск не идёт (`meta.fast = true`, защита заголовка её не сравнивает).

Замер на книге 1.1.1 (ноутбук, 8 логических ядер, `X5_WORKERS=8`, 29.09.2026):
`model.book_results` с полосой — 97–110 с, полная сборка выпуска
`ops/build_release.py --book` — ≈ 97 с; `model.book_results` на одном процессе — 8 мин
(450 с процессорного времени); `results.json` на 1 и 8 процессах совпадает байт в байт.
С книги 1.1 обратный DCF, «что даст отчёт» и нейтральная маржа уточняются на полной
полосе (десятки полных полос вместо одной), поэтому сборка примерно втрое дольше, чем на
книге 1.0 (≈ 35 с на той же машине). Под параллельной нагрузкой на те же ядра время
растёт вдвое и больше (231 с у `model.book_results` рядом с чужой сборкой).

### Что изменилось — `model/attribution.py`

`vs_previous(A, cf, previous, valuation_date=, market_price=, point_now=, meta_now=)` → блок
`changes.vs_previous`: строки `residual` («книга, факты и код» — точка на прошлых дате и цене
минус прошлая точка), `market_price` (прошлая дата, нынешняя цена), `valuation_date` (перекат);
сумма = изменению точки. `reference` — опорные числа прошлого выпуска для защиты заголовка
(печатаемая и точная медиана, V0 медианы, версия книги, дата фактов, `fast`).

### Журнал прогнозов — `model/journal.py`

`update(previous_journal, period=, forecasts={цель: прогноз}, bench=benchmarks(F, p),
recorded_at=, actuals=actuals_of(F))` → (журнал, id новых записей). Цели `x5.adj_margin`,
`x5.revenue_growth`; эталоны `same_half_last_year`, `mean_two_halves`, `last_half`
(`reported_halves(F)`: actuals → accounting → history). Прошлые записи переносятся как есть;
`actual`/`errors` заполняются один раз из `data/facts/actuals.json`; после факта прогноз не
пишется. `actuals_of(F)` читает исходный файл: `value` — узел `{"v": число, "src"|"calc"}`;
число без узла (без источника), чужая цель, не полугодие, повтор — `FactsError`. `immutability_problems(old, new)` — побайтовая сверка канонического JSON.

### Выпуск — `model/payload.py`

```python
payload = build_payload(live=None, previous=None, journal=None, fast=False,
                        book=None, facts=None, strict=True, explanations=None,
                        notes_path=None, n_workers=None, release_history=None)
problems = validate(payload)          # [] — годен
```

`build_payload` → словарь строго по `docs/PAYLOAD.md` (`REQUIRED_TOP_LEVEL`). `strict=True`
(конвейер): инвариант, необъяснённый гейт или нарушение контракта — `ReleaseBlocked(reasons)`;
`strict=False` (образец) — причины в stderr, выпуск возвращается. `journal` — журнал ветки
`data` (объект с `entries` или список); нет — берётся `previous["journal"]`. `release_history` —
`history.json` ветки `data` (список строк): по строкам публикаций `journal.releases` находит
книгу выпуска, записавшего прогноз, и когда цепочка прошлых выпусков до него не дотягивается.

`validate(payload, notes_path=None, previous_journal=None, today=None)`: блоки
`REQUIRED_TOP_LEVEL` (и ничего сверх), конечность чисел, размер ≤ 500 000 байт компактного
JSON и `meta.bytes`, `meta.payload_sha256` = хэш содержания, печать = округлению (заголовок,
полосы, точка), длины `draws_low/draws_high` = `headline.draws`, 21 строка `by_lambda` (λ =
i/20), 36 клеток, уникальные id журнала (и неизменяемость, если дан прошлый журнал), записки
`data/assumptions/release_notes.yaml` (без `expected_median` или `valid_until` — ошибка),
защита заголовка §13.4: скачок печатаемой медианы > 25 % или V0 медианы > 10 % против
`changes.vs_previous.reference` без новой книги, новых фактов или действующей записки.

**Хэш** `meta.payload_sha256` — sha256 канонического JSON (sort_keys, без пробелов, UTF-8) без
`meta.generated_at`, `meta.payload_sha256`, `meta.bytes`, `meta.previous_sha256`,
`live.fetched_at`, блока `changes` и поля `release_sha` записей журнала: время сборки и ссылки
на выпуски вне хэша, одни входы — один хэш (урок 850oa). Новые записи журнала получают
`release_sha` = хэш своего выпуска.

**Печать.** Числа выпуска — 9 значащих цифр; прогоны — до 0,1 ₽; точные медиана, полосы и
точка — до копейки; статистики полосы считаются по прогонам выпуска, поэтому ползунок λ
витрины воспроизводит заголовок бит в бит. Шаг печати — `valuation.headline.print_step`,
половина — вверх (`checks.round_to_step`).

`python -B -m model.sample_release [--live ФАЙЛ] [--fast]` — образец `var/release/sample.json`
на входах книги с полной полосой (гейты не блокируют), печатает время сборки.

### Живые входы (`live`) — что ядро ждёт от конвейера

Словарь `indicators.live.collect_live()` (`schema: x5-live-v1`); все блоки, кроме `price`,
необязательны (нет — null/пусто, выпуск выходит):

| Ключ | Форма | Что делает ядро |
|---|---|---|
| `price` | `{value, date, time, source, kind, status ("live"\|"fallback"), accepted, reason, last_accepted {value, date}}` | цена рынка = `value`; **дата оценки = max(`date`, `meta.date` книги)** (§13.4); статус → `market.price_status`, флаг `price_fallback`; блок (с `last_accepted`) переносится в `live.price` выпуска — эталон следующего прогона |
| `curve` | `{as_of, time, nodes {"1","3","5","10": доля}, source}` | только наблюдение: флаг `book_update` (сдвиг узлов 5/10 лет от кривой мира `neutral_world` книги), `live.curve.shift_bp`, плитки ОФЗ 5 и 10 лет |
| `key_rate` | `{value, date, source, note, history [{date, value}]}` | плитка ключевой ставки, строка `inputs` |
| `peers` | `{TICKER: {price, date, time, kind}}` | цены аналогов в `market.peers` (MGNT, LENT, FIXR, OKEY) |
| `bonds` | `[{isin, secid, name, coupon_type, coupon, put_date, maturity, price, price_date, ytm, duration_years, outstanding, turnover, source}]` | цены и доходности в `debt.bonds` (по ISIN поверх `debt_register.json`), спред фиксированных облигаций к ОФЗ на их дюрации (плитка, б.п.) |
| `price_history` | `[{date, close}]` за 12 мес. | график `market.price_history` (≤ 130 точек), плитка цены |
| `fetched_at`, `run_date`, `errors` | время сбора UTC, дата прогона, `{источник: причина}` | `live.fetched_at` (вне хэша), `live.errors`, `live.degraded` |

Без `live` — входы книги: дата оценки `meta.valuation_date`, цена `meta.market_price`
(`market.price_status = "book"`), плиток нет (кроме перенесённых из `previous`).
