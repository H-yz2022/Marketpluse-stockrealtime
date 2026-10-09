"""Opening-auction research: volume surge, auction phases, limits and the 竞价选股 screen (offline)."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from stockrt import archive, opening


def snap():
    return pd.DataFrame({
        "code": ["sh600001", "sz300002", "sh600003", "sh600004"],
        "name": ["甲", "乙", "*ST丙", "丁"],
        "is_st": [False, False, True, False],
        "price": [10.5, 22.0, 5.2, 11.0], "prev_close": [10.0, 20.0, 5.0, 10.0],
        "open": [10.4, 22.0, 5.1, 11.0], "volume": [6e6, 1e6, 8e6, 3e5], "amount": [6.3e7, 2.2e7, 4e7, 3.3e6],
        "bid1": [10.4, 22.0, 5.1, 11.0], "ask1": [10.4, 22.0, 5.1, 11.0],
        "bid1_volume": [4e5, 1e5, 2e5, 3e5], "limit_up": [11.0, 24.0, 5.25, 11.0],
        "float_mcap": [8e9, 12e9, 3e9, 6e9], "float_shares": [8e8, 6e8, 6e8, 5e8], "turnover_rate": 1.0,
        "time": pd.Timestamp("2026-10-09 09:40", tz="Asia/Shanghai"),
    })


def book():
    return pd.DataFrame({"code": ["sh600001", "sz300002", "sh600003", "sh600004"],
                         "volume": [1e6, 1e6, 1e6, 1e6], "amount": [1e7, 2e7, 5e6, 1e7],
                         "pct_change": [2.0, 19.98, -1.0, 10.0]})


def test_volume_surge_counts_today_against_yesterday():
    d = opening.volume_surge(snap(), book(), multiple=5)
    assert d["code"].tolist() == ["sh600001"]               # 6x yesterday; the 8x one is ST
    assert d["vol_multiple"].iloc[0] == pytest.approx(6.0)
    assert opening.volume_surge(snap(), book(), 5, exclude_st=False)["code"].tolist()[0] == "sh600003"


def test_daily_limits_and_yesterday_limit_up():
    assert opening.limit_pct("sh600001") == 10 and opening.limit_pct("sz300002") == 20
    assert opening.limit_pct("sh688001") == 20 and opening.limit_pct("bj920001") == 30
    assert opening.limit_pct("sh600003", "*ST丙") == 5
    d = opening.auction_frame(snap(), book(), "result")
    assert d.set_index("code")["yday_limit_up"].to_dict() == {
        "sh600001": False, "sz300002": True, "sh600003": False, "sh600004": True}


def test_auction_phases():
    ind = opening.auction_frame(snap(), book(), "indicative").set_index("code")
    assert ind.loc["sh600001", "auction_volume"] == 4e5         # best-bid volume while bid == ask
    res = opening.auction_frame(snap(), book(), "result").set_index("code")
    assert res.loc["sh600001", "auction_volume"] == 6e6         # 09:25-09:30: volume is the auction's
    assert res.loc["sh600004", "limit_up_open"]                  # opened at the limit (一字板)
    after = opening.auction_frame(snap(), book(), "after_open").set_index("code")
    assert np.isnan(after.loc["sh600001", "auction_volume"])     # read later from the 09:30 bar
    assert after.loc["sh600001", "gap_pct"] == pytest.approx(4.0)


def test_screen_and_shortlist():
    p = opening.AuctionParams(vs_yday_min_pct=10.0, amount_min_m=1.0, turnover_min=0.01, cap_min_bn=1.0)
    d = opening.auction_frame(snap(), book(), "result")
    assert opening.cheap_mask(d, p).tolist() == [True, False, False, False]  # gap 20% too big, ST, limit-up
    r = opening.screen(d, p, after_open=False).set_index("code")
    assert r.loc["sh600001", "passes"]
    assert r.loc["sh600001", "auction_vs_yday"] == pytest.approx(600.0)
    assert "Not opening at limit-up" in r.loc["sh600004", "missed"]
    labels = [lbl for _, lbl, _ in opening.active_rules(p, after_open=True)]
    assert "Gap still holding" not in labels  # off unless asked for


def test_volume_book_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(archive, "RESULTS_DIR", tmp_path)
    s = snap().assign(time=pd.Timestamp("2026-10-08 15:00", tz="Asia/Shanghai"))
    assert archive.save_volumes_from_snapshot("CN", s) is not None
    assert archive.save_volumes_from_snapshot("CN", s) is None    # once per session
    day, b = archive.load_volumes("CN", before=date(2026, 10, 9))
    assert day == date(2026, 10, 8) and b.set_index("code").loc["sh600001", "close"] == 10.5
    assert archive.load_volumes("CN", before=date(2026, 10, 8)) is None
