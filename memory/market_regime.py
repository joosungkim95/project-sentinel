"""
Market Regime Tracker — Classifies and persists market regime changes.

Tracks whether each asset class is trending up/down, ranging, or in
high volatility. Regime classification drives strategy selection and
risk parameter adjustment.
"""

import logging
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
