"""
Momentum Scalp Strategy — RSI + Volume for equities.

SCOUT tier: frequent, small signals on 15-minute bars across all equity symbols.

Signal logic (OR-based with confluence boosting confidence):
- BUY: RSI shows momentum (>= 50) OR volume above average (>= 1.0x)
  Either condition alone triggers at lower confidence.
  Both confirming = higher confidence.
- SELL: RSI < 35 (momentum lost)

Default parameters:
- symbols: all 7 equity symbols
- rsi_period: 14
- volume_ma_period: 20
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


class MomentumStrategy(Strategy):
    """
    Momentum scalp strategy using RSI and volume confirmation.

    SCOUT tier: buys when RSI shows building momentum with confirming
    volume, sells when momentum fades. Small position sizes, high frequency.
    """

    def __init__(
        self,
        strategy_id: str = "momentum_scalp",
        parameters: dict[str, Any] | None = None,
    ):
        default_params = {
            "rsi_period": 14,
            "rsi_buy_low": 50.0,          # OR-based: lower threshold
            "rsi_buy_high": 85.0,
            "rsi_sell_threshold": 35.0,
            "volume_ma_period": 20,
            "volume_multiplier": 1.0,     # Volume confirms but doesn't gate
            "position_size_usd": 75.0,    # Small scout size
        }
        if parameters:
            default_params.update(parameters)

        super().__init__(
            strategy_id=strategy_id,
            asset_class=AssetClass.EQUITIES,
            parameters=default_params,
            tier=StrategyTier.SCOUT,
            symbols=EQUITY_SYMBOLS,
            timeframe="15Min",
            max_signals_per_cycle=3,
        )

    async def generate_signals(
        self,
        bars: dict[str, list[dict]],
        market_regime: MarketRegime,
    ) -> list[Signal]:
        """
        Generate signals from momentum indicators.

        Args:
            bars: Bar data keyed by symbol, each value a list of OHLCV dicts.
            market_regime: Current market regime classification.

        Returns:
            List of Signals (at most max_signals_per_cycle).
        """
        signals: list[Signal] = []

        for symbol in self.symbols:
            symbol_bars = bars.get(symbol, [])
            min_bars = max(
                self.parameters["rsi_period"],
                self.parameters["volume_ma_period"],
            ) + 2  # Need extra bars for lookback

            if len(symbol_bars) < min_bars:
                logger.debug(
                    "Not enough bars for momentum on %s: %d < %d",
                    symbol,
                    len(symbol_bars),
                    min_bars,
                )
                continue

            closes = np.array([b["close"] for b in symbol_bars])
            volumes = np.array([b["volume"] for b in symbol_bars])
            current_price = closes[-1]

            # Calculate indicators
            rsi = self._calc_rsi(closes, self.parameters["rsi_period"])
            vol_ratio = self._calc_volume_ratio(
                volumes, self.parameters["volume_ma_period"]
            )

            if rsi is None or vol_ratio is None:
                continue

            current_rsi = rsi[-1]
            current_vol_ratio = vol_ratio[-1]

            logger.debug(
                "Momentum indicators for %s: RSI=%.1f, Vol=%.2fx",
                symbol,
                current_rsi,
                current_vol_ratio,
            )

            # --- BUY signal (OR-based: either condition triggers) ---
            rsi_buy_low = self.parameters["rsi_buy_low"]
            rsi_buy_high = self.parameters["rsi_buy_high"]
            vol_multiplier = self.parameters["volume_multiplier"]
            position_size = self.parameters["position_size_usd"]

            rsi_bullish = rsi_buy_low <= current_rsi <= rsi_buy_high
            volume_strong = current_vol_ratio >= vol_multiplier
            # Don't buy when RSI is in sell territory
            rsi_not_bearish = current_rsi >= self.parameters["rsi_sell_threshold"]

            if rsi_not_bearish and (rsi_bullish or volume_strong):
                confidence = self._calc_buy_confidence(
                    current_rsi, current_vol_ratio,
                    rsi_bullish, volume_strong,
                )
                quantity = position_size / current_price

                triggers = []
                if rsi_bullish:
                    triggers.append(f"RSI={current_rsi:.1f}")
                if volume_strong:
                    triggers.append(f"Vol={current_vol_ratio:.2f}x")

                logger.info(
                    "Momentum BUY: %s %s conf=%.2f",
                    symbol,
                    " + ".join(triggers),
                    confidence,
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
                            f"Momentum BUY {symbol}: "
                            f"{' + '.join(triggers)} "
                            f"({'confluence' if rsi_bullish and volume_strong else 'single trigger'})"
                        ),
                        market_regime=market_regime,
                        position_size_usd=position_size,
                        tier=self.tier,
                    )
                )

            # --- SELL signal ---
            elif current_rsi < self.parameters["rsi_sell_threshold"]:
                confidence = self._calc_sell_confidence(current_rsi)

                logger.info(
                    "Momentum SELL: %s RSI=%.1f conf=%.2f",
                    symbol,
                    current_rsi,
                    confidence,
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
                            f"Momentum SELL {symbol}: "
                            f"RSI={current_rsi:.1f} < {self.parameters['rsi_sell_threshold']}"
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
    def _calc_rsi(prices: np.ndarray, period: int) -> np.ndarray | None:
        """
        Calculate Relative Strength Index.

        Uses exponential moving average (Wilder's smoothing).
        """
        if len(prices) < period + 1:
            return None

        deltas = np.diff(prices)
        gains = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)

        # Wilder's smoothing (EMA with alpha = 1/period)
        alpha = 1.0 / period

        avg_gain = np.zeros(len(deltas))
        avg_loss = np.zeros(len(deltas))

        # Seed with SMA for first period
        avg_gain[period - 1] = np.mean(gains[:period])
        avg_loss[period - 1] = np.mean(losses[:period])

        # EMA from period onward
        for i in range(period, len(deltas)):
            avg_gain[i] = alpha * gains[i] + (1 - alpha) * avg_gain[i - 1]
            avg_loss[i] = alpha * losses[i] + (1 - alpha) * avg_loss[i - 1]

        # Calculate RSI (avoid division by zero)
        rs = np.divide(
            avg_gain[period - 1:],
            avg_loss[period - 1:],
            out=np.full(len(avg_gain[period - 1:]), 100.0),
            where=avg_loss[period - 1:] != 0,
        )
        rsi = 100.0 - (100.0 / (1.0 + rs))

        return rsi

    @staticmethod
    def _calc_volume_ratio(
        volumes: np.ndarray, ma_period: int
    ) -> np.ndarray | None:
        """Calculate volume as a ratio of its moving average."""
        if len(volumes) < ma_period:
            return None

        cumsum = np.cumsum(np.insert(volumes, 0, 0))
        vol_ma = (cumsum[ma_period:] - cumsum[:-ma_period]) / ma_period

        # Ratio of current volume to MA (aligned to end of MA window)
        current_vols = volumes[ma_period - 1:]
        ratio = np.divide(
            current_vols,
            vol_ma,
            out=np.ones(len(current_vols)),
            where=vol_ma != 0,
        )
        return ratio

    @staticmethod
    def _calc_buy_confidence(
        rsi: float,
        vol_ratio: float,
        rsi_bullish: bool,
        volume_strong: bool,
    ) -> float:
        """
        Calculate buy confidence with OR-based confluence.

        Single trigger = base confidence (0.25-0.45).
        Both triggers = boosted confidence (0.45-0.80).
        """
        rsi_score = 0.0
        vol_score = 0.0

        if rsi_bullish:
            # RSI contribution (0.20-0.45): closer to 65 = higher
            rsi_distance_from_sweet = abs(rsi - 65.0)
            rsi_score = max(0.45 - (rsi_distance_from_sweet / 50.0), 0.20)

        if volume_strong:
            # Volume contribution (0.20-0.45): higher ratio = higher
            vol_score = min(0.20 + (vol_ratio - 1.0) / 3.0, 0.45)

        # Confluence bonus: both triggers confirming
        confluence_bonus = 0.10 if (rsi_bullish and volume_strong) else 0.0

        confidence = rsi_score + vol_score + confluence_bonus
        return min(max(confidence, 0.20), 1.0)

    @staticmethod
    def _calc_sell_confidence(rsi: float) -> float:
        """Calculate sell confidence from bearish RSI."""
        rsi_score = max((30.0 - rsi) / 30.0, 0.0) * 0.7
        confidence = rsi_score + 0.2
        return min(max(confidence, 0.2), 1.0)

    @staticmethod
    def _classify_strength(confidence: float) -> SignalStrength:
        """Map confidence to signal strength."""
        if confidence >= 0.8:
            return SignalStrength.STRONG
        elif confidence >= 0.6:
            return SignalStrength.MODERATE
        return SignalStrength.WEAK
