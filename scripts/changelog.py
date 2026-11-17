"""Writes CHANGELOG.md: every commit, newest first, grouped by day, with the details from its message.

    python scripts/changelog.py

Run it before committing (the commit then includes the updated log); it reads only the git history.
"""

from __future__ import annotations

import subprocess
from collections import OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEP, END = "\x1f", "\x1e"


def commits() -> list[dict]:
    out = subprocess.run(["git", "log", f"--format=%h{SEP}%ad{SEP}%s{SEP}%b{END}", "--date=short"], cwd=ROOT,
                         capture_output=True, text=True, encoding="utf-8", check=True).stdout
    rows = []
    for chunk in out.split(END):
        if not chunk.strip():
            continue
        h, day, subject, body = (chunk.strip("\n").split(SEP) + [""])[:4]
        rows_ = [ln.rstrip() for ln in body.splitlines() if not ln.startswith(("Co-Authored-By:", "🤖"))]
        lines: list[str] = []                       # one entry per bullet or paragraph, wrapped lines joined
        fresh = True
        for ln in rows_:
            text = ln.strip()
            if not text:
                fresh = True
            elif fresh or text.startswith(("- ", "* ")):
                lines.append(text[2:] if text.startswith(("- ", "* ")) else text)
                fresh = False
            else:
                lines[-1] += " " + text
        rows.append({"hash": h, "day": day, "subject": subject.strip(), "body": lines})
    return rows


def render(rows: list[dict]) -> str:
    days: OrderedDict[str, list[dict]] = OrderedDict()
    for r in rows:
        days.setdefault(r["day"], []).append(r)
    out = ["# Change log · 更新记录", "",
           "Every change to the website and the desktop app, newest first (from the project's history; regenerate "
           "with `python scripts/changelog.py`).", ""]
    for day, items in days.items():
        out += [f"## {day}", ""]
        for r in items:
            out.append(f"- **{r['subject']}**")
            out += [f"  - {ln}" for ln in r["body"]]
        out.append("")
    return "\n".join(out)


if __name__ == "__main__":
    path = ROOT / "CHANGELOG.md"
    rows = commits()
    path.write_text(render(rows), encoding="utf-8")
    print(f"{path}: {len(rows)} commits")
