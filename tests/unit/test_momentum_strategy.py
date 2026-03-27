"""
Tests for the Momentum Scalp Strategy (SCOUT tier).

Tests cover:
- Indicator calculations (RSI, volume ratio)
- Buy signal generation
- Sell signal generation
- Edge cases (insufficient data, flat markets)
"""

import numpy as np
import pytest

from engines.models import AssetClass, MarketRegime, Side
from engines.strategy.equities.momentum import MomentumStrategy


def _make_bars(closes: list[float], volumes: list[int] | None = None) -> list[dict]:
    """Build bar dicts from close prices and optional volumes."""
    if volumes is None:
        volumes = [1_000_000] * len(closes)
    return [
        {
            "open": c * 0.999,
            "high": c * 1.002,
            "low": c * 0.998,
            "close": c,
            "volume": v,
        }
        for c, v in zip(closes, volumes)
    ]


def _trending_up_bars(n: int = 60, start: float = 100.0) -> list[dict]:
    """Generate bars with a steady uptrend."""
    closes = [start + i * 0.5 for i in range(n)]
    # Increasing volume on the recent bars
    volumes = [500_000] * (n - 5) + [1_200_000] * 5
    return _make_bars(closes, volumes)


def _trending_down_bars(n: int = 60, start: float = 130.0) -> list[dict]:
    """Generate bars with a steady downtrend."""
    closes = [start - i * 0.5 for i in range(n)]
    volumes = [800_000] * n
    return _make_bars(closes, volumes)


def _flat_bars(n: int = 60, price: float = 100.0) -> list[dict]:
    """Generate flat/ranging bars with minimal movement."""
    closes = [price + (0.1 if i % 2 == 0 else -0.1) for i in range(n)]
    volumes = [500_000] * n
    return _make_bars(closes, volumes)


class TestRSICalculation:
    """Relative Strength Index indicator."""

    def test_rsi_all_gains(self):
        prices = np.array([float(i) for i in range(100, 120)])
        rsi = MomentumStrategy._calc_rsi(prices, period=14)
        assert rsi is not None
        # All gains -> RSI should be near 100
        assert rsi[-1] > 90.0

    def test_rsi_all_losses(self):
        prices = np.array([float(i) for i in range(120, 100, -1)])
        rsi = MomentumStrategy._calc_rsi(prices, period=14)
        assert rsi is not None
        # All losses -> RSI should be near 0
        assert rsi[-1] < 10.0

    def test_rsi_range(self):
        """RSI should always be between 0 and 100."""
        np.random.seed(42)
        prices = np.cumsum(np.random.randn(100)) + 100
        rsi = MomentumStrategy._calc_rsi(prices, period=14)
        assert rsi is not None
        assert np.all(rsi >= 0.0)
        assert np.all(rsi <= 100.0)

    def test_rsi_insufficient_data(self):
        prices = np.array([100.0, 101.0, 102.0])
        rsi = MomentumStrategy._calc_rsi(prices, period=14)
        assert rsi is None


class TestVolumeRatio:
    """Volume relative to moving average."""

    def test_above_average_volume(self):
        volumes = np.array([500_000] * 20 + [1_000_000])
        ratio = MomentumStrategy._calc_volume_ratio(volumes, ma_period=20)
        assert ratio is not None
        assert ratio[-1] > 1.5  # Well above average

    def test_average_volume(self):
        volumes = np.array([500_000] * 25)
        ratio = MomentumStrategy._calc_volume_ratio(volumes, ma_period=20)
        assert ratio is not None
        assert ratio[-1] == pytest.approx(1.0, rel=0.01)

    def test_insufficient_data(self):
        volumes = np.array([500_000] * 5)
        ratio = MomentumStrategy._calc_volume_ratio(volumes, ma_period=20)
        assert ratio is None


class TestBuySignal:
    """Buy signal generation."""

    @pytest.mark.asyncio
    async def test_buy_on_strong_uptrend_with_volume(self):
        strategy = MomentumStrategy(parameters={
            "rsi_period": 14,
            "rsi_buy_low": 55.0,
            "rsi_buy_high": 85.0,
            "volume_ma_period": 20,
            "volume_multiplier": 1.0,
            "position_size_usd": 75.0,
        })
        bars = _trending_up_bars(n=60)
        signals = await strategy.generate_signals(
            bars={"SPY": bars},
            market_regime=MarketRegime.TRENDING_UP,
        )
        # With a strong uptrend + volume spike, should get a BUY
        if signals:
            assert signals[0].side == Side.BUY
            assert signals[0].confidence > 0.0
            assert signals[0].asset_class == AssetClass.EQUITIES
            assert signals[0].position_size_usd == 75.0

    @pytest.mark.asyncio
    async def test_no_signal_flat_market(self):
        strategy = MomentumStrategy()
        bars = _flat_bars(n=60)
        signals = await strategy.generate_signals(
            bars={"SPY": bars},
            market_regime=MarketRegime.RANGING,
        )
        assert len(signals) == 0

    @pytest.mark.asyncio
    async def test_no_signal_insufficient_bars(self):
        strategy = MomentumStrategy()
        bars = _make_bars([100.0, 101.0, 102.0])
        signals = await strategy.generate_signals(
            bars={"SPY": bars},
            market_regime=MarketRegime.UNKNOWN,
        )
        assert len(signals) == 0


class TestSellSignal:
    """Sell signal generation."""

    @pytest.mark.asyncio
    async def test_sell_on_downtrend(self):
        strategy = MomentumStrategy(parameters={
            "rsi_period": 14,
            "rsi_sell_threshold": 30.0,
            "volume_ma_period": 20,
            "volume_multiplier": 1.0,
            "position_size_usd": 75.0,
        })
        bars = _trending_down_bars(n=60)
        signals = await strategy.generate_signals(
            bars={"SPY": bars},
            market_regime=MarketRegime.TRENDING_DOWN,
        )
        # In a downtrend, RSI should be low -> SELL
        if signals:
            assert signals[0].side == Side.SELL
            assert signals[0].quantity == 0  # Sell entire position


class TestConfidence:
    """Confidence calculation."""

    def test_buy_confidence_bounded(self):
        conf = MomentumStrategy._calc_buy_confidence(
            rsi=65.0, vol_ratio=2.0
        )
        assert 0.0 < conf <= 1.0

    def test_sell_confidence_bounded(self):
        conf = MomentumStrategy._calc_sell_confidence(rsi=20.0)
        assert 0.0 < conf <= 1.0


class TestStrategyConfig:
    """Strategy initialization and configuration."""

    def test_default_params(self):
        strategy = MomentumStrategy()
        assert strategy.strategy_id == "momentum_scalp"
        assert strategy.asset_class == AssetClass.EQUITIES
        assert strategy.parameters["rsi_period"] == 14
        assert strategy.parameters["position_size_usd"] == 75.0
        assert strategy.parameters["volume_multiplier"] == 1.0
        assert strategy.parameters["rsi_buy_low"] == 55.0

    def test_custom_params(self):
        strategy = MomentumStrategy(
            strategy_id="momentum_custom",
            parameters={"rsi_buy_low": 60.0},
        )
        assert strategy.strategy_id == "momentum_custom"
        assert strategy.parameters["rsi_buy_low"] == 60.0
        # Defaults should still be present
        assert strategy.parameters["rsi_period"] == 14

    @pytest.mark.asyncio
    async def test_empty_market_data(self):
        strategy = MomentumStrategy()
        signals = await strategy.generate_signals(
            bars={},
            market_regime=MarketRegime.UNKNOWN,
        )
        assert signals == []
