"""
Mean Reversion Strategy — Bollinger Bands + RSI for equities.

CORE tier: moderate frequency signals on 4-hour bars.

Exploits the tendency of prices to revert to the mean after extreme moves.
Best in ranging/low-volatility markets. Blocked in downtrend regimes only.

Signal logic (OR-based with confluence boosting confidence):
- BUY: Price near lower BB (pct_b < 0.15) OR RSI oversold (< 40)
  Either alone triggers at lower confidence; both = higher confidence.
- SELL: Price near upper BB (pct_b > 0.85) OR RSI overbought (> 60)

Default parameters:
- symbols: all 7 equity symbols
- bb_period: 20
- bb_std: 1.5
- rsi_oversold: 40.0
- rsi_overbought: 60.0
"""

import logging
from typing import Any

import numpy as np

from config.symbols import EQUITY_SYMBOLS
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


class MeanReversionStrategy(Strategy):
    """
    Mean reversion strategy using Bollinger Bands and RSI.

    Buys when price is oversold at the lower band, sells when
    overbought at the upper band or when price reverts to the mean.
    """

    def __init__(
        self,
        strategy_id: str = "mean_reversion_equity",
        parameters: dict[str, Any] | None = None,
    ):
        default_params = {
            "bb_period": 20,
            "bb_std": 1.5,              # Relaxed from 2.0
            "rsi_period": 14,
            "rsi_oversold": 40.0,       # Relaxed from 30.0
            "rsi_overbought": 60.0,     # Relaxed from 70.0
            "position_size_usd": 300.0,  # Reduced from 500
        }
        if parameters:
            default_params.update(parameters)

        super().__init__(
            strategy_id=strategy_id,
            asset_class=AssetClass.EQUITIES,
            parameters=default_params,
            tier=StrategyTier.CORE,
            symbols=EQUITY_SYMBOLS,
            timeframe="4Hour",
            max_signals_per_cycle=2,
        )

    async def generate_signals(
        self,
        bars: dict[str, list[dict]],
        market_regime: MarketRegime,
    ) -> list[Signal]:
        """
        Generate signals from Bollinger Bands and RSI.

        Skips signal generation in downtrend regimes where
        mean reversion is most dangerous. Uptrends are allowed
        as defense-in-depth (mean reversion in uptrends is less risky).
        """
        # Mean reversion is dangerous in downtrends — block only those
        if market_regime == MarketRegime.TRENDING_DOWN:
            logger.debug(
                "Skipping mean reversion in %s regime", market_regime.value
            )
            return []

        signals: list[Signal] = []

        for symbol in self.symbols:
            symbol_bars = bars.get(symbol, [])
            min_bars = (
                self.parameters["bb_period"]
                + self.parameters["rsi_period"]
                + 2
            )

            if len(symbol_bars) < min_bars:
                continue

            closes = np.array([b["close"] for b in symbol_bars])
            current_price = closes[-1]

            # Calculate indicators
            upper, middle, lower = self._calc_bollinger_bands(
                closes,
                self.parameters["bb_period"],
                self.parameters["bb_std"],
            )
            rsi = self._calc_rsi(closes, self.parameters["rsi_period"])

            if upper is None or rsi is None:
                continue

            current_upper = upper[-1]
            current_middle = middle[-1]
            current_lower = lower[-1]
            current_rsi = rsi[-1]

            bb_width = (
                (current_upper - current_lower) / current_middle
                if current_middle > 0
                else 0
            )
            pct_b = (
                (current_price - current_lower)
                / (current_upper - current_lower)
                if (current_upper - current_lower) > 0
                else 0.5
            )

            logger.debug(
                "MeanRev %s: price=%.2f BB[%.2f/%.2f/%.2f] RSI=%.1f %%B=%.2f",
                symbol,
                current_price,
                current_lower,
                current_middle,
                current_upper,
                current_rsi,
                pct_b,
            )

            position_size = self.parameters["position_size_usd"]

            # BUY: price near lower band OR RSI oversold (OR-based)
            bb_oversold = pct_b < 0.15  # Near or below lower band
            rsi_oversold = current_rsi < self.parameters["rsi_oversold"]

            if bb_oversold or rsi_oversold:
                confidence = self._calc_buy_confidence(
                    pct_b, current_rsi, bb_width,
                    bb_oversold, rsi_oversold,
                )
                quantity = position_size / current_price
                take_profit = current_middle

                triggers = []
                if bb_oversold:
                    triggers.append(f"%%B={pct_b:.2f}")
                if rsi_oversold:
                    triggers.append(f"RSI={current_rsi:.1f}")

                signals.append(
                    Signal(
                        strategy_id=self.strategy_id,
                        asset_class=self.asset_class,
                        symbol=symbol,
                        side=Side.BUY,
                        quantity=round(quantity, 2),
                        target_price=current_price,
                        take_profit=round(take_profit, 2),
                        stop_loss=round(current_lower * 0.98, 2),
                        confidence=confidence,
                        strength=self._classify_strength(confidence),
                        rationale=(
                            f"Mean Reversion BUY {symbol}: "
                            f"{' + '.join(triggers)} "
                            f"({'confluence' if bb_oversold and rsi_oversold else 'single trigger'}). "
                            f"Target: middle band ${current_middle:.2f}"
                        ),
                        market_regime=market_regime,
                        position_size_usd=position_size,
                        tier=self.tier,
                    )
                )

            # SELL: price near upper band OR RSI overbought (OR-based)
            bb_overbought = pct_b > 0.85
            rsi_overbought = current_rsi > self.parameters["rsi_overbought"]

            if not (bb_oversold or rsi_oversold) and (bb_overbought or rsi_overbought):
                confidence = self._calc_sell_confidence(
                    pct_b, current_rsi, bb_width,
                    bb_overbought, rsi_overbought,
                )

                triggers = []
                if bb_overbought:
                    triggers.append(f"%%B={pct_b:.2f}")
                if rsi_overbought:
                    triggers.append(f"RSI={current_rsi:.1f}")

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
                            f"Mean Reversion SELL {symbol}: "
                            f"{' + '.join(triggers)} "
                            f"({'confluence' if bb_overbought and rsi_overbought else 'single trigger'})"
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
            np.mean(prices[i - period:i])
            for i in range(period, len(prices) + 1)
        ])
        std = np.array([
            np.std(prices[i - period:i])
            for i in range(period, len(prices) + 1)
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
    def _calc_buy_confidence(
        pct_b: float,
        rsi: float,
        bb_width: float,
        bb_triggered: bool,
        rsi_triggered: bool,
    ) -> float:
        """
        OR-based confidence: single trigger = lower, confluence = higher.

        Single trigger: 0.25-0.45.  Both: 0.45-0.80.
        """
        band_score = 0.0
        rsi_score = 0.0

        if bb_triggered:
            # Distance below band (0.15-0.40)
            band_score = min(0.15 + max(-pct_b, 0) * 1.5, 0.40)

        if rsi_triggered:
            # RSI oversold depth (0.15-0.40)
            rsi_score = min(0.15 + max((40 - rsi) / 40, 0) * 0.25, 0.40)

        # Wider bands = more volatile = less confident
        width_penalty = min(bb_width * 1.5, 0.15)
        # Confluence bonus
        confluence = 0.10 if (bb_triggered and rsi_triggered) else 0.0

        confidence = band_score + rsi_score + confluence - width_penalty
        return min(max(confidence, 0.15), 1.0)

    @staticmethod
    def _calc_sell_confidence(
        pct_b: float,
        rsi: float,
        bb_width: float,
        bb_triggered: bool,
        rsi_triggered: bool,
    ) -> float:
        """OR-based sell confidence."""
        band_score = 0.0
        rsi_score = 0.0

        if bb_triggered:
            band_score = min(0.15 + max(pct_b - 1.0, 0) * 1.5, 0.40)

        if rsi_triggered:
            rsi_score = min(0.15 + max((rsi - 60) / 40, 0) * 0.25, 0.40)

        width_penalty = min(bb_width * 1.5, 0.15)
        confluence = 0.10 if (bb_triggered and rsi_triggered) else 0.0

        confidence = band_score + rsi_score + confluence - width_penalty
        return min(max(confidence, 0.15), 1.0)

    @staticmethod
    def _classify_strength(confidence: float) -> SignalStrength:
        if confidence >= 0.8:
            return SignalStrength.STRONG
        elif confidence >= 0.6:
            return SignalStrength.MODERATE
        return SignalStrength.WEAK
