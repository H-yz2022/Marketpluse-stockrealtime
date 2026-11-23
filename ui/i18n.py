"""One language at a time: 中文 (the default) or English.

    t("中文", "English")      the text in the visitor's language
    bi("中文 · English")      one side of an older bilingual string

The language and whether the page is shown inside the desktop app come from the address once per visit
(`?lang=en`, `?app=desktop`). Without `lang` in the address, the first language follows the visitor's own
computer (see detect): the browser's language, then its time zone. The switch at the top right of every page
changes the language and keeps it in the address, so reloads, bookmarks and the desktop app keep it too. Text made
by the data layer (market status, the session note, rule names, a run's notes) is in English there; the helpers
below give its Chinese.
"""

from __future__ import annotations

import re
from datetime import date

import pandas as pd
import streamlit as st

LANGS = {"zh": "中文", "en": "English"}
_CJK = re.compile(r"[㐀-鿿]")
WEEKDAYS = "一二三四五六日"
# Time zones of mainland China, Hong Kong, Macau and Taiwan (current names and old aliases).
ZH_ZONES = {"Asia/Shanghai", "Asia/Chongqing", "Asia/Chungking", "Asia/Harbin", "Asia/Urumqi", "Asia/Kashgar",
            "Asia/Hong_Kong", "Asia/Macau", "Asia/Macao", "Asia/Taipei", "PRC", "Hongkong", "ROC"}


def detect(locale: str | None, timezone: str | None) -> str:
    """The first language for a visitor who hasn't chosen one, from their own computer - nothing is looked up
    online. The system (browser) language decides when it is Chinese or English; otherwise the time zone does
    (China, Hong Kong, Macau, Taiwan: 中文; elsewhere: English); with neither known, 中文."""
    loc = (locale or "").strip().lower()
    if loc.startswith("zh"):
        return "zh"
    if loc.startswith("en"):
        return "en"
    if timezone in ZH_ZONES:
        return "zh"
    return "en" if loc or timezone else "zh"


def _detected() -> str:
    try:
        return detect(st.context.locale, st.context.timezone)
    except Exception:  # noqa: BLE001 - no browser information (tests, bare mode)
        return "zh"


def init() -> None:
    """Once per visit: language and desktop mode from the address; afterwards keep the language in the address."""
    qp = st.query_params
    if st.session_state.get("lang") not in LANGS:
        st.session_state.lang = qp.get("lang") if qp.get("lang") in LANGS else _detected()
    if "desktop" not in st.session_state:
        st.session_state.desktop = qp.get("app") == "desktop"
    if qp.get("lang") != st.session_state.lang:
        qp["lang"] = st.session_state.lang
    # Appearance, kept in the address like the language (the desktop app remembers it from there).
    if st.session_state.get("font_size") not in FONT_SIZES:
        st.session_state.font_size = qp.get("fs") if qp.get("fs") in FONT_SIZES else "m"
    if st.session_state.get("theme_pref") not in THEMES:
        st.session_state.theme_pref = qp.get("theme") if qp.get("theme") in THEMES else "system"
    if qp.get("fs") != st.session_state.font_size:
        qp["fs"] = st.session_state.font_size
    if qp.get("theme") != st.session_state.theme_pref:
        qp["theme"] = st.session_state.theme_pref


FONT_SIZES = {"m": ("标准", "Standard", 16), "l": ("大", "Large", 18), "xl": ("特大", "Extra large", 20)}
THEMES = {"system": ("跟随系统", "Follow Windows"), "light": ("浅色", "Light"), "dark": ("深色", "Dark")}


def appearance() -> None:
    """Applies the chosen text size, and a little more contrast for the grey help text everywhere."""
    px = FONT_SIZES.get(st.session_state.get("font_size", "m"), FONT_SIZES["m"])[2]
    st.html(f"""<style>
html {{ font-size: {px}px; }}
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p {{ opacity: 1; filter: none; }}
[data-testid="stCaptionContainer"] {{ color: color-mix(in srgb, currentColor 82%, transparent); }}
</style>""")


