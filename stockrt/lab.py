"""Strategy lab: backtest, live record and scorecards for the strategies in stockrt.strategies.

* Backtest - 13 Oct 2025 to 9 Oct 2026, rebuilt from 30-minute bars (stockrt.labdata).
* Live record - every session from 12 Oct 2026, written down the day it happens by `update()`
  (scripts/daily_update.py, run by GitHub Actions after the close) and never changed afterwards,
  except to fill in exit prices once they exist.
* Today - the real-time panel: each strategy's picks as soon as its signal time has passed.

How a strategy is judged (fixed before any result was seen, docs/STRATEGY_RESEARCH.md):
excess return = the trade's return after costs minus the benchmark's return over the same window.
The main benchmark buys every eligible stock at the same moment (equal weight); CSI 300 and CSI 1000
are shown too. A strategy passes when its average excess is positive, the t-statistic of its daily
excess is at least 2 or it beats 95% of random pickers, more than half its trades beat the market, and
at least 2 of 3 sub-periods are positive.
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Callable
from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd

from . import archive, http, labdata, realtime, sample
from .calendar import now_bj
from .labdata import EXITS
from .sources import tencent
from .strategies import SIGNAL_ORDER, STRATEGIES, evaluate, live_pass, sentiment, with_ret5_cut
from .symbols import board, is_st

log = logging.getLogger(__name__)

BACKTEST_START = date(2025, 10, 13)
BACKTEST_END = date(2026, 10, 9)
LIVE_START = date(2026, 10, 12)
COSTS = {
    "Commission, 0.025% each way": 0.0005,
    "Stamp duty, 0.05% on the sale (since 28 Aug 2023)": 0.0005,
    "Transfer fee, 0.001% each way": 0.00002,
    "Slippage allowance, 0.05% each way": 0.001,
}
ROUND_TRIP = sum(COSTS.values())
BENCHES = {"ew": "Equal-weight market", "csi300": "CSI 300", "csi1000": "CSI 1000"}
LIVE_MIN_DAYS = 60
TRADE_COLS = ["strategy", "day", "time", "code", "name", "price", "pct_change", "gap_pct", "volume_ratio",
              "turnover_rate", "float_mcap", "core", "all", *[f"ret_{x}" for x in EXITS], "main_net",
              "flow_source", "auction_volume", "auction_amount"]


def lab_dir():
    """results/lab of the results in use (published by the daily job, or local)."""
    return archive.RESULTS_DIR / "lab"


def still_latest(day: pd.Timestamp | date, now: datetime) -> bool:
    """Is `day` still the market's latest session (so today-only feeds - money flow, today's minute bars -
    still describe it)? True until the next session's pre-open starts."""
    from . import tradingdays

    d = pd.Timestamp(day).date()
    nxt = tradingdays.next_trading_day("CN", d)
    return nxt is None or now.date() < nxt or (now.date() == nxt and now.time() < time(9, 15))


def universe_codes() -> tuple[list[str], dict[str, str]]:
    """Shanghai / Shenzhen A-shares (Beijing has no 30-minute bars)."""
    u = realtime.universe("CN")
    u = u[u["code"].map(board).isin(["Main", "ChiNext", "STAR"])]
    return u["code"].tolist(), dict(zip(u["code"], u["name"]))


# --------------------------------------------------------------------------- scoring a frame

def _day_table(s: pd.DataFrame, t: str) -> pd.DataFrame:
    e = s[s["eligible"]]
    g = e.groupby("day")
    b = pd.DataFrame({"n_elig": g.size()})
    for x in EXITS:
        b[f"ew_{x}"], b[f"sd_{x}"], b[f"n_{x}"] = g[f"ret_{x}"].mean(), g[f"ret_{x}"].std(), g[f"ret_{x}"].count()
        for tag in ("csi300", "csi1000"):
            if f"{tag}_{x}" in s:
                b[f"{tag}_{x}"] = s.groupby("day")[f"{tag}_{x}"].first()
    if "csi300_pct" in s:
        b["csi300_pct"] = s.groupby("day")["csi300_pct"].first()
    if t == "open":
        b["ew_open_close"], b["ew_overnight"] = g["ret_open_close"].mean(), g["ret_overnight"].mean()
    b = b.join(sentiment(s).set_index("day"))
    return b.reset_index().assign(time=t)


