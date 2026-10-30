"""Background jobs, so a slow network never blocks a page.

From a free-tier server the first rebuild of a session takes minutes (each request
to the mainland sources is slow from abroad). Pages submit the work here, keep
showing the last saved result, and swap in the new one when the job is done.
One job runs at a time to keep memory low.
"""

from __future__ import annotations

import gc
import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class Job:
    key: tuple
    state: str = "queued"          # queued | running | done | failed
    progress: float = 0.0
    message: str = "Waiting to start"
    error: str | None = None
    started: float = field(default_factory=time.time)
    finished: float | None = None
    task: str = ""
    args: dict = field(default_factory=dict)
    attempt: int = 1                  # 2, 3... when an earlier run of the same job failed
    last_error: str | None = None     # why the previous attempt failed
    where: str | None = None          # the last lines of the traceback
    retry_at: float | None = None     # failed: when a visitor's page may try again (None = no more automatic tries)
    typical: float | None = None      # how long this task has really taken on this server (median of recent runs)
    mark: tuple[float, float] | None = None   # (time, progress) once the slow stage is under way, for the time left

    @property
    def active(self) -> bool:
        return self.state in ("queued", "running")


class Runner:
    def __init__(self, workers: int = 1) -> None:
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="stockrt-job")
        self._jobs: dict[tuple, Job] = {}
        self._lock = threading.Lock()

    def get(self, key: tuple) -> Job | None:
        with self._lock:
            return self._jobs.get(key)

    def submit(self, key: tuple, fn: Callable[[Callable[[float, str], None]], object], force: bool = False) -> Job:
        """Start fn(progress) in the background unless the same job is queued, running or done."""
        with self._lock:
            job = self._jobs.get(key)
            if job and (job.active or (job.state == "done" and not force)):
                return job
            job = Job(key)
            self._jobs[key] = job
            # Keep the registry small: drop finished jobs other than the newest few.
            done = sorted((j for j in self._jobs.values() if not j.active), key=lambda j: j.finished or 0)
            for old in done[:-8]:
                self._jobs.pop(old.key, None)

        def progress(f: float, msg: str) -> None:
            job.progress, job.message = f, msg

        def run() -> None:
            job.state, job.message = "running", "Downloading the market snapshot…"
            try:
                fn(progress)
                job.state, job.progress, job.message = "done", 1.0, "Done"
            except Exception as e:  # noqa: BLE001 - reported on the page
                log.warning("job %s failed: %s", key, e)
                job.state, job.error = "failed", f"{type(e).__name__}: {e}"
            finally:
                job.finished = time.time()
                gc.collect()

        self._pool.submit(run)
        return job


# --------------------------------------------------------------------------- jobs in a separate process

