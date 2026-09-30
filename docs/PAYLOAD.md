# Выпуск X5: контракт `x5-v1`

Выпуск — один JSON (`/api/model`), из которого витрина берёт ВСЕ числа. Единственный
пересчёт во фронте — ползунок λ по `fair_value.draws_low/draws_high`. Новое число для
экрана сначала добавляется сюда и в `model/payload.py`, потом во фронт. Потолок —
500 000 байт компактного JSON; ни одного NaN/Inf. Деньги — млрд ₽, цены — ₽, доли —
доли единицы (витрина сама переводит в проценты). Даты — ISO `YYYY-MM-DD`, время —
ISO UTC.

Обязательные блоки верхнего уровня (`REQUIRED_TOP_LEVEL`): `schema, meta, market,
headline, fair_value, layers, grid, worlds, regimes, capex_levels, paths, debt,
dividends, history, reverse_dcf, judgements, uncertainty, next_report, journal,
calendar, checks, inputs, live, changes, book, indicators`. Витрина читает только
объявленные блоки (тест `test_frontend_reads_only_declared_blocks`).

## schema
`"x5-v1"`.

## meta
`generated_at`, `valuation_date`, `facts_date`, `book_version`, `book_date`,
`engine_commit`, `basis` ("до МСФО 16"), `shares_mln`, `governance_discount`,
`anchor_period`, `first_period`, `last_period`, `open_period` (первое прогнозное без
факта), `curve_as_of`, `closed_periods`, `elapsed`, `payload_sha256`, `bytes`,
`previous_sha256` (или null), `fast` (быстрая сборка на 200 прогонах — в выпуск не идёт).
**Хэш** — sha256 канонического JSON без `meta.generated_at`, `meta.payload_sha256`,
`meta.bytes`, `meta.previous_sha256`, `live.fetched_at`, блока `changes`,
`journal.entries[].release_sha` и `journal.releases`: время сборки и ссылки на выпуски вне хэша, иначе
каждая пересборка на тех же входах — новый выпуск. Числа выпуска — 9 значащих цифр.

## market
* `price`, `price_date`, `price_time` (время сделки ISS, МСК), `price_source` (словами:
  «Мосбиржа, режим TQBR»), `price_status` ("live" | "fallback" | "book" — сборка на входах
  книги), `book_price` (цена книги).
* `market_cap` (по акциям в обращении), `claims` (D слоя «свой взгляд» на дату оценки),
  `market_ev` (= V\*), `equity_share_of_ev` ((V\* − D) / V\* — доля капитала до дисконта за
  управление, как `fair_value.equity_share_of_ev` у модели), `ebitda_rep_ltm`,
  `adj_ebitda_ltm`, `ev_ebitda_ltm` (рыночный EV X5 на базе аналогов — капитализация +
  чистый долг + дивиденды к выплате — к отчётной EBITDA до МСФО 16 за 12 мес.; = строка X5
  в `peers`, книга A-G1), `pe_ltm` (или null), `dividend_yield_ltm`.
* `peers`: `rows` [{`ticker`, `name`, `price`, `price_date`, `market_cap`, `net_debt`
  (чистый долг баланса), `dividends_after_balance` (объявлены до даты баланса, выплачены
  после), `ev` (= капитализация + `net_debt` + `dividends_after_balance`), `ebitda_ltm`,
  `ev_ebitda`, `pe`, `basis`, `reported_on` (дата отчёта компании), `src`}] — X5 первой
  строкой; `as_of` — дата балансов.
* `brokers`: `rows` [{`broker`, `date`, `target`, `rating`, `horizon`, `src`,
  `after_report` (цель выставлена в день отчёта или позже), `in_median` (цель входит в
  медиану; null — правило фактов не воспроизводится)}], `median`, `median_n` (число целей
  в медиане), `median_basis` (правило медианы словами из фактов), `after_report` (метка
  отчёта), `report_date`.
* `price_history`: [{`date`, `close`}] — дневные закрытия за 12 мес. (тонкая выборка
  для графика), `price_min`, `price_max` ({`date`, `close`} по полному ряду),
  `ex_dividend`: [{`date`, `dps`}].

