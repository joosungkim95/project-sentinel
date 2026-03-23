"""
Trade persistence — insert and query trade records.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from data.models import TradeRecord
from engines.models import RiskCheckResult, TradeResult


async def insert_trade(session: AsyncSession, trade: TradeResult) -> int:
    """
    Persist a trade result to the database.

    Args:
        session: Active async database session.
        trade: TradeResult from the execution engine.

    Returns:
        The database ID of the inserted record.
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
    )
    session.add(record)
    await session.flush()
    return record.id


async def insert_rejected_signal(
    session: AsyncSession, risk_result: RiskCheckResult
) -> int:
    """
    Persist a rejected signal for learning purposes.

    Args:
        session: Active async database session.
        risk_result: RiskCheckResult with decision == REJECTED.

    Returns:
        The database ID of the inserted record.
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
    )
    session.add(record)
    await session.flush()
    return record.id


async def get_recent_trades(
    session: AsyncSession, limit: int = 50
) -> list[TradeRecord]:
    """
    Get most recent trades, newest first.

    Args:
        session: Active async database session.
        limit: Max number of trades to return.

    Returns:
        List of TradeRecord objects.
    """
    stmt = (
        select(TradeRecord)
        .order_by(TradeRecord.created_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())
