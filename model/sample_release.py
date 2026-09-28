"""Образец выпуска для витрины: `python -B -m model.sample_release`.

Пишет `var/release/sample.json` — выпуск `x5-v1` на входах книги
(`meta.valuation_date`, `meta.market_price`) с полной полосой (не `fast`), и
печатает время сборки. Сработавшие гейты без объяснений образец не
блокируют (`strict=False`): причины, по которым боевая сборка не вышла бы,
печатаются в stderr.

    python -B -m model.sample_release [--live var/live/live.json] [--fast] [--out ФАЙЛ]

`--live` — живые входы из файла `indicators.live` (образец с ценой, плитками и
облигациями); число процессов полосы — `X5_WORKERS` (по умолчанию — ядра).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from model.book import ROOT
from model.payload import build_payload, compact_json
from model.uncertainty import close_pool, workers

DEFAULT_OUT = ROOT / "var" / "release" / "sample.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Образец выпуска X5 на входах книги")
    parser.add_argument("--live", type=Path, default=None, help="живые входы (JSON indicators.live)")
    parser.add_argument("--fast", action="store_true", help="полоса на 200 прогонах")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="куда писать")
    args = parser.parse_args(argv)
    live = json.loads(args.live.read_text(encoding="utf-8")) if args.live else None
    started = time.perf_counter()
    try:
        payload = build_payload(live=live, fast=args.fast, strict=False)
    finally:
        close_pool()
    elapsed = time.perf_counter() - started
    text = compact_json(payload)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text + "\n", encoding="utf-8", newline="\n")
    head = payload["headline"]
    print(f"записано: {args.out} · {len(text.encode('utf-8'))} байт · {head['draws']} прогонов · "
          f"медиана {head['printed_central']:.0f} ₽ (P10–P90 {head['printed_band'][0]:.0f}–"
          f"{head['printed_band'][1]:.0f}) · точка {payload['fair_value']['printed']['central']:.0f} ₽ · "
          f"сборка {elapsed:.1f} с, процессов {workers()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
