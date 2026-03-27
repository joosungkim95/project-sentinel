"""
Integration tests for the tier-based pipeline and risk filtering.

Verifies end-to-end behavior:
1. SCOUT strategies generate signals with relaxed filters.
2. Risk engine filters signals based on tier confidence thresholds.
3. TierBudgetRule enforces per-tier USD exposure limits.
"""

import numpy as np
import pytest

from config.risk_config import RiskConfig
from config.tiers import TIER_RISK_BUDGET, StrategyTier
from engines.models import (
    AssetClass,
    MarketRegime,
    PortfolioSnapshot,
    RiskDecision,
    Side,
    Signal,
    SignalStrength,
)
from engines.risk.engine import RiskEngine
from engines.strategy.equities.momentum import MomentumStrategy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_portfolio(total_value: float = 10_000.0) -> PortfolioSnapshot:
    """Return a clean, healthy portfolio snapshot."""
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


def _make_signal(
    confidence: float = 0.5,
    tier: StrategyTier = StrategyTier.CORE,
    position_size_usd: float = 500.0,
    symbol: str = "SPY",
) -> Signal:
    """Return a minimal valid Signal for the given tier."""
    return Signal(
        strategy_id="test_strategy",
        asset_class=AssetClass.EQUITIES,
        symbol=symbol,
        side=Side.BUY,
        quantity=5.0,
        target_price=100.0,
        confidence=confidence,
        strength=SignalStrength.MODERATE,
        rationale="integration test signal",
        tier=tier,
        position_size_usd=position_size_usd,
    )


def _make_rising_bars(
    symbol: str,
    n_bars: int = 30,
    base_price: float = 100.0,
    base_volume: float = 1_000_000,
) -> dict[str, list[dict]]:
    """
    Build synthetic bar data that produces RSI in the 55–85 zone with
    above-average volume, triggering a SCOUT momentum BUY signal.

    Uses a zigzag pattern (mostly up, small pullback every 3rd bar) so RSI
    stays in the buy zone rather than pinning at 99 from pure upward drift.
    """
    bars = []
    price = base_price
    rng = np.random.default_rng(7)  # seed 7 reliably lands RSI ~77 on bar 30
    for i in range(n_bars):
        if i % 3 == 2:
            # Small pullback to keep RSI below 85
            change = rng.uniform(-0.004, -0.001)
        else:
            # Net upward — keeps RSI above 55
            change = rng.uniform(0.002, 0.008)
        price = price * (1.0 + change)
        bars.append(
            {
                "open": price * 0.998,
                "high": price * 1.003,
                "low": price * 0.996,
                "close": price,
                "volume": base_volume * rng.uniform(1.1, 1.8),
            }
        )
    return {symbol: bars}


def _make_risk_engine() -> RiskEngine:
    """Return a RiskEngine with default (permissive enough) config."""
    return RiskEngine(config=RiskConfig())


# ---------------------------------------------------------------------------
# Test 1: SCOUT generates signals with relaxed filters
# ---------------------------------------------------------------------------

class TestScoutGeneratesSignals:
    @pytest.mark.asyncio
    async def test_scout_generates_signals_with_loose_filters(self):
        """
        MomentumStrategy (SCOUT tier) should generate BUY signals from
        rising bar data that satisfies RSI >= 55 and volume >= 1.0x.
        """
        strategy = MomentumStrategy()

        # Use a single symbol so the test is deterministic
        symbol = "SPY"
        bars = _make_rising_bars(symbol, n_bars=30)

        signals = await strategy.generate_signals(bars, MarketRegime.TRENDING_UP)

        assert len(signals) > 0, (
            "MomentumStrategy (SCOUT) should generate at least one signal "
            "from zigzag rising bars with above-average volume."
        )

        for sig in signals:
            assert sig.tier == StrategyTier.SCOUT
            assert sig.asset_class == AssetClass.EQUITIES
            assert sig.side == Side.BUY
            assert sig.confidence >= 0.3  # SCOUT ConfidenceGate threshold


