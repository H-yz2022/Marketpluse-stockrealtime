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
             "`volumes/` holds each session's closing volumes for the opening-auction queries. The newest "
             f"{KEEP_SESSIONS} sessions keep every candidate; older ones keep the ranked list and the closest stocks, "
             f"up to {KEEP_HISTORY} sessions. {KEEP_BOOKS} volume books are kept.", "",
             "The Strategy lab's scorecards (backtest and live record) are in [lab/README.md](lab/README.md). "
             "The 16:00 after-close screen (收盘选股) is in `cn/screens/<date>.md`.", ""]
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
            "params": meta.get("params"), "source": "archive", "compact": meta.get("compact", False)}


def sessions(mkt: str) -> list[date]:
    """Archived session dates, newest first."""
    d = RESULTS_DIR / mkt.lower()
    if not d.exists():
        return []
    return sorted((date.fromisoformat(f.stem) for f in d.glob("*.json")), reverse=True)


# --------------------------------------------------------------------------- end-of-day volume book

VOLUME_COLUMNS = ["code", "name", "volume", "amount", "close", "prev_close", "pct_change", "open", "high", "low",
                  "turnover_rate", "float_shares"]


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


KEEP_SESSIONS = 5   # sessions kept in full (a trading week); the other screens keep this many too
KEEP_HISTORY = 250  # front-page sessions kept in the Past sessions list: about a year, compacted after the first week
KEEP_BOOKS = 5      # volume books kept: "yesterday", the 4-day query, and 超跌反弹's 5-session return
COMPACT_TOP = 15    # the "closest stocks" a session's page falls back to when nothing passed


def compact_session(mkt: str, day: date) -> bool:
    """Shrink an older session to what its page shows: every stock that passed at any checkpoint, those one rule
    short at the last one (the near misses in the ranked list, which stays the same) and the closest few. Returns
    True if the session was rewritten."""
    base = _base(mkt, day)
    meta_f, rows_f = base.with_suffix(".json"), base.with_suffix(".parquet")
    if not meta_f.exists() or not rows_f.exists():
        return False
    meta = json.loads(meta_f.read_text(encoding="utf-8"))
    if meta.get("compact"):
        return False
    rows = pd.read_parquet(rows_f)
    if not rows.empty:
        latest = rows[rows["checkpoint"] == meta["checkpoints"][-1]]
        n_rules = sum(c.startswith("rule_") for c in rows.columns)
        closest = latest.sort_values(["rules_met", "score"], ascending=False).head(COMPACT_TOP)["code"]
        keep = set(closest) | set(latest.loc[latest["rules_met"] >= n_rules - 1, "code"]) \
            | set(rows.loc[rows["passes"].astype(bool), "code"])
        rows = rows[rows["code"].isin(keep)]
        rows.to_parquet(rows_f, index=False, compression="zstd")
    meta["compact"] = True
    meta_f.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return True


def prune_sessions(mkt: str, keep: int = KEEP_SESSIONS, history: int = KEEP_HISTORY) -> list[Path]:
    """The Past sessions list grows slowly: the newest `keep` sessions in full, older ones compacted, and only those
    beyond `history` removed. Returns the files removed."""
    removed = []
    days = sessions(mkt)
    for d in days[history:]:
        for ext in (".parquet", ".json", ".md", ".csv"):
            path = _base(mkt, d).with_suffix(ext)
            if path.exists():
                path.unlink()
                removed.append(path)
    compacted = [d for d in days[keep:history] if compact_session(mkt, d)]
    if removed or compacted:
        write_index()
    return removed


# --------------------------------------------------------------------------- 收盘选股 (after-close screen)

def _screen_base(day: date) -> Path:
    return RESULTS_DIR / "cn" / "screens" / day.isoformat()


