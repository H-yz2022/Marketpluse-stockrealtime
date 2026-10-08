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
