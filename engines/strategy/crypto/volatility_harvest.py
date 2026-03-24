"""
Volatility Harvesting Strategy — Sells volatility in crypto markets.

Exploits the tendency of crypto volatility to spike and then contract.
When volatility is extremely high, this strategy enters positions
expecting a return to normal volatility levels.

Signal logic:
- BUY: Bollinger Band width is contracting after a spike (volatility crush)
       AND ATR is declining from a peak (calming down)
- SELL: Bollinger Band width expanding again (new vol spike)
       OR stop-loss hit

Key insight: Crypto vol clusters — after a spike, there's often a
period of compression that benefits from entering mean-reversion
positions at reduced-vol prices.

Default parameters:
- symbol: BTC-USD
- bb_period: 20
- bb_std: 2.0
- atr_period: 14
- vol_spike_threshold: 1.5 (BB width must be 1.5x its 20-period average)
- vol_crush_threshold: 0.8 (BB width must contract to 0.8x average)
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


class VolatilityHarvestStrategy(Strategy):
    """
    Volatility harvesting strategy for crypto.

    Enters after volatility spikes and contracts, expecting
    mean reversion during the subsequent low-vol period.
    """

    def __init__(
        self,
        strategy_id: str = "vol_harvest_btc",
        parameters: dict[str, Any] | None = None,
    ):
        default_params = {
            "symbol": "BTC-USD",
            "bb_period": 20,
            "bb_std": 2.0,
            "atr_period": 14,
            "vol_spike_threshold": 1.5,    # Width must have been 1.5x avg
            "vol_crush_threshold": 0.8,    # Width must now be 0.8x avg
            "atr_decline_pct": 20.0,       # ATR must have declined 20% from peak
            "position_size_usd": 150.0,
            "stop_loss_atr_mult": 2.5,     # Wide stops for crypto
        }
        if parameters:
            default_params.update(parameters)

        super().__init__(
            strategy_id=strategy_id,
            asset_class=AssetClass.CRYPTO,
            parameters=default_params,
        )

    async def generate_signals(
        self,
        market_data: dict[str, Any],
        market_regime: MarketRegime,
    ) -> list[Signal]:
        """
        Generate signals based on volatility contraction patterns.

        Looks for the sequence: vol spike → vol crush → enter position.
        """
        bars = market_data.get("bars", [])
        lookback = self.parameters["bb_period"] + 20  # Need extra for vol avg

        if len(bars) < lookback:
            return []

        closes = np.array([b["close"] for b in bars])
        highs = np.array([b["high"] for b in bars])
        lows = np.array([b["low"] for b in bars])
        current_price = closes[-1]

        # Calculate indicators
        bb_width = self._calc_bb_width(
            closes, self.parameters["bb_period"], self.parameters["bb_std"]
        )
        atr = self._calc_atr(highs, lows, closes, self.parameters["atr_period"])

        if bb_width is None or atr is None or len(bb_width) < 20:
            return []

        current_width = bb_width[-1]
        avg_width = np.mean(bb_width[-20:])
        width_ratio = current_width / avg_width if avg_width > 0 else 1.0

        recent_peak_width = max(bb_width[-20:])
        peak_ratio = recent_peak_width / avg_width if avg_width > 0 else 1.0

        current_atr = atr[-1]
        recent_peak_atr = max(atr[-20:]) if len(atr) >= 20 else max(atr)
        atr_decline = (
            (recent_peak_atr - current_atr) / recent_peak_atr * 100
            if recent_peak_atr > 0 else 0
        )

        logger.debug(
            "VolHarvest %s: width_ratio=%.2f peak_ratio=%.2f atr_decline=%.1f%%",
            self.parameters["symbol"], width_ratio, peak_ratio, atr_decline,
        )

        spike_thresh = self.parameters["vol_spike_threshold"]
        crush_thresh = self.parameters["vol_crush_threshold"]
        atr_decline_thresh = self.parameters["atr_decline_pct"]

        # BUY: Recent vol spike + current vol crush + ATR declining
        had_spike = peak_ratio >= spike_thresh
        now_crushed = width_ratio <= crush_thresh
        atr_calming = atr_decline >= atr_decline_thresh

        if had_spike and now_crushed and atr_calming:
            confidence = self._calc_confidence(
                width_ratio, peak_ratio, atr_decline
            )
            quantity = self.parameters["position_size_usd"] / current_price
            stop = current_price - (
                current_atr * self.parameters["stop_loss_atr_mult"]
            )

            return [
                Signal(
                    strategy_id=self.strategy_id,
                    asset_class=self.asset_class,
                    symbol=self.parameters["symbol"],
                    side=Side.BUY,
                    quantity=round(quantity, 8),
                    target_price=current_price,
                    stop_loss=round(stop, 2),
                    confidence=confidence,
                    strength=self._classify_strength(confidence),
                    rationale=(
                        f"Vol Harvest BUY: BB width ratio {width_ratio:.2f} "
                        f"(crush after peak {peak_ratio:.2f}x avg). "
                        f"ATR declined {atr_decline:.1f}% from peak. "
                        f"Expecting mean reversion in calmer conditions."
                    ),
                    market_regime=market_regime,
                )
            ]

        # SELL: Vol expanding again (new spike starting)
        if width_ratio > spike_thresh:
            return [
                Signal(
                    strategy_id=self.strategy_id,
                    asset_class=self.asset_class,
                    symbol=self.parameters["symbol"],
                    side=Side.SELL,
                    quantity=0,
                    target_price=current_price,
                    confidence=min(width_ratio / 2, 1.0),
                    strength=SignalStrength.MODERATE,
                    rationale=(
                        f"Vol Harvest SELL: BB width ratio {width_ratio:.2f} "
                        f"exceeds spike threshold ({spike_thresh}). "
                        f"New volatility expansion detected."
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
    def _calc_bb_width(
        prices: np.ndarray, period: int, num_std: float
    ) -> np.ndarray | None:
        """Calculate Bollinger Band width (upper - lower) / middle."""
        if len(prices) < period:
            return None

        widths = []
        for i in range(period, len(prices) + 1):
            window = prices[i - period:i]
            middle = np.mean(window)
            std = np.std(window)
            if middle > 0:
                width = (2 * num_std * std) / middle
            else:
                width = 0.0
            widths.append(width)

        return np.array(widths)

    @staticmethod
    def _calc_atr(
        highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int
    ) -> np.ndarray | None:
        """Calculate Average True Range."""
        n = len(closes)
        if n < period + 1:
            return None

        tr = np.zeros(n - 1)
        for i in range(1, n):
            tr[i - 1] = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )

        atr = np.zeros(len(tr) - period + 1)
        atr[0] = np.mean(tr[:period])
        for i in range(1, len(atr)):
            atr[i] = ((atr[i - 1] * (period - 1)) + tr[period - 1 + i]) / period

        return atr

    @staticmethod
    def _calc_confidence(
        width_ratio: float, peak_ratio: float, atr_decline: float
    ) -> float:
        """Confidence based on how clear the vol crush pattern is."""
        # Bigger crush = more confident (0–0.4)
        crush_score = min(max(1.0 - width_ratio, 0) * 2, 0.4)
        # Bigger prior spike = clearer pattern (0–0.3)
        spike_score = min(max(peak_ratio - 1.0, 0) / 2, 0.3)
        # Bigger ATR decline = more confirmation (0–0.3)
        atr_score = min(atr_decline / 100, 0.3)

        confidence = crush_score + spike_score + atr_score
        return min(max(confidence, 0.1), 1.0)

    @staticmethod
    def _classify_strength(confidence: float) -> SignalStrength:
        if confidence >= 0.8:
            return SignalStrength.STRONG
        elif confidence >= 0.6:
            return SignalStrength.MODERATE
        return SignalStrength.WEAK
