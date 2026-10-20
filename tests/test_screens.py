"""主力暗中吸筹, 长线, the history panel and the progress estimate (offline)."""

from datetime import date
from unittest.mock import patch

import numpy as np
import pandas as pd

from stockrt import accumulation, archive, longterm, panel

DAY = date(2026, 10, 20)


def test_accumulation_surge_and_price_band():
    f = pd.DataFrame({
        "code": ["a", "b", "c", "d"], "name": list("甲乙丙丁"), "yday_vs_ref": [2.0, 1.5, 1.2, 3.0],
        "flow_09:31": [1e6, 1e6, -2e6, 1e6],
        "flow_09:45": [5e6, 1.5e6, 2e6, 9e6],          # a: +400% (+4M) b: +50% c: outflow -> inflow d: +800%
        "pct_09:45": [0.5, 0.2, -0.3, 1.0],             # d: exactly +1% is outside (-1, 1)
    })
    d = accumulation.apply(f, ("09:45",)).set_index("code")
    assert d.loc["a", "hit_09:45"] and d.loc["c", "hit_09:45"]
    assert not d.loc["b", "hit_09:45"] and not d.loc["d", "hit_09:45"]
    loose = accumulation.apply(f, ("09:45",), accumulation.Params(surge_pct=40, min_increase_m=0.4)).set_index("code")
    assert loose.loc["b", "hit_09:45"]


def test_accumulation_needs_five_volume_books(tmp_path, monkeypatch):
    monkeypatch.setattr(archive, "RESULTS_DIR", tmp_path)
    vols = {"2026-10-13": 100, "2026-10-14": 110, "2026-10-15": 120, "2026-10-16": 130, "2026-10-19": 500}
    for d, v in vols.items():
        archive.save_volumes("CN", date.fromisoformat(d), pd.DataFrame({
            "code": ["sh600001", "sh600002"], "name": ["甲", "乙"], "volume": [v * 1e5, 100e5], "amount": 5e7,
            "close": 10.0, "pct_change": 0.5}))
    yday, d = accumulation.yesterday_list(DAY)
    assert yday == date(2026, 10, 19) and d["code"].tolist() == ["sh600001"]
    assert d["yday_vs_ref"].iloc[0] == 500 / 130


def test_longterm_rules_and_switches():
    snap = pd.DataFrame({"code": ["sh600001", "sh600002", "sh600003"], "name": ["甲", "乙", "*ST丙"],
                         "price": [8.0, 8.0, 8.0], "volume": 1e6, "pe_ttm": [10.0, 50.0, 10.0], "pb": [1.0, 1.0, 1.0],
                         "turnover_rate": [5.0, 5.0, 5.0]})
    s = longterm.shortlist(snap)
    assert s["code"].tolist() == ["sh600001"] and s["roe"].iloc[0] == 10.0       # ROE = P/B / P/E
    frame = s.assign(pct_change=1.0, float_mcap=1e9, amount=1e8, institutions=[3.0], inst_float_pct=5.0,
                     inst_report="2026-06-30", inflow10=[2e7], ma_spread_0=[3.0], ma_spread_1=[1.5],
                     ma_spread_2=[4.0])
    assert longterm.apply(frame)["passes"].iloc[0]                                 # converged 1 session ago
    assert not longterm.apply(frame, longterm.Params(ma_within=1))["passes"].iloc[0]
    assert longterm.apply(frame, longterm.Params(ma_within=1, use_ma=False))["passes"].iloc[0]
    assert not longterm.apply(frame.assign(institutions=np.nan))["passes"].iloc[0]


def test_panel_stands_in_for_daily_history(tmp_path, monkeypatch):
    monkeypatch.setattr(archive, "RESULTS_DIR", tmp_path)
    d = pd.bdate_range(end="2026-10-19", periods=30)
    df = pd.DataFrame({"code": "sh600001", "date": d.date, "open": 10.0, "close": np.linspace(9, 10, 30),
                       "high": 10.5, "low": 9.0, "volume": 1e6, "amount": 1e7, "turnover_rate": 1.0})
    panel.save(df)
    with patch("stockrt.tradingdays.previous_trading_day", return_value=date(2026, 10, 19)):
        h = panel.history("sh600001", DAY, prev_close=10.0)
        assert len(h) == 30 and h["close"].iloc[-1] == 10.0
        assert panel.history("sh600001", DAY, prev_close=9.5) is None         # ex-date: download instead
        assert panel.history("sh600999", DAY, prev_close=10.0) is None        # not in the panel
    with patch("stockrt.tradingdays.previous_trading_day", return_value=date(2026, 10, 20)):
        assert panel.history("sh600001", date(2026, 10, 21), prev_close=10.0) is None   # panel is stale


def test_progress_estimate():
    import time

    from stockrt import jobs
    from ui import progress

    job = jobs.Job(("k",), state="running", progress=0.5, started=time.time() - 30)
    assert 25 <= progress.eta(job) <= 35
    early = jobs.Job(("k",), state="running", progress=0.01, started=time.time() - 5)
    assert 50 <= progress.eta(early, typical=60) <= 60
