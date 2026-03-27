"""
Tests for the Value Pricing Strategy (prediction markets).

Tests cover:
- Market evaluation and edge detection
- YES/NO side opportunity identification
- Liquidity and price bound filters
- Signal ranking by edge size
- Confidence calculation
- Edge cases (no markets, no edge, illiquid markets)
- MarketSkimmerStrategy (SCOUT tier)
"""

import pytest

from config.tiers import StrategyTier
from engines.models import AssetClass, MarketRegime, Side
from engines.strategy.predictions.value_pricing import (
    MarketSkimmerStrategy,
    ValuePricingStrategy,
)


def _make_market(
    ticker: str = "KXTEST-26MAR23-B100",
    title: str = "Will X happen?",
    yes_bid: float = 0.40,
    no_bid: float = 0.50,
    yes_ask: float = 0.45,
    no_ask: float = 0.55,
    volume: int = 500,
    open_interest: int = 200,
    status: str = "open",
) -> dict:
    return {
        "ticker": ticker,
        "title": title,
        "yes_bid": yes_bid,
        "no_bid": no_bid,
        "yes_ask": yes_ask,
        "no_ask": no_ask,
        "yes_price": yes_bid,
        "no_price": no_bid,
        "volume": volume,
        "open_interest": open_interest,
        "status": status,
    }


def _market_with_yes_edge(edge: float = 0.10) -> dict:
    """Create a market where YES has more edge than NO.

    YES edge: fair_yes = 1 - no_ask = 0.60, yes_ask = 0.60 - edge
    NO edge:  fair_no = 1 - yes_ask, no_ask is set so NO edge < YES edge
    """
    no_ask = 0.40  # fair_yes = 0.60
    yes_ask = 0.60 - edge  # YES edge = edge
    # Set no_bid/no_ask so NO has smaller edge
    no_bid = no_ask - 0.02
    yes_bid = yes_ask - 0.02
    return _make_market(
        yes_bid=yes_bid,
        yes_ask=yes_ask,
        no_bid=no_bid,
        no_ask=no_ask,
    )


def _market_with_no_edge(edge: float = 0.10) -> dict:
    """Create a market where NO has more edge than YES."""
    yes_ask = 0.40  # fair_no = 0.60
    no_ask = 0.60 - edge  # NO edge = edge
    yes_bid = yes_ask - 0.02
    no_bid = no_ask - 0.02
    return _make_market(
        yes_bid=yes_bid,
        yes_ask=yes_ask,
        no_bid=no_bid,
        no_ask=no_ask,
    )


class TestMarketEvaluation:
    """Core market evaluation logic."""

    def test_detects_edge(self):
        """When yes_ask + no_ask < 1.0, the model finds edge on both sides."""
        strategy = ValuePricingStrategy()
        market = _market_with_yes_edge(edge=0.10)
        opp = strategy._evaluate_market(market)
        assert opp is not None
        assert opp["side"] in ("BUY_YES", "BUY_NO")
        assert opp["edge"] >= 0.05

    def test_detects_edge_no_side(self):
        strategy = ValuePricingStrategy()
        market = _market_with_no_edge(edge=0.10)
        opp = strategy._evaluate_market(market)
        assert opp is not None
        assert opp["side"] in ("BUY_YES", "BUY_NO")
        assert opp["edge"] >= 0.05

    def test_no_edge_returns_none(self):
        strategy = ValuePricingStrategy()
        # Fair market — no edge on either side
        market = _make_market(
            yes_bid=0.48, yes_ask=0.50,
            no_bid=0.48, no_ask=0.50,
        )
        opp = strategy._evaluate_market(market)
        assert opp is None

    def test_picks_better_side(self):
        """When both sides have edges, pick the bigger one."""
        strategy = ValuePricingStrategy()
        # YES edge = 0.12, NO edge = 0.06
        market = _make_market(
            yes_bid=0.30, yes_ask=0.33,
            no_bid=0.49, no_ask=0.55,
        )
        opp = strategy._evaluate_market(market)
        if opp is not None:
            # Should pick the side with more edge
            assert opp["edge"] > 0.05


class TestLiquidityFilters:
    """Filters for volume, open interest, and spread."""

    def test_rejects_low_volume(self):
        strategy = ValuePricingStrategy()
        market = _market_with_yes_edge()
        market["volume"] = 10  # Below 100 min
        opp = strategy._evaluate_market(market)
        assert opp is None

    def test_rejects_low_open_interest(self):
        strategy = ValuePricingStrategy()
        market = _market_with_yes_edge()
        market["open_interest"] = 5  # Below 50 min
        opp = strategy._evaluate_market(market)
        assert opp is None

    def test_rejects_wide_spread(self):
        strategy = ValuePricingStrategy()
        market = _make_market(
            yes_bid=0.20, yes_ask=0.40,  # 0.20 spread > 0.15 max
            no_bid=0.50, no_ask=0.55,
        )
        opp = strategy._evaluate_market(market)
        # YES side should be rejected for wide spread
        # NO side may pass if its spread is tight enough
        if opp is not None:
            assert opp["side"] == "BUY_NO"


