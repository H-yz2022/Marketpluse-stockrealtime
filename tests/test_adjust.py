"""Ratio-based price adjustment."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from stockrt import adjust


def bars(closes, start="2026-06-22"):
    d = pd.bdate_range(start, periods=len(closes))
    return pd.DataFrame({"date": d, "open": closes, "high": closes, "low": closes, "close": closes,
                         "event": [""] * len(closes)})


def test_multiplicative_factor_ex_date_matches_reference_price():
    # Moutai 2026-06-26: CNY 28.0242 cash dividend; Sina qfq factor 1.02367 before, 1.0 after.
    df = bars([1212.10, 1168.63], start="2026-06-25")
    segs = [(date(2025, 12, 19), 1 / 1.0236675985693, 0.0), (date(2026, 6, 26), 1.0, 0.0)]
    r = adjust.returns_from_segments(df["date"], df["close"], segs)
    assert r[1] == pytest.approx(1168.63 / (1212.10 - 28.0242) - 1, abs=2e-5)


def test_affine_hk_cash_and_split():
    # Cash: f=5 both sides, cumulative cash +26.5 (HK$5.30 per current share).
    df = bars([450.0, 456.4])
    segs = [(date(2025, 5, 16), 5.0, 252.43), (date(2026, 6, 23), 5.0, 278.93)]
    r = adjust.returns_from_segments(df["date"], df["close"], segs)
    assert r[1] == pytest.approx(456.4 / (450.0 - 5.30) - 1, rel=1e-9)
    # 1:5 split: factor 1 -> 5, no cash.
    df = bars([514.0, 108.8])
    segs = [(date(2000, 1, 1), 1.0, 10.0), (date(2026, 6, 23), 5.0, 10.0)]
    r = adjust.returns_from_segments(df["date"], df["close"], segs)
    assert r[1] == pytest.approx(108.8 * 5 / 514.0 - 1)


def test_within_segment_returns_are_raw():
    df = bars([10.0, 11.0, 12.1])
    segs = [(date(2000, 1, 1), 0.5, -2.0)]
    r = adjust.returns_from_segments(df["date"], df["close"], segs)
    assert np.allclose(r[1:], [0.1, 0.1])


def test_apply_returns_anchors_last_close_and_never_goes_negative():
    df = bars([100.0, 90.0, 95.0, 30.0])
    ret = np.array([np.nan, -0.1, 0.05, 0.02])  # last day: big dividend, true return +2%
    out = adjust.apply_returns(df, ret)
    assert out["close"].iloc[-1] == 30.0
    assert (out["close"] > 0).all()
    assert out["close"].pct_change().iloc[1:].to_numpy() == pytest.approx(ret[1:])
    assert out["close_raw"].tolist() == [100.0, 90.0, 95.0, 30.0]


def test_parse_cn_dividend_text():
    assert adjust.parse_cn_dividend("10派280.242元") == pytest.approx((28.0242, 0.0))
    assert adjust.parse_cn_dividend("10送3股转2股派5元") == pytest.approx((0.5, 0.5))
    assert adjust.parse_cn_dividend("10转增4股") == pytest.approx((0.0, 0.4))
    assert adjust.parse_cn_dividend("") is None


def test_parse_hk_dividend_text():
    assert adjust.parse_hk_dividend("中期息0.19港元") == pytest.approx(0.19)
    assert adjust.parse_hk_dividend("第二次中期息0.1美元") == pytest.approx(0.78)
    assert adjust.parse_hk_dividend("末期息0.25人民币") is None


def test_events_route_matches_reference_price():
    df = bars([4.859, 4.738], start="2026-01-16")
    df.loc[1, "event"] = "10派1.23元"
    ev = adjust.events_from_text(df, "CN")
    r = adjust.returns_from_events(df["date"], df["close"], ev)
    assert r[1] == pytest.approx(4.738 / (4.859 - 0.123) - 1)


def test_dividends_from_fund_and_hk_segments():
    fund = [(date(2025, 6, 18), 1.0, -0.123), (date(2026, 1, 19), 1.0, 0.0)]
    assert adjust.dividends_from_segments(fund) == [(date(2026, 1, 19), pytest.approx(0.123))]
    hk = [(date(2025, 5, 16), 5.0, 252.43), (date(2026, 5, 15), 5.0, 278.93)]
    assert adjust.dividends_from_segments(hk)[0][1] == pytest.approx(5.30)
    ratio_only = [(date(2025, 1, 1), 0.9, 0.0), (date(2026, 1, 1), 1.0, 0.0)]
    assert adjust.dividends_from_segments(ratio_only) == []
