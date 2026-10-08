"""Rule-based stock screener (no AI): snapshot filters -> history filters -> money flow.

The default preset is the late-session momentum screen (尾盘选股):

    non-ST, up 3-5% today, volume ratio > 1, turnover 3-8%, MA5 crossing above
    MA10, both moving averages rising, volume and price rising together, main
    funds flowing in, beating the market, float market cap CNY 5-20 billion.

Stages run cheapest first so the whole market (~5,500 stocks) is screened with
one quote sweep and only the survivors cost extra requests.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field, fields
from datetime import timedelta

import numpy as np
import pandas as pd

from . import http, realtime
from .calendar import now_bj
from .calendar import status as session_status
from .history import ma, with_live_bar
from .sources import tencent
from .symbols import market

BOARDS = ("Main", "ChiNext", "STAR", "BSE")


@dataclass
class ScreenParams:
    exclude_st: bool = True
    boards: tuple[str, ...] = BOARDS
    pct_min: float | None = 3.0
    pct_max: float | None = 5.0
    volume_ratio_min: float | None = 1.0
    turnover_min: float | None = 3.0
    turnover_max: float | None = 8.0
    float_mcap_min_bn: float | None = 5.0
    float_mcap_max_bn: float | None = 20.0
    price_min: float | None = None
    price_max: float | None = None
    outperform_market: bool = True
    golden_cross: bool = True
    cross_within: int = 3          # sessions; 1 = the cross happened today
    ma_rising: bool = True
    volume_price_up: bool = True
    main_inflow: bool = True
    max_history_candidates: int = 300

    def to_dict(self) -> dict:
        d = asdict(self)
        d["boards"] = list(self.boards)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> ScreenParams:
        known = {f.name for f in fields(cls)}
        clean = {k: v for k, v in d.items() if k in known}
        if "boards" in clean:
            clean["boards"] = tuple(clean["boards"])
        return cls(**clean)


PRESETS: dict[str, ScreenParams] = {
    "Late-session momentum (尾盘选股)": ScreenParams(),
    "Strong large caps": ScreenParams(
        pct_min=2.0, pct_max=None, volume_ratio_min=1.2, turnover_min=1.0, turnover_max=None,
        float_mcap_min_bn=50.0, float_mcap_max_bn=None, golden_cross=False, ma_rising=True,
        volume_price_up=True, main_inflow=True),
    "Volume breakout": ScreenParams(
        pct_min=2.0, pct_max=7.0, volume_ratio_min=2.0, turnover_min=5.0, turnover_max=20.0,
        float_mcap_min_bn=None, float_mcap_max_bn=None, golden_cross=False, ma_rising=False,
        volume_price_up=True, main_inflow=True, outperform_market=True),
    "Pullback in uptrend": ScreenParams(
        pct_min=-3.0, pct_max=0.0, volume_ratio_min=None, turnover_min=1.0, turnover_max=6.0,
        float_mcap_min_bn=10.0, float_mcap_max_bn=None, golden_cross=False, ma_rising=True,
        volume_price_up=False, main_inflow=False, outperform_market=False),
}


@dataclass
class ScreenResult:
    passed: pd.DataFrame
    candidates: pd.DataFrame          # every stock evaluated in stage 2/3, with per-rule columns
    near_misses: pd.DataFrame         # failed exactly one history/flow rule
    funnel: list[tuple[str, int]]
    notes: list[str] = field(default_factory=list)
    bench_pct: float | None = None


def _range(lo: float | None, hi: float | None, unit: str = "") -> str:
    if lo is not None and hi is not None:
        return f"between {lo:g}{unit} and {hi:g}{unit}"
    if lo is not None:
        return f"at least {lo:g}{unit}"
    if hi is not None:
        return f"at most {hi:g}{unit}"
    return ""


def describe(p: ScreenParams, bench_name: str = "CSI 300") -> list[str]:
    """Plain-English list of the active rules (used in the UI and in AI prompts)."""
    rules = []
    if p.exclude_st:
        rules.append("Exclude ST / *ST stocks")
    if set(p.boards) != set(BOARDS):
        rules.append("Boards: " + ", ".join(p.boards))
    for lo, hi, what, unit in ((p.pct_min, p.pct_max, "Price change today", "%"),
                               (p.turnover_min, p.turnover_max, "Turnover rate", "%"),
                               (p.float_mcap_min_bn, p.float_mcap_max_bn, "Float market cap", " bn CNY"),
                               (p.price_min, p.price_max, "Price", "")):
        r = _range(lo, hi, unit)
        if r:
            rules.append(f"{what} {r}")
    if p.volume_ratio_min is not None:
        rules.append(f"Volume ratio greater than {p.volume_ratio_min:g}")
    if p.outperform_market:
        rules.append(f"Outperforming the market today (change % above the {bench_name})")
    if p.golden_cross:
        when = "today" if p.cross_within <= 1 else f"within the last {p.cross_within} sessions"
        rules.append(f"5-day MA crossed above the 10-day MA {when} (and is still above)")
    if p.ma_rising:
        rules.append("5-day and 10-day moving averages both rising")
    if p.volume_price_up:
        rules.append("Volume and price rising together (price up today, pace-adjusted volume above yesterday's)")
    if p.main_inflow:
        rules.append("Main funds (large + extra-large orders) net inflow today")
    return rules


# --------------------------------------------------------------------------- stage 1

def _stage1(snap: pd.DataFrame, p: ScreenParams, bench_pct: float | None) -> tuple[pd.DataFrame, list]:
    steps: list[tuple[str, Callable[[pd.DataFrame], pd.Series]]] = []
    steps.append(("Traded today (not suspended)", lambda d: ~d["suspended"].fillna(False)))
    if p.exclude_st:
        steps.append(("Not ST / *ST", lambda d: ~d["is_st"]))
    if set(p.boards) != set(BOARDS):
        steps.append(("Board in " + ", ".join(p.boards), lambda d: d["board"].isin(p.boards)))

    def rng(col, lo, hi, scale=1.0):
        def f(d):
            s = d[col] / scale
            m = s.notna()
            if lo is not None:
                m &= s >= lo
            if hi is not None:
                m &= s <= hi
            return m
        return f

    if p.pct_min is not None or p.pct_max is not None:
        steps.append((f"Change {_range(p.pct_min, p.pct_max, '%')}", rng("pct_change", p.pct_min, p.pct_max)))
    if p.volume_ratio_min is not None:
        steps.append((f"Volume ratio > {p.volume_ratio_min:g}", lambda d: d["volume_ratio"] > p.volume_ratio_min))
    if p.turnover_min is not None or p.turnover_max is not None:
        steps.append((f"Turnover {_range(p.turnover_min, p.turnover_max, '%')}",
                      rng("turnover_rate", p.turnover_min, p.turnover_max)))
    if p.float_mcap_min_bn is not None or p.float_mcap_max_bn is not None:
        steps.append((f"Float cap {_range(p.float_mcap_min_bn, p.float_mcap_max_bn, ' bn')}",
                      rng("float_mcap", p.float_mcap_min_bn, p.float_mcap_max_bn, 1e9)))
    if p.price_min is not None or p.price_max is not None:
        steps.append((f"Price {_range(p.price_min, p.price_max)}", rng("price", p.price_min, p.price_max)))
    if p.outperform_market and bench_pct is not None:
        steps.append((f"Beats market ({bench_pct:+.2f}%)", lambda d: d["pct_change"] > bench_pct))

    funnel = [("All stocks", len(snap))]
    d = snap
    for name, fn in steps:
        d = d[fn(d).fillna(False)]
        funnel.append((name, len(d)))
    return d, funnel


# --------------------------------------------------------------------------- stage 2

def history_features(code: str, quote: dict) -> dict:
    """MA5/MA10 cross, MA slope and volume-price features from ~40 daily bars + the live bar."""
    start = now_bj().date() - timedelta(days=70)
    bars = tencent.daily(code, start=start, fq="qfq")
    bars = with_live_bar(bars, quote)
    if len(bars) < 12:
        return {"code": code, "history_ok": False}
    close, vol = bars["close"].reset_index(drop=True), bars["volume"].reset_index(drop=True)
    trend = trend_features(close)
    # Pace-adjust today's volume when the quote is from a session still in progress.
    st = session_status(market(code))
    qt = quote.get("time")
    live_today = qt is not None and pd.Timestamp(qt).date() == now_bj().date() and st.phase in (
        "morning", "lunch", "afternoon")
    frac = max(st.fraction, 1 / st.total_min) if live_today else 1.0
    today_vol = float(vol.iloc[-1])
    return {
        "code": code,
        "history_ok": True,
        **trend,
        "prev_volume": float(vol.iloc[-2]),
        "projected_volume": today_vol / frac,
        "ret_5d": float(close.iloc[-1] / close.iloc[-6] - 1) if len(close) > 6 else np.nan,
    }


def trend_features(close: pd.Series) -> dict:
    """MA5 / MA10 now and one session earlier, and sessions since MA5 last crossed above MA10.

    The last element of `close` is "today" (a live or checkpoint price)."""
    close = close.reset_index(drop=True)
    ma5, ma10 = ma(close, 5), ma(close, 10)
    spread = (ma5 - ma10).to_numpy()
    cross_days_ago = None
    for k in range(len(spread) - 1, 0, -1):
        if np.isnan(spread[k]) or np.isnan(spread[k - 1]):
            break
        if spread[k] > 0 >= spread[k - 1]:
            cross_days_ago = len(spread) - 1 - k
            break
        if spread[k] <= 0:
            break
    return {"ma5": float(ma5.iloc[-1]), "ma10": float(ma10.iloc[-1]), "ma5_prev": float(ma5.iloc[-2]),
            "ma10_prev": float(ma10.iloc[-2]), "cross_days_ago": cross_days_ago}


def _stage2(cands: pd.DataFrame, p: ScreenParams, progress: Callable[[float, str], None] | None) -> pd.DataFrame:
    quotes = {r["code"]: r for r in cands.to_dict("records")}
    done = [0]

    def one(code):
        try:
            return history_features(code, quotes[code])
        finally:
            done[0] += 1
            if progress:
                progress(done[0] / len(quotes), f"Daily history {done[0]}/{len(quotes)}")

    feats = [r for r in http.pmap(one, list(quotes), workers=8) if isinstance(r, dict)]
    f = pd.DataFrame(feats)
    if f.empty:
        return cands.assign(history_ok=False)
    out = cands.merge(f, on="code", how="left")
    out["history_ok"] = out["history_ok"].fillna(False).astype(bool)
    return out


def run(snap: pd.DataFrame, p: ScreenParams, bench_pct: float | None,
        progress: Callable[[float, str], None] | None = None) -> ScreenResult:
    notes: list[str] = []
    cands, funnel = _stage1(snap, p, bench_pct)
    if p.outperform_market and bench_pct is None:
        notes.append("Benchmark quote unavailable - 'beats market' rule skipped.")
    needs_hist = p.golden_cross or p.ma_rising or p.volume_price_up
    if needs_hist and len(cands) > p.max_history_candidates:
        notes.append(f"{len(cands)} stocks passed the snapshot rules; history checked for the "
                     f"{p.max_history_candidates} with the highest turnover value.")
        cands = cands.sort_values("amount", ascending=False).head(p.max_history_candidates)

    rules: list[tuple[str, str]] = []  # (column, label)
    if needs_hist and not cands.empty:
        cands = _stage2(cands, p, progress)
        ok = cands["history_ok"]
        if p.golden_cross:
            cands["rule_golden_cross"] = ok & (cands["ma5"] > cands["ma10"]) & \
                cands["cross_days_ago"].notna() & (cands["cross_days_ago"] < max(p.cross_within, 1))
            rules.append(("rule_golden_cross", "MA5 crossed above MA10"))
        if p.ma_rising:
            cands["rule_ma_rising"] = ok & (cands["ma5"] > cands["ma5_prev"]) & (cands["ma10"] > cands["ma10_prev"])
            rules.append(("rule_ma_rising", "MA5 & MA10 rising"))
        if p.volume_price_up:
            cands["rule_volume_price_up"] = ok & (cands["pct_change"] > 0) & \
                (cands["projected_volume"] > cands["prev_volume"])
            rules.append(("rule_volume_price_up", "Volume & price up"))

    if p.main_inflow and not cands.empty:
        # Only stocks still alive (or one rule short) need a money-flow lookup.
        alive = cands
        if rules:
            fails = sum((~cands[c]).astype(int) for c, _ in rules)
            alive = cands[fails <= 1]
        if progress:
            progress(1.0, f"Money flow for {len(alive)} stocks")
        flow = realtime.main_flow(alive["code"].tolist())
        if not flow.empty:
            cands = cands.merge(flow[["code", "main_net", "main_net_ratio", "source"]].rename(
                columns={"source": "flow_source"}), on="code", how="left")
        else:
            cands["main_net"] = np.nan
            notes.append("Money-flow sources unavailable - 'main funds inflow' could not be checked.")
        cands["rule_main_inflow"] = cands["main_net"] > 0
        rules.append(("rule_main_inflow", "Main funds net inflow"))

    rule_cols = [c for c, _ in rules]
    if rule_cols:
        fails = sum((~cands[c].fillna(False).astype(bool)).astype(int) for c in rule_cols)
        cands["rules_failed"] = fails
        passed = cands[fails == 0]
        near = cands[fails == 1].copy()
        near["missed_rule"] = [next(lbl for c, lbl in rules if not bool(row[c]) or pd.isna(row[c]))
                               for _, row in near.iterrows()]
        mask = pd.Series(True, index=cands.index)
        for c, lbl in rules:
            mask &= cands[c].fillna(False).astype(bool)
            funnel.append((lbl, int(mask.sum())))
    else:
        passed, near = cands, cands.iloc[0:0]
    return ScreenResult(passed=passed.reset_index(drop=True), candidates=cands.reset_index(drop=True),
                        near_misses=near.reset_index(drop=True), funnel=funnel, notes=notes, bench_pct=bench_pct)
