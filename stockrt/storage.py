"""Small on-disk cache: raw daily bars as Parquet, factor/universe files as JSON/Parquet.

Raw (unadjusted) bars are what gets cached, because they never change once a
day closes; adjustment is recomputed from fresh factors, so a new dividend
never leaves stale adjusted prices on disk.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

import pandas as pd

from . import sample
from .config import CACHE_DIR


def _path(kind: str, key: str, ext: str) -> Path:
    d = CACHE_DIR / kind
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{key}.{ext}"


def _atomic_write(path: Path, write) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    os.close(fd)
    try:
        write(tmp)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def age_seconds(kind: str, key: str, ext: str = "parquet") -> float | None:
    if sample.is_sample():
        return None
    p = _path(kind, key, ext)
    return time.time() - p.stat().st_mtime if p.exists() else None


def list_keys(kind: str, prefix: str = "", ext: str = "json") -> list[str]:
    """Cache keys of one kind (file stems), e.g. every saved checkpoint result."""
    if sample.is_sample():
        return []
    d = CACHE_DIR / kind
    return sorted(f.stem for f in d.glob(f"{prefix}*.{ext}")) if d.exists() else []


def load_frame(kind: str, key: str) -> pd.DataFrame | None:
    if sample.is_sample():
        return None
    p = _path(kind, key, "parquet")
    if not p.exists():
        return None
    try:
        return pd.read_parquet(p)
    except Exception:  # noqa: BLE001 - a corrupt cache file is just a cache miss
        return None


def save_frame(kind: str, key: str, df: pd.DataFrame) -> None:
    if sample.is_sample():
        return
    _atomic_write(_path(kind, key, "parquet"), lambda tmp: df.to_parquet(tmp, index=False))


# How long cache files are kept since they were last written. The per-day results are published (and rebuilt if
# ever needed); daily bars are re-downloaded on demand. Without this a server that stays up keeps every day's files.
MAX_AGE_DAYS = {"checkpoints": 7, "accumulation": 7, "dipbuy": 7, "longterm": 7, "daily": 30}


def prune_stale(now: float | None = None) -> tuple[int, int]:
    """Delete cache files older than MAX_AGE_DAYS for their kind. Returns (files removed, bytes freed)."""
    now = now or time.time()
    removed = freed = 0
    for kind, days in MAX_AGE_DAYS.items():
        d = CACHE_DIR / kind
        if not d.exists():
            continue
        for f in d.iterdir():
            try:
                st = f.stat()
                if f.is_file() and now - st.st_mtime > days * 86400:
                    f.unlink()
                    removed, freed = removed + 1, freed + st.st_size
            except OSError:
                continue
    return removed, freed


def size(path: Path) -> int:
    """Bytes under a folder (0 if missing)."""
    if not path.exists():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def delete(kind: str, key: str) -> None:
    """Remove a key's frame and JSON, if any."""
    for ext in ("parquet", "json"):
        _path(kind, key, ext).unlink(missing_ok=True)


def load_json(kind: str, key: str, max_age: float | None = None) -> Any | None:
    if sample.is_sample():
        return None
    p = _path(kind, key, "json")
    if not p.exists():
        return None
    if max_age is not None and time.time() - p.stat().st_mtime > max_age:
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def save_json(kind: str, key: str, obj: Any) -> None:
    if sample.is_sample():
        return
    text = json.dumps(obj, ensure_ascii=False, default=str)
    _atomic_write(_path(kind, key, "json"), lambda tmp: Path(tmp).write_text(text, encoding="utf-8"))
