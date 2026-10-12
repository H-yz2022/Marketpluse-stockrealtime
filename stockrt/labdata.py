"""Strategy-lab data: one row per stock per session, as it stood at each signal time.

Built from Tencent's 30-minute bars (about one year kept, Shanghai / Shenzhen only) plus daily
bars. Each row says what was known at the open, 10:00, 10:30, 14:00 and 14:30, and what happened
afterwards (next open, next close, close two sessions later). The backtest, the daily live record
and the real-time "today" panel all go through this one builder, so a strategy is judged on the
numbers it would actually have seen.

Prices: intraday bars are raw, daily history is Tencent's forward-adjusted series (qfq). They are
joined with a per-session offset (qfq close - raw close), constant between ex-dates, so moving
averages are not distorted by dividends. Returns are raw exit / raw entry: a trade whose holding window
crosses an ex-date is dropped (its offset changes), as is one whose exit session the stock did not trade.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from . import http
from .opening import limit_pct
from .sources import tencent
from .symbols import board, is_st

SIGNAL_TIMES = ("open", "10:00", "10:30", "14:00", "14:30")
BAR_TIMES = ("1000", "1030", "1400", "1430", "1500")
ELAPSED = {"open": 0, "10:00": 30, "10:30": 60, "14:00": 180, "14:30": 210, "15:00": 240}
EXITS = ("t1_open", "t1_close", "t2_close")
EXIT_LABEL = {"t1_open": "Next open (T+1)", "t1_close": "Next close (T+1)", "t2_close": "Close 2 sessions later (T+2)"}
INDEXES = {"sh000300": "CSI 300", "sh000852": "CSI 1000"}
HISTORY_DAYS = 120   # calendar days of daily bars before the first session (MA10, 20-day high, 60-session age)
MIN_HISTORY = 60     # sessions a stock must have traded before it is eligible (skips new listings)


def _naive(t: datetime) -> pd.Timestamp:
    ts = pd.Timestamp(t)
    return ts.tz_localize(None) if ts.tzinfo is not None else ts


def _round_price(x: pd.Series) -> pd.Series:
    """Exchange rounding to the fen, half up."""
    return np.floor(x * 100 + 0.5 + 1e-9) / 100


def intraday(bars: pd.DataFrame, asof: datetime | None = None) -> pd.DataFrame:
    """Per-session prices, cumulative volume and running high / low at each bar end time."""
    cols = ["date", "p_open", *[f"{k}_{t}" for t in BAR_TIMES for k in ("p", "v", "hi", "lo")],
            "p_last", "v_last", "hi_last", "lo_last"]
    if bars.empty:
        return pd.DataFrame(columns=cols)
    b = bars if asof is None else bars[bars["datetime"] <= _naive(asof)]
    b = b.assign(date=b["datetime"].dt.normalize(), hhmm=b["datetime"].dt.strftime("%H%M"))
    g = b.groupby("date", sort=True)
    b = b.assign(cv=g["volume"].cumsum(), chi=g["high"].cummax(), clo=g["low"].cummin())
    first = b[b["hhmm"] == "1000"].set_index("date")
    out = pd.DataFrame({"p_open": first["open"]})
    for t in BAR_TIMES:
        sel = b[b["hhmm"] == t].set_index("date")
        out[f"p_{t}"], out[f"v_{t}"], out[f"hi_{t}"], out[f"lo_{t}"] = sel["close"], sel["cv"], sel["chi"], sel["clo"]
    last = b.groupby("date", sort=True).tail(1).set_index("date")
    out["p_last"], out["v_last"], out["hi_last"], out["lo_last"] = last["close"], last["cv"], last["chi"], last["clo"]
    return out.reset_index().reindex(columns=cols)


def stock_frame(code: str, name: str, daily: pd.DataFrame, bars: pd.DataFrame, calendar: list[pd.Timestamp],
                asof: datetime | None = None) -> pd.DataFrame:
    """Join daily history and intraday bars for one stock; add what each session knew beforehand."""
    if daily.empty:
        return pd.DataFrame()
    d = daily[["date", "open", "close", "high", "low", "volume", "turnover_rate", "amount"]].rename(
        columns={"open": "q_open", "close": "q_close", "high": "q_high", "low": "q_low"}).copy()
    intra = intraday(bars, asof)
    # During a session the daily series may not have today's bar yet: take it from the 30-minute bars
    # (the newest session is unadjusted either way).
    extra = intra[intra["date"] > d["date"].max()]
    if not extra.empty:
        d = pd.concat([d, pd.DataFrame({
            "date": extra["date"], "q_open": extra["p_open"], "q_close": extra["p_last"], "q_high": extra["hi_last"],
            "q_low": extra["lo_last"], "volume": extra["v_last"]})], ignore_index=True)
    d = d.merge(intra, on="date", how="left").sort_values("date").reset_index(drop=True)
    is_index = code in INDEXES
    q = d["q_close"]
    # Offset joining raw intraday prices to the adjusted daily series. The newest bar of an
    # unfinished session is raw (offset 0): forward adjustment only rewrites the past.
    off = (q - d["p_1500"]).round(3)
    off = off.where(off.abs() >= 0.0005, 0.0)
    unfinished = d["p_1500"].isna() & d["p_1000"].notna()
    off = off.where(~unfinished, 0.0) if not is_index else pd.Series(0.0, index=d.index)
    d["off"] = off
    d["prev_close"] = q.shift(1) - off
    d["sum4"], d["sum9"] = q.shift(1).rolling(4).sum(), q.shift(1).rolling(9).sum()
    d["ma5_prev"], d["ma10_prev"] = q.shift(1).rolling(5).mean(), q.shift(1).rolling(10).mean()
    sp = d["ma5_prev"] - d["ma10_prev"]
    d["sp1"], d["sp2"], d["sp3"] = sp, sp.shift(1), sp.shift(2)
    d["prev_volume"] = d["volume"].shift(1)
    d["avg5"] = d["volume"].shift(1).rolling(5).mean()
    d["high20"] = d["q_high"].shift(1).rolling(20).max()
    d["ret5"] = q.shift(1) / q.shift(6) - 1
    d["yday_pct"] = 100 * (q.shift(1) / q.shift(2) - 1)
    fs = (d["volume"] / (d["turnover_rate"] / 100)).replace([np.inf, -np.inf], np.nan)
    d["float_shares"] = fs.where(fs > 0).ffill().shift(1)
    d["n_hist"] = np.arange(len(d))
    lim = 0.0 if is_index else limit_pct(code, name)
    d["limit_up"] = _round_price(d["prev_close"] * (1 + lim / 100)) if lim else np.nan
    closed_lu = (d["p_1500"] >= d["limit_up"] - 0.005) if lim else pd.Series(False, index=d.index)
    d["closed_limit_up"] = closed_lu.fillna(False)
    d["yday_limit_up"] = d["closed_limit_up"].shift(1, fill_value=False).astype(bool)
    run = d["closed_limit_up"].astype(int)
    streak = run.groupby((run == 0).cumsum()).cumsum()
    d["streak_prev"] = streak.shift(1, fill_value=0)
    # Exits: the next market sessions, in the same price basis (no ex-date in between).
    cal = pd.Series(calendar)
    nxt = dict(zip(cal, cal.shift(-1)))
    nxt2 = dict(zip(cal, cal.shift(-2)))
    ok1 = d["date"].shift(-1) == d["date"].map(nxt)
    ok2 = ok1 & (d["date"].shift(-2) == d["date"].map(nxt2))
    same1 = d["off"].shift(-1) == d["off"]
    same2 = same1 & (d["off"].shift(-2) == d["off"])
    # Raw exit prices: with no ex-date in the window, raw / raw is the exact return.
    d["x_t1_open"] = d["p_open"].shift(-1).where(ok1 & same1)
    d["x_t1_close"] = d["p_1500"].shift(-1).where(ok1 & same1)
    d["x_t2_close"] = d["p_1500"].shift(-2).where(ok2 & same2)
    d.insert(0, "code", code)
    d.insert(1, "name", name)
    return d[d["p_1000"].notna()].reset_index(drop=True)


def fetch(code: str, name: str, first_day: date, calendar: list[pd.Timestamp], asof: datetime | None = None,
          bar_count: int = 800) -> pd.DataFrame:
    fq = "" if code in INDEXES else "qfq"
    daily = tencent.daily(code, start=first_day - timedelta(days=HISTORY_DAYS), fq=fq)
    bars = tencent.bars30(code, first_day, count=bar_count)
    f = stock_frame(code, name, daily, bars, calendar, asof)
    if f.empty:
        return f
    f = f[f["date"] >= pd.Timestamp(first_day)]
    return _compact(f)


KEEP = ["code", "name", "date", "p_open", *[f"{k}_{t}" for t in BAR_TIMES for k in ("p", "v", "hi", "lo")],
        "off", "prev_close", "sum4", "sum9", "ma5_prev", "ma10_prev", "sp1", "sp2", "sp3", "prev_volume", "avg5",
        "high20", "ret5", "yday_pct", "float_shares", "n_hist", "limit_up", "closed_limit_up", "yday_limit_up",
        "streak_prev", "x_t1_open", "x_t1_close", "x_t2_close", "q_close", "q_high", "q_low", "q_open", "volume",
        "amount", "turnover_rate"]


SMALL = ("v_", "avg5", "prev_volume", "volume", "amount", "float_shares", "turnover_rate")


def _compact(f: pd.DataFrame) -> pd.DataFrame:
    """Volumes in float32 (prices stay float64: limit-up detection needs the fen exact)."""
    f = f.reindex(columns=KEEP)
    for c in f.columns:
        if c.startswith(SMALL):
            f[c] = f[c].astype("float32")
    return f


def index_calendar(first_day: date, asof: datetime | None = None) -> list[pd.Timestamp]:
    """Session dates from the CSI 300's 30-minute bars (the bars are the calendar)."""
    bars = tencent.bars30("sh000300", first_day)
    if asof is not None:
        bars = bars[bars["datetime"] <= _naive(asof)]
    days = sorted(bars["datetime"].dt.normalize().unique())
    return [pd.Timestamp(d) for d in days]


