"""
Strategy Graveyard — Manages disabled strategies and resurrection.

Strategies get disabled when they underperform. But market conditions
change — a strategy that failed in a ranging market might work in a
trending one. The graveyard periodically checks if conditions have
shifted to warrant giving a strategy another chance.

Resurrection criteria:
1. The strategy's target regime is now active
2. It's been at least N days since it was disabled
3. The strategy hasn't been resurrected more than M times
4. A backtest on recent data shows promise (optional)
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from data.models import StrategyHypothesisRecord
from engines.models import AssetClass, MarketRegime
from memory.market_regime import MarketRegimeTracker
from memory.strategy_journal import StrategyJournal

logger = logging.getLogger(__name__)

# Minimum days before a strategy can be resurrected
MIN_COOLDOWN_DAYS = 14

# Maximum number of times a strategy can be resurrected before permanent graveyard
MAX_RESURRECTIONS = 3


class GraveyardManager:
    """
    Evaluates disabled strategies for possible resurrection.

    Checks the graveyard during each slow loop cycle and recommends
    strategies that might work in the current market conditions.
    """

    def __init__(self, db_session: AsyncSession):
        self.db = db_session
        self.journal = StrategyJournal(db_session)
        self.regime_tracker = MarketRegimeTracker(db_session)

    async def scan_for_resurrections(self) -> list[dict]:
        """
        Scan the graveyard for strategies worth resurrecting.

        Returns:
            List of dicts with strategy info and resurrection rationale.
        """
        candidates = await self._get_resurrection_candidates()
        recommendations: list[dict] = []

        for candidate in candidates:
            rationale = await self._evaluate_candidate(candidate)
            if rationale:
                recommendations.append({
                    "hypothesis_id": candidate.id,
                    "hypothesis": candidate.hypothesis_text,
                    "original_regime": candidate.market_regime,
                    "rationale": rationale,
                    "backtest_sharpe": candidate.backtest_sharpe,
                    "paper_pnl": candidate.paper_trade_pnl,
                })

        if recommendations:
            logger.info(
                "Found %d resurrection candidates", len(recommendations)
            )
        return recommendations

    async def resurrect(self, hypothesis_id: int) -> bool:
        """
        Move a strategy from disabled/graveyard back to paper_testing.

        Args:
            hypothesis_id: ID of the strategy hypothesis to resurrect.

        Returns:
            True if resurrected, False if not eligible.
        """
        stmt = select(StrategyHypothesisRecord).where(
            StrategyHypothesisRecord.id == hypothesis_id
        )
        result = await self.db.execute(stmt)
        record = result.scalar_one_or_none()

        if not record:
            logger.warning("Hypothesis #%d not found", hypothesis_id)
            return False

        if record.status not in ("disabled", "graveyard"):
            logger.warning(
                "Hypothesis #%d is %s, not in graveyard",
                hypothesis_id, record.status,
            )
            return False

        # Check resurrection count
        resurrection_count = await self._count_resurrections(hypothesis_id)
        if resurrection_count >= MAX_RESURRECTIONS:
            logger.info(
                "Hypothesis #%d has been resurrected %d times — permanent graveyard",
                hypothesis_id, resurrection_count,
            )
            return False

        # Move back to paper testing
        await self.journal.start_paper_trading(hypothesis_id)
        await self.db.flush()

        logger.info(
            "Resurrected hypothesis #%d (resurrection #%d)",
            hypothesis_id, resurrection_count + 1,
        )
        return True

    async def enforce_graveyard_rules(self) -> list[int]:
        """
        Check active strategies that should be sent to graveyard.

        Criteria for graveyarding:
        - Paper trading for > 30 days with negative P&L
        - Backtest Sharpe < 0.5

        Returns:
            List of hypothesis IDs that were graveyarded.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        stmt = (
            select(StrategyHypothesisRecord)
            .where(
                StrategyHypothesisRecord.status == "paper_testing",
                StrategyHypothesisRecord.created_at <= cutoff,
            )
        )
        result = await self.db.execute(stmt)
        candidates = list(result.scalars().all())

        graveyarded: list[int] = []
        for c in candidates:
            should_graveyard = False
            reason = ""

            if c.paper_trade_pnl is not None and c.paper_trade_pnl < 0:
                should_graveyard = True
                reason = f"Negative paper P&L (${c.paper_trade_pnl:.2f}) after 30+ days"

            if c.backtest_sharpe is not None and c.backtest_sharpe < 0.5:
                should_graveyard = True
                reason = f"Low backtest Sharpe ({c.backtest_sharpe:.2f})"

            if should_graveyard:
                await self.journal.send_to_graveyard(c.id)
                graveyarded.append(c.id)
                logger.info(
                    "Graveyarded hypothesis #%d: %s", c.id, reason
                )

        return graveyarded

    async def _get_resurrection_candidates(
        self,
    ) -> list[StrategyHypothesisRecord]:
        """Get disabled strategies that meet cooldown requirements."""
        cooldown_cutoff = datetime.now(timezone.utc) - timedelta(
            days=MIN_COOLDOWN_DAYS
        )
        stmt = (
            select(StrategyHypothesisRecord)
            .where(
                StrategyHypothesisRecord.status.in_(["disabled", "graveyard"]),
                StrategyHypothesisRecord.updated_at <= cooldown_cutoff,
            )
            .order_by(StrategyHypothesisRecord.updated_at.asc())
            .limit(20)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def _evaluate_candidate(
        self, candidate: StrategyHypothesisRecord
    ) -> Optional[str]:
        """
        Evaluate whether a graveyard candidate should be resurrected.

        Returns rationale string if yes, None if no.
        """
        # Check resurrection count
        count = await self._count_resurrections(candidate.id)
        if count >= MAX_RESURRECTIONS:
            return None

        # Check if target regime now matches
        if candidate.market_regime:
            for asset_class in AssetClass:
                current_regime = await self.regime_tracker.get_current_regime(
                    asset_class
                )
                if current_regime.value == candidate.market_regime:
                    return (
                        f"Market regime for {asset_class.value} is now "
                        f"'{candidate.market_regime}', matching this strategy's "
                        f"target regime. Last tested with "
                        f"Sharpe={candidate.backtest_sharpe or 'N/A'}."
                    )

        # Check if it had decent backtest results despite failing in paper
        if (
            candidate.backtest_sharpe is not None
            and candidate.backtest_sharpe > 1.0
            and candidate.paper_trade_pnl is not None
            and candidate.paper_trade_pnl < 0
        ):
            return (
                f"Strategy had strong backtest (Sharpe={candidate.backtest_sharpe:.2f}) "
                f"but failed in paper trading (P&L=${candidate.paper_trade_pnl:.2f}). "
                f"Market conditions may have changed since then."
            )

        return None

    async def _count_resurrections(self, hypothesis_id: int) -> int:
        """
        Count how many times a strategy has been resurrected.

        We track this by counting transitions to paper_testing status
        after the initial one. Since we don't have a separate audit log,
        we use a convention: each resurrection adds a [RESURRECTED] note
        as a separate hypothesis linked by text.
        """
        stmt = select(func.count()).where(
            StrategyHypothesisRecord.hypothesis_text.contains(
                f"[RESURRECTED #{hypothesis_id}]"
            )
        )
        result = await self.db.execute(stmt)
        return result.scalar_one()
