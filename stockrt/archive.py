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


MARKET_NAME = {"CN": "A-shares", "HK": "Hong Kong"}
TIER_NAME = {"A": "A · every checkpoint", "B": "B · latest checkpoint", "C": "C · earlier only", "D": "D · near miss"}


def write_report(res: dict, mkt: str, ranked: pd.DataFrame, rules: list[str], index_name: str) -> Path | None:
    """Static, human-readable copy of a session: <date>.md (renders on GitHub) and <date>.csv (ranked list)."""
    if sample.is_sample() or not res.get("frames"):
        return None
    base = _base(mkt, res["day"])
    base.parent.mkdir(parents=True, exist_ok=True)
    cps = list(res["frames"])
    lines = [f"# 尾盘选股 · {MARKET_NAME.get(mkt, mkt)} · {res['day']:%a %d %b %Y}", "",
             "Rules: " + "; ".join(rules) + ".", "",
             f"| Checkpoint | Met every rule | {index_name} |", "|---|---|---|"]
    for c in cps:
        idx = res["index_pct"].get(c)
        lines.append(f"| {c} | {int(res['frames'][c]['passes'].sum())} of {len(res['frames'][c])} | "
                     + (f"{idx:+.2f}%" if idx is not None else "—") + " |")
    lines += ["", "## Ranked list", ""]
    if ranked.empty:
        lines.append("No stock met every rule or came within one narrowly missed rule.")
    else:
        hdr = ["#", "Tier", "Code", "Name", "Score", "Rules met", *cps, f"Change at {cps[-1]}", "Vol ratio",
               "Turnover", "Float cap (bn)", "Missed"]
        lines += ["| " + " | ".join(hdr) + " |", "|" + "---|" * len(hdr)]
        for _, r in ranked.iterrows():
            cells = [str(r["rank"]), TIER_NAME.get(r["tier"], r["tier"]), r["code"][2:], str(r["name"]),
                     f"{r['score']:.0f}", f"{int(r['rules_met'])}/{len(rules)}",
                     *[str(r.get(f"at_{c}", "")) or "·" for c in cps], f"{r['pct_change']:+.2f}%",
                     f"{r['volume_ratio']:.2f}", f"{r['turnover_rate']:.2f}%", f"{r['float_mcap'] / 1e9:.1f}",
                     str(r["missed"]) or "—"]
            lines.append("| " + " | ".join(cells) + " |")
    for n in res.get("notes", []):
        lines += ["", f"_{n}_"]
    lines += ["", "Tier first (a signal that held across checkpoints is more reliable), then signal score 0-100. "
                  "Research tooling, not investment advice."]
    base.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    keep = [c for c in ["rank", "tier", "code", "name", "score", "rules_met", *[f"at_{c}" for c in cps], "missed",
                        "pct_change", "volume_ratio", "turnover_rate", "float_mcap", "main_net", "confidence",
                        "after_first"] if c in ranked]
    ranked[keep].to_csv(base.with_suffix(".csv"), index=False, encoding="utf-8-sig")
    write_index()
    return base


def write_index() -> Path:
    """results/README.md: every archived session, newest first, linking to its static page."""
    lines = ["# Archived sessions", "",
             "Every finished 尾盘选股 session, kept as static files: `<date>.md` (readable here on GitHub), "
             "`<date>.csv` (ranked list) and `<date>.parquet` + `.json` (everything the app needs). "
             "`volumes/` holds each session's closing volumes for the opening-auction queries.", ""]
    for mkt in ("CN", "HK"):
        days = sessions(mkt)
        if not days:
            continue
        lines += [f"## {MARKET_NAME[mkt]}", ""]
        for d in days:
            meta = json.loads((_base(mkt, d).with_suffix(".json")).read_text(encoding="utf-8"))
            link = f"{mkt.lower()}/{d.isoformat()}.md"
            exists = (RESULTS_DIR / link).exists()
            cps = ", ".join(meta["checkpoints"])
            lines.append(f"- [{d:%a %d %b %Y}]({link})" if exists else f"- {d:%a %d %b %Y}")
            lines[-1] += f" · checkpoints {cps}"
        lines.append("")
    path = RESULTS_DIR / "README.md"
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


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


# --------------------------------------------------------------------------- end-of-day volume book

VOLUME_COLUMNS = ["code", "name", "volume", "amount", "close", "prev_close", "pct_change"]


def save_volumes(mkt: str, day: date, book: pd.DataFrame) -> Path | None:
    """Every stock's closing volume / value / close for a session - 'yesterday' for tomorrow's queries."""
    if sample.is_sample() or book.empty:
        return None
    path = RESULTS_DIR / mkt.lower() / "volumes" / f"{day.isoformat()}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    book.reindex(columns=VOLUME_COLUMNS).to_parquet(path, index=False, compression="zstd")
    return path


def volume_days(mkt: str) -> list[date]:
    d = RESULTS_DIR / mkt.lower() / "volumes"
    return sorted((date.fromisoformat(f.stem) for f in d.glob("*.parquet")), reverse=True) if d.exists() else []


def load_volumes(mkt: str, before: date) -> tuple[date, pd.DataFrame] | None:
    """The newest volume book strictly before `before` (i.e. the previous session's)."""
    days = [d for d in volume_days(mkt) if d < before]
    if not days:
        return None
    path = RESULTS_DIR / mkt.lower() / "volumes" / f"{days[0].isoformat()}.parquet"
    return days[0], pd.read_parquet(path)


def save_volumes_from_snapshot(mkt: str, snap: pd.DataFrame) -> Path | None:
    """Store the book from a whole-market snapshot taken after the close (idempotent)."""
    if snap.empty or "time" not in snap:
        return None
    t = pd.to_datetime(snap["time"]).dropna()
    if t.empty:
        return None
    day = t.max().date()
    if day in volume_days(mkt):
        return None
    live = snap[pd.to_datetime(snap["time"]).dt.date == day].rename(columns={"price": "close"})
    return save_volumes(mkt, day, live.reindex(columns=VOLUME_COLUMNS))
