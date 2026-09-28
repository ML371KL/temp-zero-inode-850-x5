"""(2) Ближние траектории целей режимов 2026H2–2030, цель на якоре 2026H1, согласование с фактом и прогнозом «6+ %».

Правило траектории: общая цель на якоре T_a (уровень фильтра series.py на 2026H1), далее линейный сход к цели LT
режима к 2031 году: цель_r(p) = T_a + (LT_r − T_a) × w(p), w = h/10, h — полугодий от якоря; у ключей-годов
(2027…2030) — w середины года (оба полугодия года читают ключ года, MODEL §0.1). LT_from = 2031 — горизонт исхода
класса (T0+4…T0+6 от T0 = LTM 2К2026).

Информационная граница: разброс целей между режимами на горизонте h (σ по вероятностям книги) не должен
превышать разброс самого уровня маржи X5 через h полугодий, который допускает история (случайное блуждание
уровня с шагом q из series.py: q·√h). Линейный сход её выполняет с запасом; «быстрый» вариант, идущий по
границе, — для сравнения в ap2u_history.py.

Запуск: python -B paths.py → paths_out.json, печать. Нужны series_out.json и refclass_out.json.
"""
from __future__ import annotations

import json
import math

from common import HERE, REGIMES, history, path_value, pct, write_json

KEYS = [("2026H2", 1.0), ("2027", 2.5), ("2028", 4.5), ("2029", 6.5), ("2030", 8.5)]   # ключ книги → h (середина)
LT_FROM = 2031
H_LT = 10.0
RHO_BOOK = -0.40          # A-C4: margin.deviation_persistence (оценка series.py, округлена до 0,05)


def load(name):
    return json.loads((HERE / name).read_text(encoding="utf-8"))


def build(t_a: float, lt: dict, w_of_h) -> dict:
    """Траектории книги (доли выручки, 4 знака) по правилу w(h)."""
    out = {}
    for r in REGIMES:
        path = {"2026H1": round(t_a, 4)}
        for key, h in KEYS:
            path[key] = round(t_a + (lt[r] - t_a) * w_of_h(h), 4)
        path["LT"] = round(lt[r], 4)
        path["LT_from"] = LT_FROM
        out[r] = path
    return out


