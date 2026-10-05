import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))


@pytest.fixture(autouse=True)
def offline_calendars(monkeypatch):
    """Tests use the bundled official calendar, never the live SZSE / HK-government feeds."""
    from stockrt import tradingdays

    def no_network(*_a, **_k):
        raise RuntimeError("network disabled in tests")

    monkeypatch.setattr(tradingdays, "fetch_cn_month", no_network)
    monkeypatch.setattr(tradingdays, "fetch_hk_holidays", no_network)
    monkeypatch.setattr(tradingdays.storage, "load_json", lambda *a, **k: None)
    tradingdays._mem.clear()
    yield
    tradingdays._mem.clear()
