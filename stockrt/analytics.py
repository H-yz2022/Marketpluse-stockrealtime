"""Return and risk analytics on adjusted daily closes.

Conventions: simple daily returns, 252 trading days a year, volatility and
Sharpe annualised, VaR reported as the 5th-percentile one-day return (a
negative number), drawdowns as negative fractions. All functions take pandas
Series indexed by date.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252
PERIODS = ["1M", "3M", "6M", "YTD", "1Y", "3Y", "5Y", "Max"]
_MONTHS = {"1M": 1, "3M": 3, "6M": 6, "1Y": 12, "3Y": 36, "5Y": 60}


def period_start(last: pd.Timestamp, period: str) -> pd.Timestamp | None:
    if period == "Max":
        return None
    if period == "YTD":
        return pd.Timestamp(year=last.year - 1, month=12, day=31)
    return last - pd.DateOffset(months=_MONTHS[period])


def window(close: pd.Series, period: str, strict: bool = True) -> pd.Series:
    """Closes covering `period`, starting from the last close on/before the period start.

    With strict=True an empty series is returned when history is too short."""
    close = close.dropna()
    if close.empty:
        return close
    start = period_start(close.index[-1], period)
    if start is None:
        return close
    before = close[close.index <= start]
    if before.empty:
        return close.iloc[0:0] if strict else close
    return close[close.index >= before.index[-1]]


def total_return(close: pd.Series) -> float:
    close = close.dropna()
    return float(close.iloc[-1] / close.iloc[0] - 1) if len(close) > 1 else np.nan


def cagr(close: pd.Series) -> float:
    close = close.dropna()
    if len(close) < 2:
        return np.nan
    years = (close.index[-1] - close.index[0]).days / 365.25
    if years < 1:
        return np.nan
    return float((close.iloc[-1] / close.iloc[0]) ** (1 / years) - 1)


def daily_returns(close: pd.Series) -> pd.Series:
    return close.dropna().pct_change().dropna()


def ann_vol(ret: pd.Series) -> float:
    return float(ret.std(ddof=1) * np.sqrt(TRADING_DAYS)) if len(ret) > 2 else np.nan


def sharpe(ret: pd.Series, rf: float) -> float:
    vol = ann_vol(ret)
    if not vol or np.isnan(vol):
        return np.nan
    return float((ret.mean() * TRADING_DAYS - rf) / vol)


def sortino(ret: pd.Series, rf: float) -> float:
    downside = ret[ret < 0]
    dd = downside.std(ddof=1) * np.sqrt(TRADING_DAYS) if len(downside) > 2 else np.nan
    return float((ret.mean() * TRADING_DAYS - rf) / dd) if dd else np.nan


def drawdown(close: pd.Series) -> pd.Series:
    close = close.dropna()
    return close / close.cummax() - 1


def max_drawdown(close: pd.Series) -> float:
    dd = drawdown(close)
    return float(dd.min()) if not dd.empty else np.nan


def var_95(ret: pd.Series) -> float:
    """Historical one-day 95% VaR as a return (e.g. -0.025 = lose 2.5% or more on 5% of days)."""
    return float(ret.quantile(0.05)) if len(ret) > 20 else np.nan


def cvar_95(ret: pd.Series) -> float:
    if len(ret) <= 20:
        return np.nan
    cut = ret.quantile(0.05)
    return float(ret[ret <= cut].mean())


def regression(ret: pd.Series, bench: pd.Series) -> dict:
    """Beta, correlation, R², annualised alpha and the systematic / specific risk split."""
    df = pd.concat([ret, bench], axis=1, join="inner").dropna()
    if len(df) < 20:
        return {k: np.nan for k in ("beta", "corr", "r2", "alpha", "systematic_share",
                                    "systematic_vol", "specific_vol", "tracking_error")}
    y, x = df.iloc[:, 0], df.iloc[:, 1]
    var_x = x.var(ddof=1)
    beta = y.cov(x) / var_x if var_x else np.nan
    corr = y.corr(x)
    resid = y - beta * x
    alpha = (y.mean() - beta * x.mean()) * TRADING_DAYS
    total_var = y.var(ddof=1)
    sys_var = beta ** 2 * var_x
    return {
        "beta": float(beta),
        "corr": float(corr),
        "r2": float(corr ** 2),
        "alpha": float(alpha),
        # Share of the stock's variance explained by the market ("market-driven").
        "systematic_share": float(min(sys_var / total_var, 1.0)) if total_var else np.nan,
        "systematic_vol": float(np.sqrt(sys_var * TRADING_DAYS)),
        "specific_vol": float(resid.std(ddof=1) * np.sqrt(TRADING_DAYS)),
        "tracking_error": float((y - x).std(ddof=1) * np.sqrt(TRADING_DAYS)),
    }


def capture_ratios(ret: pd.Series, bench: pd.Series) -> tuple[float, float]:
    df = pd.concat([ret, bench], axis=1, join="inner").dropna()
    if len(df) < 20:
        return np.nan, np.nan
    y, x = df.iloc[:, 0], df.iloc[:, 1]
    up, down = x > 0, x < 0
    upc = y[up].mean() / x[up].mean() if up.any() else np.nan
    downc = y[down].mean() / x[down].mean() if down.any() else np.nan
    return float(upc), float(downc)


def rolling_beta(ret: pd.Series, bench: pd.Series, window_days: int = 60) -> pd.Series:
    df = pd.concat([ret, bench], axis=1, join="inner").dropna()
    cov = df.iloc[:, 0].rolling(window_days).cov(df.iloc[:, 1])
    var = df.iloc[:, 1].rolling(window_days).var()
    return (cov / var).dropna()


def yearly_returns(close: pd.Series) -> pd.Series:
    """Calendar-year returns; the first year is measured from the first available close."""
    close = close.dropna()
    if close.empty:
        return pd.Series(dtype=float)
    year_end = close.groupby(close.index.year).last()
    prev = year_end.shift(1)
    prev.iloc[0] = close.iloc[0]
    return year_end / prev - 1


def monthly_returns(close: pd.Series) -> pd.DataFrame:
    close = close.dropna()
    m = close.resample("ME").last()
    r = m.pct_change()
    r.iloc[0] = m.iloc[0] / close.iloc[0] - 1
    out = pd.DataFrame({"year": r.index.year, "month": r.index.month, "ret": r.values})
    return out.pivot(index="year", columns="month", values="ret")


def period_returns(close: pd.Series) -> dict:
    """Return over every standard period (NaN where history is too short)."""
    out = {p: total_return(window(close, p)) for p in PERIODS}
    out["CAGR (Max)"] = cagr(close)
    return out


def scorecard(close: pd.Series, bench_close: pd.Series | None, period: str, rf: float) -> dict:
    """The metric set used by dashboards, compare tables and the ETF scorecard."""
    w = window(close, period, strict=period != "Max")
    ret = daily_returns(w)
    row = {
        "period_return": total_return(w),
        "cagr": cagr(w),
        "ann_vol": ann_vol(ret),
        "sharpe": sharpe(ret, rf),
        "sortino": sortino(ret, rf),
        "max_drawdown": max_drawdown(w),
        "var_95": var_95(ret),
        "cvar_95": cvar_95(ret),
        "observations": int(len(ret)),
        "start": w.index[0] if len(w) else pd.NaT,
    }
    if bench_close is not None:
        b = daily_returns(bench_close[bench_close.index >= (w.index[0] if len(w) else bench_close.index[0])])
        row.update(regression(ret, b))
        row["up_capture"], row["down_capture"] = capture_ratios(ret, b)
    return row


def correlation_matrix(panel: pd.DataFrame, period: str) -> pd.DataFrame:
    if panel.empty:
        return panel
    start = period_start(panel.index[-1], period)
    p = panel if start is None else panel[panel.index >= start]
    return p.pct_change(fill_method=None).corr(min_periods=20)


def rebased(panel: pd.DataFrame, period: str) -> pd.DataFrame:
    """Growth of 100 from the period start for each column (missing early data left blank)."""
    if panel.empty:
        return panel
    start = period_start(panel.index[-1], period)
    p = panel if start is None else panel[panel.index >= start]
    p = p.ffill()
    return 100 * p / p.bfill().iloc[0]
