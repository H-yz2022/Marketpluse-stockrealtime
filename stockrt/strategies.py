"""The Strategy lab's A-share strategies: fixed rules, written down before they were tested.

Every rule reads a state frame from stockrt.labdata.state() - the market as it stood at the
strategy's signal time - so the backtest, the daily live record and the real-time panel apply the
same code to the same kind of numbers. Registered on 11 Oct 2026 (docs/STRATEGY_RESEARCH.md); the
live record starts with the session of 12 Oct 2026.

Universe for every strategy and for the benchmark: non-ST Shanghai / Shenzhen A-shares (Main board,
ChiNext, STAR) with at least 60 sessions of history. Beijing-exchange stocks have no 30-minute bars.

Execution: the entry must be fillable - a stock at the limit-up price cannot be bought, so it is
skipped. The one exception is 首板 (S4), which buys at the limit price and assumes the order fills
(optimistic; stocks sealed at the limit since the open are skipped).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

Fn = Callable[[pd.DataFrame], pd.Series]


@dataclass(frozen=True)
class Rule:
    key: str
    label: str
    fn: Fn


@dataclass(frozen=True)
class Strategy:
    id: str
    name: str
    zh: str
    time: str                       # signal time: 'open' (09:25 auction) or HH:MM
    entry: str
    rules: tuple[Rule, ...]
    why: str
    sources: tuple[tuple[str, str], ...] = ()
    live_only: tuple[Rule, ...] = field(default_factory=tuple)   # need data with no history (checked live only)
    limit_entry: bool = False       # buys at the limit-up price (S4)
    known_at: str | None = None     # when the picks are final, if later than the signal (S4b: the close)

    @property
    def title(self) -> str:
        return f"{self.id} · {self.zh} · {self.name}"

    @property
    def time_label(self) -> str:
        return "09:25 (auction)" if self.time == "open" else self.time


def _between(s: pd.Series, lo: float | None, hi: float | None) -> pd.Series:
    m = s.notna()
    if lo is not None:
        m &= s >= lo
    if hi is not None:
        m &= s <= hi
    return m


def _cap(lo_bn: float | None, hi_bn: float | None) -> Fn:
    return lambda s: _between(s["float_mcap"] / 1e9, lo_bn, hi_bn)


S1_RULES = (
    Rule("pct", "Change +3% to +5%", lambda s: _between(s["pct_change"], 3, 5)),
    Rule("vr", "Volume ratio > 1", lambda s: s["volume_ratio"] > 1),
    Rule("turnover", "Turnover 3%–8%", lambda s: _between(s["turnover_rate"], 3, 8)),
    Rule("cross", "MA5 crossed above MA10 (last 3 sessions)", lambda s: s["cross_recent"].fillna(False)),
    Rule("ma_up", "MA5 and MA10 rising", lambda s: s["ma_rising"].fillna(False)),
    Rule("vol_price", "Volume and price up", lambda s: s["vol_price_up"].fillna(False)),
    Rule("beats", "Beats the CSI 300", lambda s: s["pct_change"] > s.get("csi300_pct", np.nan)),
    Rule("cap", "Float cap ¥5–20B", _cap(5, 20)),
)
INFLOW = Rule("inflow", "Main funds net inflow", lambda s: s.get("main_net", pd.Series(np.nan, index=s.index)) > 0)
GAP_RULES = (
    Rule("gap", "Opening gap +2% to +7%", lambda s: _between(s["gap_pct"], 2, 7)),
    Rule("not_lu_open", "Not opening at limit-up", lambda s: s["open"] < s["limit_up"] - 0.005),
    Rule("cap", "Float cap ¥3–50B", _cap(3, 50)),
)
AUCTION_RULES = (
    Rule("a_amount", "Auction value ≥ ¥10M", lambda s: s.get("auction_amount", np.nan) >= 10e6),
    Rule("a_vs_yday", "Auction volume ≥ 5% of yesterday's", lambda s: s.get("auction_volume", np.nan)
         >= 0.05 * s["prev_volume"]),
    Rule("a_turnover", "Auction turnover ≥ 0.3%", lambda s: 100 * s.get("auction_volume", np.nan)
         / s["float_shares"] >= 0.3),
)

LSY = ("Liu, Stambaugh & Yuan - Size and Value in China",
       "https://faculty.wharton.upenn.edu/wp-content/uploads/2018/03/Size-and-Value-in-China.pdf")
QD = ("PKU seminar - T+1 and the overnight return puzzle", "https://econ.pku.edu.cn/xkzy/jrxx/xkdt2/xkdtzs/376755.htm")

STRATEGIES: dict[str, Strategy] = {s.id: s for s in (
    Strategy(
        "S1", "Late-session picks", "尾盘选股", "14:30", "14:30 price", S1_RULES,
        "Your front-page screen: a fresh MA5/MA10 cross with rising volume, bought late so the day's strength "
        "is already confirmed. The research worry: China's overnight return is negative on average (the T+1 "
        "rule), so buying late and selling next morning starts with a handicap.",
        (QD,), live_only=(INFLOW,)),
    Strategy(
        "S1b", "Late-session picks, half an hour earlier", "尾盘选股 14:00", "14:00", "14:00 price", S1_RULES,
        "The same screen half an hour earlier - does waiting until 14:30 help?", (QD,), live_only=(INFLOW,)),
    Strategy(
        "S2", "Opening-auction gap", "竞价选股", "open", "Opening price (09:25 auction)", GAP_RULES,
        "A moderate gap up shows overnight demand; a limit-up open can't be bought. The auction-volume "
        "conditions of the front page's 竞价选股 need data with no history, so they are checked live only.",
        (("Hull - intraday momentum in China (overnight component)",
          "https://hull-repository.worktribe.com/OutputFile/3016448"),), live_only=AUCTION_RULES),
    Strategy(
        "S3", "Gap holding at 10:00", "高开不破", "10:00", "10:00 price",
        GAP_RULES + (Rule("holding", "Price at 10:00 ≥ the open", lambda s: s["price"] >= s["open"]),),
        "Waits 30 minutes to see whether the gap is being sold. Tests the claim that a gap which holds is "
        "real demand rather than a trap.",
        (("Jin et al. - intraday time-series momentum, China",
          "https://hull-repository.worktribe.com/output/3295550/intraday-time-series-momentum-evidence-from-china"),)),
    Strategy(
        "S4", "First limit-up", "首板", "14:30", "Limit-up price (assumes the order fills)",
        (Rule("at_limit", "At the limit-up price at 14:30", lambda s: s["at_limit"].fillna(False)),
         Rule("first", "Yesterday not limit-up (first board)", lambda s: ~s["yday_limit_up"].fillna(False))),
        "Contested: large accounts push stocks to the limit and sell the next day (magnet effect), yet other "
        "work finds next-day continuation. The fill is assumed - in reality the queue at the limit often "
        "doesn't fill, so treat these returns as an upper bound.",
        (("VoxChina - daily price limits and the magnet effect", "https://voxchina.org/show-3-49.html"),
         ("Wan et al. 2015 - price-limit hits in China (PLOS ONE)", "https://pmc.ncbi.nlm.nih.gov/articles/PMC4395215")),
        limit_entry=True),
    Strategy(
        "S4b", "First limit-up, pessimistic fill", "首板 · 悲观成交", "14:30", "Limit-up price, only if the order "
        "would fill",
        (Rule("at_limit", "At the limit-up price at 14:30", lambda s: s["at_limit"].fillna(False)),
         Rule("first", "Yesterday not limit-up (first board)", lambda s: ~s["yday_limit_up"].fillna(False)),
         Rule("filled", "Seal broke before the close (a 14:30 order fills)",
              lambda s: s["close"] < s["limit_up"] - 0.005)),
        "S4 with a realistic fill: an order queued at the limit price at 14:30 fills for certain only if the "
        "seal breaks before the close, i.e. the stock ends the day below the limit. That is exactly when the "
        "trade is worst (adverse selection), so this is the lower bound for 首板; the truth lies between S4 and "
        "S4b. The list is final at 15:00.",
        (("VoxChina - daily price limits and the magnet effect", "https://voxchina.org/show-3-49.html"),),
        limit_entry=True, known_at="15:00"),
    Strategy(
        "S5", "Morning breakout on volume", "早盘放量突破", "10:30", "10:30 price",
        (Rule("breakout", "Above the 20-session high", lambda s: s["above_high20"].fillna(False)),
         Rule("vr", "Volume ratio > 2", lambda s: s["volume_ratio"] > 2),
         Rule("pct", "Change +3% to +7%", lambda s: _between(s["pct_change"], 3, 7)),
         Rule("cap", "Float cap ¥5–50B", _cap(5, 50))),
        "Classic momentum: a new 20-day high on heavy early volume. Research warns that stocks with extreme "
        "jumps (the 'lottery' MAX effect) tend to do worse afterwards in China - this tests which wins.",
        (("Bi, Gui & Zhu - the MAX effect in China", "https://ideas.repec.org/a/eee/jbfina/v76y2017icp189-197.html"),)),
    Strategy(
        "S6", "Oversold bounce", "超跌反弹", "14:30", "14:30 price",
        (Rule("oversold", "5-session return in the market's bottom 5%", lambda s: s["ret5"] <= s["ret5_cut"]),
         Rule("pct", "Change −7% to −2%", lambda s: _between(s["pct_change"], -7, -2)),
         Rule("cap", "Float cap ≥ ¥3B", _cap(3, None))),
        "Short-term reversal is one of the strongest effects in Chinese stocks: the biggest recent losers "
        "tend to bounce. Buys them while still falling, late in the day.",
        (LSY, ("Lin et al. 2020 - reversal beats turnover as a factor in China",
               "https://ideas.repec.org/a/eee/pacfin/v62y2020ics0927538x2030113x.html"))),
)}

SIGNAL_ORDER = ("open", "10:00", "10:30", "14:00", "14:30")


def fillable(st: Strategy, s: pd.DataFrame) -> pd.Series:
    """Could the entry order fill? Not at the limit-up price (except S4, which skips one-price boards)."""
    if st.limit_entry:
        return ~s["sealed"].fillna(True)
    return s["price"] < s["limit_up"] - 0.005


def with_ret5_cut(s: pd.DataFrame, pct: float = 0.05) -> pd.DataFrame:
    """Each session's bottom-5% cut-off of the 5-session return, over the eligible universe."""
    cut = s[s["eligible"]].groupby("day")["ret5"].quantile(pct)
    return s.assign(ret5_cut=s["day"].map(cut))


