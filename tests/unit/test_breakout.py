"""
Unit tests for Breakout Detector strategy (crypto).
"""

import pytest
from engines.strategy.crypto.breakout import BreakoutStrategy
from engines.models import Side, MarketRegime


def _make_bars(prices, volumes):
    return [
        {"open": p - 1, "high": p + 2, "low": p - 2, "close": p, "volume": v}
        for p, v in zip(prices, volumes)
    ]


@pytest.mark.asyncio
async def test_breakout_buy_signal():
    """Price breaks above 20-bar high with volume confirmation."""
    prices = [100 + (i % 3 - 1) for i in range(25)]
    prices.append(110)
    volumes = [1000] * 25 + [2000]
    bars = _make_bars(prices, volumes)
    strategy = BreakoutStrategy()
    signals = await strategy.generate_signals({"BTC-USD": bars}, MarketRegime.UNKNOWN)
    assert len(signals) >= 1
    assert signals[0].side == Side.BUY


@pytest.mark.asyncio
async def test_breakout_no_signal_in_range():
    prices = [100 + (i % 3 - 1) for i in range(30)]
    volumes = [1000] * 30
    bars = _make_bars(prices, volumes)
    strategy = BreakoutStrategy()
    signals = await strategy.generate_signals({"BTC-USD": bars}, MarketRegime.UNKNOWN)
    assert len(signals) == 0


@pytest.mark.asyncio
async def test_breakout_no_signal_low_volume():
    prices = [100] * 25 + [110]
    volumes = [1000] * 25 + [800]
    bars = _make_bars(prices, volumes)
    strategy = BreakoutStrategy()
    signals = await strategy.generate_signals({"BTC-USD": bars}, MarketRegime.UNKNOWN)
    assert len(signals) == 0


@pytest.mark.asyncio
async def test_breakout_sell_signal():
    """Price breaks below 20-bar low with volume confirmation."""
    prices = [100 + (i % 3 - 1) for i in range(25)]
    prices.append(90)
    volumes = [1000] * 25 + [2000]
    bars = _make_bars(prices, volumes)
    strategy = BreakoutStrategy()
    signals = await strategy.generate_signals({"BTC-USD": bars}, MarketRegime.UNKNOWN)
    assert len(signals) >= 1
    assert signals[0].side == Side.SELL


@pytest.mark.asyncio
async def test_breakout_multi_symbol():
    """Should process multiple symbols up to max_signals_per_cycle."""
    prices = [100 + (i % 3 - 1) for i in range(25)]
    prices.append(110)
    volumes = [1000] * 25 + [2000]
    bars = _make_bars(prices, volumes)
    strategy = BreakoutStrategy()
    signals = await strategy.generate_signals(
        {"BTC-USD": bars, "ETH-USD": bars, "SOL-USD": bars, "AVAX-USD": bars},
        MarketRegime.UNKNOWN,
    )
    assert len(signals) <= strategy.max_signals_per_cycle


class TestStrategyConfig:

    def test_default_params(self):
        s = BreakoutStrategy()
        assert s.strategy_id == "breakout_crypto"
        assert s.tier.value == "scout"
        assert s.timeframe == "1Hour"
        assert s.max_signals_per_cycle == 3
        assert s.parameters["position_size_usd"] == 75.0
        assert s.parameters["stop_loss_pct"] == 2.5
        assert len(s.symbols) == 5

    @pytest.mark.asyncio
    async def test_empty_bars(self):
        s = BreakoutStrategy()
        signals = await s.generate_signals({}, MarketRegime.UNKNOWN)
        assert signals == []

    @pytest.mark.asyncio
    async def test_insufficient_bars(self):
        s = BreakoutStrategy()
        bars = _make_bars([100, 101, 102], [1000, 1000, 1000])
        signals = await s.generate_signals({"BTC-USD": bars}, MarketRegime.UNKNOWN)
        assert signals == []


class TestConfidence:

    def test_confidence_bounded(self):
        conf = BreakoutStrategy._calc_confidence(0.05, 0.5, 0.03)
        assert 0.1 <= conf <= 1.0

    def test_bigger_breakout_higher_confidence(self):
        c1 = BreakoutStrategy._calc_confidence(0.10, 0.5, 0.03)
        c2 = BreakoutStrategy._calc_confidence(0.01, 0.5, 0.03)
        assert c1 > c2

    def test_higher_volume_higher_confidence(self):
        c1 = BreakoutStrategy._calc_confidence(0.05, 1.0, 0.03)
        c2 = BreakoutStrategy._calc_confidence(0.05, 0.3, 0.03)
        assert c1 > c2
