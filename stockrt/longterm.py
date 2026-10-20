"""长线 - the after-close long-term screen, a reference for building positions the next day.

    长线。收盘的数据，给明天建仓做参考：市盈率小于20，ROE大于5%，股价小于10元大于5元，换手率大于4%，
    机构持仓：显示有机构投资者持有该股票，近10天内资金流入，近3交易日5、10、20日均线最近发生过粘合。

How each condition is read (every threshold can be changed on the page):

    市盈率 < 20          trailing P/E (TTM) between 0 and 20 - loss makers excluded
    ROE > 5%            trailing-12-month ROE = P/B ÷ P/E (both from the closing quote: earnings / book)
    5 < 股价 < 10        the closing price, CNY
    换手率 > 4%          the session's turnover
    机构持仓             at least one institution (funds, insurers, QFII, social security...) in the latest
                        reported quarter (East Money's 机构持仓 data)
    近10天资金流入        main-fund net inflow over the last 10 sessions above zero
    近3日均线粘合         on one of the last 3 sessions, the 5-, 10- and 20-day moving averages lay within 2% of
                        each other (spread = highest minus lowest, as % of the close)

Run once a day after the close on that session's data. A broad shortlist (P/E up to 40, ROE from 2%,
price 3-15, turnover from 2%) is enriched and kept, so the thresholds re-filter instantly.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from . import archive, http, sample, storage
from .symbols import em_secid, from_em_secid, is_st

QUERY_ZH = ("长线。收盘的数据，给明天建仓做参考：市盈率小于20，ROE大于5%，股价小于10元大于5元，换手率大于4%，"
            "机构持仓：显示有机构投资者持有该股票，近10天内资金流入，近3交易日5、10、20日均线最近发生过粘合。")


@dataclass
class Params:
    pe_max: float = 20.0
    roe_min: float = 5.0
    price_min: float = 5.0
    price_max: float = 10.0
    turnover_min: float = 4.0
    institutions_min: int = 1
    inflow10_min_m: float = 0.0         # CNY millions over 10 sessions
    ma_spread_max: float = 2.0          # % of the close
    ma_within: int = 3                  # sessions
    use_institutions: bool = True
    use_inflow: bool = True
    use_ma: bool = True


def shortlist(snap: pd.DataFrame) -> pd.DataFrame:
    d = snap[(snap["price"] > 0) & (snap["volume"].fillna(0) > 0)].copy()
    d["is_st"] = d["name"].fillna("").map(is_st)
    d["roe"] = 100 * d["pb"] / d["pe_ttm"]
    m = ~d["is_st"] & (d["pe_ttm"] > 0) & (d["pe_ttm"] <= 40) & (d["roe"] >= 2) & d["price"].between(3, 15) & \
        (d["turnover_rate"] >= 2)
    return d[m.fillna(False)]


# --------------------------------------------------------------------------- enrichment

def institutions(day: date) -> pd.DataFrame:
    """Number of institutions holding each stock in its latest reported quarter (East Money, whole market)."""
    key = f"orghold_{day.isoformat()}"
    cached = storage.load_frame("longterm", key)
    if cached is not None:
        return cached
    from .sources.eastmoney import HEADERS

    def quarter(qd: str) -> pd.DataFrame:
        rows, page = [], 1
        while True:
            j = http.get("https://datacenter-web.eastmoney.com/api/data/v1/get", params={
                "reportName": "RPT_MAIN_ORGHOLD", "columns": "SECURITY_CODE,REPORT_DATE,HOULD_NUM,FREESHARES_RATIO",
                "filter": f"(REPORT_DATE='{qd}')(ORG_TYPE=\"00\")", "pageSize": 500, "pageNumber": page},
                headers=HEADERS, timeout=15).json()
            res = j.get("result") or {}
            rows += res.get("data") or []
            if page >= (res.get("pages") or 0):
                break
            page += 1
        return pd.DataFrame(rows)

    parts = []
    for qd in _quarters(day):
        q = quarter(qd)
        if not q.empty:
            parts.append(q)
        if sum(len(p) for p in parts) > 4000:
            break
    if not parts:
        return pd.DataFrame(columns=["num6", "institutions", "inst_float_pct", "inst_report"])
    d = pd.concat(parts).sort_values("REPORT_DATE", ascending=False).drop_duplicates("SECURITY_CODE")
    out = pd.DataFrame({"num6": d["SECURITY_CODE"].astype(str), "institutions": d["HOULD_NUM"].astype(float),
                        "inst_float_pct": d["FREESHARES_RATIO"].astype(float),
                        "inst_report": d["REPORT_DATE"].str[:10]})
    storage.save_frame("longterm", key, out)
    return out


def _quarters(day: date) -> list[str]:
    """The two most recent quarter ends before `day` (the newer one is often still being reported)."""
    ends = []
    y = day.year
    for yy in (y, y - 1):
        for m, dd in ((12, 31), (9, 30), (6, 30), (3, 31)):
            q = date(yy, m, dd)
            if q < day:
                ends.append(q)
    return [q.isoformat() for q in sorted(ends, reverse=True)[:2]]


def inflow10(codes: list[str]) -> tuple[dict[str, float], str]:
    """Main-fund net inflow over the last 10 sessions: East Money's batch figure (主力, 10日), else Sina's daily
    history (extra-large orders) summed."""
    from .sources import eastmoney, sina

    out: dict[str, float] = {}
    try:
        for i in range(0, len(codes), 100):
            chunk = codes[i:i + 100]
            data = eastmoney._push("/api/qt/ulist.np/get", {
                "fltt": 2, "secids": ",".join(em_secid(c) for c in chunk), "fields": "f12,f13,f174"})
            for d in data.get("diff") or []:
                v = d.get("f174")
                if isinstance(v, (int, float)):
                    out[from_em_secid(f"{d['f13']}.{d['f12']}")] = float(v)
        if len(out) >= 0.8 * len(codes):
            return out, "East Money 主力 (10日)"
    except http.SourceUnavailable:
        pass
    out = {}
    for c, r in zip(codes, http.pmap(lambda c: sina.daily_flow(c, 10), codes, workers=8)):
        if isinstance(r, pd.DataFrame) and not r.empty:
            out[c] = float(r["main_net"].head(10).sum())
    return out, "Sina 特大单 (10 days)"


def ma_spread(code: str, day: date, close: float, prev_close: float | None) -> dict:
    """MA5 / MA10 / MA20 spread (% of the close) on each of the last 3 sessions up to `day` (today's close
    included): history from the panel when it covers the stock, else downloaded."""
    from . import panel
    from .sources import tencent

    h = panel.history(code, day, prev_close, min_rows=22)
    if h is None:
        h = tencent.daily(code, start=day - timedelta(days=60), end=day, fq="qfq")
        h = h[h["date"] < pd.Timestamp(day)]
    c = pd.Series(list(h["close"]) + [close])
    out = {"code": code}
    if len(c) < 22:
        return out
    for k in range(3):                       # k sessions ago
        s = c.iloc[: len(c) - k] if k else c
        mas = [s.tail(n).mean() for n in (5, 10, 20)]
        out[f"ma_spread_{k}"] = 100 * (max(mas) - min(mas)) / float(s.iloc[-1])
    return out


def run(day: date, snap: pd.DataFrame, progress: Callable[[float, str], None] | None = None,
        workers: int = 12) -> dict:
    """Screen the session `day` from its closing snapshot and archive it."""
    notes = []
    snap = snap[pd.to_datetime(snap["time"]).dt.date == day]
    cands = shortlist(snap)
    if progress:
        progress(0.05, f"长线 shortlist: {len(cands)} stocks")
    try:
        inst = institutions(day)
    except http.SourceUnavailable as e:
        inst = pd.DataFrame(columns=["num6", "institutions", "inst_float_pct", "inst_report"])
        notes.append(f"Institutional holdings unavailable ({e}); that condition can't be checked.")
    d = cands.assign(num6=cands["code"].str[2:]).merge(inst, on="num6", how="left")
    if progress:
        progress(0.2, "近10日资金流向 · 10-day main-fund flow")
    flows, src = inflow10(d["code"].tolist())
    d["inflow10"] = d["code"].map(flows)
    notes.append(f"10-day inflow: {src}.")
    rows = []
    recs = d[["code", "price", "prev_close"]].to_dict("records")
    for i in range(0, len(recs), 60):
        for r in http.pmap(lambda r: ma_spread(r["code"], day, r["price"], r["prev_close"]), recs[i:i + 60],
                           workers=workers):
            if isinstance(r, dict):
                rows.append(r)
        if progress:
            progress(0.3 + 0.7 * min(i + 60, len(recs)) / max(len(recs), 1),
                     f"均线粘合 · Moving averages {min(i + 60, len(recs))}/{len(recs)}")
    if rows:
        d = d.merge(pd.DataFrame(rows), on="code", how="left")
    keep = ["code", "name", "price", "pct_change", "pe_ttm", "pb", "roe", "turnover_rate", "float_mcap", "amount",
            "institutions", "inst_float_pct", "inst_report", "inflow10", "ma_spread_0", "ma_spread_1", "ma_spread_2"]
    frame = d.reindex(columns=keep)
    res = {"day": day, "frame": frame, "notes": notes, "shortlisted": len(cands)}
    save(res)
    return res


def apply(frame: pd.DataFrame, p: Params | None = None) -> pd.DataFrame:
    p = p or Params()
    d = frame.copy()
    d["r_pe"] = (d["pe_ttm"] > 0) & (d["pe_ttm"] < p.pe_max)
    d["r_roe"] = d["roe"] > p.roe_min
    d["r_price"] = (d["price"] > p.price_min) & (d["price"] < p.price_max)
    d["r_turnover"] = d["turnover_rate"] > p.turnover_min
    d["r_inst"] = (d["institutions"] >= p.institutions_min) if p.use_institutions else True
    d["r_inflow"] = (d["inflow10"] > p.inflow10_min_m * 1e6) if p.use_inflow else True
    spreads = d[[f"ma_spread_{k}" for k in range(min(max(p.ma_within, 1), 3))]]
    d["ma_spread_min"] = spreads.min(axis=1)
    d["r_ma"] = (d["ma_spread_min"] <= p.ma_spread_max) if p.use_ma else True
    cols = ["r_pe", "r_roe", "r_price", "r_turnover", "r_inst", "r_inflow", "r_ma"]
    d["rules_met"] = d[cols].fillna(False).astype(bool).sum(axis=1)
    d["passes"] = d["rules_met"] == len(cols)
    labels = {"r_pe": "市盈率", "r_roe": "ROE", "r_price": "股价", "r_turnover": "换手率", "r_inst": "机构持仓",
              "r_inflow": "近10日资金流入", "r_ma": "均线粘合"}
    d["missed"] = [", ".join(labels[c] for c, ok in zip(cols, row) if not ok)
                   for row in d[cols].fillna(False).astype(bool).itertuples(index=False)]
    return d.sort_values(["passes", "rules_met", "roe"], ascending=False).reset_index(drop=True)


# --------------------------------------------------------------------------- archive (rolling window)

def _base(day: date):
    return archive.RESULTS_DIR / "cn" / "longterm" / day.isoformat()


def save(res: dict):
    if sample.is_sample():
        return None
    import json

    base = _base(res["day"])
    base.parent.mkdir(parents=True, exist_ok=True)
    res["frame"].to_parquet(base.with_suffix(".parquet"), index=False, compression="zstd")
    base.with_suffix(".json").write_text(json.dumps({"day": res["day"].isoformat(), "notes": res["notes"],
                                                     "shortlisted": res["shortlisted"]}, ensure_ascii=False),
                                         encoding="utf-8")
    return base


def load(day: date) -> dict | None:
    import json

    base = _base(day)
    if not base.with_suffix(".parquet").exists():
        return None
    meta = json.loads(base.with_suffix(".json").read_text(encoding="utf-8")) if base.with_suffix(".json").exists() \
        else {}
    return {"day": day, "frame": pd.read_parquet(base.with_suffix(".parquet")), "notes": meta.get("notes", []),
            "shortlisted": meta.get("shortlisted")}


def days() -> list[date]:
    d = archive.RESULTS_DIR / "cn" / "longterm"
    return sorted((date.fromisoformat(f.stem) for f in d.glob("*.parquet")), reverse=True) if d.exists() else []


def prune(keep: int = archive.KEEP_SESSIONS) -> list:
    removed = []
    for d in days()[keep:]:
        for ext in (".parquet", ".json"):
            f = _base(d).with_suffix(ext)
            if f.exists():
                f.unlink()
                removed.append(f)
    return removed

