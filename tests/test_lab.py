"""Strategy lab: the 30-minute rebuild, rules, scoring and the 4-day volume query (offline, synthetic data)."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from stockrt import archive, lab, labdata, opening, screener, strategies

TIMES = ["1000", "1030", "1100", "1130", "1330", "1400", "1430", "1500"]


def bars_for(days: list[str], paths: dict[str, list[float]], vol: float = 1e5) -> pd.DataFrame:
    """8 bars per day; paths[day] = 8 bar closes (opens = previous close, first open = closes[0])."""
    rows = []
    for d in days:
        closes = paths[d]
        prev = closes[0]
        for t, c in zip(TIMES, closes):
            rows.append({"datetime": pd.Timestamp(f"{d} {t[:2]}:{t[2:]}"), "open": prev, "close": c,
                         "high": max(prev, c), "low": min(prev, c), "volume": vol})
            prev = c
    return pd.DataFrame(rows)


def daily_for(days: list[str], closes: list[float], vol: float = 8e5, offset: float = 0.0) -> pd.DataFrame:
    return pd.DataFrame({"date": pd.to_datetime(days), "open": closes, "close": [c + offset for c in closes],
                         "high": [c + offset for c in closes], "low": closes, "volume": vol,
                         "turnover_rate": 1.0, "amount": 1e8, "event": ""})


def test_intraday_reads_each_signal_time():
    b = bars_for(["2026-10-08"], {"2026-10-08": [10.1, 10.2, 10.0, 10.3, 10.4, 10.5, 10.6, 10.7]})
    f = labdata.intraday(b).iloc[0]
    assert f["p_open"] == 10.1 and f["p_1000"] == 10.1 and f["p_1030"] == 10.2 and f["p_1430"] == 10.6
    assert f["v_1030"] == 2e5 and f["v_1500"] == 8e5
    assert f["hi_1430"] == 10.6 and f["lo_1430"] == 10.0
    # a bar still forming (label after `asof`) is ignored
    g = labdata.intraday(b, asof=pd.Timestamp("2026-10-08 10:45")).iloc[0]
    assert g["p_1030"] == 10.2 and np.isnan(g["p_1430"])


def test_frame_offsets_exits_and_limits():
    days = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2026-08-03", periods=30)]
    closes = list(np.linspace(10, 11, 30))
    closes[-3] = round(closes[-4] * 1.1, 2)            # a limit-up close three sessions before the end
    paths = {d: [c] * 8 for d, c in zip(days, closes)}
    cal = [pd.Timestamp(d) for d in days]
    # qfq history 0.2 lower than raw (a dividend paid after the window): the offset joins the two
    f = labdata.stock_frame("sh600001", "甲", daily_for(days, closes, offset=-0.2), bars_for(days, paths), cal)
    assert f["off"].round(3).eq(-0.2).all()
    assert f["prev_close"].iloc[5] == pytest.approx(closes[4])          # raw basis
    lu = f.index[f["closed_limit_up"]]
    assert list(lu) == [len(f) - 3]
    assert f["yday_limit_up"].iloc[-2] and f["streak_prev"].iloc[-2] == 1
    # exits: next session's close in the entry's basis; none after the last session
    assert f["x_t1_close"].iloc[10] == pytest.approx(closes[11])                # raw prices
    assert np.isnan(f["x_t1_close"].iloc[-1]) and np.isnan(f["x_t2_close"].iloc[-2])
    s = labdata.state(f, "14:30")
    assert s["ret_t1_close"].iloc[10] == pytest.approx(closes[11] / closes[10] - 1, rel=1e-6)


def test_exits_skip_a_suspension_and_an_ex_date():
    days = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2026-08-03", periods=20)]
    closes = [10.0] * 20
    cal = [pd.Timestamp(d) for d in days]
    traded = days[:10] + days[11:]                                     # suspended on day 10
    f = labdata.stock_frame("sz000002", "乙", daily_for(traded, closes[:19]),
                            bars_for(traded, {d: [10.0] * 8 for d in traded}), cal)
    i9 = f.index[f["date"] == pd.Timestamp(days[9])][0]
    assert np.isnan(f.loc[i9, "x_t1_open"])                            # its next session is missed
    d2 = daily_for(days, closes)
    d2.loc[:14, "close"] -= 0.5                                         # dividend: qfq history shifted before day 15
    f2 = labdata.stock_frame("sz000002", "乙", d2, bars_for(days, {d: [10.0] * 8 for d in days}), cal)
    assert np.isnan(f2.loc[14, "x_t1_close"]) and not np.isnan(f2.loc[15, "x_t1_close"])


def test_cross_rule_matches_the_front_page():
    rng = np.random.default_rng(3)
    for _ in range(200):
        closes = 10 * np.cumprod(1 + rng.normal(0, 0.02, 25))
        today = closes[-1]
        hist = pd.Series(closes[:-1])
        tf = screener.trend_features(pd.Series(list(hist) + [today]))
        front = tf["ma5"] > tf["ma10"] and tf["cross_days_ago"] is not None and tf["cross_days_ago"] < 3
        q = hist
        sp = q.rolling(5).mean() - q.rolling(10).mean()
        ma5 = (q.tail(4).sum() + today) / 5
        ma10 = (q.tail(9).sum() + today) / 10
        lab_rule = ma5 > ma10 and (sp.iloc[-1] <= 0 or sp.iloc[-2] <= 0 or sp.iloc[-3] <= 0)
        assert lab_rule == front


def _state(**cols) -> pd.DataFrame:
    n = len(next(iter(cols.values())))
    base = {"code": [f"sh60000{i}" for i in range(n)], "name": "x", "day": pd.Timestamp("2026-10-09"),
            "eligible": True, "price": 10.0, "open": 10.0, "prev_close": 10.0, "limit_up": 11.0, "sealed": False,
            "at_limit": False, "yday_limit_up": False, "gap_pct": 0.0, "float_mcap": 10e9}
    base.update(cols)
    return pd.DataFrame(base)


def test_first_limit_up_and_gap_rules():
    s = _state(price=[11.0, 11.0, 11.0], at_limit=[True, True, True], yday_limit_up=[False, True, False],
               sealed=[False, False, True])
    ev = strategies.evaluate(strategies.STRATEGIES["S4"], s)
    assert ev["core"].tolist() == [True, False, False]     # 2nd board / sealed since the open are out
    g = _state(gap_pct=[3.0, 8.0, 3.0], open=[10.3, 10.8, 11.0], float_mcap=[10e9, 10e9, 10e9])
    ev = strategies.evaluate(strategies.STRATEGIES["S2"], g)
    assert ev["core"].tolist() == [True, False, False]     # gap too big; limit-up open


def test_live_only_rules_three_states():
    st = strategies.STRATEGIES["S1"]
    picks = pd.DataFrame({"main_net": [5e6, -1e6, np.nan]})
    assert strategies.live_pass(st, picks).tolist()[:2] == [True, False]
    assert pd.isna(strategies.live_pass(st, picks).iloc[2])


def _trades_days(n_days=40, k=3, edge=0.004, seed=1):
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2026-01-05", periods=n_days)
    rows, drows = [], []
    for d in days:
        univ = rng.normal(0, 0.02, 400)
        picks = rng.choice(univ, k, replace=False) + edge
        drows += [{"day": d, "time": t, "ew_t1_close": univ.mean(), "sd_t1_close": univ.std(ddof=1),
                   "n_t1_close": 400, "csi300_t1_close": 0.0, "yday_lu_avg": 1.0} for t in strategies.SIGNAL_ORDER]
        rows += [{"strategy": "S5", "day": d, "code": f"c{i}", "ret_t1_close": r, "all": True}
                 for i, r in enumerate(picks)]
    return pd.DataFrame(rows), pd.DataFrame(drows)


def test_metrics_and_verdict():
    tr, dy = _trades_days(edge=0.02)
    m = lab.metrics(tr, dy, "S5")
    assert m["trades"] == 120 and m["days"] == 40
    assert m["avg_excess"] > 0.01 and m["t"] > 2 and m["placebo"] > 0.99
    assert m["verdict"] == "Passes"
    tr0, dy0 = _trades_days(edge=-0.01, seed=2)
    assert lab.metrics(tr0, dy0, "S5")["verdict"] == "Fails"
    few = lab.metrics(tr.head(9), dy, "S5", live=True)
    assert few["verdict"].startswith("Too early")


def test_placebo_matches_simulation():
    rng = np.random.default_rng(7)
    univ = {d: rng.normal(0, 0.02, 300) for d in range(30)}
    picks = {d: rng.choice(univ[d], 4, replace=False) + 0.002 for d in univ}
    x = np.mean([p - univ[d].mean() for d in univ for p in picks[d]])
    var = sum(4 * univ[d].std(ddof=1) ** 2 * (300 - 4) / 299 for d in univ) / (120 ** 2)
    analytic = lab._phi(x / np.sqrt(var))
    sims = np.array([np.mean([rng.choice(univ[d], 4, replace=False) - univ[d].mean() for d in univ])
                     for _ in range(2000)])
    assert analytic == pytest.approx((sims < x).mean(), abs=0.03)


def test_volume_vs_previous_four_days(tmp_path, monkeypatch):
    monkeypatch.setattr(archive, "RESULTS_DIR", tmp_path)
    codes = ["sh600001", "sh600002", "sh600003"]
    for i, d in enumerate(["2026-09-29", "2026-09-30", "2026-10-08", "2026-10-09"]):
        archive.save_volumes("CN", date.fromisoformat(d), pd.DataFrame(
            {"code": codes, "name": ["甲", "乙", "*ST丙"], "volume": [100.0, 100.0 + 50 * i, 50.0]}))
    hist = archive.load_volume_history("CN", before=date(2026, 10, 12), n=4)
    assert [d for d, _ in hist] == [date(2026, 10, 9), date(2026, 10, 8), date(2026, 9, 30), date(2026, 9, 29)]
    snap = pd.DataFrame({"code": codes, "name": ["甲", "乙", "*ST丙"], "is_st": [False, False, True],
                         "volume": [150.0, 200.0, 500.0], "amount": 1e8, "price": 10.0})
    each = opening.volume_vs_prior(snap, hist, "each")
    assert each["code"].tolist() == ["sh600001"]                  # 200 < 250 (day 4); ST excluded
    assert opening.volume_vs_prior(snap, hist, "each", exclude_st=False)["code"].tolist()[0] == "sh600003"
    assert opening.volume_vs_prior(snap, hist, "sum").empty      # nobody beats 4 days combined
    half = opening.volume_vs_prior(snap, hist, "each", frac=0.5)  # full-day pace doubles today's volume
    assert set(half["code"]) == {"sh600001", "sh600002"}
