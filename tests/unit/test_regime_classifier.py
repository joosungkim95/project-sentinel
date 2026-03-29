"""
Unit tests for classify_from_bars() — indicator-based regime classification.
"""

import pytest

from engines.models import MarketRegime
from memory.market_regime import classify_from_bars


def _make_bars(
    closes: list[float],
    spread_pct: float = 0.01,
) -> list[dict]:
    """Build minimal OHLCV bars from a list of close prices.

    Args:
        closes: Close prices in chronological order.
        spread_pct: High/low spread as a fraction of close.
    """
    bars = []
    for c in closes:
        half = c * spread_pct / 2
        bars.append({
            "open": c,
            "high": c + half,
            "low": c - half,
            "close": c,
            "volume": 1000,
        })
    return bars


class TestClassifyFromBars:
    """Tests for the stateless regime classifier."""

    def test_insufficient_bars_returns_unknown(self) -> None:
        bars = _make_bars([100.0] * 10)
        regime, conf, indicators = classify_from_bars(bars)
        assert regime == MarketRegime.UNKNOWN
        assert conf == 0.0
        assert indicators == {}

    def test_flat_market_is_ranging(self) -> None:
        # 40 bars at exactly the same price
        bars = _make_bars([100.0] * 40)
        regime, conf, indicators = classify_from_bars(bars)
        assert regime == MarketRegime.RANGING
        assert conf >= 0.5

    def test_steady_uptrend_detected(self) -> None:
        # Price climbing from 100 to ~120 over 40 bars
        closes = [100.0 + i * 0.5 for i in range(40)]
        bars = _make_bars(closes)
        regime, conf, indicators = classify_from_bars(bars)
        assert regime == MarketRegime.TRENDING_UP
        assert conf >= 0.5
        assert indicators["sma_slope_pct"] > 0

    def test_steady_downtrend_detected(self) -> None:
        # Price falling from 120 to ~100 over 40 bars
        closes = [120.0 - i * 0.5 for i in range(40)]
        bars = _make_bars(closes)
        regime, conf, indicators = classify_from_bars(bars)
        assert regime == MarketRegime.TRENDING_DOWN
        assert conf >= 0.5
        assert indicators["sma_slope_pct"] < 0

    def test_high_volatility_detected(self) -> None:
        # Large swings — high ATR relative to price
        closes = [100.0] * 40
        bars = _make_bars(closes, spread_pct=0.10)  # 10% daily range
        regime, conf, indicators = classify_from_bars(bars)
        assert regime == MarketRegime.HIGH_VOLATILITY
        assert indicators["atr_ratio"] > 0.035

    def test_strong_trend_has_higher_confidence(self) -> None:
        # Barely trending (slope just above threshold)
        barely = [100.0 + i * 0.02 for i in range(40)]
        regime_b, conf_barely, _ = classify_from_bars(_make_bars(barely))

        # Strong trend (well above threshold)
        strong = [100.0 + i * 1.0 for i in range(40)]
        _, conf_strong, _ = classify_from_bars(_make_bars(strong))

        assert regime_b == MarketRegime.TRENDING_UP
        assert conf_strong >= conf_barely

    def test_indicators_dict_has_expected_keys(self) -> None:
        bars = _make_bars([100.0 + i * 0.5 for i in range(40)])
        _, _, indicators = classify_from_bars(bars)
        assert "sma_slope_pct" in indicators
        assert "atr_ratio" in indicators
        assert "sma_current" in indicators
        assert "atr" in indicators
        assert "price" in indicators

    def test_confidence_bounded(self) -> None:
        # Even extreme trends should have confidence <= 0.95
        extreme = [100.0 + i * 5.0 for i in range(40)]
        _, conf, _ = classify_from_bars(_make_bars(extreme))
        assert 0.0 <= conf <= 0.95

    def test_vol_takes_priority_over_trend(self) -> None:
        # Uptrend + very high volatility → should be HIGH_VOLATILITY
        closes = [100.0 + i * 0.5 for i in range(40)]
        bars = _make_bars(closes, spread_pct=0.12)
        regime, _, _ = classify_from_bars(bars)
        assert regime == MarketRegime.HIGH_VOLATILITY

    def test_custom_periods(self) -> None:
        bars = _make_bars([100.0] * 50)
        regime, conf, _ = classify_from_bars(bars, sma_period=10, atr_period=7)
        assert regime == MarketRegime.RANGING
