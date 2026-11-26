"""资金选股 (basic / advanced and hot-theme versions) and the weekly concept map."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from stockrt import archive, flowpicks, http, sample, themes


@pytest.fixture
def results(tmp_path, monkeypatch):
    monkeypatch.setattr(archive, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(sample, "is_sample", lambda: False)
    themes._cache.clear()
    return tmp_path


def test_only_real_themes_are_kept():
    for name in ("算力概念", "机器人概念", "人形机器人", "CPO概念", "深圳特区", "华为概念", "小金属概念", "上海自贸"):
        assert themes.is_theme(name), name
    for name in ("HS300_", "上证50_", "融资融券", "深股通", "基金重仓", "昨日涨停", "大盘成长", "MSCI中国", "破净股",
                 "2026三季报预增", "科技风格", "广东板块", "黑龙江", "东方财富热股"):
        assert not themes.is_theme(name), name
    assert not themes.is_theme("银行", "行业")                     # industries are a different kind of board
    assert themes._code("603636.SH") == "sh603636" and themes._code("920010.BJ") == "bj920010"
    assert themes._code("00700.HK") is None


def test_concept_map_is_downloaded_whole_and_kept_for_a_week(results, monkeypatch):
    rows = [{"SECUCODE": f"{600000 + i}.SH", "BOARD_NAME": b, "NEW_BOARD_CODE": "BK1", "BOARD_TYPE": None}
            for i in range(6000) for b in (["算力概念"] if i % 2 else ["融资融券"])]
    rows += [{"SECUCODE": "600001.SH", "BOARD_NAME": "机器人概念", "NEW_BOARD_CODE": "BK2", "BOARD_TYPE": None},
             {"SECUCODE": "600001.SH", "BOARD_NAME": "银行", "NEW_BOARD_CODE": "BK3", "BOARD_TYPE": "行业"}]
    pages = [rows[i:i + 3000] for i in range(0, len(rows), 3000)]
    monkeypatch.setattr(themes, "MIN_ROWS", 100)
    monkeypatch.setattr(themes, "_page", lambda n: {"pages": len(pages), "data": pages[n - 1]})
    res = themes.refresh()
    assert res["boards"] == 2 and res["downloaded"] == len(rows)       # 融资融券 and the industry left out
    m = themes.load_map()
    assert set(m["board"]) == {"算力概念", "机器人概念"}
    both = themes.members(m, ["机器人概念", "算力概念"]).set_index("code")
    assert both.loc["sh600001", "themes"] == "机器人概念、算力概念"
    assert themes.catalog(m).iloc[0]["board"] == "算力概念"
    today = themes.updated()
    assert not themes.stale(today) and themes.stale(date.fromordinal(today.toordinal() + 7))

    monkeypatch.setattr(themes, "MIN_ROWS", 10**6)                      # an incomplete download keeps the old map
    with pytest.raises(http.SourceUnavailable):
        themes.refresh()
    assert len(themes.load_map()) == len(m)


def test_measures_from_history_and_todays_close(monkeypatch):
    from stockrt import panel

    closes = list(np.linspace(10, 12, 30))
    h = pd.DataFrame({"date": pd.bdate_range("2026-10-01", periods=30), "close": closes,
                      "volume": [100.0] * 27 + [100.0, 100.0, 100.0], "amount": [1e6] * 30})
    monkeypatch.setattr(panel, "history", lambda *a, **k: h)
    m = flowpicks.measures("sh600000", date(2026, 11, 12), 13.0, closes[-1], 300.0, 2e6)
    c = closes + [13.0]
    assert m["ma20"] == pytest.approx(np.mean(c[-20:]))
    assert m["chg20"] == pytest.approx(100 * (13.0 / c[-21] - 1))
    assert m["chg3"] == pytest.approx(100 * (13.0 / c[-4] - 1))
    assert m["vol3_change"] == pytest.approx(100 * (500 / 300 - 1))     # 100+100+300 against 100+100+100
    assert m["amount3"] == pytest.approx(4e6)
    short = h.tail(10)
    monkeypatch.setattr(panel, "history", lambda *a, **k: short)
    assert flowpicks.measures("sh600000", date(2026, 11, 12), 13.0, None, 1.0, 1.0) == {"code": "sh600000"}


def frame():
    return pd.DataFrame({
        "code": ["a", "b", "c", "d", "e"], "name": list("ABCDE"), "price": 10.0, "pct_change": 1.0,
        "main_net5": [50e6, 50e6, -5e6, 50e6, 50e6], "main_net3": [30e6, 30e6, 30e6, -1e6, 30e6],
        "main_ratio3": [8.0, 3.0, 8.0, 8.0, 8.0], "above_ma20_pct": [2.0, 2.0, 2.0, 2.0, -1.0],
        "float_mcap": [10e9, 10e9, 10e9, 10e9, 10e9], "chg20": [5.0, 5.0, 5.0, 5.0, 5.0],
        "chg3": 1.0, "vol3_change": [80.0, 80.0, 80.0, 80.0, 80.0]})


def test_basic_and_advanced_conditions():
    d = flowpicks.apply_basic(frame()).set_index("code")
    assert d.loc["a", "passes"] and not d.loc["b", "passes"]
    assert d.loc["b", "missed"] == "大单净占比" and d.loc["c", "missed"] == "近5日主力净流入"
    assert d.loc["e", "missed"] == "20日均线"
    basic = flowpicks.apply_basic(frame(), flowpicks.BasicParams(advanced=False)).set_index("code")
    assert basic.loc[["a", "b", "d"], "passes"].all() and not basic.loc["c", "passes"]
    big = flowpicks.apply_basic(frame().assign(float_mcap=40e9)).set_index("code")
    assert big.loc["a", "missed"] == "流通市值"
    hot = flowpicks.apply_basic(frame().assign(chg20=25.0), flowpicks.BasicParams(use_chg20=False))
    assert hot.set_index("code").loc["a", "passes"]
    unknown = flowpicks.apply_basic(frame().assign(main_net5=np.nan)).set_index("code")
    assert not unknown["passes"].any()                                  # unknown flow never passes


def test_hot_theme_conditions_and_leading_themes():
    f = frame()
    mem = pd.DataFrame({"code": ["a", "b", "d"], "themes": ["算力概念", "算力概念", "机器人概念"]})
    d = flowpicks.apply_hot(f, mem).set_index("code")
    assert d.loc["a", "passes"] and d.loc["b", "passes"]               # the hot version has no large-order rule
    assert d.loc["c", "missed"] == "所属概念" and d.loc["d", "missed"] == "近3日主力净流入"
    quiet = flowpicks.apply_hot(f.assign(vol3_change=20.0), mem).set_index("code")
    assert quiet.loc["a", "missed"] == "近3日放量"

    m = pd.DataFrame({"code": ["a", "b", "c", "d", "e"], "board": ["X", "X", "Y", "Y", "Y"]})
    f2 = f.assign(chg3=[5.0, 3.0, -1.0, 1.0, -3.0])
    h = themes.hot(m, f2, min_members=2)
    assert h["board"].tolist() == ["X", "Y"] and h.iloc[0]["chg3"] == pytest.approx(4.0)
    assert h.iloc[1]["up_share"] == pytest.approx(100 / 3)


def _em_batches(monkeypatch, skip=("1.600009",), refuse_first=True):
    """East Money's batch endpoint: refuses the first request once, then answers (except for `skip`)."""
    calls = {"n": 0}

    class R:
        def __init__(self, data):
            self.data = data

        def json(self):
            return {"data": self.data}

    def get(url, params=None, **k):
        calls["n"] += 1
        if refuse_first and calls["n"] == 1:
            raise http.SourceUnavailable("throttled")
        return R({"diff": [{"f12": s.split(".")[1], "f13": int(s.split(".")[0]), "f267": 1e6, "f268": 6.0,
                            "f164": 2e6, "f165": 4.0} for s in params["secids"].split(",") if s not in skip]})

    monkeypatch.setattr(http, "get", get)
    monkeypatch.setattr(flowpicks.time_mod, "sleep", lambda s: None)
    return calls