def score_frame(frame: pd.DataFrame, idx: dict[str, pd.DataFrame], days: list[pd.Timestamp] | None = None,
                ret5_cut: float | None = None, explain: bool = False,
                chunk: int = 20) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Every strategy's picks (core rules) and the per-session benchmark table, for `days`.

    Each row already carries its history, so sessions are scored `chunk` at a time to keep memory low."""
    days = sorted(pd.Timestamp(d) for d in (days if days is not None else frame["date"].unique()))
    if len(days) > chunk:
        parts = [score_frame(frame[frame["date"].isin(days[i:i + chunk])], idx, days[i:i + chunk], ret5_cut, explain,
                             chunk) for i in range(0, len(days), chunk)]
        return (pd.concat([p[0] for p in parts], ignore_index=True),
                pd.concat([p[1] for p in parts], ignore_index=True))
    frame = frame[frame["date"].isin(days)]
    trades, tables = [], []
    for t in SIGNAL_ORDER:
        sts = [st for st in STRATEGIES.values() if st.time == t]
        s = labdata.state(frame, t, idx)
        s = with_ret5_cut(s) if ret5_cut is None else s.assign(ret5_cut=ret5_cut)
        tables.append(_day_table(s, t))
        for st in sts:
            ev = evaluate(st, s, explain=explain)
            p = ev[ev["core"]].assign(strategy=st.id)
            trades.append(p)
        del s
    tr = pd.concat(trades, ignore_index=True) if trades else pd.DataFrame()
    tr = tr.reindex(columns=[*TRADE_COLS, *(["missed"] if explain else [])])
    tr["core"] = True
    tr["all"] = [True if not STRATEGIES[s].live_only else np.nan for s in tr["strategy"]]
    tr["all"] = tr["all"].astype(object)
    return tr.reset_index(drop=True), pd.concat(tables, ignore_index=True)


def _save(df: pd.DataFrame, name: str) -> None:
    lab_dir().mkdir(parents=True, exist_ok=True)
    df.to_parquet(lab_dir() / name, index=False, compression="zstd")


def _read(name: str) -> pd.DataFrame:
    p = lab_dir() / name
    return pd.read_parquet(p) if p.exists() else pd.DataFrame()


def run_backtest(frame: pd.DataFrame, idx: dict[str, pd.DataFrame], end: date = BACKTEST_END) -> dict:
    days = sorted(d for d in frame["date"].unique() if pd.Timestamp(d).date() <= end)
    trades, table = score_frame(frame, idx, days)
    trades["all"] = trades["all"].astype(object)
    mom = intraday_momentum(idx["sh000300"])
    meta = {
        "start": str(pd.Timestamp(days[0]).date()), "end": str(pd.Timestamp(days[-1]).date()), "sessions": len(days),
        "stocks": int(frame["code"].nunique()), "generated": now_bj().strftime("%Y-%m-%d %H:%M"),
        "round_trip_cost": ROUND_TRIP, "live_start": LIVE_START.isoformat(),
    }
    _save(trades, "backtest_trades.parquet")
    old = _read("days.parquet")
    if not old.empty:
        old = old[~old["day"].isin(days)]
    _save(pd.concat([old, table], ignore_index=True).sort_values(["day", "time"]), "days.parquet")
    _save(mom, "csi300_intraday.parquet")
    (lab_dir() / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    write_report()
    return {"trades": trades, "days": table, "meta": meta}


# --------------------------------------------------------------------------- metrics

def _phi(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def picks_for(trades: pd.DataFrame, sid: str, rules: str = "core") -> pd.DataFrame:
    t = trades[trades["strategy"] == sid]
    if rules == "all":
        t = t.loc[t["all"].map(lambda v: v is True or v == True).astype(bool)]  # noqa: E712 - object column
    return t


def daily(trades: pd.DataFrame, days: pd.DataFrame, sid: str, exit: str = "t1_close", bench: str = "ew",
          rules: str = "core") -> pd.DataFrame:
    """One row per signal session: picks, their average return, the benchmark, excess after costs."""
    st = STRATEGIES[sid]
    t = picks_for(trades, sid, rules)
    t = t[t[f"ret_{exit}"].notna()]
    if t.empty:
        return pd.DataFrame(columns=["day", "n", "ret", "bench", "excess", "cum_excess", "warm"])
    b = days[days["time"] == st.time].set_index("day")
    g = t.groupby("day")[f"ret_{exit}"]
    d = pd.DataFrame({"n": g.size(), "ret": g.mean()})
    d["bench"] = b[f"{bench}_{exit}"].reindex(d.index)
    d["excess"] = d["ret"] - ROUND_TRIP - d["bench"]
    d["warm"] = b["yday_lu_avg"].reindex(d.index) > 0
    d = d.dropna(subset=["bench"]).reset_index()
    d["cum_excess"] = d["excess"].cumsum()
    return d


def metrics(trades: pd.DataFrame, days: pd.DataFrame, sid: str, exit: str = "t1_close", bench: str = "ew",
            rules: str = "core", live: bool = False) -> dict:
    st = STRATEGIES[sid]
    t = picks_for(trades, sid, rules)
    signal_days = int(t["day"].nunique())
    t = t[t[f"ret_{exit}"].notna()]
    b = days[days["time"] == st.time].set_index("day")
    t = t.assign(bench=t["day"].map(b[f"{bench}_{exit}"]), ew=t["day"].map(b[f"ew_{exit}"]),
                 sd=t["day"].map(b[f"sd_{exit}"]), n_u=t["day"].map(b[f"n_{exit}"])).dropna(subset=["bench"])
    m = {"strategy": sid, "signal_days": signal_days, "trades": len(t), "days": int(t["day"].nunique())}
    if t.empty:
        return m | {"verdict": "No signals yet" if live else "No signals", "why": ""}
    r = t[f"ret_{exit}"]
    ex = r - ROUND_TRIP - t["bench"]
    m["avg_ret"] = float(r.mean() - ROUND_TRIP)
    m["avg_excess"] = float(ex.mean())
    m["median_excess"] = float(ex.median())
    m["win"] = float((r - ROUND_TRIP > 0).mean())
    m["beat"] = float((ex > 0).mean())
    m["per_day"] = len(t) / max(m["days"], 1)
    d = daily(trades, days, sid, exit, bench, rules)
    e = d["excess"]
    m["t"] = float(e.mean() / (e.std(ddof=1) / math.sqrt(len(e)))) if len(e) > 2 and e.std(ddof=1) > 0 else np.nan
    thirds = [c.mean() for c in np.array_split(e.to_numpy(), 3) if len(c)]
    m["thirds"] = int(sum(x > 0 for x in thirds))
    curve = d["cum_excess"]
    m["max_dd"] = float((curve - curve.cummax()).min()) if len(curve) else np.nan
    # Random-pick placebo: the exact spread of an average of k_d random eligible stocks each day.
    k = t.groupby("day").size()
    per = t.groupby("day")[["sd", "n_u"]].first()
    var = (k * per["sd"] ** 2 * (per["n_u"] - k).clip(lower=0) / (per["n_u"] - 1).clip(lower=1)).sum()
    x = float((r - t["ew"]).mean())
    m["placebo"] = _phi(x / (math.sqrt(var) / len(t))) if var > 0 else np.nan
    warm = d[d["warm"]]["excess"]
    cold = d[~d["warm"]]["excess"]
    m["warm_excess"], m["cold_excess"] = (float(warm.mean()) if len(warm) else np.nan,
                                          float(cold.mean()) if len(cold) else np.nan)
    m["warm_days"], m["cold_days"] = len(warm), len(cold)
    m["verdict"], m["why"] = verdict(m, live)
    return m


def verdict(m: dict, live: bool) -> tuple[str, str]:
    need = LIVE_MIN_DAYS if live else 20
    if m["days"] < need or m["trades"] < 30:
        return (f"Too early ({m['days']}/{need} days)" if live else "Too few signals"), \
            f"needs {need} signal days and 30 trades"
    fails = []
    if not m["avg_excess"] > 0:
        fails.append(f"average excess {m['avg_excess']:+.2%}")
    if not (m["t"] >= 2 or m["placebo"] >= 0.95):
        fails.append(f"not significant (t {m['t']:.1f}, beats {m['placebo']:.0%} of random pickers)")
    if not m["beat"] > 0.5:
        fails.append(f"beats the market on {m['beat']:.0%} of trades")
    if m["thirds"] < 2:
        fails.append(f"positive in {m['thirds']} of 3 sub-periods")
    return ("Passes", "every test met") if not fails else ("Fails", "; ".join(fails))


def scorecard(trades: pd.DataFrame, days: pd.DataFrame, exit: str = "t1_close", bench: str = "ew",
              rules: str = "core", live: bool = False) -> pd.DataFrame:
    rows = [metrics(trades, days, sid, exit, bench, rules, live) for sid in STRATEGIES]
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- market checks

def intraday_momentum(fi: pd.DataFrame) -> pd.DataFrame:
    """CSI 300 by session: overnight + first half hour, the middle of the day, and the last half hour."""
    d = pd.DataFrame({"day": fi["date"]})
    d["r_on10"] = fi["p_1000"] / fi["prev_close"] - 1
    d["r_first"] = fi["p_1000"] / fi["p_open"] - 1
    d["r_rest"] = fi["p_1430"] / fi["p_1000"] - 1
    d["r_last"] = fi["p_1500"] / fi["p_1430"] - 1
    return d.dropna().reset_index(drop=True)


def momentum_stats(d: pd.DataFrame, x: str = "r_on10") -> dict:
    if len(d) < 10:
        return {}
    xs, ys = d[x].to_numpy(), d["r_last"].to_numpy()
    xm, ym = xs - xs.mean(), ys - ys.mean()
    slope = float((xm * ym).sum() / (xm ** 2).sum())
    resid = ym - slope * xm
    se = math.sqrt((resid ** 2).sum() / (len(xs) - 2) / (xm ** 2).sum())
    rule = np.sign(xs) * ys
    return {"sessions": len(d), "corr": float(np.corrcoef(xs, ys)[0, 1]), "slope": slope, "t": slope / se,
            "same_sign": float((np.sign(xs) == np.sign(ys)).mean()), "rule_bp": float(rule.mean() * 1e4),
            "rule_t": float(rule.mean() / (rule.std(ddof=1) / math.sqrt(len(rule))))}


# --------------------------------------------------------------------------- validation and books

def validate(frame: pd.DataFrame, idx: dict[str, pd.DataFrame], day: date) -> dict:
    """Rebuild one archived front-page session at 14:30 from 30-minute bars and compare."""
    res = archive.load("CN", day)
    if res is None or "14:30" not in res["frames"]:
        return {"error": f"no archived 14:30 result for {day}"}
    a = res["frames"]["14:30"]
    rule_cols = [c for c in a.columns if c.startswith("rule_") and c != "rule_inflow"]
    a_pass = set(a[a[rule_cols].fillna(False).astype(bool).all(axis=1)]["code"])
    f = frame[frame["date"] == pd.Timestamp(day)]
    s = labdata.state(f, "14:30", {c: g[g["date"] == pd.Timestamp(day)] for c, g in idx.items()})
    ev = evaluate(STRATEGIES["S1"], s)
    lab_pass = set(ev[ev["core"]]["code"])
    both = a.merge(s, on="code", suffixes=("_arch", "_lab"))
    diffs = {c: float((both[f"{c}_lab"] - both[f"{c}_arch"]).abs().median())
             for c in ("pct_change", "volume_ratio", "turnover_rate", "price")}
    return {"day": str(day), "archive_pass": sorted(a_pass), "lab_pass": sorted(lab_pass),
            "agree": sorted(a_pass & lab_pass), "only_archive": sorted(a_pass - lab_pass),
            "only_lab": sorted(lab_pass - a_pass), "compared_stocks": len(both), "median_abs_diff": diffs}


def books_from_frame(frame: pd.DataFrame, day: pd.Timestamp) -> pd.DataFrame:
    f = frame[frame["date"] == day]
    out = pd.DataFrame({
        "code": f["code"], "name": f["name"], "volume": f["volume"].astype(float), "amount": f["amount"].astype(float),
        "close": f["p_1500"], "prev_close": f["prev_close"], "open": f["p_open"],
        "high": f["q_high"] - f["off"], "low": f["q_low"] - f["off"],
        "turnover_rate": f["turnover_rate"].astype(float),
    })
    out["pct_change"] = 100 * (out["close"] / out["prev_close"] - 1)
    out["float_shares"] = out["volume"] / (out["turnover_rate"] / 100)
    return out[out["close"].notna()]


def daily_books(days: list[pd.Timestamp], codes: list[str] | None = None,
                names: dict[str, str] | None = None, workers: int = 12) -> dict[pd.Timestamp, pd.DataFrame]:
    """End-of-day volume books from daily bars (one request per stock) - for stocks without 30-minute bars
    (Beijing) or for a session nobody recorded."""
    u = realtime.universe("CN")
    if codes is None:
        codes = u["code"].tolist()
    names = (names or {}) | dict(zip(u["code"], u["name"]))
    start = min(days).date() - timedelta(days=15)

    def one(code: str) -> pd.DataFrame:
        b = tencent.daily(code, start=start)
        if b.empty:
            return b
        b = b.assign(code=code, name=names.get(code, code), prev_close=b["close"].shift(1))
        return b[b["date"].isin(days)]

    parts = [r for r in http.pmap(one, codes, workers=workers) if isinstance(r, pd.DataFrame) and not r.empty]
    if not parts:
        return {}
    b = pd.concat(parts, ignore_index=True)
    b["pct_change"] = 100 * (b["close"] / b["prev_close"] - 1)
    b["float_shares"] = b["volume"] / (b["turnover_rate"] / 100)
    return {d: g.drop(columns=["date", "event"]) for d, g in b.groupby("date")}


def _bse_books(days: list[pd.Timestamp], names: dict[str, str]) -> dict[pd.Timestamp, pd.DataFrame]:
    """Beijing-exchange stocks have no 30-minute bars: their books come from daily bars."""
    u = realtime.universe("CN")
    return daily_books(days, [c for c in u["code"] if board(c) == "BSE"], names)


def write_books(frame: pd.DataFrame, sessions: int = 30, end: date = BACKTEST_END, overwrite: bool = True) -> list:
    """End-of-day volume books (every A-share) for the last `sessions` sessions up to `end`."""
    days = sorted(d for d in frame["date"].unique() if pd.Timestamp(d).date() <= end)[-sessions:]
    days = [pd.Timestamp(d) for d in days]
    have = set(archive.volume_days("CN"))
    if not overwrite:
        days = [d for d in days if d.date() not in have]
    if not days:
        return []
    bse = _bse_books(days, {})
    paths = []
    for d in days:
        book = pd.concat([books_from_frame(frame, d), bse.get(d, pd.DataFrame())], ignore_index=True)
        paths.append(archive.save_volumes("CN", d.date(), book))
    return paths


# --------------------------------------------------------------------------- the live record

def load() -> dict:
    """Everything the page shows (a few MB): trades, the session table, the CSI 300 intraday table, meta."""
    meta_f = lab_dir() / "meta.json"
    return {"backtest": _read("backtest_trades.parquet"), "live": _read("live_trades.parquet"),
            "days": _read("days.parquet"), "momentum": _read("csi300_intraday.parquet"),
            "meta": json.loads(meta_f.read_text(encoding="utf-8")) if meta_f.exists() else {}}


def recorded(day) -> bool:
    """Is this session in the live record (written by the daily update)?"""
    lv = _read("live_trades.parquet")
    return not lv.empty and pd.Timestamp(day) in set(lv["day"])


def _session_done(day: pd.Timestamp, now: datetime) -> bool:
    return day.date() < now.date() or now.time() >= time(15, 5)


def _live_extras(day: pd.Timestamp, picks: pd.DataFrame, now: datetime) -> pd.DataFrame:
    """Main-fund flow (S1) and auction volumes (S2) - readable only on the session's own day."""
    picks = picks.copy()
    if picks.empty or not still_latest(day, now):
        return picks
    s1 = picks[picks["strategy"].isin(["S1", "S1b"])]["code"].unique().tolist()
    if s1:
        try:
            flow = realtime.main_flow(s1).set_index("code")
            m = picks["strategy"].isin(["S1", "S1b"])
            picks.loc[m, "main_net"] = picks.loc[m, "code"].map(flow["main_net"])
            picks.loc[m, "flow_source"] = picks.loc[m, "code"].map(flow["source"])
        except Exception as e:  # noqa: BLE001 - left unchecked
            log.warning("main flow unavailable: %s", e)
    s2 = picks[picks["strategy"] == "S2"]["code"].unique().tolist()
    if s2:
        from .opening import fill_auction_volumes

        try:
            got = fill_auction_volumes(pd.DataFrame({"code": s2}), s2).set_index("code")
            m = picks["strategy"] == "S2"
            picks.loc[m, "auction_volume"] = picks.loc[m, "code"].map(got["auction_volume"])
            picks.loc[m, "auction_amount"] = picks.loc[m, "code"].map(got["auction_amount"])
        except Exception as e:  # noqa: BLE001
            log.warning("auction volumes unavailable: %s", e)
    return picks


