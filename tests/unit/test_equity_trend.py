"""
Tests for the Equity Trend Following Strategy.

Tests cover:
- Buy signal in clear uptrend
- No signal in flat/ranging market
- CORE tier with EQUITIES asset class
- Indicator calculations (EMA, ADX)
- Sell signal on downtrend
- Edge cases (insufficient data, empty bars)
"""

import numpy as np
import pytest

from config.tiers import StrategyTier
from engines.models import AssetClass, MarketRegime, Side
from engines.strategy.equities.trend_following import EquityTrendFollowingStrategy


def _make_bars(
    closes: list[float],
    highs: list[float] | None = None,
    lows: list[float] | None = None,
    volumes: list[float] | None = None,
) -> list[dict]:
    """Build OHLCV bar dicts."""
    n = len(closes)
    if highs is None:
        highs = [c * 1.01 for c in closes]
    if lows is None:
        lows = [c * 0.99 for c in closes]
    if volumes is None:
        volumes = [100_000.0] * n
    return [
        {
            "open": (h + l) / 2,
            "high": h,
            "low": l,
            "close": c,
            "volume": v,
        }
        for c, h, l, v in zip(closes, highs, lows, volumes)
    ]


def _strong_uptrend_bars(n: int = 80, start: float = 450.0) -> list[dict]:
    """Generate bars with a strong, consistent uptrend (equity-style)."""
    step = start * 0.005  # 0.5% per bar
    closes = [start + i * step for i in range(n)]
    highs = [c * 1.008 for c in closes]
    lows = [c * 0.995 for c in closes]
    return _make_bars(closes, highs, lows)


def _strong_downtrend_bars(n: int = 80, start: float = 550.0) -> list[dict]:
    """Generate bars with a strong downtrend."""
    step = start * 0.005
    closes = [start - i * step for i in range(n)]
    highs = [c * 1.005 for c in closes]
    lows = [c * 0.992 for c in closes]
    return _make_bars(closes, highs, lows)


def _ranging_bars(n: int = 80, center: float = 500.0) -> list[dict]:
    """Generate bars that oscillate around a center — no trend."""
    closes = [center + 2.0 * np.sin(i * 0.3) for i in range(n)]
    highs = [c + 1.0 for c in closes]
    lows = [c - 1.0 for c in closes]
    return _make_bars(closes, highs, lows)


class TestBuySignal:
    """Buy signal generation in uptrends."""

    @pytest.mark.asyncio
    async def test_buy_on_strong_uptrend(self):
        """Rising prices over 60+ bars should produce a BUY signal."""
        strategy = EquityTrendFollowingStrategy()
        bars = _strong_uptrend_bars(n=80)
        signals = await strategy.generate_signals(
            bars={"SPY": bars},
            market_regime=MarketRegime.TRENDING_UP,
        )
        assert len(signals) >= 1
        signal = signals[0]
        assert signal.side == Side.BUY
        assert signal.symbol == "SPY"
        assert signal.asset_class == AssetClass.EQUITIES
        assert signal.stop_loss is not None
        assert signal.stop_loss < signal.target_price
        assert signal.strength is not None
        assert signal.rationale != ""
        assert signal.position_size_usd == 300.0
        assert signal.tier == StrategyTier.CORE

    @pytest.mark.asyncio
    async def test_buy_signal_has_stop_loss(self):
        """Stop loss should be set at stop_loss_pct below entry."""
        strategy = EquityTrendFollowingStrategy()
        bars = _strong_uptrend_bars(n=80)
        signals = await strategy.generate_signals(
            bars={"SPY": bars},
            market_regime=MarketRegime.TRENDING_UP,
        )
        if signals:
            signal = signals[0]
            expected_stop = signal.target_price * (1.0 - 3.0 / 100.0)
            assert abs(signal.stop_loss - expected_stop) < 0.01


