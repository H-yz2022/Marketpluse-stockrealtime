"""The worker process, the job runner that drives it, the site's clock and the history pre-check (offline)."""

import json
import time
from datetime import date, datetime
from unittest.mock import patch

import numpy as np
import pandas as pd

from stockrt import archive, checkpoint, jobs, panel
from stockrt.calendar import BJ
from stockrt.screener import ScreenParams


def _wait(job, limit=60):
    t0 = time.time()
    while job.active and time.time() - t0 < limit:
        time.sleep(0.2)


def test_process_runner_runs_dedupes_and_reports_failure(tmp_path, monkeypatch):
    monkeypatch.setattr("stockrt.config.DATA_DIR", tmp_path)
    r = jobs.ProcessRunner()
    a = r.submit(("t", 1), "selftest", {"pause": 0.3})
    assert r.submit(("t", 1), "selftest", {}) is a              # same key while queued/running: one run
    b = r.submit(("t", 2), "selftest", {"fail": True, "pause": 0.05})
    assert b.message.startswith("排队中")                         # one worker at a time: the second waits
    _wait(a)
    _wait(b)
    assert a.state == "done" and a.progress == 1.0
    assert b.state == "failed" and "asked to fail" in b.error
    assert r.submit(("t", 1), "selftest", {}) is a              # done: not re-run unless forced
    assert r.submit(("t", 1), "selftest", {}, force=True) is not a


def test_a_failed_job_keeps_its_reason_and_waits_for_its_retry(tmp_path, monkeypatch):
    # Resubmitting at once made an open page restart the job from zero every few seconds, hiding the error.
    monkeypatch.setattr("stockrt.config.DATA_DIR", tmp_path)
    r = jobs.ProcessRunner()
    a = r.submit(("f", 1), "selftest", {"fail": True, "pause": 0.01})
    _wait(a)
    assert a.state == "failed" and "asked to fail" in a.error and a.retry_at > time.time() + 30
    assert r.submit(("f", 1), "selftest", {"fail": True}) is a    # a page rerun: the same failure, not a restart
    a.retry_at = time.time() - 1                                  # the wait is over
    b = r.submit(("f", 1), "selftest", {"fail": True, "pause": 0.01})
    assert b is not a and b.attempt == 2 and "asked to fail" in b.last_error
    _wait(b)
    log = r.history()
    assert [e["attempt"] for e in log if e.get("task") == "selftest"] == [2, 1]   # every run is on the record
    assert log[0]["state"] == "failed" and "asked to fail" in log[0]["error"] and log[0]["where"]
    assert log[-1]["event"] == "server_start"
    b.attempt = len(r.RETRY_WAIT)
    b.state = "failed"
    with r._lock:
        r._finish(b, 1)
    b.attempt += 1
    with r._lock:
        r._finish(b, 1)
    assert b.retry_at is None                                     # enough: the page stops, the clock still retries
    assert r.submit(("f", 1), "selftest", {}) is b


def test_time_left_uses_real_speed_and_admits_when_it_cannot_tell():
    from ui.progress import _about, eta

    job = jobs.Job(("x",), state="running", started=time.time() - 30, typical=100)
    assert 65 <= eta(job) <= 75                                   # no speed yet: this task's usual time
    job.mark, job.progress = (time.time() - 20, 0.10), 0.30       # 20% in 20 s, then 70% to go
    assert 65 <= eta(job) <= 75
    slow = jobs.Job(("y",), state="running", started=time.time() - 300, typical=100, progress=0.05)
    assert eta(slow) is None                                      # past its usual time: no made-up countdown
    assert _about(5) == "即将完成 · almost done" and _about(83).startswith("约 1 分 20 秒")


def _snapshot(n=250):
    return pd.DataFrame({"code": [f"hk{i:05d}" for i in range(n)], "name": "x", "price": 10.0, "prev_close": 10.0,
                         "open": 10.0, "high": 10.0, "low": 10.0, "volume": 1e6, "amount": 1e7, "pct_change": 0.0,
                         "turnover_rate": 0.1, "float_mcap": 1e9, "is_st": False, "board": "Main", "suspended": False,
                         "time": pd.Timestamp("2026-10-30 16:10", tz="Asia/Shanghai")})


