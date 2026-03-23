"""
Portfolio snapshot persistence — insert and query portfolio state.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from data.models import PortfolioSnapshotRecord
from engines.models import PortfolioSnapshot


async def insert_portfolio_snapshot(
    session: AsyncSession, snapshot: PortfolioSnapshot
) -> int:
    """
    Persist a portfolio snapshot.

    Args:
        session: Active async database session.
        snapshot: PortfolioSnapshot from the execution engine.

    Returns:
        The database ID of the inserted record.
    """
    positions_dict = {
        symbol: pos.model_dump() for symbol, pos in snapshot.positions.items()
    }
    record = PortfolioSnapshotRecord(
        total_value=snapshot.total_value,
        cash=snapshot.cash,
        positions=positions_dict,
        risk_utilization=snapshot.risk_utilization,
    )
    session.add(record)
    await session.flush()
    return record.id


async def get_latest_snapshot(
    session: AsyncSession,
) -> PortfolioSnapshotRecord | None:
    """Get the most recent portfolio snapshot."""
    stmt = (
        select(PortfolioSnapshotRecord)
        .order_by(PortfolioSnapshotRecord.created_at.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()
