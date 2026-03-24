"""
Unit tests for Mean Reversion strategy.
"""

import pytest
import numpy as np
from engines.models import MarketRegime, Side
from engines.strategy.equities.mean_reversion import MeanReversionStrategy


def make_bars_at_lower_band(n=60):
    """Generate bars where price drops to the lower Bollinger Band."""
    bars = []
    # Stable prices, then a sharp drop
    for i in range(n - 5):
        bars.append({"close": 100.0 + np.sin(i * 0.1) * 0.5, "high": 101.0, "low": 99.0, "volume": 1000000})
    # Sharp drop to trigger oversold
    for i in range(5):
        price = 96.0 - i * 0.5
        bars.append({"close": price, "high": price + 0.3, "low": price - 0.3, "volume": 2000000})
    return bars


def make_bars_at_upper_band(n=60):
    """Generate bars where price rises to the upper Bollinger Band."""
    bars = []
    for i in range(n - 5):
        bars.append({"close": 100.0 + np.sin(i * 0.1) * 0.5, "high": 101.0, "low": 99.0, "volume": 1000000})
    for i in range(5):
        price = 104.0 + i * 0.5
        bars.append({"close": price, "high": price + 0.3, "low": price - 0.3, "volume": 2000000})
    return bars


def make_flat_bars(n=60):
    """Generate gently oscillating bars that shouldn't trigger extremes."""
    return [
        {"close": 100.0 + np.sin(i * 0.3) * 0.3, "high": 100.5 + np.sin(i * 0.3) * 0.3, "low": 99.5 + np.sin(i * 0.3) * 0.3, "volume": 1000000}
        for i in range(n)
    ]


class TestBollingerBands:

    def test_basic_calculation(self):
        prices = np.array([100.0] * 20 + [95.0])
        upper, middle, lower = MeanReversionStrategy._calc_bollinger_bands(prices, 20, 2.0)
        assert upper is not None
        assert middle[-1] < 100.0  # Middle includes the drop
        assert lower[-1] < middle[-1]
        assert upper[-1] > middle[-1]

    def test_insufficient_data(self):
        prices = np.array([100.0] * 5)
        upper, middle, lower = MeanReversionStrategy._calc_bollinger_bands(prices, 20, 2.0)
        assert upper is None


class TestRSI:

    def test_all_gains_high_rsi(self):
        prices = np.array([float(100 + i) for i in range(30)])
        rsi = MeanReversionStrategy._calc_rsi(prices, 14)
        assert rsi is not None
        assert rsi[-1] > 70

    def test_all_losses_low_rsi(self):
        prices = np.array([float(130 - i) for i in range(30)])
        rsi = MeanReversionStrategy._calc_rsi(prices, 14)
        assert rsi is not None
        assert rsi[-1] < 30

    def test_insufficient_data(self):
        prices = np.array([100.0, 101.0])
        rsi = MeanReversionStrategy._calc_rsi(prices, 14)
        assert rsi is None


class TestBuySignal:

    async def test_buy_at_lower_band_oversold(self):
        """Generates BUY when price at lower BB + RSI oversold."""
        strategy = MeanReversionStrategy()
        bars = make_bars_at_lower_band()
        signals = await strategy.generate_signals(
            {"bars": bars}, MarketRegime.RANGING
        )
        buys = [s for s in signals if s.side == Side.BUY]
        assert len(buys) >= 1
        assert buys[0].take_profit is not None

    async def test_no_signal_in_trending_regime(self):
        """Skips mean reversion in trending markets."""
        strategy = MeanReversionStrategy()
        bars = make_bars_at_lower_band()
        signals = await strategy.generate_signals(
            {"bars": bars}, MarketRegime.TRENDING_UP
        )
        assert signals == []

    async def test_no_signal_flat_market(self):
        """No signal when price is near the middle."""
        strategy = MeanReversionStrategy()
        bars = make_flat_bars()
        signals = await strategy.generate_signals(
            {"bars": bars}, MarketRegime.RANGING
        )
        assert signals == []


class TestSellSignal:

    async def test_sell_at_upper_band_overbought(self):
        """Generates SELL when price at upper BB + RSI overbought."""
        strategy = MeanReversionStrategy()
        bars = make_bars_at_upper_band()
        signals = await strategy.generate_signals(
            {"bars": bars}, MarketRegime.RANGING
        )
        sells = [s for s in signals if s.side == Side.SELL]
        assert len(sells) >= 1


class TestConfidence:

    def test_buy_confidence_bounded(self):
        conf = MeanReversionStrategy._calc_buy_confidence(-0.5, 20.0, 0.04)
        assert 0.1 <= conf <= 1.0

    def test_sell_confidence_bounded(self):
        conf = MeanReversionStrategy._calc_sell_confidence(1.5, 80.0, 0.04)
        assert 0.2 <= conf <= 1.0

    def test_lower_rsi_higher_buy_confidence(self):
        conf_low = MeanReversionStrategy._calc_buy_confidence(-0.3, 15.0, 0.04)
        conf_mid = MeanReversionStrategy._calc_buy_confidence(-0.3, 28.0, 0.04)
        assert conf_low >= conf_mid


class TestStrategyConfig:

    def test_default_params(self):
        s = MeanReversionStrategy()
        assert s.parameters["symbol"] == "SPY"
        assert s.parameters["bb_period"] == 20

    def test_custom_params(self):
        s = MeanReversionStrategy(parameters={"symbol": "QQQ", "bb_std": 2.5})
        assert s.parameters["symbol"] == "QQQ"
        assert s.parameters["bb_std"] == 2.5

    async def test_empty_market_data(self):
        s = MeanReversionStrategy()
        signals = await s.generate_signals({}, MarketRegime.RANGING)
        assert signals == []
