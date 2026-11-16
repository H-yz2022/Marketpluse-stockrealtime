"""Chinese for the Strategy lab's texts that live in the data layer (stockrt.strategies, stockrt.lab,
stockrt.labpools), keyed by the English. The strategies themselves stay fixed; only how they are shown changes."""

from __future__ import annotations

import re

from ui.i18n import bi, zh

RULES = {
    "Change +3% to +5%": "涨幅 +3% 至 +5%", "Volume ratio > 1": "量比 > 1", "Turnover 3%–8%": "换手率 3%–8%",
    "MA5 crossed above MA10 (last 3 sessions)": "5日线上穿10日线（最近3个交易日内）",
    "MA5 and MA10 rising": "5日线与10日线向上", "Volume and price up": "量价齐升",
    "Beats the CSI 300": "强于沪深300", "Float cap ¥5–20B": "流通市值 50亿–200亿",
    "Main funds net inflow": "主力资金净流入", "Opening gap +2% to +7%": "高开 +2% 至 +7%",
    "Not opening at limit-up": "非涨停开盘", "Float cap ¥3–50B": "流通市值 30亿–500亿",
    "Auction value ≥ ¥10M": "竞价成交额 ≥ 1000万", "Auction volume ≥ 5% of yesterday's": "竞价量 ≥ 昨日成交量的 5%",
    "Auction turnover ≥ 0.3%": "竞价换手率 ≥ 0.3%", "Price at 10:00 ≥ the open": "10:00 价格 ≥ 开盘价",
    "At the limit-up price at 14:30": "14:30 处于涨停价", "Yesterday not limit-up (first board)": "昨日未涨停（首板）",
    "Seal broke before the close (a 14:30 order fills)": "收盘前开板（14:30 的挂单能成交）",
    "Above the 20-session high": "突破20日最高价", "Volume ratio > 2": "量比 > 2",
    "Change +3% to +7%": "涨幅 +3% 至 +7%", "Float cap ¥5–50B": "流通市值 50亿–500亿",
    "5-session return in the market's bottom 5%": "近5日涨幅处于全市场后 5%", "Change −7% to −2%": "跌幅 −7% 至 −2%",
    "Float cap ≥ ¥3B": "流通市值 ≥ 30亿",
}
ENTRY = {"S1": "14:30 价格", "S1b": "14:00 价格", "S2": "开盘价（09:25 集合竞价）", "S3": "10:00 价格",
         "S4": "涨停价（假设能成交）", "S4b": "涨停价（只有确实能成交时）", "S5": "10:30 价格", "S6": "14:30 价格"}
WHY = {
    "S1": "首页的尾盘选股：新鲜的 5日/10日 均线金叉加放量，尾盘买入，当天的强势已经确认。研究上的担忧：受 T+1 影响，"
          "A股隔夜收益平均为负，尾盘买、次日早上卖一开始就处于劣势。",
    "S1b": "同一个选股提前半小时——等到 14:30 有没有帮助？",
    "S2": "适度高开说明隔夜有买盘；涨停开盘买不进。首页竞价选股中的竞价量条件没有历史数据，因此只在实盘中检查。",
    "S3": "等 30 分钟看高开是否被抛售。检验“能守住的高开是真实需求而不是陷阱”这一说法。",
    "S4": "有争议：大资金把股票拉到涨停、次日卖出（磁吸效应），但也有研究发现次日会延续。这里假设能成交——"
          "实际上涨停排队常常买不到，因此这些收益应视为上限。",
    "S4b": "S4 的现实成交版：14:30 在涨停价排队的单子，只有收盘前开板（即收盘低于涨停）才确定能成交。"
           "而这恰恰是最差的情况（逆向选择），所以这是首板的下限；真实情况介于 S4 和 S4b 之间。名单在 15:00 确定。",
    "S5": "经典动量：早盘放量创20日新高。研究提醒，A股中暴涨的股票（“彩票”MAX 效应）之后往往表现更差——这里检验谁占上风。",
    "S6": "短期反转是A股最强的效应之一：近期跌得最多的股票往往会反弹。在尾盘仍在下跌时买入。",
}
EXITS = {"Next open (T+1)": "次日开盘（T+1）", "Next close (T+1)": "次日收盘（T+1）",
         "Close 2 sessions later (T+2)": "第2个交易日收盘（T+2）"}
BENCHES = {"Equal-weight market": "等权全市场", "CSI 300": "沪深300", "CSI 1000": "中证1000"}
PARAMS = {
    "MA5 crossed above MA10 within": "5日线上穿10日线的时间", "MA5 and MA10 rising": "5日线与10日线向上",
    "Beats the CSI 300": "强于沪深300", "Yesterday closed up": "昨日收涨", "Gap never filled by 10:00": "10:00 前未回补缺口",
    "First board only (yesterday not limit-up)": "只选首板（昨日未涨停）",
}


def rule(label: str) -> str:
    return RULES.get(label, label) if zh() else label


def param(label: str) -> str:
    """A custom-parameter label: bilingual ones split, English-only ones looked up."""
    out = bi(label)
    if zh() and out == label:
        out = PARAMS.get(re.sub(r"\s*\(.*\)$", "", label), label)
    return out


def exit_label(label: str) -> str:
    return EXITS.get(label, label) if zh() else label


def bench(label: str) -> str:
    return BENCHES.get(label, label) if zh() else label


def verdict(v: str) -> str:
    """Passes / Fails / Too early (n/60 days) / No signals, with its icon."""
    if not zh():
        return {"Passes": "✅ Passes", "Fails": "❌ Fails"}.get(v, f"⏳ {v}" if str(v).startswith("Too early") else v)
    m = re.fullmatch(r"Too early \((\d+)/(\d+) days\)", str(v))
    if m:
        return f"⏳ 为时尚早（{m.group(1)}/{m.group(2)} 天）"
    return {"Passes": "✅ 通过", "Fails": "❌ 未通过", "No signals": "没有信号", "No signals yet": "暂无信号",
            "Too few signals": "信号太少", "—": "—"}.get(v, v)


WHY_PARTS = [
    (r"average excess ([+\-\d.%]+)", "平均超额 {0}"),
    (r"not significant \(t ([\-\d.]+), beats (\d+%) of random pickers\)", "不显著（t = {0}，跑赢 {1} 的随机选股）"),
    (r"beats the market on (\d+%) of trades", "只有 {0} 的交易跑赢市场"),
    (r"positive in (\d) of 3 sub-periods", "3 个子区间中只有 {0} 个为正"),
    (r"needs (\d+) signal days and 30 trades", "需要 {0} 个信号日和 30 笔交易"),
    (r"every test met", "全部检验通过"),
]


def why(text: str) -> str:
    """A verdict's reasons ("average excess -0.12%; beats the market on 45% of trades")."""
    if not zh() or not text:
        return text
    out = []
    for part in text.split("; "):
        for pat, zh_text in WHY_PARTS:
            m = re.fullmatch(pat, part.strip())
            if m:
                out.append(zh_text.format(*m.groups()))
                break
        else:
            out.append(part)
    return "；".join(out)