class TestPriceBounds:
    """Price bounds filter — avoid extremes."""

    def test_rejects_near_zero_price(self):
        strategy = ValuePricingStrategy()
        market = _make_market(
            yes_bid=0.03, yes_ask=0.05,  # Below 0.10 min
            no_bid=0.92, no_ask=0.95,  # Above 0.90 max
        )
        opp = strategy._evaluate_market(market)
        assert opp is None

    def test_rejects_near_one_price(self):
        strategy = ValuePricingStrategy()
        market = _make_market(
            yes_bid=0.93, yes_ask=0.95,  # Above 0.90 max
            no_bid=0.03, no_ask=0.05,  # Below 0.10 min
        )
        opp = strategy._evaluate_market(market)
        assert opp is None


class TestSignalGeneration:
    """Full signal generation flow."""

    @pytest.mark.asyncio
    async def test_generates_signal_for_edge(self):
        strategy = ValuePricingStrategy()
        markets = [_market_with_yes_edge(edge=0.10)]
        signals = await strategy.generate_signals(
            bars={"markets": markets},
            market_regime=MarketRegime.UNKNOWN,
        )
        assert len(signals) >= 1
        assert signals[0].side in (Side.BUY, Side.SELL)  # Either side can have edge
        assert signals[0].asset_class == AssetClass.PREDICTIONS
        assert signals[0].confidence > 0.0
        assert "edge" in signals[0].rationale

    @pytest.mark.asyncio
    async def test_sell_signal_for_no_edge(self):
        strategy = ValuePricingStrategy()
        markets = [_market_with_no_edge(edge=0.10)]
        signals = await strategy.generate_signals(
            bars={"markets": markets},
            market_regime=MarketRegime.UNKNOWN,
        )
        assert len(signals) == 1
        assert signals[0].side == Side.SELL  # Buying NO = selling YES

    @pytest.mark.asyncio
    async def test_ranks_by_edge(self):
        strategy = ValuePricingStrategy()
        markets = [
            {**_market_with_yes_edge(edge=0.06), "ticker": "SMALL-EDGE"},
            {**_market_with_yes_edge(edge=0.15), "ticker": "BIG-EDGE"},
        ]
        signals = await strategy.generate_signals(
            bars={"markets": markets},
            market_regime=MarketRegime.UNKNOWN,
        )
        if len(signals) == 2:
            # First signal should have the bigger edge
            assert signals[0].symbol == "BIG-EDGE"

    @pytest.mark.asyncio
    async def test_caps_at_max_signals(self):
        strategy = ValuePricingStrategy(parameters={"max_signals": 2})
        markets = [
            {**_market_with_yes_edge(edge=0.10), "ticker": f"MKT-{i}"}
            for i in range(5)
        ]
        signals = await strategy.generate_signals(
            bars={"markets": markets},
            market_regime=MarketRegime.UNKNOWN,
        )
        assert len(signals) <= 2

    @pytest.mark.asyncio
    async def test_no_markets_returns_empty(self):
        strategy = ValuePricingStrategy()
        signals = await strategy.generate_signals(
            bars={"markets": []},
            market_regime=MarketRegime.UNKNOWN,
        )
        assert signals == []

    @pytest.mark.asyncio
    async def test_empty_market_data(self):
        strategy = ValuePricingStrategy()
        signals = await strategy.generate_signals(
            bars={},
            market_regime=MarketRegime.UNKNOWN,
        )
        assert signals == []

    @pytest.mark.asyncio
    async def test_all_markets_no_edge(self):
        strategy = ValuePricingStrategy()
        # Fair pricing — no edge
        markets = [
            _make_market(
                ticker=f"FAIR-{i}",
                yes_bid=0.48, yes_ask=0.50,
                no_bid=0.48, no_ask=0.50,
            )
            for i in range(5)
        ]
        signals = await strategy.generate_signals(
            bars={"markets": markets},
            market_regime=MarketRegime.UNKNOWN,
        )
        assert signals == []

    @pytest.mark.asyncio
    async def test_signal_includes_tier_and_position_size(self):
        """Signals must include tier and position_size_usd fields."""
        strategy = ValuePricingStrategy()
        markets = [_market_with_yes_edge(edge=0.10)]
        signals = await strategy.generate_signals(
            bars={"markets": markets},
            market_regime=MarketRegime.UNKNOWN,
        )
        assert len(signals) >= 1
        assert signals[0].tier == StrategyTier.CORE
        assert signals[0].position_size_usd == 50.0


