"""Today's main-fund flow: East Money only, fetched patiently (refused batches retried); what it still won't
answer stays unknown - Sina's figures measure something else and are never mixed in."""

from unittest.mock import patch

import pandas as pd

from stockrt import http, realtime
from stockrt.sources import eastmoney


def _rows(chunk):
    return [{"code": c, "main_net": 1.0, "source": "eastmoney"} for c in chunk]


def _no_sina(code):
    raise AssertionError("Sina's flow is never used for gaps")


def test_refused_batch_is_retried_and_what_stays_refused_is_unknown():
    codes = [f"sz{n:06d}" for n in range(350)] + ["hk00700"]
    tries = {}

    def em(chunk):
        tries[chunk[0]] = tries.get(chunk[0], 0) + 1
        if chunk[0] == "sz000100" and tries[chunk[0]] == 1:           # throttled once, answers on the retry
            raise http.SourceUnavailable("eastmoney: HTTP 502")
        if chunk[0] == "sz000200":                                     # refused every time
            raise http.SourceUnavailable("eastmoney: connection reset")
        return _rows(chunk)

    with patch.object(eastmoney, "flow_rows", side_effect=em), patch("time.sleep", lambda s: None), \
            patch.object(realtime.sina, "money_flow", side_effect=_no_sina):
        df = realtime.main_flow(codes)
    assert tries["sz000100"] == 2
    assert len(df) == 251 and df["code"].is_unique                     # 100 refused stocks left out
    assert not df["code"].isin([f"sz{n:06d}" for n in range(200, 300)]).any()
    assert realtime.sources(df) == "eastmoney 251"


def test_a_refused_first_batch_means_east_money_is_refusing_now():
    calls = []

    def em(chunk):
        calls.append(chunk[0])
        raise http.SourceUnavailable("down")

    with patch.object(eastmoney, "flow_rows", side_effect=em), patch("time.sleep", lambda s: None), \
            patch.object(realtime.sina, "money_flow", side_effect=_no_sina):
        df = realtime.main_flow([f"sh{600000 + n}" for n in range(250)] + ["hk00700"])
    assert calls == ["sh600000"] * 3                                   # the probe, three tries; not the others
    assert df.empty and list(df.columns)[:2] == ["code", "main_net"]
    assert realtime.sources(pd.DataFrame()) == "none"
