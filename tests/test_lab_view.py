"""Strategy lab section (front page, loaded on demand) and the parameter pools (offline)."""

import pandas as pd
import pytest

from stockrt import lab, labpools, sample


@pytest.fixture
def sample_mode():
    sample.set_mode("sample")
    yield
    sample.set_mode("live")


@pytest.mark.parametrize("view", ["card", "custom", "today", "detail", "checks", "method"])
def test_every_lab_view_renders(sample_mode, view):
    from streamlit.testing.v1 import AppTest

    def app():
        from ui import lab_view

        lab_view.render()

    at = AppTest.from_function(app, default_timeout=120)
    at.session_state["lab_view"] = view
    at.run()
    assert not at.exception, [e.value for e in at.exception]


def test_official_parameters_reproduce_the_backtest():
    """With the default parameters each pool picks (almost exactly) the official backtest's trades."""
    bt = lab.read("backtest_trades.parquet")
    if bt.empty or not (labpools.POOL_DIR / "S1.parquet").exists():
        pytest.skip("backtest or pools not installed")
    bt["day"] = pd.to_datetime(bt["day"])
    for sid in ("S1", "S2", "S4", "S5"):
        p = labpools.picks(sid, labpools.load(sid), labpools.defaults(sid))
        ours = set(zip(pd.to_datetime(p["day"]), p["code"].astype(str)))
        off = bt[bt["strategy"] == sid]
        theirs = set(zip(off["day"], off["code"]))
        assert len(theirs - ours) <= 0.002 * len(theirs) + 1, sid      # rounding at a boundary, at most
        assert len(ours - theirs) <= 0.002 * len(theirs) + 2, sid


def test_changing_a_parameter_changes_the_picks():
    if not (labpools.POOL_DIR / "S1.parquet").exists():
        pytest.skip("pools not installed")
    pool = labpools.load("S1")
    base = labpools.picks("S1", pool, labpools.defaults("S1"))
    wider = labpools.picks("S1", pool, labpools.defaults("S1") | {"pct": (2.0, 7.0), "beats": False})
    assert len(wider) > len(base) and set(base.index) <= set(wider.index)