## headline
`central` (медиана, точная), `printed_central`, `band` [P10, P90], `printed_band`,
`inner` [P25, P75], `printed_inner`, `mean`, `p_central_below_market` (витрина печатает
с двумя знаками при значении меньше 1 %),
`market_price`, `print_step`, `draws`, `lambda`. Статистики считаются по прогонам
выпуска, округлённым до 0,1 ₽ (так же, как их пересчитает фронт); печать — к шагу,
половина вверх.

## fair_value
* `low`, `central`, `high` (точка при центральных значениях: низ, точка при λ книги,
  верх), `printed` {low, central, high}, `lambda`, `lambda_step` (0,05).
* `rates_view` {`rub`: верх − низ}.
* `by_lambda`: [{`lambda`, `point`, `median`, `p10`, `p25`, `p75`, `p90`, `mean`,
  `p_below`}] — 21 строка λ = 0, 0,05, …, 1 (контроль фронта; фронт считает сам по
  прогонам, таблица — сверка).
* `draws_low`, `draws_high`: массивы длины `headline.draws` (цены ₽, округление до 0,1).
* `center_ev`: {`v0_median`, `v0_point`, `v_star`, `gap_median`, `gap_point`,
  `rub_per_1pct_ev_median`, `rub_per_1pct_ev_point`, `ebitda_ntm` (скорр. EBITDA
  слоя «свой взгляд» текущего и следующего полугодий), `ev_ebitda_ntm_median` (EV медианы /
  `ebitda_ntm`), `ev_ebitda_ntm_market` (V\* / `ebitda_ntm`)} — пара «модель — рынок» на
  одной базе. База — полугодие даты оценки (первое незакрытое прогнозное:
  `meta.first_period`, сдвинутое на `meta.closed_periods` полугодий) и следующее за ним
  (`docs/MODEL.md` §13.2): при дате оценки во 2П 2026 — 2П 2026 + 1П 2027, с 01.01.2027 —
  1П 2027 + 2П 2027. Это не скользящие 12 месяцев: прошедшая часть текущего полугодия в
  базе есть, 1 января база сдвигается на полугодие. Имена `ntm` и `fwd` — условные, ключи
  контракта не меняются; витрина подписывает базу периодами из `meta`.
* `equity_share_of_ev` (капитал / V0 точки).

## layers
`analytical`, `market_implied`, `macro_neutral` — у каждого: `title`, `world_weights`,
`v0`, `d`, `equity`, `price`, `pv_fcff`, `pv_shield`, `pv_terminal`, `pv_financing`
(PV вычетов финансирования — издержки размещения, проценты сверх справедливого спреда,
кэрри подушки, положительным числом: `v0` = `pv_fcff` + `pv_shield` + `pv_terminal` −
`pv_financing`), `pv_terminal_financing` (терминальная часть `pv_financing`: Σp·TV_fin·df =
`pv_terminal` − `terminal_share`·`v0`), `terminal_share` (доля терминала **чистая** —
(`pv_terminal` − `pv_terminal_financing`) / `v0`), `ev_ebitda_fwd` (EV / скорр. EBITDA
текущего и следующего полугодий — база та же, что у `fair_value.center_ev.ebitda_ntm`),
`ebitda_ntm` (скорр. EBITDA текущего и следующего полугодий), `v0_to_d`.

## grid
`cells`: 36 × {`world`, `regime`, `capex`, `p_analytical`, `p_market_implied`,
`p_neutral`, `ev`, `d`, `equity`, `price`, `margin_lt`, `ev_ebitda_fwd`,
`terminal_share`, `max_leverage`}. `regime_order`, `capex_order`, `world_order`.

## worlds
По N, H, M: `name`, `weights` {analytical, market_implied, macro_neutral},
`key_rate` [{year, value}] (среднее за год), `cpi`, `food_cpi` (то же),
`zero_curve` {1, 3, 5, 10, LT}, `lt_inflation`, `r_terminal` (z_LT + β_u·ERP),
`real_terminal`, `price` (цена слоя «только этот мир»), `v0`. `source` —
`worlds.source` книги как есть: {`book` (книга Магнита и её тег), `curve_date`,
`tag_commit`, `kernel_sha256`}.

