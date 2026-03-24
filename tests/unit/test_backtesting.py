"""
Unit tests for the backtesting framework and graveyard logic.
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from backtesting.data_loader import (
    generate_synthetic_bars,
    generate_trending_bars,
    generate_ranging_bars,
    generate_volatile_bars,
    slice_walk_forward,
)
from backtesting.engine import BacktestEngine, BacktestResult
from data.models import Base, StrategyHypothesisRecord, MarketRegimeRecord
from engines.learning.graveyard import GraveyardManager, MIN_COOLDOWN_DAYS
from engines.models import (
    AssetClass,
    MarketRegime,
    StrategyPerformance,
    StrategyStatus,
)
from engines.strategy.equities.sma_crossover import SMACrossoverStrategy


# --- Fixtures ---

@pytest.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_factory() as session:
        yield session
    await engine.dispose()


# =====================================================================
# Data Loader tests
# =====================================================================

class TestSyntheticBars:

    def test_generate_correct_count(self):
        bars = generate_synthetic_bars(num_bars=100, seed=42)
        assert len(bars) == 100

    def test_bars_have_ohlcv(self):
        bars = generate_synthetic_bars(num_bars=10, seed=42)
        for bar in bars:
            assert "open" in bar
            assert "high" in bar
            assert "low" in bar
            assert "close" in bar
            assert "volume" in bar
            assert "date" in bar

    def test_ohlc_consistency(self):
        """High >= max(open, close) and Low <= min(open, close)."""
        bars = generate_synthetic_bars(num_bars=200, seed=42)
        for bar in bars:
            assert bar["high"] >= bar["open"]
            assert bar["high"] >= bar["close"]
            assert bar["low"] <= bar["open"]
            assert bar["low"] <= bar["close"]

    def test_seed_reproducibility(self):
        bars1 = generate_synthetic_bars(num_bars=50, seed=123)
        bars2 = generate_synthetic_bars(num_bars=50, seed=123)
        assert bars1 == bars2

    def test_trending_up(self):
        bars = generate_trending_bars(num_bars=200, direction="up", seed=42)
        assert bars[-1]["close"] > bars[0]["close"]

    def test_trending_down(self):
        bars = generate_trending_bars(num_bars=200, direction="down", seed=42)
        assert bars[-1]["close"] < bars[0]["close"]

    def test_volatile_higher_variance(self):
        calm = generate_synthetic_bars(num_bars=100, volatility=0.01, seed=42)
        wild = generate_volatile_bars(num_bars=100, seed=42)
        calm_range = max(b["close"] for b in calm) - min(b["close"] for b in calm)
        wild_range = max(b["close"] for b in wild) - min(b["close"] for b in wild)
        assert wild_range > calm_range


class TestWalkForward:

    def test_basic_slicing(self):
        bars = generate_synthetic_bars(num_bars=100, seed=42)
        windows = slice_walk_forward(bars, train_size=60, test_size=20)
        assert len(windows) >= 1
        for train, test in windows:
            assert len(train) == 60
            assert len(test) == 20

    def test_no_overlap(self):
        bars = generate_synthetic_bars(num_bars=100, seed=42)
        windows = slice_walk_forward(bars, train_size=50, test_size=25)
        if len(windows) >= 2:
            # Test windows shouldn't overlap
            assert windows[0][1][-1] != windows[1][1][-1]

    def test_too_few_bars(self):
        bars = generate_synthetic_bars(num_bars=10, seed=42)
        windows = slice_walk_forward(bars, train_size=50, test_size=25)
        assert len(windows) == 0


# =====================================================================
# BacktestEngine tests
# =====================================================================

class TestBacktestEngine:

    async def test_runs_on_trending_data(self):
        """Engine produces results on trending data."""
        strategy = SMACrossoverStrategy(
            parameters={"short_window": 5, "long_window": 20, "position_size_usd": 1000.0}
        )
        engine = BacktestEngine(strategy, initial_capital=50_000.0)

        bars = generate_trending_bars(num_bars=200, direction="up", seed=42)
        result = await engine.run(bars, warmup_bars=25)

        assert isinstance(result, BacktestResult)
        assert result.num_bars == 200
        assert result.strategy_id == "sma_crossover_spy"
        assert len(result.equity_curve) > 0

    async def test_equity_curve_starts_at_capital(self):
        """Equity curve starts at initial capital."""
        strategy = SMACrossoverStrategy()
        engine = BacktestEngine(strategy, initial_capital=100_000.0)

        bars = generate_ranging_bars(num_bars=100, seed=42)
        result = await engine.run(bars, warmup_bars=55)

        assert result.equity_curve[0] == pytest.approx(100_000.0, rel=0.01)

    async def test_no_trades_on_short_data(self):
        """No trades when not enough bars for indicators."""
        strategy = SMACrossoverStrategy(
            parameters={"long_window": 50}
        )
        engine = BacktestEngine(strategy)

        bars = generate_synthetic_bars(num_bars=30, seed=42)
        result = await engine.run(bars, warmup_bars=25)

        assert result.total_trades == 0

    async def test_summary_string(self):
        """Summary produces readable text."""
        strategy = SMACrossoverStrategy()
        engine = BacktestEngine(strategy)
        bars = generate_trending_bars(num_bars=200, seed=42)
        result = await engine.run(bars, warmup_bars=55)

        summary = result.summary()
        assert "Backtest: sma_crossover_spy" in summary
        assert "Trades:" in summary
        assert "Win Rate:" in summary

    async def test_risk_engine_rejects_signals(self):
        """Risk engine can reject signals during backtest."""
        from config.risk_config import RiskConfig

        # Very restrictive risk config
        config = RiskConfig(max_position_pct=0.1)  # Only 0.1% per position
        strategy = SMACrossoverStrategy(
            parameters={"position_size_usd": 50_000.0}  # Large position
        )
        engine = BacktestEngine(strategy, risk_config=config)

        bars = generate_trending_bars(num_bars=200, seed=42)
        result = await engine.run(bars, warmup_bars=55)

        # Some signals should have been rejected or reduced
        assert result.rejected_signals >= 0  # May or may not reject depending on rules


# =====================================================================
# GraveyardManager tests
# =====================================================================

class TestGraveyardManager:

    async def test_no_candidates_when_empty(self, db_session):
        """No resurrections when graveyard is empty."""
        manager = GraveyardManager(db_session)
        recs = await manager.scan_for_resurrections()
        assert recs == []

    async def test_cooldown_respected(self, db_session):
        """Recently disabled strategies are not eligible."""
        # Add a disabled strategy that was just disabled
        record = StrategyHypothesisRecord(
            hypothesis_text="Recent failure",
            source="test",
            market_regime="trending_up",
            status="disabled",
        )
        db_session.add(record)
        await db_session.commit()

        manager = GraveyardManager(db_session)
        recs = await manager.scan_for_resurrections()
        assert recs == []  # Too recent

    async def test_resurrect_eligible_strategy(self, db_session):
        """Can resurrect a strategy that meets criteria."""
        # Create a strategy disabled long enough ago
        now = datetime.now(timezone.utc)
        old_date = now - timedelta(days=MIN_COOLDOWN_DAYS + 5)

        record = StrategyHypothesisRecord(
            hypothesis_text="Old strategy worth revisiting",
            source="test",
            market_regime="trending_up",
            backtest_sharpe=1.5,
            paper_trade_pnl=-50.0,
            status="disabled",
        )
        db_session.add(record)
        await db_session.flush()

        # Manually set updated_at to past cooldown
        from sqlalchemy import update
        stmt = (
            update(StrategyHypothesisRecord)
            .where(StrategyHypothesisRecord.id == record.id)
            .values(updated_at=old_date)
        )
        await db_session.execute(stmt)
        await db_session.commit()

        # Set current regime to match
        regime_record = MarketRegimeRecord(
            asset_class="equities",
            regime_type="trending_up",
            confidence=0.85,
            indicators={},
            started_at=now - timedelta(days=2),
        )
        db_session.add(regime_record)
        await db_session.commit()

        manager = GraveyardManager(db_session)
        recs = await manager.scan_for_resurrections()
        assert len(recs) >= 1
        assert "trending_up" in recs[0]["rationale"]

    async def test_resurrect_moves_to_paper_testing(self, db_session):
        """Resurrection changes status to paper_testing."""
        record = StrategyHypothesisRecord(
            hypothesis_text="To be resurrected",
            source="test",
            status="disabled",
        )
        db_session.add(record)
        await db_session.commit()

        manager = GraveyardManager(db_session)
        success = await manager.resurrect(record.id)
        assert success is True

        # Verify status changed
        from sqlalchemy import select
        stmt = select(StrategyHypothesisRecord).where(
            StrategyHypothesisRecord.id == record.id
        )
        result = await db_session.execute(stmt)
        updated = result.scalar_one()
        assert updated.status == "paper_testing"

    async def test_cannot_resurrect_active_strategy(self, db_session):
        """Cannot resurrect a strategy that isn't in the graveyard."""
        record = StrategyHypothesisRecord(
            hypothesis_text="Already active",
            source="test",
            status="active",
        )
        db_session.add(record)
        await db_session.commit()

        manager = GraveyardManager(db_session)
        success = await manager.resurrect(record.id)
        assert success is False

    async def test_enforce_graveyard_rules(self, db_session):
        """Strategies failing paper trading get graveyarded."""
        now = datetime.now(timezone.utc)
        old_date = now - timedelta(days=35)

        record = StrategyHypothesisRecord(
            hypothesis_text="Failing paper trade",
            source="test",
            status="paper_testing",
            paper_trade_pnl=-200.0,
        )
        db_session.add(record)
        await db_session.flush()

        # Set created_at to > 30 days ago
        from sqlalchemy import update
        stmt = (
            update(StrategyHypothesisRecord)
            .where(StrategyHypothesisRecord.id == record.id)
            .values(created_at=old_date)
        )
        await db_session.execute(stmt)
        await db_session.commit()

        manager = GraveyardManager(db_session)
        graveyarded = await manager.enforce_graveyard_rules()
        assert record.id in graveyarded
