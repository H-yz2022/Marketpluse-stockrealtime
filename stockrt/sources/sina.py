"""Sina Finance - market universe lists, price-adjustment factors and money flow.

* Market_Center.getHQNodeData / getHKStockData   every listed A-share / HK stock, 100 per page
* realstock/company/<code>/qfq.js                A-share forward-adjust factors (ratio based)
* stock/hkstock/<code>/hfq.js                    HK backward-adjust factor + cumulative cash
* MoneyFlow.ssi_ssfx_flzjtj                      one stock's money flow today by order size
* MoneyFlow.ssl_bkzj_ssggzj                      market-wide money-flow ranking (65 pages)

Sina rejects requests without a finance.sina.com.cn Referer and bans IPs that
hammer it, so page fetches use few workers and results are cached upstream.
"""

from __future__ import annotations

import json
import re
from datetime import date

import pandas as pd

from .. import http, sample
from ..symbols import bare

HEADERS = {"Referer": "https://finance.sina.com.cn"}
API = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
PAGE = 100


def _json(url: str, params: dict | None = None):
    r = http.get(url, params=params, headers=HEADERS, timeout=20)
    txt = r.text.strip()
    if txt in ("null", ""):
        return None
    return json.loads(txt)


# --------------------------------------------------------------------------- universe

def _count(method: str, node: str) -> int:
    v = _json(API + method, {"node": node})
    return int(v)


def _pages(method: str, node: str, total: int, page_size: int = PAGE, workers: int = 4) -> list[dict]:
    """Fetch every page. The advertised count can be low (HK reports ~2,500 but has more),
    so keep requesting past it until a page comes back short or empty."""
    def fetch(pages: range) -> list[list[dict]]:
        params = [{"page": p, "num": page_size, "sort": "symbol", "asc": 1, "node": node} for p in pages]
        out = []
        for res in http.pmap(lambda p: _json(API + method, p), params, workers=workers):
            if isinstance(res, Exception):
                raise res
            out.append(res or [])
        return out

    last = total // page_size + 1
    batches = fetch(range(1, last + 1))
    while batches and len(batches[-1]) == page_size:
        more = fetch(range(last + 1, last + 1 + workers))
        batches.extend(more)
        last += workers
        if any(len(b) < page_size for b in more):
            break
    return [row for b in batches for row in b]


@sample.replay("sina.universe_cn")
def universe_cn() -> pd.DataFrame:
    """Every A-share listed on Shanghai, Shenzhen and Beijing (code, name, board data)."""
    total = _count("Market_Center.getHQNodeStockCount", "hs_a")
    rows = _pages("Market_Center.getHQNodeData", "hs_a", total)
    df = pd.DataFrame(rows)
    out = pd.DataFrame({
        "code": df["symbol"],
        "name": df["name"].str.replace(" ", "", regex=False),
        "price": pd.to_numeric(df["trade"], errors="coerce"),
        "pct_change": pd.to_numeric(df["changepercent"], errors="coerce"),
        "turnover_rate": pd.to_numeric(df["turnoverratio"], errors="coerce"),
        "pe_ttm": pd.to_numeric(df["per"], errors="coerce"),
        "pb": pd.to_numeric(df["pb"], errors="coerce"),
        "total_mcap": pd.to_numeric(df["mktcap"], errors="coerce") * 1e4,
        "float_mcap": pd.to_numeric(df["nmc"], errors="coerce") * 1e4,
    })
    return out.drop_duplicates("code").reset_index(drop=True)


@sample.replay("sina.universe_hk")
def universe_hk() -> pd.DataFrame:
    total = _count("Market_Center.getHKStockCount", "qbgg_hk")
    # The HK endpoint silently caps pages at 60 rows whatever `num` says.
    rows = _pages("Market_Center.getHKStockData", "qbgg_hk", total, page_size=60)
    df = pd.DataFrame(rows)
    df = df[df["symbol"].astype(str).str.zfill(5).str[0] != "8"]  # drop RMB-counter duplicates (8xxxx)
    out = pd.DataFrame({
        "code": "hk" + df["symbol"].astype(str).str.zfill(5),
        "name": df["name"].str.strip(),
        "name_en": df.get("engname", ""),
        "price": pd.to_numeric(df["lasttrade"], errors="coerce"),
        "pct_change": pd.to_numeric(df["changepercent"], errors="coerce"),
    })
    return out.drop_duplicates("code").reset_index(drop=True)


