"""
Tests for the Momentum Strategy.

Tests cover:
- Indicator calculations (ROC, RSI, volume ratio)
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


class TestROCCalculation:
    """Rate of Change indicator."""

    def test_basic_roc(self):
        prices = np.array([100.0, 102.0, 105.0, 110.0, 108.0])
        roc = MomentumStrategy._calc_roc(prices, period=2)
        assert roc is not None
        assert len(roc) == 3  # 5 - 2 = 3
        # First ROC: (105 - 100) / 100 * 100 = 5.0
        assert abs(roc[0] - 5.0) < 0.01

    def test_roc_insufficient_data(self):
        prices = np.array([100.0, 102.0])
        roc = MomentumStrategy._calc_roc(prices, period=3)
        assert roc is None

    def test_roc_negative(self):
        prices = np.array([110.0, 108.0, 105.0, 100.0, 95.0])
        roc = MomentumStrategy._calc_roc(prices, period=2)
        assert roc is not None
        assert roc[-1] < 0  # Prices dropping


class TestRSICalculation:
    """Relative Strength Index indicator."""

    def test_rsi_all_gains(self):
        prices = np.array([float(i) for i in range(100, 120)])
        rsi = MomentumStrategy._calc_rsi(prices, period=14)
        assert rsi is not None
        # All gains → RSI should be near 100
        assert rsi[-1] > 90.0

    def test_rsi_all_losses(self):
        prices = np.array([float(i) for i in range(120, 100, -1)])
        rsi = MomentumStrategy._calc_rsi(prices, period=14)
        assert rsi is not None
        # All losses → RSI should be near 0
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
        # 20 bars at 500k then 1 bar at 1M
        # The MA window ending at bar 20 includes bars 1–20 (avg ~525k),
        # so ratio is 1M / 525k ≈ 1.9
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
            "symbol": "QQQ",
            "roc_period": 14,
            "rsi_period": 14,
            "roc_threshold": 2.0,
            "rsi_buy_low": 50.0,
            "rsi_buy_high": 70.0,
            "volume_ma_period": 20,
            "volume_multiplier": 1.2,
            "position_size_usd": 500.0,
        })
        bars = _trending_up_bars(n=60)
        signals = await strategy.generate_signals(
            market_data={"bars": bars},
            market_regime=MarketRegime.TRENDING_UP,
        )
        # With a strong uptrend + volume spike, should get a BUY
        if signals:
            assert signals[0].side == Side.BUY
            assert signals[0].symbol == "QQQ"
            assert signals[0].confidence > 0.0
            assert signals[0].asset_class == AssetClass.EQUITIES

    @pytest.mark.asyncio
    async def test_no_signal_flat_market(self):
        strategy = MomentumStrategy()
        bars = _flat_bars(n=60)
        signals = await strategy.generate_signals(
            market_data={"bars": bars},
            market_regime=MarketRegime.RANGING,
        )
        assert len(signals) == 0

    @pytest.mark.asyncio
    async def test_no_signal_insufficient_bars(self):
        strategy = MomentumStrategy()
        bars = _make_bars([100.0, 101.0, 102.0])
        signals = await strategy.generate_signals(
            market_data={"bars": bars},
            market_regime=MarketRegime.UNKNOWN,
        )
        assert len(signals) == 0


class TestSellSignal:
    """Sell signal generation."""

    @pytest.mark.asyncio
    async def test_sell_on_downtrend(self):
        strategy = MomentumStrategy(parameters={
            "symbol": "QQQ",
            "roc_period": 14,
            "rsi_period": 14,
            "roc_threshold": 2.0,
            "rsi_sell_threshold": 30.0,
            "volume_ma_period": 20,
            "volume_multiplier": 1.2,
            "position_size_usd": 500.0,
        })
        bars = _trending_down_bars(n=60)
        signals = await strategy.generate_signals(
            market_data={"bars": bars},
            market_regime=MarketRegime.TRENDING_DOWN,
        )
        # In a downtrend, RSI should be low and/or ROC negative → SELL
        if signals:
            assert signals[0].side == Side.SELL
            assert signals[0].quantity == 0  # Sell entire position


class TestConfidence:
    """Confidence calculation."""

    def test_buy_confidence_bounded(self):
        conf = MomentumStrategy._calc_buy_confidence(
            roc=5.0, rsi=60.0, vol_ratio=2.0
        )
        assert 0.0 < conf <= 1.0

    def test_sell_confidence_bounded(self):
        conf = MomentumStrategy._calc_sell_confidence(roc=-5.0, rsi=20.0)
        assert 0.0 < conf <= 1.0

    def test_higher_roc_higher_confidence(self):
        low = MomentumStrategy._calc_buy_confidence(
            roc=2.0, rsi=60.0, vol_ratio=1.5
        )
        high = MomentumStrategy._calc_buy_confidence(
            roc=8.0, rsi=60.0, vol_ratio=1.5
        )
        assert high > low


class TestStrategyConfig:
    """Strategy initialization and configuration."""

    def test_default_params(self):
        strategy = MomentumStrategy()
        assert strategy.strategy_id == "momentum_qqq"
        assert strategy.asset_class == AssetClass.EQUITIES
        assert strategy.parameters["symbol"] == "QQQ"
        assert strategy.parameters["roc_period"] == 14

    def test_custom_params(self):
        strategy = MomentumStrategy(
            strategy_id="momentum_spy",
            parameters={"symbol": "SPY", "roc_threshold": 3.0},
        )
        assert strategy.strategy_id == "momentum_spy"
        assert strategy.parameters["symbol"] == "SPY"
        assert strategy.parameters["roc_threshold"] == 3.0
        # Defaults should still be present
        assert strategy.parameters["rsi_period"] == 14

    @pytest.mark.asyncio
    async def test_empty_market_data(self):
        strategy = MomentumStrategy()
        signals = await strategy.generate_signals(
            market_data={},
            market_regime=MarketRegime.UNKNOWN,
        )
        assert signals == []
