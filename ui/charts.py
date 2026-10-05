"""Altair charts. Conventions: thin marks, one y-axis per chart (volume gets its own
chart), hover tooltips on every chart, series colours fixed per entity, and
market-convention up/down colours (red = up in mainland China by default)."""

from __future__ import annotations

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

from ui.fmt import up_down_colors

LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
DARK = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"]
MUTED = "#898781"
NEUTRAL_MID = "#f0efec"
DIVERGING = ("#2a78d6", "#e34948")  # negative pole, positive pole

CN_TICKS = ["09:30", "10:00", "10:30", "11:00", "11:30", "13:30", "14:00", "14:30", "15:00"]
HK_TICKS = ["09:30", "10:30", "11:30", "13:30", "14:30", "15:30", "16:00"]


def palette() -> list[str]:
    try:
        return DARK if st.context.theme.type == "dark" else LIGHT
    except Exception:  # noqa: BLE001
        return LIGHT


def _color_scale(labels: list[str]) -> alt.Scale:
    pal = palette()
    return alt.Scale(domain=labels, range=[pal[i % len(pal)] for i in range(len(labels))])


def intraday_price(bars: pd.DataFrame, prev_close: float | None, mkt: str, cut_time: str | None = None,
                   height: int = 300) -> alt.LayerChart:
    df = bars[bars["session"].isin(["AM", "PM"])].copy()
    df["t"] = df["datetime"].dt.strftime("%H:%M")
    order = df["t"].tolist()
    ticks = [t for t in (CN_TICKS if mkt == "CN" else HK_TICKS) if t in set(order)]
    x = alt.X("t:O", sort=order, title=None,
              axis=alt.Axis(values=ticks, labelAngle=0, grid=False, labelColor=MUTED, tickColor=MUTED))
    long = df.melt(id_vars=["t", "volume", "pct"], value_vars=["price", "vwap"], var_name="series", value_name="value")
    long["series"] = long["series"].map({"price": "Price", "vwap": "VWAP (average price)"})
    pal = palette()
    color = alt.Color("series:N", title=None, legend=alt.Legend(orient="top", direction="horizontal"),
                      scale=alt.Scale(domain=["Price", "VWAP (average price)"], range=[pal[0], pal[1]]))
    lo, hi = df["price"].min(), df["price"].max()
    if prev_close:
        lo, hi = min(lo, prev_close), max(hi, prev_close)
    pad = (hi - lo) * 0.08 or hi * 0.01
    y = alt.Y("value:Q", title=None, scale=alt.Scale(domain=[lo - pad, hi + pad], zero=False))
    lines = alt.Chart(long).mark_line(interpolate="linear").encode(
        x=x, y=y, color=color,
        strokeWidth=alt.condition(alt.datum.series == "Price", alt.value(2), alt.value(1.5)))
    layers = [lines]
    if prev_close:
        ref = pd.DataFrame({"value": [prev_close], "label": [f"Prev close {prev_close:,.2f}"]})
        layers.append(alt.Chart(ref).mark_rule(color=MUTED, strokeWidth=1).encode(y="value:Q"))
    if cut_time and cut_time in set(order):
        cut = pd.DataFrame({"t": [cut_time], "label": [f"{cut_time} cut-off"]})
        layers.append(alt.Chart(cut).mark_rule(color=pal[6], strokeWidth=1.5).encode(x=alt.X("t:O", sort=order)))
        layers.append(alt.Chart(cut).mark_text(align="left", dx=4, y=8, color=pal[6], fontSize=11).encode(
            x=alt.X("t:O", sort=order), text="label:N"))
    hover = alt.selection_point(fields=["t"], nearest=True, on="pointerover", empty=False, clear="pointerout")
    layers.append(alt.Chart(df).mark_rule(color=MUTED).encode(
        x=alt.X("t:O", sort=order),
        opacity=alt.condition(hover, alt.value(0.5), alt.value(0)),
        tooltip=[alt.Tooltip("t:N", title="Time"), alt.Tooltip("price:Q", title="Price", format=",.2f"),
                 alt.Tooltip("pct:Q", title="Change %", format="+.2f"),
                 alt.Tooltip("vwap:Q", title="VWAP", format=",.2f"),
                 alt.Tooltip("volume:Q", title="Volume", format=",.0f")],
    ).add_params(hover))
    layers.append(alt.Chart(df).mark_point(filled=True, size=50, color=pal[0]).encode(
        x=alt.X("t:O", sort=order), y="price:Q", opacity=alt.condition(hover, alt.value(1), alt.value(0))))
    return alt.layer(*layers).properties(height=height)