def main():
    ser, rc = load("series_out.json"), load("refclass_out.json")
    H = history()
    halves = H["halves"]
    t_a = round(ser["anchor"]["level_pp"] / 100, 4)
    lt = {r: rc["lt_pp"][r] / 100 for r in REGIMES}
    p = rc["prob_book"]
    q = ser["base"]["q_pp"] / 100
    fact = halves["2026H1"]["m"]
    dev0 = fact - t_a
    print(f"Якорь 2026H1: факт {pct(fact, 3)} %, цель на якоре T_a = уровень фильтра {pct(ser['anchor']['level_pp']/100, 3)} % "
          f"(± {ser['anchor']['level_sd_pp']:.2f} п.п.) → книга {pct(t_a)} %; отклонение якоря {dev0*100:+.3f} п.п.")

    targets = build(t_a, lt, lambda h: min(1.0, h / H_LT))
    sd_lt = math.sqrt(sum(p[r] * (lt[r] - sum(p[x] * lt[x] for x in REGIMES)) ** 2 for r in REGIMES))
    print("\nЦели режимов, % (линейный сход к LT в 2031):")
    print("  " + f"{'ключ':<8}" + "".join(f"{r:>9}" for r in REGIMES) + f"{'E':>9}{'σ режимов':>11}{'граница q√h':>13}{'h':>6}")
    rows = []
    for key, h in [("2026H1", 0.0)] + KEYS + [("LT", H_LT)]:
        vals = {r: (targets[r][key] if key in targets[r] else None) for r in REGIMES}
        e = sum(p[r] * vals[r] for r in REGIMES)
        sd = math.sqrt(sum(p[r] * (vals[r] - e) ** 2 for r in REGIMES))
        bound = q * math.sqrt(h)
        rows.append({"key": key, "h": h, "values": vals, "e": e, "sd": sd, "bound": bound, "ok": sd <= bound + 1e-12})
        print("  " + f"{key:<8}" + "".join(f"{vals[r]*100:>9.2f}" for r in REGIMES)
              + f"{e*100:>9.3f}{sd*100:>11.3f}{bound*100:>13.3f}{h:>6.1f}" + ("" if sd <= bound + 1e-12 else "  ← выше границы"))

    # ожидание маржи по полугодиям с хвостом отклонения якоря (MODEL §4.3, сезон 0)
    print(f"\nОжидаемая маржа (Σ P·цель + ρ^k·отклонение якоря, ρ = {RHO_BOOK}):")
    exp_half = {}
    for i, per in enumerate(["2026H2", "2027H1", "2027H2", "2028H1", "2028H2", "2029H1", "2029H2", "2030H1", "2030H2", "2031H1"]):
        k = i + 1
        e_t = sum(p[r] * path_value(targets[r], per) for r in REGIMES)
        tail = RHO_BOOK ** k * dev0
        exp_half[per] = e_t + tail
        print(f"  {per}: цель {e_t*100:.3f} %, хвост {tail*100:+.3f} п.п. → {exp_half[per]*100:.3f} %")

    # 2026 год против прогноза компании
    r_h1 = halves["2026H1"]["rev"]
    e_h1 = halves["2026H1"]["ebitda"]
    g_h1 = r_h1 / halves["2025H1"]["rev"] - 1
    print(f"\n2026 год: 1П факт {pct(fact)} % при росте выручки {g_h1*100:.1f} % г/г.")
    checks = {}
    for g in (g_h1, 0.12, 0.134):
        r_h2 = halves["2025H2"]["rev"] * (1 + g)
        m_year = (e_h1 + exp_half["2026H2"] * r_h2) / (r_h1 + r_h2)
        need = (0.06 * (r_h1 + r_h2) - e_h1) / r_h2
        checks[f"{g:.3f}"] = {"rev_h2": r_h2, "m_year": m_year, "h2_needed_for_6pct": need}
        print(f"  рост 2П {g*100:4.1f} %: ожидание года {pct(m_year, 3)} %; для «6 %» 2П нужно {pct(need, 3)} % (ожидание 2П {pct(exp_half['2026H2'], 3)} %)")
    print(f"  прошлые 2П: 2023 {pct(halves['2023H2']['m'])}, 2024 {pct(halves['2024H2']['m'])}, 2025 {pct(halves['2025H2']['m'])} %")

    # разложение ближнего пути на 2027–2028 против оценки Т-Инвестиций
    e27 = (exp_half["2027H1"] + exp_half["2027H2"]) / 2
    e28 = (exp_half["2028H1"] + exp_half["2028H2"]) / 2
    print(f"\nОжидание 2027 {pct(e27, 3)} %, 2028 {pct(e28, 3)} % (Т-Инвестиции 10.06.2026: 5,8 % отчётной ≈ 6,0 % скорр. на 2027–2028; refclass_out проверка D)")

    # «быстрая» траектория по информационной границе (для сравнения)
    w_fast = lambda h: min(1.0, q * math.sqrt(h) / sd_lt) if h > 0 else 0.0
    fast = build(t_a, lt, w_fast)
    print(f"\nДля сравнения — траектории по границе (w = min(1, q√h / σ_LT), σ_LT {sd_lt*100:.2f} п.п.): "
          + "; ".join(f"{r} 2026H2 {fast[r]['2026H2']*100:.2f}, 2027 {fast[r]['2027']*100:.2f}" for r in REGIMES))

    write_json("paths_out.json", {"t_a": t_a, "dev_anchor": dev0, "rho": RHO_BOOK, "targets": targets, "rows": rows,
                                  "expected_half": exp_half, "year_2026": checks, "expected_2027": e27, "expected_2028": e28,
                                  "fast_targets": fast, "sd_lt": sd_lt, "q": q, "prob": p})
    print("\nЗаписано: paths_out.json")


if __name__ == "__main__":
    main()
