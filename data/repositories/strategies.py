"""
Strategy performance persistence — insert and query strategy metrics.
"""

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from data.models import StrategyPerformanceRecord


async def insert_strategy_performance(
    session: AsyncSession,
    strategy_id: str,
    perf_date: date,
    trades_count: int,
    win_rate: float,
    total_pnl: float,
    sharpe_ratio: float | None,
    max_drawdown: float,
    risk_budget_used: float,
    parameters: dict | None = None,
) -> int:
    """
    Persist daily strategy performance metrics.

    Returns:
        The database ID of the inserted record.
    """
    record = StrategyPerformanceRecord(
        strategy_id=strategy_id,
        date=perf_date,
        trades_count=trades_count,
        win_rate=win_rate,
        total_pnl=total_pnl,
        sharpe_ratio=sharpe_ratio,
        max_drawdown=max_drawdown,
        risk_budget_used=risk_budget_used,
        parameters=parameters,
    )
    session.add(record)
    await session.flush()
    return record.id


async def get_strategy_performance(
    session: AsyncSession,
    strategy_id: str,
    limit: int = 30,
) -> list[StrategyPerformanceRecord]:
    """Get recent performance records for a strategy."""
    stmt = (
        select(StrategyPerformanceRecord)
        .where(StrategyPerformanceRecord.strategy_id == strategy_id)
        .order_by(StrategyPerformanceRecord.date.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())
