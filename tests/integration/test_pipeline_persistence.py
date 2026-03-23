"""
Integration test: verify pipeline._log_outcome persists to database.
"""

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from data.models import Base
from data.repositories.trades import get_recent_trades
from engines.models import (
    AssetClass,
    MarketRegime,
    RiskCheckResult,
    RiskDecision,
    Side,
    Signal,
    SignalStrength,
    TradeResult,
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


class TestPipelinePersistence:
    async def test_log_outcome_persists_executed_trade(self, session):
        pipeline = TradingPipeline(
            risk_engine=RiskEngine(RiskConfig()),
            executor=Executor(),
            strategies=[],
            db_session=session,
        )
        signal = _make_signal()
        risk_result = RiskCheckResult(
            decision=RiskDecision.APPROVED,
            original_signal=signal,
            approved_quantity=10.0,
            risk_utilization_pct=5.0,
            portfolio_value=10000.0,
        )
        trade_result = TradeResult(
            trade_id="test-001",
            signal=signal,
            risk_check=risk_result,
            executed=True,
            fill_price=450.50,
            fill_quantity=10.0,
            platform="alpaca",
        )

        await pipeline._log_outcome(signal, risk_result, trade_result)

        trades = await get_recent_trades(session, limit=10)
        assert len(trades) == 1
        assert trades[0].symbol == "SPY"
        assert trades[0].risk_check_result == "approved"

    async def test_log_outcome_persists_rejected_signal(self, session):
        pipeline = TradingPipeline(
            risk_engine=RiskEngine(RiskConfig()),
            executor=Executor(),
            strategies=[],
            db_session=session,
        )
        signal = _make_signal()
        risk_result = RiskCheckResult(
            decision=RiskDecision.REJECTED,
            original_signal=signal,
            rejection_reasons=["Hard floor breached"],
            risk_utilization_pct=0.0,
            portfolio_value=8500.0,
        )

        await pipeline._log_outcome(signal, risk_result, None)

        trades = await get_recent_trades(session, limit=10)
        assert len(trades) == 1
        assert trades[0].risk_check_result == "rejected"

    async def test_log_outcome_works_without_db_session(self):
        pipeline = TradingPipeline(
            risk_engine=RiskEngine(RiskConfig()),
            executor=Executor(),
            strategies=[],
        )
        signal = _make_signal()
        risk_result = RiskCheckResult(
            decision=RiskDecision.APPROVED,
            original_signal=signal,
            approved_quantity=1.0,
            risk_utilization_pct=1.0,
            portfolio_value=10000.0,
        )

        await pipeline._log_outcome(signal, risk_result, None)
        assert len(pipeline.get_trade_log()) == 1
