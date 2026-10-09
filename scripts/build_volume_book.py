"""Build the end-of-day volume book for one past session (every A-share's volume, value, close, change).

    python scripts/build_volume_book.py --date 2026-10-08

The opening-auction queries compare today with the previous session ("volume today >= 5x
yesterday's"). Quote feeds only carry today's numbers, so each session's closing volumes are
kept in results/cn/volumes/<date>.parquet. The app saves them automatically from any snapshot
taken after the close; this script backfills a session that was missed (one request per stock).
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from stockrt import archive, http, realtime  # noqa: E402
from stockrt.sources import tencent  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", type=date.fromisoformat, required=True, help="session date, YYYY-MM-DD")
    args = ap.parse_args()
    day = args.date
    u = realtime.universe("CN")
    names = dict(zip(u["code"], u["name"]))
    codes = u["code"].tolist()
    t0 = time.time()

    def one(code: str) -> dict | None:
        bars = tencent.daily(code, start=day - timedelta(days=15), end=day)
        bars = bars[bars["date"] <= pd.Timestamp(day)]
        if bars.empty or bars["date"].iloc[-1] != pd.Timestamp(day):
            return None  # suspended that day
        last = bars.iloc[-1]
        prev = float(bars["close"].iloc[-2]) if len(bars) > 1 else None
        return {"code": code, "name": names.get(code, code), "volume": float(last["volume"]),
                "amount": float(last["amount"]) if pd.notna(last["amount"]) else None,
                "close": float(last["close"]), "prev_close": prev,
                "pct_change": 100 * (float(last["close"]) / prev - 1) if prev else None}

    rows = []
    for i in range(0, len(codes), 200):
        rows += [r for r in http.pmap(one, codes[i:i + 200], workers=12) if isinstance(r, dict)]
        print(f"  {min(i + 200, len(codes)):,}/{len(codes):,}  {time.time() - t0:,.0f}s", flush=True)
    book = pd.DataFrame(rows)
    path = archive.save_volumes("CN", day, book)
    print(f"saved {len(book):,} stocks -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
