"""
Unit tests for KCS-02: Implied Probability vs. Spot Price Divergence Model.

Tests verify signal generation logic for the CryptoProbabilityStrategy,
which compares Kalshi crypto contract implied probabilities against a
log-normal model probability.
"""

import math
import pytest

from engines.models import MarketRegime, Side
from engines.strategy.predictions.crypto_probability import CryptoProbabilityStrategy


# --- Helpers ---

def _make_market(
    ticker: str,
    yes_ask: float,
    no_ask: float,
    yes_bid: float,
    no_bid: float,
    volume: int,
    open_interest: int,
    close_time: str,
    strike_price: float,
) -> dict:
    return {
        "ticker": ticker,
        "title": f"Will BTC be above ${strike_price:,.0f}?",
        "yes_ask": yes_ask,
        "no_ask": no_ask,
        "yes_bid": yes_bid,
        "no_bid": no_bid,
        "volume": volume,
        "open_interest": open_interest,
        "close_time": close_time,
        "strike_price": strike_price,
        "status": "open",
    }


def _make_bars(count: int = 750, base_price: float = 70000.0) -> list[dict]:
    """Generate synthetic hourly bars with ~50% annualized vol."""
    import numpy as np

    rng = np.random.default_rng(123)
    hourly_std = 0.50 / math.sqrt(365 * 24)
    bars = []
    price = base_price
    for i in range(count):
        ret = rng.normal(0, hourly_std)
        price = price * math.exp(ret)
        bars.append({
            "timestamp": str(1700000000 + i * 3600),
            "open": price * 0.999,
            "high": price * 1.002,
            "low": price * 0.998,
            "close": price,
            "volume": 100.0,
        })
    return bars


# Use a future date so hours_to_expiry is always positive in tests.
# 3 days from now avoids min_hours_to_expiry filter.
from datetime import datetime as _dt, timezone as _tz, timedelta as _td
CLOSE_TIME = (_dt.now(tz=_tz.utc) + _td(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- Tests ---

class TestCryptoProbabilityStrategy:

    @pytest.fixture
    def strategy(self) -> CryptoProbabilityStrategy:
        return CryptoProbabilityStrategy()

    @pytest.mark.asyncio
    async def test_buy_yes_when_model_above_market(self, strategy: CryptoProbabilityStrategy) -> None:
        """
        Deep ITM scenario: spot=70000, strike=60000.
        Model probability should be very high; market at 0.50 means large YES edge → BUY YES.
        """
        market = _make_market(
            ticker="KXBTCD-26APR01-B60000",
            yes_ask=0.50,
            no_ask=0.51,
            yes_bid=0.48,
            no_bid=0.49,
            volume=500,
            open_interest=200,
            close_time=CLOSE_TIME,
            strike_price=60000.0,
        )
        bars = {
            "markets": [market],
            "crypto_bars": _make_bars(count=750, base_price=70000.0),
        }

        signals = await strategy.generate_signals(bars, MarketRegime.TRENDING_UP)

        assert len(signals) >= 1
        sig = signals[0]
        assert sig.side == Side.BUY
        assert sig.symbol == "KXBTCD-26APR01-B60000"
        assert sig.confidence > 0

    @pytest.mark.asyncio
    async def test_buy_no_when_model_below_market(self, strategy: CryptoProbabilityStrategy) -> None:
        """
        Deep OTM scenario: spot=70000, strike=90000.
        Model probability should be very low; market at 0.50 means large NO edge → BUY NO (Side.SELL).
        """
        market = _make_market(
            ticker="KXBTCD-26APR01-B90000",
            yes_ask=0.50,
            no_ask=0.51,
            yes_bid=0.48,
            no_bid=0.49,
            volume=500,
            open_interest=200,
            close_time=CLOSE_TIME,
            strike_price=90000.0,
        )
        bars = {
            "markets": [market],
            "crypto_bars": _make_bars(count=750, base_price=70000.0),
        }

        signals = await strategy.generate_signals(bars, MarketRegime.TRENDING_DOWN)

        assert len(signals) >= 1
        sig = signals[0]
        assert sig.side == Side.SELL
        assert sig.symbol == "KXBTCD-26APR01-B90000"

    @pytest.mark.asyncio
    async def test_no_signal_when_edge_too_small(self, strategy: CryptoProbabilityStrategy) -> None:
        """
        ATM scenario: strike set to the actual final spot price so model probability ~50%.
        Market implied probability also ~50% → edge < min_edge_pp → no signal.
        """
        crypto_bars = _make_bars(count=750, base_price=70000.0)
        # Use the final bar's close as strike so spot == strike → model ~50%
        atm_strike = crypto_bars[-1]["close"]

        market = _make_market(
            ticker="KXBTCD-26APR01-BATM",
            yes_ask=0.50,
            no_ask=0.51,
            yes_bid=0.49,
            no_bid=0.50,
            volume=500,
            open_interest=200,
            close_time=CLOSE_TIME,
            strike_price=atm_strike,
        )
        bars = {
            "markets": [market],
            "crypto_bars": crypto_bars,
        }

        signals = await strategy.generate_signals(bars, MarketRegime.RANGING)

        assert len(signals) == 0

    @pytest.mark.asyncio
    async def test_skips_illiquid_markets(self, strategy: CryptoProbabilityStrategy) -> None:
        """Markets with very low volume are filtered out → no signal."""
        market = _make_market(
            ticker="KXBTCD-26APR01-B60000",
            yes_ask=0.50,
            no_ask=0.51,
            yes_bid=0.48,
            no_bid=0.49,
            volume=3,
            open_interest=1,
            close_time=CLOSE_TIME,
            strike_price=60000.0,
        )
        bars = {
            "markets": [market],
            "crypto_bars": _make_bars(count=750, base_price=70000.0),
        }

        signals = await strategy.generate_signals(bars, MarketRegime.RANGING)

        assert len(signals) == 0

    @pytest.mark.asyncio
    async def test_skips_wide_spread(self, strategy: CryptoProbabilityStrategy) -> None:
        """
        Wide spread (yes_ask=0.55, yes_bid=0.40, spread=0.15 > max_spread=0.05) → no signal.
        """
        market = _make_market(
            ticker="KXBTCD-26APR01-B60000",
            yes_ask=0.55,
            no_ask=0.61,
            yes_bid=0.40,
            no_bid=0.45,
            volume=500,
            open_interest=200,
            close_time=CLOSE_TIME,
            strike_price=60000.0,
        )
        bars = {
            "markets": [market],
            "crypto_bars": _make_bars(count=750, base_price=70000.0),
        }

        signals = await strategy.generate_signals(bars, MarketRegime.RANGING)

        assert len(signals) == 0

    @pytest.mark.asyncio
    async def test_no_signal_when_insufficient_bars(self, strategy: CryptoProbabilityStrategy) -> None:
        """Only 10 bars provided (need 48 min observations) → vol calc fails → no signal."""
        market = _make_market(
            ticker="KXBTCD-26APR01-B60000",
            yes_ask=0.50,
            no_ask=0.51,
            yes_bid=0.48,
            no_bid=0.49,
            volume=500,
            open_interest=200,
            close_time=CLOSE_TIME,
            strike_price=60000.0,
        )
        bars = {
            "markets": [market],
            "crypto_bars": _make_bars(count=10, base_price=70000.0),
        }

        signals = await strategy.generate_signals(bars, MarketRegime.UNKNOWN)

        assert len(signals) == 0
