"""Download daily bars for a date range to CSV (long format + close-price matrix).

    python scripts/download_daily.py --codes 600519 000858 0700.HK --start 2024-01-01
    python scripts/download_daily.py --watchlist --start 2025-01-01 --zip
    python scripts/download_daily.py --group "Baijiu (white liquor)" --start 2020-01-01 --raw
    python scripts/download_daily.py --all-cn --start 2026-01-01 --out data/exports

Prices are ratio-adjusted (dividends and splits reinvested) unless --raw is given;
every file also carries close_raw and the daily total return `ret`.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stockrt import export, realtime, watchlist  # noqa: E402
from stockrt.config import DATA_DIR, STARTER_GROUPS  # noqa: E402
from stockrt.symbols import normalize  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--codes", nargs="+", help="symbols in any format: 600519, 600519.SH, 0700.HK, hk00700")
    src.add_argument("--watchlist", action="store_true", help="the app's saved watchlist")
    src.add_argument("--group", choices=list(STARTER_GROUPS), help="a starter theme group")
    src.add_argument("--all-cn", action="store_true", help="every listed A-share (~5,500)")
    src.add_argument("--all-hk", action="store_true", help="every listed Hong Kong stock (~2,800)")
    ap.add_argument("--start", type=date.fromisoformat, help="first date, YYYY-MM-DD (default: full history)")
    ap.add_argument("--end", type=date.fromisoformat, help="last date, YYYY-MM-DD (default: latest)")
    ap.add_argument("--raw", action="store_true", help="unadjusted prices (as traded)")
    ap.add_argument("--zip", action="store_true", help="also write a ZIP bundle with a README")
    ap.add_argument("--out", type=Path, default=DATA_DIR / "exports", help="output folder")
    args = ap.parse_args()

    if args.codes:
        codes = [normalize(c) for c in args.codes]
    elif args.watchlist:
        codes = watchlist.load()
    elif args.group:
        codes = STARTER_GROUPS[args.group]
    else:
        codes = realtime.universe("CN" if args.all_cn else "HK")["code"].tolist()
    names = {}
    for mkt in ("CN", "HK"):
        try:
            u = realtime.universe(mkt)
            names.update(dict(zip(u["code"], u["name"])))
        except Exception:  # noqa: BLE001
            pass

    bulk = len(codes) > 60
    print(f"{len(codes):,} symbols, {args.start or 'first listing'} -> {args.end or 'latest'}, "
          f"{'raw' if args.raw else 'ratio-adjusted'} prices{' (bulk mode)' if bulk else ''}")
    frames, errors, t0 = [], [], time.time()
    for i in range(0, len(codes), 50):
        chunk = codes[i:i + 50]
        d, e = export.daily_dataset(chunk, names, args.start, args.end, adjusted=not args.raw, bulk=bulk, workers=8)
        frames.append(d)
        errors += e
        done = min(i + 50, len(codes))
        print(f"  {done:,}/{len(codes):,} symbols  {time.time() - t0:,.0f}s", flush=True)
    import pandas as pd

    data = pd.concat([f for f in frames if not f.empty], ignore_index=True)
    args.out.mkdir(parents=True, exist_ok=True)
    tag = export.stamp()
    long_path = args.out / f"daily_long_{tag}.csv"
    wide_path = args.out / f"daily_closes_wide_{tag}.csv"
    long_path.write_bytes(export.to_csv_bytes(data))
    wide_path.write_bytes(export.to_csv_bytes(export.wide_closes(data)))
    print(f"wrote {long_path} ({len(data):,} rows)\nwrote {wide_path}")
    if args.zip:
        zp = args.out / f"daily_bundle_{tag}.zip"
        zp.write_bytes(export.zip_bytes({"daily_long.csv": long_path.read_bytes(),
                                         "daily_closes_wide.csv": wide_path.read_bytes(),
                                         "README.txt": export.DAILY_README.encode("utf-8")}))
        print(f"wrote {zp}")
    if errors:
        print(f"{len(errors)} symbols failed:", *errors[:20], sep="\n  ")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
