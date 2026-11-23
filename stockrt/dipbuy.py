"""低位吸筹 - buy the dip without catching a falling knife.

    低位吸筹（抄底不接飞刀）：近5个交易日跌幅超过10%，今日主力资金净流入超过3000万元，现价站上60日均线，
    近12个月扣非净利润为正，今日涨幅小于5%。

How each condition is read (every threshold can be changed on the page):

    近5日跌幅 > 10%       the price now against the close 5 sessions ago (forward-adjusted, so a dividend isn't a
                         "drop"): price / close[T-5] - 1 <= -10%
    今日主力净流入 > 3000万  today's main-fund (extra-large + large orders) net inflow, East Money's figure
                         (Sina's when East Money is blocked)
    站上60日均线           the price now above the average of the last 60 closes (today's price counted as one)
    扣非净利润(TTM) > 0     net profit excluding non-recurring items over the last 12 months: the latest year-to-date
                         report + the last annual report - the same period a year earlier (East Money's F10 data)
    今日涨幅 < 5%          today's change against yesterday's close: not already chasing a big rebound

The point of the combination: a sharp short drop, but the longer trend (MA60) still intact, real money buying
today and a business that makes money - a pullback, not a collapse.

A loose shortlist (a 5-session drop of 5% or more, non-ST, trading today) is enriched and kept, so the
thresholds re-filter instantly. During the session it is recomputed when opened (at most every 15 minutes);
after the close it is computed once and fixed.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd

from . import archive, http, sample, storage
from .symbols import is_st

QUERY_ZH = ("低位吸筹（抄底不接飞刀）：近5个交易日跌幅超过10%，今日主力资金净流入超过3000万元，现价站上60日均线，"
            "近12个月扣非净利润为正，今日涨幅小于5%。")
QUERY_EN = ("Buy the dip, avoid the falling knife: down more than 10% over the last 5 trading days, today's "
            "main-fund net inflow above CNY 30 million, price above the 60-day moving average, and positive net "
            "profit excluding non-recurring items over the last 12 months, and up less than 5% today.")
SHORTLIST_DROP = 5.0          # % - loose, so the page can relax the 10% rule without recomputing
CLOSE = time(15, 0)
REFRESH_MIN = 15              # minutes between live recomputes during the session


@dataclass
class Params:
    drop5_min: float = 10.0          # % fall over 5 sessions
    inflow_min_m: float = 30.0       # CNY millions, today
    ma_n: int = 60                   # 20 / 60 / 120
    profit_min_m: float = 0.0        # CNY millions, trailing 12 months
    change_max: float = 5.0          # % today's change must stay below
    use_drop: bool = True
    use_inflow: bool = True
    use_ma: bool = True
    use_profit: bool = True
    use_change: bool = True


def drop5_rough(snap: pd.DataFrame, day: date) -> pd.Series:
    """The 5-session change for every stock from data already on disk: the history panel (adjusted), else the
    volume books (unadjusted). Used only to pick the shortlist; exact figures come from fresh bars. All stocks in
    one pass over the panel (a per-stock lookup took 16 s for the whole market)."""
    from . import panel, tradingdays

    ref: dict[str, float] = {}
    df, _ = panel._loaded()
    if not df.empty:
        before = df[df["date"] < pd.Timestamp(day)]
        g = before.groupby("code", sort=False)
        last = g.tail(1).set_index("code")
        fifth = g.tail(5).groupby("code", sort=False).head(1).set_index("code")["close"]
        n = g.size()
        prev = tradingdays.previous_trading_day("CN", day)
        quotes = snap.set_index("code")["prev_close"]
        ok = (n >= 5) & (last["date"].dt.date >= prev if prev else True)
        # A stock that went ex-dividend since the panel was saved: its previous close no longer matches.
        agree = (last["close"] / quotes.reindex(last.index) - 1).abs() <= 0.002
        keep = ok.reindex(fifth.index).fillna(False) & agree.reindex(fifth.index).fillna(False)
        ref = fifth[keep].astype(float).to_dict()
    books = archive.load_volume_history("CN", before=day, n=5)
    if len(books) == 5:
        oldest = books[-1][1].set_index("code")["close"]
        for c in snap["code"]:
            if c not in ref and c in oldest.index:
                ref[c] = float(oldest[c])
    base = snap["code"].map(ref)
    return 100 * (snap["price"].to_numpy() / base - 1)


def shortlist(snap: pd.DataFrame, day: date) -> pd.DataFrame:
    d = snap[(snap["price"] > 0) & (snap["volume"].fillna(0) > 0)].copy()
    d = d[~d["name"].fillna("").map(is_st)]
    d["drop5_rough"] = drop5_rough(d, day).to_numpy()
    return d[d["drop5_rough"] <= -SHORTLIST_DROP]


# --------------------------------------------------------------------------- enrichment

def trend(code: str, day: date, price: float, prev_close: float | None = None) -> dict:
    """The exact 5-session change and the price against MA20 / MA60 / MA120, from adjusted daily bars: the saved
    130-session history panel when it covers the stock (no download), else downloaded."""
    from . import panel
    from .sources import tencent

    h = panel.history(code, day, prev_close, min_rows=59)
    if h is None:
        h = tencent.daily(code, start=day - timedelta(days=200), end=day, fq="qfq")
        h = h[h["date"] < pd.Timestamp(day)]
    out = {"code": code}
    c = h["close"].astype(float).tolist()
    if len(c) >= 5:
        out["drop5"] = 100 * (price / c[-5] - 1)
    for n in (20, 60, 120):
        if len(c) >= n - 1:
            ma = (sum(c[-(n - 1):]) + price) / n
            out[f"ma{n}"] = ma
            out[f"above_ma{n}_pct"] = 100 * (price / ma - 1)
    return out


def _ttm(rows: pd.DataFrame) -> tuple[float | None, str | None]:
    """Trailing-12-month value from year-to-date reports: latest YTD + last annual - same period a year before."""
    rows = rows.dropna(subset=["value"]).sort_values("date")
    if rows.empty:
        return None, None
    last = rows.iloc[-1]
    d = last["date"]
    if (d.month, d.day) == (12, 31):
        return float(last["value"]), f"{d:%Y} annual"
    by = {r.date: r.value for r in rows.itertuples()}
    annual = by.get(pd.Timestamp(d.year - 1, 12, 31))
    same = by.get(pd.Timestamp(d.year - 1, d.month, d.day))
    if annual is None or same is None:
        return None, None
    return float(last["value"] + annual - same), f"to {d:%Y-%m}"


def profit_ttm(codes: list[str], day: date) -> pd.DataFrame:
    """扣非净利润 over the last 12 months for each code (East Money F10 main financial data).

    Company reports change a few times a year, so answers are kept for the week (not the day), and the requests
    run six at a time: this was 75 s for 2,000 stocks, one request after another, every day."""
    from .sources.eastmoney import HEADERS

    year, week, _ = day.isocalendar()
    key = f"profit_{year}w{week:02d}"
    cached = storage.load_frame("dipbuy", key)
    have = set(cached["code"]) if cached is not None else set()
    todo = [c for c in codes if c not in have]
    since = f"{day.year - 2}-01-01"

    def chunk_rows(chunk: list[str]) -> list[dict]:
        secu = '","'.join(f"{c[2:]}.{c[:2].upper()}" for c in chunk)
        rows, page = [], 1
        while True:
            j = http.get("https://datacenter-web.eastmoney.com/api/data/v1/get", params={
                "reportName": "RPT_F10_FINANCE_MAINFINADATA", "columns": "SECUCODE,REPORT_DATE,KCFJCXSYJLR",
                "filter": f'(SECUCODE in ("{secu}"))(REPORT_DATE>=\'{since}\')', "pageSize": 500,
                "pageNumber": page}, headers=HEADERS, timeout=15).json()
            res = j.get("result") or {}
            rows += res.get("data") or []
            if page >= (res.get("pages") or 0):
                return rows
            page += 1

    chunks = [todo[i:i + 80] for i in range(0, len(todo), 80)]
    rows: list[dict] = []
    failed = 0
    for got in http.pmap(chunk_rows, chunks, workers=6):
        if isinstance(got, list):
            rows += got
        else:
            failed += 1
    if chunks and failed == len(chunks):
        raise http.SourceUnavailable("East Money financial reports didn't answer")
    out = []
    if rows:
        f = pd.DataFrame(rows)
        f["code"] = f["SECUCODE"].str[-2:].str.lower() + f["SECUCODE"].str[:6]
        f["date"] = pd.to_datetime(f["REPORT_DATE"])
        f["value"] = pd.to_numeric(f["KCFJCXSYJLR"], errors="coerce")
        for code, g in f.groupby("code"):
            v, basis = _ttm(g)
            out.append({"code": code, "profit_ttm": v, "profit_basis": basis})
    fresh = pd.DataFrame(out, columns=["code", "profit_ttm", "profit_basis"])
    if not failed:      # a stock with no reports is remembered as unknown; one whose batch failed is asked again
        fresh = pd.concat([fresh, pd.DataFrame({"code": [c for c in todo if c not in set(fresh["code"])]})])
    allp = fresh if cached is None else pd.concat([cached, fresh], ignore_index=True)
    storage.save_frame("dipbuy", key, allp)
    return allp[allp["code"].isin(codes)]


def run(day: date, snap: pd.DataFrame, progress: Callable[[float, str], None] | None = None,
        workers: int = 12, now: datetime | None = None) -> dict:
    """Screen `day` from a snapshot (live during the session, closing after it) and save it."""
    from . import realtime
    from .calendar import now_bj

    now = now or now_bj()
    notes: list[str] = []
    snap = snap[pd.to_datetime(snap["time"]).dt.date == day]
    cands = shortlist(snap, day)
    if progress:
        progress(0.05, f"低位吸筹 shortlist: {len(cands)} stocks down {SHORTLIST_DROP:g}%+ over 5 sessions")
    rows = []
    recs = cands[["code", "price", "prev_close"]].to_dict("records")
    for i in range(0, len(recs), 60):
        for r in http.pmap(lambda r: trend(r["code"], day, r["price"], r["prev_close"]), recs[i:i + 60],
                           workers=workers):
            if isinstance(r, dict):
                rows.append(r)
        if progress:
            progress(0.05 + 0.6 * min(i + 60, len(recs)) / max(len(recs), 1),
                     f"均线 · Moving averages {min(i + 60, len(recs))}/{len(recs)}")
    d = cands.merge(pd.DataFrame(rows, columns=["code"]) if not rows else pd.DataFrame(rows), on="code", how="left")
    if progress:
        progress(0.7, "今日主力资金 · Today's main-fund flow")
    codes = d["code"].tolist()
    flow = realtime.main_flow(codes, workers=workers) if codes else pd.DataFrame(columns=["code", "main_net", "source"])
    d = d.merge(flow[["code", "main_net"]], on="code", how="left")
    flow_src = realtime.sources(flow)
    if not flow.empty:
        notes.append(f"Main-fund flow: {flow_src}.")
    if progress:
        progress(0.85, "扣非净利润 · Net profit excl. non-recurring items")
    try:
        prof = profit_ttm(codes, day) if codes else pd.DataFrame(columns=["code", "profit_ttm", "profit_basis"])
        d = d.merge(prof, on="code", how="left")
    except http.SourceUnavailable as e:
        d["profit_ttm"], d["profit_basis"] = np.nan, None
        notes.append(f"Financial reports unavailable ({e}); the profit condition can't be checked.")
    keep = ["code", "name", "price", "pct_change", "drop5", "drop5_rough", "main_net", "ma20", "ma60", "ma120",
            "above_ma20_pct", "above_ma60_pct", "above_ma120_pct", "profit_ttm", "profit_basis", "turnover_rate",
            "float_mcap", "amount"]
    final = day < now.date() or now.time() >= CLOSE
    res = {"day": day, "frame": d.reindex(columns=keep), "notes": notes, "shortlisted": len(cands),
           "computed": now.strftime("%Y-%m-%d %H:%M"), "final": final, "flow_sources": flow_src}
    save(res)
    return res


def apply(frame: pd.DataFrame, p: Params | None = None) -> pd.DataFrame:
    p = p or Params()
    d = frame.copy()
    drop = d["drop5"].fillna(d["drop5_rough"])
    ma_n = min((20, 60, 120), key=lambda n: abs(n - p.ma_n))
    d["r_drop"] = (drop <= -p.drop5_min) if p.use_drop else True
    d["r_inflow"] = (d["main_net"] > p.inflow_min_m * 1e6) if p.use_inflow else True
    d["r_ma"] = (d[f"above_ma{ma_n}_pct"] > 0) if p.use_ma else True
    d["r_profit"] = (d["profit_ttm"] > p.profit_min_m * 1e6) if p.use_profit else True
    d["r_change"] = (d["pct_change"] < p.change_max) if p.use_change else True
    cols = ["r_drop", "r_inflow", "r_ma", "r_profit", "r_change"]
    d["rules_met"] = d[cols].fillna(False).astype(bool).sum(axis=1)
    d["passes"] = d["rules_met"] == len(cols)
    labels = {"r_drop": "近5日跌幅", "r_inflow": "主力净流入", "r_ma": f"MA{ma_n}", "r_profit": "扣非净利润",
              "r_change": "今日涨幅"}
    d["missed"] = [", ".join(labels[c] for c, ok in zip(cols, row) if not ok)
                   for row in d[cols].fillna(False).astype(bool).itertuples(index=False)]
    return d.sort_values(["passes", "rules_met", "main_net"], ascending=False).reset_index(drop=True)


def stale(res: dict | None, now: datetime) -> bool:
    """Whether a saved result for today should be recomputed: none yet, or a live one older than REFRESH_MIN."""
    if res is None:
        return True
    if res.get("final"):
        return False
    if now.time() >= CLOSE:
        return True
    age = now.replace(tzinfo=None) - datetime.strptime(res["computed"], "%Y-%m-%d %H:%M")
    return age > timedelta(minutes=REFRESH_MIN)


def bucket(now: datetime) -> str:
    """The job key's time part: one live run per 15 minutes, one after the close."""
    if now.time() >= CLOSE:
        return "close"
    return f"{now.hour:02d}{now.minute // REFRESH_MIN * REFRESH_MIN:02d}"


