# Письменные объяснения сработавших гейтов правдоподобия (docs/MODEL.md §13.2).
#
# Гейт — не ошибка, а место, где модель расходится с историей или с рынком.
# Расходиться можно, не замечать — нельзя: сработавший гейт без действующей
# записи здесь роняет сборку выпуска.
#
# Формат записи (все три ключа обязательны, других ключей нет):
#
#   ключ_гейта:                      # ev_ebitda | margin_range | capex_range | terminal_share |
#                                    # real_rate | leverage_path | equity_cushion
#     explanation: >-
#       Текст для человека: где и почему срабатывает, почему это не ошибка книги.
#     expected_mass: [0.05, 0.20]    # коридор массы (доли 0…1): суммарная вероятность клеток
#                                    # слоя «свой макро-взгляд», где условие нарушено
#     valid_until: "2027-04-30"      # объяснение действует ВКЛЮЧИТЕЛЬНО по эту дату
#
# Сборка падает, если гейт сработал и: записи нет; срок истёк; масса вне
# коридора `expected_mass` (объяснение описывает не ту картину). Незнакомый
# ключ гейта или записи — ошибка чтения файла (model/checks.py).
#
# Массы и срабатывания на входах книги печатает `python -B -m model.book_results`
# (data/assumptions/run_output.txt, раздел «Гейты»).
#
# Файл пишет команда приёма миров (ops/tools/adopt_worlds.py, шаг gate-texts) из шаблона
# gate_explanations.tpl: слова — шаблона (их пишет ведущий), числа — метки из таблиц книги
# (results.json), книги и листа гейтов; правится шаблон, не этот файл. Гейт погас — запись
# снимает команда; новый гейт или другой состав клеток — красный флаг отчёта команды.

ev_ebitda:
  #= состав: res:gates[name=ev_ebitda].cells@sorted = ["H|stress|high", "M|floor|low", "M|full|base", "M|full|high", "M|full|low", "M|partial|low", "N|full|low"]
  explanation: >-
    Срабатывает в двух хвостах (EV клетки к скорр. EBITDA текущего и следующего полугодий). Снизу — клетка
    «Стресс» при высоком capex в мире «Высокие ставки надолго» ({{res:cells[world=H&regime=stress&capex=high].ev_ebitda_fwd r2}}×, вес {{res:cells[world=H&regime=stress&capex=high].p_analytical r3}}) при низе коридора {{book:checks.ev_ebitda.H[0] r1}}×;
    в мирах «Нормализация» и «Рыночный как есть» те же клетки чуть выше края и не срабатывают ({{res:cells[world=N&regime=stress&capex=high].ev_ebitda_fwd r2}}× и
    {{res:cells[world=M&regime=stress&capex=high].ev_ebitda_fwd r2}}×; все три вместе весят {{res:cells[*regime=stress&capex=high].p_analytical@sum r3}} = P(стресс) {{res:regimes.posterior.stress r3}} × P(высокий capex | стресс) {{book:joint.capex_prob_given_regime.stress.high r2}}): маржа стресса
    (цель LT {{res:regimes.target_lt.stress p1}} %) при высоком поддерживающем capex ({{book:capex.maintenance.high.LT p1}} % выручки в стационаре) оставляет свободный
    поток около нуля, и EV клетки близка к долгу. Сверху — мир «Рыночный как есть»: «Дно», «Частичный
    возврат» и «Полный возврат» при низком capex и «Полный возврат» при базовом и высоком ({{res:cells[*world=M&capex=low&regime!=stress|world=M&regime=full].ev_ebitda_fwd@span r1}}× при
    коридоре до {{book:checks.ev_ebitda.M[1] r1}}×; «Дно» при низком capex — {{res:cells[world=M&regime=floor&capex=low].ev_ebitda_fwd r2}}× и «Полный возврат» при высоком — {{res:cells[world=M&regime=full&capex=high].ev_ebitda_fwd r2}}× — у самого
    края, вышли за него с мирами книги 1.2), и мир «Нормализация»: «Полный возврат» при низком capex
    (≈{{res:cells[world=N&regime=full&capex=low].ev_ebitda_fwd r1}}× при коридоре до {{book:checks.ev_ebitda.N[1] r1}}×); масса {{res:gates[name=ev_ebitda].mass r3}} (на книге 1.1.4 — 0,047, пять клеток). В мире «Рыночный как
    есть» причина — сам мир: его центральные клетки («Дно» и «Частичный возврат» при базовом capex, ≈{{res:cells[world=M&regime=floor&capex=base].ev_ebitda_fwd r1}}× и
    ≈{{res:cells[world=M&regime=partial&capex=base].ev_ebitda_fwd r1}}×) уже выше всей истории режима высоких
    ставок ({{gs:ev_ebitda.stats.M.fwd_min r1}}–{{gs:ev_ebitda.stats.M.fwd_max r1}}× к EBITDA четырёх следующих кварталов на конец квартала, 2023–2026 гг.),
    потому что модель не переносит нынешний ценовой дисконт X5 (2,8× на 25.09.2026) в стоимость;
    снижения capex до низкого уровня хватает, чтобы выйти за коридор 1,25 × {{gs:ev_ebitda.stats.M.fwd_max r2}}. «Полный возврат» при
    низком capex в мире «Нормализация» — маржа как в лучшие годы X5 ({{res:regimes.target_lt.full p2}} %) при capex у нижней
    границы. Это не ошибка книги: обе стороны — хвостовые сочетания осей (X5 под давлением режет
    вложения, поэтому стресс редко идёт с высоким capex — суждение A-P3); центральные клетки миров
    «Нормализация» и «Высокие ставки надолго» («Дно» и «Частичный возврат» при базовом capex) —
    {{res:cells[*world=N,H&regime=floor,partial&capex=base].ev_ebitda_fwd@span r1}}×, внутри коридоров.
  expected_mass: [0.02, 0.12]
  valid_until: "2027-04-30"

