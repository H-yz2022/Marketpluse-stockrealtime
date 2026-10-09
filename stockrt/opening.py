"""Opening-auction research (集合竞价选股) and the volume-surge query - A-shares.

How the auction shows up in the data (measured live on 9 Oct 2026, 09:22):
* 09:15-09:25  `price` is the indicative matched price, best bid = best ask = that price, and the
               best-bid volume is the indicative matched volume; `open` and `volume` are still 0.
* 09:25-09:30  the auction has matched: `open` is the opening price, `volume`/`amount` are the
               auction's matched volume and value (nothing else has traded yet).
* from 09:30   `volume` includes continuous trading; the auction itself is the first (09:30) bar
               of the minute series, so its volume is read from there for the shortlisted stocks.
Indices do not print during the auction (their price stays at the previous close until 09:25).

"Yesterday" comes from the end-of-day volume book (stockrt.archive.load_volumes).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from . import http
from .sources import tencent
from .symbols import board


@dataclass
class AuctionParams:
    exclude_st: bool = True
    gap_min: float | None = 2.0          # opening gap vs previous close, %
    gap_max: float | None = 7.0
    skip_limit_up_open: bool = True      # 一字板: no shares available at the open
    amount_min_m: float | None = 10.0    # auction value traded, CNY millions (竞价金额)
    vs_yday_min_pct: float | None = 5.0  # auction volume as % of yesterday's whole-day volume (竞价量占比)
    turnover_min: float | None = 0.3     # auction volume / float shares, % (竞价换手率)
    cap_min_bn: float | None = 3.0       # float market cap, CNY billions
    cap_max_bn: float | None = 50.0
    yday_up: bool = False                # yesterday closed higher
    yday_limit_up: bool = False          # yesterday closed at the daily limit (连板接力)
    holding: bool = False                # after the open: price still at or above the opening price

    def to_dict(self) -> dict:
        return asdict(self)


# (rule column, label, why it matters) - shown on the page next to the screen.
RULES = [
    ("a_not_st", "Non-ST", "ST stocks have a 5% limit and delisting risk - a different game."),
    ("a_gap", "Gap {gap}", "A moderate gap up shows overnight demand. Above ~7% the risk of a fade (and of chasing) "
                           "rises sharply; below 2% the signal is weak."),
    ("a_not_limit", "Not opening at limit-up", "A one-price limit-up open (一字板) cannot be bought, so it is "
                                              "useless for a screen."),
    ("a_amount", "Auction value ≥ ¥{amount}M", "Money actually committed in the auction. Tiny auctions are easy to "
                                               "push around and say little."),
    ("a_vs_yday", "Auction volume ≥ {vs_yday}% of yesterday", "The classic 竞价爆量: when the auction alone "
                                                              "trades a large slice of yesterday's whole day, "
                                                              "interest is unusual."),
    ("a_turnover", "Auction turnover ≥ {turnover}%", "Auction volume relative to float shares - comparable across "
                                                     "big and small caps."),
    ("a_cap", "Float cap {cap}", "Mid caps move on flows; mega caps rarely gap, micro caps are noisy."),
    ("a_yday_up", "Yesterday closed up", "Continuation after an up day is more reliable than a gap after a fall."),
    ("a_yday_limit", "Yesterday limit-up (接力)", "Relay plays on yesterday's limit-up names - high risk, high "
                                                  "attention."),
    ("a_holding", "Gap still holding", "After 09:30: price at or above the open - the gap is not being sold."),
]


def _fmt_range(lo, hi, unit=""):
    if lo is not None and hi is not None:
        return f"{lo:g}–{hi:g}{unit}"
    return f"≥ {lo:g}{unit}" if lo is not None else (f"≤ {hi:g}{unit}" if hi is not None else "any")


def active_rules(p: AuctionParams, after_open: bool) -> list[tuple[str, str, str]]:
    on = {
        "a_not_st": p.exclude_st, "a_gap": p.gap_min is not None or p.gap_max is not None,
        "a_not_limit": p.skip_limit_up_open, "a_amount": p.amount_min_m is not None,
        "a_vs_yday": p.vs_yday_min_pct is not None, "a_turnover": p.turnover_min is not None,
        "a_cap": p.cap_min_bn is not None or p.cap_max_bn is not None, "a_yday_up": p.yday_up,
        "a_yday_limit": p.yday_limit_up, "a_holding": p.holding and after_open,
    }
    fill = {"gap": _fmt_range(p.gap_min, p.gap_max, "%"), "amount": f"{p.amount_min_m or 0:g}",
            "vs_yday": f"{p.vs_yday_min_pct or 0:g}", "turnover": f"{p.turnover_min or 0:g}",
            "cap": _fmt_range(p.cap_min_bn, p.cap_max_bn, " bn")}
    return [(c, lbl.format(**fill), why) for c, lbl, why in RULES if on[c]]


def limit_pct(code: str, name: str = "") -> float:
    """Daily price limit in %: 10 main board, 5 ST, 20 ChiNext / STAR, 30 Beijing."""
    b = board(code)
    if b == "BSE":
        return 30.0
    if b in ("ChiNext", "STAR"):
        return 20.0
    return 5.0 if "ST" in (name or "").upper() else 10.0


def _with_yesterday(snap: pd.DataFrame, book: pd.DataFrame) -> pd.DataFrame:
    y = book[["code", "volume", "amount", "pct_change"]].rename(
        columns={"volume": "yday_volume", "amount": "yday_amount", "pct_change": "yday_pct"})
    d = snap.merge(y, on="code", how="left")
    d["yday_limit"] = [limit_pct(c, n) for c, n in zip(d["code"], d["name"].fillna(""))]
    d["yday_limit_up"] = d["yday_pct"] >= d["yday_limit"] - 0.2
    return d


def volume_surge(snap: pd.DataFrame, book: pd.DataFrame, multiple: float = 5.0, min_amount: float = 0.0,
                 exclude_st: bool = True) -> pd.DataFrame:
    """Stocks whose volume today is at least `multiple` x yesterday's whole-day volume."""
    d = _with_yesterday(snap, book)
    d = d[(d["yday_volume"] > 0) & (d["volume"] > 0)]
    if exclude_st and "is_st" in d:
        d = d[~d["is_st"].fillna(False)]
    d = d.assign(vol_multiple=d["volume"] / d["yday_volume"])
    d = d[(d["vol_multiple"] >= multiple) & (d["amount"].fillna(0) >= min_amount)]
    return d.sort_values("vol_multiple", ascending=False).reset_index(drop=True)


