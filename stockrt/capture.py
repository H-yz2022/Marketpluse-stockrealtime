"""Point-in-time captures (e.g. at 14:00 Beijing) of the whole market and the watchlist.

A market-wide snapshot cannot be rebuilt after the fact - by the close, the
14:00 turnover, volume ratio and order flow are gone - so it has to be saved
when it happens. Files land in data/snapshots/<date>/ as CSV:

    <HHMM>_cn_market.csv     every A-share quote (+ main-fund flow when available)
    <HHMM>_hk_market.csv     every HK quote
    <HHMM>_watchlist_1min.csv  1-minute bars so far for each watchlist symbol
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

from . import intraday, realtime, watchlist
from .calendar import now_bj
from .config import SNAPSHOT_DIR
from .export import to_csv_bytes
from .sources import sina

log = logging.getLogger(__name__)


def traded_today() -> tuple[bool, object]:
    """True when the CSI 300 has printed a quote today (catches public holidays)."""
    idx = realtime.quotes(["sh000300"])
    qdate = idx["time"].iloc[0].date() if not idx.empty and idx["time"].iloc[0] is not None else None
    return qdate == now_bj().date(), qdate


def capture(label: str | None = None, include_flow: bool = True, include_hk: bool = True) -> list[Path]:
    now = now_bj()
    label = label or now.strftime("%H%M")
    folder = SNAPSHOT_DIR / now.strftime("%Y-%m-%d")
    folder.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    cn = realtime.market_snapshot("CN")
    if include_flow:
        try:
            flow = sina.money_flow_rank()
            cn = cn.merge(flow[["code", "main_net", "main_net_ratio", "retail_net"]], on="code", how="left")
        except Exception as e:  # noqa: BLE001
            log.warning("money-flow ranking unavailable: %s", e)
    cn.insert(0, "captured_at", now.replace(tzinfo=None))
    p = folder / f"{label}_cn_market.csv"
    p.write_bytes(to_csv_bytes(cn))
    written.append(p)

    if include_hk:
        try:
            hk = realtime.market_snapshot("HK")
            hk.insert(0, "captured_at", now.replace(tzinfo=None))
            p = folder / f"{label}_hk_market.csv"
            p.write_bytes(to_csv_bytes(hk))
            written.append(p)
        except Exception as e:  # noqa: BLE001
            log.warning("HK snapshot failed: %s", e)

    codes = watchlist.load()
    names = dict(zip(cn["code"], cn["name"]))
    _, minutes = intraday.watchlist_table(codes, names, None)
    if not minutes.empty:
        p = folder / f"{label}_watchlist_1min.csv"
        p.write_bytes(to_csv_bytes(minutes))
        written.append(p)
    return written


def list_snapshots() -> pd.DataFrame:
    rows = []
    for f in sorted(SNAPSHOT_DIR.glob("*/*.csv"), reverse=True):
        rows.append({"date": f.parent.name, "file": f.name, "size_kb": round(f.stat().st_size / 1024, 1),
                     "path": str(f), "modified": datetime.fromtimestamp(f.stat().st_mtime)})
    return pd.DataFrame(rows)


class AutoCapture:
    """Background thread that captures at fixed Beijing times on weekdays while the app runs."""

    def __init__(self) -> None:
        self.times: set[str] = {"14:00"}
        self.enabled = False
        self.last_runs: dict[str, str] = {}
        self.last_error: str | None = None
        self._thread = threading.Thread(target=self._loop, daemon=True, name="auto-capture")
        self._thread.start()

    def _loop(self) -> None:
        while True:
            try:
                if self.enabled:
                    now = now_bj()
                    hhmm = now.strftime("%H:%M")
                    key = now.strftime("%Y-%m-%d ") + hhmm
                    if now.weekday() < 5 and hhmm in self.times and key not in self.last_runs.values():
                        self.last_runs[hhmm] = key
                        if traded_today()[0]:
                            files = capture(label=hhmm.replace(":", ""))
                            log.info("auto-capture %s wrote %s", key, files)
                        else:
                            log.info("auto-capture %s skipped: market closed today", key)
            except Exception as e:  # noqa: BLE001
                self.last_error = f"{now_bj():%Y-%m-%d %H:%M} {e}"
            time.sleep(20)
