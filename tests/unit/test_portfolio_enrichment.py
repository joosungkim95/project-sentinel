"""
Unit tests for portfolio snapshot P&L/drawdown enrichment.

Verifies that _get_enriched_snapshot correctly computes daily_pnl,
weekly_pnl, total_pnl, and drawdown_from_peak from trade history
and historical snapshots.
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from data.models import Base, PortfolioSnapshotRecord, TradeRecord
from engines.models import (
    AssetClass,
    PortfolioSnapshot,
    PositionInfo,
    Side,
)
from engines.pipeline import TradingPipeline
from engines.risk.engine import RiskEngine
from engines.execution.base import Executor
from config.risk_config import RiskConfig


TEST_DB_URL = (
    "postgresql+asyncpg://sentinel:sentinel_dev@localhost:5432/sentinel"
)


@pytest.fixture
async def session():
    engine = create_async_engine(TEST_DB_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession)
    async with factory() as sess:
        yield sess
        await sess.rollback()
    await engine.dispose()


def _make_pipeline(session) -> TradingPipeline:
    """Create a TradingPipeline with a mock executor and real DB session."""
    risk_engine = RiskEngine(RiskConfig())
    executor = Executor()
    # Mock the executor's get_portfolio_snapshot
    executor.get_portfolio_snapshot = AsyncMock(return_value=PortfolioSnapshot(
        total_value=10000.0,
        cash=5000.0,
        positions={},
        risk_utilization={},
        daily_pnl=0.0,
        weekly_pnl=0.0,
        total_pnl=0.0,
        drawdown_from_peak=0.0,
    ))
    return TradingPipeline(
        risk_engine=risk_engine,
        executor=executor,
        strategies=[],
        db_session=session,
    )


def _insert_trade(
    session,
    pnl: float,
    exit_time: datetime,
) -> None:
    """Insert a closed trade with given P&L and exit time."""
    trade = TradeRecord(
        strategy_id="test_strategy",
        asset_class="equities",
        symbol="SPY",
        side="buy",
        quantity=10.0,
        price=450.0,
        signal_confidence=0.75,
        risk_check_result="approved",
        risk_utilization_pct=5.0,
        entry_time=exit_time - timedelta(hours=1),
        exit_time=exit_time,
        exit_price=450.0 + (pnl / 10.0),
        pnl=pnl,
        pnl_pct=(pnl / 4500.0) * 100,
        market_regime="trending_up",
    )
    session.add(trade)


def _insert_snapshot(
    session,
    total_value: float,
    created_at: datetime,
) -> None:
    """Insert a historical portfolio snapshot."""
    snap = PortfolioSnapshotRecord(
        total_value=total_value,
        cash=total_value * 0.5,
        positions={},
        risk_utilization={},
        daily_pnl=0.0,
        weekly_pnl=0.0,
        total_pnl=0.0,
        drawdown_from_peak=0.0,
        created_at=created_at,
    )
    session.add(snap)


class TestPortfolioEnrichment:
    @pytest.mark.asyncio
    async def test_no_trades_returns_zeros(self, session):
        """With no trades in DB, P&L values should be zero."""
        pipeline = _make_pipeline(session)
        snapshot = await pipeline._get_enriched_snapshot()

        assert snapshot.daily_pnl == 0.0
        assert snapshot.weekly_pnl == 0.0
        assert snapshot.total_pnl == 0.0
        assert snapshot.drawdown_from_peak == 0.0

    @pytest.mark.asyncio
    async def test_daily_pnl_from_todays_trades(self, session):
        """daily_pnl sums only trades closed today."""
        now = datetime.now(timezone.utc)
        yesterday = now - timedelta(days=1, hours=1)

        _insert_trade(session, pnl=100.0, exit_time=now - timedelta(hours=1))
        _insert_trade(session, pnl=-30.0, exit_time=now - timedelta(hours=2))
        _insert_trade(session, pnl=200.0, exit_time=yesterday)  # not today
        await session.flush()

        pipeline = _make_pipeline(session)
        snapshot = await pipeline._get_enriched_snapshot()

        assert snapshot.daily_pnl == pytest.approx(70.0)
        # Weekly should include all 3
        assert snapshot.weekly_pnl == pytest.approx(270.0)

    @pytest.mark.asyncio
    async def test_weekly_pnl_window(self, session):
        """weekly_pnl sums trades from last 7 days only."""
        now = datetime.now(timezone.utc)

        _insert_trade(session, pnl=50.0, exit_time=now - timedelta(days=3))
        _insert_trade(session, pnl=80.0, exit_time=now - timedelta(days=6))
        _insert_trade(session, pnl=999.0, exit_time=now - timedelta(days=10))
        await session.flush()

        pipeline = _make_pipeline(session)
        snapshot = await pipeline._get_enriched_snapshot()

        assert snapshot.weekly_pnl == pytest.approx(130.0)
        assert snapshot.total_pnl == pytest.approx(1129.0)

    @pytest.mark.asyncio
    async def test_drawdown_from_peak(self, session):
        """drawdown_from_peak calculates % drop from historical peak."""
        now = datetime.now(timezone.utc)

        # Historical peak was 12000, current value is 10000 → 16.67% drawdown
        _insert_snapshot(session, total_value=12000.0,
                         created_at=now - timedelta(days=5))
        _insert_snapshot(session, total_value=11000.0,
                         created_at=now - timedelta(days=3))
        await session.flush()

        pipeline = _make_pipeline(session)
        snapshot = await pipeline._get_enriched_snapshot()

        expected_dd = (12000.0 - 10000.0) / 12000.0 * 100.0
        assert snapshot.drawdown_from_peak == pytest.approx(
            round(expected_dd, 2)
        )

    @pytest.mark.asyncio
    async def test_no_drawdown_at_new_high(self, session):
        """drawdown should be 0 when current value exceeds all history."""
        now = datetime.now(timezone.utc)

        _insert_snapshot(session, total_value=9000.0,
                         created_at=now - timedelta(days=5))
        await session.flush()

        pipeline = _make_pipeline(session)
        snapshot = await pipeline._get_enriched_snapshot()

        # Current value 10000 > historical peak 9000
        assert snapshot.drawdown_from_peak == 0.0

    @pytest.mark.asyncio
    async def test_no_db_session_returns_raw_snapshot(self):
        """Without db_session, returns the executor's raw snapshot."""
        risk_engine = RiskEngine(RiskConfig())
        executor = Executor()
        executor.get_portfolio_snapshot = AsyncMock(
            return_value=PortfolioSnapshot(
                total_value=10000.0,
                cash=5000.0,
                positions={},
                risk_utilization={},
                daily_pnl=0.0,
                weekly_pnl=0.0,
                total_pnl=0.0,
                drawdown_from_peak=0.0,
            )
        )
        pipeline = TradingPipeline(
            risk_engine=risk_engine,
            executor=executor,
            strategies=[],
            db_session=None,
        )
        snapshot = await pipeline._get_enriched_snapshot()

        assert snapshot.daily_pnl == 0.0
        assert snapshot.drawdown_from_peak == 0.0

    @pytest.mark.asyncio
    async def test_negative_pnl_feeds_safety_rules(self, session):
        """Verify DailyLoss and WeeklyDrawdown rules trigger with real data."""
        from engines.risk.rules import DailyLossCircuitBreaker, WeeklyDrawdownRule
        from engines.models import Signal, SignalStrength

        now = datetime.now(timezone.utc)

        # Insert a large daily loss: -400 on a 10000 portfolio = 4%
        _insert_trade(session, pnl=-400.0, exit_time=now - timedelta(hours=1))
        await session.flush()

        pipeline = _make_pipeline(session)
        snapshot = await pipeline._get_enriched_snapshot()

        assert snapshot.daily_pnl == pytest.approx(-400.0)

        # DailyLossCircuitBreaker with 3% threshold should trigger
        signal = Signal(
            strategy_id="test",
            asset_class=AssetClass.EQUITIES,
            symbol="SPY",
            side=Side.BUY,
            quantity=1.0,
            target_price=450.0,
            confidence=0.75,
            strength=SignalStrength.MODERATE,
            rationale="test",
        )
        daily_rule = DailyLossCircuitBreaker(max_daily_loss_pct=3.0)
        result = daily_rule.check(signal, snapshot, 1.0)
        assert result.rejected
        assert "circuit breaker" in result.reason.lower()

        # WeeklyDrawdownRule with 5% threshold should not trigger (4% < 5%)
        weekly_rule = WeeklyDrawdownRule(max_weekly_drawdown_pct=5.0)
        result = weekly_rule.check(signal, snapshot, 1.0)
        assert not result.rejected
