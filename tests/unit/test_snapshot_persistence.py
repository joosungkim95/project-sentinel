"""Snapshot persistence: scheduler periodically writes portfolio_snapshots.

Until this lands, /portfolio returns "no snapshots yet" because
data.repositories.portfolio.insert_portfolio_snapshot is defined but
never called from anywhere in the codebase.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engines.models import PortfolioSnapshot
from engines.scheduler import TradingScheduler


def _snap(total: float = 12_345.67) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        total_value=total,
        cash=total,
        positions={},
        risk_utilization={},
        daily_pnl=0.0,
        weekly_pnl=0.0,
        total_pnl=0.0,
        drawdown_from_peak=0.0,
    )


@pytest.mark.asyncio
async def test_persist_portfolio_snapshot_calls_repository():
    """The scheduler must persist the executor's snapshot via the repository."""
    executor = MagicMock()
    executor.get_portfolio_snapshot = AsyncMock(return_value=_snap())

    scheduler = TradingScheduler(
        risk_engine=MagicMock(),
        executor=executor,
        strategies=[],
    )

    fake_session = AsyncMock()
    fake_session.__aenter__.return_value = fake_session
    fake_session.__aexit__.return_value = None

    with patch(
        "engines.scheduler.async_session_factory", return_value=fake_session,
    ), patch(
        "engines.scheduler.insert_portfolio_snapshot", new=AsyncMock(return_value=42),
    ) as insert_mock:
        await scheduler._persist_portfolio_snapshot()

    executor.get_portfolio_snapshot.assert_awaited_once()
    insert_mock.assert_awaited_once()
    persisted = insert_mock.await_args.args[1]
    assert persisted.total_value == 12_345.67