def test_no_stock_qualifying_is_a_saved_result_not_a_failure(tmp_path, monkeypatch):
    # 30 Oct 2026: no Hong Kong stock passed the first screen. It failed the GitHub job and looped on the page.
    spec, day = checkpoint.SPECS["HK"], date(2026, 10, 30)
    monkeypatch.setattr(archive, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr("stockrt.storage.CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(checkpoint, "checkpoint_reached", lambda d, c: True)
    monkeypatch.setattr(checkpoint, "_index_pct", lambda *a, **k: {})
    res = checkpoint.evaluate(_snapshot(), spec=spec, day=day)
    assert list(res["frames"]) == list(spec.checkpoints) and res["candidates"] == 0
    assert day in archive.sessions("HK")
    back = archive.load("HK", day)
    assert back["frames"]["16:00"].empty and "No stock passed" in " ".join(back["notes"])
    assert checkpoint.ranked(back, spec=spec).empty
    assert checkpoint.latest_saved(spec)["day"] == day
    with patch.object(checkpoint.realtime, "market_snapshot", return_value=_snapshot()):
        from stockrt import worker

        assert "2026-10-30" in worker.frontpage({"mkt": "HK", "day": "2026-10-30"}, lambda f, m: None)
    # A feed that fails is different: too few quotes must not be saved as "nothing qualified".
    assert not checkpoint.evaluate(_snapshot(5), spec=spec, day=date(2026, 10, 29))["frames"]


def test_a_failed_rebuild_resumes_instead_of_starting_over(tmp_path, monkeypatch):
    spec, day = checkpoint.SPECS["CN"], date(2026, 10, 30)
    monkeypatch.setattr("stockrt.storage.CACHE_DIR", tmp_path)
    monkeypatch.setattr(checkpoint, "RETRY_PAUSE", 0)
    # The test setup stubs load_json (no calendar downloads); this test needs it to read the saved progress.
    saved = lambda kind, key: checkpoint.storage._path(kind, key, "json")  # noqa: E731
    monkeypatch.setattr(checkpoint.storage, "load_json", lambda kind, key, **k: json.loads(
        saved(kind, key).read_text(encoding="utf-8")) if saved(kind, key).exists() else None)
    codes = [f"sh{i:06d}" for i in range(100)]
    quotes = {c: {} for c in codes}
    asked, down = [], True

    def rows(code, q, d, cps, sp, minutes=None):
        asked.append(code)
        if down and code >= "sh000060":                           # the source stops answering part-way
            raise checkpoint.http.SourceUnavailable("paused")
        return [{"code": code, "checkpoint": "14:00", "price": 1.0}]

    monkeypatch.setattr(checkpoint, "_stock_rows", rows)
    out = {"notes": []}
    args = (codes, quotes, day, ("14:00",), spec, "k_parts", True, None, out)
    try:
        checkpoint._rebuild_stocks(*args)
        raise AssertionError("should have failed: 40 of 100 stocks missing")
    except checkpoint.IncompleteRebuild as e:
        assert "40 of 100" in str(e) and "60 are saved" in str(e)
    asked.clear()
    down = False
    recs = checkpoint._rebuild_stocks(*args)
    assert sorted(asked) == codes[60:]                            # only the missing stocks are asked for again
    assert sorted(r["code"] for r in recs) == codes


def test_a_few_stocks_missing_are_noted_and_the_run_completes(tmp_path, monkeypatch):
    spec, day = checkpoint.SPECS["CN"], date(2026, 10, 30)
    monkeypatch.setattr("stockrt.storage.CACHE_DIR", tmp_path)
    monkeypatch.setattr(checkpoint, "RETRY_PAUSE", 0)
    codes = [f"sh{i:06d}" for i in range(100)]

    def rows(code, q, d, cps, sp, minutes=None):
        if code == "sh000007":
            raise checkpoint.http.SourceUnavailable("no")
        return [{"code": code, "checkpoint": "14:00"}]

    monkeypatch.setattr(checkpoint, "_stock_rows", rows)
    out = {"notes": []}
    recs = checkpoint._rebuild_stocks(codes, {c: {} for c in codes}, day, ("14:00",), spec, "k2", True, None, out)
    assert len(recs) == 99 and "1 of 100" in out["notes"][0]


def test_compacting_a_session_keeps_its_ranked_list(tmp_path, monkeypatch):
    monkeypatch.setattr(archive, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(checkpoint, "close_miss", lambda r, p, spec: False)
    spec, day = checkpoint.SPECS["CN"], date(2026, 10, 12)
    n = 200
    frame = pd.DataFrame({"code": [f"sh{i:06d}" for i in range(n)], "name": "x", "score": np.arange(n, 0, -1.0),
                          "rules_met": [10] * 3 + [7] * (n - 3), "passes": [True] * 3 + [False] * (n - 3),
                          "flow_at": "checkpoint", "missed": "", "pct_change": 4.0, "volume_ratio": 2.0,
                          "turnover_rate": 5.0, "float_mcap": 1e10, "main_net": 1e7,
                          **{f"rule_{i}": True for i in range(10)}})
    res = {"day": day, "frames": {c: frame for c in spec.checkpoints}, "index_pct": {}, "notes": [], "candidates": n}
    archive.save(res, "CN", ScreenParams().to_dict())
    before = checkpoint.ranked(archive.load("CN", day), spec=spec)["code"].tolist()
    assert archive.compact_session("CN", day) is True
    back = archive.load("CN", day)
    assert back["compact"] and len(back["frames"]["15:00"]) == archive.COMPACT_TOP
    assert len(pd.read_parquet(tmp_path / "cn" / "2026-10-12.parquet")) == archive.COMPACT_TOP * 3
    assert checkpoint.ranked(back, spec=spec)["code"].tolist() == before and len(before) == 3
    assert archive.compact_session("CN", day) is False            # once is enough


def test_clock_deadlines_and_retries(monkeypatch):
    from ui import scheduler

    done, submitted = set(), []

    class FakeJob:
        def __init__(self):
            self.active, self.state, self.error = True, "running", None

    monkeypatch.setattr(scheduler, "_done", lambda t, d: t.name in done)
    monkeypatch.setattr(scheduler, "_submit", lambda t, d, force: submitted.append((t.name, force)) or FakeJob())
    monkeypatch.setattr(scheduler.tradingdays, "is_trading_day", lambda m, d: True)
    monkeypatch.setattr(scheduler.Scheduler, "_sync", lambda self, now: None)
    monkeypatch.setattr(scheduler.Scheduler, "_loop", lambda self: None)
    s = scheduler.Scheduler()
    t = datetime(2026, 10, 30, 15, 10, tzinfo=BJ)                # Friday
    s.tick(t)
    assert [n for n, _ in submitted] == ["accumulation", "cn_close", "longterm"]   # due ones only
    s.tick(t)
    assert len(submitted) == 3                                    # running: not queued twice
    for j in s.jobs.values():
        j.active, j.state = False, "failed"
    s.attempts = {k: v - 11 * 60 for k, v in s.attempts.items()}  # 11 minutes later: retried, forced
    s.tick(t)
    assert len(submitted) == 6 and all(f for _, f in submitted[3:])
    done.update({"accumulation", "cn_close"})
    for j in s.jobs.values():                                     # finished, but 长线 still has no result
        j.active, j.state = False, "done"
    states = {x["label"]: x["state"] for x in s.status(datetime(2026, 10, 30, 15, 50, tzinfo=BJ))}
    assert states["主力暗中吸筹"] == "done" and states["收盘选股"] == "waiting" and states["长线"] == "late"


def test_history_precheck_skips_only_hopeless_stocks(tmp_path, monkeypatch):
    monkeypatch.setattr(archive, "RESULTS_DIR", tmp_path)
    d = pd.bdate_range(end="2026-10-29", periods=20).date
    rising = np.r_[np.full(15, 10.0), [9.8, 9.7, 9.6, 9.7, 9.8]]      # MA5 below MA10: a cross is near
    falling = np.linspace(12, 10, 20)                                   # long decline: no cross today
    series = (("sh600001", rising), ("sh600002", falling))
    rows = [pd.DataFrame({"code": c, "date": d, "open": x, "close": x, "high": x, "low": x, "volume": 1e6,
                          "amount": 1e7, "turnover_rate": 2.0}) for c, x in series]
    panel.save(pd.concat(rows, ignore_index=True))
    cands = pd.DataFrame({"code": ["sh600001", "sh600002", "sh600003"], "prev_close": [9.8, 10.0, 10.0],
                          "high": [10.3, 10.4, 10.4], "volume": [2e6, 3e5, 2e6]})
    with patch("stockrt.tradingdays.previous_trading_day", return_value=date(2026, 10, 29)):
        kept, skipped = checkpoint.history_prefilter(cands, ScreenParams(), date(2026, 10, 30), ("14:00",))
    # sh600002: no fresh cross possible and too little volume for the ratio and volume rules -> skipped;
    # sh600003 isn't in the panel, so it is kept for the full check
    assert kept["code"].tolist() == ["sh600001", "sh600003"] and skipped == 1
