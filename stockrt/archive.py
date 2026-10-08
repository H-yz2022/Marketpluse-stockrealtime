"""Finished 尾盘选股 sessions kept in the repository: results/<market>/<date>.parquet + .json.

Render's free disk is wiped on every restart, so results computed there vanish.
Sessions archived here travel with the code: every deployment - including a cold
free-tier instance - opens on real past sessions immediately, and the front
page's "Past sessions" section lists them.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd

from . import sample
from .config import ROOT

RESULTS_DIR = ROOT / "results"


def _base(mkt: str, day: date) -> Path:
    return RESULTS_DIR / mkt.lower() / day.isoformat()


def save(res: dict, mkt: str, params: dict) -> Path | None:
    """Archive a complete result (every checkpoint) for the default rules."""
    if sample.is_sample() or not res.get("frames"):
        return None
    base = _base(mkt, res["day"])
    base.parent.mkdir(parents=True, exist_ok=True)
    rows = pd.concat([f.assign(checkpoint=c) for c, f in res["frames"].items()], ignore_index=True)
    rows.to_parquet(base.with_suffix(".parquet"), index=False, compression="zstd")
    base.with_suffix(".json").write_text(json.dumps({
        "day": res["day"].isoformat(), "market": mkt, "checkpoints": list(res["frames"]),
        "index_pct": res["index_pct"], "notes": res["notes"], "candidates": res["candidates"],
        "flow_checked": res.get("flow_checked", True), "params": params}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    return base


def load(mkt: str, day: date) -> dict | None:
    base = _base(mkt, day)
    meta_f, rows_f = base.with_suffix(".json"), base.with_suffix(".parquet")
    if not meta_f.exists() or not rows_f.exists():
        return None
    meta = json.loads(meta_f.read_text(encoding="utf-8"))
    rows = pd.read_parquet(rows_f)
    frames = {c: rows[rows["checkpoint"] == c].reset_index(drop=True) for c in meta["checkpoints"]}
    return {"day": day, "frames": frames, "index_pct": meta["index_pct"], "pending": [], "notes": meta["notes"],
            "candidates": meta["candidates"], "flow_checked": meta.get("flow_checked", True),
            "params": meta.get("params"), "source": "archive"}


def sessions(mkt: str) -> list[date]:
    """Archived session dates, newest first."""
    d = RESULTS_DIR / mkt.lower()
    if not d.exists():
        return []
    return sorted((date.fromisoformat(f.stem) for f in d.glob("*.json")), reverse=True)