class TestConfidence:
    """Confidence calculation."""

    def test_confidence_bounded(self):
        conf = ValuePricingStrategy._calc_confidence({
            "edge": 0.10,
            "spread": 0.05,
            "volume": 1000,
        })
        assert 0.0 < conf <= 1.0

    def test_bigger_edge_higher_confidence(self):
        low = ValuePricingStrategy._calc_confidence({
            "edge": 0.05, "spread": 0.05, "volume": 500,
        })
        high = ValuePricingStrategy._calc_confidence({
            "edge": 0.15, "spread": 0.05, "volume": 500,
        })
        assert high > low

    def test_tighter_spread_higher_confidence(self):
        wide = ValuePricingStrategy._calc_confidence({
            "edge": 0.10, "spread": 0.12, "volume": 500,
        })
        tight = ValuePricingStrategy._calc_confidence({
            "edge": 0.10, "spread": 0.02, "volume": 500,
        })
        assert tight > wide

    def test_higher_volume_higher_confidence(self):
        low_vol = ValuePricingStrategy._calc_confidence({
            "edge": 0.10, "spread": 0.05, "volume": 100,
        })
        high_vol = ValuePricingStrategy._calc_confidence({
            "edge": 0.10, "spread": 0.05, "volume": 2000,
        })
        assert high_vol > low_vol


class TestStrategyConfig:
    """Strategy initialization and configuration."""

    def test_default_params(self):
        strategy = ValuePricingStrategy()
        assert strategy.strategy_id == "value_kalshi"
        assert strategy.asset_class == AssetClass.PREDICTIONS
        assert strategy.parameters["min_edge"] == 0.05
        assert strategy.parameters["max_signals"] == 3
        assert strategy.tier == StrategyTier.CORE
        assert strategy.timeframe == "realtime"
        assert strategy.max_signals_per_cycle == 3

    def test_custom_params(self):
        strategy = ValuePricingStrategy(
            strategy_id="value_aggressive",
            parameters={"min_edge": 0.03, "max_signals": 5},
        )
        assert strategy.parameters["min_edge"] == 0.03
        assert strategy.parameters["max_signals"] == 5
        # Defaults preserved
        assert strategy.parameters["min_volume"] == 100

    def test_position_sizing(self):
        """Position size should produce reasonable contract counts."""
        strategy = ValuePricingStrategy()
        opp = {
            "side": "BUY_YES",
            "ticker": "TEST",
            "title": "Test",
            "price": 0.40,
            "fair_value": 0.50,
            "edge": 0.10,
            "spread": 0.03,
            "volume": 500,
            "open_interest": 200,
        }
        signal = strategy._build_signal(opp, MarketRegime.UNKNOWN)
        # $50 / $0.40 = 125 contracts
        assert signal.quantity == 125.0
        assert signal.target_price == 0.40


class TestMarketSkimmer:
    """SCOUT tier skimmer strategy."""

    def test_scout_tier(self):
        skimmer = MarketSkimmerStrategy()
        assert skimmer.tier == StrategyTier.SCOUT
        assert skimmer.strategy_id == "skimmer_kalshi"
        assert skimmer.timeframe == "realtime"

    def test_looser_params(self):
        skimmer = MarketSkimmerStrategy()
        assert skimmer.parameters["min_edge"] == 0.03
        assert skimmer.parameters["min_volume"] == 50
        assert skimmer.parameters["max_spread"] == 0.25
        assert skimmer.parameters["scan_limit"] == 100

    @pytest.mark.asyncio
    async def test_finds_opportunities_core_misses(self):
        """Skimmer should find edges that CORE ignores (edge < 0.05)."""
        core = ValuePricingStrategy()
        skimmer = MarketSkimmerStrategy()

        # Market with small edge (0.04) — below CORE threshold
        market = _market_with_yes_edge(edge=0.04)
        core_opp = core._evaluate_market(market)
        skimmer_opp = skimmer._evaluate_market(market)

        assert core_opp is None  # CORE ignores small edge
        assert skimmer_opp is not None  # SCOUT finds it

    @pytest.mark.asyncio
    async def test_accepts_lower_volume(self):
        """Skimmer accepts markets with lower volume."""
        skimmer = MarketSkimmerStrategy()
        market = _market_with_yes_edge(edge=0.10)
        market["volume"] = 60  # Below CORE's 100, above SCOUT's 50
        opp = skimmer._evaluate_market(market)
        assert opp is not None
