"""
Unit tests for Risk Engine rules.

The Risk Engine is the safety net — it gets the most thorough testing.
Every rule gets positive tests (should pass) and negative tests (should block).
"""

import pytest
from datetime import datetime

from engines.models import (
    AssetClass,
    MarketRegime,
    PortfolioSnapshot,
    PositionInfo,
    RiskCheckResult,
    RiskDecision,
    Side,
    Signal,
    SignalStrength,
)
from engines.risk.rules import (
    AssetClassConcentrationRule,
    CorrelationRule,
    DailyLossCircuitBreaker,
    HardFloorRule,
    PositionSizeRule,
    WeeklyDrawdownRule,
)
from engines.risk.engine import RiskEngine
from config.risk_config import RiskConfig


# --- Fixtures ---

def make_signal(
    symbol: str = "SPY",
    side: Side = Side.BUY,
    quantity: float = 10.0,
    target_price: float = 450.0,
    asset_class: AssetClass = AssetClass.EQUITIES,
    confidence: float = 0.75,
) -> Signal:
    return Signal(
        strategy_id="test_strategy",
        asset_class=asset_class,
        symbol=symbol,
        side=side,
        quantity=quantity,
        target_price=target_price,
        confidence=confidence,
        strength=SignalStrength.MODERATE,
        rationale="Test signal",
    )


def make_portfolio(
    total_value: float = 10000.0,
    cash: float = 5000.0,
    daily_pnl: float = 0.0,
    weekly_pnl: float = 0.0,
    drawdown: float = 0.0,
    positions: dict | None = None,
    risk_utilization: dict | None = None,
) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        total_value=total_value,
        cash=cash,
        positions=positions or {},
        risk_utilization=risk_utilization or {},
        daily_pnl=daily_pnl,
        weekly_pnl=weekly_pnl,
        total_pnl=0.0,
        drawdown_from_peak=drawdown,
    )


# === Hard Floor Rule ===

class TestHardFloorRule:
    def test_passes_when_no_drawdown(self):
        rule = HardFloorRule(floor_pct=90.0)
        result = rule.check(make_signal(), make_portfolio(), 10.0)
        assert not result.rejected

    def test_passes_just_under_threshold(self):
        rule = HardFloorRule(floor_pct=90.0)
        portfolio = make_portfolio(drawdown=9.9)
        result = rule.check(make_signal(), portfolio, 10.0)
        assert not result.rejected

    def test_rejects_at_threshold(self):
        rule = HardFloorRule(floor_pct=90.0)
        portfolio = make_portfolio(drawdown=10.0)
        result = rule.check(make_signal(), portfolio, 10.0)
        assert result.rejected
        assert "hard floor" in result.reason.lower()

    def test_rejects_beyond_threshold(self):
        rule = HardFloorRule(floor_pct=90.0)
        portfolio = make_portfolio(drawdown=15.0)
        result = rule.check(make_signal(), portfolio, 10.0)
        assert result.rejected

    def test_custom_floor(self):
        rule = HardFloorRule(floor_pct=95.0)
        portfolio = make_portfolio(drawdown=5.0)
        result = rule.check(make_signal(), portfolio, 10.0)
        assert result.rejected


# === Position Size Rule ===

class TestPositionSizeRule:
    def test_passes_within_limit(self):
        rule = PositionSizeRule(max_position_pct=10.0)
        signal = make_signal(quantity=1.0, target_price=500.0)
        portfolio = make_portfolio(total_value=10000.0)
        result = rule.check(signal, portfolio, 1.0)
        assert not result.rejected

    def test_reduces_oversized_position(self):
        rule = PositionSizeRule(max_position_pct=10.0)
        # 10 shares * $450 = $4500, which is 45% of $10k portfolio
        signal = make_signal(quantity=10.0, target_price=450.0)
        portfolio = make_portfolio(total_value=10000.0)
        result = rule.check(signal, portfolio, 10.0)
        assert result.reduced_quantity is not None
        # Max should be ~2.22 shares ($1000 / $450)
        assert result.reduced_quantity < 10.0
        assert result.reduced_quantity * 450.0 <= 1000.0 + 0.01  # Allow float precision

    def test_rejects_zero_portfolio(self):
        rule = PositionSizeRule(max_position_pct=10.0)
        portfolio = make_portfolio(total_value=0.0)
        result = rule.check(make_signal(), portfolio, 10.0)
        assert result.rejected


# === Asset Class Concentration Rule ===

class TestAssetClassConcentrationRule:
    def test_passes_within_limit(self):
        rule = AssetClassConcentrationRule(max_asset_class_pct=40.0)
        signal = make_signal(quantity=1.0, target_price=500.0)
        portfolio = make_portfolio(
            total_value=10000.0,
            risk_utilization={"equities": 10.0},
        )
        result = rule.check(signal, portfolio, 1.0)
        assert not result.rejected

    def test_reduces_when_would_exceed(self):
        rule = AssetClassConcentrationRule(max_asset_class_pct=40.0)
        signal = make_signal(quantity=10.0, target_price=450.0)
        portfolio = make_portfolio(
            total_value=10000.0,
            risk_utilization={"equities": 35.0},
        )
        result = rule.check(signal, portfolio, 10.0)
        # 35% + 45% = 80%, so should reduce to use only remaining 5%
        assert result.reduced_quantity is not None
        assert result.reduced_quantity < 10.0

    def test_rejects_when_already_at_limit(self):
        rule = AssetClassConcentrationRule(max_asset_class_pct=40.0)
        signal = make_signal(quantity=1.0, target_price=450.0)
        portfolio = make_portfolio(
            total_value=10000.0,
            risk_utilization={"equities": 40.0},
        )
        result = rule.check(signal, portfolio, 1.0)
        assert result.rejected