def _apply_live(picks: pd.DataFrame, frame_state: pd.DataFrame | None = None) -> pd.DataFrame:
    picks = picks.copy()
    picks["all"] = picks["all"].astype(object)
    for sid, g in picks.groupby("strategy"):
        st = STRATEGIES[sid]
        if not st.live_only:
            picks.loc[g.index, "all"] = True
        else:
            g2 = g.copy()
            if "prev_volume" not in g2 or "float_shares" not in g2:
                g2 = g2.merge(frame_state, on=["day", "code"], how="left") if frame_state is not None else g2
            picks.loc[g.index, "all"] = live_pass(st, g2.set_index(g.index)).to_numpy()
    return picks


def update(progress: Callable[[float, str], None] | None = None, workers: int = 16,
           now: datetime | None = None) -> dict:
    """Record every finished session since the last one (from 12 Oct 2026) and fill pending exits."""
    if sample.is_sample():
        return {"skipped": "sample mode"}
    now = now or now_bj()
    bt, lv, days_tbl = _read("backtest_trades.parquet"), _read("live_trades.parquet"), _read("days.parquet")
    probe = labdata.index_calendar(now.date() - timedelta(days=40))
    done = [d for d in probe if _session_done(d, now)]
    recorded = set(lv["day"]) if not lv.empty else set()
    new_days = [d for d in done if d.date() >= LIVE_START and d not in recorded]
    meta_f = lab_dir() / "meta.json"
    meta = json.loads(meta_f.read_text(encoding="utf-8")) if meta_f.exists() else {}
    pending = set()
    if meta.get("exits_checked_through") != str(done[-1].date()):
        for t in (bt, lv):
            if not t.empty:
                miss = t[t[[f"ret_{x}" for x in EXITS]].isna().any(axis=1)]
                pending |= {d for d in miss["day"].unique() if done[-6] <= pd.Timestamp(d) < done[-1]}
    window = sorted(set(new_days) | {pd.Timestamp(d) for d in pending})
    if not window:
        return {"recorded": [], "filled": 0, "note": "up to date"}
    first = window[0].date()
    n_sess = sum(1 for d in probe if d >= window[0])
    codes, names = universe_codes()
    frame, idx, _ = labdata.build(codes, names, first, progress=progress, workers=workers,
                                  bar_count=min(800, 8 * (n_sess + 3)), min_coverage=0.97)
    # The bottom-5% cut-off needs the whole market, which this frame is.
    trades, table = score_frame(frame, idx, window)
    trades["recorded_at"] = now.strftime("%Y-%m-%d %H:%M")
    # 1) new sessions -> the live record (with the live-only checks where the data still exists)
    new = trades[trades["day"].isin(new_days)]
    if not new.empty:
        ctx = pd.concat([labdata.state(frame, t, None)[["day", "code", "prev_volume", "float_shares"]].assign(time=t)
                         for t in ("open",)], ignore_index=True).drop(columns="time")
        parts = []
        for d, g in new.groupby("day"):
            g = _live_extras(pd.Timestamp(d), g, now)
            parts.append(_apply_live(g, ctx))
        new = pd.concat(parts, ignore_index=True)
        lv = pd.concat([lv, new], ignore_index=True) if not lv.empty else new
    # 2) pending exits -> fill from the rebuilt prices (picks themselves never change)
    filled = 0
    rets = [f"ret_{x}" for x in EXITS]
    fresh = trades.set_index(["strategy", "day", "code"])[rets]
    for name, t in (("backtest", bt), ("live", lv)):
        if t.empty:
            continue
        key = t.set_index(["strategy", "day", "code"]).index
        upd = fresh.reindex(key)
        for c in rets:
            gap = t[c].isna().to_numpy() & upd[c].notna().to_numpy()
            filled += int(gap.sum())
            t.loc[gap, c] = upd[c].to_numpy()[gap]
        if name == "backtest":
            bt = t
        else:
            lv = t
    # 3) the session table (benchmarks, sentiment) for the window
    if not days_tbl.empty:
        days_tbl = days_tbl[~days_tbl["day"].isin(window)]
    days_tbl = pd.concat([days_tbl, table], ignore_index=True).sort_values(["day", "time"])
    mom = _read("csi300_intraday.parquet")
    m_new = intraday_momentum(idx["sh000300"])
    m_new = m_new[m_new["day"].map(lambda d: _session_done(pd.Timestamp(d), now))]
    mom = pd.concat([mom[~mom["day"].isin(m_new["day"])] if not mom.empty else mom, m_new], ignore_index=True)
    if not bt.empty:
        _save(bt, "backtest_trades.parquet")
    if not lv.empty:
        lv["all"] = lv["all"].astype(object)
        _save(lv, "live_trades.parquet")
    _save(days_tbl, "days.parquet")
    _save(mom.sort_values("day"), "csi300_intraday.parquet")
    write_books(frame, sessions=len(window), end=done[-1].date(), overwrite=False)
    meta["live_updated"] = now.strftime("%Y-%m-%d %H:%M")
    meta["exits_checked_through"] = str(done[-1].date())
    meta_f.write_text(json.dumps(meta, indent=1), encoding="utf-8")
    write_report()
    return {"recorded": [str(d.date()) for d in new_days], "new_picks": len(new), "filled": filled}


