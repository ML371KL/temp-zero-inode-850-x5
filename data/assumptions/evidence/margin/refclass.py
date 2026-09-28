"""(1) Долгосрочные цели режимов и вероятности X5: референс-класс эпизодов книги Магнита 1.5 на шкале X5.

Класс — inputs/episodes_magnit_book15.json (копия листа книги Магнита 1.5, только чтение), правило B:
T0 = пик + 4 года при просадке ≥150 б.п.; исход — средняя маржа T0+4…T0+6; f = (исход − T0)/(пик − T0);
стресс f < −0,10; дно −0,10…0,25; частичный 0,25…0,75; полный ≥0,75. Для эпизодов класса A (Магнит, Лента)
точки считаются по рядам, для B/C — числа листа.

Шкала X5: пик 2021 (годовая скорр. маржа), T0 — LTM 2К2026; потеря = пик − T0.
Цели режимов «дно», «частичный», «полный» = T0 + (средний f режима) × потеря + поправка на смесь форматов
(метод Магнита + арифметика X3 §2.4): возврат — это отыгрыш потерянной маржи, он пропорционален потере.
Цель «стресс» — среднее двух шкал класса (f и п.п.): новое падение — новый удар по статьям затрат в п.п. выручки;
шкала f для мелкой потери X5 сжимает его втрое, шкала п.п. переносит удары других рынков (британская ценовая
война, Dia) целиком; середина — суждение (C).
Центр E[m_LT] — точностно-взвешенное среднее двух оценок класса (шкала f и шкала п.п.), вероятности —
частоты класса со сглаживанием Лапласа ½, минимально наклонённые (по KL), чтобы Σ p·цель = центр.

Запуск: python -B refclass.py → refclass_out.json, печать.
"""
from __future__ import annotations

import math
import random
import statistics as st

import json

from common import HERE, REGIMES, history, load_json, write_json

RU = {"stress": "стресс", "floor": "дно", "partial": "частичный", "full": "полный"}
F_FULL_MAGNIT = 0.842        # f цели «полный» книги Магнита (класс полных возвратов не содержит): episodes README §2
BOOT_N, BOOT_SEED = 20000, 20260928


def cls(f: float) -> str:
    return "stress" if f < -0.10 else ("floor" if f < 0.25 else ("partial" if f < 0.75 else "full"))


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (c - h) / d), min(1.0, (c + h) / d)


def rule_b(series: dict, peak_year: int):
    s = {int(k): v for k, v in series.items()}
    pk, t0 = s[peak_year], peak_year + 4
    if t0 not in s or pk - s[t0] < 1.5:
        return None
    ys = [s.get(t0 + k) for k in (4, 5, 6)]
    if any(v is None for v in ys):
        return None
    return peak_year, pk, t0, s[t0], st.mean(ys)


def episodes():
    d = load_json("episodes_magnit_book15.json")
    out = []
    for ep in d["episodes"]:
        if ep["rel"] == "A":
            r = rule_b(d["series"][ep["series"]]["values"], ep["peak_year"])
            if r is None:
                continue
            py, pk, t0, mt0, o = r
        else:
            if not ep.get("B"):
                continue
            py, pk = ep["peak"]
            t0, mt0, o = ep["B"]
        f = (o - mt0) / (pk - mt0)
        out.append({"id": ep["id"], "chain": ep["chain"], "country": ep["country"], "rel": ep["rel"],
                    "peak_year": py, "peak": pk, "t0": t0, "m_t0": mt0, "outcome": o, "loss": pk - mt0,
                    "f": f, "d_pp": o - mt0, "ratio": o / mt0, "regime": cls(f)})
    return d, out


