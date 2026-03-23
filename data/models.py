"""
SQLAlchemy ORM models for Sentinel database tables.

Maps to the schema defined in CLAUDE.md. Each model corresponds to
one database table for persisting trades, risk events, and portfolio state.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Column,
    Date,
    DateTime,
    Float,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for all ORM models."""
    pass


# Generic JSON type that maps to JSONB on Postgres, JSON on SQLite
_JsonType = JSON().with_variant(JSONB(), "postgresql")


class TradeRecord(Base):
    """Every trade with full context."""

    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_id = Column(String(100), nullable=False, index=True)
    asset_class = Column(String(20), nullable=False, index=True)
    symbol = Column(String(100), nullable=False, index=True)
    side = Column(String(10), nullable=False)
    quantity = Column(Float, nullable=False)
    price = Column(Float, nullable=False)
    signal_confidence = Column(Float, nullable=False)
    risk_check_result = Column(String(20), nullable=False)
    risk_utilization_pct = Column(Float, nullable=False)
    entry_time = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    exit_time = Column(DateTime(timezone=True), nullable=True)
    exit_price = Column(Float, nullable=True)
    pnl = Column(Float, nullable=True)
    pnl_pct = Column(Float, nullable=True)
    market_regime = Column(String(30), nullable=False)
    context_snapshot_id = Column(Integer, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class StrategyPerformanceRecord(Base):
    """Daily strategy performance tracking."""

    __tablename__ = "strategy_performance"

    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_id = Column(String(100), nullable=False, index=True)
    date = Column(Date, nullable=False)
    trades_count = Column(Integer, nullable=False, default=0)
    win_rate = Column(Float, nullable=False, default=0.0)
    total_pnl = Column(Float, nullable=False, default=0.0)
    sharpe_ratio = Column(Float, nullable=True)
    max_drawdown = Column(Float, nullable=False, default=0.0)
    risk_budget_used = Column(Float, nullable=False, default=0.0)
    parameters = Column(_JsonType, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class RiskEventRecord(Base):
    """Risk events and circuit breakers."""

    __tablename__ = "risk_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_type = Column(String(50), nullable=False, index=True)
    severity = Column(String(20), nullable=False)
    details = Column(_JsonType, nullable=True)
    portfolio_value_at_event = Column(Float, nullable=False)
    action_taken = Column(String(100), nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class StrategyHypothesisRecord(Base):
    """Strategy hypotheses and test results (Learning Engine)."""

    __tablename__ = "strategy_hypotheses"

    id = Column(Integer, primary_key=True, autoincrement=True)
    hypothesis_text = Column(Text, nullable=False)
    source = Column(String(50), nullable=False)
    market_regime = Column(String(30), nullable=True)
    backtest_sharpe = Column(Float, nullable=True)
    backtest_max_dd = Column(Float, nullable=True)
    paper_trade_days = Column(Integer, nullable=True)
    paper_trade_pnl = Column(Float, nullable=True)
    status = Column(String(20), nullable=False, default="proposed")
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class MarketRegimeRecord(Base):
    """Market regime classification over time."""

    __tablename__ = "market_regimes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    asset_class = Column(String(20), nullable=False, index=True)
    regime_type = Column(String(30), nullable=False)
    confidence = Column(Float, nullable=False)
    indicators = Column(_JsonType, nullable=True)
    started_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    ended_at = Column(DateTime(timezone=True), nullable=True)


class PortfolioSnapshotRecord(Base):
    """Point-in-time portfolio state for context assembly."""

    __tablename__ = "portfolio_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    total_value = Column(Float, nullable=False)
    cash = Column(Float, nullable=False)
    positions = Column(_JsonType, nullable=True)
    risk_utilization = Column(_JsonType, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