def lang() -> str:
    try:
        value = st.session_state.get("lang", "zh")
    except Exception:  # noqa: BLE001 - outside a page run (tests, scripts)
        return "zh"
    return value if value in LANGS else "zh"           # never a missing or odd value


def zh() -> bool:
    return lang() == "zh"


def desktop() -> bool:
    """Shown inside the desktop app (website-only parts are left out)."""
    try:
        return bool(st.session_state.get("desktop", False))
    except Exception:  # noqa: BLE001
        return False


def t(zh_text: str, en_text: str) -> str:
    return zh_text if zh() else en_text


def bi(text: str) -> str:
    """The visitor's side of "中文 · English" (either order); text without both sides is returned unchanged."""
    parts = text.split(" · ")
    if len(parts) < 2:
        return text
    for i in range(1, len(parts)):
        left, right = " · ".join(parts[:i]), " · ".join(parts[i:])
        lz, rz = bool(_CJK.search(left)), bool(_CJK.search(right))
        if lz and not rz:
            return left if zh() else right
        if rz and not lz:
            return right if zh() else left
    return text


# --------------------------------------------------------------------------- dates

def day(d, long: bool = False) -> str:
    """Fri 13 Nov / 11月13日 周五 (long: with the year)."""
    d = pd.Timestamp(d)
    if zh():
        return (f"{d.year}年" if long else "") + f"{d.month}月{d.day}日 周{WEEKDAYS[d.weekday()]}"
    return d.strftime("%a %d %b %Y" if long else "%a %d %b")


def wd(d) -> str:
    """Fri 13 / 周五 13日."""
    d = pd.Timestamp(d)
    return f"周{WEEKDAYS[d.weekday()]} {d.day}日" if zh() else d.strftime("%a %d")


# --------------------------------------------------------------------------- the data layer's text

def duration(minutes: int) -> str:
    h, m = divmod(max(int(minutes), 0), 60)
    if zh():
        return f"{h}小时{m}分钟" if h else f"{m}分钟"
    return f"{h} h {m} min" if h else f"{m} min"


PHASES = {"morning": "上午", "afternoon": "下午"}


def status_label(s) -> str:
    """calendar.SessionStatus.label in the visitor's language."""
    if not zh():
        return s.label
    nxt = f"{day(s.next_session)} 09:30" if s.next_session else None
    if s.phase in ("weekend", "holiday"):
        why = "周末" if s.phase == "weekend" else (s.holiday or "节假日")
        return f"休市 · {why}" + (f" · {nxt} 开盘" if nxt else "")
    if s.phase == "before open":
        return f"今日 09:30 开盘 · 还有{duration(s.to_open_min)}"
    if s.phase == "pre-open":
        return f"集合竞价中 · {duration(s.to_open_min)}后开盘"
    if s.phase in ("morning", "afternoon"):
        return f"交易中 · {PHASES[s.phase]}{'（半日市）' if s.half_day else ''} · 距收盘{duration(s.to_close_min)}"
    if s.phase == "lunch":
        return "午间休市 · 13:00 恢复交易"
    if s.phase == "closing auction":
        return "收盘集合竞价"
    return "今日已收盘" + (f" · 下一交易日 {nxt}" if nxt else "")


def session_note(d: date, spec) -> str:
    """checkpoint.session_note in the visitor's language."""
    from stockrt import checkpoint
    from stockrt.calendar import now_bj, status

    if not zh():
        return checkpoint.session_note(d, spec)
    if d == now_bj().date():
        return ""
    s = status(spec.mkt)
    if s.phase in ("holiday", "weekend"):
        subject = "A股" if spec.mkt == "CN" else "港股"
        return f"{subject}{status_label(s)}。显示最近一个交易日：{day(d)}。"
    if s.phase in ("before open", "pre-open"):
        return f"今日尚未开盘（北京时间 09:30 开盘）。显示最近一个交易日：{day(d)}。"
    return f"显示最近一个交易日：{day(d)}。"