# ---------------------------------------------------------------------------
# Test 2: Risk engine filters by tier confidence threshold
# ---------------------------------------------------------------------------

class TestRiskEngineFiltersByTierConfidence:
    def test_scout_signal_at_0_35_passes_confidence_gate(self):
        """
        SCOUT tier has a confidence threshold of 0.3.
        A signal with confidence=0.35 must pass ConfidenceGateRule.
        """
        engine = _make_risk_engine()
        portfolio = _make_portfolio()
        signal = _make_signal(confidence=0.35, tier=StrategyTier.SCOUT)

        result = engine.evaluate(signal, portfolio)

        assert result.decision != RiskDecision.REJECTED or all(
            "confidence" not in r.lower() for r in result.rejection_reasons
        ), (
            "SCOUT signal at 0.35 confidence should not be rejected by "
            "ConfidenceGateRule (threshold=0.3)."
        )

    def test_sniper_signal_at_0_35_rejected_by_confidence_gate(self):
        """
        SNIPER tier has a confidence threshold of 0.7.
        A signal with confidence=0.35 must be rejected by ConfidenceGateRule.
        """
        engine = _make_risk_engine()
        portfolio = _make_portfolio()
        signal = _make_signal(confidence=0.35, tier=StrategyTier.SNIPER)

        result = engine.evaluate(signal, portfolio)

        assert result.decision == RiskDecision.REJECTED, (
            "SNIPER signal at 0.35 confidence should be rejected by "
            "ConfidenceGateRule (threshold=0.7)."
        )
        assert any(
            "confidence" in r.lower() for r in result.rejection_reasons
        ), "Rejection reason should mention confidence."


# ---------------------------------------------------------------------------
# Test 3: TierBudgetRule enforces per-tier exposure limits
# ---------------------------------------------------------------------------

class TestTierBudgetLimitsExposure:
    def test_scout_budget_exhausted_rejects_scout_but_not_core(self):
        """
        Fill the SCOUT budget via on_trade_executed(), then verify:
        - The next SCOUT signal is rejected by TierBudgetRule.
        - A CORE signal with the same size is still approved.

        Portfolio = $10,000. SCOUT budget = 20% = $2,000.
        """
        engine = _make_risk_engine()
        portfolio = _make_portfolio(total_value=10_000.0)
        tier_budget_rule = engine._tier_budget_rule

        scout_budget_usd = TIER_RISK_BUDGET[StrategyTier.SCOUT] * portfolio.total_value
        # e.g. 0.20 * 10_000 = 2_000

        # Simulate a prior trade that exhausted the SCOUT budget
        exhaustion_signal = _make_signal(
            confidence=0.5,
            tier=StrategyTier.SCOUT,
            position_size_usd=scout_budget_usd,
        )
        tier_budget_rule.on_trade_executed(exhaustion_signal)

        # --- New SCOUT signal should now be rejected ---
        new_scout_signal = _make_signal(
            confidence=0.5,
            tier=StrategyTier.SCOUT,
            position_size_usd=100.0,  # Even a small amount exceeds the budget
        )
        scout_result = engine.evaluate(new_scout_signal, portfolio)

        assert scout_result.decision == RiskDecision.REJECTED, (
            "SCOUT signal should be rejected after SCOUT budget is fully used."
        )
        assert any(
            "scout" in r.lower() or "budget" in r.lower()
            for r in scout_result.rejection_reasons
        ), "Rejection reason should mention scout tier or budget."

        # --- CORE signal should still pass ---
        core_signal = _make_signal(
            confidence=0.6,
            tier=StrategyTier.CORE,
            position_size_usd=500.0,
        )
        core_result = engine.evaluate(core_signal, portfolio)

        assert core_result.decision != RiskDecision.REJECTED or all(
            "budget" not in r.lower() for r in core_result.rejection_reasons
        ), (
            "CORE signal should not be rejected by TierBudgetRule when only "
            "the SCOUT budget has been exhausted."
        )
