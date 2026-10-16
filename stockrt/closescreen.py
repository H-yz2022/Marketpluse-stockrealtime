"""收盘选股 - the after-close screen, run once a day from 16:00 Beijing:

    请列出成交量翻倍，突破压力位，换手率＞3%，站上牛熊线的非ST股票

How each term is read (the usual technical-analysis meanings; the two choices are switchable):

    成交量翻倍   today's volume at least 2x the previous session's
    突破压力位   close above the highest high of the previous 20 sessions (前20日最高价; or 60)
    换手率＞3%   turnover above 3%
    站上牛熊线   close above the 250-day moving average (年线, the usual 牛熊分界线; or 120-day 半年线)
    非ST         ST / *ST excluded

Everything comes from the session's end-of-day volume book (volume, close, turnover - saved from the
real-time snapshot after the close) plus daily history for the shortlisted stocks, so a session can be
screened any time later. Daily history is forward-adjusted, and the day's own adjusted close is used,
so dividends never fake a breakout.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta

import numpy as np
import pandas as pd

from . import archive, http
from .sources import tencent
from .symbols import board, is_st

QUERY_ZH = "请列出成交量翻倍，突破压力位，换手率＞3%，站上牛熊线的非ST股票"
QUERY_EN = ("Non-ST stocks whose **volume doubled**, that **broke through resistance**, with **turnover above 3%**, "
            "closing **above the bull-bear line**.")
VOLUME_MULTIPLE = 2.0
TURNOVER_MIN = 3.0
RESISTANCE = {20: "previous 20 sessions' high (前20日最高价)", 60: "previous 60 sessions' high (前60日最高价)"}
BULL_BEAR = {250: "250-day MA (年线)", 120: "120-day MA (半年线)"}
BOARDS = ("Main", "ChiNext", "STAR", "BSE")


def rules(resistance: int = 20, ma: int = 250) -> list[tuple[str, str, str]]:
    """(column, label, definition) for the chosen readings."""
    return [
        ("r_not_st", "非ST · Non-ST", "ST / *ST stocks are excluded."),
        ("r_volume", "成交量翻倍 · Volume doubled", f"Volume at least {VOLUME_MULTIPLE:g}x the previous session's."),
        ("r_breakout", "突破压力位 · Broke resistance", f"Close above the {RESISTANCE[resistance]}."),
        ("r_turnover", "换手率＞3% · Turnover > 3%", f"Turnover above {TURNOVER_MIN:g}% of float shares."),
        ("r_bullbear", "站上牛熊线 · Above the bull-bear line", f"Close above the {BULL_BEAR[ma]}."),
    ]


def shortlist(book: pd.DataFrame, prev: pd.DataFrame | None) -> pd.DataFrame:
    """Stocks the end-of-day facts don't rule out: non-ST, traded, turnover > 3%, and - when the previous
    session's book exists - volume at least double. (Without it, history decides the volume rule.)"""
    d = book[(book["volume"] > 0) & (book["close"] > 0)].copy()
    d["is_st"] = d["name"].fillna("").map(is_st)
    d = d[~d["is_st"] & (d["turnover_rate"] > TURNOVER_MIN) & d["code"].map(board).isin(BOARDS)]
    if prev is not None and not prev.empty:
        pv = prev.drop_duplicates("code").set_index("code")["volume"]
        d = d[d["volume"] >= VOLUME_MULTIPLE * d["code"].map(pv).fillna(np.inf)]
    return d


def features(code: str, day: date, close_raw: float, volume: float) -> dict | None:
    """Resistance and moving averages from forward-adjusted daily bars, in the session's own price basis."""
    bars = tencent.daily(code, start=day - timedelta(days=420), end=day, fq="qfq")
    if bars.empty:
        return None
    hist = bars[bars["date"] < pd.Timestamp(day)]
    on_day = bars[bars["date"] == pd.Timestamp(day)]
    close = float(on_day["close"].iloc[0]) if not on_day.empty else close_raw
    if len(hist) < 20:
        return None
    closes = hist["close"].to_numpy()

    def ma(n: int) -> float:
        return float((closes[-(n - 1):].sum() + close) / n) if len(closes) >= n - 1 else np.nan

    return {"code": code, "close_adj": close, "prev_volume": float(hist["volume"].iloc[-1]),
            "res20": float(hist["high"].tail(20).max()), "res60": float(hist["high"].tail(60).max()),
            "ma120": ma(120), "ma250": ma(250), "sessions": len(hist), "vol_x": volume / float(hist["volume"].iloc[-1])
            if hist["volume"].iloc[-1] else np.nan}


def apply(d: pd.DataFrame, resistance: int = 20, ma: int = 250) -> pd.DataFrame:
    """Rule columns, rules met and what was missed, for the chosen readings."""
    d = d.copy()
    d["r_not_st"] = ~d["is_st"].fillna(False).astype(bool)
    d["r_volume"] = d["vol_x"] >= VOLUME_MULTIPLE
    d["r_breakout"] = d["close_adj"] > d[f"res{resistance}"]
    d["r_turnover"] = d["turnover_rate"] > TURNOVER_MIN
    d["r_bullbear"] = d["close_adj"] > d[f"ma{ma}"]
    rs = rules(resistance, ma)
    cols = [c for c, _, _ in rs]
    d["rules_met"] = d[cols].fillna(False).astype(bool).sum(axis=1)
    d["passes"] = d["rules_met"] == len(cols)
    d["missed"] = [", ".join(lbl.split(" · ")[0] for (c, lbl, _), ok in zip(rs, row) if not ok)
                   for row in d[cols].fillna(False).astype(bool).itertuples(index=False)]
    d["above_resistance_pct"] = 100 * (d["close_adj"] / d[f"res{resistance}"] - 1)
    d["above_ma_pct"] = 100 * (d["close_adj"] / d[f"ma{ma}"] - 1)
    return d.sort_values(["passes", "rules_met", "vol_x"], ascending=False).reset_index(drop=True)


def run(day: date, book: pd.DataFrame | None = None, progress: Callable[[float, str], None] | None = None,
        workers: int = 12) -> dict:
    """Screen one session (from its volume book) and archive it. Returns {'day', 'frame', 'notes'}."""
    notes = []
    if book is None:
        got = archive.load_volume_history("CN", before=day + timedelta(days=1), n=1)
        if not got or got[0][0] != day:
            raise FileNotFoundError(f"no volume book for {day}")
        book = got[0][1]
    prev = archive.load_volumes("CN", before=day)
    if prev is None:
        notes.append("No volume book for the previous session: the volume rule is checked from daily bars only.")
    cands = shortlist(book, prev[1] if prev else None)
    rows = []
    recs = cands.to_dict("records")
    for i in range(0, len(recs), 60):
        chunk = recs[i:i + 60]
        for r, f in zip(chunk, http.pmap(lambda r: features(r["code"], day, r["close"], r["volume"]), chunk,
                                         workers=workers)):
            if isinstance(f, dict):
                rows.append({**{k: r.get(k) for k in ("code", "name", "close", "pct_change", "volume", "amount",
                                                      "turnover_rate", "float_shares", "is_st")}, **f})
        if progress:
            progress(min(i + 60, len(recs)) / max(len(recs), 1), f"Daily history {min(i + 60, len(recs))}/{len(recs)}")
    frame = pd.DataFrame(rows)
    if not frame.empty:
        frame["float_mcap"] = frame["close"] * frame["float_shares"]
    archive.save_screen(day, frame, {"shortlisted": len(cands), "notes": notes})
    return {"day": day, "frame": frame, "notes": notes, "shortlisted": len(cands)}