def tilt(q: dict, t: dict, target: float) -> tuple[dict, float]:
    """Минимальный по KL наклон p ∝ q·exp(λ·t), при котором Σ p·t = target (бисекция по λ)."""
    def mean(lam):
        w = {r: q[r] * math.exp(lam * t[r]) for r in q}
        z = sum(w.values())
        return sum(w[r] / z * t[r] for r in q), {r: w[r] / z for r in q}
    lo, hi = -50.0, 50.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if mean(mid)[0] < target:
            lo = mid
        else:
            hi = mid
    return mean((lo + hi) / 2)[1], (lo + hi) / 2


def main():
    raw, eps = episodes()
    n = len(eps)
    print(f"Референс-класс (лист Магнита 1.5, правило B), n = {n}:")
    print(f"  {'эпизод':<32}{'кл':>3}{'пик':>7}{'T0':>6}{'m_T0':>7}{'исход':>7}{'потеря':>8}{'f':>7}{'Δ п.п.':>8}  режим")
    for e in eps:
        print(f"  {e['chain'] + ' ' + str(e['peak_year']):<32}{e['rel']:>3}{e['peak']:>7.2f}{e['t0']:>6}{e['m_t0']:>7.2f}"
              f"{e['outcome']:>7.2f}{e['loss']:>8.2f}{e['f']:>+7.2f}{e['d_pp']:>+8.2f}  {RU[e['regime']]}")
    by = {r: [e for e in eps if e["regime"] == r] for r in REGIMES}
    freq = {r: len(by[r]) / n for r in REGIMES}
    lap = {r: (len(by[r]) + 0.5) / (n + 2) for r in REGIMES}
    fmean = {r: (st.mean(e["f"] for e in by[r]) if by[r] else None) for r in REGIMES}
    dmean = {r: (st.mean(e["d_pp"] for e in by[r]) if by[r] else None) for r in REGIMES}
    for r in REGIMES:
        lo, hi = wilson(len(by[r]), n)
        print(f"  {RU[r]:<10} {len(by[r])} эп.  доля {freq[r]*100:5.1f} % (Уилсон 95 %: {lo*100:.0f}–{hi*100:.0f})  Лаплас ½ {lap[r]*100:5.1f} %"
              f"  средний f {fmean[r] if fmean[r] is None else round(fmean[r], 3)}  средний Δ {dmean[r] if dmean[r] is None else round(dmean[r], 2)} п.п.")

    # ---------------- шкала X5
    H = history()
    yrs, halves = H["years"], H["halves"]
    peak_year = max((y for y in yrs if 2018 <= y <= 2025), key=lambda y: yrs[y]["m"])
    peak = yrs[peak_year]["m"] * 100
    h1, h2 = halves["2025H2"], halves["2026H1"]
    t0 = (h1["ebitda"] + h2["ebitda"]) / (h1["rev"] + h2["rev"]) * 100
    loss = peak - t0
    t0_b = yrs[peak_year + 4]["m"] * 100
    print(f"\nX5 сейчас: пик {peak_year} {peak:.2f} %, T0 = LTM 2К2026 {t0:.2f} % (правило B, пик + 4 = {peak_year+4}: {t0_b:.2f} %), "
          f"потеря {loss:.2f} п.п. — порог класса 1,50 п.п. НЕ выполнен (глубина {peak - t0_b:.2f} на пик + 4)")

    ext = load_json("external_points.json")
    mx = ext["mix_arithmetic"]

    def group(s):
        st_, mg = mx["structures"][s], mx["margins"][s]
        return sum(st_[k] * mg[k] for k in st_) - mx["center_cost"]
    g25, g28, g30 = group("2025"), group("2028_strategy"), group("2030_realistic")
    mix = ((g28 - g25) + (g30 - g25)) / 2 * 100
    print(f"Смесь форматов (X3 §2.4, класс C): 2025 {g25*100:.2f} %; структура стратегии-2028 {(g28-g25)*100:+.2f} п.п.; "
          f"реалистичная 2030 {(g30-g25)*100:+.2f} п.п.; центр (среднее двух) {mix:+.2f} п.п.")

    # ---------------- цели режимов: средний f режима класса на шкале X5 + смесь; стресс — среднее шкал f и п.п.
    f_reg = dict(fmean)
    f_reg["full"] = F_FULL_MAGNIT
    lt_f = {r: t0 + f_reg[r] * loss + mix for r in REGIMES}
    lt_pp = {r: (t0 + dmean[r] + mix if dmean[r] is not None else None) for r in REGIMES}
    lt_raw = dict(lt_f)
    lt_raw["stress"] = (lt_f["stress"] + lt_pp["stress"]) / 2
    lt = {r: round(lt_raw[r] * 20) / 20 for r in REGIMES}            # шаг 0,05 п.п.
    print("\nЦели режимов LT (шкала f — метод Магнита; стресс — среднее шкал f и п.п.; + смесь):")
    for r in REGIMES:
        alt = f"{lt_pp[r]:.2f}" if lt_pp[r] is not None else "—"
        print(f"  {RU[r]:<10} f {f_reg[r]:+.3f} → шкала f {lt_f[r]:.3f} %, шкала п.п. {alt} % → {lt_raw[r]:.3f} % → книга {lt[r]:.2f} %")
    print(f"  глубина стресса от LTM: {lt['stress'] - t0:+.2f} п.п. (книга Магнита: 4,0 − 4,83 = −0,83 п.п.)")

    # ---------------- центр E[m_LT]: две оценки класса и перекрёстные проверки
    rng = random.Random(BOOT_SEED)
    fs = [e["f"] for e in eps]
    ds = [e["d_pp"] for e in eps]
    bf, bd = [], []
    for _ in range(BOOT_N):
        idx = [rng.randrange(n) for _ in range(n)]
        bf.append(st.mean(fs[i] for i in idx))
        bd.append(st.mean(ds[i] for i in idx))
    ef, ed = st.mean(fs), st.mean(ds)
    se_f, se_d = st.pstdev(bf) * loss, st.pstdev(bd)
    est_a = t0 + ef * loss + mix
    est_b = t0 + ed + mix
    wa, wb = 1 / se_f ** 2, 1 / se_d ** 2
    center = (wa * est_a + wb * est_b) / (wa + wb)
    series = json.loads((HERE / "series_out.json").read_text(encoding="utf-8"))
    est_c = series["anchor"]["level_pp"] + mix
    lti_ltm = (h1["lti"] + h2["lti"]) / (h1["rev"] + h2["rev"]) * 100
    tinv = [a for a in ext["analysts"] if a["house"] == "Т-Инвестиции" and a["year"] == "2027–2028"][0]
    est_d = tinv["margin"] * 100 + lti_ltm
    sorted_f = sorted(bf)
    ci_f = (t0 + sorted_f[int(0.025 * BOOT_N)] * loss + mix, t0 + sorted_f[int(0.975 * BOOT_N)] * loss + mix)
    print(f"\nЦентр E[m_LT]:")
    print(f"  A. класс, шкала f: {t0:.2f} + ({ef:+.3f}) × {loss:.2f} {mix:+.2f} = {est_a:.3f} % (se {se_f:.3f}; бутстрэп 95 %: {ci_f[0]:.2f}–{ci_f[1]:.2f})")
    print(f"  B. класс, шкала п.п.: {t0:.2f} + ({ed:+.3f}) {mix:+.2f} = {est_b:.3f} % (se {se_d:.3f})")
    print(f"  точностно-взвешенный центр A и B: {center:.3f} %")
    print(f"  проверка C. уровень фильтра на якоре без изменения + смесь: {est_c:.3f} %")
    print(f"  проверка D. Т-Инвестиции 10.06.2026: отчётная 2027–2028 {tinv['margin']*100:.1f} % + LTI LTM {lti_ltm:.2f} = {est_d:.3f} % (скорр., класс B)")

    # ---------------- вероятности: Лаплас ½, наклон к центру
    p, lam = tilt(lap, lt, center)
    p_book = {r: round(p[r] * 200) / 200 for r in REGIMES}          # шаг 0,5 п.п.
    diff = round(1 - sum(p_book.values()), 10)
    p_book["floor"] = round(p_book["floor"] + diff, 4)
    e_book = sum(p_book[r] * lt[r] for r in REGIMES)
    sd_book = math.sqrt(sum(p_book[r] * (lt[r] - e_book) ** 2 for r in REGIMES))
    e_lap = sum(lap[r] * lt[r] for r in REGIMES)
    e_raw = sum(freq[r] * lt[r] for r in REGIMES)
    print(f"\nВероятности: Лаплас ½ даёт E = {e_lap:.3f} %, частоты класса как есть — {e_raw:.3f} %; наклон λ = {lam:+.3f}:")
    for r in REGIMES:
        print(f"  {RU[r]:<10} Лаплас ½ {lap[r]*100:5.1f} % → наклон {p[r]*100:5.2f} % → книга {p_book[r]*100:5.1f} %  × цель {lt[r]:.2f} %")
    print(f"  E[m_LT] книги {e_book:.3f} %, σ между режимами {sd_book:.3f} п.п.")

    # ---------------- сравнения и чувствительности
    x3 = {"lt": {"stress": 5.3, "floor": 5.9, "partial": 6.5, "full": 7.1}, "p": {"stress": 0.25, "floor": 0.40, "partial": 0.30, "full": 0.05}}
    e_x3 = sum(x3["p"][r] * x3["lt"][r] for r in REGIMES)
    sd_x3 = math.sqrt(sum(x3["p"][r] * (x3["lt"][r] - e_x3) ** 2 for r in REGIMES))
    z_x3 = (e_x3 - center) / math.sqrt(1 / (wa + wb))
    lo_s, hi_s = wilson(len(by["stress"]), n)
    print(f"\nПредложение X3 (5,3/5,9/6,5/7,1 %; 25/40/30/5 %): E {e_x3:.3f} %, σ {sd_x3:.3f}; выше центра на {e_x3-center:+.3f} п.п. "
          f"({z_x3:+.1f} se центра); стресс 25 % против класса {freq['stress']*100:.0f} % (Уилсон {lo_s*100:.0f}–{hi_s*100:.0f} %) — у нижнего края")
    emp = [e for e in raw["episodes"] if e["id"] == "empire_2014"][0]
    f_emp = (emp["A"][2] - emp["A"][1]) / (emp["peak"][1] - emp["A"][1])     # T0 правила A (FY17), исход FY21–23
    fs11 = fs + [f_emp]
    e11 = t0 + st.mean(fs11) * loss + mix
    print(f"Чувствительность: порог 1,25 п.п. (глубина X5) впускает Empire/Sobeys (падение 1,27; по правилу A f {f_emp:+.2f}, «полный»): "
          f"n = 11, E[f] {st.mean(fs11):+.3f} → {e11:.3f} %")
    rus = [e for e in eps if e["country"] == "Россия"]
    print(f"Подвыборка Россия (n = {len(rus)}): режимы {', '.join(RU[e['regime']] for e in rus)}; E[f] {st.mean(e['f'] for e in rus):+.3f} → "
          f"{t0 + st.mean(e['f'] for e in rus) * loss + mix:.3f} %")
    peers = ext["peers_2025"]
    print(f"Аналоги 2025: Лента {peers['lenta']['margin']*100:.2f} % (выше цели «полный» {lt['full']:.2f}); Магнит {peers['magnit']['margin']*100:.2f} % "
          f"(ниже цели «стресс» {lt['stress']:.2f}); форматы X5 2024: «Пятёрочка» {ext['x5_formats_2024']['pyaterochka_margin']*100:.1f} %, «Перекрёсток» {ext['x5_formats_2024']['perekrestok_margin']*100:.1f} %")
    plateau = st.mean(yrs[y]["m"] * 100 for y in range(2018, 2023))
    print(f"История X5: плато 2018–2022 {plateau:.2f} %, годовой минимум 2025 {yrs[2025]['m']*100:.2f} %, полугодовой минимум 2026H1 {halves['2026H1']['m']*100:.2f} %")

    # ---------------- ось полосы A-C0: неопределённость СРЕДНЕГО уровня (не разброс режимов)
    a, b, c = (g28 - g25) * 100, (g30 - g25) * 100, mix
    sd_mix = math.sqrt((a * a + b * b + c * c - a * b - a * c - b * c) / 18)          # треугольное (a, b, мода c)
    ests = [est_a, est_b, est_c, est_d]
    sd_est = st.stdev(ests)
    sd_param = math.sqrt(se_f ** 2 + sd_est ** 2 + sd_mix ** 2)
    ax_lo, ax_hi = -0.5, 0.4
    sd_axis = math.sqrt((ax_lo ** 2 + ax_hi ** 2) / 12 - ((ax_hi + ax_lo) / 6) ** 2)  # MODEL §9: |s| треугольное, края low/high
    print(f"\nОсь A-C0 (сдвиг LT всех режимов): se класса {se_f:.3f}, разброс оценок A–D {sd_est:.3f}, смесь форматов {sd_mix:.3f} "
          f"→ σ среднего {sd_param:.3f} п.п.; ось [{ax_lo}; +{ax_hi}] п.п. по правилу MODEL §9 даёт σ {sd_axis:.3f}, среднее {(ax_hi+ax_lo)/6:+.3f}")
    pc = ext["price_cap"]
    print(f"  σ оси ≈ σ среднего; асимметрия вниз: хвосты вниз — регуляторный потолок наценок (вероятность {pc['prob_5y']*100:.0f} % за 5 лет, "
          f"{pc['effect_pp'][0]:+.1f}…{pc['effect_pp'][1]:+.1f} п.п.; A-G1 X3) и структура стратегии-2028 ({a:+.2f}); вверх — реалистичная смесь 2030 ({b:+.2f}) "
          f"и класс с Empire ({e11-center:+.2f}); бутстрэп класса 95 %: {ci_f[0]-center:+.2f}…{ci_f[1]-center:+.2f} п.п. от центра")

    out = {"episodes": eps, "n": n, "freq": freq, "laplace": lap, "f_mean": fmean, "d_mean": dmean,
           "axis": {"sd_class": se_f, "sd_estimators": sd_est, "sd_mix": sd_mix, "sd_param": sd_param,
                    "low_pp": ax_lo, "high_pp": ax_hi, "sd_axis": sd_axis},
           "wilson": {r: wilson(len(by[r]), n) for r in REGIMES},
           "x5": {"peak_year": peak_year, "peak_pp": peak, "t0_ltm_pp": t0, "t0_rule_b_pp": t0_b, "loss_pp": loss},
           "mix_pp": mix, "mix_structures_pp": {"2028_strategy": (g28 - g25) * 100, "2030_realistic": (g30 - g25) * 100},
           "lt_f_scale_pp": lt_f, "lt_pp_scale_pp": lt_pp, "lt_raw_pp": lt_raw, "lt_pp": lt,
           "center": {"A_f_scale": est_a, "A_se": se_f, "A_ci95": ci_f, "B_pp_scale": est_b, "B_se": se_d,
                      "center_pp": center, "center_se": math.sqrt(1 / (wa + wb)),
                      "check_C_level": est_c, "check_D_tinvest": est_d},
           "prob_tilted": p, "lambda": lam, "prob_book": p_book, "e_lt_book_pp": e_book, "sd_lt_book_pp": sd_book,
           "e_lt_laplace_pp": e_lap, "e_lt_raw_pp": e_raw,
           "x3": {"e_pp": e_x3, "sd_pp": sd_x3, "z_vs_center": z_x3},
           "sens_empire_n11_pp": e11, "plateau_2018_2022_pp": plateau}
    write_json("refclass_out.json", out)
    print("\nЗаписано: refclass_out.json")


if __name__ == "__main__":
    main()
