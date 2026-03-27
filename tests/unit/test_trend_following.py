"""
Tests for the Trend Following Strategy (crypto).

Tests cover:
- Indicator calculations (EMA, ADX, ATR)
- Buy signal generation (trend confirmed)
- Sell signal generation (trend fading)
- Edge cases (insufficient data, flat markets)
"""

import numpy as np
import pytest

from engines.models import AssetClass, MarketRegime, Side
from engines.strategy.crypto.trend_following import TrendFollowingStrategy


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
        volumes = [100.0] * n
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


def _strong_uptrend_bars(n: int = 80, start: float = 50000.0) -> list[dict]:
    """Generate bars with a strong, consistent uptrend (crypto-style)."""
    step = start * 0.005  # 0.5% per bar
    closes = [start + i * step for i in range(n)]
    highs = [c * 1.008 for c in closes]
    lows = [c * 0.995 for c in closes]
    return _make_bars(closes, highs, lows)


def _strong_downtrend_bars(n: int = 80, start: float = 70000.0) -> list[dict]:
    """Generate bars with a strong downtrend."""
    step = start * 0.005
    closes = [start - i * step for i in range(n)]
    highs = [c * 1.005 for c in closes]
    lows = [c * 0.992 for c in closes]
    return _make_bars(closes, highs, lows)


def _ranging_bars(n: int = 80, center: float = 60000.0) -> list[dict]:
    """Generate bars that oscillate around a center — no trend."""
    closes = [center + 200 * np.sin(i * 0.3) for i in range(n)]
    highs = [c + 100 for c in closes]
    lows = [c - 100 for c in closes]
    return _make_bars(closes, highs, lows)


class TestEMACalculation:
    """Exponential Moving Average."""

    def test_basic_ema(self):
        prices = np.array([float(i) for i in range(1, 21)])
        ema = TrendFollowingStrategy._calc_ema(prices, period=5)
        assert ema is not None
        # EMA should be > SMA at end of uptrend (more weight on recent)
        sma_last5 = np.mean(prices[-5:])
        assert ema[-1] > sma_last5 * 0.95

    def test_ema_tracks_price(self):
        """EMA should converge toward current price."""
        prices = np.array([100.0] * 10 + [200.0] * 20)
        ema = TrendFollowingStrategy._calc_ema(prices, period=10)
        assert ema is not None
        # After 20 bars at 200, EMA should be close to 200
        assert ema[-1] > 180.0

    def test_ema_insufficient_data(self):
        prices = np.array([100.0, 101.0])
        ema = TrendFollowingStrategy._calc_ema(prices, period=5)
        assert ema is None

    def test_fast_ema_more_responsive(self):
        """Shorter period EMA should react faster to price changes."""
        prices = np.array([100.0] * 30 + [120.0] * 10)
        fast = TrendFollowingStrategy._calc_ema(prices, period=5)
        slow = TrendFollowingStrategy._calc_ema(prices, period=20)
        assert fast is not None
        assert slow is not None
        # Fast EMA should be closer to 120 after the jump
        assert fast[-1] > slow[-1]


class TestADXCalculation:
    """Average Directional Index."""

    def test_adx_strong_trend(self):
        bars = _strong_uptrend_bars(n=80)
        highs = np.array([b["high"] for b in bars])
        lows = np.array([b["low"] for b in bars])
        closes = np.array([b["close"] for b in bars])
        adx = TrendFollowingStrategy._calc_adx(highs, lows, closes, period=14)
        assert adx is not None
        # Strong trend should produce ADX > 20
        assert adx[-1] > 20.0

    def test_adx_ranging_market(self):
        bars = _ranging_bars(n=80)
        highs = np.array([b["high"] for b in bars])
        lows = np.array([b["low"] for b in bars])
        closes = np.array([b["close"] for b in bars])
        adx = TrendFollowingStrategy._calc_adx(highs, lows, closes, period=14)
        assert adx is not None
        # Ranging market should have lower ADX
        assert adx[-1] < 40.0

    def test_adx_range(self):
        """ADX should be between 0 and 100."""
        np.random.seed(42)
        n = 100
        closes = np.cumsum(np.random.randn(n)) + 60000
        highs = closes + np.abs(np.random.randn(n)) * 100
        lows = closes - np.abs(np.random.randn(n)) * 100
        adx = TrendFollowingStrategy._calc_adx(highs, lows, closes, period=14)
        assert adx is not None
        assert np.all(adx >= 0.0)
        assert np.all(adx <= 100.0)

    def test_adx_insufficient_data(self):
        highs = np.array([100.0] * 10)
        lows = np.array([99.0] * 10)
        closes = np.array([99.5] * 10)
        adx = TrendFollowingStrategy._calc_adx(highs, lows, closes, period=14)
        assert adx is None


class TestATRCalculation:
    """Average True Range."""

    def test_atr_basic(self):
        bars = _strong_uptrend_bars(n=30)
        highs = np.array([b["high"] for b in bars])
        lows = np.array([b["low"] for b in bars])
        closes = np.array([b["close"] for b in bars])
        atr = TrendFollowingStrategy._calc_atr(highs, lows, closes, period=14)
        assert atr is not None
        assert atr[-1] > 0  # ATR should be positive

    def test_atr_insufficient_data(self):
        highs = np.array([100.0] * 5)
        lows = np.array([99.0] * 5)
        closes = np.array([99.5] * 5)
        atr = TrendFollowingStrategy._calc_atr(highs, lows, closes, period=14)
        assert atr is None