def plan_reason(x: dict, spec) -> str:
    """A checkpoint card's "not yet" line."""
    from stockrt import checkpoint

    if not zh():
        return x["reason"]
    cp = x["checkpoint"]
    left = checkpoint._until(cp)
    left = left.replace(" h ", "小时").replace(" min", "分钟").replace("h", "小时")
    return f"还有{left}"                     # the card already shows the time and a "未到" badge


RULES_ZH = {
    "rule_not_st": "非ST", "rule_pct": "涨幅 {}", "rule_vr": "量比 > {}", "rule_turnover": "换手率 {}",
    "rule_cross": "5日线上穿10日线", "rule_ma_rising": "均线向上", "rule_vol_price": "量价齐升",
    "rule_inflow": "主力资金流入", "rule_outperform": "强于{}", "rule_float_cap": "流通市值 {}",
}
INDEX_ZH = {"CSI 300": "沪深300", "Hang Seng Index": "恒生指数", "HSI": "恒指"}


def rule_labels(p, spec) -> list[tuple[str, str]]:
    """checkpoint.rule_labels in the visitor's language."""
    from stockrt import checkpoint

    en = checkpoint.rule_labels(p, spec)
    if not zh():
        return en

    def rng(lo, hi, unit=""):
        if lo is not None and hi is not None:
            return f"{lo:g}–{hi:g}{unit}"
        return f"≥ {lo:g}{unit}" if lo is not None else (f"≤ {hi:g}{unit}" if hi is not None else "不限")

    cap = "亿" if spec.mkt == "CN" else "亿港元"
    lo = p.float_mcap_min_bn * 10 if p.float_mcap_min_bn is not None else None
    hi = p.float_mcap_max_bn * 10 if p.float_mcap_max_bn is not None else None
    fill = {"rule_pct": rng(p.pct_min, p.pct_max, "%"),
            "rule_vr": f"{p.volume_ratio_min:g}" if p.volume_ratio_min is not None else "",
            "rule_turnover": rng(p.turnover_min, p.turnover_max, "%"),
            "rule_outperform": INDEX_ZH.get(spec.index_name, spec.index_name),
            "rule_float_cap": rng(lo, hi, cap)}
    return [(k, RULES_ZH[k].format(fill.get(k, "")).strip()) for k, _ in en]


def missed(text: str, p, spec) -> str:
    """A "missed" column built from English rule names (尾盘选股), in the visitor's language."""
    if not zh() or not isinstance(text, str) or not text:
        return text
    from stockrt import checkpoint

    names = {en: zh_ for (_, en), (_, zh_) in zip(checkpoint.rule_labels(p, spec), rule_labels(p, spec))}
    return "，".join(names.get(x.strip(), x.strip()) for x in text.split(","))


NOTES = [
    (r"(\d+) stocks couldn't meet two or more of the history rules at any price they traded at today, so they "
     r"weren't rebuilt\.", "{0} 只股票在今日任何价位都至少有两条历史条件无法满足，未重建。"),
    (r"Main-fund flow at (.+) isn't available for this session from your network; the session's end-of-day net "
     r"inflow is used instead \(confidence: medium\)\.", "{0} 的主力资金数据无法获取，改用当日收盘净流入（可信度：中）。"),
    (r"No main-fund flow source is reachable right now, so 'main funds flowing in' was not checked "
     r"\(confidence: low\)\.", "暂时无法获取主力资金数据，未检查“主力资金流入”（可信度：低）。"),
    (r"No stock passed the first screen on (\S+) .*", "{0} 没有股票通过第一轮筛选（涨幅、换手率与市值），因此无结果。"),
    (r"(\d+) of (\d+) stocks couldn't be read and were left out\.", "{1} 只股票中有 {0} 只无法读取，已略过。"),
    (r"Rebuilt afterwards from (\S+)'s minute data and end-of-day volume book\.", "事后根据 {0} 的分时数据与收盘成交量重建。"),
    (r"Main-fund flow for (\d+) stocks is Sina's \(East Money's history didn't answer\)\.",
     "{0} 只股票的主力资金数据来自新浪（东方财富历史数据无响应）。"),
    (r"Main-fund flow unavailable for (\d+) stocks: they miss that rule\.", "{0} 只股票无主力资金数据，该条件视为未满足。"),
    (r"(.+) minute data unavailable - 'beats the market' could not be checked\.",
     "{0} 分时数据不可用，无法检查“强于大盘”。"),
    (r"Main-fund flow: (.+)\.", "主力资金数据来源：{0}。"),
    (r"10-day inflow: (.+)\.", "近10日资金流入来源：{0}。"),
    (r"Minute flow unavailable for (\d+) of (\d+) stocks\.", "{1} 只股票中有 {0} 只无分时资金数据。"),
    (r"The previous 5 sessions' volume books aren't all saved yet\.", "前5个交易日的成交量数据尚未全部保存。"),
    (r"No volume book for the previous session: the volume rule is checked from daily bars only\.",
     "缺少上一交易日的成交量数据：成交量条件仅按日线检查。"),
    (r"Institutional holdings unavailable \((.+)\); that condition can't be checked\.",
     "机构持仓数据不可用（{0}），无法检查该条件。"),
    (r"Financial reports unavailable \((.+)\); the profit condition can't be checked\.",
     "财报数据不可用（{0}），无法检查利润条件。"),
]


