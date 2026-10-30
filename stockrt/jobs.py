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
    """

    TIMEOUT = 15 * 60

    def __init__(self) -> None:
        import collections

        from .config import DATA_DIR

        self.dir = DATA_DIR / "jobs"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[tuple, Job] = {}
        self._queue: collections.deque = collections.deque()
        self._active: tuple | None = None
        self._proc = None
        self._lock = threading.Lock()
        threading.Thread(target=self._loop, daemon=True, name="job-supervisor").start()

    def get(self, key: tuple) -> Job | None:
        with self._lock:
            return self._jobs.get(key)

    def busy(self) -> bool:
        with self._lock:
            return self._active is not None or bool(self._queue)

    def submit(self, key: tuple, task: str, args: dict, force: bool = False) -> Job:
        """Queue `python -m stockrt.worker task` unless the same job is queued, running or (unless force) done."""
        with self._lock:
            job = self._jobs.get(key)
            if job and (job.active or (job.state == "done" and not force)):
                return job
            job = Job(key)
            job.task, job.args = task, args  # type: ignore[attr-defined]
            job.message = "排队中 · Queued" if (self._active or self._queue) else "Starting"
            self._jobs[key] = job
            self._queue.append(key)
            done = sorted((j for j in self._jobs.values() if not j.active), key=lambda j: j.finished or 0)
            for old in done[:-20]:
                self._jobs.pop(old.key, None)
            return job

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
        log = open(self.dir / "worker.log", "ab")  # noqa: SIM115 - closed when the process ends
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        self._proc = subprocess.Popen(
            [sys.executable, "-m", "stockrt.worker", job.task, json.dumps(job.args, default=str), str(pf)],
            cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT, env=env)
        self._proc._log = log  # type: ignore[attr-defined]
        self._active = key
        job.state, job.started, job.message = "running", time.time(), "Starting the worker"

    def _poll(self) -> None:
        import json

        key = self._active
        job = self._jobs.get(key)
        pf = self._progress_file(key)
        if job is not None and pf.exists():
            try:
                st = json.loads(pf.read_text(encoding="utf-8"))
                job.progress, job.message = float(st.get("progress", job.progress)), st.get("message", job.message)
                if st.get("error"):
                    job.error = st["error"]
            except (OSError, ValueError):
                pass
        rc = self._proc.poll()
        if rc is None and job is not None and time.time() - job.started > self.TIMEOUT:
            self._proc.kill()
            job.error = f"timed out after {self.TIMEOUT // 60} min"
            rc = -1
        if rc is not None:
            self._proc._log.close()  # type: ignore[attr-defined]
            if job is not None:
                job.state = "done" if rc == 0 and not job.error else "failed"
                job.error = job.error or (None if rc == 0 else f"worker exited with code {rc}")
                job.progress, job.finished = (1.0 if job.state == "done" else job.progress), time.time()
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
