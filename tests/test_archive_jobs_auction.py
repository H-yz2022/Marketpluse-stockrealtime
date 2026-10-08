"""Results archive, background jobs and the pre-open auction helpers (offline)."""

import time
from datetime import date

import numpy as np
import pandas as pd
import pytest

from stockrt import archive, auction, checkpoint, jobs, sample


def test_archive_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(archive, "RESULTS_DIR", tmp_path)
    frame = pd.DataFrame({"code": ["sh600001"], "passes": [True], "score": [80.0]})
    res = {"day": date(2026, 10, 8), "frames": {"14:00": frame, "15:00": frame}, "index_pct": {"14:00": 0.1},
           "notes": ["n"], "candidates": 3}
    archive.save(res, "CN", {"pct_min": 3.0})
    back = archive.load("CN", date(2026, 10, 8))
    assert back["source"] == "archive" and list(back["frames"]) == ["14:00", "15:00"]
    assert back["frames"]["15:00"]["code"].tolist() == ["sh600001"]
    assert archive.sessions("CN") == [date(2026, 10, 8)] and archive.sessions("HK") == []


def test_repository_archive_feeds_the_front_page():
    # The committed results/ folder lets a fresh server show real sessions with no network at all.
    assert date(2026, 10, 8) in archive.sessions("CN")
    res = checkpoint.load_result(date(2026, 10, 8), spec=checkpoint.SPECS["CN"])
    assert res is not None and set(res["frames"]) == {"14:00", "14:30", "15:00"}
    latest = checkpoint.latest_saved(checkpoint.SPECS["CN"], on_or_before=date(2026, 10, 7))
    assert latest is not None and latest["day"] == date(2026, 9, 30)


def test_runner_runs_once_and_reports_failures():
    r = jobs.Runner()
    calls = []

    def work(progress):
        progress(0.5, "half")
        calls.append(1)

    j = r.submit(("a",), work)
    for _ in range(100):
        if not j.active:
            break
        time.sleep(0.02)
    assert j.state == "done" and calls == [1]
    assert r.submit(("a",), work) is j and calls == [1]          # done jobs are not re-run
    assert r.submit(("a",), work, force=True).key == ("a",)       # unless forced

    def boom(progress):
        raise ValueError("feed down")

    f = r.submit(("b",), boom)
    for _ in range(100):
        if not f.active:
            break
        time.sleep(0.02)
    assert f.state == "failed" and "feed down" in f.error


def test_auction_indicative_price_and_gaps():
    q = pd.DataFrame({
        "code": ["a", "b", "c"], "price": [0.0, 10.2, 0.0], "prev_close": [10.0, 10.0, 10.0],
        "bid1": [10.5, 10.1, 0.0], "ask1": [10.5, 10.3, 0.0], "open": [0.0, 10.2, 0.0], "is_st": False,
        "float_mcap": 5e9,
    })
    d = auction.indicative(q)
    assert d.loc[0, "indicative"] == pytest.approx(10.5)       # bid1 == ask1: the auction's matched price
    assert d.loc[1, "indicative"] == pytest.approx(10.2)       # otherwise the price field
    assert np.isnan(d.loc[2, "indicative"])                    # nothing published yet
    assert d.loc[0, "gap_pct"] == pytest.approx(5.0)
    b = auction.breadth(d)
    assert b["up"] == 2 and b["up_3"] == 1
    up, down = auction.movers(d, 1)
    assert up["code"].tolist() == ["a"]
    assert auction.opening(q)["code"].tolist() == ["b"]


@pytest.mark.skipif(not sample.available(), reason="sample_data/ not built")
def test_front_page_never_blocks_offline():
    from pathlib import Path

    from streamlit.testing.v1 import AppTest

    sample.set_mode("sample")
    try:
        at = AppTest.from_file(str(Path(__file__).resolve().parent.parent / "streamlit_app.py"), default_timeout=60)
        at.run()
        assert not at.exception
        assert any(m.label == "Session" for m in at.metric)
    finally:
        sample.set_mode("live")
