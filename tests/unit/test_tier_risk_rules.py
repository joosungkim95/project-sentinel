"""
Unit tests for tier-based risk rules: ConfidenceGateRule, TierBudgetRule,
and updated CorrelationRule.CORRELATION_GROUPS.
"""

import pytest

from config.tiers import StrategyTier
from engines.models import (
    AssetClass,
    PortfolioSnapshot,
    Side,
    Signal,
    SignalStrength,
)
from engines.risk.rules import (
    ConfidenceGateRule,
    CorrelationRule,
    TierBudgetRule,
)


# --- Helpers ---

def _make_signal(
    symbol: str = "SPY",
    confidence: float = 0.5,
    tier: StrategyTier = StrategyTier.CORE,
    position_size_usd: float = 1000.0,
    quantity: float = 10.0,
    target_price: float = 100.0,
    asset_class: AssetClass = AssetClass.EQUITIES,
) -> Signal:
    return Signal(
        strategy_id="test_strategy",
        asset_class=asset_class,
        symbol=symbol,
        side=Side.BUY,
        quantity=quantity,
        target_price=target_price,
        confidence=confidence,
        strength=SignalStrength.MODERATE,
        rationale="test signal",
        tier=tier,
        position_size_usd=position_size_usd,
    )


def _make_portfolio(total_value: float = 10_000.0) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        total_value=total_value,
        cash=total_value,
        positions={},
        risk_utilization={},
        daily_pnl=0.0,
        weekly_pnl=0.0,
        total_pnl=0.0,
        drawdown_from_peak=0.0,
    )


# --- ConfidenceGateRule ---

class TestConfidenceGateRule:
    def test_scout_passes_at_threshold(self):
        """Scout tier threshold is 0.3 — exactly 0.3 should pass."""
        rule = ConfidenceGateRule()
        signal = _make_signal(confidence=0.3, tier=StrategyTier.SCOUT)
        result = rule.check(signal, _make_portfolio(), signal.quantity)
        assert not result.rejected

    def test_scout_fails_below_threshold(self):
        """Scout tier threshold is 0.3 — 0.2 should be rejected."""
        rule = ConfidenceGateRule()
        signal = _make_signal(confidence=0.2, tier=StrategyTier.SCOUT)
        result = rule.check(signal, _make_portfolio(), signal.quantity)
        assert result.rejected
        assert "confidence" in result.reason.lower()

    def test_sniper_requires_0_7(self):
        """Sniper tier threshold is 0.7 — 0.69 should be rejected."""
        rule = ConfidenceGateRule()
        signal = _make_signal(confidence=0.69, tier=StrategyTier.SNIPER)
        result = rule.check(signal, _make_portfolio(), signal.quantity)
        assert result.rejected

    def test_sniper_passes_at_0_7(self):
        """Sniper tier threshold is 0.7 — exactly 0.7 should pass."""
        rule = ConfidenceGateRule()
        signal = _make_signal(confidence=0.7, tier=StrategyTier.SNIPER)
        result = rule.check(signal, _make_portfolio(), signal.quantity)
        assert not result.rejected

    def test_core_passes_at_0_5(self):
        """Core tier threshold is 0.5."""
        rule = ConfidenceGateRule()
        signal = _make_signal(confidence=0.5, tier=StrategyTier.CORE)
        result = rule.check(signal, _make_portfolio(), signal.quantity)
        assert not result.rejected


# --- TierBudgetRule ---

class TestTierBudgetRule:
    def test_approves_within_budget(self):
        """Scout budget is 20% of 10k = $2000. A $1000 trade should pass."""
        rule = TierBudgetRule()
        signal = _make_signal(
            tier=StrategyTier.SCOUT, position_size_usd=1000.0
        )
        portfolio = _make_portfolio(total_value=10_000.0)
        result = rule.check(signal, portfolio, signal.quantity)
        assert not result.rejected

    def test_rejects_when_exceeded(self):
        """Scout budget is 20% of 10k = $2000. A $2500 trade should fail."""
        rule = TierBudgetRule()
        signal = _make_signal(
            tier=StrategyTier.SCOUT, position_size_usd=2500.0
        )
        portfolio = _make_portfolio(total_value=10_000.0)
        result = rule.check(signal, portfolio, signal.quantity)
        assert result.rejected
        assert "budget" in result.reason.lower()

    def test_position_close_frees_budget(self):
        """After a position is opened and then closed, budget is freed."""
        rule = TierBudgetRule()
        portfolio = _make_portfolio(total_value=10_000.0)

        # Open a $1500 scout position
        signal_a = _make_signal(
            symbol="AAPL",
            tier=StrategyTier.SCOUT,
            position_size_usd=1500.0,
        )
        result = rule.check(signal_a, portfolio, signal_a.quantity)
        assert not result.rejected
        rule.on_trade_executed(signal_a)

        # Try another $1000 scout — should exceed ($1500 + $1000 > $2000)
        signal_b = _make_signal(
            symbol="MSFT",
            tier=StrategyTier.SCOUT,
            position_size_usd=1000.0,
        )
        result = rule.check(signal_b, portfolio, signal_b.quantity)
        assert result.rejected

        # Close first position
        rule.on_position_closed(signal_a)

        # Now the $1000 trade should pass
        result = rule.check(signal_b, portfolio, signal_b.quantity)
        assert not result.rejected

    def test_tiers_are_independent(self):
        """Filling scout budget should not affect core budget."""
        rule = TierBudgetRule()
        portfolio = _make_portfolio(total_value=10_000.0)

        # Fill scout budget ($2000)
        scout_signal = _make_signal(
            tier=StrategyTier.SCOUT, position_size_usd=2000.0
        )
        result = rule.check(scout_signal, portfolio, scout_signal.quantity)
        assert not result.rejected
        rule.on_trade_executed(scout_signal)

        # Core trade should still be allowed (core budget = 50% = $5000)
        core_signal = _make_signal(
            tier=StrategyTier.CORE, position_size_usd=3000.0
        )
        result = rule.check(core_signal, portfolio, core_signal.quantity)
        assert not result.rejected


# --- CorrelationRule groups ---

class TestCorrelationGroups:
    def test_crypto_ecosystem_has_avax_and_doge(self):
        groups = CorrelationRule.CORRELATION_GROUPS
        assert "AVAX" in groups["crypto_ecosystem"]
        assert "DOGE" in groups["crypto_ecosystem"]

    def test_us_mega_tech_group_exists(self):
        groups = CorrelationRule.CORRELATION_GROUPS
        assert "us_mega_tech" in groups
        assert "AAPL" in groups["us_mega_tech"]
        assert "MSFT" in groups["us_mega_tech"]
        assert "NVDA" in groups["us_mega_tech"]
