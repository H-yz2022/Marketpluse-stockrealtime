"""Built-in sample dataset: offline replay and a full-app smoke test (runs in CI, no network)."""

import pytest

from stockrt import http, realtime, sample, screener
from stockrt.history import adjusted_daily

pytestmark = pytest.mark.skipif(not sample.available(), reason="sample_data/ not built")


@pytest.fixture
def sample_mode():
    sample.set_mode("sample")
    yield
    sample.set_mode("live")


def test_network_is_off(sample_mode):
    with pytest.raises(http.SourceUnavailable):
        http.get("https://qt.gtimg.cn/q=sh600519")


def test_replay_serves_every_layer(sample_mode):
    snap = realtime.market_snapshot("CN")
    assert len(snap) > 5000 and snap["price"].notna().mean() > 0.95
    df, label = adjusted_daily("sh600519")
    assert len(df) > 2000 and "ratio-adjusted" in label and (df["close"] > 0).all()
    flow = realtime.main_flow(["sh600519"])
    assert flow["main_net"].notna().all()


def test_screener_runs_offline(sample_mode):
    snap = realtime.market_snapshot("CN")
    bench = realtime.quotes(["sh000300"]).iloc[0]["pct_change"]
    res = screener.run(snap, screener.ScreenParams(), float(bench))
    assert res.funnel[0][1] == len(snap)
    assert all(n2 <= n1 for (_, n1), (_, n2) in zip(res.funnel, res.funnel[1:]))


def test_every_page_renders_offline(sample_mode):
    from pathlib import Path

    from streamlit.testing.v1 import AppTest

    root = Path(__file__).resolve().parent.parent
    at = AppTest.from_file(str(root / "streamlit_app.py"), default_timeout=120)
    at.run()
    for page in sorted((root / "app_pages").glob("*.py")):
        at.switch_page(f"app_pages/{page.name}")
        at.run()
        assert not at.exception, f"{page.name}: {[e.value for e in at.exception]}"


def test_front_page_replay_offline(sample_mode):
    from stockrt import checkpoint

    snap = realtime.market_snapshot("CN")
    res = checkpoint.evaluate(snap)
    assert set(res["frames"]) == {"14:00", "14:30", "15:00"}
    assert res["index_pct"] and res["candidates"] > 50
    r = checkpoint.ranked(res)
    assert not r.empty and r["rank"].tolist() == list(range(1, len(r) + 1))
