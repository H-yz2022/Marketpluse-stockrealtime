"""Symbols, calendar, analytics, intraday stats, SQL guard, sentiment."""

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from stockrt import analytics, calendar, intraday, query, sentiment, symbols
from stockrt.calendar import BJ


@pytest.mark.parametrize("raw,expected", [
    ("600519", "sh600519"), ("600519.SH", "sh600519"), ("600519.ss", "sh600519"), ("SZ000858", "sz000858"),
    ("000001", "sz000001"), ("300750", "sz300750"), ("159915", "sz159915"), ("510300", "sh510300"),
    ("688981", "sh688981"), ("920000", "bj920000"), ("0700.HK", "hk00700"), ("700", "hk00700"),
    ("hk9988", "hk09988"), ("HSI", "hkHSI"), ("sh000300", "sh000300"),
])
def test_normalize(raw, expected):
    assert symbols.normalize(raw) == expected


def test_symbol_helpers():
    assert symbols.em_secid("sh600519") == "1.600519"
    assert symbols.em_secid("hk00700") == "116.00700"
    assert symbols.from_em_secid("0.000858") == "sz000858"
    assert symbols.volume_unit("sh688707") == 1 and symbols.volume_unit("sz000858") == 100
    assert symbols.board("sz300750") == "ChiNext" and symbols.board("sh688981") == "STAR"
    assert symbols.is_index("sh000300") and not symbols.is_index("sz000300")
    assert symbols.is_fund("sz159915") and symbols.is_st("*ST东园")


@pytest.mark.parametrize("hhmm,phase,elapsed", [
    ("09:00", "closed", 0), ("09:20", "pre-open", 0), ("10:30", "morning", 60), ("12:00", "lunch", 120),
    ("14:00", "afternoon", 180), ("15:05", "closed", 240),
])
def test_cn_sessions(hhmm, phase, elapsed):
    h, m = map(int, hhmm.split(":"))
    s = calendar.status("CN", datetime(2026, 10, 9, h, m, tzinfo=BJ))  # a Friday
    assert (s.phase, s.elapsed_min) == (phase, elapsed)


def test_hk_and_weekend():
    assert calendar.status("HK", datetime(2026, 10, 9, 15, 30, tzinfo=BJ)).to_close_min == 30
    assert calendar.status("CN", datetime(2026, 10, 10, 10, 0, tzinfo=BJ)).phase == "weekend"
    assert calendar.total_minutes("CN") == 240 and calendar.total_minutes("HK") == 330


def series(values, start="2024-01-02"):
    return pd.Series(values, index=pd.bdate_range(start, periods=len(values)), dtype=float)


def test_return_drawdown_vol():
    s = series([100, 110, 99, 120])
    assert analytics.total_return(s) == pytest.approx(0.2)
    assert analytics.max_drawdown(s) == pytest.approx(99 / 110 - 1)
    r = analytics.daily_returns(series(np.linspace(100, 110, 300)))
    assert analytics.ann_vol(r) < 0.01


def test_regression_recovers_beta():
    rng = np.random.default_rng(0)
    x = pd.Series(rng.normal(0, 0.01, 500))
    y = 2 * x + rng.normal(0, 0.001, 500)
    reg = analytics.regression(y, x)
    assert reg["beta"] == pytest.approx(2, rel=0.02)
    assert reg["systematic_share"] > 0.95


def test_windows_and_yearly():
    s = series(np.arange(1, 600, dtype=float))
    assert len(analytics.window(s, "1M")) in range(20, 25)
    assert analytics.window(s, "5Y").empty  # not enough history
    y = analytics.yearly_returns(s)
    assert y.index.tolist() == [2024, 2025, 2026]


def test_intraday_stats():
    t = pd.date_range("2026-10-09 09:30", periods=121, freq="min").append(
        pd.date_range("2026-10-09 13:01", periods=60, freq="min"))
    price = np.r_[np.linspace(10, 10.5, 121), np.linspace(10.5, 10.2, 60)]
    bars = pd.DataFrame({"datetime": t, "price": price, "volume": 100.0, "amount": price * 100,
                         "session": ["AM"] * 121 + ["PM"] * 60})
    s = intraday.stats(intraday.enrich(bars, 10.0), 10.0, "CN")
    assert s["am_return"] == pytest.approx(0.05)
    assert s["pm_return"] == pytest.approx(10.2 / 10.5 - 1)
    assert s["high_time"] == "11:30" and s["minutes"] == 181
    assert len(intraday.cut(bars, "14:00")) == 121 + 60


def test_sql_guard():
    tables = {"snapshot": pd.DataFrame({"code": ["a", "b"], "pct_change": [1.0, 4.0]})}
    out = query.run("SELECT code FROM snapshot WHERE pct_change > 3", tables)
    assert out["code"].tolist() == ["b"]
    for bad in ("DELETE FROM snapshot", "SELECT 1; DROP TABLE snapshot", "ATTACH 'x.db' AS y"):
        with pytest.raises(ValueError):
            query.run(bad, tables)


def test_sentiment_components():
    q = {"buy_volume": 70, "sell_volume": 30, "pct_change": 3.0, "high": 11, "low": 10, "price": 10.9}
    s = sentiment.stock_sentiment(q, {"main_net_ratio": 10.0}, 0.5, ["关于股份回购的公告", "关于股东减持的公告"])
    assert set(s["components"]) == {"Order flow", "Main funds", "Relative strength", "Close location", "Announcements"}
    assert s["score"] > 0 and s["label"] in ("Bullish", "Mildly bullish")