def test_flows_after_the_close_fill_gaps_from_east_moneys_own_history(monkeypatch):
    from stockrt.sources import eastmoney, sina

    _em_batches(monkeypatch)
    hist = pd.DataFrame({"date": [date(2026, 11, d) for d in (16, 17, 18, 19, 20)],
                         "main_net": [5e6, 4e6, 3e6, 2e6, 1e6]})
    monkeypatch.setattr(eastmoney, "daily_flow", lambda c, n: hist)
    monkeypatch.setattr(sina, "daily_flow", lambda *a: pytest.fail("Sina's buckets differ: never mixed in"))
    f = flowpicks.flows(["sh600000", "sh600009"], date(2026, 11, 20), {"sh600009": 1e8}).set_index("code")
    assert f.loc["sh600000", "flow_source"] == "eastmoney" and f.loc["sh600000", "main_net3"] == 1e6
    assert f.loc["sh600009", "flow_source"] == "eastmoney history"
    assert f.loc["sh600009", "main_net3"] == 6e6 and f.loc["sh600009", "main_net5"] == 15e6   # last 3 / last 5
    assert f.loc["sh600009", "main_ratio3"] == pytest.approx(6.0)
    stale_hist = hist.iloc[:-1]                                     # history not yet up to the session: unknown
    monkeypatch.setattr(eastmoney, "daily_flow", lambda c, n: stale_hist)
    f = flowpicks.flows(["sh600000", "sh600009"], date(2026, 11, 20), {}).set_index("code")
    assert "sh600009" not in f.index