# --------------------------------------------------------------------------- today, in real time

def reached(t: str, now: datetime) -> bool:
    if t == "open":
        return now.time() >= time(9, 31)  # the opening price arrives with the first 30-minute bar
    hh, mm = map(int, t.split(":"))
    return now.time() >= time(hh, mm, 30)


def ret5_cut_from_books(day: date, pct: float = 0.05) -> float | None:
    """The market's bottom-5% five-session return as of the previous close, from the volume books."""
    hist = archive.load_volume_history("CN", before=day, n=5)
    if len(hist) < 5:
        return None
    r = None
    for _, b in hist:
        g = (1 + b.set_index("code")["pct_change"] / 100)
        r = g if r is None else r.mul(g, fill_value=np.nan)
    names = hist[0][1].set_index("code")["name"].reindex(r.index).fillna("")
    ok = ~names.map(is_st) & pd.Series(r.index.map(lambda c: board(c) in ("Main", "ChiNext", "STAR")), index=r.index)
    r = (r[ok] - 1).dropna()
    return float(r.quantile(pct)) if len(r) > 100 else None


def prefilter(snap: pd.DataFrame, times: list[str], day: date) -> dict[str, list[str]]:
    """Stocks that could meet each reached strategy (conditions that today's range can rule out)."""
    d = snap[~snap["suspended"].fillna(False) & ~snap["is_st"].fillna(False) & (snap["prev_close"] > 0)]
    d = d[d["code"].map(board).isin(["Main", "ChiNext", "STAR"])]
    shares = d["float_mcap"] / d["price"]
    hi = 100 * (d["high"] / d["prev_close"] - 1)
    lo = 100 * (d["low"] / d["prev_close"] - 1)
    gap = 100 * (d["open"] / d["prev_close"] - 1)
    out: dict[str, list[str]] = {}
    for sid, st in STRATEGIES.items():
        if st.time not in times:
            continue
        if sid in ("S1", "S1b"):
            m = (hi >= 3) & (lo <= 5) & (d["turnover_rate"] >= 3) & (d["high"] * shares >= 5e9) & \
                (d["low"] * shares <= 20e9)
        elif sid in ("S2", "S3"):
            m = (gap >= 2) & (gap <= 7) & (d["open"] < d["limit_up"] - 0.005) & (d["high"] * shares >= 3e9) & \
                (d["low"] * shares <= 50e9)
        elif sid in ("S4", "S4b"):
            m = d["high"] >= d["limit_up"] - 0.005
        elif sid == "S5":
            m = (hi >= 3) & (lo <= 7) & (d["high"] * shares >= 5e9) & (d["low"] * shares <= 50e9)
        else:  # S6
            m = (lo <= -2) & (hi >= -7) & (d["high"] * shares >= 3e9)
        out[sid] = d[m.fillna(False)]["code"].tolist()
    return out


