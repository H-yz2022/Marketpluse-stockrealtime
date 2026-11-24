"""Main-fund flow: East Money batches are fetched patiently (refused ones retried, not paused for 10 minutes);
only what East Money still won't answer goes to Sina."""

from unittest.mock import patch

import pandas as pd

from stockrt import http, realtime
from stockrt.sources import eastmoney


def _rows(chunk):
    return [{"code": c, "main_net": 1.0, "source": "eastmoney"} for c in chunk]


def test_refused_batch_is_retried_then_only_its_codes_go_to_sina():
    codes = [f"sz{n:06d}" for n in range(350)] + ["hk00700"]
    tries, asked = {}, []

    def em(chunk):
        tries[chunk[0]] = tries.get(chunk[0], 0) + 1
        if chunk[0] == "sz000100" and tries[chunk[0]] == 1:           # throttled once, answers on the retry
            raise http.SourceUnavailable("eastmoney: HTTP 502")
        if chunk[0] == "sz000200":                                     # refused every time
            raise http.SourceUnavailable("eastmoney: connection reset")
        return _rows(chunk)

    def sina(code):
        asked.append(code)
        return {"code": code, "main_net": 2.0, "source": "sina"}

    with patch.object(eastmoney, "flow_rows", side_effect=em), \
            patch.object(realtime.sina, "money_flow", side_effect=sina), \
            patch("time.sleep", lambda s: None):
        df = realtime.main_flow(codes)
    assert tries["sz000100"] == 2
    assert sorted(asked) == [f"sz{n:06d}" for n in range(200, 300)]     # only the batch still refused
    assert len(df) == 351 and df["code"].is_unique                     # HK came back from East Money
    assert realtime.sources(df) == "eastmoney 251 · sina 100"


def test_a_refused_first_batch_means_east_money_is_refusing_now():
    calls = []

    def em(chunk):
        calls.append(chunk[0])
        raise http.SourceUnavailable("down")

    with patch.object(eastmoney, "flow_rows", side_effect=em), patch("time.sleep", lambda s: None), \
            patch.object(realtime.sina, "money_flow", side_effect=lambda c: {"code": c, "main_net": 1.0,
                                                                              "source": "sina"}):
        df = realtime.main_flow([f"sh{600000 + n}" for n in range(250)] + ["hk00700"])
    assert calls == ["sh600000", "sh600000"]                           # the probe, tried twice; not the others
    assert len(df) == 250 and set(df["source"]) == {"sina"}            # A-shares from Sina; HK has no fallback
    assert realtime.sources(pd.DataFrame()) == "none"
