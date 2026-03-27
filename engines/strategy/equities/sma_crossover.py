"""
SMA Crossover Strategy — Simple Moving Average crossover for equities.

SNIPER tier: rare, high-conviction signals on daily bars.

Signal logic:
- BUY when short SMA crosses above long SMA (golden cross)
- SELL when short SMA crosses below long SMA (death cross)
- Confidence based on the magnitude of the crossover

Default parameters:
- short_window: 10 (bars)
- long_window: 50 (bars)
- symbols: SPY, QQQ, IWM
"""

import logging
from typing import Any

import numpy as np

from config.symbols import EQUITY_SNIPER_SYMBOLS
from config.tiers import StrategyTier
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
            tier=StrategyTier.SNIPER,
            symbols=EQUITY_SNIPER_SYMBOLS,
            timeframe="1Day",
            max_signals_per_cycle=1,
        )

    async def generate_signals(
        self,
        bars: dict[str, list[dict]],
        market_regime: MarketRegime,
    ) -> list[Signal]:
        """
        Generate signals from SMA crossover analysis.

        Args:
            bars: Bar data keyed by symbol, each value a list of OHLCV dicts.
            market_regime: Current market regime classification.

        Returns:
            List of Signals (at most max_signals_per_cycle).
        """
        signals: list[Signal] = []

        for symbol in self.symbols:
            symbol_bars = bars.get(symbol, [])
            if len(symbol_bars) < self.parameters["long_window"] + 1:
                logger.debug(
                    "Not enough bars for SMA calculation on %s: %d < %d",
                    symbol,
                    len(symbol_bars),
                    self.parameters["long_window"] + 1,
                )
                continue

            closes = np.array([b["close"] for b in symbol_bars])

            short_sma = self._calc_sma(closes, self.parameters["short_window"])
            long_sma = self._calc_sma(closes, self.parameters["long_window"])

            if short_sma is None or long_sma is None:
                continue

            # Current and previous crossover state
            current_diff_pct = ((short_sma[-1] - long_sma[-1]) / long_sma[-1]) * 100
            prev_diff_pct = ((short_sma[-2] - long_sma[-2]) / long_sma[-2]) * 100

            min_crossover = self.parameters["min_crossover_pct"]
            current_price = closes[-1]
            position_size = self.parameters["position_size_usd"]

            # Golden cross: short SMA crosses above long SMA
            if prev_diff_pct <= 0 and current_diff_pct > min_crossover:
                confidence = min(abs(current_diff_pct) / 2.0, 1.0)
                quantity = position_size / current_price

                logger.info(
                    "SMA Golden Cross: %s short=%.2f long=%.2f diff=%.2f%%",
                    symbol,
                    short_sma[-1],
                    long_sma[-1],
                    current_diff_pct,
                )

                signals.append(
                    Signal(
                        strategy_id=self.strategy_id,
                        asset_class=self.asset_class,
                        symbol=symbol,
                        side=Side.BUY,
                        quantity=round(quantity, 2),
                        target_price=current_price,
                        confidence=confidence,
                        strength=self._classify_strength(confidence),
                        rationale=(
                            f"SMA Golden Cross on {symbol}: "
                            f"{self.parameters['short_window']}-bar SMA "
                            f"({short_sma[-1]:.2f}) crossed above "
                            f"{self.parameters['long_window']}-bar SMA "
                            f"({long_sma[-1]:.2f}). "
                            f"Crossover magnitude: {current_diff_pct:.2f}%"
                        ),
                        market_regime=market_regime,
                        position_size_usd=position_size,
                        tier=self.tier,
                    )
                )

            # Death cross: short SMA crosses below long SMA
            elif prev_diff_pct >= 0 and current_diff_pct < -min_crossover:
                confidence = min(abs(current_diff_pct) / 2.0, 1.0)

                logger.info(
                    "SMA Death Cross: %s short=%.2f long=%.2f diff=%.2f%%",
                    symbol,
                    short_sma[-1],
                    long_sma[-1],
                    current_diff_pct,
                )

                signals.append(
                    Signal(
                        strategy_id=self.strategy_id,
                        asset_class=self.asset_class,
                        symbol=symbol,
                        side=Side.SELL,
                        quantity=0,  # Sell entire position
                        target_price=current_price,
                        confidence=confidence,
                        strength=self._classify_strength(confidence),
                        rationale=(
                            f"SMA Death Cross on {symbol}: "
                            f"{self.parameters['short_window']}-bar SMA "
                            f"({short_sma[-1]:.2f}) crossed below "
                            f"{self.parameters['long_window']}-bar SMA "
                            f"({long_sma[-1]:.2f}). "
                            f"Crossover magnitude: {current_diff_pct:.2f}%"
                        ),
                        market_regime=market_regime,
                        position_size_usd=position_size,
                        tier=self.tier,
                    )
                )

            if len(signals) >= self.max_signals_per_cycle:
                break

        return signals[:self.max_signals_per_cycle]

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
