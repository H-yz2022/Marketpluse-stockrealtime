"""Shared HTTP layer: one pooled session, retries, per-host circuit breaker, health stats.

China market data lives behind undocumented JSON endpoints that throttle,
reset connections or 502 without warning (East Money especially). Every
request goes through `get()` so that:

* transient failures are retried with backoff,
* a host that keeps failing is skipped for a cool-down instead of stalling
  every page load (callers then fall back to another provider),
* the Data sources page can show live health for each host.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TypeVar
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)

BREAKER_THRESHOLD = 3     # consecutive failures before a host is paused
BREAKER_COOLDOWN = 180.0  # seconds a paused host is skipped


class SourceUnavailable(RuntimeError):
    """Raised when a host is paused by the circuit breaker or keeps failing."""


class NotFound(SourceUnavailable):
    """HTTP 404: the host is healthy, the resource does not exist (not a breaker failure)."""


@dataclass
class HostHealth:
    host: str
    ok: int = 0
    failed: int = 0
    consecutive_failures: int = 0
    last_ok: float | None = None
    last_error: str | None = None
    last_error_at: float | None = None
    paused_until: float = 0.0
    latencies: list[float] = field(default_factory=list)

    @property
    def avg_latency_ms(self) -> float | None:
        return 1000 * sum(self.latencies) / len(self.latencies) if self.latencies else None


_lock = threading.Lock()
_health: dict[str, HostHealth] = {}
_local = threading.local()


def _session(retry: bool = True) -> requests.Session:
    # One session per thread (requests.Session is not guaranteed thread-safe); a
    # second no-retry session serves optional sources that should fail fast.
    attr = "session" if retry else "session_fast"
    s = getattr(_local, attr, None)
    if s is None:
        s = requests.Session()
        policy = Retry(total=2, connect=2, read=1, backoff_factor=0.4,
                       status_forcelist=(500, 502, 503, 504), allowed_methods=("GET",)) if retry else Retry(0)
        adapter = HTTPAdapter(max_retries=policy, pool_connections=16, pool_maxsize=16)
        s.mount("https://", adapter)
        s.mount("http://", adapter)
        s.headers["User-Agent"] = UA
        setattr(_local, attr, s)
    return s


def _health_for(host: str) -> HostHealth:
    with _lock:
        if host not in _health:
            _health[host] = HostHealth(host)
        return _health[host]


def health() -> list[HostHealth]:
    with _lock:
        return sorted(_health.values(), key=lambda h: h.host)


def get(url: str, *, params: dict | None = None, headers: dict | None = None,
        timeout: float = 12.0, allow_redirects: bool = True, fast_fail: bool = False,
        cooldown: float = BREAKER_COOLDOWN) -> requests.Response:
    """GET with retries + circuit breaker. Raises SourceUnavailable on failure.

    fast_fail: no retries, and the host is paused after its first failure
    (for optional enhancers whose absence has a fallback)."""
    host = urlparse(url).hostname or url
    h = _health_for(host)
    now = time.time()
    if h.paused_until > now:
        raise SourceUnavailable(f"{host} paused for {h.paused_until - now:.0f}s after repeated failures")
    t0 = time.perf_counter()
    try:
        r = _session(retry=not fast_fail).get(url, params=params, headers=headers, timeout=timeout,
                                              allow_redirects=allow_redirects)
        if r.status_code == 404:
            with _lock:
                h.ok += 1
                h.consecutive_failures = 0
                h.last_ok = time.time()
            raise NotFound(f"{host}: HTTP 404 for {url}")
        if r.status_code >= 400:
            raise requests.HTTPError(f"HTTP {r.status_code}")
        if not r.content:
            raise requests.ConnectionError("empty response")
    except requests.RequestException as e:
        with _lock:
            h.failed += 1
            h.consecutive_failures += 1
            h.last_error = f"{type(e).__name__}: {str(e)[:160]}"
            h.last_error_at = time.time()
            if h.consecutive_failures >= (1 if fast_fail else BREAKER_THRESHOLD):
                h.paused_until = time.time() + cooldown
        raise SourceUnavailable(f"{host}: {h.last_error}") from e
    with _lock:
        h.ok += 1
        h.consecutive_failures = 0
        h.last_ok = time.time()
        h.latencies = (h.latencies + [time.perf_counter() - t0])[-50:]
    return r


def reset_breakers() -> None:
    with _lock:
        for h in _health.values():
            h.paused_until = 0.0
            h.consecutive_failures = 0


T = TypeVar("T")
R = TypeVar("R")


def pmap(fn: Callable[[T], R], items: Iterable[T], workers: int = 8) -> list[R | Exception]:
    """Map in a thread pool, returning results in order; exceptions are returned, not raised."""
    items = list(items)
    if not items:
        return []

    def safe(x: T) -> R | Exception:
        try:
            return fn(x)
        except Exception as e:  # noqa: BLE001 - surfaced to caller per item
            return e

    with ThreadPoolExecutor(max_workers=min(workers, len(items))) as ex:
        return list(ex.map(safe, items))