## regimes
По stress, floor, partial, full: `title` («Стресс», «Дно», «Частичный возврат», «Полный
возврат» — слова книги), `target` [{period, value}] (якорь…LT),
`lt`, `prior`, `posterior`, `demand`. `history`: [{period, adj_margin, rep_margin}]
полугодия 2018H1–якорь; `annual_history`: [{year, adj_margin}] 2011–последний год.
`expected_lt` (Σ posterior × LT). `update`: {sigma_pp, rho, cap_pp, observations}.

## capex_levels
По low, base, high: `maintenance` [{year, value}], `lt`, `p_given_regime`
{stress, floor, partial, full}. `history`: [{year, capex_pct, da_pct}] (`da_pct` —
амортизация **и обесценение** внеоборотных активов к выручке, как в databook; модель
считает D&A без обесценения).
`price_per_m2`, `infra_per_m2`, `maintenance_area_share`.

## paths
Ожидаемый путь слоя «свой взгляд» (взвешенный по вероятностям клеток), по годам
2026–2036 и отдельно полугодия. Год якоря (2026) — факт 1П + прогноз 2П только в строках
из `fact` (выручка, скорр. и отчётная EBITDA, D&A, capex, LTI); строки из
`forecast_only` (чек, трафик, разбивка capex, ΔNWC, налог, FCFF, щит, проценты,
дивиденды) — только прогнозные полугодия `forecast_periods` (2П). Запасы (площадь,
чистый долг, рычаг) — на конец года.
`annual`: [{`year`, `revenue`, `revenue_growth`, `ticket`, `traffic`, `area_end`,
`area_growth`, `margin`, `adj_ebitda`, `lti`, `da`, `capex`, `capex_maintenance`,
`capex_growth`, `capex_infra`, `capex_pct`, `nwc_change`, `tax_unlevered`, `fcff`,
`shield`, `interest`, `dividends`, `net_debt`, `leverage`, `fact`, `forecast_only`,
`forecast_periods`}]. `ticket`, `traffic` — рост г/г чека (выручка без НДС, с клином
`revenue.vat_effect`) и трафика **зрелой сети** (созревание новых магазинов — в площади,
поэтому отчётный LFL X5 выше), взвешенные по выручке прогнозных полугодий года;
`halves`: [{`period`, `revenue`, `margin`, `adj_ebitda`, `capex`, `fcff`, `net_debt`}],
`fact_marks`: какие строки — факт.

## debt
* `anchor`: {`as_of`, `total_debt`, `cash`, `net_debt`, `leverage`, `leasing`,
  `lease_liabilities_ifrs16`, `credit_lines_unused`, `floating_share`,
  `effective_rate`, `ratings` [{agency, rating, outlook, date}]}.
* `bridge`: {`lines` [{`key`, `label`, `amount`, `included`, `src`}] — строки моста из
  отчётности; `ev_rows` [{`key`, `label`, `amount`}] — из чего складывается V0 слоя «свой
  взгляд» (сумма = `v0`); `v0`; `rows_at_valuation` [{`key`, `label`, `amount`}] —
  разложение D на дату оценки (ЧД факт, операционная касса, строки, дивиденды к выплате,
  перекат; сумма = `total`); `total`; `equity` (= `v0` − `total`); `equity_rows`
  [{`key`, `label`, `amount`}] — что прибавляется к капиталу в формуле цены (выручка от
  продажи казначейского пакета; пусто — ничего); `treasury_mln` (казначейский пакет, млн
  акций: цена = (`equity` + Σ `equity_rows`) × (1 − g) / (акции в обращении +
  `treasury_mln`))}. Витрина рисует все строки всех списков, какие есть: новая
  составляющая EV или требований — новая строка.