# --------------------------------------------------------------------------- adjustment factors

def _js_object(text: str) -> dict:
    m = re.search(r"=\s*(\{.*?\})\s*(?:/\*|;|$)", text, re.S)
    if not m:
        raise ValueError("no JSON object in factor file")
    return json.loads(m.group(1))


@sample.replay("sina.factor_segments")
def factor_segments(code: str) -> list[tuple[date, float, float]]:
    """Affine segments (start_date, a, b): adjusted = a * raw + b from start_date onward.

    A-share stocks: qfq factor f, adjusted = raw / f            -> (1/f, 0)
    A-share funds:  qfq split s and cumulative cash u           -> (1/s, -u/s)
    Hong Kong:      hfq factor f and cumulative cash c          -> (f, c)
    Returns [] when the security has no corporate actions (or is an index).
    """
    if code.startswith("hk"):
        url = f"https://finance.sina.com.cn/stock/hkstock/{bare(code)}/hfq.js"
    else:
        url = f"https://finance.sina.com.cn/realstock/company/{code}/qfq.js"
    try:
        obj = _js_object(http.get(url, headers=HEADERS, timeout=15).text)
    except http.NotFound:
        return []
    segs = []
    for row in obj.get("data", []):
        d = date.fromisoformat(row["d"])
        if code.startswith("hk"):
            segs.append((d, float(row["f"]), float(row.get("c", 0) or 0)))
        elif "s" in row or "u" in row:
            s = float(row.get("s", 1) or 1) * float(row.get("f", 1) or 1)
            u = float(row.get("u", 0) or 0)
            segs.append((d, 1.0 / s, -u / s))
        else:
            segs.append((d, 1.0 / float(row["f"]), 0.0))
    return sorted(segs)


# --------------------------------------------------------------------------- money flow

@sample.replay("sina.money_flow")
def money_flow(code: str) -> dict:
    """Today's money flow for one A-share, split by order size (CNY).

    Sina buckets: r0 = extra-large orders, r1 = large, r2 = medium, r3 = small.
    'Main funds' (主力) is the usual r0 + r1 net inflow.
    """
    d = _json(API + "MoneyFlow.ssi_ssfx_flzjtj", {"daima": code})
    if not d:
        raise ValueError(f"no money-flow data for {code}")
    g = lambda k: float(d.get(k) or 0)  # noqa: E731
    main_in = g("r0_in") + g("r1_in")
    main_out = g("r0_out") + g("r1_out")
    retail_net = (g("r2_in") - g("r2_out")) + (g("r3_in") - g("r3_out"))
    total = sum(g(f"r{i}_in") + g(f"r{i}_out") for i in range(4))
    return {
        "code": code,
        "main_net": main_in - main_out,
        "main_in": main_in,
        "main_out": main_out,
        "xl_net": g("r0_in") - g("r0_out"),
        "l_net": g("r1_in") - g("r1_out"),
        "retail_net": retail_net,
        "main_net_ratio": 100 * (main_in - main_out) / total if total else None,
        "source": "sina",
    }


@sample.replay("sina.money_flow_rank")
def money_flow_rank(workers: int = 4) -> pd.DataFrame:
    """Main-fund net inflow for every A-share and fund (slow: ~65 pages)."""
    method = API + "MoneyFlow.ssl_bkzj_ssggzj"
    rows: list[dict] = []
    page, step = 1, workers
    while True:
        batch = [{"page": p, "num": PAGE, "sort": "r0_net", "asc": 0, "bankuai": "", "shichang": ""}
                 for p in range(page, page + step)]
        results = http.pmap(lambda p: _json(method, p), batch, workers=workers)
        done = False
        for res in results:
            if isinstance(res, Exception):
                raise res
            if not res:
                done = True
                continue
            rows.extend(res)
            if len(res) < PAGE:
                done = True
        if done:
            break
        page += step
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    amount = pd.to_numeric(df["amount"], errors="coerce")
    out = pd.DataFrame({
        "code": df["symbol"],
        "main_net": pd.to_numeric(df["r0_net"], errors="coerce"),
        "retail_net": pd.to_numeric(df["r3_net"], errors="coerce"),
        "net_amount": pd.to_numeric(df["netamount"], errors="coerce"),
        "amount": amount,
    })
    out["main_net_ratio"] = 100 * out["main_net"] / amount.where(amount > 0)
    return out.drop_duplicates("code").reset_index(drop=True)