def note(text: str) -> str:
    """A run's note (written in English when it was computed) in the visitor's language; unknown ones unchanged."""
    if not zh() or not isinstance(text, str):
        return text
    for pat, zh_ in NOTES:
        m = re.fullmatch(pat, text.strip())
        if m:
            return zh_.format(*m.groups())
    return text


AUCTION_RULES_ZH = {
    "a_not_st": ("非ST", "ST 股涨跌幅限制为 5% 且有退市风险——是另一种玩法。"),
    "a_gap": ("高开 {gap}", "适度高开说明隔夜有买盘。高于约 7% 时回落（和追高）的风险急剧上升；低于 2% 信号偏弱。"),
    "a_not_limit": ("非一字涨停开盘", "一字涨停开盘买不进，对选股没有意义。"),
    "a_amount": ("竞价成交额 ≥ {amount} 百万元", "竞价中真正投入的资金。竞价额太小容易被操纵，参考价值低。"),
    "a_vs_yday": ("竞价量 ≥ 昨日成交量的 {vs_yday}%", "经典的竞价爆量：仅竞价就成交了昨日全天的一大部分，说明关注度异常。"),
    "a_turnover": ("竞价换手率 ≥ {turnover}%", "竞价量相对流通股本——大小盘之间可以比较。"),
    "a_cap": ("流通市值 {cap}", "中盘股随资金流动；超大盘很少高开，小微盘噪音大。"),
    "a_yday_up": ("昨日收涨", "上涨之后的延续比下跌之后的高开更可靠。"),
    "a_yday_limit": ("昨日涨停（接力）", "昨日涨停股的接力——高风险、高关注。"),
    "a_holding": ("仍维持高开", "09:30 之后：价格不低于开盘价——高开没有被抛售。"),
}


def auction_rules(active: list[tuple[str, str, str]], p) -> list[tuple[str, str]]:
    """opening.active_rules as (label, why) in the visitor's language."""
    if not zh():
        return [(lbl, why) for _, lbl, why in active]
    from stockrt.opening import _fmt_range

    fill = {"gap": _fmt_range(p.gap_min, p.gap_max, "%"), "amount": f"{p.amount_min_m or 0:g}",
            "vs_yday": f"{p.vs_yday_min_pct or 0:g}", "turnover": f"{p.turnover_min or 0:g}",
            "cap": _fmt_range(p.cap_min_bn * 10 if p.cap_min_bn is not None else None,
                              p.cap_max_bn * 10 if p.cap_max_bn is not None else None, " 亿")}
    return [(AUCTION_RULES_ZH[c][0].format(**fill), AUCTION_RULES_ZH[c][1]) for c, _, _ in active]