def today(snap: pd.DataFrame, progress: Callable[[float, str], None] | None = None, now: datetime | None = None,
          workers: int = 12) -> dict:
    """Each strategy's picks for the session in progress (or just finished), as its signal time passes."""
    now = now or now_bj()
    day = pd.Timestamp(pd.to_datetime(snap["time"]).max().date())
    live_day = day.date() == now.date()
    times = [t for t in SIGNAL_ORDER if not live_day or reached(t, now)]
    cut = ret5_cut_from_books(day.date())
    cands = prefilter(snap, times, day.date())
    waiting = {sid: st.known_at for sid, st in STRATEGIES.items()
               if st.known_at and live_day and not reached(st.known_at, now)}
    for sid in waiting:
        cands.pop(sid, None)
    if cut is None:
        cands.pop("S6", None)
    codes = sorted({c for v in cands.values() for c in v})
    names = dict(zip(snap["code"], snap["name"]))
    picks = pd.DataFrame(columns=TRADE_COLS)
    explained = []
    if codes:
        frame, idx, _ = labdata.build(codes, names, day.date(), progress=progress, workers=workers, bar_count=40)
        frame = frame[frame["date"] == day]
        idx = {c: f[f["date"] == day] for c, f in idx.items()}
        parts = []
        for t in times:
            s = labdata.state(frame, t, idx).assign(ret5_cut=cut if cut is not None else np.nan)
            for st in (x for x in STRATEGIES.values() if x.time == t and x.id in cands):
                ev = evaluate(st, s[s["code"].isin(cands[st.id])])
                p = ev[ev["core"]].assign(strategy=st.id)
                parts.append(p)
                near = ev[~ev["core"] & (ev["rules_met"] >= ev["rules_met"].max() - 1)] if len(ev) else ev
                explained.append(near.assign(strategy=st.id).sort_values("rules_met", ascending=False).head(10))
        if parts:
            picks = pd.concat(parts, ignore_index=True)
            ctx = picks[["day", "code", "prev_volume", "float_shares"]]
            picks = picks.reindex(columns=[*TRADE_COLS, "prev_volume", "float_shares", "missed"])
            picks["all"] = np.nan
            if live_day:
                picks = _live_extras(day, picks, now)
            picks = _apply_live(picks, ctx)
    return {"day": day.date(), "times": times, "picks": picks, "cut": cut,
            "near": pd.concat(explained, ignore_index=True) if explained else pd.DataFrame(),
            "pending": {sid: st.time for sid, st in STRATEGIES.items() if st.time not in times} | waiting,
            "computed": now.strftime("%H:%M:%S"), "candidates": len(codes)}


