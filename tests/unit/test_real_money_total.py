"""Tests for real_money_total — distinguishing paper-account capital
from real-money capital in the portfolio snapshot.

Today's portfolio is dominated by Alpaca paper ($100k) while real money
sits on Coinbase (~$18) and Kalshi ($10). Risk rules and Discord alerts
that report only `total_value` mislead at-a-glance interpretation.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engines.execution.alpaca import AlpacaAdapter
from engines.execution.base import Executor
from engines.execution.coinbase import CoinbaseAdapter
from engines.execution.kalshi import KalshiAdapter
from engines.models import AssetClass, PortfolioSnapshot


# ---------------------------------------------------------------------------
# Adapter-level: each adapter knows its own real-money contribution
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_alpaca_paper_real_money_value_is_zero():
    """Paper accounts contribute $0 to real_money_total even with $100k cash."""
    adapter = AlpacaAdapter(api_key="x", secret_key="y",
                            base_url="https://paper-api.alpaca.markets")
    adapter.get_account_value = AsyncMock(return_value=100_000.0)

    assert adapter.is_paper is True
    assert await adapter.real_money_value() == 0.0


@pytest.mark.asyncio
async def test_alpaca_live_real_money_value_returns_account_value():
    """Live accounts forward get_account_value() unchanged."""
    adapter = AlpacaAdapter(api_key="x", secret_key="y",
                            base_url="https://api.alpaca.markets")
    adapter.get_account_value = AsyncMock(return_value=12_345.67)

    assert adapter.is_paper is False
    assert await adapter.real_money_value() == 12_345.67


@pytest.mark.asyncio
async def test_coinbase_real_money_value_returns_account_value():
    """Coinbase is always real money (no sandbox account here)."""
    adapter = CoinbaseAdapter.__new__(CoinbaseAdapter)
    adapter.get_account_value = AsyncMock(return_value=18.34)

    assert adapter.is_paper is False
    assert await adapter.real_money_value() == 18.34


@pytest.mark.asyncio
async def test_kalshi_real_money_value_returns_account_value_even_in_observe_only():
    """observe_only is about order placement, not capital — the $10 balance is real."""
    adapter = KalshiAdapter.__new__(KalshiAdapter)
    adapter.observe_only = True
    adapter.get_account_value = AsyncMock(return_value=10.0)

    assert adapter.is_paper is False
    assert await adapter.real_money_value() == 10.0


# ---------------------------------------------------------------------------
# Executor aggregates real_money_total across adapters
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_portfolio_snapshot_computes_real_money_total():
    """Executor sums real_money_value() across registered adapters."""
    paper_alpaca = MagicMock()
    paper_alpaca.asset_class = AssetClass.EQUITIES
    paper_alpaca.is_paper = True
    paper_alpaca.get_account_value = AsyncMock(return_value=100_000.0)
    paper_alpaca.get_positions = AsyncMock(return_value=[])
    paper_alpaca.real_money_value = AsyncMock(return_value=0.0)

    coinbase = MagicMock()
    coinbase.asset_class = AssetClass.CRYPTO
    coinbase.is_paper = False
    coinbase.get_account_value = AsyncMock(return_value=18.0)
    coinbase.get_positions = AsyncMock(return_value=[])
    coinbase.real_money_value = AsyncMock(return_value=18.0)

    kalshi = MagicMock()
    kalshi.asset_class = AssetClass.PREDICTIONS
    kalshi.is_paper = False
    kalshi.get_account_value = AsyncMock(return_value=10.0)
    kalshi.get_positions = AsyncMock(return_value=[])
    kalshi.real_money_value = AsyncMock(return_value=10.0)

    executor = Executor()
    executor.register_adapter(paper_alpaca)
    executor.register_adapter(coinbase)
    executor.register_adapter(kalshi)

    snapshot = await executor.get_portfolio_snapshot()

    assert snapshot.total_value == pytest.approx(100_028.0)
    assert snapshot.real_money_total == pytest.approx(28.0)


# ---------------------------------------------------------------------------
# Discord alerts include both totals
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_alert_risk_event_includes_real_money_field():
    """Risk alerts must show both portfolio total and real-money total."""
    from engines import alerts

    captured: dict = {}

    async def _capture(**kwargs):
        captured.update(kwargs)
        return True

    with patch.object(alerts, "send_alert", new=_capture):
        await alerts.alert_risk_event(
            event_type="Signal Rejected",
            details="test details",
            portfolio_value=100_132.08,
            real_money_value=28.34,
        )

    fields = captured["fields"]
    assert "Portfolio Value" in fields
    assert fields["Portfolio Value"] == "$100,132.08"
    assert "Real Money" in fields
    assert fields["Real Money"] == "$28.34"
