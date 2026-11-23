"""The site's clock: each trading day's fixed results, queued in the worker at their times, with deadlines.

Every 30 seconds it looks at what is already saved on disk (so a restart loses nothing) and queues whatever
is due and missing into the worker process - never running anything heavy itself, so the web server keeps
answering. A task still missing 10 minutes after an attempt is retried; one missing past its deadline is shown
as late. The GitHub job publishes durable copies later (it starts hours late). A free host sleeps when nobody
visits (Streamlit Community Cloud after 12 hours, Render after 15 minutes), and while asleep nothing runs.
"""

from __future__ import annotations

import logging
import os
import threading
import time as _time
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

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
    intraday: bool = False      # a live result computed ahead of visitors: only on time (never caught up later -
    #                             the after-close tasks cover the day), and not listed in the sidebar


def _at(hhmm: str, plus: int = 0) -> time:
    return (datetime.strptime(hhmm, "%H:%M") + timedelta(minutes=plus)).time()


def _intraday() -> tuple[Task, ...]:
    """While the server runs during the session, each live screen is computed at its checkpoint, so it is ready
    before anyone opens it: 主力暗中吸筹 after each morning checkpoint, 尾盘选股 at 14:00 and 14:30, and 低位吸筹
    every half hour. Each has 20-25 minutes to finish before it counts as missed."""
    tasks = [Task(f"acc_{c.replace(':', '')}", f"主力暗中吸筹 {c}", _at(c, 2), _at(c, 22), intraday=True)
             for c in ("09:45", "10:15", "10:45")]
    tasks += [Task("tail_1400", "尾盘选股 14:00", _at("14:00", 1), _at("14:00", 25), intraday=True),
              Task("tail_1430", "尾盘选股 14:30", _at("14:30", 1), _at("14:30", 25), intraday=True)]
    tasks += [Task(f"dip_{c.replace(':', '')}", f"低位吸筹 {c}", _at(c), _at(c, 20), intraday=True)
              for c in ("10:00", "10:30", "11:00", "13:30", "14:00", "14:30")]
    return tuple(tasks)


TASKS = _intraday() + (
    Task("accumulation", "主力暗中吸筹", time(11, 16), time(11, 40)),
    Task("cn_close", "尾盘选股 15:00", time(15, 2), time(15, 20)),
    Task("dipbuy", "低位吸筹", time(15, 3), time(15, 30)),
    Task("longterm", "长线", time(15, 6), time(15, 40)),
    Task("themes", "题材成分（每周）", time(15, 4), time(16, 30)),
    Task("flowpicks", "资金选股", time(15, 8), time(15, 45)),
    Task("close_screen", "收盘选股", time(16, 1), time(16, 20)),
    Task("hk_close", "港股 16:00", time(16, 12), time(16, 40), mkt="HK"),
)


def _done(t: Task, day: date) -> bool:
    from stockrt import accumulation, archive, dipbuy, flowpicks, longterm, themes

    if t.name == "themes":
        return not themes.stale(day)             # weekly: done while the saved map is under 7 days old
    if t.name == "flowpicks":
        return flowpicks.load(day) is not None
    if t.name.startswith("acc_"):
        return accumulation.load_partial(day, _acc_reached(t)) is not None
    if t.name.startswith("tail_"):
        from stockrt import checkpoint, storage
        from stockrt.screener import ScreenParams

        key = checkpoint._result_key(day, ScreenParams(), _tail_reached(t), checkpoint.SPECS["CN"])
        return storage.load_json("checkpoints", key) is not None
    if t.name.startswith("dip_"):
        res = dipbuy.load(day)
        return res is not None and (bool(res["final"]) or res["computed"] >= f"{day} {t.start:%H:%M}")
    if t.name == "accumulation":
        return accumulation.load(day) is not None
    if t.name == "dipbuy":
        res = dipbuy.load(day)
        return res is not None and bool(res["final"])
    if t.name == "cn_close":
        return day in archive.sessions("CN")
    if t.name == "longterm":
        return longterm.load(day) is not None
    if t.name == "close_screen":
        return day in archive.screen_days()
    return day in archive.sessions("HK")


