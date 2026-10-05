"""SQL over in-memory datasets (SQLite). Read-only by construction: every query
runs against a fresh in-memory copy, so nothing a query does can persist."""

from __future__ import annotations

import re
import sqlite3

import pandas as pd

MAX_ROWS = 5000

EXAMPLES = {
    "Late-session momentum (snapshot rules only)": """\
SELECT code, name, price, pct_change, volume_ratio, turnover_rate,
       ROUND(float_mcap / 1e9, 2) AS float_cap_bn
FROM snapshot
WHERE is_st = 0
  AND pct_change BETWEEN 3 AND 5
  AND volume_ratio > 1
  AND turnover_rate BETWEEN 3 AND 8
  AND float_mcap BETWEEN 5e9 AND 20e9
ORDER BY pct_change DESC""",
    "Biggest movers by board": """\
SELECT board, COUNT(*) AS stocks,
       ROUND(AVG(pct_change), 2) AS avg_change,
       SUM(pct_change > 0) AS advancers, SUM(pct_change < 0) AS decliners,
       ROUND(SUM(amount) / 1e8, 1) AS turnover_100m
FROM snapshot GROUP BY board ORDER BY turnover_100m DESC""",
    "Strongest active buying (外盘 > 内盘)": """\
SELECT code, name, pct_change, turnover_rate,
       ROUND(1.0 * buy_volume / (buy_volume + sell_volume), 3) AS buy_share
FROM snapshot
WHERE buy_volume + sell_volume > 0 AND amount > 2e8
ORDER BY buy_share DESC LIMIT 30""",
    "Near 52-week high with volume": """\
SELECT code, name, price, high_52w, volume_ratio, pct_change
FROM snapshot
WHERE price >= 0.97 * high_52w AND volume_ratio > 1.5 AND is_st = 0
ORDER BY volume_ratio DESC""",
}

_FORBIDDEN = re.compile(r"\b(attach|detach|pragma|load_extension)\b", re.I)


def _sqlite_ready(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for c in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[c]):
            out[c] = out[c].astype(str)
        elif out[c].dtype == object:
            out[c] = out[c].map(lambda v: v if v is None or isinstance(v, (str, int, float)) else str(v))
        elif pd.api.types.is_bool_dtype(out[c]):
            out[c] = out[c].astype(int)
    return out


def run(sql: str, tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    stmt = sql.strip().rstrip(";")
    if not re.match(r"^(select|with)\b", stmt, re.I):
        raise ValueError("Only SELECT (or WITH ... SELECT) queries are allowed.")
    if ";" in stmt:
        raise ValueError("Run one statement at a time.")
    if _FORBIDDEN.search(stmt):
        raise ValueError("ATTACH / PRAGMA / extensions are not allowed.")
    con = sqlite3.connect(":memory:")
    try:
        for name, df in tables.items():
            if df is not None and not df.empty:
                _sqlite_ready(df).to_sql(name, con, index=False)
        return pd.read_sql_query(stmt, con).head(MAX_ROWS)
    finally:
        con.close()


def schema(tables: dict[str, pd.DataFrame]) -> dict[str, list[str]]:
    return {name: list(df.columns) for name, df in tables.items() if df is not None and not df.empty}
