"""Tests for prediction-market data fetching in the pipeline.

Focus: ensure the per-market `get_quote` enrichment loop is gone — it
caused 429 storms because Kalshi's `/markets` listing endpoint already
returns yes/no bid/ask, volume, and open_interest, so per-ticker
re-fetching was redundant work.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from engines.execution.base import Executor
from engines.models import AssetClass
from engines.pipeline import TradingPipeline


def _make_strategy(strategy_id: str = "value_kalshi"):
    s = MagicMock()
    s.strategy_id = strategy_id
    s.asset_class = AssetClass.PREDICTIONS
    s.parameters = {"scan_limit": 50}
    s.needs_crypto_bars = False
    return s


def _make_pipeline() -> TradingPipeline:
    return TradingPipeline(
        risk_engine=MagicMock(),
        executor=Executor(),
        strategies=[],
    )


@pytest.mark.asyncio
async def test_prediction_fetch_does_not_per_market_quote():
    """get_markets returns full pricing already; per-market get_quote is a 429 farm."""
    adapter = MagicMock()
    adapter.get_markets = AsyncMock(return_value=[
        {
            "ticker": "KX-A", "title": "A", "yes_bid": 0.4, "no_bid": 0.55,
            "yes_ask": 0.42, "no_ask": 0.58, "volume": 100, "open_interest": 50,
        },
        {
            "ticker": "KX-B", "title": "B", "yes_bid": 0.6, "no_bid": 0.35,
            "yes_ask": 0.62, "no_ask": 0.38, "volume": 200, "open_interest": 75,
        },
    ])
    adapter.get_quote = AsyncMock(side_effect=AssertionError(
        "get_quote must NOT be called per-market — it's the source of the 429 storm"
    ))

    pipeline = _make_pipeline()
    result = await pipeline._fetch_prediction_data(adapter, _make_strategy())

    adapter.get_markets.assert_awaited_once()
    adapter.get_quote.assert_not_called()
    assert result["markets"][0]["yes_bid"] == 0.4
    assert result["markets"][1]["volume"] == 200
