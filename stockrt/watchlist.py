"""Watchlist persisted as JSON in data/watchlist.json."""

from __future__ import annotations

import json

from .config import DEFAULT_WATCHLIST, WATCHLIST_FILE
from .symbols import normalize


def load() -> list[str]:
    if WATCHLIST_FILE.exists():
        try:
            codes = json.loads(WATCHLIST_FILE.read_text(encoding="utf-8"))
            return [normalize(c) for c in codes]
        except Exception:  # noqa: BLE001 - fall back to defaults on a bad file
            pass
    return list(DEFAULT_WATCHLIST)


def save(codes: list[str]) -> None:
    clean = list(dict.fromkeys(normalize(c) for c in codes))
    WATCHLIST_FILE.write_text(json.dumps(clean, indent=2), encoding="utf-8")