def auction_frame(snap: pd.DataFrame, book: pd.DataFrame, phase: str) -> pd.DataFrame:
    """One row per stock with the auction's price, gap, volume and value.

    phase: 'indicative' (09:15-09:25), 'result' (09:25-09:30) or 'after_open' (volume filled later)."""
    d = _with_yesterday(snap, book)
    if phase == "indicative":
        crossed = (d["bid1"] > 0) & (d["bid1"] == d["ask1"])
        d["auction_price"] = np.where(crossed, d["bid1"], d["price"])
        d["auction_volume"] = np.where(crossed, d["bid1_volume"], np.nan)
    elif phase == "result":
        d["auction_price"] = d["open"].where(d["open"] > 0, d["price"])
        d["auction_volume"] = d["volume"]
    else:
        d["auction_price"] = d["open"].where(d["open"] > 0)
        d["auction_volume"] = np.nan
    d["auction_amount"] = d["auction_volume"] * d["auction_price"]
    d["gap_pct"] = 100 * (d["auction_price"] / d["prev_close"] - 1)
    d["limit_up_open"] = d["limit_up"].notna() & (d["auction_price"] >= d["limit_up"] - 1e-6)
    return d


def fill_auction_volumes(d: pd.DataFrame, codes: list[str], progress=None, workers: int = 10) -> pd.DataFrame:
    """After 09:30: read each shortlisted stock's auction volume from its first (09:30) minute bar."""

    def one(code: str) -> tuple[str, float, float] | None:
        _, _, bars = tencent.minute_today(code)
        first = bars[bars["datetime"].dt.strftime("%H:%M") == "09:30"] if not bars.empty else bars
        if first.empty:
            return None
        return code, float(first["volume"].iloc[0]), float(first["amount"].iloc[0])

    got: dict[str, tuple[float, float]] = {}
    for i in range(0, len(codes), 40):
        for r in http.pmap(one, codes[i:i + 40], workers=workers):
            if isinstance(r, tuple):
                got[r[0]] = (r[1], r[2])
        if progress:
            progress(min(i + 40, len(codes)) / max(len(codes), 1),
                     f"Reading auction volumes {min(i + 40, len(codes))}/{len(codes)}")
    d = d.copy()
    d["auction_volume"] = d["code"].map(lambda c: got.get(c, (np.nan, np.nan))[0])
    d["auction_amount"] = d["code"].map(lambda c: got.get(c, (np.nan, np.nan))[1])
    return d


