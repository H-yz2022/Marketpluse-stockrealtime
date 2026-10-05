"""Dataset exports: CSV (UTF-8 with BOM so Excel shows Chinese names) and ZIP bundles."""

from __future__ import annotations

import io
import zipfile
from datetime import date, datetime

import pandas as pd

from . import adjust, history, http, intraday
from .sources import tencent
from .symbols import is_fund, is_index, market

DAILY_EXPORT_COLUMNS = ["code", "name", "date", "open", "high", "low", "close", "close_raw", "ret",
                        "volume", "amount", "turnover_rate", "factor", "event"]


def to_csv_bytes(df: pd.DataFrame) -> bytes:
    out = df.copy()
    for c in out.columns:
        if isinstance(out[c].dtype, pd.DatetimeTZDtype):
            out[c] = out[c].dt.tz_localize(None)  # Beijing wall-clock time
    return out.to_csv(index=False).encode("utf-8-sig")


def zip_bytes(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def _bulk_daily(code: str, start: date | None, end: date | None) -> tuple[pd.DataFrame, str]:
    """One request per symbol, no disk cache: for whole-market downloads.

    Stocks are ratio-adjusted from the dividend text embedded in Tencent's daily
    bars (no extra request); funds use Sina factors (Tencent has no fund events)."""
    raw = tencent.daily(code, start=start, end=end)
    if raw.empty:
        return raw, "no data"
    if is_index(code):
        return adjust.apply_returns(raw, raw["close"].pct_change().to_numpy()), "price index (no adjustment)"
    if is_fund(code):
        segs, src = history.factor_segments(code)
        if segs:
            ret = adjust.returns_from_segments(raw["date"], raw["close"], segs)
            return adjust.apply_returns(raw, ret), f"ratio-adjusted ({src} factors)"
    events = adjust.events_from_text(raw, market(code))
    ret = adjust.returns_from_events(raw["date"], raw["close"], events)
    label = "ratio-adjusted (dividend text)" if events else "no corporate actions in range"
    return adjust.apply_returns(raw, ret), label


def daily_dataset(codes: list[str], names: dict[str, str], start: date | None, end: date | None,
                  adjusted: bool = True, bulk: bool = False, workers: int = 6) -> tuple[pd.DataFrame, list[str]]:
    """Long-format daily bars for many symbols. Returns (data, errors).

    bulk=False reuses the cached full history (best for a watchlist);
    bulk=True fetches only the requested range (best for hundreds of symbols)."""

    def one(code: str) -> pd.DataFrame:
        df, label = _bulk_daily(code, start, end) if bulk else history.daily(code, start, end)
        df = df.assign(code=code, name=names.get(code, code), adjustment=label)
        if not adjusted:
            df["close"] = df["close_raw"]
            for col in ("open", "high", "low"):
                df[col] = df[col] / df["factor"]
        return df

    frames, errors = [], []
    for code, res in zip(codes, http.pmap(one, codes, workers=workers)):
        if isinstance(res, Exception):
            errors.append(f"{code}: {res}")
        elif not res.empty:
            frames.append(res)
    if not frames:
        return pd.DataFrame(columns=DAILY_EXPORT_COLUMNS), errors
    data = pd.concat(frames, ignore_index=True)
    data["date"] = pd.to_datetime(data["date"]).dt.date
    cols = DAILY_EXPORT_COLUMNS + ["adjustment"]
    return data[[c for c in cols if c in data.columns]], errors


def wide_closes(data: pd.DataFrame) -> pd.DataFrame:
    """Date x symbol matrix of closes - convenient for correlation / returns work."""
    if data.empty:
        return data
    return data.pivot_table(index="date", columns="code", values="close").reset_index()


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M")


def intraday_dataset(codes: list[str], names: dict[str, str], sessions: str, cut: str | None,
                     workers: int = 6) -> tuple[pd.DataFrame, list[str]]:
    """1-minute price/volume series. sessions: 'latest' or 'last5'; cut applies to every day."""

    def one(code: str) -> pd.DataFrame:
        if sessions == "latest":
            day, pc, bars = intraday.today(code)
            bars = bars.assign(date=day, prev_close=pc)
        else:
            bars = intraday.five_days(code)
        if cut and not bars.empty:
            h, m = (int(x) for x in cut.split(":"))
            bars = bars[bars["datetime"].dt.hour * 60 + bars["datetime"].dt.minute <= h * 60 + m]
        return bars.assign(code=code, name=names.get(code, code))

    frames, errors = [], []
    for code, res in zip(codes, http.pmap(one, codes, workers=workers)):
        if isinstance(res, Exception):
            errors.append(f"{code}: {res}")
        elif not res.empty:
            frames.append(res)
    if not frames:
        return pd.DataFrame(), errors
    out = pd.concat(frames, ignore_index=True)
    first = ["code", "name", "date", "datetime", "session", "price", "pct", "vwap", "volume", "amount",
             "cum_volume", "cum_amount", "prev_close"]
    return out[[c for c in first if c in out.columns]], errors


def ohlc_dataset(codes: list[str], names: dict[str, str], period: str,
                 workers: int = 6) -> tuple[pd.DataFrame, list[str]]:
    """Minute OHLC bars (A-shares) for every symbol."""

    def one(code: str) -> pd.DataFrame:
        if market(code) != "CN":
            raise ValueError("minute OHLC bars are only available for mainland symbols")
        return tencent.minute_ohlc(code, period).assign(code=code, name=names.get(code, code))

    frames, errors = [], []
    for code, res in zip(codes, http.pmap(one, codes, workers=workers)):
        if isinstance(res, Exception):
            errors.append(f"{code}: {res}")
        elif not res.empty:
            frames.append(res)
    if not frames:
        return pd.DataFrame(), errors
    out = pd.concat(frames, ignore_index=True)
    return out[["code", "name", "datetime", "open", "high", "low", "close", "volume"]], errors


DAILY_README = """Daily dataset - column guide
=============================
code           Exchange-prefixed symbol: sh = Shanghai, sz = Shenzhen, bj = Beijing, hk = Hong Kong
name           Security name
date           Trading date (Beijing)
open/high/low/close
               Prices. "Adjusted" files are ratio-adjusted (dividends and splits reinvested), anchored so
               the last close in the file equals the real traded price. "Raw" files are as traded.
close_raw      Unadjusted close as traded that day
ret            Daily total return (decimal, 0.012 = +1.2%), measured against the exchange's ex-rights
               reference price on ex-dividend days
volume         Shares traded (A-share lots already converted to shares)
amount         Value traded, in CNY (mainland) or HKD (Hong Kong)
turnover_rate  Volume / float shares, in percent
factor         adjusted price = raw price x factor
event          Corporate action on that date as published (e.g. 10派28元 = CNY 2.8 cash per share)
adjustment     Which method adjusted this symbol (Sina factors, dividend text, or none for indices)

Sources: Tencent daily bars; Sina adjustment factors. Generated by the China & HK markets prototype.
Research data, not investment advice; verify anything material against exchange filings.
"""
