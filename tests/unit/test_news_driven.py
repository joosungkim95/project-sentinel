"""
Unit tests for News-Driven prediction market strategy.

Updated for new bar-based signature (bars dict with "markets" key).
"""

import pytest
from config.tiers import StrategyTier
from engines.models import MarketRegime, Side
from engines.strategy.predictions.news_driven import NewsDrivenStrategy


def make_repricing_market(
    price_move: float = 0.12,
    volume: int = 5000,
    avg_volume: int = 500,
) -> dict:
    """Create a market showing a news-driven repricing event."""
    prev_price = 0.40
    return {
        "ticker": "KXEVENT-24-001",
        "title": "Will event X happen?",
        "yes_price": prev_price + price_move,
        "no_price": 1.0 - (prev_price + price_move),
        "yes_bid": prev_price + price_move - 0.02,
        "prev_yes_close": prev_price,
        "prev_price": prev_price,
        "volume": volume,
        "volume_avg": avg_volume,
        "avg_daily_volume": avg_volume,
        "spread": 0.04,
        "open_interest": 1000,
    }


def make_quiet_market() -> dict:
    """Create a market with no unusual activity."""
    return {
        "ticker": "KXQUIET-24-002",
        "title": "Quiet market",
        "yes_price": 0.50,
        "no_price": 0.50,
        "yes_bid": 0.49,
        "prev_yes_close": 0.49,
        "prev_price": 0.49,
        "volume": 200,
        "volume_avg": 300,
        "avg_daily_volume": 300,
        "spread": 0.03,
        "open_interest": 500,
    }


def make_wide_spread_market() -> dict:
    """Market with a repricing but illiquid spread."""
    return {
        "ticker": "KXWIDE-24-003",
        "yes_price": 0.60,
        "prev_yes_close": 0.45,
        "prev_price": 0.45,
        "volume": 5000,
        "volume_avg": 500,
        "avg_daily_volume": 500,
        "spread": 0.20,  # Too wide
    }


class TestMarketEvaluation:

    def test_detects_repricing(self):
        strategy = NewsDrivenStrategy()
        market = make_repricing_market()
        result = strategy._evaluate_market(market)
        assert result is not None
        score, direction = result
        assert score > 0
        assert direction == "yes"  # Price moved up

    def test_detects_negative_repricing(self):
        strategy = NewsDrivenStrategy()
        market = make_repricing_market(price_move=-0.12)
        result = strategy._evaluate_market(market)
        assert result is not None
        _, direction = result
        assert direction == "no"  # Price moved down

    def test_ignores_quiet_market(self):
        strategy = NewsDrivenStrategy()
        market = make_quiet_market()
        result = strategy._evaluate_market(market)
        assert result is None

    def test_ignores_wide_spread(self):
        strategy = NewsDrivenStrategy()
        market = make_wide_spread_market()
        result = strategy._evaluate_market(market)
        assert result is None

    def test_ignores_low_volume(self):
        strategy = NewsDrivenStrategy()
        market = make_repricing_market(volume=50)  # Below min
        result = strategy._evaluate_market(market)
        assert result is None

    def test_ignores_small_move(self):
        strategy = NewsDrivenStrategy()
        market = make_repricing_market(price_move=0.02)  # Below threshold
        result = strategy._evaluate_market(market)
        assert result is None

    def test_ignores_extreme_prices(self):
        strategy = NewsDrivenStrategy()
        market = make_repricing_market()
        market["yes_price"] = 0.95  # Too extreme
        result = strategy._evaluate_market(market)
        assert result is None


class TestSignalGeneration:

    @pytest.mark.asyncio
    async def test_generates_signal_for_repricing(self):
        strategy = NewsDrivenStrategy()
        signals = await strategy.generate_signals(
            bars={"markets": [make_repricing_market()]},
            market_regime=MarketRegime.UNKNOWN,
        )
        assert len(signals) >= 1
        assert signals[0].side == Side.BUY
        assert "News-driven" in signals[0].rationale

    @pytest.mark.asyncio
    async def test_caps_at_max_signals(self):
        strategy = NewsDrivenStrategy(parameters={"max_signals": 1})
        markets = [
            make_repricing_market(price_move=0.15, volume=8000),
            make_repricing_market(price_move=0.10, volume=6000),
        ]
        markets[1]["ticker"] = "KXEVENT-24-002"
        signals = await strategy.generate_signals(
            bars={"markets": markets},
            market_regime=MarketRegime.UNKNOWN,
        )
        assert len(signals) <= 1

    @pytest.mark.asyncio
    async def test_ranks_by_score(self):
        strategy = NewsDrivenStrategy(parameters={"max_signals": 2})
        big_move = make_repricing_market(price_move=0.20, volume=10000)
        small_move = make_repricing_market(price_move=0.09, volume=2000)
        small_move["ticker"] = "KXSMALL-24"
        signals = await strategy.generate_signals(
            bars={"markets": [small_move, big_move]},
            market_regime=MarketRegime.UNKNOWN,
        )
        if len(signals) == 2:
            # First signal should be the bigger move
            assert signals[0].symbol == "KXEVENT-24-001"

    @pytest.mark.asyncio
    async def test_no_markets_returns_empty(self):
        strategy = NewsDrivenStrategy()
        signals = await strategy.generate_signals(
            bars={"markets": []},
            market_regime=MarketRegime.UNKNOWN,
        )
        assert signals == []

    @pytest.mark.asyncio
    async def test_empty_data_returns_empty(self):
        strategy = NewsDrivenStrategy()
        signals = await strategy.generate_signals(
            bars={},
            market_regime=MarketRegime.UNKNOWN,
        )
        assert signals == []

    @pytest.mark.asyncio
    async def test_signal_includes_tier_and_position_size(self):
        """Signals must include SNIPER tier and position_size_usd."""
        strategy = NewsDrivenStrategy()
        signals = await strategy.generate_signals(
            bars={"markets": [make_repricing_market()]},
            market_regime=MarketRegime.UNKNOWN,
        )
        assert len(signals) >= 1
        assert signals[0].tier == StrategyTier.SNIPER
        assert signals[0].position_size_usd == 500.0


class TestConfidence:

    def test_confidence_bounded(self):
        conf = NewsDrivenStrategy._calc_confidence(0.5, 5000, 0.15)
        assert 0.15 <= conf <= 1.0

    def test_bigger_score_higher_confidence(self):
        c1 = NewsDrivenStrategy._calc_confidence(1.0, 5000, 0.15)
        c2 = NewsDrivenStrategy._calc_confidence(0.3, 5000, 0.15)
        assert c1 >= c2

    def test_more_volume_higher_confidence(self):
        c1 = NewsDrivenStrategy._calc_confidence(0.5, 10000, 0.15)
        c2 = NewsDrivenStrategy._calc_confidence(0.5, 1000, 0.15)
        assert c1 >= c2


class TestStrategyConfig:

    def test_default_params(self):
        s = NewsDrivenStrategy()
        assert s.parameters["price_move_threshold"] == 0.06
        assert s.parameters["volume_spike_mult"] == 2.5
        assert s.parameters["min_volume"] == 100
        assert s.parameters["max_spread"] == 0.12
        assert s.parameters["position_size_usd"] == 500.0
        assert s.asset_class.value == "predictions"
        assert s.tier == StrategyTier.SNIPER
        assert s.timeframe == "realtime"
        assert s.max_signals_per_cycle == 2

    def test_custom_params(self):
        s = NewsDrivenStrategy(parameters={"max_signals": 5})
        assert s.parameters["max_signals"] == 5
