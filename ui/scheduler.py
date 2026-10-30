"""The site's clock: each trading day's fixed results, queued in the worker at their times, with deadlines.

Every 30 seconds it looks at what is already saved on disk (so a restart loses nothing) and queues whatever
is due and missing into the worker process - never running anything heavy itself, so the web server keeps
answering Render's health check. A task still missing 10 minutes after an attempt is retried; one missing
past its deadline is shown as late. The GitHub job publishes durable copies later (it starts hours late).
A free Render server sleeps after 15 idle minutes, and while asleep nothing runs: an uptime monitor pinging
the site keeps it awake (see README).
"""

from __future__ import annotations

import logging
import threading
import time as _time
from dataclasses import dataclass
from datetime import date, datetime, time

import streamlit as st

from stockrt import sample, tradingdays
from stockrt.calendar import now_bj

log = logging.getLogger(__name__)
RETRY = 10 * 60


@dataclass(frozen=True)
class Task:
    name: str
    label: str
    start: time
    deadline: time
    mkt: str = "CN"


TASKS = (
    Task("accumulation", "主力暗中吸筹", time(11, 16), time(11, 40)),
    Task("cn_close", "尾盘选股 15:00", time(15, 2), time(15, 20)),
    Task("longterm", "长线", time(15, 6), time(15, 40)),
    Task("close_screen", "收盘选股", time(16, 1), time(16, 20)),
    Task("hk_close", "港股 16:00", time(16, 12), time(16, 40), mkt="HK"),
)


def _done(t: Task, day: date) -> bool:
    from stockrt import accumulation, archive, longterm

    if t.name == "accumulation":
        return accumulation.load(day) is not None
    if t.name == "cn_close":
        return day in archive.sessions("CN")
    if t.name == "longterm":
        return longterm.load(day) is not None
    if t.name == "close_screen":
        return day in archive.screen_days()
    return day in archive.sessions("HK")


def _submit(t: Task, day: date, force: bool):
    from stockrt import accumulation, checkpoint
    from stockrt.screener import ScreenParams
    from ui.picks_view import frontpage_key, job_runner
    from ui.screens_view import acc_key, longterm_key

    r, w = job_runner(), checkpoint.WORKERS
    if t.name == "accumulation":
        return r.submit(acc_key(day, accumulation.CHECKPOINTS), "accumulation",
                        {"day": str(day), "reached": list(accumulation.CHECKPOINTS), "workers": w}, force=force)
    if t.name in ("cn_close", "hk_close"):
        spec = checkpoint.SPECS[t.mkt]
        return r.submit(frontpage_key(t.mkt, day, spec.checkpoints, ScreenParams()), "frontpage",
                        {"mkt": t.mkt, "day": str(day), "params": ScreenParams().to_dict(), "use_cache": True},
                        force=force)
    if t.name == "longterm":
        return r.submit(longterm_key(day), "longterm", {"day": str(day), "workers": w}, force=force)
    return r.submit(("close_screen", str(day)), "close_screen", {"day": str(day), "workers": w}, force=force)


class Scheduler:
    def __init__(self) -> None:
        self.attempts: dict[tuple[str, str], float] = {}
        self.jobs: dict[tuple[str, str], object] = {}
        self.last_error: str | None = None
        self.last_sync = 0.0
        self.first_sync = None          # the download a freshly started server does before anything else
        self.sync_checked = False
        threading.Thread(target=self._loop, daemon=True, name="site-scheduler").start()

    def _loop(self) -> None:
        while True:
            try:
                self.tick()
            except Exception as e:  # noqa: BLE001 - keep the clock running
                self.last_error = f"{now_bj():%H:%M} {e}"
                log.warning("scheduler: %s", e)
            _time.sleep(30)

    def tick(self, now: datetime | None = None) -> None:
        if sample.is_sample():
            return
        now = now or now_bj()
        self._sync(now)
        if self.first_sync is not None and self.first_sync.active:
            return                      # what's already published must not be recomputed
        for t in TASKS:
            day = now.date()
            if now.time() < t.start or tradingdays.is_trading_day(t.mkt, day) is False or now.weekday() >= 5:
                continue
            key = (t.name, str(day))
            if _done(t, day):
                continue
            job = self.jobs.get(key)
            if job is not None and job.active:
                continue
            last = self.attempts.get(key)
            if last is not None and _time.time() - last < RETRY:
                continue
            self.attempts[key] = _time.time()
            self.jobs[key] = _submit(t, day, force=last is not None)

    def _sync(self, now: datetime) -> None:
        """Every 5 minutes: queue a download when newer results have been published (next in the queue)."""
        from stockrt import published

        published.use_current()
        if _time.time() - self.last_sync < 300:
            return
        self.last_sync = _time.time()
        try:
            if published.needs_download():
                from ui.picks_view import job_runner

                job = job_runner().submit(("sync", int(_time.time() // 300)), "sync", {}, front=True)
                self.first_sync = self.first_sync or job
        finally:
            self.sync_checked = True

    @property
    def syncing(self) -> bool:
        """A freshly started server is still fetching the published results (its disk starts empty)."""
        return not self.sync_checked or (self.first_sync is not None and self.first_sync.active)

    def status(self, now: datetime | None = None) -> list[dict]:
        """Today's automatic updates: waiting / running / done / late / failed - for the sidebar."""
        now = now or now_bj()
        day = now.date()
        out = []
        for t in TASKS:
            if now.weekday() >= 5 or tradingdays.is_trading_day(t.mkt, day) is False:
                continue
            key = (t.name, str(day))
            job = self.jobs.get(key)
            if _done(t, day):
                state = "done"
            elif job is not None and job.active:
                state = "running"
            elif now.time() < t.start:
                state = "waiting"
            elif job is not None and job.state == "failed":
                state = "failed"
            else:
                state = "late" if now.time() > t.deadline else "due"
            out.append({"label": t.label, "at": t.start.strftime("%H:%M"), "state": state,
                        "error": getattr(job, "error", None) if state == "failed" else None})
        return out


@st.cache_resource
def start() -> Scheduler:
    return Scheduler()
