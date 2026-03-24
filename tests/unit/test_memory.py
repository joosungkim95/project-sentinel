"""
Unit tests for the memory/context management layer.

Uses in-memory SQLite so tests run without Postgres.
"""

import pytest
from datetime import datetime, timedelta, timezone

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from data.models import (
    Base,
    MarketRegimeRecord,
    PortfolioSnapshotRecord,
    RiskEventRecord,
    StrategyHypothesisRecord,
    StrategyPerformanceRecord,
    TradeRecord,
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
from memory.context_manager import ContextManager
from memory.market_regime import MarketRegimeTracker
from memory.strategy_journal import StrategyJournal
from memory.trade_journal import TradeJournal


# --- Fixtures ---

@pytest.fixture
async def db_session():
    """Create an in-memory SQLite session for testing."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_factory() as session:
        yield session

    await engine.dispose()


def make_signal(
    symbol: str = "SPY",
    side: Side = Side.BUY,
    quantity: float = 10.0,
    confidence: float = 0.75,
    strategy_id: str = "sma_crossover_spy",
    asset_class: AssetClass = AssetClass.EQUITIES,
) -> Signal:
    return Signal(
        strategy_id=strategy_id,
        asset_class=asset_class,
        symbol=symbol,
        side=side,
        quantity=quantity,
        confidence=confidence,
        strength=SignalStrength.MODERATE,
        rationale="Test signal",
        market_regime=MarketRegime.TRENDING_UP,
    )


def make_portfolio() -> PortfolioSnapshot:
    return PortfolioSnapshot(
        total_value=100000.0,
        cash=50000.0,
        positions={},
        risk_utilization={"equities": 25.0, "crypto": 10.0},
        daily_pnl=500.0,
        weekly_pnl=1500.0,
        total_pnl=5000.0,
        drawdown_from_peak=2.0,
    )


def make_trade_result(signal: Signal | None = None) -> TradeResult:
    sig = signal or make_signal()
    return TradeResult(
        trade_id="test-001",
        signal=sig,
        risk_check=RiskCheckResult(
            decision=RiskDecision.APPROVED,
            original_signal=sig,
            approved_quantity=sig.quantity,
            risk_utilization_pct=15.0,
            portfolio_value=100000.0,
        ),
        executed=True,
        fill_price=450.0,
        fill_quantity=10.0,
        platform="alpaca",
    )


# --- Seed helpers ---

async def seed_trades(session: AsyncSession, count: int = 5) -> list[int]:
    """Insert some test trades and return their IDs."""
    ids = []
    for i in range(count):
        record = TradeRecord(
            strategy_id="sma_crossover_spy",
            asset_class="equities",
            symbol="SPY",
            side="buy",
            quantity=10.0,
            price=450.0 + i,
            signal_confidence=0.75,
            risk_check_result="approved",
            risk_utilization_pct=15.0,
            market_regime="trending_up",
            pnl=float(i * 10 - 20),  # -20, -10, 0, 10, 20
        )
        session.add(record)
        await session.flush()
        ids.append(record.id)
    await session.commit()
    return ids


async def seed_strategy_performance(
    session: AsyncSession, strategy_id: str = "sma_crossover_spy"
) -> None:
    """Insert test strategy performance records."""
    from datetime import date

    for i in range(5):
        record = StrategyPerformanceRecord(
            strategy_id=strategy_id,
            date=date.today() - timedelta(days=i),
            trades_count=3,
            win_rate=0.6 + i * 0.05,
            total_pnl=100.0 * (i - 2),
            sharpe_ratio=1.0 + i * 0.2,
            max_drawdown=5.0 + i,
            risk_budget_used=20.0,
        )
        session.add(record)
    await session.commit()


async def seed_risk_events(session: AsyncSession) -> None:
    """Insert test risk events."""
    for severity in ["info", "warning", "critical"]:
        record = RiskEventRecord(
            event_type="daily_loss_limit",
            severity=severity,
            details={"loss_pct": 3.5},
            portfolio_value_at_event=97000.0,
            action_taken="signal_rejected",
        )
        session.add(record)
    # Also add a circuit breaker event
    cb = RiskEventRecord(
        event_type="circuit_breaker",
        severity="critical",
        details={"reason": "daily loss exceeded 5%"},
        portfolio_value_at_event=95000.0,
        action_taken="trading_halted",
    )
    session.add(cb)
    await session.commit()


async def seed_hypotheses(session: AsyncSession) -> None:
    """Insert test strategy hypotheses."""
    for i, status in enumerate(["proposed", "active", "disabled", "graveyard"]):
        record = StrategyHypothesisRecord(
            hypothesis_text=f"Hypothesis {i}: test strategy in {status} state",
            source="test",
            market_regime="trending_up",
            status=status,
        )
        session.add(record)
    await session.commit()


async def seed_regimes(session: AsyncSession) -> None:
    """Insert test market regime records."""
    now = datetime.now(timezone.utc)
    # Closed regime
    old = MarketRegimeRecord(
        asset_class="equities",
        regime_type="ranging",
        confidence=0.7,
        indicators={"vix": "18"},
        started_at=now - timedelta(days=10),
        ended_at=now - timedelta(days=3),
    )
    # Current regime (open)
    current = MarketRegimeRecord(
        asset_class="equities",
        regime_type="trending_up",
        confidence=0.85,
        indicators={"vix": "14", "adx": "28"},
        started_at=now - timedelta(days=3),
        ended_at=None,
    )
    session.add_all([old, current])
    await session.commit()


# =====================================================================
# ContextManager tests
# =====================================================================

class TestContextManager:

    async def test_build_strategy_context_empty_db(self, db_session):
        """Strategy context works on an empty database."""
        cm = ContextManager(db_session)
        ctx = await cm.build_strategy_context(
            make_portfolio(), MarketRegime.TRENDING_UP
        )
        assert ctx.market_regime == MarketRegime.TRENDING_UP
        assert ctx.top_strategies == []
        assert ctx.bottom_strategies == []
        assert ctx.recent_hypotheses == []

    async def test_build_strategy_context_with_data(self, db_session):
        """Strategy context returns real strategy data."""
        await seed_strategy_performance(db_session)
        await seed_hypotheses(db_session)

        cm = ContextManager(db_session)
        ctx = await cm.build_strategy_context(
            make_portfolio(), MarketRegime.TRENDING_UP
        )
        assert len(ctx.top_strategies) >= 1
        assert ctx.top_strategies[0].strategy_id == "sma_crossover_spy"
        assert len(ctx.recent_hypotheses) >= 1

    async def test_build_trade_context(self, db_session):
        """Trade context assembles signal + performance + similar trades."""
        await seed_trades(db_session)
        await seed_strategy_performance(db_session)

        cm = ContextManager(db_session)
        signal = make_signal()
        ctx = await cm.build_trade_context(signal, make_portfolio())

        assert ctx.signal.symbol == "SPY"
        assert ctx.strategy_recent_performance.strategy_id == "sma_crossover_spy"
        assert len(ctx.similar_past_trades) > 0

    async def test_build_risk_context(self, db_session):
        """Risk context includes risk events and circuit breaker history."""
        await seed_risk_events(db_session)

        cm = ContextManager(db_session)
        ctx = await cm.build_risk_context(make_portfolio())

        assert len(ctx.recent_risk_events) >= 3
        assert len(ctx.circuit_breaker_history) >= 1
        assert ctx.circuit_breaker_history[0]["severity"] == "critical"

    async def test_build_learning_context(self, db_session):
        """Learning context includes all strategy data."""
        await seed_strategy_performance(db_session)
        await seed_trades(db_session)
        await seed_hypotheses(db_session)

        cm = ContextManager(db_session)
        ctx = await cm.build_learning_context(make_portfolio(), period_days=30)

        assert len(ctx.strategy_performances) >= 1
        assert ctx.period_days == 30

    async def test_pnl_over_days(self, db_session):
        """PnL calculation sums realized trades correctly."""
        await seed_trades(db_session)  # P&L: -20, -10, 0, 10, 20 = 0

        cm = ContextManager(db_session)
        pnl = await cm._get_pnl_over_days(30)
        assert pnl == 0.0

    async def test_market_conditions_with_regime(self, db_session):
        """Market conditions returns current regime data."""
        await seed_regimes(db_session)

        cm = ContextManager(db_session)
        conditions = await cm._get_market_conditions(AssetClass.EQUITIES)
        assert conditions["regime"] == "trending_up"
        assert float(conditions["confidence"]) == 0.85

    async def test_to_prompt_text_respects_budget(self, db_session):
        """Prompt text is truncated when it exceeds token budget."""
        cm = ContextManager(db_session)
        ctx = await cm.build_strategy_context(
            make_portfolio(), MarketRegime.TRENDING_UP
        )
        text = cm.to_prompt_text(ctx, max_tokens=50)
        # 50 tokens * 4 chars = 200 chars max
        assert len(text) <= 200

    async def test_pearson_correlation(self, db_session):
        """Pearson correlation computes correctly."""
        cm = ContextManager(db_session)
        # Perfect positive correlation
        assert cm._pearson([1, 2, 3, 4], [2, 4, 6, 8]) == 1.0
        # Perfect negative correlation
        assert cm._pearson([1, 2, 3, 4], [8, 6, 4, 2]) == -1.0
        # Too few points
        assert cm._pearson([1, 2], [3, 4]) == 0.0


# =====================================================================
# TradeJournal tests
# =====================================================================

class TestTradeJournal:

    async def test_record_and_close_trade(self, db_session):
        """Record a trade, then close it and verify P&L."""
        journal = TradeJournal(db_session)
        trade = make_trade_result()
        trade_id = await journal.record_trade(trade)
        await db_session.commit()

        assert trade_id > 0

        # Close at a profit
        await journal.close_trade(trade_id, exit_price=460.0)
        await db_session.commit()

        # Verify P&L: (460 - 450) * 10 = 100
        from data.models import TradeRecord
        from sqlalchemy import select

        stmt = select(TradeRecord).where(TradeRecord.id == trade_id)
        result = await db_session.execute(stmt)
        record = result.scalar_one()
        assert record.pnl == 100.0
        assert record.exit_price == 460.0

    async def test_record_rejection(self, db_session):
        """Record a rejected signal."""
        journal = TradeJournal(db_session)
        signal = make_signal()
        risk_result = RiskCheckResult(
            decision=RiskDecision.REJECTED,
            original_signal=signal,
            rejection_reasons=["daily loss limit"],
            risk_utilization_pct=90.0,
            portfolio_value=100000.0,
        )
        trade_id = await journal.record_rejection(risk_result)
        await db_session.commit()
        assert trade_id > 0

    async def test_strategy_summary(self, db_session):
        """Strategy summary computes correct stats."""
        await seed_trades(db_session)
        journal = TradeJournal(db_session)
        summary = await journal.get_strategy_summary("sma_crossover_spy")

        assert summary["total_trades"] == 5
        assert summary["total_pnl"] == 0.0  # -20 + -10 + 0 + 10 + 20
        assert summary["wins"] == 2  # pnl > 0: 10, 20
        assert summary["losses"] == 3  # pnl <= 0: -20, -10, 0

    async def test_rejection_rate(self, db_session):
        """Rejection rate calculation."""
        # Add 2 approved, 1 rejected
        for result in ["approved", "approved", "rejected"]:
            record = TradeRecord(
                strategy_id="test_strat",
                asset_class="equities",
                symbol="SPY",
                side="buy",
                quantity=10.0,
                price=450.0,
                signal_confidence=0.75,
                risk_check_result=result,
                risk_utilization_pct=15.0,
                market_regime="trending_up",
            )
            db_session.add(record)
        await db_session.commit()

        journal = TradeJournal(db_session)
        rate = await journal.get_rejection_rate("test_strat")
        assert abs(rate - 1 / 3) < 0.01


# =====================================================================
# StrategyJournal tests
# =====================================================================

class TestStrategyJournal:

    async def test_full_lifecycle(self, db_session):
        """Test hypothesis lifecycle: propose → backtest → paper → activate."""
        journal = StrategyJournal(db_session)

        # Propose
        hid = await journal.propose_hypothesis(
            "Mean reversion on VIX spikes",
            source="claude_weekly_review",
            market_regime="high_volatility",
        )
        await db_session.commit()
        assert hid > 0

        # Backtest
        await journal.record_backtest_result(hid, sharpe_ratio=1.5, max_drawdown=8.0)
        await db_session.commit()

        # Paper trade
        await journal.start_paper_trading(hid)
        await journal.record_paper_result(hid, days=14, pnl=350.0)
        await db_session.commit()

        # Activate
        await journal.activate(hid)
        await db_session.commit()

        active = await journal.get_active_hypotheses()
        assert any(h.id == hid for h in active)

    async def test_graveyard(self, db_session):
        """Strategies can be sent to graveyard and retrieved."""
        journal = StrategyJournal(db_session)
        hid = await journal.propose_hypothesis("Bad idea", source="test")
        await db_session.commit()

        await journal.send_to_graveyard(hid)
        await db_session.commit()

        graveyard = await journal.get_graveyard()
        assert len(graveyard) == 1
        assert graveyard[0].status == "graveyard"


# =====================================================================
# MarketRegimeTracker tests
# =====================================================================

class TestMarketRegimeTracker:

    async def test_unknown_when_empty(self, db_session):
        """Returns UNKNOWN when no regime data exists."""
        tracker = MarketRegimeTracker(db_session)
        regime = await tracker.get_current_regime(AssetClass.EQUITIES)
        assert regime == MarketRegime.UNKNOWN

    async def test_update_regime_creates_record(self, db_session):
        """First update creates a new regime record."""
        tracker = MarketRegimeTracker(db_session)
        changed = await tracker.update_regime(
            AssetClass.EQUITIES,
            MarketRegime.TRENDING_UP,
            confidence=0.85,
            indicators={"vix": 14, "adx": 28},
        )
        await db_session.commit()
        assert changed is True

        regime = await tracker.get_current_regime(AssetClass.EQUITIES)
        assert regime == MarketRegime.TRENDING_UP

    async def test_same_regime_no_change(self, db_session):
        """Updating with the same regime returns False."""
        tracker = MarketRegimeTracker(db_session)
        await tracker.update_regime(
            AssetClass.EQUITIES, MarketRegime.RANGING, confidence=0.7
        )
        await db_session.commit()

        changed = await tracker.update_regime(
            AssetClass.EQUITIES, MarketRegime.RANGING, confidence=0.75
        )
        await db_session.commit()
        assert changed is False

    async def test_regime_change_closes_old(self, db_session):
        """Changing regime closes the old one and starts a new one."""
        tracker = MarketRegimeTracker(db_session)
        await tracker.update_regime(
            AssetClass.CRYPTO, MarketRegime.HIGH_VOLATILITY, confidence=0.9
        )
        await db_session.commit()

        changed = await tracker.update_regime(
            AssetClass.CRYPTO, MarketRegime.TRENDING_DOWN, confidence=0.8
        )
        await db_session.commit()
        assert changed is True

        regime = await tracker.get_current_regime(AssetClass.CRYPTO)
        assert regime == MarketRegime.TRENDING_DOWN

        # Check history shows both
        history = await tracker.get_regime_history(AssetClass.CRYPTO, days=1)
        assert len(history) == 2

    async def test_regime_duration(self, db_session):
        """Duration reports how long current regime has been active."""
        tracker = MarketRegimeTracker(db_session)
        await tracker.update_regime(
            AssetClass.EQUITIES, MarketRegime.TRENDING_UP, confidence=0.85
        )
        await db_session.commit()

        duration = await tracker.get_regime_duration(AssetClass.EQUITIES)
        assert duration is not None
        # Should be very recent (< 1 second)
        assert duration.total_seconds() < 5