def save_screen(day: date, frame: pd.DataFrame, meta: dict) -> Path | None:
    """One session of the after-close screen: every shortlisted stock with its measures (parquet), the run's
    notes (json) and a readable page with the stocks that met every rule (md)."""
    if sample.is_sample():
        return None
    from .closescreen import QUERY_ZH, apply, rules

    base = _screen_base(day)
    base.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(base.with_suffix(".parquet"), index=False, compression="zstd")
    base.with_suffix(".json").write_text(json.dumps({"day": day.isoformat(), **meta}, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
    lines = [f"# 收盘选股 · {day:%a %d %b %Y}", "", QUERY_ZH, "",
             "; ".join(f"**{lbl}**: {desc}" for _, lbl, desc in rules()), ""]
    d = apply(frame) if not frame.empty else frame
    hits = d[d["passes"]] if not d.empty else d
    if hits.empty:
        lines.append(f"No stock met every rule ({meta.get('shortlisted', 0)} shortlisted).")
    else:
        lines += ["| Code | Name | Close | Change | Volume x prev | Turnover | Above resistance | Above MA250 |",
                  "|---|---|---|---|---|---|---|---|"]
        for r in hits.itertuples():
            lines.append(f"| {r.code[2:]} | {r.name} | {r.close:.2f} | {r.pct_change:+.2f}% | {r.vol_x:.1f}x | "
                         f"{r.turnover_rate:.2f}% | {r.above_resistance_pct:+.2f}% | {r.above_ma_pct:+.2f}% |")
    lines += ["", "Research tooling, not investment advice."]
    base.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return base


def screen_days() -> list[date]:
    d = RESULTS_DIR / "cn" / "screens"
    return sorted((date.fromisoformat(f.stem) for f in d.glob("*.parquet")), reverse=True) if d.exists() else []


def load_screen(day: date) -> tuple[pd.DataFrame, dict] | None:
    base = _screen_base(day)
    if not base.with_suffix(".parquet").exists():
        return None
    meta_f = base.with_suffix(".json")
    meta = json.loads(meta_f.read_text(encoding="utf-8")) if meta_f.exists() else {}
    return pd.read_parquet(base.with_suffix(".parquet")), meta


def prune_screens(keep: int = KEEP_SESSIONS) -> list[Path]:
    removed = []
    for d in screen_days()[keep:]:
        for ext in (".parquet", ".json", ".md"):
            path = _screen_base(d).with_suffix(ext)
            if path.exists():
                path.unlink()
                removed.append(path)
    return removed


def write_job_log(steps: list[dict], now) -> Path:
    """results/job_log.json: what the last daily-update runs did (newest first) - readable without GitHub access."""
    path = RESULTS_DIR / "job_log.json"
    try:
        runs = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    except (OSError, ValueError):
        runs = []
    runs = [{"at": now.strftime("%Y-%m-%d %H:%M"), "steps": steps}] + runs
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(runs[:12], ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def _panel_last():
    from . import panel

    return panel.last_day()


def write_manifest(now) -> Path:
    """results/manifest.json: what is published, and when - the website checks it to pick up new results."""
    path = RESULTS_DIR / "manifest.json"
    path.write_text(json.dumps({
        "updated": now.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "sessions": {m: [d.isoformat() for d in sessions(m)] for m in ("CN", "HK")},
        "volume_books": [d.isoformat() for d in volume_days("CN")],
        "close_screens": [d.isoformat() for d in screen_days()],
        "panel": str(_panel_last()),
    }, indent=1), encoding="utf-8")
    return path


def prune_volumes(mkt: str, keep: int = KEEP_BOOKS) -> list[Path]:
    """Keep the newest `keep` volume books (all the queries need)."""
    removed = []
    for d in volume_days(mkt)[keep:]:
        path = RESULTS_DIR / mkt.lower() / "volumes" / f"{d.isoformat()}.parquet"
        path.unlink(missing_ok=True)
        removed.append(path)
    return removed


def load_volume_history(mkt: str, before: date, n: int = 4) -> list[tuple[date, pd.DataFrame]]:
    """The `n` newest volume books strictly before `before`, newest first."""
    days = [d for d in volume_days(mkt) if d < before][:n]
    return [(d, pd.read_parquet(RESULTS_DIR / mkt.lower() / "volumes" / f"{d.isoformat()}.parquet")) for d in days]


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
