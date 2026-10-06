"""Paste-ready prompts for an external AI assistant (no model is called from the app).

Each builder returns plain text: the task, exact column definitions and units,
the data itself as CSV, and guardrails (use only the data given, flag gaps).
English and Simplified Chinese versions are provided.
"""

from __future__ import annotations

import pandas as pd

MAX_ROWS = 60

DATA_DICTIONARY = {
    "en": {
        "pct_change": "today's price change vs previous close, in %",
        "volume_ratio": "volume ratio (量比): volume per minute so far today / average volume per minute over the last 5 sessions",
        "turnover_rate": "turnover rate (换手率): today's volume / float shares, in %",
        "float_mcap": "float (circulating) market value, in CNY or HKD",
        "amount": "turnover value traded today, in CNY or HKD",
        "main_net": "main funds net inflow today: large + extra-large order buying minus selling, CNY",
        "ma5 / ma10": "5- and 10-session simple moving averages of the daily close",
        "cross_days_ago": "sessions since MA5 last crossed above MA10 (0 = today)",
        "projected_volume": "today's volume scaled to a full session (equal to volume after the close)",
        "vwap": "volume-weighted average price for the session so far",
        "period_return / ann_vol / sharpe": "return over the period, annualised volatility, Sharpe ratio (252 days)",
        "max_drawdown / var_95": "worst peak-to-trough fall; 5th-percentile one-day return",
        "beta / corr / r2": "sensitivity, correlation and R² of daily returns vs the benchmark index",
    },
    "zh": {
        "pct_change": "今日涨跌幅（相对昨收，%）",
        "volume_ratio": "量比：今日开盘以来每分钟平均成交量 / 过去5个交易日每分钟平均成交量",
        "turnover_rate": "换手率：今日成交量 / 流通股本（%）",
        "float_mcap": "流通市值（人民币或港元）",
        "amount": "今日成交额（人民币或港元）",
        "main_net": "主力净流入：大单+超大单买入减卖出（人民币）",
        "ma5 / ma10": "5日、10日收盘价简单移动平均",
        "cross_days_ago": "距最近一次5日线上穿10日线的交易日数（0=今天）",
        "projected_volume": "按交易时长折算的全天成交量（收盘后等于实际成交量）",
        "vwap": "当日成交量加权平均价（均价）",
        "period_return / ann_vol / sharpe": "区间收益、年化波动率、夏普比率（按252个交易日）",
        "max_drawdown / var_95": "最大回撤；单日95% VaR（第5百分位日收益）",
        "beta / corr / r2": "相对基准指数的贝塔、相关系数、R²",
    },
}

GUARDRAILS = {
    "en": ("Rules: use only the data below; do not invent prices, news or fundamentals. If something "
           "cannot be answered from this data, say exactly what is missing. This is research, not "
           "investment advice - present observations and risks, not buy/sell instructions."),
    "zh": ("要求：只使用下面提供的数据，不要编造价格、新闻或基本面。如果数据不足以回答，请明确指出缺少什么。"
           "本分析仅用于研究，不构成投资建议——请给出观察与风险，而不是买卖指令。"),
}


def _csv(df: pd.DataFrame, cols: list[str] | None = None, n: int = MAX_ROWS) -> str:
    if df is None or df.empty:
        return "(no rows)"
    d = df[[c for c in (cols or df.columns) if c in df.columns]].head(n).copy()
    for c in d.columns:
        if pd.api.types.is_float_dtype(d[c]):
            d[c] = d[c].round(4)
    return d.to_csv(index=False).strip()


def _dictionary(lang: str, keys: list[str] | None = None) -> str:
    dd = DATA_DICTIONARY[lang]
    items = [(k, v) for k, v in dd.items() if keys is None or k in keys]
    return "\n".join(f"- {k}: {v}" for k, v in items)