def test_flows_during_the_session_leave_gaps_unknown(monkeypatch):
    from stockrt.sources import eastmoney

    _em_batches(monkeypatch, refuse_first=False)
    monkeypatch.setattr(eastmoney, "daily_flow", lambda *a: pytest.fail("history stops at yesterday mid-session"))
    f = flowpicks.flows(["sh600000", "sh600009"], date(2026, 11, 20), {}, live=True).set_index("code")
    assert list(f.index) == ["sh600000"]


def test_intraday_volume_curve_and_live_refresh():
    assert flowpicks.day_share(0) == pytest.approx(0.05)            # floor: never divide by ~0
    assert flowpicks.day_share(30) == pytest.approx(0.289)          # 10:00 - not 30/240 = 12.5%
    assert flowpicks.day_share(45) == pytest.approx((0.289 + 0.414) / 2)
    assert flowpicks.day_share(240) == 1.0 and flowpicks.day_share(300) == 1.0
    from datetime import datetime

    live = {"day": date(2026, 11, 24), "final": False, "computed": "2026-11-24 10:03"}
    assert not flowpicks.stale(live, datetime(2026, 11, 24, 10, 20))
    assert flowpicks.stale(live, datetime(2026, 11, 24, 10, 40))     # older than 30 minutes
    assert flowpicks.stale(live, datetime(2026, 11, 24, 15, 9))      # the session is over: the fixed run
    assert flowpicks.stale(live, datetime(2026, 11, 25, 9, 0))       # yesterday's never got its fixed run
    assert not flowpicks.stale({**live, "final": True}, datetime(2026, 11, 24, 15, 9))
    assert flowpicks.stale(None, datetime(2026, 11, 24, 10, 0))
    assert flowpicks.bucket(datetime(2026, 11, 24, 10, 47)) == "1030"
    assert flowpicks.bucket(datetime(2026, 11, 24, 15, 9)) == "close"


def test_a_live_run_projects_todays_volume_and_is_not_final(results, monkeypatch):
    from datetime import datetime

    from stockrt.calendar import BJ

    seen = {}

    def fake_measures(code, day, price, prev_close, volume, amount):
        seen[code] = volume
        return {"code": code, "above_ma20_pct": 1.0, "chg3": 1.0, "vol3_change": 0.0}

    monkeypatch.setattr(flowpicks, "measures", fake_measures)
    monkeypatch.setattr(flowpicks, "flows", lambda codes, day, a3, workers=8, live=False: pd.DataFrame(
        {"code": codes, "main_net3": 1.0, "main_ratio3": 1.0, "main_net5": 1.0, "main_ratio5": 1.0,
         "flow_source": "eastmoney"}))
    day = date(2026, 11, 24)
    snap = pd.DataFrame({"code": ["sh600000"], "name": ["X"], "price": [10.0], "prev_close": [9.9],
                         "volume": [2890.0], "amount": [1e6], "time": [pd.Timestamp("2026-11-24 10:00", tz=BJ)],
                         "pct_change": 1.0, "float_mcap": 1e10, "turnover_rate": 1.0})
    res = flowpicks.run(day, snap, now=datetime(2026, 11, 24, 10, 0, tzinfo=BJ))
    assert seen["sh600000"] == pytest.approx(10000.0)               # 2,890 by 10:00 = 28.9% of a day
    assert not res["final"] and not flowpicks.load(day)["final"]
    assert any(n.startswith("Live as of 10:00") for n in res["notes"])
    res = flowpicks.run(day, snap, now=datetime(2026, 11, 24, 15, 9, tzinfo=BJ))
    assert seen["sh600000"] == 2890.0 and flowpicks.load(day)["final"]


