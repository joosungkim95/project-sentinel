"""
Market Regime Tracker — Classifies and persists market regime changes.

Tracks whether each asset class is trending up/down, ranging, or in
high volatility. Regime classification drives strategy selection and
risk parameter adjustment.

Also provides classify_from_bars() for stateless, indicator-based
regime detection from OHLCV price data (no trade history needed).
"""

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from data.models import MarketRegimeRecord
from engines.models import AssetClass, MarketRegime

logger = logging.getLogger(__name__)


class MarketRegimeTracker:
    """
    Tracks and classifies market conditions over time.

    Each asset class has an independent regime classification. When a regime
    changes, the old one is ended and a new one started.
    """

    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    async def get_current_regime(
        self, asset_class: AssetClass
    ) -> MarketRegime:
        """
        Get the current market regime for an asset class.

        Args:
            asset_class: Which asset class to check.

        Returns:
            The current MarketRegime enum value.
        """
        stmt = (
            select(MarketRegimeRecord)
            .where(
                MarketRegimeRecord.asset_class == asset_class.value,
                MarketRegimeRecord.ended_at.is_(None),
            )
            .order_by(MarketRegimeRecord.started_at.desc())
            .limit(1)
        )
        result = await self.db.execute(stmt)
        record = result.scalar_one_or_none()
        if not record:
            return MarketRegime.UNKNOWN

        try:
            return MarketRegime(record.regime_type)
        except ValueError:
            return MarketRegime.UNKNOWN

    async def update_regime(
        self,
        asset_class: AssetClass,
        new_regime: MarketRegime,
        confidence: float,
        indicators: Optional[dict] = None,
    ) -> bool:
        """
        Update the market regime for an asset class.

        Only creates a new record if the regime has actually changed.

        Args:
            asset_class: Which asset class.
            new_regime: The newly detected regime.
            confidence: Confidence level (0.0 to 1.0).
            indicators: Supporting indicator values (e.g., VIX, ADX).

        Returns:
            True if the regime changed, False if it stayed the same.
        """
        current = await self._get_current_record(asset_class)

        if current and current.regime_type == new_regime.value:
            # Same regime — update confidence and indicators
            current.confidence = confidence
            if indicators:
                current.indicators = indicators
            await self.db.flush()
            return False

        # Regime changed — close old, start new
        now = datetime.now(timezone.utc)

        if current:
            current.ended_at = now
            started = current.started_at
            if started and started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            logger.info(
                "Regime ended: %s %s (lasted %s)",
                asset_class.value,
                current.regime_type,
                (now - started) if started else "unknown",
            )

        new_record = MarketRegimeRecord(
            asset_class=asset_class.value,
            regime_type=new_regime.value,
            confidence=confidence,
            indicators=indicators or {},
            started_at=now,
        )
        self.db.add(new_record)
        await self.db.flush()

        logger.info(
            "New regime: %s → %s (confidence: %.2f)",
            asset_class.value, new_regime.value, confidence,
        )
        return True

    async def get_regime_history(
        self,
        asset_class: Optional[AssetClass] = None,
        days: int = 30,
    ) -> list[MarketRegimeRecord]:
        """
        Get regime history, optionally filtered by asset class.

        Args:
            asset_class: Filter to a specific asset class, or None for all.
            days: Lookback period.

        Returns:
            List of regime records, newest first.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        stmt = (
            select(MarketRegimeRecord)
            .where(MarketRegimeRecord.started_at >= cutoff)
            .order_by(MarketRegimeRecord.started_at.desc())
        )
        if asset_class:
            stmt = stmt.where(
                MarketRegimeRecord.asset_class == asset_class.value
            )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_regime_duration(
        self, asset_class: AssetClass
    ) -> Optional[timedelta]:
        """
        Get how long the current regime has been active.

        Returns:
            Duration as timedelta, or None if no current regime.
        """
        current = await self._get_current_record(asset_class)
        if not current or not current.started_at:
            return None
        started = current.started_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - started

    async def _get_current_record(
        self, asset_class: AssetClass
    ) -> Optional[MarketRegimeRecord]:
        """Get the current (open-ended) regime record for an asset class."""
        stmt = (
            select(MarketRegimeRecord)
            .where(
                MarketRegimeRecord.asset_class == asset_class.value,
                MarketRegimeRecord.ended_at.is_(None),
            )
            .order_by(MarketRegimeRecord.started_at.desc())
            .limit(1)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Stateless indicator-based regime classification
# ---------------------------------------------------------------------------

# Minimum bars needed for classification (SMA-20 + ATR-14 overlap)
_MIN_BARS = 30

# SMA slope thresholds (annualized % change per bar)
_TREND_SLOPE_THRESHOLD = 0.15  # 0.15% per bar ≈ noticeable trend
_STRONG_TREND_SLOPE = 0.40     # 0.40% per bar ≈ strong trend

# ATR / price ratio thresholds for volatility classification
_HIGH_VOL_RATIO = 0.035  # ATR > 3.5% of price → high volatility
_LOW_VOL_RATIO = 0.010   # ATR < 1.0% of price → calm / ranging


def classify_from_bars(
    bars: list[dict],
    sma_period: int = 20,
    atr_period: int = 14,
) -> tuple[MarketRegime, float, dict]:
    """
    Classify market regime from OHLCV bar data using technical indicators.

    Uses SMA slope for trend direction and ATR/price ratio for volatility.
    No trade history or database access required.

    Args:
        bars: List of OHLCV dicts with keys: open, high, low, close.
              Must be in chronological order (oldest first).
        sma_period: Period for SMA trend calculation.
        atr_period: Period for ATR volatility calculation.

    Returns:
        Tuple of (regime, confidence, indicators_dict).
        indicators_dict contains sma_slope_pct, atr_ratio, sma_current
        for observability.
    """
    if len(bars) < _MIN_BARS:
        return MarketRegime.UNKNOWN, 0.0, {}

    closes = [float(b["close"]) for b in bars]
    highs = [float(b["high"]) for b in bars]
    lows = [float(b["low"]) for b in bars]

    # SMA slope: compare current SMA to SMA from half-period ago
    sma_now = sum(closes[-sma_period:]) / sma_period
    half = sma_period // 2
    sma_prev = sum(closes[-(sma_period + half) : -half]) / sma_period
    if sma_prev > 0:
        sma_slope_pct = (sma_now - sma_prev) / sma_prev * 100
    else:
        sma_slope_pct = 0.0

    # ATR: average true range over last atr_period bars
    true_ranges = []
    for i in range(-atr_period, 0):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        true_ranges.append(tr)
    atr = sum(true_ranges) / len(true_ranges)
    current_price = closes[-1]
    atr_ratio = atr / current_price if current_price > 0 else 0.0

    indicators = {
        "sma_slope_pct": round(sma_slope_pct, 4),
        "atr_ratio": round(atr_ratio, 5),
        "sma_current": round(sma_now, 2),
        "atr": round(atr, 4),
        "price": round(current_price, 2),
    }

    # High volatility takes priority
    if atr_ratio > _HIGH_VOL_RATIO:
        confidence = min(atr_ratio / (_HIGH_VOL_RATIO * 2), 0.95)
        return MarketRegime.HIGH_VOLATILITY, confidence, indicators

    # Trend detection from SMA slope
    abs_slope = abs(sma_slope_pct)
    if abs_slope >= _TREND_SLOPE_THRESHOLD:
        # Scale confidence: threshold → 0.5, strong → 0.9
        slope_ratio = min(abs_slope / _STRONG_TREND_SLOPE, 1.0)
        confidence = 0.5 + slope_ratio * 0.4

        if sma_slope_pct > 0:
            return MarketRegime.TRENDING_UP, confidence, indicators
        else:
            return MarketRegime.TRENDING_DOWN, confidence, indicators

    # Low slope + low vol → ranging
    confidence = 0.5 + (1.0 - abs_slope / _TREND_SLOPE_THRESHOLD) * 0.3
    return MarketRegime.RANGING, confidence, indicators
