"""The program inside the desktop app: ChinaHKMarketsCore.exe, started by the launcher (desktop/app.py).

    --server PORT              the whole site, served on this PC only (127.0.0.1:PORT)
    --worker --serve           the background worker (stockrt.worker), started by the server; stays running
    --selftest                 import everything the pages need and exit 0 (used by the build)

It needs no website: market data comes straight from Tencent, Sina and East Money, results are computed here
and kept in the user's data folder (STOCKRT_DATA_DIR, set by the launcher). Both modes exit when the process
that started them exits, so closing the window never leaves anything running.

Run from source for development:  python desktop/core.py --server 8601
"""

from __future__ import annotations

import os
import shutil
import sys
import threading
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)
APP_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))   # where streamlit_app.py is
FONT = "'Segoe UI', 'Microsoft YaHei UI', 'Microsoft YaHei', sans-serif"            # no fonts from the internet


def data_dir() -> Path:
    d = Path(os.environ.get("STOCKRT_DATA_DIR") or APP_DIR / "data")
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_to(name: str, keep_stdout: bool = False) -> None:
    """A windowed program has no console: send prints, warnings and tracebacks to a log file instead. keep_stdout:
    stdout is a pipe the parent reads (the worker's replies), so only stderr goes to the file."""
    if not FROZEN and sys.stdout is not None:
        return
    logs = data_dir() / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    path = logs / name
    try:
        if path.exists() and path.stat().st_size > 2_000_000:
            path.unlink()
    except OSError:
        pass
    f = open(path, "a", encoding="utf-8", buffering=1)  # noqa: SIM115 - open for the life of the process
    sys.stderr = f
    if not keep_stdout:
        sys.stdout = f


def exit_with_parent() -> None:
    """Stop this process as soon as the one that started it is gone (Windows)."""
    if os.name != "nt":
        return
    parent = os.getppid()

    def watch() -> None:
        import ctypes

        kernel = ctypes.windll.kernel32
        handle = kernel.OpenProcess(0x00100000, False, parent)        # SYNCHRONIZE
        if handle:
            kernel.WaitForSingleObject(handle, 0xFFFFFFFF)            # until it exits
        os._exit(0)

    threading.Thread(target=watch, daemon=True, name="parent-watch").start()


def seed_results() -> None:
    """Past sessions and the Strategy lab's backtest come with the program: copy what the user's results folder
    doesn't have yet (first start, or after an update)."""
    target = os.environ.get("STOCKRT_RESULTS_DIR")
    bundled = APP_DIR / "results"
    if not target or not bundled.exists() or Path(target) == bundled:
        return
    for f in bundled.rglob("*"):
        to = Path(target) / f.relative_to(bundled)
        if f.is_file() and not to.exists():
            to.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, to)


def server(port: int) -> int:
    log_to("server.log")
    exit_with_parent()
    seed_results()
    os.chdir(APP_DIR)                                   # .streamlit/config.toml and the pages are found from here
    sys.argv = ["streamlit", "run", str(APP_DIR / "streamlit_app.py"),
                f"--server.port={port}", "--server.address=127.0.0.1", "--server.headless=true",
                "--server.fileWatcherType=none", "--server.runOnSave=false", "--browser.gatherUsageStats=false",
                "--global.developmentMode=false", f"--theme.font={FONT}", "--theme.codeFont=Consolas, monospace"]
    from streamlit.web import cli

    return cli.main() or 0


def worker(argv: list[str]) -> int:
    serving = argv[:1] == ["--serve"]
    if serving and FROZEN:
        # A windowed program starts without sys.stdin / sys.stdout even when its parent passes pipes: open them.
        import io

        sys.stdin = io.TextIOWrapper(os.fdopen(0, "rb"), encoding="utf-8")
        sys.stdout = io.TextIOWrapper(os.fdopen(1, "wb"), encoding="utf-8", line_buffering=True)
    log_to("worker.log", keep_stdout=serving)
    exit_with_parent()
    from stockrt import worker as w

    return w.main(["worker", *argv])


def selftest() -> int:
    log_to("selftest.log")
    import importlib

    import streamlit

    for mod in ("pandas", "numpy", "pyarrow", "altair", "requests", "sqlite3", "zoneinfo", "stockrt.worker",
                "stockrt.checkpoint", "stockrt.lab", "stockrt.labpools", "stockrt.dipbuy", "stockrt.longterm",
                "stockrt.accumulation", "stockrt.closescreen", "stockrt.query", "stockrt.export", "stockrt.capture",
                "stockrt.etfs", "stockrt.sentiment", "stockrt.selftest", "ui.picks_view", "ui.screens_view",
                "ui.lab_view", "ui.scheduler", "ui.charts", "ui.cache", "ui.progress"):
        importlib.import_module(mod)
    need = [Path(streamlit.__file__).parent / "static" / "index.html", APP_DIR / "streamlit_app.py",
            APP_DIR / "app_pages" / "picks.py", APP_DIR / ".streamlit" / "config.toml",
            APP_DIR / "stockrt" / "trading_calendar.json", APP_DIR / "results" / "lab" / "meta.json"]
    missing = [str(p) for p in need if not p.exists()]
    import zoneinfo

    zoneinfo.ZoneInfo("Asia/Shanghai")
    print("selftest", "ok" if not missing else f"missing: {missing}", flush=True)
    return 1 if missing else 0


def main(argv: list[str]) -> int:
    if not FROZEN:
        sys.path.insert(0, str(APP_DIR))
    if argv[:1] == ["--server"]:
        return server(int(argv[1]))
    if argv[:1] == ["--worker"]:
        return worker(argv[1:])
    if argv[:1] == ["--selftest"]:
        return selftest()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
