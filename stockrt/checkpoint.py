"""Replay the late-session screen as the market stood at 14:00 and 14:30 of the latest session.

After the close, quote feeds only show end-of-day values, so each stock's state
at a checkpoint is rebuilt from its 1-minute series:

    price, change %      last minute at or before the checkpoint vs previous close
    volume ratio (量比)   (volume so far / minutes elapsed) / (5-session avg volume / 240)
    turnover rate        volume so far / float shares
    float market cap     checkpoint price x float shares
    MA5 / MA10           daily closes before that session + the checkpoint price
    volume & price up    up at the checkpoint, and volume so far paced to a full
                         session above the previous session's volume
    beats the market     change % above the CSI 300's at the same minute
    main funds           East Money minute flow when reachable; otherwise the
                         session's end-of-day net inflow (flagged lower confidence)

To keep requests down, minute data is fetched only for stocks that end-of-day
facts don't already rule out (a stock whose whole-day range never touched
+3..+5% cannot have been inside it at 14:00; cumulative turnover only grows).
If a capture from data/snapshots exists for that minute, its real values are
used instead (scripts/capture_snapshot.py or auto-capture).
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from collections.abc import Callable
from datetime import date, time, timedelta

import numpy as np
import pandas as pd

from . import http, realtime, sample, storage
from .calendar import elapsed_at, now_bj, total_minutes
from .config import SNAPSHOT_DIR
from .screener import BOARDS, ScreenParams, trend_features
from .sources import eastmoney, tencent

log = logging.getLogger(__name__)

CHECKPOINTS = ("14:00", "14:30", "15:00")
CLOSE = "15:00"
GRACE = timedelta(minutes=1)  # let the minute's bar (and the 15:00 closing auction) print
INDEX = "sh000300"


def rule_labels(p: ScreenParams) -> list[tuple[str, str]]:
    def rng(lo, hi, unit=""):
        if lo is not None and hi is not None:
            return f"{lo:g}–{hi:g}{unit}"
        return f"≥ {lo:g}{unit}" if lo is not None else (f"≤ {hi:g}{unit}" if hi is not None else "any")

    return [
        ("rule_not_st", "Non-ST"),
        ("rule_pct", f"Up {rng(p.pct_min, p.pct_max, '%')}"),
        ("rule_vr", f"Volume ratio > {p.volume_ratio_min:g}" if p.volume_ratio_min is not None else "Volume ratio"),
        ("rule_turnover", f"Turnover {rng(p.turnover_min, p.turnover_max, '%')}"),
        ("rule_cross", "MA5 crosses above MA10"),
        ("rule_ma_rising", "MAs rising"),
        ("rule_vol_price", "Volume & price up"),
        ("rule_inflow", "Main funds flowing in"),
        ("rule_outperform", "Beats the CSI 300"),
        ("rule_float_cap", f"Float cap {rng(p.float_mcap_min_bn, p.float_mcap_max_bn, ' bn')}"),
    ]


def _hhmm(s: str) -> time:
    h, m = (int(x) for x in s.split(":"))
    return time(h, m)


def session_day(snap: pd.DataFrame) -> date:
    t = snap["time"].dropna()
    return pd.Timestamp(t.max()).date()


def checkpoint_reached(day: date, hhmm: str) -> bool:
    now = now_bj()
    if day < now.date():
        return True
    return (now - GRACE).time() >= _hhmm(hhmm)


def latest_session_day(mkt: str = "CN") -> date:
    """The session the front page should show, from the official calendar (no network):
    today once the market has opened on a trading day, otherwise the previous trading day."""
    from . import tradingdays

    now = now_bj()
    today = now.date()
    if tradingdays.is_trading_day(mkt, today) is not False and today.weekday() < 5 and \
            now.time() >= time(9, 30):
        return today
    return tradingdays.previous_trading_day(mkt, today) or today - timedelta(days=1)


def _until(hhmm: str) -> str:
    now = now_bj()
    target = now.replace(hour=int(hhmm[:2]), minute=int(hhmm[3:]), second=0, microsecond=0) + GRACE
    mins = max(math.ceil((target - now).total_seconds() / 60), 1)
    h, m = divmod(mins, 60)
    return f"{h} h {m} min" if h else f"{m} min"


def plan(day: date) -> list[dict]:
    """Each checkpoint for `day`: shown, or not yet - and why, in plain words."""
    out = []
    for cp in CHECKPOINTS:
        if checkpoint_reached(day, cp):
            out.append({"checkpoint": cp, "ready": True, "reason": ""})
        elif cp == CLOSE:
            out.append({"checkpoint": cp, "ready": False,
                        "reason": f"Not yet - the market closes at 15:00 Beijing (in {_until(cp)})."})
        else:
            out.append({"checkpoint": cp, "ready": False,
                        "reason": f"Not yet - comes at {cp} Beijing (in {_until(cp)})."})
    return out


def session_note(day: date) -> str:
    """Why the page shows `day` rather than today, when they differ."""
    from .calendar import status

    today = now_bj().date()
    if day == today:
        return ""
    s = status("CN")
    when = pd.Timestamp(day).strftime("%a %d %b")
    if s.phase in ("holiday", "weekend"):
        return f"A-shares are {s.label[0].lower() + s.label[1:]}. Showing the latest session, {when}."
    if s.phase in ("before open", "pre-open"):
        return f"Today's session hasn't started yet (opens 09:30 Beijing). Showing the latest session, {when}."
    return f"Showing the latest session, {when}."


# --------------------------------------------------------------------------- candidate pre-filter

def prefilter(snap: pd.DataFrame, p: ScreenParams) -> pd.DataFrame:
    """Stocks that could have met the snapshot rules at an earlier minute of the same session.

    Only conditions that end-of-day (or now) values can rule out are applied: the
    checkpoint price lies inside the session's high-low range, and cumulative
    turnover can only have been lower earlier in the day."""
    d = snap[~snap["suspended"].fillna(False)]
    if p.exclude_st:
        d = d[~d["is_st"]]
    if set(p.boards) != set(BOARDS):
        d = d[d["board"].isin(p.boards)]
    d = d[d["prev_close"] > 0]
    lo_pct = 100 * (d["low"] / d["prev_close"] - 1)
    hi_pct = 100 * (d["high"] / d["prev_close"] - 1)
    m = pd.Series(True, index=d.index)
    if p.pct_min is not None:
        m &= hi_pct >= p.pct_min
    if p.pct_max is not None:
        m &= lo_pct <= p.pct_max
    if p.turnover_min is not None:
        m &= d["turnover_rate"] >= p.turnover_min
    shares = d["float_mcap"] / d["price"]
    if p.float_mcap_min_bn is not None:
        m &= d["high"] * shares >= p.float_mcap_min_bn * 1e9
    if p.float_mcap_max_bn is not None:
        m &= d["low"] * shares <= p.float_mcap_max_bn * 1e9
    return d[m.fillna(False)]


# --------------------------------------------------------------------------- per-stock reconstruction

def _index_pct(day: date, cps: tuple[str, ...]) -> dict[str, float]:
    out: dict[str, float] = {}
    try:
        mday, pc, bars = tencent.minute_today(INDEX)
    except Exception as e:  # noqa: BLE001 - the rule is then reported as unchecked
        log.warning("index minute data unavailable: %s", e)
        return out
    if mday != day or bars.empty or not pc:
        return out
    for hh in cps:
        b = bars[bars["datetime"].dt.time <= _hhmm(hh)]
        if not b.empty:
            out[hh] = 100 * (float(b["price"].iloc[-1]) / pc - 1)
    return out


def _stock_rows(code: str, q: dict, day: date, cps: tuple[str, ...]) -> list[dict]:
    mday, prev_close, bars = tencent.minute_today(code)
    if mday != day or bars.empty or not prev_close:
        return []
    reg = bars[bars["session"].isin(["AM", "PM"])]
    hist = tencent.daily(code, start=day - timedelta(days=100), fq="qfq")
    hist = hist[hist["date"] < pd.Timestamp(day)]
    if len(hist) < 10 or reg.empty:
        return []
    avg5 = float(hist["volume"].tail(5).mean())
    float_shares = q.get("float_shares") or (q["float_mcap"] / q["price"] if q.get("price") else None)
    last_t = reg["datetime"].iloc[-1].time()
    eod_price = float(reg["price"].iloc[-1])
    complete = last_t >= time(15, 0)
    per_min_base = avg5 / total_minutes("CN") if avg5 else np.nan
    rows = []
    for hh in cps:
        t = _hhmm(hh)
        if last_t < t:
            continue  # session hasn't reached this checkpoint
        b = reg[reg["datetime"].dt.time <= t]
        price = float(b["price"].iloc[-1])
        cum_vol, cum_amt = float(b["volume"].sum()), float(b["amount"].sum())
        elapsed = elapsed_at("CN", hh)
        trend = trend_features(pd.Series(hist["close"].tolist() + [price]))
        hi, lo = float(b["price"].max()), float(b["price"].min())
        rows.append({
            "code": code, "checkpoint": hh, "trade_date": day, "price": price,
            "pct_change": 100 * (price / prev_close - 1),
            "volume_ratio": (cum_vol / elapsed) / per_min_base if per_min_base else np.nan,
            "turnover_rate": 100 * cum_vol / float_shares if float_shares else np.nan,
            "float_mcap": price * float_shares if float_shares else np.nan,
            "volume": cum_vol, "amount": cum_amt,
            "vwap": cum_amt / cum_vol if cum_vol else np.nan,
            "range_position": (price - lo) / (hi - lo) if hi > lo else 0.5,
            "from_high": price / hi - 1,
            **trend,
            "prev_volume": float(hist["volume"].iloc[-1]),
            "projected_volume": cum_vol / (elapsed / total_minutes("CN")),
            "after": (eod_price / price - 1),
            "after_label": "to close" if complete else "to latest",
            "source": "minute data",
        })
    return rows


def _captured(day: date, hh: str) -> pd.DataFrame | None:
    path = SNAPSHOT_DIR / day.isoformat() / f"{hh.replace(':', '')}_cn_market.csv"
    if not path.exists():
        return None
    try:
        return pd.read_csv(path, encoding="utf-8-sig", dtype={"code": str})
    except Exception as e:  # noqa: BLE001
        log.warning("unreadable capture %s: %s", path, e)
        return None


def _captured_rows(cap: pd.DataFrame, p: ScreenParams, day: date, hh: str, codes: list[str]) -> list[dict]:
    """Rows from a real capture at that minute: quote fields as they were, history from daily bars."""
    cap = cap[cap["code"].isin(codes)]
    rows = []

    def one(r: dict) -> dict | None:
        hist = tencent.daily(r["code"], start=day - timedelta(days=100), fq="qfq")
        hist = hist[hist["date"] < pd.Timestamp(day)]
        if len(hist) < 10:
            return None
        elapsed = elapsed_at("CN", hh)
        hi, lo, price = r.get("high"), r.get("low"), r["price"]
        return {
            "code": r["code"], "checkpoint": hh, "trade_date": day, "price": price,
            "pct_change": r["pct_change"], "volume_ratio": r["volume_ratio"], "turnover_rate": r["turnover_rate"],
            "float_mcap": r["float_mcap"], "volume": r["volume"], "amount": r["amount"], "vwap": r.get("vwap"),
            "range_position": (price - lo) / (hi - lo) if hi and lo and hi > lo else 0.5,
            "from_high": price / hi - 1 if hi else 0.0,
            **trend_features(pd.Series(hist["close"].tolist() + [price])),
            "prev_volume": float(hist["volume"].iloc[-1]),
            "projected_volume": r["volume"] / (elapsed / total_minutes("CN")),
            "main_net_at": r.get("main_net"), "after": np.nan, "after_label": "",
            "source": "captured snapshot",
        }

    for res in http.pmap(one, cap.to_dict("records"), workers=8):
        if isinstance(res, dict):
            rows.append(res)
    return rows


# --------------------------------------------------------------------------- rules and score

def apply_rules(df: pd.DataFrame, p: ScreenParams, index_pct: float | None) -> pd.DataFrame:
    d = df.copy()

    def between(s, lo, hi):
        m = s.notna()
        if lo is not None:
            m &= s >= lo
        if hi is not None:
            m &= s <= hi
        return m

    d["rule_not_st"] = ~d["is_st"].fillna(False).astype(bool)
    d["rule_pct"] = between(d["pct_change"], p.pct_min, p.pct_max)
    d["rule_vr"] = d["volume_ratio"] > (p.volume_ratio_min if p.volume_ratio_min is not None else -np.inf)
    d["rule_turnover"] = between(d["turnover_rate"], p.turnover_min, p.turnover_max)
    d["rule_cross"] = (d["ma5"] > d["ma10"]) & d["cross_days_ago"].notna() & \
        (d["cross_days_ago"] < max(p.cross_within, 1))
    d["rule_ma_rising"] = (d["ma5"] > d["ma5_prev"]) & (d["ma10"] > d["ma10_prev"])
    d["rule_vol_price"] = (d["pct_change"] > 0) & (d["projected_volume"] > d["prev_volume"])
    d["rule_inflow"] = d["main_net"] > 0
    d["rule_outperform"] = d["pct_change"] > index_pct if index_pct is not None else False
    d["rule_float_cap"] = between(d["float_mcap"] / 1e9, p.float_mcap_min_bn, p.float_mcap_max_bn)
    rule_cols = [c for c, _ in rule_labels(p)]
    d["rules_met"] = d[rule_cols].fillna(False).astype(int).sum(axis=1)
    d["passes"] = d["rules_met"] == len(rule_cols)
    labels = dict(rule_labels(p))
    d["missed"] = d[rule_cols].apply(lambda r: ", ".join(labels[c] for c in rule_cols if not bool(r[c])), axis=1)
    return d


def _clip(x):
    return np.clip(x, 0.0, 1.0)


def score(df: pd.DataFrame, index_pct: float | None) -> pd.DataFrame:
    """Signal-quality score 0-100 from six transparent components (each 0-1).

    trend 25%      fresh golden cross (today best), both MAs rising
    volume 20%     volume ratio sweet spot 2-4 (thin <1 and overheated >6 score lower)
    money 20%      main-fund net inflow as a share of value traded
    intraday 20%   above VWAP, near the day's high, not fading from it
    relative 10%   lead over the CSI 300
    liquidity 5%   value traded so far (deeper books, harder to push around)
    """
    d = df.copy()
    cross = d["cross_days_ago"].map(lambda x: {0: 1.0, 1: 0.8, 2: 0.6}.get(x, 0.4) if pd.notna(x) else 0.0)
    cross = cross.where(d["ma5"] > d["ma10"], 0.0)
    d["s_trend"] = 0.6 * cross + 0.2 * (d["ma5"] > d["ma5_prev"]) + 0.2 * (d["ma10"] > d["ma10_prev"])
    vr = d["volume_ratio"].fillna(0)
    d["s_volume"] = np.select([vr < 1, vr < 2, vr <= 4, vr <= 8], [vr * 0.5, 0.5 + 0.5 * (vr - 1), 1.0,
                              1.0 - 0.125 * (vr - 4)], 0.4)
    ratio = (d["main_net"] / d["amount_for_flow"]).fillna(0)
    d["s_money"] = _clip(0.5 + ratio * 5)  # +10% of turnover -> 1.0, -10% -> 0.0
    vs_vwap = (d["price"] / d["vwap"] - 1).fillna(0)
    d["s_intraday"] = (0.4 * _clip(0.5 + vs_vwap * 50) + 0.4 * d["range_position"].fillna(0.5)
                       + 0.2 * _clip(1 + d["from_high"].fillna(0) / 0.03))
    excess = d["pct_change"] - (index_pct if index_pct is not None else 0)
    d["s_relative"] = _clip(excess / 5)
    d["s_liquidity"] = _clip((np.log10(d["amount"].clip(lower=1)) - 7) / 2)  # 10M -> 0, 1B -> 1
    d["score"] = 100 * (0.25 * d["s_trend"] + 0.20 * d["s_volume"] + 0.20 * d["s_money"]
                        + 0.20 * d["s_intraday"] + 0.10 * d["s_relative"] + 0.05 * d["s_liquidity"])
    return d


# --------------------------------------------------------------------------- orchestration

def _cache_key(day: date, p: ScreenParams, cps: tuple[str, ...]) -> str:
    h = hashlib.md5(json.dumps(p.to_dict(), sort_keys=True).encode()).hexdigest()[:8]
    return f"{day.isoformat()}_{'-'.join(c.replace(':', '') for c in cps)}_{h}"


def _result_key(day: date, p: ScreenParams, reached: tuple[str, ...]) -> str:
    return "result_" + _cache_key(day, p, reached)


def save_result(res: dict, p: ScreenParams) -> None:
    if not res["frames"]:
        return
    key = _result_key(res["day"], p, tuple(res["frames"]))
    rows = pd.concat([f.assign(checkpoint=c) for c, f in res["frames"].items()], ignore_index=True)
    storage.save_frame("checkpoints", key, rows)
    storage.save_json("checkpoints", key, {"day": res["day"].isoformat(), "checkpoints": list(res["frames"]),
                                           "index_pct": res["index_pct"], "notes": res["notes"],
                                           "candidates": res["candidates"]})


def load_result(day: date, p: ScreenParams | None = None) -> dict | None:
    """A finished result for `day` at every checkpoint reached so far, without touching the market:
    the disk cache, else the bundled sample when it covers that same session."""
    p = p or ScreenParams()
    reached = tuple(c for c in CHECKPOINTS if checkpoint_reached(day, c))
    if not reached:
        return None
    key = _result_key(day, p, reached)
    meta = storage.load_json("checkpoints", key)
    rows = storage.load_frame("checkpoints", key)
    if meta and rows is not None:
        frames = {c: rows[rows["checkpoint"] == c].reset_index(drop=True) for c in meta["checkpoints"]}
        return {"day": day, "frames": frames, "index_pct": meta["index_pct"], "pending": [],
                "notes": meta["notes"], "candidates": meta["candidates"], "source": "saved"}
    saved = sample.picks() if sample.available() else None
    if saved and saved["day"] == day and saved["params"] == p.to_dict() and tuple(saved["frames"]) == reached:
        return {**saved, "source": "bundled"}
    return None


def evaluate(snap: pd.DataFrame, p: ScreenParams | None = None, cps: tuple[str, ...] = CHECKPOINTS,
             progress: Callable[[float, str], None] | None = None, use_cache: bool = True) -> dict:
    """Screen the latest session at each checkpoint. Returns a dict with per-checkpoint frames,
    the index move at each checkpoint, candidate counts and data-quality notes."""
    p = p or ScreenParams()
    if sample.is_sample():
        saved = sample.picks()
        if saved and saved["params"] == p.to_dict() and tuple(saved["frames"]) == cps:
            return saved
    day = session_day(snap)
    reached = tuple(c for c in cps if checkpoint_reached(day, c))
    pending = [c for c in cps if c not in reached]
    out = {"day": day, "frames": {}, "index_pct": {}, "pending": pending, "notes": [], "candidates": 0}
    if not reached:
        return out
    idx = _index_pct(day, reached)
    out["index_pct"] = idx
    cands = prefilter(snap, p)
    out["candidates"] = len(cands)
    quotes = {r["code"]: r for r in cands.to_dict("records")}
    codes = list(quotes)
    # A checkpoint's picture is fixed once it has passed, so results are cached as soon as they exist.
    key = _cache_key(day, p, reached)
    cached = storage.load_frame("checkpoints", key) if use_cache else None

    if cached is not None:
        rows = cached
    else:
        recs: list[dict] = []
        captured = {hh: _captured(day, hh) for hh in reached}
        minute_cps = tuple(hh for hh in reached if captured[hh] is None)
        for hh in reached:
            if captured[hh] is not None:
                recs += _captured_rows(captured[hh], p, day, hh, codes)
        if minute_cps:
            step = 40
            for i in range(0, len(codes), step):
                chunk = codes[i:i + step]
                for res in http.pmap(lambda c: _stock_rows(c, quotes[c], day, minute_cps), chunk, workers=10):
                    if isinstance(res, list):
                        recs += res
                if progress:
                    progress(min(i + step, len(codes)) / max(len(codes), 1),
                             f"Rebuilding {min(i + step, len(codes))}/{len(codes)} stocks at {', '.join(minute_cps)}")
        rows = pd.DataFrame(recs)
        if not rows.empty:
            rows = _attach_flow(rows, day)
        if not rows.empty:
            storage.save_frame("checkpoints", key, rows)

    if rows.empty:
        out["notes"].append("No stock could be rebuilt for this session.")
        return out
    meta = pd.DataFrame([{"code": c, "name": q["name"], "board": q["board"], "is_st": q["is_st"],
                          "eod_pct": q["pct_change"]} for c, q in quotes.items()])
    rows = rows.drop(columns=[c for c in ("name", "board", "is_st", "eod_pct") if c in rows]).merge(meta, on="code")
    for hh in reached:
        f = rows[rows["checkpoint"] == hh]
        f = score(apply_rules(f, p, idx.get(hh)), idx.get(hh))
        out["frames"][hh] = f.sort_values(["rules_met", "score"], ascending=False).reset_index(drop=True)
    if (rows["flow_at"] == "end of day").any():
        out["notes"].append("Main-fund flow at 14:00 / 14:30 isn't available for this session from your network; "
                            "the session's end-of-day net inflow is used instead (confidence: medium).")
    if idx == {}:
        out["notes"].append("CSI 300 minute data unavailable - 'beats the market' could not be checked.")
    save_result(out, p)
    return out


def _attach_flow(rows: pd.DataFrame, day: date) -> pd.DataFrame:
    """main_net at the checkpoint: capture value, East Money minute flow, else end-of-day (flagged)."""
    rows = rows.copy()
    rows["main_net"] = rows.get("main_net_at")
    rows["flow_at"] = np.where(rows["main_net"].notna(), "checkpoint", None)
    rows["amount_for_flow"] = rows["amount"]
    need = rows[rows["main_net"].isna()]["code"].unique().tolist()
    if not need:
        return rows
    minute_flow: dict[str, pd.DataFrame] = {}
    try:
        first = eastmoney.intraday_flow(need[0])
        if not first.empty and first["datetime"].dt.date.iloc[-1] == day:
            minute_flow[need[0]] = first
            for c, res in zip(need[1:], http.pmap(eastmoney.intraday_flow, need[1:], workers=8)):
                if isinstance(res, pd.DataFrame) and not res.empty:
                    minute_flow[c] = res
    except Exception as e:  # noqa: BLE001 - East Money often unreachable outside mainland China
        log.info("minute money flow unavailable: %s", e)
    for i, r in rows[rows["main_net"].isna()].iterrows():
        mf = minute_flow.get(r["code"])
        if mf is not None:
            at = mf[mf["datetime"].dt.time <= _hhmm(r["checkpoint"])]
            if not at.empty:
                rows.at[i, "main_net"] = float(at["main_net"].iloc[-1])
                rows.at[i, "flow_at"] = "checkpoint"
    still = rows[rows["main_net"].isna()]["code"].unique().tolist()
    if still:
        eod = realtime.main_flow(still)
        if not eod.empty:
            m = eod.set_index("code")["main_net"]
            mask = rows["main_net"].isna() & rows["code"].isin(m.index)
            rows.loc[mask, "main_net"] = rows.loc[mask, "code"].map(m)
            rows.loc[mask, "flow_at"] = "end of day"
            # At 15:00 (the close) an end-of-day flow IS the checkpoint value.
            at_close = mask & (rows["checkpoint"] == CLOSE) & (day < now_bj().date() or now_bj().hour >= 15)
            rows.loc[at_close, "flow_at"] = "checkpoint"
            # Compare an end-of-day flow with end-of-day value traded.
            amt = realtime.quotes(still).set_index("code")["amount"]
            rows.loc[mask, "amount_for_flow"] = rows.loc[mask, "code"].map(amt)
    rows["main_net"] = pd.to_numeric(rows["main_net"], errors="coerce")
    return rows


TIERS = {
    "A": "Met every rule at every checkpoint so far",
    "B": "Met every rule at the latest checkpoint",
    "C": "Met every rule earlier, not at the latest checkpoint",
    "D": "One rule narrowly missed at the latest checkpoint",
}


def close_miss(r: pd.Series, p: ScreenParams) -> bool:
    """Is the single missed rule only narrowly missed? (A limit-up stock 'misses' +3-5% by a mile.)"""
    missed = [c for c, _ in rule_labels(p) if not bool(r[c])]
    if len(missed) != 1:
        return False
    rule = missed[0]

    def near(v, lo, hi, tol):
        if pd.isna(v):
            return False
        return (lo is None or v >= lo - tol) and (hi is None or v <= hi + tol)

    if rule == "rule_not_st":
        return False  # ST is a hard exclusion
    if rule == "rule_pct":
        return near(r["pct_change"], p.pct_min, p.pct_max, 1.0)
    if rule == "rule_turnover":
        return near(r["turnover_rate"], p.turnover_min, p.turnover_max, 1.0)
    if rule == "rule_vr":
        return pd.notna(r["volume_ratio"]) and r["volume_ratio"] >= (p.volume_ratio_min or 0) - 0.2
    if rule == "rule_float_cap":
        lo = p.float_mcap_min_bn * 0.85 if p.float_mcap_min_bn is not None else None
        hi = p.float_mcap_max_bn * 1.15 if p.float_mcap_max_bn is not None else None
        return near(r["float_mcap"] / 1e9, lo, hi, 0)
    return True  # yes/no rules (cross, slopes, volume-price, inflow, beats market)


def ranked(result: dict, p: ScreenParams | None = None, top: int = 30) -> pd.DataFrame:
    """One ranked list across checkpoints: tier first (persistence = reliability), then score.

    A needs every rule at every checkpoint reached (at least two). Tier D holds stocks one rule
    short at the latest checkpoint, and only when that rule was narrowly missed."""
    p = p or ScreenParams()
    frames = result["frames"]
    if not frames:
        return pd.DataFrame()
    cps = [c for c in CHECKPOINTS if c in frames]
    latest = cps[-1]
    by_cp = {c: frames[c].set_index("code") for c in cps}
    passes = {c: set(by_cp[c].index[by_cp[c]["passes"].astype(bool)]) for c in cps}
    first = by_cp[cps[0]]
    rows = []
    for code in dict.fromkeys(code for c in reversed(cps) for code in by_cp[c].index):
        passed_at = [c for c in cps if code in passes[c]]
        r = next(by_cp[c].loc[code] for c in reversed(cps) if code in by_cp[c].index)
        if len(cps) >= 2 and len(passed_at) == len(cps):
            tier = "A"
        elif latest in passed_at:
            tier = "B"
        elif passed_at:
            tier = "C"
        elif code in by_cp[latest].index and close_miss(by_cp[latest].loc[code], p):
            tier = "D"
        else:
            continue
        row = {"tier": tier, "code": code, **r.to_dict()}
        for c in cps:
            row[f"at_{c}"] = "✓" if code in passes[c] else ("·" if code in by_cp[c].index else "")
        if code in first.index:  # hindsight from the first checkpoint, never used for ranking
            row["after_first"] = first.loc[code].get("after", np.nan)
            row["after_first_label"] = first.loc[code].get("after_label", "")
        rows.append(row)
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out = out.sort_values(["tier", "score"], ascending=[True, False]).head(top).reset_index(drop=True)
    out.insert(0, "rank", range(1, len(out) + 1))
    out["confidence"] = np.where(out["flow_at"] == "end of day", "Medium", "High")
    return out
