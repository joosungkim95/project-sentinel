"""Tests for Alpaca adapter market-data plumbing."""
import pytest
from unittest.mock import MagicMock, patch

from engines.execution.alpaca import AlpacaAdapter, _ALPACA_TIMEFRAME_MAP


@pytest.mark.parametrize("key,expected", [
    ("1Min", "1Min"),
    ("5Min", "5Min"),
    ("15Min", "15Min"),
    ("1Hour", "1Hour"),
    ("4Hour", "4Hour"),
    ("1Day", "1Day"),
])
def test_timeframe_map_serializes_each_key(key, expected):
    """Every entry must serialize via .value — alpaca-py 0.43.x calls
    timeframe.value when building the request URL, so passing a raw
    string for the unit (e.g. TimeFrame(5, "Min")) raises AttributeError
    at request time."""
    tf = _ALPACA_TIMEFRAME_MAP[key]
    assert tf.value == expected


@pytest.mark.asyncio
async def test_get_historical_bars_uses_iex_feed():
    """Free-tier Alpaca subscriptions reject SIP; explicit feed='iex' is required."""
    adapter = AlpacaAdapter(api_key="x", secret_key="y")
    captured: dict = {}

    class _FakeBar:
        timestamp = __import__("datetime").datetime(2026, 5, 6)
        open = high = low = close = 100.0
        volume = 1

    class _FakeBars:
        def __getitem__(self, _symbol):
            return [_FakeBar()]

    def _capture(request):
        captured["request"] = request
        return _FakeBars()

    adapter._data_client = MagicMock()
    adapter._data_client.get_stock_bars.side_effect = _capture

    bars = await adapter.get_historical_bars("SPY", timeframe="1Day", limit=5)

    assert bars, "expected at least one bar from the fake response"
    assert captured["request"].feed == "iex", (
        "StockBarsRequest must set feed='iex' so free-tier accounts work; "
        "default is SIP which returns 'subscription does not permit'."
    )
