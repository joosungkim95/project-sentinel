"""
Integration tests for repository functions against real Postgres.

Requires: PostgreSQL running locally with sentinel database.
"""

import pytest
from datetime import date

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from data.models import Base
from data.repositories.trades import (
    insert_trade,
    insert_rejected_signal,
    get_recent_trades,
)
from data.repositories.risk_events import (
    insert_risk_event,
    get_recent_risk_events,
)
from data.repositories.portfolio import (
    insert_portfolio_snapshot,
    get_latest_snapshot,
)
from data.repositories.strategies import (
    insert_strategy_performance,
    get_strategy_performance,
)
from engines.models import (
    AssetClass,
    MarketRegime,
    PortfolioSnapshot,
    RiskCheckResult,
    RiskDecision,
    Side,
    Signal,
    SignalStrength,
    TradeResult,
)

TEST_DB_URL = (
    "postgresql+asyncpg://sentinel:sentinel_dev@localhost:5432/sentinel"
)


@pytest.fixture
async def session():
    """Create a test session with a fresh schema."""
    engine = create_async_engine(TEST_DB_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession)
    async with factory() as sess:
        yield sess
        await sess.rollback()
    await engine.dispose()


def _make_signal() -> Signal:
    return Signal(
        strategy_id="sma_crossover_spy",
        asset_class=AssetClass.EQUITIES,
        symbol="SPY",
        side=Side.BUY,
        quantity=10.0,
        target_price=450.0,
        confidence=0.75,
        strength=SignalStrength.MODERATE,
        rationale="Golden cross detected",
        market_regime=MarketRegime.TRENDING_UP,
    )


def _make_risk_result(signal: Signal) -> RiskCheckResult:
    return RiskCheckResult(
        decision=RiskDecision.APPROVED,
        original_signal=signal,
        approved_quantity=10.0,
        risk_utilization_pct=5.0,
        portfolio_value=10000.0,
    )


class TestTradeRepository:
    async def test_insert_and_query_trade(self, session: AsyncSession) -> None:
        signal = _make_signal()
        risk_result = _make_risk_result(signal)
        trade = TradeResult(
            trade_id="test-001",
            signal=signal,
            risk_check=risk_result,
            executed=True,
            fill_price=450.50,
            fill_quantity=10.0,
            platform="alpaca",
        )
        trade_id = await insert_trade(session, trade)
        await session.commit()
        assert trade_id is not None

        trades = await get_recent_trades(session, limit=10)
        assert len(trades) == 1
        assert trades[0].symbol == "SPY"

    async def test_insert_rejected_signal(self, session: AsyncSession) -> None:
        signal = _make_signal()
        risk_result = RiskCheckResult(
            decision=RiskDecision.REJECTED,
            original_signal=signal,
            rejection_reasons=["Hard floor breached"],
            risk_utilization_pct=0.0,
            portfolio_value=8500.0,
        )
        record_id = await insert_rejected_signal(session, risk_result)
        await session.commit()
        assert record_id is not None


class TestRiskEventRepository:
    async def test_insert_and_query(self, session: AsyncSession) -> None:
        event_id = await insert_risk_event(
            session,
            event_type="circuit_breaker",
            severity="critical",
            details={"reason": "daily loss 3.2%"},
            portfolio_value=9680.0,
            action_taken="halt_all_trading",
        )
        await session.commit()
        assert event_id is not None

        events = await get_recent_risk_events(session)
        assert len(events) == 1
        assert events[0].event_type == "circuit_breaker"


class TestPortfolioRepository:
    async def test_insert_and_get_latest(self, session: AsyncSession) -> None:
        snapshot = PortfolioSnapshot(
            total_value=10500.0,
            cash=5000.0,
            positions={},
            risk_utilization={"equities": 52.4},
            daily_pnl=150.0,
            weekly_pnl=500.0,
            total_pnl=500.0,
            drawdown_from_peak=0.0,
        )
        snap_id = await insert_portfolio_snapshot(session, snapshot)
        await session.commit()
        assert snap_id is not None

        latest = await get_latest_snapshot(session)
        assert latest is not None
        assert latest.total_value == 10500.0


class TestStrategyPerformanceRepository:
    async def test_insert_and_query(self, session: AsyncSession) -> None:
        perf_id = await insert_strategy_performance(
            session,
            strategy_id="sma_crossover_spy",
            perf_date=date.today(),
            trades_count=5,
            win_rate=0.6,
            total_pnl=120.0,
            sharpe_ratio=1.1,
            max_drawdown=2.5,
            risk_budget_used=15.0,
            parameters={"short_window": 10, "long_window": 50},
        )
        await session.commit()
        assert perf_id is not None

        records = await get_strategy_performance(session, "sma_crossover_spy")
        assert len(records) == 1