equity_cushion:
  #= всегда
  #= состав: res:gates[name=equity_cushion].fired = false
  explanation: >-
    Срабатывает в клетке «Стресс» при высоком capex мира «Высокие ставки надолго», когда её капитал
    уходит ниже нуля: EV клетки близка к долгу, как в нижнем хвосте гейта EV / EBITDA (на входах
    книги {{book:meta.version str}} капитал выше нуля — {{res:cells[world=H&regime=stress&capex=high].equity s1}} млрд ₽ при EV {{res:cells[world=H&regime=stress&capex=high].ev r0}} млрд ₽, и гейт не срабатывает; на книге
    1.1.4 было +34,0 млрд ₽, на 1.1.3 — +0,8 млрд ₽, на 1.1.1 — −3,0 млрд ₽), вес клетки {{res:cells[world=H&regime=stress&capex=high].p_analytical r3}}. В мирах
    «Нормализация» и «Рыночный как есть» те же клетки выше нуля ({{res:cells[world=N&regime=stress&capex=high].equity r0}} и {{res:cells[world=M&regime=stress&capex=high].equity r0}} млрд ₽); все три вместе весят
    {{res:cells[*regime=stress&capex=high].p_analytical@sum r3}}. Это
    хвостовое сочетание осей, а не ошибка книги: цена клетки считается по той же формуле, что и у слоя
    (капитал + выручка от продажи казначейского пакета на выпущенные акции), и остаётся положительной
    (≈{{res:cells[world=H&regime=stress&capex=high].price r0}} ₽), а слои далеко от предела: V0/D ≈ {{res:layers.analytical.v0_to_d r1}} при пределе {{book:checks.min_v0_to_d r1}}, кредитный пут слоя — доли рубля на
    акцию, поэтому возврат к структурной оценке для цены не нужен.
  expected_mass: [0.01, 0.07]
  valid_until: "2027-04-30"