def screen_spec(lang: str = "en") -> str:
    """The user's natural-language screen, ready for an AI that can read attached CSVs."""
    if lang == "zh":
        return """请帮我从A股中筛选股票，条件如下：
1. 非ST股票；
2. 今日涨幅在3%到5%之间；
3. 量比大于1；
4. 换手率在3%到8%之间；
5. 5日均线上穿10日均线（金叉）；
6. 均线向上（5日、10日均线均较前一日上升）；
7. 量价齐升（今日上涨，且折算全天成交量高于昨日）；
8. 主力资金净流入；
9. 今日涨幅强于大盘（沪深300）；
10. 流通市值在50亿到200亿元之间。

我会附上两个CSV：市场快照（每只股票一行：code, name, pct_change, volume_ratio, turnover_rate, float_mcap, main_net …）
和日线数据（code, date, close, volume …）。请用代码逐条计算，列出满足全部条件的股票，并单独列出只差一个条件的股票及其未满足的条件。
""" + GUARDRAILS["zh"]
    return """Please screen China A-shares for stocks that meet ALL of these conditions:
1. Not ST / *ST;
2. Price change today between +3% and +5%;
3. Volume ratio greater than 1;
4. Turnover rate between 3% and 8%;
5. The 5-day moving average crosses above the 10-day moving average (golden cross);
6. The moving averages are rising (MA5 and MA10 both higher than the previous session);
7. Volume and price rising together (up today, and full-day-equivalent volume above yesterday's);
8. Main funds flowing in (net inflow of large + extra-large orders);
9. Outperforming the market today (change above the CSI 300);
10. Circulating (float) market value between CNY 5 billion and 20 billion.

I will attach two CSV files: a market snapshot (one row per stock: code, name, pct_change, volume_ratio,
turnover_rate, float_mcap, main_net, ...) and daily bars (code, date, close, volume, ...). Compute each
rule with code, list the stocks that pass all ten, and separately list stocks that miss exactly one rule
(and which rule).
""" + GUARDRAILS["en"]


def screen_review(rules: list[str], funnel: list[tuple[str, int]], passed: pd.DataFrame,
                  near: pd.DataFrame, as_of: str, lang: str = "en") -> str:
    cols = ["code", "name", "price", "pct_change", "volume_ratio", "turnover_rate", "float_mcap", "amount",
            "ma5", "ma10", "cross_days_ago", "projected_volume", "prev_volume", "main_net", "board"]
    funnel_txt = "\n".join(f"- {name}: {n}" for name, n in funnel)
    if lang == "zh":
        return f"""你是一名A股市场分析助手。以下是我在 {as_of}（北京时间）用规则选股器得到的结果。

筛选条件：
{chr(10).join('- ' + r for r in rules)}

筛选漏斗（每一步后剩余股票数）：
{funnel_txt}

字段说明：
{_dictionary('zh')}

满足全部条件的股票（CSV）：
{_csv(passed, cols)}

只差一个条件的股票（CSV，missed_rule 为未满足的条件）：
{_csv(near, cols + ['missed_rule'], 30)}

请：
1. 按信号强弱对入选股票排序，并说明理由（量比、换手、资金流、均线结构）；
2. 指出每只股票最主要的风险（如换手过高、临近涨停、流通市值偏小等）；
3. 从“只差一个条件”的股票中，指出哪些值得收盘前继续观察；
4. 指出这组条件本身可能存在的偏差或盲点。

{GUARDRAILS['zh']}"""
    return f"""You are an analyst for the China A-share market. Below are the results of a rule-based stock screen
I ran at {as_of} (Beijing time).

Screen rules:
{chr(10).join('- ' + r for r in rules)}

Screen funnel (stocks remaining after each rule):
{funnel_txt}

Column definitions:
{_dictionary('en')}

Stocks that passed every rule (CSV):
{_csv(passed, cols)}

Near misses - failed exactly one rule (CSV; missed_rule says which):
{_csv(near, cols + ['missed_rule'], 30)}

Please:
1. Rank the passing stocks by signal strength and explain why (volume ratio, turnover, money flow, MA structure).
2. Flag the main risk for each (e.g. overheated turnover, close to the limit-up price, very small float).
3. From the near misses, say which are worth watching into the close and what would confirm them.
4. Point out blind spots or biases in this rule set itself.

{GUARDRAILS['en']}"""