def intraday_volume(bars: pd.DataFrame, height: int = 110) -> alt.Chart:
    df = bars[bars["session"].isin(["AM", "PM"])].copy()
    df["t"] = df["datetime"].dt.strftime("%H:%M")
    order = df["t"].tolist()
    up, down = up_down_colors()
    df["dir"] = np.where(df["price"].diff().fillna(0) >= 0, "Up minute", "Down minute")
    return alt.Chart(df).mark_bar().encode(
        x=alt.X("t:O", sort=order, axis=None),
        y=alt.Y("volume:Q", title=None, axis=alt.Axis(format="~s", labelColor=MUTED, tickCount=3)),
        color=alt.Color("dir:N", scale=alt.Scale(domain=["Up minute", "Down minute"], range=[up, down]),
                        legend=None),
        tooltip=[alt.Tooltip("t:N", title="Time"), alt.Tooltip("volume:Q", title="Volume", format=",.0f"),
                 alt.Tooltip("amount:Q", title="Value", format=",.0f")],
    ).properties(height=height)


def intraday_multi(minutes: pd.DataFrame, labels: dict[str, str], mkt: str, cut_time: str | None = None,
                   height: int = 340) -> alt.LayerChart:
    """% change from previous close for several stocks on one session axis (lunch break removed)."""
    df = minutes[minutes["session"].isin(["AM", "PM"])].copy()
    df["t"] = df["datetime"].dt.strftime("%H:%M")
    order = sorted(df["t"].unique())
    codes = list(dict.fromkeys(df["code"]))
    df["label"] = df["code"].map(lambda c: labels.get(c, c))
    domain = [labels.get(c, c) for c in codes]
    ticks = [t for t in (CN_TICKS if mkt == "CN" else HK_TICKS) if t in set(order)]
    x = alt.X("t:O", sort=order, title=None,
              axis=alt.Axis(values=ticks, labelAngle=0, grid=False, labelColor=MUTED))
    lines = alt.Chart(df).mark_line(strokeWidth=1.8).encode(
        x=x, y=alt.Y("pct:Q", title="Change vs previous close (%)", axis=alt.Axis(format="+.1f")),
        color=alt.Color("label:N", title=None, scale=_color_scale(domain), legend=alt.Legend(orient="top", columns=4)))
    zero = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(color=MUTED, strokeWidth=1).encode(y="y:Q")
    wide = df.pivot_table(index="t", columns="label", values="pct").reset_index()
    tooltip = [alt.Tooltip("t:N", title="Time")] + [
        alt.Tooltip(f"{lbl}:Q", title=lbl, format="+.2f") for lbl in domain[:8] if lbl in wide.columns]
    hover = alt.selection_point(fields=["t"], nearest=True, on="pointerover", empty=False, clear="pointerout")
    rule = alt.Chart(wide).mark_rule(color=MUTED).encode(
        x=alt.X("t:O", sort=order), opacity=alt.condition(hover, alt.value(0.5), alt.value(0)),
        tooltip=tooltip).add_params(hover)
    layers = [zero, lines, rule]
    if cut_time and cut_time in set(order):
        pal = palette()
        cut = pd.DataFrame({"t": [cut_time], "label": [f"{cut_time} cut-off"]})
        layers.append(alt.Chart(cut).mark_rule(color=pal[6], strokeWidth=1.5).encode(x=alt.X("t:O", sort=order)))
    return alt.layer(*layers).properties(height=height)


def daily_price(df: pd.DataFrame, height: int = 280) -> alt.LayerChart:
    d = df[["date", "close", "ret", "volume"]].copy()
    pal = palette()
    x = alt.X("date:T", title=None, axis=alt.Axis(labelColor=MUTED, grid=False))
    line = alt.Chart(d).mark_line(strokeWidth=2, color=pal[0]).encode(
        x=x, y=alt.Y("close:Q", title=None, scale=alt.Scale(zero=False)))
    hover = alt.selection_point(fields=["date"], nearest=True, on="pointerover", empty=False, clear="pointerout")
    rule = alt.Chart(d).mark_rule(color=MUTED).encode(
        x="date:T", opacity=alt.condition(hover, alt.value(0.5), alt.value(0)),
        tooltip=[alt.Tooltip("date:T", title="Date"), alt.Tooltip("close:Q", title="Close (adj.)", format=",.2f"),
                 alt.Tooltip("ret:Q", title="Day return", format="+.2%"),
                 alt.Tooltip("volume:Q", title="Volume", format=",.0f")]).add_params(hover)
    dot = alt.Chart(d).mark_point(filled=True, size=50, color=pal[0]).encode(
        x="date:T", y="close:Q", opacity=alt.condition(hover, alt.value(1), alt.value(0)))
    return alt.layer(line, rule, dot).properties(height=height)


