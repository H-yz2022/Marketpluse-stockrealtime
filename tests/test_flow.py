"""Main-fund flow: East Money batches are kept even when some are refused; only the gaps go to Sina."""

from unittest.mock import patch

import pandas as pd

from stockrt import http, realtime


def _em(chunk):
    if chunk[0] == "sz000100":                      # the second batch is refused
        raise http.SourceUnavailable("eastmoney: connection reset")
    return pd.DataFrame({"code": chunk, "main_net": 1.0, "source": "eastmoney"})


def test_refused_batch_only_sends_its_codes_to_sina():
    codes = [f"sz{n:06d}" for n in range(250)] + ["hk00700"]
    asked = []

    def sina(code):
        asked.append(code)
        return {"code": code, "main_net": 2.0, "source": "sina"}

    with patch.object(realtime.eastmoney, "main_flow_batch", side_effect=_em), \
            patch.object(realtime.sina, "money_flow", side_effect=sina):
        df = realtime.main_flow(codes)
    assert sorted(asked) == [f"sz{n:06d}" for n in range(100, 200)]     # the refused batch, nothing else
    assert len(df) == 251 and df["code"].is_unique                    # HK came back from East Money
    assert realtime.sources(df) == "eastmoney 151 · sina 100"


def test_all_refused_falls_back_to_sina_for_a_shares():
    with patch.object(realtime.eastmoney, "main_flow_batch", side_effect=http.SourceUnavailable("down")), \
            patch.object(realtime.sina, "money_flow", side_effect=lambda c: {"code": c, "main_net": 1.0,
                                                                              "source": "sina"}):
        df = realtime.main_flow(["sh600519", "hk00700"])
    assert df["code"].tolist() == ["sh600519"]
    assert realtime.sources(pd.DataFrame()) == "none"
