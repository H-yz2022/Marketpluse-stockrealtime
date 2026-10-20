"""One clear "updating" card for every background job on the site.

Large bilingual status, a progress bar with the current step, the time elapsed and an estimated
countdown, and a reassurance that the page updates by itself and that the last saved result stays
readable below. Polls every 2 s inside a fragment, so only the card redraws.
"""

from __future__ import annotations

import time

import streamlit as st

from stockrt import jobs


def _fmt(sec: float) -> str:
    sec = max(int(round(sec)), 0)
    if sec < 60:
        return f"{sec} 秒 · {sec} s"
    return f"{sec // 60} 分 {sec % 60:02d} 秒 · {sec // 60} min {sec % 60:02d} s"


def eta(job: jobs.Job, typical: float | None = None) -> float | None:
    """Seconds left: from the progress rate once it is meaningful, else the typical duration."""
    elapsed = time.time() - job.started
    if job.progress >= 0.08 and elapsed > 3:
        return elapsed * (1 - job.progress) / job.progress
    if typical:
        return max(typical - elapsed, 5)
    return None


def job_card(job: jobs.Job, what_zh: str, what_en: str, typical: float | None = None) -> None:
    """Show a running job; rerun the page when it finishes."""

    @st.fragment(run_every="2s")
    def card() -> None:
        if not job.active:
            st.rerun()
            return
        elapsed = time.time() - job.started
        left = eta(job, typical)
        with st.container(border=True):
            st.markdown("### :material/autorenew: 正在实时更新 · Updating live data")
            st.markdown(f"**{what_zh}** · {what_en}")
            st.progress(min(max(job.progress, 0.02), 1.0), text=job.message)
            c1, c2 = st.columns(2)
            c1.metric("预计剩余 · Time left", _fmt(left) if left is not None else "估算中 · estimating…")
            c2.metric("已用时 · Elapsed", _fmt(elapsed))
            st.caption("页面会自动刷新，无需操作。下方先显示上次保存的结果。 · The page refreshes by itself when it is "
                       "done - nothing to click. The last saved result stays below meanwhile.")

    card()