class ProcessRunner:
    """Runs heavy jobs one at a time in a separate, lowest-priority Python process.

    A free Render server has a tenth of a CPU, and its health check gives up after 5 seconds. Running a
    rebuild inside the web server's own process (threads share one interpreter) starved it: measured on
    the live site, the health check went from 0.3 s to 5-10 s while a job ran, and Render restarted the
    instance. Here the work runs in `python -m stockrt.worker` at the lowest CPU priority, the web process
    only starts it and reads its progress file, and the worker exits when done (its memory is returned).

    Jobs are de-duplicated by key, so the site's clock and a visitor asking for the same result share one
    run. Results are written to disk by the worker; pages read them from there.

    A failed job stays failed, with its reason, until its retry time: resubmitting at once made a page restart
    the job from zero every few seconds for as long as it was open, and hid the error. Every finished
    run is written to data/jobs/history.jsonl (the Data sources page) and to the server's log (Render's Logs tab).
    """

    TIMEOUT = 15 * 60
    RETRY_WAIT = (60, 180, 600)     # seconds before a page may try again, after the 1st, 2nd and 3rd failure

    def __init__(self) -> None:
        import collections

        from .config import DATA_DIR

        self.dir = DATA_DIR / "jobs"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.history_file = self.dir / "history.jsonl"
        self._jobs: dict[tuple, Job] = {}
        self._queue: collections.deque = collections.deque()
        self._active: tuple | None = None
        self._proc = None
        self._lock = threading.Lock()
        self._trim_logs()
        self._write_history({"event": "server_start"})
        threading.Thread(target=self._loop, daemon=True, name="job-supervisor").start()

    def get(self, key: tuple) -> Job | None:
        with self._lock:
            return self._jobs.get(key)

    def busy(self) -> bool:
        with self._lock:
            return self._active is not None or bool(self._queue)

    def ahead(self, key: tuple) -> int:
        """How many jobs run before this queued one."""
        with self._lock:
            q = list(self._queue)
            return (q.index(key) if key in q else 0) + (self._active is not None)

    def submit(self, key: tuple, task: str, args: dict, force: bool = False, front: bool = False) -> Job:
        """Queue `python -m stockrt.worker task` unless the same job is queued, running, done, or failed and not
        yet due for a retry (force overrides the last two). front: run it next."""
        with self._lock:
            old = self._jobs.get(key)
            if old and old.active:
                return old
            if old and not force:
                if old.state == "done" or (old.state == "failed" and
                                           (old.retry_at is None or time.time() < old.retry_at)):
                    return old
            job = Job(key, task=task, args=args, typical=self._typical(task, args))
            if old and old.state == "failed":
                job.attempt, job.last_error = old.attempt + 1, old.error
            job.message = "排队中 · Queued" if (self._active or self._queue) else "Starting"
            self._jobs[key] = job
            (self._queue.appendleft if front else self._queue.append)(key)
            done = sorted((j for j in self._jobs.values() if not j.active), key=lambda j: j.finished or 0)
            for gone in done[:-20]:
                self._jobs.pop(gone.key, None)
            return job

    # ------------------------------------------------------------------ the log of finished runs

    def _trim_logs(self) -> None:
        worker_log = self.dir / "worker.log"
        try:
            if worker_log.exists() and worker_log.stat().st_size > 1_000_000:
                worker_log.unlink()
            if self.history_file.exists():
                lines = self.history_file.read_text(encoding="utf-8").splitlines()
                if len(lines) > 400:
                    self.history_file.write_text("\n".join(lines[-300:]) + "\n", encoding="utf-8")
        except OSError as e:
            log.warning("job logs: %s", e)

    def _write_history(self, rec: dict) -> None:
        import json

        from .calendar import now_bj

        rec = {"at": now_bj().strftime("%Y-%m-%d %H:%M:%S"), **rec}
        line = json.dumps(rec, ensure_ascii=False, default=str)
        print(f"[job] {line}", flush=True)                # Render's Logs tab
        try:
            with open(self.history_file, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError as e:
            log.warning("job history: %s", e)

    def history(self, n: int = 50) -> list[dict]:
        """The newest `n` log entries, newest first (finished runs and server starts)."""
        import json

        try:
            lines = self.history_file.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out = []
        for line in reversed(lines[-n:]):
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    def _typical(self, task: str, args: dict) -> float | None:
        """The median duration of this task's recent successful runs on this server."""
        runs = [r["seconds"] for r in self.history(200)
                if r.get("task") == task and r.get("mkt") == args.get("mkt") and r.get("state") == "done"
                and not r.get("resumed")][:5]
        return sorted(runs)[len(runs) // 2] if runs else None

    def _finish(self, job: Job, rc: int) -> None:
        now = time.time()
        job.state = "done" if rc == 0 and not job.error else "failed"
        job.error = job.error or (None if rc == 0 else f"worker exited with code {rc}")
        job.progress, job.finished = (1.0 if job.state == "done" else job.progress), now
        if job.state == "failed":
            job.retry_at = now + self.RETRY_WAIT[job.attempt - 1] if job.attempt <= len(self.RETRY_WAIT) else None
        self._write_history({"task": job.task, "mkt": job.args.get("mkt"), "day": job.args.get("day"),
                             "state": job.state, "seconds": round(now - job.started), "attempt": job.attempt,
                             "resumed": job.attempt > 1, "result": job.message if job.state == "done" else None,
                             "error": job.error, "where": job.where})

    # ------------------------------------------------------------------ the worker process

    def _progress_file(self, key: tuple):
        import hashlib

        return self.dir / (hashlib.sha1(repr(key).encode()).hexdigest()[:16] + ".json")

    def _start(self, key: tuple) -> None:
        import json
        import os
        import subprocess
        import sys

        from .config import ROOT

        job = self._jobs[key]
        pf = self._progress_file(key)
        pf.unlink(missing_ok=True)
        worker_log = open(self.dir / "worker.log", "ab")  # noqa: SIM115 - closed when the process ends
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        self._proc = subprocess.Popen(
            [sys.executable, "-m", "stockrt.worker", job.task, json.dumps(job.args, default=str), str(pf)],
            cwd=str(ROOT), stdout=worker_log, stderr=subprocess.STDOUT, env=env)
        self._proc._log = worker_log  # type: ignore[attr-defined]
        self._active = key
        job.state, job.started, job.message = "running", time.time(), "Starting the worker"

    def _poll(self) -> None:
        import json

        key = self._active
        job = self._jobs.get(key)
        pf = self._progress_file(key)
        rc = self._proc.poll()              # before reading the file: a worker that just exited has written its reason
        if job is not None and pf.exists():
            try:
                st = json.loads(pf.read_text(encoding="utf-8"))
                job.progress, job.message = float(st.get("progress", job.progress)), st.get("message", job.message)
                if job.mark is None and job.progress >= 0.10:
                    job.mark = (time.time(), job.progress)
                if st.get("error"):
                    job.error, job.where = st["error"], st.get("where")
            except (OSError, ValueError):
                pass
        if rc is None and job is not None and time.time() - job.started > self.TIMEOUT:
            self._proc.kill()
            job.error = f"timed out after {self.TIMEOUT // 60} min"
            rc = -1
        if rc is not None:
            self._proc._log.close()  # type: ignore[attr-defined]
            if job is not None:
                self._finish(job, rc)
            self._active, self._proc = None, None

    def _loop(self) -> None:
        while True:
            try:
                with self._lock:
                    if self._active is not None:
                        self._poll()
                    if self._active is None and self._queue:
                        self._start(self._queue.popleft())
            except Exception as e:  # noqa: BLE001 - the supervisor must keep running
                log.warning("job supervisor: %s", e)
                with self._lock:
                    if self._active is not None and self._active in self._jobs:
                        self._jobs[self._active].state = "failed"
                        self._jobs[self._active].error = str(e)
                    self._active, self._proc = None, None
            time.sleep(0.5)