def intraday_review(table: pd.DataFrame, cut_time: str, lang: str = "en") -> str:
    cols = ["code", "name", "trade_date", "last", "day_return", "am_return", "pm_return", "last_30m_return",
            "vwap", "vs_vwap", "range_position", "high_time", "low_time", "am_volume_share", "intraday_vol",
            "max_intraday_drawdown", "amount"]
    if lang == "zh":
        return f"""以下是我的自选股截至 {cut_time}（北京时间）的分时数据统计。收益为小数（0.012 = 1.2%）；
am_return = 上午收盘相对昨收；pm_return = 当前相对上午收盘；vs_vwap = 当前价相对均价；
range_position = 当前价在日内高低区间中的位置（0=最低，1=最高）；am_volume_share = 上午成交量占比。

{_csv(table, cols)}

请分析：
1. 哪些股票午后走强/走弱，与上午表现是否背离；
2. 哪些股票站稳均价线之上、收于日内高位，哪些冲高回落；
3. 结合成交量分布，判断尾盘可能的强弱；
4. 列出最值得收盘前关注的3只股票及理由。

{GUARDRAILS['zh']}"""
    return f"""Below are intraday statistics for my watchlist as of {cut_time} Beijing time. Returns are decimals
(0.012 = 1.2%). am_return = morning close vs previous close; pm_return = latest vs morning close;
vs_vwap = latest price vs session VWAP; range_position = where the price sits in today's high-low range
(0 = low, 1 = high); am_volume_share = share of today's volume traded in the morning.

{_csv(table, cols)}

Please analyse:
1. Which stocks strengthened or weakened in the afternoon, and whether that diverges from the morning.
2. Which are holding above VWAP near the day's high, and which have faded from their highs.
3. What the volume distribution suggests about strength into the close.
4. The three names most worth watching before the close, with reasons.

{GUARDRAILS['en']}"""


def compare_review(metrics: pd.DataFrame, period: str, bench_name: str, corr: pd.DataFrame | None,
                   yearly: pd.DataFrame | None, lang: str = "en") -> str:
    corr_txt = corr.round(2).to_csv() if corr is not None and not corr.empty else "(not available)"
    yearly_txt = yearly.round(4).to_csv() if yearly is not None and not yearly.empty else "(not available)"
    if lang == "zh":
        return f"""请比较以下股票在“{period}”区间的风险与收益（基准：{bench_name}；收益和回撤为小数）。

字段说明：
{_dictionary('zh')}
- systematic_share：收益方差中由大盘解释的比例（市场驱动部分），其余为个股特有风险

风险收益指标（CSV）：
{_csv(metrics)}

日收益相关系数矩阵：
{corr_txt}

年度收益（行=年份，列=股票）：
{yearly_txt}

请：1. 总结风险调整后收益最好和最差的股票；2. 说明哪些股票主要受大盘驱动，哪些主要由个股因素驱动；
3. 从相关性角度评价组合分散效果；4. 指出年度收益中的异常年份。

{GUARDRAILS['zh']}"""
    return f"""Compare the risk and return of these stocks over the "{period}" period (benchmark: {bench_name};
returns and drawdowns are decimals).

Column definitions:
{_dictionary('en')}
- systematic_share: share of return variance explained by the market ("market-driven"); the rest is stock-specific

Risk / return metrics (CSV):
{_csv(metrics)}

Correlation of daily returns:
{corr_txt}

Calendar-year returns (rows = years, columns = stocks):
{yearly_txt}

Please: 1. Summarise the best and worst risk-adjusted performers. 2. Explain which names are mostly
market-driven and which are driven by company-specific factors. 3. Assess diversification from the
correlations. 4. Call out unusual years.

{GUARDRAILS['en']}"""


