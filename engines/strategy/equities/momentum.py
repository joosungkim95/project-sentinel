"""
Momentum Strategy — Rate of Change + RSI for equities.

Captures trending moves by combining:
1. Rate of Change (ROC) — measures price momentum over N periods
2. Relative Strength Index (RSI) — identifies overbought/oversold
3. Volume confirmation — validates momentum with above-average volume

Signal logic:
- BUY: ROC > threshold AND RSI 50–70 (building, not overbought) AND volume up
- SELL: ROC < -threshold OR RSI < 30 (momentum lost or oversold bounce risk)

Default parameters:
- symbol: QQQ (Nasdaq-100, high momentum characteristics)
- roc_period: 14 (bars)
- rsi_period: 14 (bars)
- roc_threshold: 2.0 (% change to trigger)
- volume_ma_period: 20 (for volume confirmation)
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


class MomentumStrategy(Strategy):
    """
    Momentum strategy using ROC, RSI, and volume confirmation.

    Buys when price momentum is accelerating with confirming volume,
    sells when momentum fades or reversal signals appear.
    """

    def __init__(
        self,
        strategy_id: str = "momentum_qqq",
        parameters: dict[str, Any] | None = None,
    ):
        default_params = {
            "symbol": "QQQ",
            "roc_period": 14,
            "rsi_period": 14,
            "roc_threshold": 2.0,       # Min % ROC to trigger buy
            "rsi_buy_low": 50.0,        # RSI must be above this to buy
            "rsi_buy_high": 70.0,       # RSI must be below this to buy
            "rsi_sell_threshold": 30.0,  # RSI below this → sell
            "volume_ma_period": 20,
            "volume_multiplier": 1.2,   # Volume must be 1.2x average
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
        Generate signals from momentum indicators.

        Args:
            market_data: Must contain 'bars' key with OHLCV data.
                         Each bar: {open, high, low, close, volume}
            market_regime: Current market regime classification.

        Returns:
            List with 0 or 1 Signal.
        """
        bars = market_data.get("bars", [])
        min_bars = max(
            self.parameters["roc_period"],
            self.parameters["rsi_period"],
            self.parameters["volume_ma_period"],
        ) + 2  # Need extra bars for lookback

        if len(bars) < min_bars:
            logger.debug(
                "Not enough bars for momentum: %d < %d",
                len(bars),
                min_bars,
            )
            return []

        closes = np.array([b["close"] for b in bars])
        volumes = np.array([b["volume"] for b in bars])
        current_price = closes[-1]

        # Calculate indicators
        roc = self._calc_roc(closes, self.parameters["roc_period"])
        rsi = self._calc_rsi(closes, self.parameters["rsi_period"])
        vol_ratio = self._calc_volume_ratio(
            volumes, self.parameters["volume_ma_period"]
        )

        if roc is None or rsi is None or vol_ratio is None:
            return []

        current_roc = roc[-1]
        current_rsi = rsi[-1]
        current_vol_ratio = vol_ratio[-1]

        logger.debug(
            "Momentum indicators for %s: ROC=%.2f%%, RSI=%.1f, Vol=%.2fx",
            self.parameters["symbol"],
            current_roc,
            current_rsi,
            current_vol_ratio,
        )

        # --- BUY signal ---
        roc_threshold = self.parameters["roc_threshold"]
        rsi_buy_low = self.parameters["rsi_buy_low"]
        rsi_buy_high = self.parameters["rsi_buy_high"]
        vol_multiplier = self.parameters["volume_multiplier"]

        if (
            current_roc > roc_threshold
            and rsi_buy_low <= current_rsi <= rsi_buy_high
            and current_vol_ratio >= vol_multiplier
        ):
            confidence = self._calc_buy_confidence(
                current_roc, current_rsi, current_vol_ratio
            )
            quantity = self.parameters["position_size_usd"] / current_price

            logger.info(
                "Momentum BUY: %s ROC=%.2f%% RSI=%.1f Vol=%.2fx conf=%.2f",
                self.parameters["symbol"],
                current_roc,
                current_rsi,
                current_vol_ratio,
                confidence,
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
                        f"Momentum BUY: ROC({self.parameters['roc_period']})="
                        f"{current_roc:.2f}% > {roc_threshold}%, "
                        f"RSI({self.parameters['rsi_period']})={current_rsi:.1f} "
                        f"(in {rsi_buy_low}-{rsi_buy_high} zone), "
                        f"Volume {current_vol_ratio:.2f}x avg"
                    ),
                    market_regime=market_regime,
                )
            ]

        # --- SELL signal ---
        rsi_sell = self.parameters["rsi_sell_threshold"]
        prev_roc = roc[-2] if len(roc) >= 2 else 0.0

        if (
            (current_roc < -roc_threshold and prev_roc >= -roc_threshold)
            or current_rsi < rsi_sell
        ):
            confidence = self._calc_sell_confidence(current_roc, current_rsi)

            sell_reason = []
            if current_roc < -roc_threshold:
                sell_reason.append(
                    f"ROC={current_roc:.2f}% crossed below -{roc_threshold}%"
                )
            if current_rsi < rsi_sell:
                sell_reason.append(f"RSI={current_rsi:.1f} < {rsi_sell}")

            logger.info(
                "Momentum SELL: %s %s conf=%.2f",
                self.parameters["symbol"],
                ", ".join(sell_reason),
                confidence,
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
                        f"Momentum SELL: {'; '.join(sell_reason)}"
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
    def _calc_roc(prices: np.ndarray, period: int) -> np.ndarray | None:
        """
        Calculate Rate of Change as percentage.

        ROC = ((price - price_n_periods_ago) / price_n_periods_ago) * 100
        """
        if len(prices) <= period:
            return None
        roc = ((prices[period:] - prices[:-period]) / prices[:-period]) * 100
        return roc

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
        roc: float, rsi: float, vol_ratio: float
    ) -> float:
        """
        Calculate buy confidence from indicator confluence.

        Higher ROC, RSI in sweet spot, and strong volume = higher confidence.
        """
        # ROC contribution (0–0.4): stronger momentum = higher
        roc_score = min(abs(roc) / 10.0, 0.4)

        # RSI contribution (0–0.3): closer to 60 (sweet spot) = higher
        rsi_distance_from_sweet = abs(rsi - 60.0)
        rsi_score = max(0.3 - (rsi_distance_from_sweet / 50.0), 0.0)

        # Volume contribution (0–0.3): higher ratio = higher
        vol_score = min((vol_ratio - 1.0) / 2.0, 0.3)

        confidence = roc_score + rsi_score + vol_score
        return min(max(confidence, 0.1), 1.0)

    @staticmethod
    def _calc_sell_confidence(roc: float, rsi: float) -> float:
        """Calculate sell confidence from bearish indicators."""
        roc_score = min(abs(roc) / 10.0, 0.5)
        rsi_score = max((30.0 - rsi) / 30.0, 0.0) * 0.5
        confidence = roc_score + rsi_score
        return min(max(confidence, 0.2), 1.0)

    @staticmethod
    def _classify_strength(confidence: float) -> SignalStrength:
        """Map confidence to signal strength."""
        if confidence >= 0.8:
            return SignalStrength.STRONG
        elif confidence >= 0.6:
            return SignalStrength.MODERATE
        return SignalStrength.WEAK
