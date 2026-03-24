"""
Trade Journal — Records every trade with full context and outcomes.

This is the Learning Engine's primary data source. Every trade (executed or
rejected) is recorded with the full decision context so the system can
learn from its own history.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from data.models import TradeRecord
from engines.models import RiskCheckResult, Signal, TradeResult

logger = logging.getLogger(__name__)


class TradeJournal:
    """
    Persistent trade journal that records and queries trade history.

    Records both executed trades and rejected signals so the Learning
    Engine can evaluate both what happened and what was avoided.
    """

    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    async def record_trade(
        self,
        trade: TradeResult,
        context_snapshot_id: Optional[int] = None,
    ) -> int:
        """
        Record an executed trade with full context.

        Args:
            trade: The executed trade result.
            context_snapshot_id: Optional link to the portfolio snapshot at decision time.

        Returns:
            Database ID of the trade record.
        """
        record = TradeRecord(
            strategy_id=trade.signal.strategy_id,
            asset_class=trade.signal.asset_class.value,
            symbol=trade.signal.symbol,
            side=trade.signal.side.value,
            quantity=trade.fill_quantity or trade.signal.quantity,
            price=trade.fill_price or trade.signal.target_price or 0.0,
            signal_confidence=trade.signal.confidence,
            risk_check_result=trade.risk_check.decision.value,
            risk_utilization_pct=trade.risk_check.risk_utilization_pct,
            market_regime=trade.signal.market_regime.value,
            context_snapshot_id=context_snapshot_id,
        )
        self.db.add(record)
        await self.db.flush()
        logger.info(
            "Recorded trade #%d: %s %s %s @ %.4f",
            record.id, trade.signal.side.value, trade.signal.symbol,
            trade.signal.strategy_id, record.price,
        )
        return record.id

    async def record_rejection(
        self,
        risk_result: RiskCheckResult,
        context_snapshot_id: Optional[int] = None,
    ) -> int:
        """
        Record a rejected signal for learning purposes.

        Args:
            risk_result: The risk check result with rejection reasons.
            context_snapshot_id: Optional link to portfolio snapshot.

        Returns:
            Database ID of the trade record.
        """
        signal = risk_result.original_signal
        record = TradeRecord(
            strategy_id=signal.strategy_id,
            asset_class=signal.asset_class.value,
            symbol=signal.symbol,
            side=signal.side.value,
            quantity=signal.quantity,
            price=signal.target_price or 0.0,
            signal_confidence=signal.confidence,
            risk_check_result=risk_result.decision.value,
            risk_utilization_pct=risk_result.risk_utilization_pct,
            market_regime=signal.market_regime.value,
            context_snapshot_id=context_snapshot_id,
        )
        self.db.add(record)
        await self.db.flush()
        logger.info(
            "Recorded rejection #%d: %s %s — %s",
            record.id, signal.symbol, signal.strategy_id,
            ", ".join(risk_result.rejection_reasons),
        )
        return record.id

    async def close_trade(
        self,
        trade_id: int,
        exit_price: float,
        exit_time: Optional[datetime] = None,
    ) -> None:
        """
        Record the exit of a trade (fills in pnl).

        Args:
            trade_id: ID of the trade record to close.
            exit_price: Price at which the position was closed.
            exit_time: When the exit happened. Defaults to now.
        """
        stmt = select(TradeRecord).where(TradeRecord.id == trade_id)
        result = await self.db.execute(stmt)
        record = result.scalar_one_or_none()
        if not record:
            logger.warning("Trade #%d not found for close", trade_id)
            return

        record.exit_price = exit_price
        record.exit_time = exit_time or datetime.now(timezone.utc)

        if record.side == "buy":
            record.pnl = (exit_price - record.price) * record.quantity
        else:
            record.pnl = (record.price - exit_price) * record.quantity

        if record.price > 0:
            record.pnl_pct = record.pnl / (record.price * record.quantity) * 100

        await self.db.flush()
        logger.info(
            "Closed trade #%d: P&L=%.2f (%.2f%%)",
            trade_id, record.pnl or 0, record.pnl_pct or 0,
        )

    async def get_strategy_summary(
        self, strategy_id: str, days: int = 30
    ) -> dict:
        """
        Get a summary of a strategy's trade history.

        Args:
            strategy_id: The strategy to summarize.
            days: Lookback period.

        Returns:
            Dict with trade count, win rate, total P&L, avg P&L.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        stmt = (
            select(TradeRecord)
            .where(
                TradeRecord.strategy_id == strategy_id,
                TradeRecord.created_at >= cutoff,
                TradeRecord.risk_check_result == "approved",
            )
        )
        result = await self.db.execute(stmt)
        trades = list(result.scalars().all())

        closed = [t for t in trades if t.pnl is not None]
        wins = [t for t in closed if (t.pnl or 0) > 0]

        return {
            "strategy_id": strategy_id,
            "period_days": days,
            "total_trades": len(trades),
            "closed_trades": len(closed),
            "open_trades": len(trades) - len(closed),
            "wins": len(wins),
            "losses": len(closed) - len(wins),
            "win_rate": len(wins) / len(closed) if closed else 0.0,
            "total_pnl": sum(t.pnl or 0 for t in closed),
            "avg_pnl": (
                sum(t.pnl or 0 for t in closed) / len(closed)
                if closed else 0.0
            ),
            "best_trade": max((t.pnl or 0 for t in closed), default=0),
            "worst_trade": min((t.pnl or 0 for t in closed), default=0),
        }

    async def get_rejection_rate(
        self, strategy_id: str, days: int = 7
    ) -> float:
        """
        Get the rejection rate for a strategy over a period.

        Returns:
            Rejection rate as a float between 0.0 and 1.0.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        stmt = (
            select(
                func.count().label("total"),
                func.count()
                .filter(TradeRecord.risk_check_result == "rejected")
                .label("rejected"),
            )
            .where(
                TradeRecord.strategy_id == strategy_id,
                TradeRecord.created_at >= cutoff,
            )
        )
        result = await self.db.execute(stmt)
        row = result.one()
        if row.total == 0:
            return 0.0
        return row.rejected / row.total
