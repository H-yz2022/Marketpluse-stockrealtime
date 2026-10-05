"""Rule-based sentiment - transparent signals, no language model.

Each component is scaled to [-1, 1] and shown separately, so the composite
score can always be traced back to the data that produced it:

* order flow       active buying vs active selling volume (外盘 vs 内盘)
* main funds       large/extra-large order net inflow as % of turnover
* relative strength today's move minus the benchmark's
* close location   where the price sits in today's high-low range
* announcements    keyword tone of recent company announcement titles
"""

from __future__ import annotations

import math

import pandas as pd

POSITIVE = [
    "增持", "回购", "预增", "扭亏", "中标", "签订", "签署", "获得", "分红", "派息", "上调", "增长",
    "突破", "批准", "通过审核", "注册生效", "战略合作", "大幅增长", "创新高", "股份購回", "股份回购",
]
NEGATIVE = [
    "减持", "质押", "冻结", "诉讼", "仲裁", "处罚", "立案", "调查", "预亏", "预减", "亏损", "下修",
    "风险提示", "退市", "终止", "违规", "警示", "问询", "延期", "停产", "召回", "被执行", "失信",
]


def _clip(x: float) -> float:
    return max(-1.0, min(1.0, x))


def headline_tone(titles: list[str]) -> tuple[float | None, list[dict]]:
    """Average keyword tone of announcement titles and the matches that drove it."""
    scores, hits = [], []
    for t in titles:
        if not t:
            continue
        pos = [w for w in POSITIVE if w in t]
        neg = [w for w in NEGATIVE if w in t]
        if pos or neg:
            s = (len(pos) - len(neg)) / (len(pos) + len(neg))
            scores.append(s)
            hits.append({"title": t, "positive": ", ".join(pos), "negative": ", ".join(neg), "score": s})
    if not scores:
        return None, hits
    return sum(scores) / len(scores), hits


def stock_sentiment(quote: dict, flow: dict | None, bench_pct: float | None,
                    titles: list[str] | None = None) -> dict:
    comps: dict[str, float | None] = {}
    buy, sell = quote.get("buy_volume"), quote.get("sell_volume")
    if buy is not None and sell is not None and (buy + sell) > 0:
        comps["Order flow"] = _clip((buy - sell) / (buy + sell) * 2)
    if flow and flow.get("main_net_ratio") is not None and not pd.isna(flow.get("main_net_ratio")):
        comps["Main funds"] = _clip(float(flow["main_net_ratio"]) / 20)
    pct = quote.get("pct_change")
    if pct is not None and bench_pct is not None:
        comps["Relative strength"] = _clip((pct - bench_pct) / 4)
    hi, lo, px = quote.get("high"), quote.get("low"), quote.get("price")
    if hi and lo and px and hi > lo:
        comps["Close location"] = _clip(2 * (px - lo) / (hi - lo) - 1)
    hits: list[dict] = []
    if titles:
        tone, hits = headline_tone(titles)
        if tone is not None:
            comps["Announcements"] = _clip(tone)
    vals = [v for v in comps.values() if v is not None and not math.isnan(v)]
    score = 100 * sum(vals) / len(vals) if vals else None
    return {"score": score, "label": label(score), "components": comps, "headline_hits": hits}


def label(score: float | None) -> str:
    if score is None:
        return "n/a"
    if score >= 35:
        return "Bullish"
    if score >= 10:
        return "Mildly bullish"
    if score > -10:
        return "Neutral"
    if score > -35:
        return "Mildly bearish"
    return "Bearish"
