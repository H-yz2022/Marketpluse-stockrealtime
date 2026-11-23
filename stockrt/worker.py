"""The background worker: one heavy task per process, at the lowest CPU priority.

    python -m stockrt.worker <task> '<json args>' <progress file>

Started by stockrt.jobs.ProcessRunner, never imported by the web pages. It reads the results folder the
website is using, does the work, writes its results there (or to the shared cache) and its progress to
the progress file, then exits - so the web server keeps answering, and the memory is returned.

Tasks:
    frontpage      尾盘选股 checkpoints for a session (A-shares or Hong Kong)
    accumulation   主力暗中吸筹 at the checkpoints reached so far
    longterm       长线 on the closing data
    dipbuy         低位吸筹 - live during the session, fixed after the close
    flowpicks      资金选股 (basic / advanced and hot-theme versions) on the closing data
    themes         the concept map (which companies are in each concept), refreshed weekly
    close_screen   收盘选股 (16:00)
    lab_today      Strategy lab: today's signals
    cn_close       the A-share close: volume book + front page
    sync           download the newly published results
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from datetime import date
from pathlib import Path


class Progress:
    def __init__(self, path: Path) -> None:
        self.path, self.last = path, 0.0
        self.write(0.0, "Starting")

    def write(self, f: float, msg: str, error: str | None = None, where: str | None = None) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"progress": f, "message": msg, "error": error, "where": where, "at": time.time()},
                                  ensure_ascii=False), encoding="utf-8")
        for _ in range(20):             # Windows refuses the swap while the web server is reading the file
            try:
                os.replace(tmp, self.path)
                return
            except PermissionError:
                time.sleep(0.05)
        os.replace(tmp, self.path)

    def __call__(self, f: float, msg: str) -> None:
        if time.time() - self.last >= 0.5 or f >= 1:
            self.last = time.time()
            self.write(f, msg)


def _day(a: dict) -> date:
    return date.fromisoformat(a["day"])


def frontpage(a: dict, progress) -> str:
    from . import archive, checkpoint, realtime
    from .screener import ScreenParams

    spec = checkpoint.SPECS[a["mkt"]]
    p = ScreenParams.from_dict(a["params"]) if a.get("params") else ScreenParams()
    progress(0.02, "下载全市场行情 · Downloading the market snapshot")
    snap = realtime.market_snapshot(a["mkt"])
    res = checkpoint.evaluate(snap, p, progress=progress, use_cache=a.get("use_cache", True), spec=spec,
                              day=_day(a))
    if a["mkt"] == "CN" and checkpoint.checkpoint_reached(_day(a), "15:00"):
        archive.save_volumes_from_snapshot("CN", snap)
    if not res["frames"]:
        raise RuntimeError("; ".join(res["notes"]) or "no stock could be rebuilt")
    return f"{res['day']} {list(res['frames'])}"


def accumulation(a: dict, progress) -> str:
    from . import accumulation as acc
    from . import realtime

    cps = tuple(a["reached"])
    progress(0.02, "下载全市场行情 · Downloading the market snapshot")
    res = acc.compute(_day(a), realtime.market_snapshot("CN"), cps, progress, workers=a.get("workers", 12))
    acc.save_partial(res)
    if cps == acc.CHECKPOINTS:
        acc.save(res)
    return f"{len(res['frame'])} of {res['candidates']}"


def longterm(a: dict, progress) -> str:
    from . import checkpoint, realtime
    from . import longterm as lt

    snap = realtime.market_snapshot("CN")
    if checkpoint.session_day(snap) != _day(a):
        raise RuntimeError("the quotes have moved on to another session")
    res = lt.run(_day(a), snap, progress, workers=a.get("workers", 12))
    return f"{res['shortlisted']} shortlisted"


def dipbuy(a: dict, progress) -> str:
    from . import checkpoint, realtime
    from . import dipbuy as dip

    snap = realtime.market_snapshot("CN")
    if checkpoint.session_day(snap) != _day(a):
        raise RuntimeError("the quotes have moved on to another session")
    res = dip.run(_day(a), snap, progress, workers=a.get("workers", 12))
    return (f"{int(dip.apply(res['frame']).passes.sum())} of {res['shortlisted']} shortlisted"
            f" (money flow: {res.get('flow_sources', '?')})")


def flowpicks(a: dict, progress) -> str:
    from . import checkpoint, realtime
    from . import flowpicks as fp

    snap = realtime.market_snapshot("CN")
    if checkpoint.session_day(snap) != _day(a):
        raise RuntimeError("the quotes have moved on to another session")
    res = fp.run(_day(a), snap, progress, workers=a.get("workers", 12))
    return f"{int(fp.apply_basic(res['frame']).passes.sum())} basic+advanced; " + " ".join(res["notes"])


def themes(a: dict, progress) -> str:
    from . import themes as th

    res = th.refresh(progress)
    return f"{res['boards']} concepts, {res['rows']:,} companies-in-concepts"


def close_screen(a: dict, progress) -> str:
    from . import archive, checkpoint, closescreen, realtime

    day = _day(a)
    if day not in archive.volume_days("CN"):
        snap = realtime.market_snapshot("CN")
        if checkpoint.session_day(snap) == day:
            archive.save_volumes_from_snapshot("CN", snap)
    res = closescreen.run(day, progress=progress, workers=a.get("workers", 12))
    return f"{res['shortlisted']} shortlisted"


def lab_today(a: dict, progress) -> str:
    from . import lab, realtime

    res = lab.today(realtime.market_snapshot("CN"), progress, workers=a.get("workers", 12))
    lab.save_today(res)
    return f"{len(res['picks'])} picks"


def cn_close(a: dict, progress) -> str:
    from . import archive, checkpoint, realtime

    snap = realtime.market_snapshot("CN")
    archive.save_volumes_from_snapshot("CN", snap)
    day = checkpoint.session_day(snap)
    if day not in archive.sessions("CN"):
        checkpoint.evaluate(snap, progress=progress)
    return str(day)


def sync(a: dict, progress) -> str:
    from . import published

    return str(published.download())


def selftest(a: dict, progress) -> str:
    """Checks the worker machinery (used by the tests); "pid" reports which process ran it."""
    for i in range(3):
        progress((i + 1) / 4, f"step {i + 1}")
        time.sleep(a.get("pause", 0.1))
    if a.get("fail"):
        raise RuntimeError("asked to fail")
    if a.get("crash"):
        os._exit(3)                         # a worker that dies mid-job
    return f"ok pid {os.getpid()}" if a.get("pid") else "ok"


TASKS = {"selftest": selftest, "frontpage": frontpage, "accumulation": accumulation, "longterm": longterm,
         "dipbuy": dipbuy, "flowpicks": flowpicks, "themes": themes,
         "close_screen": close_screen, "lab_today": lab_today, "cn_close": cn_close, "sync": sync}


def run(task: str, args: dict, progress_path: Path) -> int:
    """One job: its progress and outcome go to the progress file; returns 0 when it succeeded."""
    progress = Progress(progress_path)
    try:
        from . import http, published

        http.reset_breakers()               # a source paused during an earlier job gets a fresh chance
        published.use_current()             # the same results folder the website is reading
        out = TASKS[task](args, progress)
        progress.write(1.0, f"Done: {out}")
        return 0
    except Exception as e:  # noqa: BLE001 - reported to the page through the progress file
        traceback.print_exc()
        tb = traceback.format_exc().strip().splitlines()
        progress.write(0.0, "Failed", error=f"{type(e).__name__}: {str(e)[:300]}", where=" | ".join(tb[-4:])[:500])
        return 1


def serve() -> int:
    """Stay running and take jobs one at a time: one JSON line per job on stdin ({"task", "args", "progress"}),
    one line back on stdout when it ends ({"progress", "rc"}). Code is loaded and connections to the data sources
    are opened once, instead of for every job (measured: ~4.5 s of imports, 1.5-4 s for a source's first request
    from the US). Anything a task prints goes to stderr (the worker log), so stdout carries only the replies."""
    reply = sys.stdout
    sys.stdout = sys.stderr
    for line in sys.stdin:
        if not line.strip():
            continue
        req = json.loads(line)
        rc = run(req["task"], req.get("args") or {}, Path(req["progress"]))
        reply.write(json.dumps({"progress": req["progress"], "rc": rc}) + "\n")
        reply.flush()
    return 0


def main(argv: list[str]) -> int:
    if hasattr(os, "nice"):
        try:
            os.nice(19)                     # the web server always goes first
        except OSError:
            pass
    if argv[1:2] == ["--serve"]:
        return serve()
    return run(argv[1], json.loads(argv[2]), Path(argv[3]))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
