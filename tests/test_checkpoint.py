"""14:00 / 14:30 replay: pre-filter, per-minute rebuild, rules, score and tiers (offline)."""

from datetime import date
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from stockrt import checkpoint
from stockrt.screener import ScreenParams

DAY = date(2026, 9, 30)


def minute_bars(path_pct, prev_close=10.0, vol_per_min=1000.0):
    """One session of 1-minute bars following a %-change path (241 points, lunch break removed)."""
    times = list(pd.date_range("2026-09-30 09:30", "2026-09-30 11:30", freq="min")) + \
        list(pd.date_range("2026-09-30 13:01", "2026-09-30 15:00", freq="min"))
    price = prev_close * (1 + np.interp(np.arange(len(times)), np.linspace(0, len(times) - 1, len(path_pct)),
                                        path_pct) / 100)
    df = pd.DataFrame({"datetime": times, "price": price, "volume": vol_per_min})
    df["amount"] = df["price"] * df["volume"]
    df["cum_volume"], df["cum_amount"] = df["volume"].cumsum(), df["amount"].cumsum()
    df["session"] = ["AM" if t.hour < 12 else "PM" for t in df["datetime"]]
    return df


def daily_hist(closes, vol=240_000.0):
    d = pd.bdate_range(end="2026-09-29", periods=len(closes))
    return pd.DataFrame({"date": d, "open": closes, "high": closes, "low": closes, "close": closes,
                         "volume": vol, "amount": 1e7, "turnover_rate": 1.0, "event": ""})


def test_prefilter_keeps_only_stocks_that_could_have_qualified():
    snap = pd.DataFrame({
        "code": ["a", "b", "c", "d"], "name": ["好", "*ST坏", "涨停", "冷门"], "is_st": [False, True, False, False],
        "board": "Main", "suspended": False, "prev_close": 10.0,
        "low": [10.1, 10.1, 10.9, 10.1], "high": [10.6, 10.6, 11.0, 10.6],   # c never traded below +9%
        "price": [10.4, 10.4, 11.0, 10.4], "turnover_rate": [5.0, 5.0, 5.0, 1.0],  # d: too little turnover all day
        "float_mcap": 10e9,
    })
    assert checkpoint.prefilter(snap, ScreenParams())["code"].tolist() == ["a"]


def test_rebuild_at_checkpoints():
    q = {"price": 10.4, "float_mcap": 10.4e6 * 1000, "float_shares": 1e6}
    # Five sessions at 10.0 then five at 9.9: MA5 (9.9) sits below MA10 (9.95). A checkpoint price
    # above 4 x 10.0 - 3 x 9.9 = 10.3 lifts MA5 over MA10 today, with both averages rising.
    hist = daily_hist(np.r_[np.full(5, 10.0), np.full(5, 9.9)])
    bars = minute_bars([0, 1, 2, 4, 4.2], prev_close=10.0)
    with patch("stockrt.checkpoint.tencent.minute_today", return_value=(DAY, 10.0, bars)), \
         patch("stockrt.checkpoint.tencent.daily", return_value=hist):
        rows = {r["checkpoint"]: r for r in checkpoint._stock_rows("sh600001", q, DAY, ("14:00", "14:30"))}
    r = rows["14:00"]
    elapsed = 180
    assert r["volume"] == pytest.approx(1000 * (121 + 60))
    # Volume ratio = (volume / minutes elapsed) / (5-day average volume / 240).
    assert r["volume_ratio"] == pytest.approx((r["volume"] / elapsed) / (240_000 / 240))
    assert r["turnover_rate"] == pytest.approx(100 * r["volume"] / 1e6)
    assert r["float_mcap"] == pytest.approx(r["price"] * 1e6)
    assert r["price"] > 10.3
    assert r["cross_days_ago"] == 0 and r["ma5"] > r["ma10"]
    assert r["ma5"] > r["ma5_prev"] and r["ma10"] > r["ma10_prev"]
    assert rows["14:30"]["price"] > r["price"] and r["after_label"] == "to close"


def frame(**over):
    base = {"code": "x", "name": "x", "is_st": False, "pct_change": 4.0, "volume_ratio": 2.5, "turnover_rate": 5.0,
            "float_mcap": 10e9, "ma5": 10.2, "ma10": 10.0, "ma5_prev": 10.0, "ma10_prev": 9.9, "cross_days_ago": 0,
            "projected_volume": 2e6, "prev_volume": 1e6, "main_net": 5e6, "amount": 5e7, "amount_for_flow": 5e7,
            "price": 10.4, "vwap": 10.3, "range_position": 0.9, "from_high": -0.002, "flow_at": "checkpoint"}
    base.update(over)
    return pd.DataFrame([base])


def test_rules_and_score():
    p = ScreenParams()
    good = checkpoint.score(checkpoint.apply_rules(frame(), p, 0.4), 0.4).iloc[0]
    assert good["passes"] and good["rules_met"] == 10 and good["missed"] == ""
    assert 70 < good["score"] <= 100
    weak = checkpoint.score(checkpoint.apply_rules(frame(volume_ratio=0.8, main_net=-5e6, range_position=0.1), p, 0.4),
                            0.4).iloc[0]
    assert not weak["passes"] and weak["score"] < good["score"]


def test_tiers_and_close_misses():
    p = ScreenParams()
    mk = lambda **o: checkpoint.score(checkpoint.apply_rules(frame(**o), p, 0.4), 0.4)  # noqa: E731
    res = {"frames": {
        "14:00": pd.concat([mk(code="held"), mk(code="faded"), mk(code="late", pct_change=2.5)], ignore_index=True),
        "14:30": pd.concat([mk(code="held"), mk(code="faded", turnover_rate=8.5), mk(code="late"),
                            mk(code="near", turnover_rate=2.6), mk(code="limitup", pct_change=9.98)],
                           ignore_index=True),
    }}
    r = checkpoint.ranked(res, p).set_index("code")
    assert r.loc["held", "tier"] == "A"
    assert r.loc["late", "tier"] == "B"
    assert r.loc["faded", "tier"] == "C"
    assert r.loc["near", "tier"] == "D"
    assert "limitup" not in r.index  # +9.98% misses 3-5% by far too much to be a near miss
    assert r["tier"].tolist() == sorted(r["tier"].tolist())
