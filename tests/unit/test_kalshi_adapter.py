"""Tests for Kalshi adapter extensions."""
import pytest
from unittest.mock import AsyncMock
from engines.execution.kalshi import KalshiAdapter


@pytest.fixture
def adapter():
    """Create a KalshiAdapter with a dummy RSA key."""
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return KalshiAdapter(api_key="test-key", private_key_pem=pem, base_url="https://demo-api.kalshi.co")


@pytest.mark.asyncio
async def test_get_crypto_markets_passes_series_ticker(adapter):
    """get_crypto_markets should pass series_ticker to the API and return enriched data."""
    mock_response = {
        "markets": [{
            "ticker": "KXBTCD-26MAR28-B70000",
            "title": "Will Bitcoin be above $70,000 on March 28?",
            "yes_bid": 55, "no_bid": 44,
            "yes_ask": 56, "no_ask": 45,
            "volume": 500, "open_interest": 200,
            "status": "open",
            "close_time": "2026-03-28T23:59:59Z",
            "strike_price": 70000,
        }]
    }
    adapter._get = AsyncMock(return_value=mock_response)
    markets = await adapter.get_crypto_markets(series_ticker="KXBTC", limit=50)

    adapter._get.assert_called_once_with("/markets", params={"limit": 50, "status": "open", "series_ticker": "KXBTC"})
    assert len(markets) == 1
    m = markets[0]
    assert m["ticker"] == "KXBTCD-26MAR28-B70000"
    assert m["yes_ask"] == 0.56  # cents to dollars
    assert m["no_ask"] == 0.45
    assert m["yes_bid"] == 0.55
    assert m["no_bid"] == 0.44
    assert m["close_time"] == "2026-03-28T23:59:59Z"
    assert m["strike_price"] == 70000
    assert m["volume"] == 500
