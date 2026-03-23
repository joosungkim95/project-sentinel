"""
SMA Crossover Strategy — Simple Moving Average crossover for equities.

This is the first strategy, intentionally simple. It serves as:
1. A baseline to validate the full pipeline works
2. A template for more complex strategies
3. A benchmark for the Learning Engine to beat

Signal logic:
- BUY when short SMA crosses above long SMA (golden cross)
- SELL when short SMA crosses below long SMA (death cross)
- Confidence based on the magnitude of the crossover

Default parameters:
- short_window: 10 (bars)
- long_window: 50 (bars)
- symbol: SPY
"""

import logging
from typing import Any

import numpy as np

from engines.models import (
    AssetClass,
    MarketRegime,
    Signal,
    SignalStrength,
    Side,
    StrategyPerformance,
    StrategyStatus,
)
from engines.strategy.base import Strategy

logger = logging.getLogger(__name__)


class SMACrossoverStrategy(Strategy):
    """
    Simple Moving Average Crossover strategy.

    Generates buy signals when the short-term SMA crosses above
    the long-term SMA, and sell signals on the reverse.
    """

    def __init__(
        self,
        strategy_id: str = "sma_crossover_spy",
        parameters: dict[str, Any] | None = None,
    ):
        default_params = {
            "symbol": "SPY",
            "short_window": 10,
            "long_window": 50,
            "position_size_usd": 500.0,  # Dollar amount per trade
            "min_crossover_pct": 0.1,    # Min % difference to trigger
        }
        if parameters:
            default_params.update(parameters)

        super().__init__(
            strategy_id=strategy_id,
            asset_class=AssetClass.EQUITIES,
            parameters=default_params,
        )

    async def generate_signals(
        self,
        market_data: dict[str, Any],
        market_regime: MarketRegime,
    ) -> list[Signal]:
        """
        Generate signals from SMA crossover analysis.

        Args:
            market_data: Must contain 'bars' key with OHLCV data.
                         Each bar: {close: float, ...}
            market_regime: Current market regime classification.

        Returns:
            List with 0 or 1 Signal.
        """
        bars = market_data.get("bars", [])
        if len(bars) < self.parameters["long_window"] + 1:
            logger.debug(
                "Not enough bars for SMA calculation: %d < %d",
                len(bars),
                self.parameters["long_window"] + 1,
            )
            return []

        closes = np.array([b["close"] for b in bars])

        short_sma = self._calc_sma(closes, self.parameters["short_window"])
        long_sma = self._calc_sma(closes, self.parameters["long_window"])

        if short_sma is None or long_sma is None:
            return []

        # Current and previous crossover state
        current_diff_pct = ((short_sma[-1] - long_sma[-1]) / long_sma[-1]) * 100
        prev_diff_pct = ((short_sma[-2] - long_sma[-2]) / long_sma[-2]) * 100

        min_crossover = self.parameters["min_crossover_pct"]
        current_price = closes[-1]

        # Golden cross: short SMA crosses above long SMA
        if prev_diff_pct <= 0 and current_diff_pct > min_crossover:
            confidence = min(abs(current_diff_pct) / 2.0, 1.0)
            quantity = self.parameters["position_size_usd"] / current_price

            logger.info(
                "SMA Golden Cross: %s short=%.2f long=%.2f diff=%.2f%%",
                self.parameters["symbol"],
                short_sma[-1],
                long_sma[-1],
                current_diff_pct,
            )

            return [
                Signal(
                    strategy_id=self.strategy_id,
                    asset_class=self.asset_class,
                    symbol=self.parameters["symbol"],
                    side=Side.BUY,
                    quantity=round(quantity, 2),
                    target_price=current_price,
                    confidence=confidence,
                    strength=self._classify_strength(confidence),
                    rationale=(
                        f"SMA Golden Cross: {self.parameters['short_window']}-bar SMA "
                        f"({short_sma[-1]:.2f}) crossed above "
                        f"{self.parameters['long_window']}-bar SMA ({long_sma[-1]:.2f}). "
                        f"Crossover magnitude: {current_diff_pct:.2f}%"
                    ),
                    market_regime=market_regime,
                )
            ]

        # Death cross: short SMA crosses below long SMA
        if prev_diff_pct >= 0 and current_diff_pct < -min_crossover:
            confidence = min(abs(current_diff_pct) / 2.0, 1.0)

            logger.info(
                "SMA Death Cross: %s short=%.2f long=%.2f diff=%.2f%%",
                self.parameters["symbol"],
                short_sma[-1],
                long_sma[-1],
                current_diff_pct,
            )

            return [
                Signal(
                    strategy_id=self.strategy_id,
                    asset_class=self.asset_class,
                    symbol=self.parameters["symbol"],
                    side=Side.SELL,
                    quantity=0,  # Sell entire position
                    target_price=current_price,
                    confidence=confidence,
                    strength=self._classify_strength(confidence),
                    rationale=(
                        f"SMA Death Cross: {self.parameters['short_window']}-bar SMA "
                        f"({short_sma[-1]:.2f}) crossed below "
                        f"{self.parameters['long_window']}-bar SMA ({long_sma[-1]:.2f}). "
                        f"Crossover magnitude: {current_diff_pct:.2f}%"
                    ),
                    market_regime=market_regime,
                )
            ]

        return []

    async def get_performance(self, period_days: int) -> StrategyPerformance:
        """Calculate performance metrics. TODO: implement with DB."""
        return StrategyPerformance(
            strategy_id=self.strategy_id,
            period_days=period_days,
            trades_count=0,
            win_rate=0.0,
            total_pnl=0.0,
            max_drawdown=0.0,
            risk_budget_used_pct=0.0,
            status=self.status,
        )

    @staticmethod
    def _calc_sma(
        prices: np.ndarray, window: int
    ) -> np.ndarray | None:
        """Calculate Simple Moving Average."""
        if len(prices) < window:
            return None
        # Use cumsum for efficient SMA calculation
        cumsum = np.cumsum(np.insert(prices, 0, 0))
        sma = (cumsum[window:] - cumsum[:-window]) / window
        return sma

    @staticmethod
    def _classify_strength(confidence: float) -> SignalStrength:
        """Map confidence to signal strength."""
        if confidence >= 0.8:
            return SignalStrength.STRONG
        elif confidence >= 0.6:
            return SignalStrength.MODERATE
        return SignalStrength.WEAK
