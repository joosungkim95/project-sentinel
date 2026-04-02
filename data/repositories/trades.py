"""
Trade persistence — insert and query trade records.
"""

from typing import Optional

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
        platform=trade.platform,
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
    session: AsyncSession,
    limit: int = 50,
    platform: Optional[str] = None,
    asset_class: Optional[str] = None,
    strategy_id: Optional[str] = None,
    side: Optional[str] = None,
) -> list[TradeRecord]:
    """
    Get most recent trades, newest first, with optional filters.

    Args:
        session: Active async database session.
        limit: Max number of trades to return.
        platform: Filter by platform (e.g., "coinbase", "paper_crypto").
        asset_class: Filter by asset class (e.g., "crypto", "equities").
        strategy_id: Filter by strategy ID.
        side: Filter by side ("buy" or "sell").

    Returns:
        List of TradeRecord objects.
    """
    stmt = select(TradeRecord)

    if platform == "paper":
        stmt = stmt.where(TradeRecord.platform.like("paper_%"))
    elif platform == "live":
        stmt = stmt.where(~TradeRecord.platform.like("paper_%"))
    elif platform:
        stmt = stmt.where(TradeRecord.platform == platform)

    if asset_class:
        stmt = stmt.where(TradeRecord.asset_class == asset_class)
    if strategy_id:
        stmt = stmt.where(TradeRecord.strategy_id == strategy_id)
    if side:
        stmt = stmt.where(TradeRecord.side == side)

    stmt = stmt.order_by(TradeRecord.created_at.desc()).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())
