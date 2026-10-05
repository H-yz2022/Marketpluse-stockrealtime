"""Symbol normalisation.

Canonical form is Tencent-style, lower-case market prefix + exchange code:

    sh600519  Shanghai stock / fund / index (sh000300 = CSI 300)
    sz000858  Shenzhen stock / fund / index (sz399006 = ChiNext)
    bj920000  Beijing Stock Exchange
    hk00700   Hong Kong (5-digit, zero padded); hkHSI = Hang Seng Index

Every provider wants a different spelling; the helpers below convert.
"""

from __future__ import annotations

import re

_HK_INDEXES = {"HSI", "HSCEI", "HSTECH", "HSCCI"}


def normalize(raw: str) -> str:
    """Turn user input like '600519', '600519.SH', '0700.HK', 'hk700' into canonical form."""
    s = raw.strip()
    if not s:
        raise ValueError("empty symbol")
    up = s.upper()

    m = re.fullmatch(r"(SH|SZ|BJ)(\d{6})", up)
    if m:
        return m.group(1).lower() + m.group(2)
    m = re.fullmatch(r"(\d{6})\.(SH|SS|SZ|BJ)", up)
    if m:
        ex = {"SS": "sh"}.get(m.group(2), m.group(2).lower())
        return ex + m.group(1)
    m = re.fullmatch(r"HK(\d{1,5})", up)
    if m:
        return "hk" + m.group(1).zfill(5)
    m = re.fullmatch(r"(\d{1,5})\.HK", up)
    if m:
        return "hk" + m.group(1).zfill(5)
    m = re.fullmatch(r"HK([A-Z]+)", up)
    if m and m.group(1) in _HK_INDEXES:
        return "hk" + m.group(1)
    if up in _HK_INDEXES:
        return "hk" + up
    if re.fullmatch(r"\d{6}", up):
        return _a_share_prefix(up) + up
    if re.fullmatch(r"\d{1,5}", up):
        return "hk" + up.zfill(5)
    raise ValueError(f"unrecognised symbol: {raw!r}")


def _a_share_prefix(code: str) -> str:
    """Exchange for a bare 6-digit code, assuming a stock or fund (not an index)."""
    if code.startswith(("92", "8", "4")):
        return "bj"
    if code.startswith(("6", "9", "5")):  # 6 = SH stocks, 9 = SH B-shares, 5 = SH funds/ETFs
        return "sh"
    return "sz"  # 0/2/3 = SZ stocks, 1 = SZ funds/ETFs (159xxx)


def market(code: str) -> str:
    """'CN' for mainland exchanges, 'HK' for Hong Kong."""
    return "HK" if code.startswith("hk") else "CN"


def bare(code: str) -> str:
    """Exchange code without the market prefix: sh600519 -> 600519, hk00700 -> 00700."""
    return code[2:]


def is_index(code: str) -> bool:
    if code.startswith("hk"):
        return not code[2:].isdigit()
    return (code.startswith("sh000") or code.startswith("sz399")) and len(code) == 8


def is_fund(code: str) -> bool:
    """Exchange-traded funds (ETFs/LOFs) on the mainland exchanges."""
    return (code.startswith("sh5") or code.startswith("sz15") or code.startswith("sz16")) and len(code) == 8


def em_secid(code: str) -> str:
    """East Money 'secid': 1.600519 (SH), 0.000858 (SZ/BJ), 116.00700 (HK), 100.HSI (HK index)."""
    if code.startswith("sh"):
        return "1." + code[2:]
    if code.startswith(("sz", "bj")):
        return "0." + code[2:]
    if code.startswith("hk"):
        return ("100." if is_index(code) else "116.") + code[2:]
    raise ValueError(code)


def from_em_secid(secid: str) -> str:
    mkt, num = secid.split(".", 1)
    if mkt == "1":
        return "sh" + num
    if mkt == "0":
        return ("bj" if num.startswith(("92", "8", "4")) else "sz") + num
    return "hk" + num


def volume_unit(code: str) -> float:
    """Shares per volume unit in Tencent feeds: HK and STAR Market report shares,
    every other mainland security reports 100-share lots (verified against amount / price)."""
    if code.startswith("hk") or code.startswith(("sh688", "sh689")):
        return 1.0
    return 100.0


def is_st(name: str) -> bool:
    """Special-treatment (ST / *ST) stocks - flagged for financial distress."""
    return "ST" in name.upper().replace(" ", "")


def board(code: str) -> str:
    """Listing board, used by screener filters."""
    if code.startswith("hk"):
        return "HK"
    num = code[2:]
    if code.startswith("bj"):
        return "BSE"
    if num.startswith("688") or num.startswith("689"):
        return "STAR"
    if num.startswith("30"):
        return "ChiNext"
    if is_fund(code):
        return "Fund"
    if is_index(code):
        return "Index"
    return "Main"