def evaluate(st: Strategy, s: pd.DataFrame, explain: bool = True) -> pd.DataFrame:
    """Eligible stocks with one column per rule, `core` (every backtestable rule, and fillable),
    `rules_met` and the rules `missed`."""
    out = s[s["eligible"]].copy()
    cols = []
    for r in st.rules:
        out[f"r_{r.key}"] = r.fn(out).fillna(False).astype(bool)
        cols.append(f"r_{r.key}")
    out["r_fill"] = fillable(st, out).fillna(False).astype(bool)
    cols.append("r_fill")
    out["core"] = out[cols].all(axis=1)
    out["rules_met"] = out[cols].sum(axis=1)
    labels = {f"r_{r.key}": r.label for r in st.rules} | {"r_fill": "Buyable at entry"}
    if explain:
        out["missed"] = [", ".join(lbl for lbl, ok in zip([labels[c] for c in cols], row) if not ok)
                         for row in out[cols].itertuples(index=False)]
    return out


def live_pass(st: Strategy, picks: pd.DataFrame) -> pd.Series:
    """The live-only rules (main-fund flow, auction volume) on stocks that passed the core rules:
    True / False, or NaN where the data couldn't be read."""
    if not st.live_only:
        return pd.Series(True, index=picks.index, dtype=object)
    ok = pd.Series(True, index=picks.index, dtype=object)
    for r in st.live_only:
        m = r.fn(picks)
        known = _inputs_known(r, picks)
        ok = ok.where(~known | m.fillna(False), False)
        ok = ok.where(known | (ok == False), np.nan)  # noqa: E712 - object column
    return ok


def _inputs_known(r: Rule, picks: pd.DataFrame) -> pd.Series:
    col = "main_net" if r.key == "inflow" else "auction_volume"
    return picks[col].notna() if col in picks else pd.Series(False, index=picks.index)


def sentiment(s: pd.DataFrame) -> pd.DataFrame:
    """短线情绪 per session at the state's time: how yesterday's limit-up stocks are doing, limit-ups,
    failed limit-ups (炸板率) and the highest limit-up streak (连板高度)."""
    e = s[s["eligible"]]
    g = e.groupby("day")
    out = pd.DataFrame({
        "yday_lu_avg": e[e["yday_limit_up"]].groupby("day")["pct_change"].mean(),
        "yday_lu_n": e[e["yday_limit_up"]].groupby("day").size(),
        "at_limit_n": g["at_limit"].sum(),
        "touched_n": g["touched_limit"].sum(),
        "max_streak": g["streak_prev"].max(),
    })
    out["zhaban_rate"] = 1 - out["at_limit_n"] / out["touched_n"].replace(0, np.nan)
    return out.reset_index()