* `bonds`: [{`isin`, `name`, `outstanding` (в обращении — из реестра фактов: он учитывает
  бумаги, выкупленные по оферте; размещённый объём ISS — только без факта),
  `outstanding_anchor` (на дату баланса якоря), `coupon_type`, `coupon`, `spread`,
  `put_date`, `maturity`, `price`, `ytm`, `as_of`}]; `bank_loans` {short, long, total}.
* `wall`: [{`period` (квартал/год), `bonds`, `banks` (null — график кредитов по срокам не
  раскрыт)}] — график погашений/оферт; `wall_note` — как построена стена, словами из
  реестра фактов (`debt_register.wall_note`; null — нет).

## dividends
`policy` {target_leverage [lo, hi], no_pay_above, frequency, text}; `register`
[{id, label, dps, amount, record_date, ex_date, pay_until, status (declared | paying |
paid | unclaimed), paid_share, in_claims}]; `history` [{period, label, dps, amount,
record_date}] — по времени, от старых к новым (9M раньше FY того же года); `model`
[{year, amount, dps}] — ожидаемые выплаты модели по **году выплаты** (слой «свой взгляд»;
за 9 мес. — 1П следующего года, финал — 2П); год якоря — только прогнозные полугодия
(`paths.annual[].forecast_only` содержит `dividends`), дивиденды с отсечкой до даты оценки
— в требованиях (`register[].in_claims`), а не здесь;
`next_expected` {label, record_date_est (дата ISO или null: из фактов, иначе ближайшая
отсечка календаря), record_date_note (текст оценки), pay_period (полугодие выплаты:
за 9 мес. — 1П следующего года, финал — 2П), dps_model (дивиденд модели в `pay_period`,
₽ на акцию), note}; `yield_ltm`.

## history
`annual` [{year, revenue, growth, adj_margin, rep_margin, capex_pct, da_pct,
da_excl_impairment_pct (D&A без обесценения, с 2023 г.), other_investing_payments
(прочие платежи по инвестиционной деятельности, млрд ₽), finance_lease_receipts
(тело чистых инвестиций в аренду, млрд ₽), leverage, lfl, lfl_traffic, lfl_ticket,
area_end, stores_end}], `halves`
[{period, revenue, growth, adj_margin, capex_pct}], `formats` [{year, pyaterochka,
perekrestok, chizhik, digital, other}] (выручка), `format_area` (то же для площади).

## reverse_dcf
`rows`: [{`name`, `unit`, `book`, `solved`, `delta`, `in_range`, `range`, `status`
("solved" | "unreachable"), `point_solved`}]; `target` (рыночная цена);
`method` (словами: поиск на подвыборке 200 прогонов со сдвигом к полной полосе,
уточнение секущей на полной полосе — `docs/MODEL.md` §11).

## judgements
`rows`: [{`id`, `name`, `unit`, `book`, `low`, `high`, `price_low`, `price_high`,
`swing`, `share`}] — «суждения по цене ошибки» (точка) и вклад в полосу; по
убыванию `swing`.

## uncertainty
`contributions`: [{`axis`, `share`}] (сумма 1), `draws`, `seed`, `axes_count`,
`mean`, `histogram_bins` (рекомендованные границы для гистограммы, ₽).

## next_report
* `period` (открытое полугодие), `events` [{date, title, kind ("trading_update" |
  "ifrs" | "dividend" | "cbr"), confirmed (bool), note}] — от даты оценки до МСФО,
  закрывающего `period` (первое событие `ifrs` с датой позже конца полугодия),
  включительно. Витрина ведёт отсчёт до этого МСФО, а более ранние отчёты (МСФО за
  1/3 кв., `trading_update`) показывает отдельной строкой как события внутри полугодия.
* `expectation`: {`revenue_growth` (г/г полугодия), `revenue`, `margin`,
  `adj_ebitda`, `by_regime` [{regime, margin}]}.
* `guidance`: {`revenue_growth` [lo, hi], `margin_min`, `capex_pct` [lo, hi],
  `openings`, `required_h2_margin`, `required_h2_growth` (нижняя граница),
  `required_h2_growth_range`, `src`}.