def test_twenty_sessions_are_kept_older_ones_shrunk(results):
    days = list(pd.bdate_range("2026-10-01", periods=25).date)
    f = frame().assign(main_net3=[30e6, -1e6, 30e6, -1e6, 30e6], main_net5=[50e6, -5e6, -5e6, -5e6, 50e6])
    f = pd.concat([f, f.assign(code=f["code"] + "2")], ignore_index=True)          # 10 stocks
    themes.save(pd.DataFrame({"code": f["code"], "board": "X", "board_code": "BK1"}), date(2026, 11, 1))
    for d in days:
        flowpicks.save({"day": d, "frame": f, "notes": ["n"], "checked": 5, "flow_checked": 4, "computed": "x"})
    flowpicks.prune()
    kept = flowpicks.days()
    assert len(kept) == 20 and kept[0] == days[-1] and kept[-1] == days[5]          # the oldest 5 removed
    recent, older = flowpicks.load(kept[4]), flowpicks.load(kept[5])
    assert not recent["compact"] and len(recent["frame"]) == 10                     # the last week in full
    assert older["compact"] and sorted(older["frame"]["code"]) == ["a", "a2", "c", "c2", "e", "e2"]  # with inflow
    assert older["frame"]["price"].dtype == "float32"
    assert older["hot"][0]["board"] == "X" and older["hot"][0]["members"] == 10    # leading themes kept first
    flowpicks.prune()                                                               # shrunk only once
    assert len(flowpicks.load(kept[5])["frame"]) == 6


def test_older_buy_the_dip_sessions_keep_the_hits_and_near_misses(results, monkeypatch):
    from stockrt import dipbuy

    monkeypatch.setattr(archive, "SCREEN_KEEP_ROWS", 1)
    n = 80
    f = pd.DataFrame({"code": [f"s{i}" for i in range(n)], "drop5": -12.0, "drop5_rough": np.nan,
                      "main_net": [40e6] * 2 + [10e6] * (n - 2), "above_ma20_pct": 1.0, "above_ma60_pct": 1.0,
                      "above_ma120_pct": 1.0, "profit_ttm": [1e8] * 3 + [-1e8] * (n - 3), "pct_change": 1.0})
    for d in pd.bdate_range("2026-10-01", periods=7).date:
        dipbuy.save({"day": d, "frame": f, "notes": [], "shortlisted": n, "computed": "x", "final": True})
    dipbuy.prune()
    old = dipbuy.load(dipbuy.days()[-1])
    assert old["compact"]
    # s0, s1 pass; s2 misses only the inflow; the rest miss two rules and are dropped (bar the closest one)
    assert set(old["frame"]["code"]) == {"s0", "s1", "s2"}


def test_scheduler_runs_the_screen_daily_and_the_concept_map_weekly(results, monkeypatch):
    from ui import scheduler

    tasks = {t.name: t for t in scheduler.TASKS}
    day = date(2026, 11, 20)
    assert not scheduler._done(tasks["flowpicks"], day) and not scheduler._done(tasks["themes"], day)
    flowpicks.save({"day": day, "frame": frame(), "notes": [], "checked": 5, "flow_checked": 4, "computed": "x"})
    themes.save(pd.DataFrame({"code": ["a"], "board": ["X"], "board_code": ["BK1"]}), date(2026, 11, 16))
    assert scheduler._done(tasks["flowpicks"], day) and scheduler._done(tasks["themes"], day)
    flowpicks.save({"day": day, "frame": frame(), "notes": [], "checked": 5, "flow_checked": 4,
                    "computed": f"{day} 10:33", "final": False})
    assert not scheduler._done(tasks["flowpicks"], day)              # a live result: the fixed run is still due
    assert scheduler._done(tasks["fp_1030"], day) and not scheduler._done(tasks["fp_1100"], day)
    assert not scheduler._done(tasks["themes"], date(2026, 11, 23))   # a week later: download again
    assert tasks["flowpicks"].start == flowpicks.AT and not tasks["flowpicks"].intraday


