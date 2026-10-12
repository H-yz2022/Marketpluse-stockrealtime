"""Strategy lab from the command line: backtest, daily live record, validation.

    python scripts/strategy_lab.py fetch              # one year of 30-minute + daily bars, all A-shares (~15 min)
    python scripts/strategy_lab.py backtest           # score every strategy on the fetched year
    python scripts/strategy_lab.py validate --date 2026-10-08
    python scripts/strategy_lab.py books --sessions 30   # volume books for the last N sessions
    python scripts/strategy_lab.py update             # record new sessions live + fill pending exits

`fetch` keeps its download in data/lab/ (not in git); `backtest`, `books` and `update` write the
small result files the website reads into results/lab/ and results/cn/volumes/.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from stockrt import lab, labdata  # noqa: E402
from stockrt.config import DATA_DIR  # noqa: E402

WORK = DATA_DIR / "lab"


def _progress(t0: float):
    last = [0.0]

    def p(f: float, msg: str) -> None:
        if f - last[0] >= 0.02 or f >= 1:
            last[0] = f
            print(f"  {msg}  {time.time() - t0:,.0f}s", flush=True)

    return p


def cmd_fetch(args) -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    codes, names = lab.universe_codes()
    if args.limit:
        codes = codes[: args.limit]
    t0 = time.time()
    frame, idx, cal = labdata.build(codes, names, args.start, progress=_progress(t0), workers=args.workers)
    frame.to_parquet(WORK / "frame.parquet", index=False, compression="zstd")
    pd.concat([f.assign(index_code=c) for c, f in idx.items()], ignore_index=True).to_parquet(
        WORK / "index.parquet", index=False)
    pd.Series(cal, name="day").to_frame().to_parquet(WORK / "calendar.parquet", index=False)
    print(f"{frame['code'].nunique():,} stocks, {len(frame):,} stock-sessions, {len(cal)} sessions "
          f"({cal[0]:%Y-%m-%d} to {cal[-1]:%Y-%m-%d}) in {time.time() - t0:,.0f}s -> {WORK}")
    return 0


def _load():
    frame = pd.read_parquet(WORK / "frame.parquet")
    ix = pd.read_parquet(WORK / "index.parquet")
    idx = {c: g.drop(columns="index_code").reset_index(drop=True) for c, g in ix.groupby("index_code")}
    return frame, idx


def cmd_backtest(args) -> int:
    frame, idx = _load()
    t0 = time.time()
    out = lab.run_backtest(frame, idx, end=args.end)
    print(f"backtest: {len(out['trades']):,} trades over {out['meta']['sessions']} sessions in "
          f"{time.time() - t0:,.0f}s")
    print(lab.scorecard(out["trades"], out["days"]).to_string())
    return 0


def cmd_validate(args) -> int:
    frame, idx = _load()
    print(lab.validate(frame, idx, args.date))
    return 0


def cmd_books(args) -> int:
    frame, _ = _load()
    for p in lab.write_books(frame, sessions=args.sessions, end=args.end):
        print("saved", p)
    return 0


def cmd_update(args) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    t0 = time.time()
    res = lab.update(progress=_progress(t0), workers=args.workers)
    print(res)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--start", type=date.fromisoformat, default=date(2025, 10, 13))
    f.add_argument("--workers", type=int, default=16)
    f.add_argument("--limit", type=int, default=0, help="first N stocks only (testing)")
    b = sub.add_parser("backtest")
    b.add_argument("--end", type=date.fromisoformat, default=lab.BACKTEST_END)
    v = sub.add_parser("validate")
    v.add_argument("--date", type=date.fromisoformat, default=date(2026, 10, 8))
    k = sub.add_parser("books")
    k.add_argument("--sessions", type=int, default=30)
    k.add_argument("--end", type=date.fromisoformat, default=lab.BACKTEST_END)
    u = sub.add_parser("update")
    u.add_argument("--workers", type=int, default=16)
    args = ap.parse_args()
    return {"fetch": cmd_fetch, "backtest": cmd_backtest, "validate": cmd_validate, "books": cmd_books,
            "update": cmd_update}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
