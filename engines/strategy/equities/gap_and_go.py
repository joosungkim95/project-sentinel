"""
Gap and Go Strategy — Trade gap continuations at market open.

SCOUT tier: frequent checks on 15-min bars for gap setups.

Detects stocks that gapped up or down significantly from the previous
close and trades the continuation of that gap direction. Gaps driven
by earnings, news, or large overnight moves tend to continue rather
than fill in the first few hours.

Signal logic:
- BUY: Stock gapped up >= gap_threshold_pct from previous close
  AND current price is still above the gap level (holding the gap)
  AND volume is elevated (confirming institutional interest)
- SELL: Price fills back below the gap level (gap failed)

Default parameters:
- symbols: all 7 equity symbols
- gap_threshold_pct: 1.5 (minimum 1.5% gap to trigger)
- max_gap_pct: 8.0 (skip extreme gaps — likely to reverse)
- position_size_usd: 100.0
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


class GapAndGoStrategy(Strategy):
    """
    Gap and Go strategy for equities.

    Detects significant price gaps at market open and trades the
    continuation direction. Scout tier: small positions, fast frequency.
    """

    def __init__(
        self,
        strategy_id: str = "gap_and_go_equity",
        parameters: dict[str, Any] | None = None,
    ):
        default_params = {
            "gap_threshold_pct": 1.5,    # Min gap % to trigger
            "max_gap_pct": 8.0,          # Skip extreme gaps
            "volume_mult": 1.2,          # Volume confirmation threshold
            "hold_ratio": 0.5,           # Price must hold at least 50% of gap
            "position_size_usd": 100.0,
            "stop_loss_pct": 1.5,        # Tight stop for gap trades
            "lookback_bars": 5,          # Recent bars to confirm gap holding
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
            max_signals_per_cycle=2,
        )

    async def generate_signals(
        self,
        bars: dict[str, list[dict]],
        market_regime: MarketRegime,
    ) -> list[Signal]:
        """Generate gap-and-go signals across equity symbols."""
        all_signals: list[Signal] = []

        for symbol in self.symbols:
            symbol_bars = bars.get(symbol, [])
            if len(symbol_bars) < 10:
                continue

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
        """Analyze a single symbol for gap setups."""
        closes = np.array([b["close"] for b in symbol_bars])
        opens = np.array([b["open"] for b in symbol_bars])
        volumes = np.array([b["volume"] for b in symbol_bars])

        current_price = closes[-1]

        # Detect gap: compare today's first bar open to yesterday's close.
        # With 15-min bars, we look for a significant jump between bars
        # that likely represents the overnight/session gap.
        # Use the largest gap in recent bars as the gap candidate.
        gap_pct, gap_direction, prev_close = self._detect_gap(
            opens, closes, self.parameters["lookback_bars"],
        )

        if gap_pct is None:
            return []

        gap_threshold = self.parameters["gap_threshold_pct"]
        max_gap = self.parameters["max_gap_pct"]

        logger.debug(
            "Gap %s: gap=%.2f%% direction=%s prev_close=%.2f price=%.2f",
            symbol, gap_pct, gap_direction, prev_close, current_price,
        )

        # Filter: gap must be significant but not extreme
        if gap_pct < gap_threshold or gap_pct > max_gap:
            return []

        # Check if gap is holding
        hold_ratio = self.parameters["hold_ratio"]
        gap_amount = prev_close * (gap_pct / 100)

        if gap_direction == "up":
            gap_level = prev_close + gap_amount * hold_ratio
            gap_holding = current_price >= gap_level
        else:
            gap_level = prev_close - gap_amount * hold_ratio
            gap_holding = current_price <= gap_level

        if not gap_holding:
            return []

        # Volume confirmation (confluence, not hard gate)
        vol_mult = self.parameters["volume_mult"]
        recent_vol = np.mean(volumes[-3:]) if len(volumes) >= 3 else volumes[-1]
        avg_vol = np.mean(volumes[:-3]) if len(volumes) > 3 else np.mean(volumes)
        vol_ratio = recent_vol / avg_vol if avg_vol > 0 else 1.0
        volume_confirmed = vol_ratio >= vol_mult

        # Calculate confidence
        confidence = self._calc_confidence(
            gap_pct, gap_holding, volume_confirmed, vol_ratio,
        )

        position_size = self.parameters["position_size_usd"]
        quantity = position_size / current_price
        stop_pct = self.parameters["stop_loss_pct"]

        if gap_direction == "up":
            stop_loss = current_price * (1 - stop_pct / 100)
            side = Side.BUY
        else:
            stop_loss = current_price * (1 + stop_pct / 100)
            side = Side.SELL

        triggers = [f"gap {gap_direction} {gap_pct:.1f}%", "holding"]
        if volume_confirmed:
            triggers.append(f"vol {vol_ratio:.1f}x")

        logger.info(
            "Gap&Go %s: %s %s conf=%.2f",
            side.value.upper(), symbol, " + ".join(triggers), confidence,
        )

        return [
            Signal(
                strategy_id=self.strategy_id,
                asset_class=self.asset_class,
                symbol=symbol,
                side=side,
                quantity=round(quantity, 2),
                target_price=current_price,
                stop_loss=round(stop_loss, 2),
                confidence=confidence,
                strength=self._classify_strength(confidence),
                rationale=(
                    f"Gap&Go {side.value.upper()} {symbol}: "
                    f"{' + '.join(triggers)} from ${prev_close:.2f}. "
                    f"Current ${current_price:.2f}."
                ),
                market_regime=market_regime,
                position_size_usd=position_size,
                tier=self.tier,
            )
        ]

    @staticmethod
    def _detect_gap(
        opens: np.ndarray,
        closes: np.ndarray,
        lookback: int,
    ) -> tuple[float | None, str | None, float | None]:
        """
        Detect the largest inter-bar gap in the lookback window.

        Returns (gap_pct, direction, prev_close) or (None, None, None).
        """
        if len(opens) < 2:
            return None, None, None

        # Look for gaps between consecutive bars (close -> next open)
        best_gap_pct = 0.0
        best_direction = None
        best_prev_close = None

        start = max(0, len(opens) - lookback)
        for i in range(start + 1, len(opens)):
            prev_close = closes[i - 1]
            if prev_close <= 0:
                continue

            current_open = opens[i]
            gap = (current_open - prev_close) / prev_close * 100

            if abs(gap) > best_gap_pct:
                best_gap_pct = abs(gap)
                best_direction = "up" if gap > 0 else "down"
                best_prev_close = prev_close

        if best_gap_pct < 0.5:  # Ignore trivial gaps
            return None, None, None

        return best_gap_pct, best_direction, best_prev_close

    @staticmethod
    def _calc_confidence(
        gap_pct: float,
        gap_holding: bool,
        volume_confirmed: bool,
        vol_ratio: float,
    ) -> float:
        """
        Confidence based on gap size, holding behavior, and volume.

        Gap holding is primary. Volume is confluence boost.
        """
        # Gap size (0.20-0.40): moderate gaps are best
        # Sweet spot: 2-5% gaps
        if gap_pct <= 5.0:
            gap_score = min(0.20 + gap_pct / 15.0, 0.40)
        else:
            # Larger gaps = slightly less confident (more prone to reversal)
            gap_score = max(0.40 - (gap_pct - 5.0) / 20.0, 0.25)

        # Holding bonus (0.10-0.20)
        hold_score = 0.15 if gap_holding else 0.0

        # Volume confluence (0 or 0.10-0.20)
        vol_score = 0.0
        if volume_confirmed:
            vol_score = min(0.10 + (vol_ratio - 1.0) / 5.0, 0.20)

        confidence = gap_score + hold_score + vol_score
        return min(max(confidence, 0.20), 1.0)

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
    def _classify_strength(confidence: float) -> SignalStrength:
        """Map confidence to signal strength."""
        if confidence >= 0.8:
            return SignalStrength.STRONG
        elif confidence >= 0.6:
            return SignalStrength.MODERATE
        return SignalStrength.WEAK