def stock_brief(name: str, code: str, kpis: dict, intraday_stats: dict, recent: pd.DataFrame,
                titles: list[str], lang: str = "en") -> str:
    kpi_txt = "\n".join(f"- {k}: {v}" for k, v in kpis.items())
    intra_txt = "\n".join(f"- {k}: {v}" for k, v in intraday_stats.items()) or "- (not available)"
    ann = "\n".join(f"- {t}" for t in titles[:15]) or "- (none)"
    bars = _csv(recent, ["date", "open", "high", "low", "close", "volume", "amount", "turnover_rate", "ret"], 30)
    if lang == "zh":
        return f"""请为 {name}（{code}）写一份简要研究笔记。

关键指标：
{kpi_txt}

今日分时统计：
{intra_txt}

近30个交易日日线（前复权-等比，ret 为日收益小数）：
{bars}

近期公告标题：
{ann}

请包括：当前走势与成交特征、风险指标解读、公告中值得注意的事项、需要进一步核实的问题。
{GUARDRAILS['zh']}"""
    return f"""Write a short research note on {name} ({code}).

Key metrics:
{kpi_txt}

Today's intraday statistics:
{intra_txt}

Last 30 daily bars (ratio-adjusted; ret = daily return as a decimal):
{bars}

Recent announcement titles (Chinese):
{ann}

Cover: the current trend and trading activity, what the risk metrics say, anything notable in the
announcements, and open questions to verify.
{GUARDRAILS['en']}"""


def market_summary(b: dict, movers: pd.DataFrame, as_of: str, lang: str = "en") -> str:
    """Whole-market breadth plus the most active movers."""
    stats = "\n".join(f"- {k}: {round(v, 4) if isinstance(v, float) else v}" for k, v in b.items())
    cols = ["code", "name", "pct_change", "amount", "turnover_rate", "volume_ratio", "float_mcap", "board"]
    if lang == "zh":
        return f"""以下是A股市场在 {as_of}（北京时间）的整体情况与成交最活跃的个股。

市场宽度统计（advance_ratio=上涨家数占比；limit_up/limit_down=涨停/跌停家数；mood_score为规则打分，-100到100）：
{stats}

成交额最大的个股（CSV）：
{_csv(movers, cols, 40)}

请总结：今日市场情绪与赚钱效应、资金集中的方向（板块/风格）、需要警惕的信号，以及尾盘可能的走势判断依据。
{GUARDRAILS['zh']}"""
    return f"""Here is the state of the China A-share market at {as_of} Beijing time, with the most actively traded stocks.

Market breadth (advance_ratio = share of advancing stocks; limit_up / limit_down = stocks at the daily
price limit; mood_score is a rule-based score from -100 to 100):
{stats}

Most actively traded stocks by value (CSV):
{_csv(movers, cols, 40)}

Please summarise: today's market mood and breadth, where money is concentrating (sectors / styles),
warning signs, and what would indicate the direction into the close.
{GUARDRAILS['en']}"""


def custom(question: str, data: pd.DataFrame, dataset_name: str, lang: str = "en", n: int = MAX_ROWS) -> str:
    """Any question about any dataset from the app."""
    if lang == "zh":
        return f"""{question.strip()}

数据集：{dataset_name}（CSV，前 {min(n, len(data))} 行，共 {len(data)} 行）。字段说明：
{_dictionary('zh')}

{_csv(data, n=n)}

{GUARDRAILS['zh']}"""
    return f"""{question.strip()}

Dataset: {dataset_name} (CSV, first {min(n, len(data))} of {len(data)} rows). Column definitions:
{_dictionary('en')}

{_csv(data, n=n)}

{GUARDRAILS['en']}"""


# --------------------------------------------------------------------------- saved prompt library

def _library_file():
    from .config import DATA_DIR

    return DATA_DIR / "prompt_library.json"


BUILTIN_LIBRARY = [
    {"name": "Late-session momentum screen (English)", "text": screen_spec("en"), "builtin": True},
    {"name": "尾盘选股条件（中文）", "text": screen_spec("zh"), "builtin": True},
    {"name": "Explain a stock's move today",
     "text": "Using only the attached intraday (1-minute) and daily data for {stock}, explain how today's move "
             "developed: the opening gap, the morning trend, the afternoon, volume around the turning points, and "
             "where the price sits relative to VWAP. Then list what would confirm or invalidate the move tomorrow.\n\n"
             + GUARDRAILS["en"], "builtin": True},
    {"name": "Build a factor table from the daily dataset",
     "text": "From the attached daily dataset (long format: code, date, close, volume, amount, turnover_rate, ret), "
             "write Python (pandas) that computes for each code: 20-day momentum, 20-day volatility, average "
             "turnover, 60-day max drawdown, and the correlation with sh000300. Show the code, then the resulting "
             "table sorted by momentum.", "builtin": True},
]


