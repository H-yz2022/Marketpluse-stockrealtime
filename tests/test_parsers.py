"""Parsers against real recorded Tencent responses (offline)."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from stockrt.sources import tencent

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def quotes():
    rows = tencent.parse_quote_text((FIX / "tencent_quotes.txt").read_text(encoding="utf-8"))
    return {r["code"]: r for r in rows}


def test_unknown_symbol_is_skipped(quotes):
    assert "xx999999" not in quotes
    assert {"sh600519", "hk00700", "sh000300", "hkHSI", "sh688707", "sh510300"} <= set(quotes)


def test_a_share_units(quotes):
    q = quotes["sh600519"]
    assert q["market"] == "CN" and q["currency"] == "CNY"
    # Volume converted from 100-share lots: value traded / VWAP must equal shares.
    assert q["amount"] / q["vwap"] == pytest.approx(q["volume"], rel=0.01)
    assert q["float_mcap"] > 1e12  # quoted in 100M CNY, converted to CNY
    assert 0 < q["turnover_rate"] < 100 and q["volume_ratio"] > 0
    assert q["buy_volume"] + q["sell_volume"] == pytest.approx(q["volume"], rel=0.01)


def test_star_market_volume_already_in_shares(quotes):
    q = quotes["sh688707"]
    assert q["amount"] / q["vwap"] == pytest.approx(q["volume"], rel=0.01)


def test_etf_units(quotes):
    q = quotes["sh510300"]
    assert q["amount"] / q["vwap"] == pytest.approx(q["volume"], rel=0.01)
    assert q["pb"] is None  # Tencent writes 0 for 'not applicable'


def test_hk_stock(quotes):
    q = quotes["hk00700"]
    assert q["market"] == "HK" and q["currency"] == "HKD"
    assert q["amount"] / q["vwap"] == pytest.approx(q["volume"], rel=0.01)
    assert q["time"].tzinfo is not None


def test_hk_index_turnover_scaled(quotes):
    q = quotes["hkHSI"]
    assert q["volume"] is None and q["amount"] > 1e10  # HK$ tens of billions, not 10k units
    assert q["pe_ttm"] is None


def test_index_pe(quotes):
    assert 5 < quotes["sh000300"]["pe_ttm"] < 40


def test_minute_series_per_minute_volume():
    payload = json.loads((FIX / "tencent_minute.json").read_text(encoding="utf-8"))

    class R:
        def json(self):
            return payload

    with patch("stockrt.http.get", return_value=R()):
        day, prev_close, bars = tencent.minute_today("sh600519")
    assert prev_close > 0
    assert (bars["volume"] >= 0).all()
    # Per-minute volumes are diffs of the cumulative series.
    assert bars["cum_volume"].iloc[4] == pytest.approx(bars["volume"].iloc[:5].sum())
    assert set(bars["session"]) <= {"AM", "PM", "post", "auction"}
    assert bars["datetime"].dt.date.iloc[0] == day
