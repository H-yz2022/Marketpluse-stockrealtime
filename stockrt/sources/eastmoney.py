"""East Money - optional enhancers (intermittently blocked outside mainland China).

East Money has the richest fields but its push2/push2his hosts throttle
aggressively (connection resets, 502s, redirects to push2delay). Nothing in
the app depends on it alone: each function here has a fallback upstream.

* push2 .../ulist.np/get                main-fund net inflow for a batch of symbols
* push2 .../stock/fflow/kline/get       today's cumulative money flow per minute
* fundf10.eastmoney.com/jjfl_<code>     fund fee schedule (management / custody / sales)
* fundmobapi .../FundBaseTypeInformation fund profile incl. net assets
* np-anotice-stock .../security/ann     company announcements (titles + dates)
"""

from __future__ import annotations

import re

import pandas as pd

from .. import http
from ..symbols import bare, em_secid, from_em_secid, market

PUSH_HOSTS = ["https://82.push2.eastmoney.com", "https://push2.eastmoney.com"]
HEADERS = {"Referer": "https://quote.eastmoney.com/"}


def _push(path: str, params: dict) -> dict:
    last: Exception | None = None
    for host in PUSH_HOSTS:
        try:
            j = http.get(host + path, params=params, headers=HEADERS, timeout=6,
                         fast_fail=True, cooldown=600).json()
            if j.get("data") is not None:
                return j["data"]
            last = ValueError("empty data")
        except (http.SourceUnavailable, ValueError) as e:
            last = e
    raise http.SourceUnavailable(f"eastmoney {path}: {last}")


def main_flow_batch(codes: list[str]) -> pd.DataFrame:
    """Today's main-fund net inflow (CNY/HKD) for up to a few hundred symbols."""
    rows = []
    for i in range(0, len(codes), 100):
        chunk = codes[i:i + 100]
        data = _push("/api/qt/ulist.np/get", {
            "fltt": 2, "secids": ",".join(em_secid(c) for c in chunk),
            "fields": "f12,f13,f14,f62,f184,f66,f72,f78,f84",
        })
        for d in data.get("diff") or []:
            code = from_em_secid(f"{d['f13']}.{d['f12']}")
            num = lambda k: d.get(k) if isinstance(d.get(k), (int, float)) else None  # noqa: E731
            rows.append({
                "code": code, "main_net": num("f62"), "main_net_ratio": num("f184"),
                "xl_net": num("f66"), "l_net": num("f72"), "retail_net": (num("f78") or 0) + (num("f84") or 0),
                "source": "eastmoney",
            })
    return pd.DataFrame(rows)


def intraday_flow(code: str) -> pd.DataFrame:
    """Cumulative money flow by order size for each minute of the latest session."""
    data = _push("/api/qt/stock/fflow/kline/get", {
        "lmt": 0, "klt": 1, "secid": em_secid(code),
        "fields1": "f1,f2,f3,f7", "fields2": "f51,f52,f53,f54,f55,f56,f57",
    })
    recs = []
    for k in data.get("klines") or []:
        p = k.split(",")
        recs.append({"datetime": pd.Timestamp(p[0]), "main_net": float(p[1]), "small_net": float(p[2]),
                     "medium_net": float(p[3]), "large_net": float(p[4]), "xl_net": float(p[5])})
    return pd.DataFrame(recs)


def fund_fees(code: str) -> dict:
    """Annual fee rates (percent) from the fund's F10 fee page."""
    html = http.get(f"https://fundf10.eastmoney.com/jjfl_{bare(code)}.html", timeout=15).text
    out: dict[str, float | None] = {}
    for label, key in (("管理费率", "management"), ("托管费率", "custody"), ("销售服务费率", "sales_service")):
        m = re.search(label + r"</td><td[^>]*>([\d.]+)%", html)
        out[key] = float(m.group(1)) if m else None
    known = [v for v in out.values() if v is not None]
    out["total"] = round(sum(known), 4) if known else None
    return out


def fund_profile(code: str) -> dict:
    j = http.get("https://fundmobapi.eastmoney.com/FundMApi/FundBaseTypeInformation.ashx",
                 params={"FCODE": bare(code), "deviceid": "Wap", "plat": "Wap", "product": "EFund",
                         "version": "2.0.0"}, timeout=15).json()
    d = j.get("Datas") or {}
    net_assets = d.get("ENDNAV")
    return {
        "name": d.get("SHORTNAME"), "manager": d.get("JJGS"), "nav": d.get("DWJZ"), "nav_date": d.get("FSRQ"),
        "net_assets": float(net_assets) if net_assets not in (None, "", "--") else None,
        "inception": (d.get("ISSEDATE") or "")[:10] or None,
    }


def announcements(code: str, n: int = 30) -> pd.DataFrame:
    """Latest company announcements (title, date, category)."""
    ann_type = "H" if market(code) == "HK" else "A"
    j = http.get("https://np-anotice-stock.eastmoney.com/api/security/ann", params={
        "sr": -1, "page_size": n, "page_index": 1, "ann_type": ann_type,
        "client_source": "web", "stock_list": bare(code),
    }, timeout=15).json()
    rows = []
    for a in (j.get("data") or {}).get("list") or []:
        cols = a.get("columns") or [{}]
        art = a.get("art_code") or ""
        rows.append({
            "date": (a.get("notice_date") or "")[:10], "title": a.get("title_ch") or a.get("title"),
            "category": cols[0].get("column_name"),
            # Announcement page on East Money, and the original filing as PDF.
            "url": f"https://data.eastmoney.com/notices/detail/{bare(code)}/{art}.html" if art else None,
            "pdf": f"https://pdf.dfcfw.com/pdf/H2_{art}_1.pdf" if art else None,
        })
    return pd.DataFrame(rows, columns=["date", "title", "category", "url", "pdf"])
