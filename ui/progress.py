"""One clear "updating" card for every background job on the site.

Large status line, a progress bar with the current step, the time elapsed and an honest estimate of the time left:
from the job's measured speed once it has one, else how long this task has really taken on this server. It says so
when a job waits in the queue, is slower than usual, or is a retry, and when a job fails it shows the reason and when
it will try again. Polls every 2 s inside a fragment, so only the card redraws. One language at a time (ui.i18n).
"""

from __future__ import annotations

import re
import time

import streamlit as st

from stockrt import jobs
from ui.i18n import bi, t, zh


def _fmt(sec: float) -> str:
    sec = max(int(round(sec)), 0)
    if sec < 60:
        return t(f"{sec} 秒", f"{sec} s")
    return t(f"{sec // 60} 分 {sec % 60:02d} 秒", f"{sec // 60} min {sec % 60:02d} s")


def _about(sec: float) -> str:
    """An estimate, not a stopwatch: rounded, and labelled as approximate."""
    if sec < 10:
        return t("即将完成", "almost done")
    step = 10 if sec >= 60 else 5
    return t("约 ", "about ") + _fmt(round(sec / step) * step)


MESSAGES = [
    (r"Rebuilding (\d+)/(\d+) stocks at (.+)", "正在重建 {0}/{1} 只股票（{2}）"),
    (r"Rebuilding (\d+)/(\d+) stocks for (.+)", "正在重建 {0}/{1} 只股票（{2}）"),
    (r"Starting the worker", "正在启动后台任务"),
    (r"Starting", "正在开始"),
    (r"Done: .*", "完成"),
    (r"Failed", "失败"),
    (r"History panel ([\d,]+)", "历史数据 {0}"),
    (r"Daily history (\d+)/(\d+)", "日线数据 {0}/{1}"),
    (r"Main-fund flow (\d+)/(\d+)", "主力资金 {0}/{1}"),
    (r"(\S+) shortlist: (\d+) stocks.*", "{0} 初筛：{1} 只股票"),
]


def message(text: str) -> str:
    """A worker's progress line in the visitor's language."""
    text = bi(text or "")
    if not zh():
        return text
    for pat, zh_text in MESSAGES:
        m = re.fullmatch(pat, text.strip())
        if m:
            return zh_text.format(*m.groups())
    return text


def eta(job: jobs.Job, typical: float | None = None) -> float | None:
    """Seconds left: from the speed since the slow stage began, else this task's usual duration. None when the
    job has already outlasted its usual duration and there is no speed to go on: a guess would only be wrong."""
    now = time.time()
    if job.mark is not None and job.progress - job.mark[1] >= 0.08 and now > job.mark[0]:
        return (now - job.mark[0]) * (1 - job.progress) / (job.progress - job.mark[1])
    usual = job.typical or typical
    if usual and now - job.started < usual:
        return usual - (now - job.started)
    return None


def _what(title: str, detail: str) -> str:
    return f"**{title}** · {detail}" if detail else f"**{title}**"


def _failed(job: jobs.Job, what_zh: str, what_en: str) -> None:
    from ui.picks_view import job_runner

    with st.container(border=True):
        st.markdown(f"### :material/error: {t('这次更新没有完成', 'This update did not finish')}\n\n{_what(what_zh, what_en)}")
        st.markdown(f":red[**{t('原因', 'Reason')}:** {job.error}]")
        left = None if job.retry_at is None else job.retry_at - time.time()
        if left is None:
            st.caption(t("已重试多次，此页面不再自动重试；网站的时钟每 10 分钟还会再试。",
                         "Tried several times: this page won't retry by itself, but the site's clock tries again "
                         "every 10 minutes."))
        else:
            st.caption(t(f"第 {job.attempt} 次尝试失败，{_fmt(left)} 后自动重试，已完成的部分不会重做。下方仍显示上次保存的结果。",
                         f"Attempt {job.attempt} failed; it retries by itself in {_fmt(left)} and keeps what was "
                         "finished. The last saved result stays below."))
        if st.button(t("立即重试", "Retry now"), icon=":material/refresh:", key=f"retry_{abs(hash(job.key))}"):
            job_runner().submit(job.key, job.task, job.args, force=True, front=True)
            st.rerun()


def job_card(job: jobs.Job, what_zh: str, what_en: str, typical: float | None = None) -> None:
    """Show a queued, running or failed job; rerun the page when it finishes or its retry falls due. The headline
    and the detail line come in the visitor's language (callers pass them through ui.i18n.t)."""

    @st.fragment(run_every="2s")
    def card() -> None:
        from ui.picks_view import job_runner

        if job.state == "done":
            st.rerun()
            return
        if job.state == "failed":
            if job.retry_at is not None and time.time() >= job.retry_at:
                st.rerun()
                return
            _failed(job, what_zh, what_en)
            return
        elapsed = time.time() - job.started
        with st.container(border=True):
            st.markdown(f"### :material/autorenew: {t('正在实时更新', 'Updating live data')}")
            st.markdown(_what(what_zh, what_en))
            if job.attempt > 1:
                st.caption(t(f":material/replay: 第 {job.attempt} 次尝试，从上次进度继续。上次原因：{job.last_error}",
                             f":material/replay: Attempt {job.attempt}, continuing from where the last one stopped. "
                             f"Last time: {job.last_error}"))
            if job.state == "queued":
                n = job_runner().ahead(job.key)
                st.progress(0.02, text=t("排队中", "Queued"))
                c1, c2 = st.columns(2)
                c1.metric(t("前面还有", "Jobs ahead"), t(f"{n} 个任务", f"{n}"))
                c2.metric(t("已等待", "Waiting"), _fmt(elapsed))
            else:
                left = eta(job, typical)
                st.progress(min(max(job.progress, 0.02), 1.0), text=message(job.message))
                c1, c2 = st.columns(2)
                c1.metric(t("预计剩余", "Time left"), _about(left) if left is not None else t("比平时慢", "slower than usual"))
                c2.metric(t("已用时", "Elapsed"), _fmt(elapsed))
            st.caption(t("页面会自动刷新，无需操作。下方先显示上次保存的结果。",
                         "The page refreshes by itself when it is done - nothing to click. The last saved result "
                         "stays below meanwhile."))

    card()