def daily_volume(df: pd.DataFrame, height: int = 100) -> alt.Chart:
    d = df[["date", "volume", "ret"]].copy()
    up, down = up_down_colors()
    d["dir"] = np.where(d["ret"].fillna(0) >= 0, "Up day", "Down day")
    return alt.Chart(d).mark_bar().encode(
        x=alt.X("date:T", axis=None),
        y=alt.Y("volume:Q", title=None, axis=alt.Axis(format="~s", labelColor=MUTED, tickCount=3)),
        color=alt.Color("dir:N", scale=alt.Scale(domain=["Up day", "Down day"], range=[up, down]), legend=None),
        tooltip=[alt.Tooltip("date:T", title="Date"), alt.Tooltip("volume:Q", title="Volume", format=",.0f")],
    ).properties(height=height)


def multi_line(wide: pd.DataFrame, labels: dict[str, str], y_title: str, y_format: str = ",.1f",
               height: int = 320, baseline: float | None = None) -> alt.LayerChart:
    """One line per column (entity). Colours follow column order, never rank."""
    cols = list(wide.columns)
    long = wide.reset_index().melt(id_vars=wide.index.name or "date", var_name="code", value_name="value").dropna()
    long = long.rename(columns={wide.index.name or "date": "date"})
    long["label"] = long["code"].map(lambda c: labels.get(c, c))
    domain = [labels.get(c, c) for c in cols]
    x = alt.X("date:T", title=None, axis=alt.Axis(labelColor=MUTED, grid=False))
    color = alt.Color("label:N", title=None, scale=_color_scale(domain), legend=alt.Legend(orient="top", columns=4))
    lines = alt.Chart(long).mark_line(strokeWidth=1.8).encode(
        x=x, y=alt.Y("value:Q", title=y_title, axis=alt.Axis(format=y_format), scale=alt.Scale(zero=False)),
        color=color)
    hover = alt.selection_point(fields=["date"], nearest=True, on="pointerover", empty=False, clear="pointerout")
    wide_tt = wide.copy()
    wide_tt.columns = [labels.get(c, c) for c in cols]
    wide_tt = wide_tt.reset_index().rename(columns={wide.index.name or "date": "date"})
    tooltip = [alt.Tooltip("date:T", title="Date")] + [
        alt.Tooltip(f"{lbl}:Q", title=lbl, format=y_format) for lbl in wide_tt.columns[1:9]]
    rule = alt.Chart(wide_tt).mark_rule(color=MUTED).encode(
        x="date:T", opacity=alt.condition(hover, alt.value(0.5), alt.value(0)), tooltip=tooltip).add_params(hover)
    layers = [lines, rule]
    if baseline is not None:
        base_rule = alt.Chart(pd.DataFrame({"y": [baseline]})).mark_rule(color=MUTED, strokeWidth=1)
        layers.insert(0, base_rule.encode(y="y:Q"))
    return alt.layer(*layers).properties(height=height)


def corr_heatmap(corr: pd.DataFrame, labels: dict[str, str], height: int | None = None) -> alt.LayerChart:
    names = [labels.get(c, c) for c in corr.columns]
    c = corr.copy()
    c.index, c.columns = names, names
    long = c.stack().reset_index()
    long.columns = ["a", "b", "corr"]
    neg, pos = DIVERGING
    base = alt.Chart(long).encode(
        x=alt.X("a:N", sort=names, title=None, axis=alt.Axis(labelAngle=-35)),
        y=alt.Y("b:N", sort=names, title=None))
    rect = base.mark_rect(stroke="#fcfcfb", strokeWidth=2).encode(
        color=alt.Color("corr:Q", title="Correlation",
                        scale=alt.Scale(domain=[-1, 0, 1], range=[neg, NEUTRAL_MID, pos])),
        tooltip=[alt.Tooltip("a:N", title="Row"), alt.Tooltip("b:N", title="Column"),
                 alt.Tooltip("corr:Q", title="Correlation", format=".2f")])
    text = base.mark_text(fontSize=11).encode(
        text=alt.Text("corr:Q", format=".2f"),
        color=alt.condition("abs(datum.corr) > 0.6", alt.value("#ffffff"), alt.value("#0b0b0b")))
    h = height or max(220, 34 * len(names))
    return alt.layer(rect, text).properties(height=h)