class TestNoSignal:
    """Cases where no signal should be generated."""

    @pytest.mark.asyncio
    async def test_no_signal_flat_market(self):
        """Flat/ranging market should produce no BUY signal."""
        strategy = EquityTrendFollowingStrategy()
        bars = _ranging_bars(n=80)
        signals = await strategy.generate_signals(
            bars={"SPY": bars},
            market_regime=MarketRegime.RANGING,
        )
        # In a ranging market, either no signal or a sell
        buy_signals = [s for s in signals if s.side == Side.BUY]
        assert len(buy_signals) == 0

    @pytest.mark.asyncio
    async def test_no_signal_insufficient_bars(self):
        strategy = EquityTrendFollowingStrategy()
        bars = _make_bars([450.0, 451.0, 452.0])
        signals = await strategy.generate_signals(
            bars={"SPY": bars},
            market_regime=MarketRegime.UNKNOWN,
        )
        assert len(signals) == 0

    @pytest.mark.asyncio
    async def test_empty_bars(self):
        strategy = EquityTrendFollowingStrategy()
        signals = await strategy.generate_signals(
            bars={},
            market_regime=MarketRegime.UNKNOWN,
        )
        assert signals == []


class TestSellSignal:
    """Sell signal generation."""

    @pytest.mark.asyncio
    async def test_sell_on_downtrend(self):
        strategy = EquityTrendFollowingStrategy()
        bars = _strong_downtrend_bars(n=80)
        signals = await strategy.generate_signals(
            bars={"SPY": bars},
            market_regime=MarketRegime.TRENDING_DOWN,
        )
        if signals:
            assert signals[0].side == Side.SELL
            assert signals[0].quantity == 0  # Full exit


class TestStrategyConfig:
    """Strategy initialization and tier configuration."""

    def test_is_core_tier(self):
        strategy = EquityTrendFollowingStrategy()
        assert strategy.tier == StrategyTier.CORE

    def test_is_equities_asset_class(self):
        strategy = EquityTrendFollowingStrategy()
        assert strategy.asset_class == AssetClass.EQUITIES

    def test_default_params(self):
        strategy = EquityTrendFollowingStrategy()
        assert strategy.strategy_id == "trend_equities"
        assert strategy.parameters["fast_ema_period"] == 12
        assert strategy.parameters["slow_ema_period"] == 26
        assert strategy.parameters["adx_period"] == 14
        assert strategy.parameters["adx_trend_threshold"] == 20.0
        assert strategy.parameters["adx_fade_threshold"] == 15.0
        assert strategy.parameters["position_size_usd"] == 300.0
        assert strategy.parameters["stop_loss_pct"] == 3.0
        assert strategy.symbols == ["SPY", "QQQ", "NVDA", "IWM"]
        assert strategy.timeframe == "4Hour"
        assert strategy.max_signals_per_cycle == 2

    def test_custom_params(self):
        strategy = EquityTrendFollowingStrategy(
            strategy_id="trend_spy_only",
            parameters={"position_size_usd": 500.0},
        )
        assert strategy.parameters["position_size_usd"] == 500.0
        assert strategy.parameters["adx_period"] == 14  # Default preserved


class TestIndicators:
    """EMA and ADX calculations."""

    def test_ema_basic(self):
        prices = np.array([float(i) for i in range(1, 21)])
        ema = EquityTrendFollowingStrategy._calc_ema(prices, period=5)
        assert ema is not None
        assert len(ema) == 16  # 20 - 5 + 1

    def test_ema_insufficient_data(self):
        prices = np.array([100.0, 101.0])
        ema = EquityTrendFollowingStrategy._calc_ema(prices, period=5)
        assert ema is None

    def test_adx_strong_trend(self):
        bars = _strong_uptrend_bars(n=80)
        highs = np.array([b["high"] for b in bars])
        lows = np.array([b["low"] for b in bars])
        closes = np.array([b["close"] for b in bars])
        adx = EquityTrendFollowingStrategy._calc_adx(highs, lows, closes, period=14)
        assert adx is not None
        assert adx[-1] > 20.0

    def test_adx_insufficient_data(self):
        highs = np.array([100.0] * 10)
        lows = np.array([99.0] * 10)
        closes = np.array([99.5] * 10)
        adx = EquityTrendFollowingStrategy._calc_adx(highs, lows, closes, period=14)
        assert adx is None


class TestMultiSymbol:
    """Multi-symbol support."""

    @pytest.mark.asyncio
    async def test_processes_multiple_symbols(self):
        strategy = EquityTrendFollowingStrategy()
        bars = _strong_uptrend_bars(n=80)
        signals = await strategy.generate_signals(
            bars={"SPY": bars, "QQQ": bars},
            market_regime=MarketRegime.TRENDING_UP,
        )
        assert len(signals) <= strategy.max_signals_per_cycle
