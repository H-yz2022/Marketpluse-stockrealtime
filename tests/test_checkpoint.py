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


def test_plan_explains_pending_checkpoints(monkeypatch):
    from datetime import datetime

    from stockrt.calendar import BJ

    monkeypatch.setattr(checkpoint, "now_bj", lambda: datetime(2026, 10, 8, 14, 20, tzinfo=BJ))
    plan = {x["checkpoint"]: x for x in checkpoint.plan(date(2026, 10, 8))}
    assert plan["14:00"]["ready"] and not plan["14:30"]["ready"] and not plan["15:00"]["ready"]
    assert "in 11 min" in plan["14:30"]["reason"]
    assert "closes at 15:00" in plan["15:00"]["reason"] and "41 min" in plan["15:00"]["reason"]
    # One minute of grace: 15:00 is shown from 15:01, after the closing auction prints.
    monkeypatch.setattr(checkpoint, "now_bj", lambda: datetime(2026, 10, 8, 15, 0, 30, tzinfo=BJ))
    assert not checkpoint.checkpoint_reached(date(2026, 10, 8), "15:00")
    monkeypatch.setattr(checkpoint, "now_bj", lambda: datetime(2026, 10, 8, 15, 1, tzinfo=BJ))
    assert checkpoint.checkpoint_reached(date(2026, 10, 8), "15:00")


def test_latest_session_skips_holidays(monkeypatch):
    from datetime import datetime

    from stockrt.calendar import BJ

    # Golden Week: on Tue 6 Oct the latest A-share session is Wed 30 Sep, and the page says why.
    monkeypatch.setattr(checkpoint, "now_bj", lambda: datetime(2026, 10, 6, 12, 0, tzinfo=BJ))
    assert checkpoint.latest_session_day() == date(2026, 9, 30)
    # Reopening day before the open: still the previous session; after 09:30, today.
    monkeypatch.setattr(checkpoint, "now_bj", lambda: datetime(2026, 10, 8, 9, 0, tzinfo=BJ))
    assert checkpoint.latest_session_day() == date(2026, 9, 30)
    monkeypatch.setattr(checkpoint, "now_bj", lambda: datetime(2026, 10, 8, 9, 31, tzinfo=BJ))
    assert checkpoint.latest_session_day() == date(2026, 10, 8)


def test_three_checkpoint_tiers():
    p = ScreenParams()
    mk = lambda **o: checkpoint.score(checkpoint.apply_rules(frame(**o), p, 0.4), 0.4)  # noqa: E731
    res = {"frames": {
        "14:00": pd.concat([mk(code="always"), mk(code="once")], ignore_index=True),
        "14:30": pd.concat([mk(code="always"), mk(code="once", turnover_rate=9.0)], ignore_index=True),
        "15:00": pd.concat([mk(code="always"), mk(code="once", turnover_rate=9.5)], ignore_index=True),
    }}
    r = checkpoint.ranked(res, p).set_index("code")
    assert r.loc["always", "tier"] == "A" and r.loc["always", "at_15:00"] == "✓"
    assert r.loc["once", "tier"] == "C" and r.loc["once", "at_14:00"] == "✓" and r.loc["once", "at_15:00"] == "·"


HK = checkpoint.SPECS["HK"]


def test_hk_spec_labels_and_plan(monkeypatch):
    from datetime import datetime

    from stockrt.calendar import BJ

    labels = dict(checkpoint.rule_labels(ScreenParams(), HK))
    assert labels["rule_outperform"] == "Beats the Hang Seng Index"
    assert labels["rule_float_cap"].endswith("bn HKD")
    monkeypatch.setattr(checkpoint, "now_bj", lambda: datetime(2026, 10, 9, 15, 40, tzinfo=BJ))
    plan = {x["checkpoint"]: x for x in checkpoint.plan(date(2026, 10, 9), HK)}
    assert plan["15:00"]["ready"] and plan["15:30"]["ready"] and not plan["16:00"]["ready"]
    assert "closes at 16:00" in plan["16:00"]["reason"]


def test_hk_close_includes_closing_auction():
    from datetime import time as t

    from stockrt.calendar import session_of

    assert session_of("HK", t(16, 8)) == "PM"      # closing-auction print belongs to the session
    assert session_of("CN", t(15, 5)) == "post"    # mainland after-hours fixed-price trades do not
    times = list(pd.date_range("2026-10-08 09:30", "2026-10-08 12:00", freq="min")) + \
        list(pd.date_range("2026-10-08 13:01", "2026-10-08 15:59", freq="min")) + [pd.Timestamp("2026-10-08 16:08")]
    price = np.r_[np.full(len(times) - 1, 10.4), [10.45]]
    bars = pd.DataFrame({"datetime": times, "price": price, "volume": 1000.0})
    bars["amount"] = bars["price"] * bars["volume"]
    bars["session"] = [session_of("HK", x.time()) for x in bars["datetime"]]
    hist = daily_hist(np.r_[np.full(5, 10.0), np.full(5, 9.9)])
    q = {"price": 10.45, "float_mcap": 10.45e6, "float_shares": 1e6}
    with patch("stockrt.checkpoint.tencent.minute_today", return_value=(date(2026, 10, 8), 10.0, bars)), \
         patch("stockrt.checkpoint.tencent.daily", return_value=hist):
        rows = {r["checkpoint"]: r for r in checkpoint._stock_rows("hk00001", q, date(2026, 10, 8),
                                                                   HK.checkpoints, HK)}
    assert set(rows) == {"15:00", "15:30", "16:00"}
    assert rows["16:00"]["price"] == pytest.approx(10.45)       # the official close, after the auction
    assert rows["15:30"]["price"] == pytest.approx(10.4)
    assert rows["16:00"]["volume"] == pytest.approx(1000 * len(times))


def test_missing_money_flow_is_unchecked_not_failed():
    p = ScreenParams()
    d = checkpoint.apply_rules(frame(main_net=np.nan), p, 0.4, HK, flow_known=False)
    assert bool(d["rule_inflow"].iloc[0]) and d["passes"].iloc[0]
    res = {"frames": {c: checkpoint.score(d, 0.4) for c in HK.checkpoints}, "flow_checked": False}
    assert (checkpoint.ranked(res, p, spec=HK)["confidence"] == "Low").all()