class IncompleteData(RuntimeError):
    """Too few stocks could be downloaded to score a session fairly (the benchmark is the whole market)."""


def build(codes: list[str], names: dict[str, str], first_day: date, asof: datetime | None = None,
          progress: Callable[[float, str], None] | None = None, workers: int = 16,
          bar_count: int = 800, min_coverage: float = 0.0,
          ) -> tuple[pd.DataFrame, dict[str, pd.DataFrame], list[pd.Timestamp]]:
    """Frames for many stocks (and the two benchmark indices) from `first_day` on.

    Stocks whose download failed (throttling, a paused endpoint) are retried in later passes after a
    pause. With `min_coverage`, raise IncompleteData if fewer than that share of `codes` came through -
    a session scored on part of the market would be wrong, so it is better to fail and retry later."""
    import time as _time

    cal = index_calendar(first_day, asof)
    idx = {c: fetch(c, n, first_day, cal, asof, bar_count) for c, n in INDEXES.items()}
    parts: dict[str, pd.DataFrame] = {}
    empty: set[str] = set()                   # answered, but no bars in the window (suspended, delisted)
    step = 200
    for attempt in range(4):
        todo = [c for c in codes if c not in parts and c not in empty]
        if not todo:
            break
        if attempt:
            _time.sleep(15 * attempt)
            http.reset_breakers()
        for i in range(0, len(todo), step):
            chunk = todo[i:i + step]
            for c, r in zip(chunk, http.pmap(lambda c: fetch(c, names.get(c, c), first_day, cal, asof, bar_count),
                                             chunk, workers=workers)):
                if isinstance(r, pd.DataFrame):
                    if r.empty:
                        empty.add(c)
                    else:
                        parts[c] = r
            if progress:
                done = len(parts) + len(empty)
                tag = f" (retry {attempt})" if attempt else ""
                progress(min(done / max(len(codes), 1), 1.0), f"30-minute and daily bars {done:,}/{len(codes):,}{tag}")
    got = (len(parts) + len(empty)) / max(len(codes), 1)
    if got < min_coverage:
        raise IncompleteData(f"only {got:.0%} of {len(codes):,} stocks downloaded (need {min_coverage:.0%})")
    frame = pd.concat(parts.values(), ignore_index=True) if parts else pd.DataFrame(columns=KEEP)
    return frame, idx, cal