# --------------------------------------------------------------------------- archive (rolling window)

def _base(day: date):
    return archive.RESULTS_DIR / "cn" / "dipbuy" / day.isoformat()


def save(res: dict):
    if sample.is_sample():
        return None
    base = _base(res["day"])
    base.parent.mkdir(parents=True, exist_ok=True)
    res["frame"].to_parquet(base.with_suffix(".parquet"), index=False, compression="zstd")
    base.with_suffix(".json").write_text(json.dumps({
        "day": res["day"].isoformat(), "notes": res["notes"], "shortlisted": res["shortlisted"],
        "computed": res["computed"], "final": res["final"]}, ensure_ascii=False), encoding="utf-8")
    return base


def load(day: date) -> dict | None:
    base = _base(day)
    if not base.with_suffix(".parquet").exists():
        return None
    meta = json.loads(base.with_suffix(".json").read_text(encoding="utf-8")) if base.with_suffix(".json").exists() \
        else {}
    return {"day": day, "frame": pd.read_parquet(base.with_suffix(".parquet")), "notes": meta.get("notes", []),
            "shortlisted": meta.get("shortlisted"), "computed": meta.get("computed", ""),
            "final": meta.get("final", True)}


def days() -> list[date]:
    d = archive.RESULTS_DIR / "cn" / "dipbuy"
    return sorted((date.fromisoformat(f.stem) for f in d.glob("*.parquet")), reverse=True) if d.exists() else []


def prune(keep: int = archive.KEEP_SESSIONS) -> list:
    removed = []
    for d in days()[keep:]:
        for ext in (".parquet", ".json"):
            f = _base(d).with_suffix(ext)
            if f.exists():
                f.unlink()
                removed.append(f)
    return removed
