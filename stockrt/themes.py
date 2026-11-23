"""题材 · concept sectors: which listed companies belong to each concept (AI 算力, 机器人 ...), refreshed weekly.

East Money's concept boards - the same lists its app shows. Membership changes slowly (a few companies a week), so
the whole map, roughly 450 concepts and 50-100 thousand stock-concept pairs, is downloaded once a week in about 35
large pages from East Money's data centre (RPT_F10_CORETHEME_BOARDTYPE - a different server from the quote server,
which throttles bursts) and kept in results/cn/themes/. Industry boards, regions and the index / holder / style
lists East Money files among its concepts (沪深300, 融资融券, 基金重仓, 昨日涨停, 大盘成长 ...) are left out: they
aren't themes.

Which themes are "hot" is worked out every day from the latest session (hot() below), not stored here.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import date

import pandas as pd

from . import archive, http, sample

URL = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
HEADERS = {"Referer": "https://emweb.securities.eastmoney.com/"}
REPORT = "RPT_F10_CORETHEME_BOARDTYPE"
PAGE = 3000
REFRESH_DAYS = 7
MIN_ROWS = 10_000          # a download smaller than this is incomplete: keep the previous map
NON_THEME = re.compile(
    r"_$|^HS300|上证|深证|深成|中证|创业成份|创业板综|MSCI|富时罗素|标准普尔|^GDR$|^A?B股$|^AH股$|沪股通|深股通|融资融券|"
    r"转债标的|重仓|新进|增仓|减仓|持股|养老金|昨日|最近多板|新高|破净|破发|破增发|超跌|低价股|百元股|微盘|小盘|中盘|"
    r"大盘|权重股|行业龙头|周期股|价值股|红利股|微利股|趋势股|反转股|题材股|高成长股|市净率|市盈率|连续亏损|外资背景|"
    r"东方财富热股|风格|报预|报扭亏|报首亏|预盈|预增|预减|股权集中|股权分散|密集调研|增持计划|券商金股|摘帽|^ST股$|"
    r"次新股|茅指数|宁组合|举牌|股权激励|股权转让|做市|板块$|^IPO受益$|^(黑龙江|内蒙古|新疆|西藏|广西|宁夏)$")
# Quick picks on the page: a few concepts that together make up each popular theme.
PRESETS = {
    "ai": (("AI 算力", "AI computing power"), ["算力概念", "算力租赁", "智算中心（AIDC）", "CPO概念", "液冷服务器"]),
    "robot": (("机器人", "Robotics"), ["机器人概念", "人形机器人", "机器人执行器", "减速器"]),
}
DEFAULT = ["算力概念"]


def is_theme(name: str, board_type: str | None = None) -> bool:
    return board_type != "行业" and bool(name) and not NON_THEME.search(name)


def _dir():
    return archive.RESULTS_DIR / "cn" / "themes"


def _code(secucode: str) -> str | None:
    """'603636.SH' -> 'sh603636'."""
    num, _, ex = str(secucode).partition(".")
    return f"{ex.lower()}{num}" if ex.upper() in ("SH", "SZ", "BJ") else None


def _page(n: int) -> dict:
    j = http.get(URL, params={"reportName": REPORT, "columns": "SECUCODE,BOARD_NAME,NEW_BOARD_CODE,BOARD_TYPE",
                              "pageSize": PAGE, "pageNumber": n, "sortColumns": "SECUCODE,BOARD_CODE",
                              "sortTypes": "1,1"},
                 headers=HEADERS, timeout=30).json()
    res = j.get("result") or {}
    if not res.get("data"):
        raise http.SourceUnavailable(f"eastmoney {REPORT} page {n}: {j.get('message')}")
    return res


def refresh(progress: Callable[[float, str], None] | None = None) -> dict:
    """Download the whole concept map and save it (about 35 requests). Raises if it comes back incomplete."""
    first = _page(1)
    pages = int(first.get("pages") or 1)
    rows = list(first["data"])
    rest = list(range(2, pages + 1))
    for i in range(0, len(rest), 8):
        for r in http.pmap(_page, rest[i:i + 8], workers=4):
            if isinstance(r, Exception):
                raise r
            rows += r["data"]
        if progress:
            progress(min(i + 9, pages) / pages, f"题材成分 · Concept members {min(i + 9, pages)}/{pages}")
    raw = pd.DataFrame(rows)
    if len(raw) < MIN_ROWS:
        raise http.SourceUnavailable(f"concept map incomplete: {len(raw)} rows")
    keep = [is_theme(n, t) for n, t in zip(raw["BOARD_NAME"], raw.get("BOARD_TYPE", pd.Series(None, index=raw.index)))]
    d = raw[keep]
    m = pd.DataFrame({"code": d["SECUCODE"].map(_code), "board": d["BOARD_NAME"].astype(str),
                      "board_code": d["NEW_BOARD_CODE"].astype(str)}).dropna().drop_duplicates(["code", "board"])
    save(m, date.today())
    return {"rows": len(m), "boards": m["board"].nunique(), "downloaded": len(raw)}


def save(m: pd.DataFrame, when: date) -> None:
    if sample.is_sample():
        return
    d = _dir()
    d.mkdir(parents=True, exist_ok=True)
    m.to_parquet(d / "map.parquet", index=False, compression="zstd")
    (d / "meta.json").write_text(json.dumps({"updated": when.isoformat(), "rows": len(m),
                                             "boards": int(m["board"].nunique())}), encoding="utf-8")
    _cache.clear()


_cache: dict = {}


def load_map() -> pd.DataFrame | None:
    p = _dir() / "map.parquet"
    if not p.exists():
        return None
    key = (str(p), p.stat().st_mtime)
    if _cache.get("key") != key:
        _cache.update(key=key, df=pd.read_parquet(p))
    return _cache["df"]


def updated() -> date | None:
    p = _dir() / "meta.json"
    try:
        return date.fromisoformat(json.loads(p.read_text(encoding="utf-8"))["updated"])
    except (OSError, ValueError, KeyError):
        return None


def stale(today: date) -> bool:
    u = updated()
    return u is None or (today - u).days >= REFRESH_DAYS


def catalog(m: pd.DataFrame) -> pd.DataFrame:
    """Every concept with its number of companies, largest first."""
    return (m.groupby(["board", "board_code"]).size().rename("members").reset_index()
            .sort_values(["members", "board"], ascending=[False, True]).reset_index(drop=True))


def members(m: pd.DataFrame, boards: list[str]) -> pd.DataFrame:
    """The companies in any of `boards`: code -> the selected concepts it belongs to ('、'-joined)."""
    d = m[m["board"].isin(boards)]
    if d.empty:
        return pd.DataFrame(columns=["code", "themes"])
    return d.groupby("code")["board"].agg(lambda s: "、".join(sorted(s))).rename("themes").reset_index()


def hot(m: pd.DataFrame, frame: pd.DataFrame, n: int = 12, min_members: int = 8) -> pd.DataFrame:
    """The leading concepts of the latest session: ranked by their companies' average change over the last 3
    sessions (whole market), with the 3-day main-fund net inflow of the companies whose flow was checked."""
    d = m.merge(frame[["code", "chg3", "main_net3"]], on="code", how="inner")
    if d.empty:
        return pd.DataFrame(columns=["board", "members", "chg3", "main_net3", "up_share"])
    g = d.groupby("board")
    out = pd.DataFrame({"members": g.size(), "chg3": g["chg3"].mean(), "main_net3": g["main_net3"].sum(min_count=1),
                        "up_share": g["chg3"].apply(lambda s: 100 * (s > 0).mean())})
    out = out[out["members"] >= min_members]
    return out.sort_values("chg3", ascending=False).head(n).reset_index()
