"""Formatting helpers and shared column configs."""

from __future__ import annotations

import math

import pandas as pd
import streamlit as st

CURRENCY_SYMBOL = {"CNY": "¥", "HKD": "HK$", "USD": "$"}


def _bad(x) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x)) or (x is pd.NA)


def pct(x, digits: int = 2, signed: bool = True, already_pct: bool = False) -> str:
    """Format a fraction (0.0123) - or a percent value with already_pct=True - as '+1.23%'."""
    if _bad(x):
        return "—"
    v = x if already_pct else 100 * x
    return f"{v:+.{digits}f}%" if signed else f"{v:.{digits}f}%"


def num(x, digits: int = 2) -> str:
    return "—" if _bad(x) else f"{x:,.{digits}f}"


def money(x, currency: str = "CNY", digits: int = 2) -> str:
    if _bad(x):
        return "—"
    sym = CURRENCY_SYMBOL.get(currency or "CNY", "")
    a = abs(x)
    for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if a >= div:
            return f"{'-' if x < 0 else ''}{sym}{a / div:.{digits}f}{suf}"
    return f"{'-' if x < 0 else ''}{sym}{a:,.0f}"


def price(x, currency: str = "CNY") -> str:
    if _bad(x):
        return "—"
    return f"{CURRENCY_SYMBOL.get(currency or 'CNY', '')}{x:,.2f}" if x >= 1 else f"{x:.3f}"


def delta_color() -> str:
    """China convention: red = up, green = down (st.metric 'inverse')."""
    return "inverse" if st.session_state.get("up_red", True) else "normal"


def up_down_colors() -> tuple[str, str]:
    red, green = "#d03b3b", "#0ca30c"
    return (red, green) if st.session_state.get("up_red", True) else (green, red)


def color_signed(v) -> str:
    """Styler cell CSS: up/down colour for signed numbers (colour only, formatting via column_config)."""
    if _bad(v) or v == 0:
        return ""
    up, down = up_down_colors()
    return f"color: {up if v > 0 else down}"


def style_signed(df: pd.DataFrame, cols: list[str]):
    present = [c for c in cols if c in df.columns]
    return df.style.map(color_signed, subset=present)


P = st.column_config


def pct_col(label: str, help: str | None = None, frac: bool = True):
    """Percent column: frac=True for fractions (0.05), False for values already in percent (5.0)."""
    return P.NumberColumn(label, format="percent" if frac else "%.2f%%", help=help)


def compact_col(label: str, help: str | None = None):
    return P.NumberColumn(label, format="compact", help=help)


QUOTE_COLUMNS = {
    "code": P.TextColumn("Code", pinned=True),
    "name": P.TextColumn("Name", pinned=True),
    "price": P.NumberColumn("Price", format="%.2f"),
    "pct_change": pct_col("Change", frac=False),
    "volume_ratio": P.NumberColumn("Vol ratio", format="%.2f", help="量比: per-minute volume today vs 5-day average"),
    "turnover_rate": P.NumberColumn("Turnover", format="%.2f%%", help="换手率: volume / float shares"),
    "float_mcap": compact_col("Float cap", "流通市值"),
    "total_mcap": compact_col("Market cap"),
    "amount": compact_col("Value traded", "成交额"),
    "volume": compact_col("Volume (shares)"),
    "pe_ttm": P.NumberColumn("P/E (TTM)", format="%.1f"),
    "pb": P.NumberColumn("P/B", format="%.2f"),
    "div_yield": P.NumberColumn("Div. yield", format="%.2f%%"),
    "amplitude": P.NumberColumn("Range", format="%.2f%%", help="振幅: (high - low) / previous close"),
    "main_net": compact_col("Main net inflow", "主力净流入: large + extra-large orders, bought minus sold"),
    "main_net_ratio": P.NumberColumn("Main net / value", format="%.1f%%"),
    "board": P.TextColumn("Board"),
}
