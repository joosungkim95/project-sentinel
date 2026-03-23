"""
Risk event persistence — insert and query risk events.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from data.models import RiskEventRecord


async def insert_risk_event(
    session: AsyncSession,
    event_type: str,
    severity: str,
    details: dict,
    portfolio_value: float,
    action_taken: str,
) -> int:
    """
    Persist a risk event.

    Args:
        session: Active async database session.
        event_type: Type of risk event.
        severity: "info", "warning", or "critical".
        details: JSON-serializable event details.
        portfolio_value: Portfolio value at time of event.
        action_taken: What the system did.

    Returns:
        The database ID of the inserted record.
    """
    record = RiskEventRecord(
        event_type=event_type,
        severity=severity,
        details=details,
        portfolio_value_at_event=portfolio_value,
        action_taken=action_taken,
    )
    session.add(record)
    await session.flush()
    return record.id


async def get_recent_risk_events(
    session: AsyncSession, limit: int = 20
) -> list[RiskEventRecord]:
    """Get most recent risk events."""
    stmt = (
        select(RiskEventRecord)
        .order_by(RiskEventRecord.created_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())