# === Daily Loss Circuit Breaker ===

class TestDailyLossCircuitBreaker:
    def test_passes_when_profitable(self):
        rule = DailyLossCircuitBreaker(max_daily_loss_pct=3.0)
        portfolio = make_portfolio(daily_pnl=100.0)
        result = rule.check(make_signal(), portfolio, 10.0)
        assert not result.rejected

    def test_passes_just_under_threshold(self):
        rule = DailyLossCircuitBreaker(max_daily_loss_pct=3.0)
        portfolio = make_portfolio(total_value=10000.0, daily_pnl=-290.0)
        result = rule.check(make_signal(), portfolio, 10.0)
        assert not result.rejected

    def test_rejects_at_threshold(self):
        rule = DailyLossCircuitBreaker(max_daily_loss_pct=3.0)
        portfolio = make_portfolio(total_value=10000.0, daily_pnl=-300.0)
        result = rule.check(make_signal(), portfolio, 10.0)
        assert result.rejected
        assert "circuit breaker" in result.reason.lower()

    def test_rejects_beyond_threshold(self):
        rule = DailyLossCircuitBreaker(max_daily_loss_pct=3.0)
        portfolio = make_portfolio(total_value=10000.0, daily_pnl=-500.0)
        result = rule.check(make_signal(), portfolio, 10.0)
        assert result.rejected


# === Weekly Drawdown Rule ===

class TestWeeklyDrawdownRule:
    def test_passes_when_no_loss(self):
        rule = WeeklyDrawdownRule(max_weekly_drawdown_pct=5.0)
        portfolio = make_portfolio(weekly_pnl=200.0)
        result = rule.check(make_signal(), portfolio, 10.0)
        assert not result.rejected
        assert result.reduced_quantity is None

    def test_reduces_when_threshold_hit(self):
        rule = WeeklyDrawdownRule(
            max_weekly_drawdown_pct=5.0, reduction_factor=0.5
        )
        portfolio = make_portfolio(total_value=10000.0, weekly_pnl=-500.0)
        result = rule.check(make_signal(), portfolio, 10.0)
        assert result.reduced_quantity == 5.0  # 50% of 10


# === Correlation Rule ===

class TestCorrelationRule:
    def test_passes_uncorrelated_symbol(self):
        rule = CorrelationRule(max_correlated_exposure_pct=50.0)
        signal = make_signal(symbol="AAPL")  # Not in any default group
        portfolio = make_portfolio()
        result = rule.check(signal, portfolio, 10.0)
        assert not result.rejected

    def test_rejects_correlated_overexposure(self):
        rule = CorrelationRule(max_correlated_exposure_pct=50.0)
        signal = make_signal(symbol="QQQ", quantity=5.0, target_price=400.0)
        # Already have SPY position worth $4000 (40% of $10k)
        portfolio = make_portfolio(
            total_value=10000.0,
            positions={
                "SPY": PositionInfo(
                    symbol="SPY",
                    asset_class=AssetClass.EQUITIES,
                    side=Side.BUY,
                    quantity=10.0,
                    entry_price=400.0,
                    current_price=400.0,
                    unrealized_pnl=0.0,
                    pnl_pct=0.0,
                    strategy_id="test",
                )
            },
        )
        result = rule.check(signal, portfolio, 5.0)
        # SPY ($4000) + QQQ ($2000) = $6000 = 60% > 50%
        assert result.rejected


# === Full Risk Engine Integration ===

class TestRiskEngineIntegration:
    def test_approves_good_signal(self):
        engine = RiskEngine(RiskConfig())
        signal = make_signal(quantity=1.0, target_price=450.0)
        portfolio = make_portfolio(total_value=10000.0)
        result = engine.evaluate(signal, portfolio)
        assert result.decision in (RiskDecision.APPROVED, RiskDecision.REDUCED)

    def test_rejects_when_at_hard_floor(self):
        engine = RiskEngine(RiskConfig(hard_floor_pct=90.0))
        signal = make_signal(quantity=1.0, target_price=450.0)
        portfolio = make_portfolio(drawdown=11.0)
        result = engine.evaluate(signal, portfolio)
        assert result.decision == RiskDecision.REJECTED

    def test_circuit_breaker_blocks_all(self):
        engine = RiskEngine(RiskConfig())
        engine.activate_circuit_breaker(duration_hours=1)
        signal = make_signal(quantity=1.0, target_price=100.0)
        portfolio = make_portfolio()
        result = engine.evaluate(signal, portfolio)
        assert result.decision == RiskDecision.REJECTED
        assert "circuit breaker" in result.rejection_reasons[0].lower()