def close_rules(resistance: int, ma: int) -> list[tuple[str, str]]:
    """closescreen.rules as (label, reading) in the visitor's language."""
    from stockrt import closescreen

    if not zh():
        return [(bi(lbl), desc) for _, lbl, desc in closescreen.rules(resistance, ma)]
    line = {250: "年线", 120: "半年线"}.get(ma, "")
    return [("非ST", "排除 ST / *ST 股票。"),
            ("成交量翻倍", f"成交量至少为上一交易日的 {closescreen.VOLUME_MULTIPLE:g} 倍。"),
            ("突破压力位", f"收盘价高于前 {resistance} 个交易日的最高价。"),
            ("换手率＞3%", f"换手率高于流通股本的 {closescreen.TURNOVER_MIN:g}%。"),
            ("站上牛熊线", f"收盘价高于 {ma} 日均线{'（' + line + '）' if line else ''}。")]


MOODS = {"Strongly bullish": "强烈看多", "Bullish": "偏多", "Neutral": "中性", "Bearish": "偏空",
         "Strongly bearish": "强烈看空"}
BOARDS_ZH = {"Main": "主板", "ChiNext": "创业板", "STAR": "科创板", "BSE": "北交所"}
FUNNEL = [
    (r"All stocks", "全部股票"), (r"Traded today \(not suspended\)", "今日有交易（未停牌）"),
    (r"Not ST / \*ST", "非ST / *ST"), (r"Board in (.+)", "板块：{0}"), (r"Change (.+)", "涨幅 {0}"),
    (r"Volume ratio > (.+)", "量比 > {0}"), (r"Turnover (.+)", "换手率 {0}"), (r"Float cap (.+) bn", "流通市值 {0} 十亿"),
    (r"Price (.+)", "价格 {0}"), (r"Beats market \((.+)\)", "强于大盘（{0}）"),
    (r"MA5 crossed above MA10", "5日线上穿10日线"), (r"MA5 & MA10 rising", "5日线与10日线向上"),
    (r"Volume & price up", "量价齐升"), (r"Main funds net inflow", "主力资金净流入"),
]
NOTES += [
    (r"Benchmark quote unavailable - 'beats market' rule skipped\.", "基准行情不可用——跳过“强于大盘”条件。"),
    (r"(\d+) stocks passed the snapshot rules; history checked for the (\d+) with the highest turnover value\.",
     "{0} 只股票通过快照条件；只检查成交额最大的 {1} 只的历史数据。"),
    (r"Money-flow sources unavailable - 'main funds inflow' could not be checked\.",
     "资金流向数据不可用——无法检查“主力资金流入”。"),
]


def mood(label: str) -> str:
    return MOODS.get(label, label) if zh() else label


def funnel_step(name: str) -> str:
    if not zh():
        return name
    for pat, zh_text in FUNNEL:
        m = re.fullmatch(pat, name)
        if m:
            out = zh_text.format(*m.groups())
            for en, zh_board in BOARDS_ZH.items():
                out = out.replace(en, zh_board) if pat.startswith("Board") else out
            return out
    return name


INDEX_NAMES_ZH = {"SSE Composite": "上证指数", "SZSE Component": "深证成指", "CSI 300": "沪深300", "SSE 50": "上证50",
                  "CSI 500": "中证500", "CSI 1000": "中证1000", "ChiNext": "创业板指", "STAR 50": "科创50",
                  "Hang Seng Index": "恒生指数", "HS China Enterprises": "恒生国企指数", "Hang Seng TECH": "恒生科技指数"}
GROUPS_ZH = {"Baijiu (white liquor)": "白酒", "Telecom": "通信", "Banks": "银行", "HK tech & platforms": "港股科技与平台",
             "HK financials": "港股金融", "New energy": "新能源"}


def index_name(name: str) -> str:
    return INDEX_NAMES_ZH.get(name, name) if zh() else name


def group_name(name: str) -> str:
    return GROUPS_ZH.get(name, name) if zh() else name

NOTES += [
    (r"3- and 5-day money flow: (.+) \(of ([\d,]+) stocks above MA20\)\.",
     "近3日 / 近5日主力资金来源：{0}（共查询站上20日均线的 {1} 只股票）。"),
    (r"Money flow unknown for ([\d,]+) stocks \(no answer from East Money or Sina\)\.",
     "{0} 只股票的资金流向未知（东方财富与新浪均无响应）。"),
]