# --------------------------------------------------------------------------- state at a signal time

def state(f: pd.DataFrame, t: str, idx: dict[str, pd.DataFrame] | None = None) -> pd.DataFrame:
    """Every stock-session as it stood at signal time `t` ('open', '10:00', ... '14:30')."""
    k = t.replace(":", "")
    price = f["p_open"] if t == "open" else f[f"p_{k}"]
    adj = price + f["off"]
    s = pd.DataFrame({"code": f["code"], "name": f["name"], "day": f["date"], "time": t, "price": price,
                      "prev_close": f["prev_close"], "open": f["p_open"]})
    s["pct_change"] = 100 * (price / f["prev_close"] - 1)
    s["gap_pct"] = 100 * (f["p_open"] / f["prev_close"] - 1)
    if t == "open":
        cum, hi, lo = pd.Series(np.nan, index=f.index), f["p_open"], f["p_open"]
    else:
        cum, hi, lo = f[f"v_{k}"].astype(float), f[f"hi_{k}"], f[f"lo_{k}"]
    s["high"], s["low"], s["volume"] = hi, lo, cum
    el = ELAPSED[t]
    s["volume_ratio"] = (cum / el) / (f["avg5"] / 240) if el else np.nan
    s["turnover_rate"] = 100 * cum / f["float_shares"]
    s["float_shares"] = f["float_shares"]
    s["float_mcap"] = price * f["float_shares"]
    ma5, ma10 = (f["sum4"] + adj) / 5, (f["sum9"] + adj) / 10
    s["ma5"], s["ma10"] = ma5, ma10
    spread = ma5 - ma10
    s["cross_recent"] = (spread > 0) & ((f["sp1"] <= 0) | (f["sp2"] <= 0) | (f["sp3"] <= 0))
    s["ma_rising"] = (ma5 > f["ma5_prev"]) & (ma10 > f["ma10_prev"])
    projected = cum / (el / 240) if el else np.nan
    s["projected_volume"] = projected
    s["prev_volume"] = f["prev_volume"]
    s["vol_price_up"] = (s["pct_change"] > 0) & (projected > f["prev_volume"])
    s["high20"] = f["high20"]
    s["above_high20"] = adj > f["high20"]
    s["ret5"] = f["ret5"]
    s["yday_pct"], s["yday_limit_up"], s["streak_prev"] = f["yday_pct"], f["yday_limit_up"], f["streak_prev"]
    s["limit_up"] = f["limit_up"]
    s["at_limit"] = price >= f["limit_up"] - 0.005
    s["touched_limit"] = hi >= f["limit_up"] - 0.005
    s["sealed"] = lo >= f["limit_up"] - 0.005
    s["is_st"] = f["name"].fillna("").map(is_st)
    s["board"] = f["code"].map(board)
    s["n_hist"] = f["n_hist"]
    for x in EXITS:
        s[f"ret_{x}"] = f[f"x_{x}"] / price - 1
    s["close"] = f["p_1500"]                                  # the session's close (known only after 15:00)
    s["ret_open_close"] = f["p_1500"] / f["p_open"] - 1      # same session, open -> close
    s["ret_overnight"] = f["x_t1_open"] / f["p_1500"] - 1     # close -> next open
    s["eligible"] = (~s["is_st"]) & s["board"].isin(["Main", "ChiNext", "STAR"]) & (f["n_hist"] >= MIN_HISTORY) & \
        (price > 0) & (f["prev_close"] > 0) & (s["volume"].fillna(1) > 0)
    if idx:
        for c, name in INDEXES.items():
            fi = idx.get(c)
            if fi is None or fi.empty:
                continue
            si = state_index(fi, t)
            tag = "csi300" if c == "sh000300" else "csi1000"
            m = s[["day"]].merge(si.rename(columns=lambda x: f"{tag}_{x}" if x != "day" else x), on="day", how="left")
            for col in m.columns:
                if col != "day":
                    s[col] = m[col].to_numpy()
    return s


def state_index(fi: pd.DataFrame, t: str) -> pd.DataFrame:
    k = t.replace(":", "")
    price = fi["p_open"] if t == "open" else fi[f"p_{k}"]
    out = pd.DataFrame({"day": fi["date"], "pct": 100 * (price / fi["prev_close"] - 1)})
    for x in EXITS:
        out[x] = fi[f"x_{x}"] / price - 1
    return out
