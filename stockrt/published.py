"""Published results: what the daily GitHub job computed after the close, read by the website.

The job (.github/workflows/daily.yml) writes results/ and force-pushes it to the repository's
`results` branch as a single commit, so the branch never accumulates history. The website downloads
that branch (one small tarball) whenever its manifest changes and reads sessions, volume books and
the Strategy lab from it. So after the close the site shows fixed figures straight away - no
recomputing on every visit, and nothing lost when a free-tier server restarts with an empty disk.

Without a repository to read from (offline, or no git remote), the site uses the local results/.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import shutil
import tarfile
import threading
import time
from pathlib import Path

import requests

from . import archive, sample
from .config import DATA_DIR, ROOT

log = logging.getLogger(__name__)

BRANCH = os.environ.get("STOCKRT_RESULTS_BRANCH", "results")
SYNC_DIR = DATA_DIR / "published"
MIN_INTERVAL = 60.0          # seconds between manifest checks (per server)
_lock = threading.Lock()
_state = {"checked": 0.0, "stamp": None, "dir": None, "error": None}


def repo() -> str | None:
    """owner/name of the GitHub repository: STOCKRT_RESULTS_REPO, Render's own variable, or the git remote."""
    for var in ("STOCKRT_RESULTS_REPO", "RENDER_GIT_REPO_SLUG"):
        if os.environ.get(var):
            return os.environ[var]
    cfg = ROOT / ".git" / "config"
    if cfg.exists():
        m = re.search(r"github\.com[:/]([\w.-]+/[\w.-]+?)(?:\.git)?\s", cfg.read_text(encoding="utf-8") + "\n")
        if m:
            return m.group(1)
    return None


def enabled() -> bool:
    return repo() is not None and not sample.is_sample() and os.environ.get("STOCKRT_PUBLISHED", "1") != "0"


# After the close the daily job (15:05 Beijing for A-shares, 16:15 for Hong Kong, each with a retry)
# publishes the session. Until the deadline the site waits for it rather than recomputing; after it (the
# job was late or failed) the site computes the session itself.
CLOSE = {"CN": (15, 0), "HK": (16, 10)}
DEADLINE = {"CN": (16, 10), "HK": (17, 10)}


def await_close(mkt: str, day) -> bool:
    """True when `day` is today, the market has closed, and the daily job's publication is still due."""
    from datetime import time as dtime

    from .calendar import now_bj

    if not enabled():
        return False
    now = now_bj()
    return day == now.date() and dtime(*CLOSE[mkt]) <= now.time() < dtime(*DEADLINE[mkt])


def manifest() -> dict:
    """The manifest of the results in use (published or local)."""
    f = archive.RESULTS_DIR / "manifest.json"
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    except (OSError, ValueError):
        return {}


def _safe_extract(data: bytes, dest: Path) -> Path:
    """Extract a GitHub tarball (one top-level folder) without trusting member paths."""
    dest.mkdir(parents=True, exist_ok=True)
    root = None
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for m in tar.getmembers():
            parts = Path(m.name).parts
            if not parts or m.name.startswith(("/", "\\")) or ".." in parts:
                continue
            root = root or parts[0]
            target = dest.joinpath(*parts)
            if m.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif m.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(m) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
    if root is None:
        raise ValueError("empty tarball")
    return dest / root


def sync(force: bool = False) -> dict:
    """Point the app at the newest published results (at most one manifest check a minute)."""
    if not enabled():
        return {"source": "local"}
    with _lock:
        now = time.time()
        if not force and now - _state["checked"] < MIN_INTERVAL:
            return _status()
        _state["checked"] = now
        name = repo()
        try:
            r = requests.get(f"https://raw.githubusercontent.com/{name}/{BRANCH}/manifest.json",
                             params={"t": int(now // 60)}, timeout=8)
            if r.status_code == 404:
                _state["error"] = "nothing published yet"
                return _status()
            r.raise_for_status()
            stamp = r.json().get("updated")
            if stamp and stamp == _state["stamp"]:
                return _status()
            t = requests.get(f"https://codeload.github.com/{name}/tar.gz/refs/heads/{BRANCH}", timeout=30)
            t.raise_for_status()
            folder = re.sub(r"[^0-9A-Za-z]", "", stamp or str(int(now)))
            path = _safe_extract(t.content, SYNC_DIR / folder)
            old = _state["dir"]
            archive.RESULTS_DIR = path
            _state.update(stamp=stamp, dir=path, error=None)
            if old and old != path:
                shutil.rmtree(old.parent, ignore_errors=True)
            log.info("published results %s -> %s", stamp, path)
        except Exception as e:  # noqa: BLE001 - keep showing what we have
            _state["error"] = f"{type(e).__name__}: {str(e)[:120]}"
            log.info("published results unavailable: %s", e)
        return _status()


def _status() -> dict:
    return {"source": "published" if _state["dir"] else "local", "stamp": _state["stamp"],
            "error": _state["error"]}