def _acc_reached(t: Task) -> tuple[str, ...]:
    from stockrt import accumulation

    last = f"{t.name[4:6]}:{t.name[6:]}"
    return tuple(c for c in accumulation.CHECKPOINTS if c <= last)


def _tail_reached(t: Task) -> tuple[str, ...]:
    return ("14:00",) if t.name == "tail_1400" else ("14:00", "14:30")


def _submit(t: Task, day: date, force: bool):
    from stockrt import accumulation, checkpoint
    from stockrt.screener import ScreenParams
    from ui.picks_view import frontpage_key, job_runner
    from ui.screens_view import acc_key, dipbuy_key, flowpicks_key, longterm_key, themes_key

    r, w = job_runner(), checkpoint.WORKERS
    if t.name == "themes":
        return r.submit(themes_key(day), "themes", {}, force=force)
    if t.name == "flowpicks":
        return r.submit(flowpicks_key(day), "flowpicks", {"day": str(day), "workers": w}, force=force)
    # The same job keys as the pages, so a visitor arriving meanwhile shares the run instead of starting another.
    if t.name.startswith("acc_"):
        reached = _acc_reached(t)
        return r.submit(acc_key(day, reached), "accumulation",
                        {"day": str(day), "reached": list(reached), "workers": w}, force=force)
    if t.name.startswith("tail_"):
        reached = _tail_reached(t)
        return r.submit(frontpage_key("CN", day, reached, ScreenParams()), "frontpage",
                        {"mkt": "CN", "day": str(day), "params": ScreenParams().to_dict(), "use_cache": True},
                        force=force)
    if t.name.startswith("dip_"):
        from stockrt import dipbuy
        from stockrt.calendar import now_bj

        return r.submit(dipbuy_key(day, dipbuy.bucket(now_bj())), "dipbuy", {"day": str(day), "workers": w},
                        force=force)
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
    if t.name == "dipbuy":
        return r.submit(dipbuy_key(day, "close"), "dipbuy", {"day": str(day), "workers": w}, force=force)
    return r.submit(("close_screen", str(day)), "close_screen", {"day": str(day), "workers": w}, force=force)


class Scheduler:
    def __init__(self) -> None:
        self.attempts: dict[tuple[str, str], float] = {}
        self.jobs: dict[tuple[str, str], object] = {}
        self.last_error: str | None = None
        self.last_sync = 0.0
        self.last_prune: date | None = None
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
        self._prune(now)
        if self.first_sync is not None and self.first_sync.active:
            return                      # what's already published must not be recomputed
        for t in TASKS:
            day = now.date()
            if now.time() < t.start or tradingdays.is_trading_day(t.mkt, day) is False or now.weekday() >= 5:
                continue
            if t.intraday and now.time() > t.deadline:
                continue                # its moment has passed; the after-close result covers it
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

    def _prune(self, now: datetime) -> None:
        """Once a day: drop old cache files and finished jobs' progress files, so a server that stays up for weeks
        doesn't keep every day's data."""
        if self.last_prune == now.date():
            return
        self.last_prune = now.date()
        from stockrt import storage
        from ui.picks_view import job_runner

        n, freed = storage.prune_stale()
        job_runner()._trim_logs()
        if n:
            log.info("pruned %d cache files (%.1f MB)", n, freed / 1e6)
        if os.environ.get("STOCKRT_DESKTOP") == "1":
            # On the website the daily GitHub job keeps the results folder to its rolling window; a desktop copy
            # that can't reach GitHub has to do it for the results it computes itself.
            from stockrt import accumulation, archive, dipbuy, flowpicks, longterm

            for mkt in ("CN", "HK"):
                archive.prune_sessions(mkt)
            archive.prune_volumes("CN")
            archive.prune_screens()
            for m in (longterm, accumulation, dipbuy, flowpicks):
                m.prune()

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
            if t.intraday or now.weekday() >= 5 or tradingdays.is_trading_day(t.mkt, day) is False:
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