* `benchmarks`: [{`name`, `margin`, `revenue_growth`, `note`}] — наивные эталоны
  («то же полугодие год назад», «среднее двух полугодий», «как прошлое полугодие»).
* `table`: [{`margin`, `point`, `median`, `d_point`, `d_median`, `posterior`
  {stress…full}}] (медианы — полной полосы); `neutral` {`median`, `point`};
  `neutral_gap` {`median`} (невязка медианы полной полосы при нейтральной марже, ₽);
  `rub_per_01pp` (медиана на 0,1 п.п. — наклон МНК медианы по `table`, в среднем по таблице).

## journal
`entries`: [{`id`, `target` ("x5.adj_margin" | "x5.revenue_growth"), `period`,
`recorded_at`, `release_sha`, `forecast`, `benchmarks` {name: value},
`actual` (или null), `errors` {forecast, benchmarks}}]; `rule` (текст правила
допуска); `status`; `releases` {`release_sha`: {`book_version`, `generated_at`}} — книга
выпусков, записавших прогнозы (записи неизменяемы, поэтому версия книги — рядом, а не в
них): перенос из прошлого журнала, `meta` прошлого выпуска и его `changes.vs_previous`,
строки публикаций `history.json` ветки `data` (`build_release.py --history`);
только выпуски, на которые ссылаются записи; текущего выпуска в карте нет (его книга —
`meta.book_version`).

## calendar
`events`: [{`date`, `title`, `kind`, `confirmed`, `note`}] ближайших 12 месяцев.

## checks
`invariants`: [{name, ok, detail}]; `gates`: [{`name`, `title`, `fired`, `mass`,
`explanation`, `valid_until`, `expected_mass`}]; `flags`: [{`name`, `title`,
`raised`, `detail`}].

## inputs
`rows`: [{`name`, `value`, `unit` (`version` — версия книги, печатается как есть),
`as_of`, `source` (словами), `status` ("ok" | "stale" | "fallback")}] — цена, кривая,
ключевая, книга, факты.

## live
`price` {value, date, accepted, reason}, `curve` {as_of, nodes {1,3,5,10},
book_nodes, shift_bp {5, 10}}, `key_rate` {value, date (день наблюдения), since (дата
решения, с которой действует ставка; null — раньше окна истории)}, `valuation_date`.

## changes
`vs_previous`: {`previous_sha`, `previous_generated_at`, `rows` [{`component`,
`title`, `rub`}], `total_rub`, `reference` (опорные числа прошлого выпуска для
защиты заголовка), `note`} — атрибуция изменения точки: дата оценки (перекат),
цена рынка, книга/факты/код (остатком).

## book
`version`, `date`, `tag`, `sections`: [{id, title}], `worlds_source`,
`facts_date`, `key_judgements`: [{id, name, value, unit}].

## indicators
`tiles`: [{`id`, `title`, `unit`, `value`, `date`, `change` (к точке `change_from`
полного ряда: у цены — к прошлому закрытию; у ключевой — к прошлому отличному
значению), `change_from`, `since` (у ключевой — дата решения), `min`, `max` ({date,
value} по полному ряду), `history` [{date, value}] (≤ 60 точек, тонкая выборка)}] — X5
(цена), ключевая ставка, ОФЗ 5 и 10 лет (бескупонная кривая), спред облигаций X5 к ОФЗ
(веса — остатки в обращении из реестра фактов).

## Дополнительные поля (не ломают витрину)

`judgements.rows[].kind/paths`, `reverse_dcf.rows[].kind/paths/search/point_status/search_value/gap_full`
(решение поиска на подвыборке и невязка медианы полной полосы в решении, ₽),
`uncertainty.subsample/delta`, `uncertainty.contributions[].rank_corr`,
`checks.gates[].status/blocking/cells/detail`, `checks.invariants[].title`,
`live.errors/degraded/fetched_at`, `grid.cells[].growth/credit/demand`,
`debt.bonds[].series/duration_years`, `next_report.book_period`, `benchmarks[].key`.