def test_a_live_run_without_money_flow_keeps_the_last_list(results, monkeypatch):
    from datetime import datetime

    from stockrt.calendar import BJ

    monkeypatch.setattr(flowpicks, "measures", lambda code, *a: {"code": code, "above_ma20_pct": 1.0})
    monkeypatch.setattr(flowpicks, "flows", lambda codes, day, a3, workers=8, live=False: pd.DataFrame(
        columns=["code", "main_net3", "main_ratio3", "main_net5", "main_ratio5", "flow_source"]))
    day = date(2026, 11, 24)
    snap = pd.DataFrame({"code": ["sh600000"], "name": ["X"], "price": [10.0], "prev_close": [9.9],
                         "volume": [100.0], "amount": [1e6], "time": [pd.Timestamp("2026-11-24 10:00", tz=BJ)],
                         "pct_change": 1.0, "float_mcap": 1e10, "turnover_rate": 1.0})
    with pytest.raises(http.SourceUnavailable):
        flowpicks.run(day, snap, now=datetime(2026, 11, 24, 10, 0, tzinfo=BJ))
    assert flowpicks.load(day) is None                               # nothing saved over the last good list


def _dip_frame():
    # a passes everything else; b misses one rule (profit); c misses two (drop and MA60)
    return pd.DataFrame({"code": ["a", "b", "c"], "drop5": [-12.0, -12.0, -6.0], "drop5_rough": np.nan,
                         "above_ma60_pct": [1.0, 1.0, -2.0], "profit_ttm": [1e8, -1e8, 1e8],
                         "pct_change": [1.0, 1.0, 1.0]})


def test_buy_the_dip_asks_money_flow_only_where_it_can_decide():
    from stockrt import dipbuy

    assert dipbuy.flow_candidates(_dip_frame()) == ["a", "b"]
    assert dipbuy.flow_candidates(_dip_frame().assign(pct_change=[7.0, 1.0, 1.0])) == ["a", "b"]  # a one short
    assert dipbuy.flow_candidates(_dip_frame().assign(pct_change=[1.0, 7.0, 1.0])) == ["a"]   # b now two short
    assert dipbuy.flow_candidates(_dip_frame().iloc[:0]) == []


def test_buy_the_dip_keeps_the_last_list_when_east_money_refuses_mid_session(results, monkeypatch):
    from datetime import datetime

    from stockrt import dipbuy, realtime
    from stockrt.calendar import BJ

    shortlist = _dip_frame().assign(name="X", price=10.0, prev_close=10.1, turnover_rate=1.0, float_mcap=1e10,
                                    amount=1e7).drop(columns=["above_ma60_pct", "profit_ttm"])
    monkeypatch.setattr(dipbuy, "shortlist", lambda snap, day: shortlist)
    monkeypatch.setattr(dipbuy, "trend", lambda code, day, price, pc: {
        "code": code, "above_ma60_pct": {"a": 1.0, "b": 1.0, "c": -2.0}[code]})
    monkeypatch.setattr(dipbuy, "profit_ttm", lambda codes, day: pd.DataFrame(
        {"code": ["a", "b", "c"], "profit_ttm": [1e8, -1e8, 1e8], "profit_basis": "x"}))
    asked = []
    monkeypatch.setattr(realtime, "main_flow", lambda codes, workers=8: asked.append(list(codes)) or pd.DataFrame(
        columns=["code", "main_net", "source"]))
    snap = pd.DataFrame({"time": [pd.Timestamp("2026-11-26 10:00", tz=BJ)]})
    day = date(2026, 11, 26)
    with pytest.raises(http.SourceUnavailable):
        dipbuy.run(day, snap, now=datetime(2026, 11, 26, 10, 0, tzinfo=BJ))
    assert asked == [["a", "b"]] and dipbuy.load(day) is None        # c can't pass: never asked; nothing saved
    res = dipbuy.run(day, snap, now=datetime(2026, 11, 26, 15, 5, tzinfo=BJ))   # after the close: saved, flagged
    assert res["final"] and any(n.startswith("Money flow unknown for 2") for n in res["notes"])
