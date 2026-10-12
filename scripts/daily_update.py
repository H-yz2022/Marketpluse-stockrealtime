"""After the close: archive the session for the website (run by GitHub Actions on weekdays).

    python scripts/daily_update.py
    python scripts/daily_update.py --backfill 2026-10-09     # just rebuild one missed session

1. Saves today's end-of-day volume book (every A-share, from one whole-market snapshot) - "yesterday"
   for tomorrow's auction and volume queries.
2. Archives the front page (尾盘选股) for A-shares and Hong Kong, so "Past sessions" stays complete.
3. Updates the Strategy lab: records today's picks for every strategy (with the live-only checks:
   main-fund flow and auction volume) and fills in exit prices of earlier picks.
4. Rebuilds any of the last 4 sessions missing from the front page's past sessions (a day the job
   didn't run): the minute series reaches five sessions back.
5. Keeps the newest 30 volume books (the queries need only the last few).

Everything lands in results/, which the workflow commits; Render redeploys with it. Skips holidays
and weekends unless --force. Each step is independent: one failing doesn't stop the others.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
import traceback
from datetime import date
from datetime import time as dtime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stockrt import archive, checkpoint, lab, realtime, tradingdays  # noqa: E402
from stockrt.calendar import now_bj  # noqa: E402

log = logging.getLogger("daily_update")


def step(name: str, fn) -> bool:
    t0 = time.time()
    print(f"== {name}", flush=True)
    try:
        out = fn()
        print(f"   ok in {time.time() - t0:,.0f}s: {out}", flush=True)
        return True
    except Exception:  # noqa: BLE001 - keep going, report at the end
        traceback.print_exc()
        print(f"   FAILED after {time.time() - t0:,.0f}s", flush=True)
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true", help="run on a non-trading day too")
    ap.add_argument("--skip-front-page", action="store_true")
    ap.add_argument("--workers", type=int, default=24)
    ap.add_argument("--backfill", type=date.fromisoformat, metavar="YYYY-MM-DD",
                    help="only rebuild this past A-share session for the front page (one of the last five)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.backfill:
        res = checkpoint.backfill(args.backfill)
        print(f"{args.backfill}: checkpoints {list(res['frames'])}; " + " ".join(res["notes"]))
        return 0 if res["frames"] else 1
    now = now_bj()
    if not args.force and tradingdays.is_trading_day("CN", now.date()) is False:
        print(f"{now:%Y-%m-%d} is not an A-share trading day - nothing to do.")
        return 0
    ok = True
    snap = {}

    def book():
        snap["cn"] = realtime.market_snapshot("CN")
        return archive.save_volumes_from_snapshot("CN", snap["cn"]) or "already saved"

    ok &= step("Volume book", book)
    if not args.skip_front_page:
        def front(mkt):
            def run():
                s = snap.get(mkt.lower()) if mkt == "CN" else None
                s = s if s is not None else realtime.market_snapshot(mkt)
                res = checkpoint.evaluate(s, spec=checkpoint.SPECS[mkt])
                return f"{res['day']} checkpoints {list(res['frames'])}"
            return run

        ok &= step("Front page, A-shares", front("CN"))
        if now.time() >= dtime(16, 12):  # Hong Kong's closing auction ends 16:10
            ok &= step("Front page, Hong Kong", front("HK"))
    ok &= step("Strategy lab", lambda: lab.update(workers=args.workers))
    if not args.skip_front_page:
        ok &= step("Catch up missed sessions (front page)",
                   lambda: [str(d) for d in checkpoint.catch_up()] or "none missing")
    ok &= step("Prune old volume books", lambda: f"{len(archive.prune_volumes('CN', keep=30))} removed")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
