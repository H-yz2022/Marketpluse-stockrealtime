"""The worker process, the job runner that drives it, the site's clock and the history pre-check (offline)."""

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
