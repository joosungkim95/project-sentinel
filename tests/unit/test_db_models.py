"""
Unit tests for SQLAlchemy ORM models.

Tests model instantiation and column defaults using an in-memory SQLite database.
"""

import pytest
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from data.models import (
    Base,
    TradeRecord,
    StrategyPerformanceRecord,
    RiskEventRecord,
    StrategyHypothesisRecord,
    MarketRegimeRecord,
    PortfolioSnapshotRecord,
)


@pytest.fixture
def db_session():
    """Create an in-memory SQLite database for testing."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


class TestTradeRecord:
    def test_create_trade(self, db_session):
        trade = TradeRecord(
            strategy_id="sma_crossover_spy",
            asset_class="equities",
            symbol="SPY",
            side="buy",
            quantity=10.0,
            price=450.0,
            signal_confidence=0.75,
            risk_check_result="approved",
            risk_utilization_pct=5.0,
            market_regime="trending_up",
        )
        db_session.add(trade)
        db_session.commit()
        db_session.refresh(trade)
        assert trade.id is not None
        assert trade.symbol == "SPY"
        assert trade.pnl is None

    def test_trade_with_exit(self, db_session):
        trade = TradeRecord(
            strategy_id="sma_crossover_spy",
            asset_class="equities",
            symbol="SPY",
            side="buy",
            quantity=10.0,
            price=450.0,
            signal_confidence=0.75,
            risk_check_result="approved",
            risk_utilization_pct=5.0,
            market_regime="trending_up",
            exit_price=460.0,
            pnl=100.0,
            pnl_pct=2.22,
        )
        db_session.add(trade)
        db_session.commit()
        assert trade.pnl == 100.0


class TestRiskEventRecord:
    def test_create_risk_event(self, db_session):
        event = RiskEventRecord(
            event_type="circuit_breaker",
            severity="critical",
            details={"reason": "daily loss exceeded 3%"},
            portfolio_value_at_event=9500.0,
            action_taken="halt_all_trading",
        )
        db_session.add(event)
        db_session.commit()
        assert event.id is not None
        assert event.severity == "critical"


class TestStrategyPerformanceRecord:
    def test_create_performance(self, db_session):
        perf = StrategyPerformanceRecord(
            strategy_id="sma_crossover_spy",
            date=datetime.now(timezone.utc).date(),
            trades_count=15,
            win_rate=0.6,
            total_pnl=250.0,
            sharpe_ratio=1.2,
            max_drawdown=3.5,
            risk_budget_used=25.0,
            parameters={"short_window": 10, "long_window": 50},
        )
        db_session.add(perf)
        db_session.commit()
        assert perf.id is not None


class TestPortfolioSnapshotRecord:
    def test_create_snapshot(self, db_session):
        snap = PortfolioSnapshotRecord(
            total_value=10500.0,
            cash=5000.0,
            positions={"SPY": {"qty": 10, "price": 450}},
            risk_utilization={"equities": 42.8},
        )
        db_session.add(snap)
        db_session.commit()
        assert snap.id is not None
        assert snap.total_value == 10500.0


class TestStrategyHypothesisRecord:
    def test_create_hypothesis(self, db_session):
        hyp = StrategyHypothesisRecord(
            hypothesis_text="BTC mean reversion at 20-day bands",
            source="learning_engine",
            market_regime="ranging",
            status="proposed",
        )
        db_session.add(hyp)
        db_session.commit()
        assert hyp.id is not None


class TestMarketRegimeRecord:
    def test_create_regime(self, db_session):
        regime = MarketRegimeRecord(
            asset_class="equities",
            regime_type="trending_up",
            confidence=0.85,
            indicators={"sma_50_200": "golden_cross", "vix": 15.2},
        )
        db_session.add(regime)
        db_session.commit()
        assert regime.id is not None
