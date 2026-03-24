"""
Strategy Journal — Records strategy hypotheses, test results, and lessons.

Tracks the full lifecycle of strategy ideas: proposed → backtested →
paper_testing → active → disabled/graveyard. This is how the Learning
Engine remembers what worked, what failed, and why.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from data.models import StrategyHypothesisRecord

logger = logging.getLogger(__name__)


class StrategyJournal:
    """
    Persistent journal for strategy hypotheses and their outcomes.

    Supports the full lifecycle: propose → backtest → paper trade → activate/disable.
    """

    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    async def propose_hypothesis(
        self,
        hypothesis_text: str,
        source: str,
        market_regime: Optional[str] = None,
    ) -> int:
        """
        Record a new strategy hypothesis.

        Args:
            hypothesis_text: Description of the strategy idea.
            source: Where it came from (e.g., "claude_weekly_review", "manual").
            market_regime: The market regime this hypothesis targets.

        Returns:
            Database ID of the hypothesis record.
        """
        record = StrategyHypothesisRecord(
            hypothesis_text=hypothesis_text,
            source=source,
            market_regime=market_regime,
            status="proposed",
        )
        self.db.add(record)
        await self.db.flush()
        logger.info(
            "New hypothesis #%d: %s (source: %s)",
            record.id, hypothesis_text[:80], source,
        )
        return record.id

    async def record_backtest_result(
        self,
        hypothesis_id: int,
        sharpe_ratio: float,
        max_drawdown: float,
    ) -> None:
        """
        Record backtest results for a hypothesis.

        Args:
            hypothesis_id: ID of the hypothesis.
            sharpe_ratio: Sharpe ratio from backtest.
            max_drawdown: Max drawdown from backtest.
        """
        stmt = select(StrategyHypothesisRecord).where(
            StrategyHypothesisRecord.id == hypothesis_id
        )
        result = await self.db.execute(stmt)
        record = result.scalar_one_or_none()
        if not record:
            logger.warning("Hypothesis #%d not found", hypothesis_id)
            return

        record.backtest_sharpe = sharpe_ratio
        record.backtest_max_dd = max_drawdown
        record.status = "backtested"
        await self.db.flush()
        logger.info(
            "Backtest results for #%d: sharpe=%.2f, max_dd=%.2f%%",
            hypothesis_id, sharpe_ratio, max_drawdown,
        )

    async def start_paper_trading(self, hypothesis_id: int) -> None:
        """Move a hypothesis to paper trading status."""
        await self._update_status(hypothesis_id, "paper_testing")

    async def record_paper_result(
        self,
        hypothesis_id: int,
        days: int,
        pnl: float,
    ) -> None:
        """
        Record paper trading results.

        Args:
            hypothesis_id: ID of the hypothesis.
            days: Number of days paper traded.
            pnl: Total P&L from paper trading.
        """
        stmt = select(StrategyHypothesisRecord).where(
            StrategyHypothesisRecord.id == hypothesis_id
        )
        result = await self.db.execute(stmt)
        record = result.scalar_one_or_none()
        if not record:
            return

        record.paper_trade_days = days
        record.paper_trade_pnl = pnl
        await self.db.flush()
        logger.info(
            "Paper results for #%d: %d days, P&L=%.2f",
            hypothesis_id, days, pnl,
        )

    async def activate(self, hypothesis_id: int) -> None:
        """Promote a hypothesis to active strategy."""
        await self._update_status(hypothesis_id, "active")

    async def disable(self, hypothesis_id: int) -> None:
        """Disable a strategy (can be resurrected later)."""
        await self._update_status(hypothesis_id, "disabled")

    async def send_to_graveyard(self, hypothesis_id: int) -> None:
        """Send a strategy to the graveyard (permanent disable)."""
        await self._update_status(hypothesis_id, "graveyard")

    async def get_active_hypotheses(self) -> list[StrategyHypothesisRecord]:
        """Get all hypotheses that are actively being tested or traded."""
        stmt = (
            select(StrategyHypothesisRecord)
            .where(
                StrategyHypothesisRecord.status.in_(
                    ["proposed", "backtested", "paper_testing", "active"]
                )
            )
            .order_by(StrategyHypothesisRecord.created_at.desc())
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_graveyard(
        self, limit: int = 20
    ) -> list[StrategyHypothesisRecord]:
        """Get disabled and graveyarded strategies for review."""
        stmt = (
            select(StrategyHypothesisRecord)
            .where(
                StrategyHypothesisRecord.status.in_(["disabled", "graveyard"])
            )
            .order_by(StrategyHypothesisRecord.updated_at.desc())
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_recent_proposals(
        self, days: int = 30, limit: int = 10
    ) -> list[StrategyHypothesisRecord]:
        """Get recently proposed hypotheses."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        stmt = (
            select(StrategyHypothesisRecord)
            .where(StrategyHypothesisRecord.created_at >= cutoff)
            .order_by(StrategyHypothesisRecord.created_at.desc())
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def _update_status(self, hypothesis_id: int, status: str) -> None:
        """Update the status of a hypothesis."""
        stmt = (
            update(StrategyHypothesisRecord)
            .where(StrategyHypothesisRecord.id == hypothesis_id)
            .values(status=status)
        )
        await self.db.execute(stmt)
        await self.db.flush()
        logger.info("Hypothesis #%d → %s", hypothesis_id, status)
