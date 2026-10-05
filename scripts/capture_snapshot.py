"""Capture the whole market at this moment (meant for 14:00 Beijing via Task Scheduler / cron).

    python scripts/capture_snapshot.py               # A-shares (+ money flow), HK, watchlist 1-min bars
    python scripts/capture_snapshot.py --no-hk --label 1430
    python scripts/capture_snapshot.py --force       # also on weekends / outside trading hours

Writes CSV files to data/snapshots/<YYYY-MM-DD>/. Exit code 0 on success.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stockrt import capture  # noqa: E402
from stockrt.calendar import now_bj, status  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--label", help="file prefix, default HHMM Beijing time")
    ap.add_argument("--no-hk", action="store_true", help="skip the Hong Kong snapshot")
    ap.add_argument("--no-flow", action="store_true", help="skip the market-wide money-flow ranking (~30 s)")
    ap.add_argument("--force", action="store_true", help="run even when the A-share market is closed")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    st = status("CN")
    if st.phase == "weekend" and not args.force:
        print(f"{now_bj():%Y-%m-%d %H:%M} Beijing is a weekend - nothing to capture (use --force to override).")
        return 0
    if not args.force:
        # Public holidays aren't in the calendar: if the index hasn't printed today, the market is shut.
        traded, last = capture.traded_today()
        if not traded:
            print(f"No A-share quotes printed today yet (last {last}): holiday or before the open - "
                  "skipping (use --force to override).")
            return 0
    files = capture.capture(label=args.label, include_flow=not args.no_flow, include_hk=not args.no_hk)
    for f in files:
        print(f"saved {f}  ({f.stat().st_size / 1024:,.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
