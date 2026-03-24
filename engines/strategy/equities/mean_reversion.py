"""
Mean Reversion Strategy — Bollinger Bands + RSI for equities.

Exploits the tendency of prices to revert to the mean after extreme moves.
Best in ranging/low-volatility markets. Should be disabled during strong trends.

Signal logic:
- BUY: Price touches lower Bollinger Band AND RSI < 30 (oversold)
- SELL: Price touches upper Bollinger Band AND RSI > 70 (overbought)
         OR price reverts to middle band (take profit)

Default parameters:
- symbol: SPY
- bb_period: 20 (Bollinger Band lookback)
- bb_std: 2.0 (standard deviations for bands)
- rsi_period: 14
- rsi_oversold: 30.0
- rsi_overbought: 70.0
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


class MeanReversionStrategy(Strategy):
    """
    Mean reversion strategy using Bollinger Bands and RSI.

    Buys when price is oversold at the lower band, sells when
    overbought at the upper band or when price reverts to the mean.
    """

    def __init__(
        self,
        strategy_id: str = "mean_reversion_spy",
        parameters: dict[str, Any] | None = None,
    ):
        default_params = {
            "symbol": "SPY",
            "bb_period": 20,
            "bb_std": 2.0,
            "rsi_period": 14,
            "rsi_oversold": 30.0,
            "rsi_overbought": 70.0,
            "position_size_usd": 500.0,
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
        Generate signals from Bollinger Bands and RSI.

        Skips signal generation in strong trending regimes where
        mean reversion is likely to fail.
        """
        # Mean reversion is dangerous in strong trends
        if market_regime in (
            MarketRegime.TRENDING_UP,
            MarketRegime.TRENDING_DOWN,
        ):
            logger.debug(
                "Skipping mean reversion in %s regime", market_regime.value
            )
            return []

        bars = market_data.get("bars", [])
        min_bars = self.parameters["bb_period"] + self.parameters["rsi_period"] + 2

        if len(bars) < min_bars:
            return []

        closes = np.array([b["close"] for b in bars])
        current_price = closes[-1]

        # Calculate indicators
        upper, middle, lower = self._calc_bollinger_bands(
            closes, self.parameters["bb_period"], self.parameters["bb_std"]
        )
        rsi = self._calc_rsi(closes, self.parameters["rsi_period"])

        if upper is None or rsi is None:
            return []

        current_upper = upper[-1]
        current_middle = middle[-1]
        current_lower = lower[-1]
        current_rsi = rsi[-1]

        bb_width = (current_upper - current_lower) / current_middle if current_middle > 0 else 0
        pct_b = (
            (current_price - current_lower) / (current_upper - current_lower)
            if (current_upper - current_lower) > 0 else 0.5
        )

        logger.debug(
            "MeanRev %s: price=%.2f BB[%.2f/%.2f/%.2f] RSI=%.1f %%B=%.2f",
            self.parameters["symbol"], current_price,
            current_lower, current_middle, current_upper,
            current_rsi, pct_b,
        )

        # BUY: price at/below lower band + RSI oversold
        if (
            current_price <= current_lower
            and current_rsi < self.parameters["rsi_oversold"]
        ):
            confidence = self._calc_buy_confidence(pct_b, current_rsi, bb_width)
            quantity = self.parameters["position_size_usd"] / current_price
            take_profit = current_middle  # Target mean reversion to middle band

            return [
                Signal(
                    strategy_id=self.strategy_id,
                    asset_class=self.asset_class,
                    symbol=self.parameters["symbol"],
                    side=Side.BUY,
                    quantity=round(quantity, 2),
                    target_price=current_price,
                    take_profit=round(take_profit, 2),
                    stop_loss=round(current_lower * 0.98, 2),
                    confidence=confidence,
                    strength=self._classify_strength(confidence),
                    rationale=(
                        f"Mean Reversion BUY: Price ${current_price:.2f} at lower "
                        f"BB ${current_lower:.2f}, RSI={current_rsi:.1f} (oversold). "
                        f"Target: middle band ${current_middle:.2f}"
                    ),
                    market_regime=market_regime,
                )
            ]

        # SELL: price at/above upper band + RSI overbought
        if (
            current_price >= current_upper
            and current_rsi > self.parameters["rsi_overbought"]
        ):
            confidence = self._calc_sell_confidence(pct_b, current_rsi, bb_width)

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
                        f"Mean Reversion SELL: Price ${current_price:.2f} at upper "
                        f"BB ${current_upper:.2f}, RSI={current_rsi:.1f} (overbought)."
                    ),
                    market_regime=market_regime,
                )
            ]

        return []

    async def get_performance(self, period_days: int) -> StrategyPerformance:
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
    def _calc_bollinger_bands(
        prices: np.ndarray, period: int, num_std: float
    ) -> tuple[np.ndarray | None, np.ndarray | None, np.ndarray | None]:
        """Calculate Bollinger Bands (upper, middle, lower)."""
        if len(prices) < period:
            return None, None, None

        middle = np.array([
            np.mean(prices[i - period:i]) for i in range(period, len(prices) + 1)
        ])
        std = np.array([
            np.std(prices[i - period:i]) for i in range(period, len(prices) + 1)
        ])

        upper = middle + num_std * std
        lower = middle - num_std * std

        return upper, middle, lower

    @staticmethod
    def _calc_rsi(prices: np.ndarray, period: int) -> np.ndarray | None:
        """Calculate Relative Strength Index."""
        if len(prices) < period + 1:
            return None

        deltas = np.diff(prices)
        gains = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)

        rsi_values = []
        avg_gain = np.mean(gains[:period])
        avg_loss = np.mean(losses[:period])

        for i in range(period, len(deltas)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

            if avg_loss == 0:
                rsi_values.append(100.0)
            else:
                rs = avg_gain / avg_loss
                rsi_values.append(100.0 - (100.0 / (1.0 + rs)))

        return np.array(rsi_values) if rsi_values else None

    @staticmethod
    def _calc_buy_confidence(pct_b: float, rsi: float, bb_width: float) -> float:
        """Higher confidence when further below band with lower RSI."""
        # Distance below band (0–0.4)
        band_score = min(max(-pct_b, 0) * 2, 0.4)
        # RSI oversold depth (0–0.4)
        rsi_score = min(max((30 - rsi) / 30, 0) * 0.4, 0.4)
        # Wider bands = more volatile = less confident (0–0.2)
        width_penalty = min(bb_width * 2, 0.2)

        confidence = band_score + rsi_score + 0.2 - width_penalty
        return min(max(confidence, 0.1), 1.0)

    @staticmethod
    def _calc_sell_confidence(pct_b: float, rsi: float, bb_width: float) -> float:
        """Higher confidence when further above band with higher RSI."""
        band_score = min(max(pct_b - 1.0, 0) * 2, 0.4)
        rsi_score = min(max((rsi - 70) / 30, 0) * 0.4, 0.4)
        width_penalty = min(bb_width * 2, 0.2)

        confidence = band_score + rsi_score + 0.2 - width_penalty
        return min(max(confidence, 0.2), 1.0)

    @staticmethod
    def _classify_strength(confidence: float) -> SignalStrength:
        if confidence >= 0.8:
            return SignalStrength.STRONG
        elif confidence >= 0.6:
            return SignalStrength.MODERATE
        return SignalStrength.WEAK