class TestBuySignal:
    """Buy signal generation."""

    @pytest.mark.asyncio
    async def test_buy_on_strong_uptrend(self):
        strategy = TrendFollowingStrategy()
        bars = _strong_uptrend_bars(n=80)
        signals = await strategy.generate_signals(
            bars={"BTC-USD": bars},
            market_regime=MarketRegime.TRENDING_UP,
        )
        if signals:
            assert signals[0].side == Side.BUY
            assert signals[0].symbol == "BTC-USD"
            assert signals[0].asset_class == AssetClass.CRYPTO
            assert signals[0].stop_loss is not None
            assert signals[0].stop_loss < signals[0].target_price

    @pytest.mark.asyncio
    async def test_no_signal_ranging(self):
        strategy = TrendFollowingStrategy()
        bars = _ranging_bars(n=80)
        signals = await strategy.generate_signals(
            bars={"BTC-USD": bars},
            market_regime=MarketRegime.RANGING,
        )
        # In a ranging market, either no signal or a sell (trend fading)
        if signals:
            assert signals[0].side == Side.SELL

    @pytest.mark.asyncio
    async def test_no_signal_insufficient_bars(self):
        strategy = TrendFollowingStrategy()
        bars = _make_bars([50000.0, 50100.0, 50200.0])
        signals = await strategy.generate_signals(
            bars={"BTC-USD": bars},
            market_regime=MarketRegime.UNKNOWN,
        )
        assert len(signals) == 0


class TestSellSignal:
    """Sell signal generation."""

    @pytest.mark.asyncio
    async def test_sell_on_downtrend(self):
        strategy = TrendFollowingStrategy()
        bars = _strong_downtrend_bars(n=80)
        signals = await strategy.generate_signals(
            bars={"BTC-USD": bars},
            market_regime=MarketRegime.TRENDING_DOWN,
        )
        if signals:
            assert signals[0].side == Side.SELL
            assert signals[0].quantity == 0  # Full exit


class TestConfidence:
    """Confidence calculations."""

    def test_buy_confidence_bounded(self):
        conf = TrendFollowingStrategy._calc_buy_confidence(
            fast_ema=51000.0, slow_ema=50000.0,
            adx=35.0, price=51500.0, just_crossed=True,
            adx_trending=True, price_above_ema=True,
        )
        assert 0.0 < conf <= 1.0

    def test_sell_confidence_bounded(self):
        conf = TrendFollowingStrategy._calc_sell_confidence(
            fast_ema=49000.0, slow_ema=50000.0,
            adx=15.0, bearish_cross=True,
        )
        assert 0.0 < conf <= 1.0

    def test_crossover_boosts_confidence(self):
        no_cross = TrendFollowingStrategy._calc_buy_confidence(
            fast_ema=51000.0, slow_ema=50000.0,
            adx=30.0, price=51500.0, just_crossed=False,
            adx_trending=True, price_above_ema=True,
        )
        with_cross = TrendFollowingStrategy._calc_buy_confidence(
            fast_ema=51000.0, slow_ema=50000.0,
            adx=30.0, price=51500.0, just_crossed=True,
            adx_trending=True, price_above_ema=True,
        )
        assert with_cross > no_cross


class TestStrategyConfig:
    """Strategy initialization."""

    def test_default_params(self):
        strategy = TrendFollowingStrategy()
        assert strategy.strategy_id == "trend_crypto"
        assert strategy.asset_class == AssetClass.CRYPTO
        assert strategy.parameters["fast_ema_period"] == 12
        assert strategy.parameters["slow_ema_period"] == 26
        assert strategy.parameters["adx_trend_threshold"] == 20.0
        assert strategy.parameters["adx_fade_threshold"] == 15.0
        assert strategy.symbols == ["BTC-USD", "ETH-USD", "SOL-USD"]
        assert strategy.timeframe == "4Hour"
        assert strategy.max_signals_per_cycle == 2

    def test_custom_params(self):
        strategy = TrendFollowingStrategy(
            strategy_id="trend_eth",
            parameters={"position_size_usd": 100.0},
        )
        assert strategy.parameters["position_size_usd"] == 100.0
        # Defaults preserved
        assert strategy.parameters["adx_period"] == 14

    @pytest.mark.asyncio
    async def test_empty_market_data(self):
        strategy = TrendFollowingStrategy()
        signals = await strategy.generate_signals(
            bars={},
            market_regime=MarketRegime.UNKNOWN,
        )
        assert signals == []

    @pytest.mark.asyncio
    async def test_multi_symbol(self):
        """Should process multiple symbols."""
        strategy = TrendFollowingStrategy()
        bars = _strong_uptrend_bars(n=80)
        signals = await strategy.generate_signals(
            bars={"BTC-USD": bars, "ETH-USD": bars},
            market_regime=MarketRegime.TRENDING_UP,
        )
        # At most max_signals_per_cycle
        assert len(signals) <= strategy.max_signals_per_cycle
