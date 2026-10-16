"""After the close: compute, save and publish the session (run by GitHub Actions on weekdays).

    python scripts/daily_update.py                       # what the scheduled job runs
    python scripts/daily_update.py --backfill 2026-10-09  # just rebuild one missed session

Every step checks what is already saved, so running it twice is harmless and fast. The workflow runs
it several times each afternoon (GitHub sometimes skips a scheduled run); whichever run comes first
does the work.

1. Volume book: every A-share's closing volume, from one whole-market snapshot - "yesterday" for the
   next session's auction and volume queries.
2. Front page (尾盘选股): today's A-share session at 14:00 / 14:30 / 15:00, and Hong Kong's once it
   has closed (16:10 Beijing).
3. Strategy lab: today's picks for every strategy, with the live-only checks (main-fund flow, auction
   volume), and the exits of earlier picks.
4. Catch-up: any of the last 4 sessions missing from the front page is rebuilt (the minute series
   reaches five sessions back); a session rebuilt with Sina's money flow is redone once East
   Money answers again.
5. 收盘选股, the after-close screen (from 16:00 Beijing): 成交量翻倍，突破压力位，换手率＞3%，站上牛熊线的非ST股票.
6. Rolling window: only the newest 4 sessions and 5 volume books are kept, so the data never piles up
   (the Strategy lab's log is kept whole: it is the test itself, and grows by a few MB a year).
7. job_log.json (what each run did, including errors) and manifest.json, which tells the website that new
   results are published.

Each step is independent: one failing doesn't stop the others (the exit code reports it).
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

from stockrt import archive, checkpoint, closescreen, lab, realtime  # noqa: E402
from stockrt.calendar import now_bj  # noqa: E402

LOG: list[dict] = []   # written to results/job_log.json, so failures are visible without GitHub access


def step(name: str, fn) -> bool:
    t0 = time.time()
    print(f"== {name}", flush=True)
    try:
        out = fn()
        print(f"   ok in {time.time() - t0:,.0f}s: {out}", flush=True)
        LOG.append({"step": name, "ok": True, "seconds": round(time.time() - t0), "result": str(out)[:300]})
        return True
    except Exception as e:  # noqa: BLE001 - keep going, report at the end
        traceback.print_exc()
        print(f"   FAILED after {time.time() - t0:,.0f}s", flush=True)
        tb = traceback.format_exc().strip().splitlines()
        LOG.append({"step": name, "ok": False, "seconds": round(time.time() - t0),
                    "error": f"{type(e).__name__}: {str(e)[:300]}", "where": " | ".join(tb[-4:])[:600]})
        return False


def close_screens(now) -> str:
    """收盘选股 for every recent session that has a volume book and no list yet - today's from 16:00."""
    done = []
    for day in archive.sessions("CN")[:archive.KEEP_SESSIONS]:
        if day in archive.screen_days():
            continue
        if day == now.date() and now.time() < dtime(16, 0):
            continue
        if day not in archive.volume_days("CN"):
            continue
        res = closescreen.run(day)
        done.append(f"{day}: {int(closescreen.apply(res['frame']).passes.sum()) if not res['frame'].empty else 0} "
                    f"of {res['shortlisted']}")
    return "; ".join(done) or "up to date"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true", help="run even before today's close")
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
    session = checkpoint.latest_session_day("CN")
    if not args.force and session == now.date() and now.time() < dtime(15, 2):
        print(f"{now:%Y-%m-%d %H:%M} Beijing: today's A-share session hasn't closed yet - nothing to do.")
        return 0
    ok = True
    snap = {}

    def cn_snapshot():
        if "cn" not in snap:
            snap["cn"] = realtime.market_snapshot("CN")
        return snap["cn"]

    ok &= step("Volume book", lambda: archive.save_volumes_from_snapshot("CN", cn_snapshot()) or "already saved")

    def front(mkt: str):
        spec = checkpoint.SPECS[mkt]
        s = cn_snapshot() if mkt == "CN" else realtime.market_snapshot(mkt)
        day = checkpoint.session_day(s)
        if day in archive.sessions(mkt):
            return f"{day} already saved"
        res = checkpoint.evaluate(s, spec=spec)
        if not res["frames"]:  # nothing rebuilt: say why in the job log instead of passing silently
            raise RuntimeError(f"{res['day']}: " + ("; ".join(res["notes"]) or f"pending {res.get('pending')}"))
        return f"{res['day']} checkpoints {list(res['frames'])}"

    ok &= step("Front page, A-shares", lambda: front("CN"))
    hk_session = checkpoint.latest_session_day("HK")
    if hk_session < now.date() or now.time() >= dtime(16, 12):  # Hong Kong's closing auction ends 16:10
        ok &= step("Front page, Hong Kong", lambda: front("HK"))
    ok &= step("Strategy lab", lambda: lab.update(workers=args.workers))
    ok &= step("Catch up missed sessions", lambda: [str(d) for d in checkpoint.catch_up()] or "none missing")
    ok &= step("After-close screen (16:00)", lambda: close_screens(now_bj()))
    ok &= step("Rolling window", lambda: f"{len(archive.prune_sessions('CN')) + len(archive.prune_sessions('HK'))} "
                                         f"session files, {len(archive.prune_volumes('CN'))} volume books and "
                                         f"{len(archive.prune_screens())} screen files removed")
    archive.write_job_log(LOG, now_bj())
    ok &= step("Manifest", lambda: archive.write_manifest(now_bj()))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
