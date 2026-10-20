"""The site's own clock: each trading day's fixed results are computed on the server at their times.

The GitHub job publishes durable copies, but it starts hours late; this makes sure that, whenever the
server is awake, the morning screen is ready after 11:15, the A-share close after 15:00, 长线 after 15:05,
收盘选股 at 16:00 and Hong Kong after its close - before anyone has to wait for them. (A free Render server
sleeps after 15 idle minutes; an uptime pinger keeps it awake, see README.)
"""

from __future__ import annotations

import logging
import threading
import time

import streamlit as st

from stockrt import sample, tradingdays
from stockrt.calendar import now_bj

log = logging.getLogger(__name__)

TASKS = (("accumulation", "11:16"), ("cn_close", "15:03"), ("longterm", "15:06"), ("close_screen", "16:01"),
         ("hk_close", "16:12"))


def _run(name: str, day) -> None:
    from stockrt import accumulation, archive, checkpoint, closescreen, longterm, realtime

    if name == "accumulation":
        if accumulation.load(day) is None:
            accumulation.save(accumulation.compute(day, realtime.market_snapshot("CN"), accumulation.CHECKPOINTS,
                                                   workers=checkpoint.WORKERS))
    elif name == "cn_close":
        snap = realtime.market_snapshot("CN")
        archive.save_volumes_from_snapshot("CN", snap)
        if day not in archive.sessions("CN"):
            checkpoint.evaluate(snap)
    elif name == "longterm":
        if longterm.load(day) is None:
            snap = realtime.market_snapshot("CN")
            if checkpoint.session_day(snap) == day:
                longterm.run(day, snap, workers=checkpoint.WORKERS)
    elif name == "close_screen":
        if day not in archive.screen_days() and day in archive.volume_days("CN"):
            closescreen.run(day, workers=checkpoint.WORKERS)
    elif name == "hk_close":
        if day not in archive.sessions("HK"):
            checkpoint.evaluate(realtime.market_snapshot("HK"), spec=checkpoint.SPECS["HK"])


class Scheduler:
    def __init__(self) -> None:
        self.done: set[tuple[str, str]] = set()
        self.last_error: str | None = None
        threading.Thread(target=self._loop, daemon=True, name="site-scheduler").start()

    def _loop(self) -> None:
        while True:
            try:
                self.tick()
            except Exception as e:  # noqa: BLE001 - keep the clock running
                self.last_error = f"{now_bj():%H:%M} {e}"
                log.warning("scheduler: %s", e)
            time.sleep(30)

    def tick(self) -> None:
        if sample.is_sample():
            return
        now = now_bj()
        day = now.date()
        if now.weekday() >= 5 or tradingdays.is_trading_day("CN", day) is False:
            return
        from ui.picks_view import job_runner

        hhmm = now.strftime("%H:%M")
        for name, at in TASKS:
            key = (name, str(day))
            if key in self.done or hhmm < at:
                continue
            self.done.add(key)
            job_runner().submit(("scheduled", name, str(day)), lambda progress, n=name: _run(n, day))


@st.cache_resource
def start() -> Scheduler:
    return Scheduler()