def cheap_mask(d: pd.DataFrame, p: AuctionParams) -> pd.Series:
    """Rules that need no auction volume - used to shortlist before reading minute bars."""
    m = d["auction_price"].notna() & (d["prev_close"] > 0)
    if p.exclude_st:
        m &= ~d["is_st"].fillna(False)
    if p.gap_min is not None:
        m &= d["gap_pct"] >= p.gap_min
    if p.gap_max is not None:
        m &= d["gap_pct"] <= p.gap_max
    if p.skip_limit_up_open:
        m &= ~d["limit_up_open"]
    cap = d["float_mcap"] / 1e9
    if p.cap_min_bn is not None:
        m &= cap >= p.cap_min_bn
    if p.cap_max_bn is not None:
        m &= cap <= p.cap_max_bn
    if p.yday_up:
        m &= d["yday_pct"] > 0
    if p.yday_limit_up:
        m &= d["yday_limit_up"].fillna(False)
    return m.fillna(False)


def screen(d: pd.DataFrame, p: AuctionParams, after_open: bool) -> pd.DataFrame:
    """Evaluate every active rule per stock; passing stocks first, then by auction volume vs yesterday."""
    d = d.copy()
    d["auction_turnover"] = 100 * d["auction_volume"] / d["float_shares"]
    d["auction_vs_yday"] = 100 * d["auction_volume"] / d["yday_volume"]

    def rng(s, lo, hi):
        m = s.notna()
        if lo is not None:
            m &= s >= lo
        if hi is not None:
            m &= s <= hi
        return m

    d["a_not_st"] = ~d["is_st"].fillna(False)
    d["a_gap"] = rng(d["gap_pct"], p.gap_min, p.gap_max)
    d["a_not_limit"] = ~d["limit_up_open"]
    d["a_amount"] = d["auction_amount"] >= (p.amount_min_m or 0) * 1e6
    d["a_vs_yday"] = d["auction_vs_yday"] >= (p.vs_yday_min_pct or 0)
    d["a_turnover"] = d["auction_turnover"] >= (p.turnover_min or 0)
    d["a_cap"] = rng(d["float_mcap"] / 1e9, p.cap_min_bn, p.cap_max_bn)
    d["a_yday_up"] = d["yday_pct"] > 0
    d["a_yday_limit"] = d["yday_limit_up"].fillna(False)
    d["a_holding"] = d["price"] >= d["auction_price"]
    cols = [c for c, _, _ in active_rules(p, after_open)]
    d["rules_met"] = d[cols].fillna(False).astype(bool).sum(axis=1)
    d["passes"] = d["rules_met"] == len(cols)
    labels = {c: lbl for c, lbl, _ in active_rules(p, after_open)}
    d["missed"] = d[cols].apply(lambda r: ", ".join(labels[c] for c in cols if not bool(r[c])), axis=1)
    return d.sort_values(["passes", "rules_met", "auction_vs_yday"], ascending=False).reset_index(drop=True)
