"""收盘选股: shortlist, resistance / bull-bear readings, and the archived screen (offline)."""

from datetime import date
from unittest.mock import patch

import numpy as np
import pandas as pd

from stockrt import archive, closescreen

DAY = date(2026, 10, 15)


def bars(closes, highs=None, vol=1e6, today_close=None, today_vol=3e6):
    d = pd.bdate_range(end="2026-10-14", periods=len(closes))
    df = pd.DataFrame({"date": d, "open": closes, "high": highs if highs is not None else closes,
                       "low": closes, "close": closes, "volume": vol, "turnover_rate": 1.0, "amount": 1e7,
                       "event": ""})
    if today_close is not None:
        df = pd.concat([df, pd.DataFrame([{"date": pd.Timestamp(DAY), "open": today_close, "high": today_close,
                                           "low": today_close, "close": today_close, "volume": today_vol,
                                           "turnover_rate": 5.0, "amount": 1e8, "event": ""}])], ignore_index=True)
    return df


def test_shortlist_uses_yesterday_book():
    book = pd.DataFrame({"code": ["sh600001", "sh600002", "sh600003", "sh600004"],
                         "name": ["甲", "乙", "*ST丙", "丁"], "volume": [3e6, 3e6, 3e6, 3e6], "close": 10.0,
                         "turnover_rate": [5.0, 2.0, 5.0, 5.0]})
    prev = pd.DataFrame({"code": ["sh600001", "sh600002", "sh600003", "sh600004"], "volume": [1e6, 1e6, 1e6, 2e6]})
    assert closescreen.shortlist(book, prev)["code"].tolist() == ["sh600001"]  # turnover, ST, only 1.5x
    assert len(closescreen.shortlist(book, None)) == 2                           # no book: volume left to history


def test_features_and_rules():
    closes = np.r_[np.full(240, 8.0), np.linspace(9, 10, 20)]   # MA250 ~ 8.1; 20-day high 10
    with patch.object(closescreen.tencent, "daily", return_value=bars(closes, today_close=10.5)):
        f = closescreen.features("sh600001", DAY, close_raw=10.5, volume=3e6)
    assert f["res20"] == 10.0 and f["vol_x"] == 3.0 and f["close_adj"] == 10.5
    assert 8.0 < f["ma250"] < 8.3 and f["sessions"] == 260
    row = pd.DataFrame([{**f, "name": "甲", "is_st": False, "turnover_rate": 5.0, "close": 10.5, "pct_change": 5.0}])
    d = closescreen.apply(row)
    assert d["passes"].iloc[0] and d["missed"].iloc[0] == ""
    low = closescreen.apply(row.assign(close_adj=9.9))
    assert not low["passes"].iloc[0] and low["missed"].iloc[0] == "突破压力位"


def test_short_history_has_no_bull_bear_line():
    with patch.object(closescreen.tencent, "daily", return_value=bars(np.linspace(5, 6, 100))):
        f = closescreen.features("sh600001", DAY, close_raw=6.5, volume=3e6)
    assert np.isnan(f["ma250"]) and np.isnan(f["ma120"])     # 100 sessions: too young for either line
    d = closescreen.apply(pd.DataFrame([{**f, "name": "新", "is_st": False, "turnover_rate": 5.0}]))
    assert not d["passes"].iloc[0] and "站上牛熊线" in d["missed"].iloc[0]


def test_run_archives_the_screen(tmp_path, monkeypatch):
    monkeypatch.setattr(archive, "RESULTS_DIR", tmp_path)
    archive.save_volumes("CN", date(2026, 10, 14), pd.DataFrame({"code": ["sh600001"], "name": ["甲"],
                                                                 "volume": [1e6], "close": [10.0]}))
    book = pd.DataFrame({"code": ["sh600001"], "name": ["甲"], "volume": [3e6], "close": [10.5], "pct_change": 5.0,
                         "amount": 3e7, "turnover_rate": [5.0], "float_shares": [6e7]})
    closes = np.r_[np.full(240, 8.0), np.linspace(9, 10, 20)]
    with patch.object(closescreen.tencent, "daily", return_value=bars(closes, today_close=10.5)):
        res = closescreen.run(DAY, book=book, workers=1)
    assert res["shortlisted"] == 1 and archive.screen_days() == [DAY]
    frame, meta = archive.load_screen(DAY)
    assert closescreen.apply(frame)["passes"].all() and meta["shortlisted"] == 1
    assert "成交量翻倍" in (tmp_path / "cn" / "screens" / f"{DAY}.md").read_text(encoding="utf-8")