def load_library() -> list[dict]:
    import json

    f = _library_file()
    saved = []
    if f.exists():
        try:
            saved = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            saved = []
    return BUILTIN_LIBRARY + [dict(p, builtin=False) for p in saved]


def save_to_library(name: str, text: str) -> None:
    import json

    f = _library_file()
    saved = [p for p in load_library() if not p["builtin"] and p["name"] != name]
    saved.append({"name": name, "text": text})
    f.write_text(json.dumps([{"name": p["name"], "text": p["text"]} for p in saved], ensure_ascii=False, indent=2),
                 encoding="utf-8")


def delete_from_library(name: str) -> None:
    import json

    f = _library_file()
    saved = [p for p in load_library() if not p["builtin"] and p["name"] != name]
    f.write_text(json.dumps([{"name": p["name"], "text": p["text"]} for p in saved], ensure_ascii=False, indent=2),
                 encoding="utf-8")


def checkpoint_review(rules: list[str], day: str, index_moves: dict[str, float], ranked: pd.DataFrame,
                      lang: str = "en") -> str:
    """The front-page ranked list (14:00 / 14:30 replay) as a prompt."""
    cols = ["rank", "tier", "code", "name", "score", "rules_met", "missed", "at_14:00", "at_14:30", "at_15:00",
            "pct_change", "volume_ratio", "turnover_rate", "float_mcap", "cross_days_ago", "main_net", "flow_at",
            "price", "vwap", "after_first"]
    moves = ", ".join(f"{k} {v:+.2f}%" for k, v in index_moves.items())
    tiers = ("A = met every rule at every checkpoint; B = at the latest checkpoint; C = earlier but not at the "
             "latest; D = one rule narrowly missed")
    if lang == "zh":
        return f"""以下是 {day} A股按我的尾盘选股条件、在14:00、14:30和15:00收盘三个时点回放筛选并排序的结果。沪深300：{moves}。

选股条件：
{chr(10).join('- ' + r for r in rules)}

分级：A=每个时点都满足全部条件；B=最新时点满足；C=较早时点满足、最新时点不满足；D=差一个条件且差距很小。
at_14:00 / at_14:30 / at_15:00 列中 ✓ 表示该时点满足全部条件。
score为0-100的信号质量分（均线 25%、量能 20%、主力资金 20%、分时形态 20%、相对强弱 10%、流动性 5%）。
after_first 为14:00之后到收盘的涨跌（事后数据，未参与排序）。flow_at=end of day 表示主力资金用的是全天数据。

{_csv(ranked, cols)}

请：1. 评价排序是否合理，哪几只最可靠、为什么；2. 指出每只的主要风险；3. 结合 after 列，评估这套条件在当天的有效性；
4. 建议如何改进条件或排序。
{GUARDRAILS['zh']}"""
    return f"""Below is my China A-share late-session screen, replayed at 14:00, 14:30 and the 15:00 close on
{day} and ranked.
CSI 300 at those times: {moves}.

Screen rules:
{chr(10).join('- ' + r for r in rules)}

Tiers: {tiers}. In the at_14:00 / at_14:30 / at_15:00 columns ✓ means every rule was met at that time.
score is a 0-100 signal-quality score (trend 25%, volume 20%, main funds 20%, intraday pattern 20%,
relative strength 10%, liquidity 5%). after_first = move from 14:00 to the close (hindsight, not used
for ranking). flow_at = "end of day" means main-fund flow is the session's
end-of-day figure rather than the value at the checkpoint.

{_csv(ranked, cols)}

Please: 1. Assess whether the ranking makes sense and which names look most reliable, and why.
2. Flag the main risk for each. 3. Using the after column, judge how well this screen worked that day.
4. Suggest improvements to the rules or the ranking.
{GUARDRAILS['en']}"""
