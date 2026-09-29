"""Полоса §9 и обратный DCF §11 на книге assumptions.yaml (канон; ядро model/uncertainty.py).

Печатает медиану и полосы, P(центр < рынка), вклад осей (доля квадрата ранговой корреляции) и обратный DCF по осям
итогового списка — проверка, что оси и отрезки поиска книги работают. Факты — data/facts. Дата оценки и цена — книги
(meta.valuation_date, meta.market_price), как в results.json. Выход: band_check_out.json, band_check.out.
"""
from __future__ import annotations

import sys
import warnings

from book_merge import build_book
from common import REPO, Report, r4, write_json

sys.path.insert(0, str(REPO))
warnings.simplefilter("ignore")
from model.facts import core_facts, load_facts  # noqa: E402
from model.uncertainty import Subsample, band, median, reverse_dcf  # noqa: E402


def main() -> None:
    R = Report("band_check")
    A, used = build_book()
    F = load_facts(REPO / "data" / "facts")
    CF = core_facts(F, A)
    B = band(A, CF)
    st = B.stats()
    R.h(f"Полоса: {B.n} прогонов, зерно {B.seed}, осей {len(B.axes)}; книга {', '.join(used)} {A['meta']['version']}")
    R("  " + ", ".join((f"{k} {v:.4f}" if k == "p_below" else f"{k} {v:.1f}") if isinstance(v, float) else f"{k} {v}"
                       for k, v in st.items()))
    contrib = B.contributions()
    R.h("Вклад осей (доля квадрата ранговой корреляции с центром)")
    for c in sorted(contrib, key=lambda c: -c["share"]):
        R(f"  {c['share']:.4f}  {c['axis']}")
    full = median(B.center)
    sub = Subsample(B, full, CF, size=A["valuation"]["reverse_dcf"]["subsample"],
                    valuation_date=None, market_price=None)
    rev = reverse_dcf(A, CF, sub)
    R.h("Обратный DCF (медиана подвыборки 200 + сдвиг; для справки — точка)")
    for r in rev:
        s = "недостижимо" if r["solved"] is None else f"{r['solved']:.4f}"
        p = "недостижимо" if r["point_solved"] is None else f"{r['point_solved']:.4f}"
        R(f"  {r['name']}: книга {r['book']:.4f} → медиана {s} ({'в диапазоне' if r['in_range'] else 'вне'} "
          f"{r['range']}); точка {p}")
    write_json("band_check_out.json", {"book": used, "version": str(A["meta"]["version"]), "stats": {k: (r4(v) if isinstance(v, float) else v)
                                                                   for k, v in st.items()},
                                       "contributions": [{"name": c["axis"], "share": r4(c["share"])} for c in contrib],
                                       "reverse_dcf": [{k: (r4(v) if isinstance(v, float) else v) for k, v in r.items()
                                                        if k in ("name", "book", "solved", "in_range", "point_solved")}
                                                       for r in rev]})
    R.save()


if __name__ == "__main__":
    main()