# --------------------------------------------------------------------------- static report

def write_report(exit: str = "t1_close") -> None:
    data = load()
    if data["backtest"].empty:
        return
    meta = data["meta"]
    lines = ["# Strategy lab · 策略实验室", "",
             f"Backtest {meta.get('start')} to {meta.get('end')} ({meta.get('sessions')} sessions, "
             f"{meta.get('stocks', 0):,} stocks, rebuilt from 30-minute bars). Live record from "
             f"{LIVE_START:%d %b %Y}; last updated {meta.get('live_updated', '—')} Beijing.", "",
             f"Exit: **{labdata.EXIT_LABEL[exit]}**. Excess = return after {ROUND_TRIP:.2%} round-trip costs minus "
             "the equal-weight market bought at the same moment. Rules were fixed before testing; see "
             "[docs/STRATEGY_RESEARCH.md](../../docs/STRATEGY_RESEARCH.md).", ""]
    for label, trades, live in (("Backtest", data["backtest"], False), ("Live record", data["live"], True)):
        lines += [f"## {label}", ""]
        if trades.empty:
            lines += ["No sessions recorded yet.", ""]
            continue
        sc = scorecard(trades, data["days"], exit, "ew", "all" if live else "core", live)
        lines += ["| Strategy | Signal | Trades | Days | Avg net | Avg excess | Beat market | t | Random-pick % | "
                  "Verdict |", "|---|---|---|---|---|---|---|---|---|---|"]
        for r in sc.to_dict("records"):
            st = STRATEGIES[r["strategy"]]

            def f(k, fmt):
                v = r.get(k)
                return format(v, fmt) if v is not None and not (isinstance(v, float) and np.isnan(v)) else "—"

            lines.append(f"| {st.title} | {st.time_label} | {r['trades']} | {r['days']} | {f('avg_ret', '+.2%')} | "
                         f"{f('avg_excess', '+.2%')} | {f('beat', '.0%')} | {f('t', '.1f')} | {f('placebo', '.0%')} | "
                         f"{r['verdict']} |")
        lines.append("")
    lines += ["Research tooling, not investment advice."]
    lab_dir().mkdir(parents=True, exist_ok=True)
    (lab_dir() / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