def returns_heatmap(table: pd.DataFrame, height: int | None = None, row_title: str = "Year") -> alt.LayerChart:
    """Rows x columns of returns (fractions), coloured by sign in market convention, labelled in-cell."""
    t = table.copy()
    t.index = t.index.astype(str)
    long = t.reset_index().melt(id_vars=t.index.name or "index", var_name="col", value_name="ret")
    long = long.rename(columns={t.index.name or "index": "row"}).dropna()
    up, down = up_down_colors()
    rows = list(t.index)
    cols = [str(c) for c in t.columns]
    long["col"] = long["col"].astype(str)
    base = alt.Chart(long).encode(
        x=alt.X("col:N", sort=cols, title=None, axis=alt.Axis(labelAngle=0, orient="top")),
        y=alt.Y("row:N", sort=rows, title=row_title))
    rect = base.mark_rect(stroke="#fcfcfb", strokeWidth=2).encode(
        color=alt.Color("ret:Q", legend=None,
                        scale=alt.Scale(domain=[-0.4, 0, 0.4], range=[down, NEUTRAL_MID, up], clamp=True)),
        tooltip=[alt.Tooltip("row:N", title=row_title), alt.Tooltip("col:N", title="Series"),
                 alt.Tooltip("ret:Q", title="Return", format="+.1%")])
    text = base.mark_text(fontSize=11).encode(
        text=alt.Text("ret:Q", format="+.0%"),
        color=alt.condition("abs(datum.ret) > 0.25", alt.value("#ffffff"), alt.value("#0b0b0b")))
    h = height or max(160, 26 * len(rows))
    return alt.layer(rect, text).properties(height=h)


def funnel(steps: list[tuple[str, int]], height: int | None = None) -> alt.LayerChart:
    df = pd.DataFrame(steps, columns=["step", "count"])
    df["order"] = range(len(df))
    pal = palette()
    base = alt.Chart(df).encode(y=alt.Y("step:N", sort=list(df["step"]), title=None,
                                        axis=alt.Axis(labelLimit=320)),
                                x=alt.X("count:Q", title=None, scale=alt.Scale(type="symlog"),
                                        axis=alt.Axis(labelColor=MUTED, tickCount=4)))
    bars = base.mark_bar(color=pal[0], cornerRadiusEnd=4, height=16).encode(
        tooltip=[alt.Tooltip("step:N", title="Rule"), alt.Tooltip("count:Q", title="Stocks left", format=",")])
    text = base.mark_text(align="left", dx=4, color="#52514e", fontSize=11).encode(text=alt.Text("count:Q", format=","))
    return alt.layer(bars, text).properties(height=height or 26 * len(df) + 20)


def risk_return(df: pd.DataFrame, labels: dict[str, str], height: int = 320) -> alt.LayerChart:
    d = df.copy()
    d["label"] = d["code"].map(lambda c: labels.get(c, c))
    domain = list(d["label"])
    base = alt.Chart(d).encode(
        x=alt.X("ann_vol:Q", title="Volatility (annualised)", axis=alt.Axis(format="%"), scale=alt.Scale(zero=False)),
        y=alt.Y("period_return:Q", title="Period return", axis=alt.Axis(format="%")))
    pts = base.mark_circle(size=110, stroke="#fcfcfb", strokeWidth=2, opacity=1).encode(
        color=alt.Color("label:N", scale=_color_scale(domain), legend=None),
        tooltip=[alt.Tooltip("label:N", title="Stock"), alt.Tooltip("period_return:Q", title="Return", format="+.1%"),
                 alt.Tooltip("ann_vol:Q", title="Volatility", format=".1%"),
                 alt.Tooltip("sharpe:Q", title="Sharpe", format=".2f")])
    txt = base.mark_text(align="left", dx=8, fontSize=11, color="#52514e").encode(text="label:N")
    zero = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(color=MUTED, strokeWidth=1).encode(y="y:Q")
    return alt.layer(zero, pts, txt).properties(height=height)
