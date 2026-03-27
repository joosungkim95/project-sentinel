"""
Breakout Detector Strategy — Price breakouts with volume as confluence for crypto.

SCOUT tier: frequent scans on 1-hour bars across all crypto symbols.

Detects price breakouts above/below recent range. Volume confirms but
doesn't gate — a breakout without volume fires at lower confidence.

Signal logic (OR-relaxed):
- BUY: Price > highest high of last 20 bars (primary trigger)
  Volume > 1.3x average = confluence boost, not hard requirement
- SELL: Price < lowest low of last 20 bars (primary trigger)

Default parameters:
- symbols: all 5 crypto symbols
- lookback: 20 bars
- volume_mult: 1.3 (confluence threshold)
- position_size_usd: 75.0
- stop_loss_pct: 2.5
"""

import logging
from typing import Any

import numpy as np

from config.symbols import CRYPTO_SYMBOLS
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


class BreakoutStrategy(Strategy):
    """
    Breakout detection strategy for crypto.

    Scans for price breaking above recent highs or below recent lows
    with volume confirmation. Scout tier: small positions, frequent checks.
    """

    def __init__(
        self,
        strategy_id: str = "breakout_crypto",
        parameters: dict[str, Any] | None = None,
    ):
        default_params = {
            "lookback": 20,
            "volume_mult": 1.3,
            "position_size_usd": 75.0,
            "stop_loss_pct": 2.5,
        }
        if parameters:
            default_params.update(parameters)

        super().__init__(
            strategy_id=strategy_id,
            asset_class=AssetClass.CRYPTO,
            parameters=default_params,
            tier=StrategyTier.SCOUT,
            symbols=CRYPTO_SYMBOLS,
            timeframe="1Hour",
            max_signals_per_cycle=3,
        )

    async def generate_signals(
        self,
        bars: dict[str, list[dict]],
        market_regime: MarketRegime,
    ) -> list[Signal]:
        """
        Generate breakout signals across all symbols.

        Args:
            bars: Bar data keyed by symbol, each value a list of OHLCV dicts.
            market_regime: Current market regime classification.

        Returns:
            List of Signal objects (may be empty if no breakouts detected).
        """
        all_signals: list[Signal] = []

        for symbol, symbol_bars in bars.items():
            signals = self._analyze_symbol(symbol, symbol_bars, market_regime)
            all_signals.extend(signals)
            if len(all_signals) >= self.max_signals_per_cycle:
                break

        return all_signals[: self.max_signals_per_cycle]

    def _analyze_symbol(
        self,
        symbol: str,
        symbol_bars: list[dict],
        market_regime: MarketRegime,
    ) -> list[Signal]:
        """Analyze a single symbol for breakout signals."""
        lookback = self.parameters["lookback"]
        min_bars = lookback + 1  # Need lookback window + current bar

        if len(symbol_bars) < min_bars:
            logger.debug(
                "Not enough bars for breakout %s: %d < %d",
                symbol, len(symbol_bars), min_bars,
            )
            return []

        # Extract price and volume arrays
        highs = np.array([b["high"] for b in symbol_bars])
        lows = np.array([b["low"] for b in symbol_bars])
        closes = np.array([b["close"] for b in symbol_bars])
        volumes = np.array([b["volume"] for b in symbol_bars])

        current_price = closes[-1]
        current_volume = volumes[-1]

        # Lookback window (excluding the current bar)
        window_highs = highs[-(lookback + 1) : -1]
        window_lows = lows[-(lookback + 1) : -1]
        window_volumes = volumes[-(lookback + 1) : -1]

        highest_high = np.max(window_highs)
        lowest_low = np.min(window_lows)
        avg_volume = np.mean(window_volumes)
        range_width = highest_high - lowest_low

        volume_mult = self.parameters["volume_mult"]
        volume_confirmed = (
            current_volume > avg_volume * volume_mult if avg_volume > 0 else False
        )

        logger.debug(
            "Breakout %s: price=%.2f high=%.2f low=%.2f vol=%s avg_vol=%.0f",
            symbol, current_price, highest_high, lowest_low,
            "CONFIRMED" if volume_confirmed else "low",
            avg_volume,
        )

        # --- BUY breakout: price above highest high (volume boosts confidence) ---
        if current_price > highest_high:
            breakout_magnitude = (
                (current_price - highest_high) / highest_high
                if highest_high > 0
                else 0.0
            )
            volume_strength = (
                (current_volume / avg_volume - 1.0) if avg_volume > 0 else 0.0
            )
            range_pct = range_width / current_price if current_price > 0 else 0.0

            confidence = self._calc_confidence(
                breakout_magnitude, volume_strength, range_pct,
                volume_confirmed,
            )
            quantity = self.parameters["position_size_usd"] / current_price
            stop_loss = current_price * (1 - self.parameters["stop_loss_pct"] / 100)

            vol_label = (
                f"{current_volume / avg_volume:.1f}x avg (confirmed)"
                if volume_confirmed
                else f"{current_volume / avg_volume:.1f}x avg"
            )

            logger.info(
                "Breakout BUY: %s price=%.2f > high=%.2f, vol=%s",
                symbol, current_price, highest_high, vol_label,
            )

            return [
                Signal(
                    strategy_id=self.strategy_id,
                    asset_class=self.asset_class,
                    symbol=symbol,
                    side=Side.BUY,
                    quantity=round(quantity, 8),
                    target_price=current_price,
                    stop_loss=round(stop_loss, 2),
                    confidence=confidence,
                    strength=self._classify_strength(confidence),
                    rationale=(
                        f"Breakout BUY: price ${current_price:,.2f} broke above "
                        f"20-bar high ${highest_high:,.2f} "
                        f"(+{breakout_magnitude * 100:.2f}%). "
                        f"Volume {vol_label}."
                    ),
                    market_regime=market_regime,
                    position_size_usd=self.parameters["position_size_usd"],
                    tier=self.tier,
                )
            ]

        # --- SELL breakout: price below lowest low ---
        if current_price < lowest_low:
            breakout_magnitude = (
                (lowest_low - current_price) / lowest_low
                if lowest_low > 0
                else 0.0
            )
            volume_strength = (
                (current_volume / avg_volume - 1.0) if avg_volume > 0 else 0.0
            )
            range_pct = range_width / current_price if current_price > 0 else 0.0

            confidence = self._calc_confidence(
                breakout_magnitude, volume_strength, range_pct,
                volume_confirmed,
            )

            logger.info(
                "Breakout SELL: %s price=%.2f < low=%.2f, vol_confirmed=%s",
                symbol, current_price, lowest_low, volume_confirmed,
            )

            return [
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
                        f"Breakout SELL: price ${current_price:,.2f} broke below "
                        f"20-bar low ${lowest_low:,.2f} "
                        f"(-{breakout_magnitude * 100:.2f}%)."
                    ),
                    market_regime=market_regime,
                    position_size_usd=self.parameters["position_size_usd"],
                    tier=self.tier,
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
    def _calc_confidence(
        breakout_magnitude: float,
        volume_strength: float,
        range_pct: float,
        volume_confirmed: bool,
    ) -> float:
        """
        Calculate confidence from breakout characteristics.

        Price breakout is primary (0.20-0.45 alone).
        Volume confirmation adds a confluence bonus.
        """
        # Breakout magnitude (0.20-0.45): bigger breakout = more confident
        mag_score = min(0.20 + breakout_magnitude * 8, 0.45)

        # Range width (0-0.20): wider range = more significant breakout
        range_score = min(range_pct * 4, 0.20)

        # Volume as confluence (0 or 0.10-0.25)
        vol_score = 0.0
        if volume_confirmed:
            vol_score = min(0.10 + volume_strength / 4.0, 0.25)

        confidence = mag_score + range_score + vol_score
        return min(max(confidence, 0.20), 1.0)

    @staticmethod
    def _classify_strength(confidence: float) -> SignalStrength:
        """Map confidence to signal strength."""
        if confidence >= 0.8:
            return SignalStrength.STRONG
        elif confidence >= 0.6:
            return SignalStrength.MODERATE
        return SignalStrength.WEAK
