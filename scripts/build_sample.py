"""Build (or refresh) the built-in sample dataset in sample_data/ from live sources.

    python scripts/build_sample.py                 # ~5-8 minutes (one request per A-share for recent bars)
    python scripts/build_sample.py --years 5 --recent-days 60

Contents: one full-market quote snapshot (A-shares + HK) with main-fund flow; daily bars
(N years) for the starter universe, index ETFs and indices; the last ~2 months of daily bars
for every other A-share (so the screener's moving-average rules work offline); five sessions of
1-minute data, adjustment factors, ETF fees and announcements for the starter universe.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from stockrt import http, realtime  # noqa: E402
from stockrt.calendar import now_bj  # noqa: E402
from stockrt.config import BENCHMARKS, DEFAULT_WATCHLIST, INDEX_NAMES, STARTER_GROUPS  # noqa: E402
from stockrt.etfs import ETFS  # noqa: E402
from stockrt.sample import SAMPLE_DIR  # noqa: E402
from stockrt.sources import eastmoney, sina, tencent  # noqa: E402
from stockrt.symbols import is_fund, is_index, market  # noqa: E402


def collect(fn, codes, label, workers=8):
    t0 = time.time()
    out, errors = {}, []
    for code, res in zip(codes, http.pmap(fn, codes, workers=workers)):
        if isinstance(res, Exception):
            errors.append(f"{code}: {res}")
        else:
            out[code] = res
    print(f"  {label}: {len(out):,}/{len(codes):,} ok in {time.time() - t0:,.0f}s"
          + (f" ({len(errors)} failed, e.g. {errors[0][:90]})" if errors else ""), flush=True)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--years", type=int, default=10, help="daily history for the starter universe")
    ap.add_argument("--recent-days", type=int, default=90, help="calendar days of bars for every other A-share")
    args = ap.parse_args()

    core = list(dict.fromkeys(
        DEFAULT_WATCHLIST + [c for g in STARTER_GROUPS.values() for c in g] + [e["code"] for e in ETFS]
        + [e["index"] for e in ETFS if e["index"]] + list(BENCHMARKS.values()) + list(INDEX_NAMES)))
    print(f"Starter universe: {len(core)} symbols")
    tmp = SAMPLE_DIR.with_name("sample_data.tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)

    print("Universe and quotes")
    cn_u, hk_u = realtime.universe("CN", refresh=True), realtime.universe("HK", refresh=True)
    universe = pd.concat([cn_u.assign(market="CN"), hk_u.assign(market="HK")], ignore_index=True)
    snap_cn, snap_hk = realtime.market_snapshot("CN"), realtime.market_snapshot("HK")
    extra = tencent.quotes(core)
    quotes = pd.concat([snap_cn, snap_hk, extra], ignore_index=True).drop_duplicates("code")
    quotes = quotes.reindex(columns=tencent.QUOTE_COLUMNS)
    print(f"  {len(quotes):,} quotes")

    print("Daily bars")
    today = now_bj().date()
    full = collect(lambda c: tencent.daily(c, start=today - timedelta(days=365 * args.years)), core,
                   f"{args.years}-year history")
    others = [c for c in cn_u["code"] if c not in full]
    recent = collect(lambda c: tencent.daily(c, start=today - timedelta(days=args.recent_days)), others,
                     f"last {args.recent_days} days, other A-shares", workers=10)
    daily = pd.concat([df.assign(code=c) for c, df in {**full, **recent}.items() if not df.empty], ignore_index=True)
    daily = daily.sort_values(["code", "date"]).reset_index(drop=True)

    print("Intraday, flow, factors, fees, announcements")
    minutes = collect(tencent.minute_5day, core, "5-session minutes")
    minutes_df = pd.concat([m.assign(code=c) for c, m in minutes.items() if not m.empty], ignore_index=True)
    flow = sina.money_flow_rank()
    print(f"  money flow: {len(flow):,} rows")
    factors = collect(sina.factor_segments, [c for c in core if not is_index(c)], "adjustment factors")
    fees = collect(eastmoney.fund_fees, [c for c in core if is_fund(c)], "ETF fees")
    stocks = [c for c in core if not is_index(c) and not is_fund(c) and not (market(c) == "HK" and c in
                                                                             [e["code"] for e in ETFS])]
    anns = collect(lambda c: eastmoney.announcements(c, 20), stocks, "announcements")
    anns_df = pd.concat([a.assign(code=c) for c, a in anns.items() if not a.empty], ignore_index=True)

    print("Writing files")
    universe.to_parquet(tmp / "universe.parquet", index=False, compression="zstd")
    quotes.to_parquet(tmp / "quotes.parquet", index=False, compression="zstd")
    daily.to_parquet(tmp / "daily.parquet", index=False, compression="zstd", row_group_size=20_000)
    minutes_df.to_parquet(tmp / "minutes.parquet", index=False, compression="zstd")
    flow.to_parquet(tmp / "flow.parquet", index=False, compression="zstd")
    anns_df.to_parquet(tmp / "announcements.parquet", index=False, compression="zstd")
    (tmp / "factors.json").write_text(json.dumps(
        {c: [(d.isoformat(), a, b) for d, a, b in s] for c, s in factors.items() if s}), encoding="utf-8")
    (tmp / "fees.json").write_text(json.dumps(fees, ensure_ascii=False, indent=1), encoding="utf-8")

    q = quotes.set_index("code")["time"]
    meta = {
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "as_of": str(pd.Timestamp(q.get("sh000300")).date()),
        "as_of_hk": str(pd.Timestamp(q.get("hkHSI")).date()),
        "n_quotes": int(len(quotes)),
        "n_full_history": len(full),
        "history_years": args.years,
        "recent_sessions": int(daily[daily["code"].isin(recent)].groupby("code").size().median()) if recent else 0,
        "starter_symbols": core,
        "sources": "Tencent quotes/minutes/daily, Sina universe/factors/money flow, East Money fees/announcements",
    }
    (tmp / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    (tmp / "README.md").write_text(
        "# Built-in sample dataset\n\n"
        f"Market data as of {meta['as_of']} (A-shares) / {meta['as_of_hk']} (HK), built {meta['built_at'][:10]} "
        "with `python scripts/build_sample.py`.\n\n"
        "| File | Contents |\n|---|---|\n"
        "| quotes.parquet | one quote per listed A-share / HK stock, ETF and index (that session's close) |\n"
        f"| daily.parquet | raw daily bars: {meta['history_years']} years for the starter universe, "
        f"~{meta['recent_sessions']} sessions for every other A-share |\n"
        "| minutes.parquet | 1-minute series, last 5 sessions, starter universe |\n"
        "| flow.parquet | main-fund net inflow per A-share for that session |\n"
        "| universe.parquet | every listed code and name |\n"
        "| factors.json, fees.json, announcements.parquet | adjustment factors, ETF fees, recent announcements |\n\n"
        "Prices and flows come from Tencent, Sina and East Money public web endpoints and remain theirs; this "
        "small sample is included for demonstration and testing only.\n", encoding="utf-8")

    shutil.rmtree(SAMPLE_DIR, ignore_errors=True)
    tmp.rename(SAMPLE_DIR)
    total = sum(f.stat().st_size for f in SAMPLE_DIR.iterdir()) / 1e6
    for f in sorted(SAMPLE_DIR.iterdir()):
        print(f"  {f.name:24} {f.stat().st_size / 1e6:6.2f} MB")
    print(f"Done: {SAMPLE_DIR} ({total:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
