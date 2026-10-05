"""Screener stages on synthetic data (network calls patched)."""

from unittest.mock import patch

import numpy as np
import pandas as pd

from stockrt import screener


def snapshot():
    rows = [
        # code, name, pct, vr, turnover, float cap, st
        ("sh600001", "好股票", 4.0, 1.5, 5.0, 10e9),
        ("sh600002", "*ST差股", 4.0, 1.5, 5.0, 10e9),
        ("sh600003", "涨太多", 7.0, 1.5, 5.0, 10e9),
        ("sh600004", "量比低", 4.0, 0.8, 5.0, 10e9),
        ("sh600005", "太大了", 4.0, 1.5, 5.0, 80e9),
        ("sz000006", "均线差", 4.0, 1.5, 5.0, 10e9),
    ]
    df = pd.DataFrame(rows, columns=["code", "name", "pct_change", "volume_ratio", "turnover_rate", "float_mcap"])
    df["is_st"] = df["name"].str.contains("ST")
    df["board"] = "Main"
    df["suspended"] = False
    df["price"] = 10.4  # live price = last synthetic close
    df["amount"] = 1e8
    df["volume"] = 2e6
    df["time"] = pd.Timestamp("2026-09-28 15:00", tz="Asia/Shanghai")  # = last synthetic bar
    for c in ("open", "high", "low"):
        df[c] = 10.0
    df["turnover_rate"] = df["turnover_rate"].astype(float)
    return df


def fake_daily(code, start=None, end=None, fq=""):
    n = 30
    d = pd.bdate_range("2026-08-18", periods=n)
    if code == "sh600001":   # dip then sharp recovery -> MA5 crosses above MA10 on the last bar
        close = np.r_[np.full(20, 10.0), np.linspace(9.6, 9.0, 8), [9.9, 10.4]]
    else:                    # steady decline -> no cross
        close = np.linspace(12, 10, n)
    return pd.DataFrame({"date": d, "open": close, "high": close, "low": close, "close": close,
                         "volume": np.r_[np.full(n - 1, 1e6), [2e6]], "amount": 1e7, "turnover_rate": 1.0,
                         "event": ""})


def test_default_screen_end_to_end():
    flow = pd.DataFrame({"code": ["sh600001", "sz000006"], "main_net": [5e6, 1e6], "main_net_ratio": [5.0, 1.0],
                         "xl_net": 0, "l_net": 0, "retail_net": 0, "source": "test"})
    with patch("stockrt.screener.tencent.daily", side_effect=fake_daily), \
         patch("stockrt.screener.realtime.main_flow", return_value=flow):
        res = screener.run(snapshot(), screener.ScreenParams(cross_within=1), bench_pct=0.5)
    assert res.passed["code"].tolist() == ["sh600001"]
    funnel = dict(res.funnel)
    assert funnel["All stocks"] == 6
    assert funnel["Not ST / *ST"] == 5
    assert res.near_misses.empty or "sz000006" not in res.near_misses["code"].tolist() or \
        res.near_misses.set_index("code").loc["sz000006", "missed_rule"]


def test_describe_lists_every_rule():
    rules = screener.describe(screener.ScreenParams())
    assert len(rules) == 10
    assert any("5-day MA crossed above" in r for r in rules)


def test_params_roundtrip():
    p = screener.ScreenParams(pct_min=1.0, boards=("Main",))
    assert screener.ScreenParams.from_dict(p.to_dict()) == p
