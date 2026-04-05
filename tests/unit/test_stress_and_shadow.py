"""
Unit tests for Risk Engine stress testing and Shadow Mode executor.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime

from config.risk_config import RiskConfig
from engines.execution.shadow import ShadowExecutor, ShadowStats, MIN_TRADE_SIZES
from engines.execution.base import Executor
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
    TradeResult,
)
from engines.risk.stress_test import (
    StressTestRunner,
    build_flash_crash_scenario,
    build_correlated_selloff_scenario,
    build_gap_down_scenario,
    build_cascading_failure_scenario,
    build_concentration_drift_scenario,
    build_slow_bleed_scenario,
)


def make_signal(
    symbol: str = "SPY",
    side: Side = Side.BUY,
    quantity: float = 10.0,
    price: float = 450.0,
    asset_class: AssetClass = AssetClass.EQUITIES,
) -> Signal:
    return Signal(
        strategy_id="test",
        asset_class=asset_class,
        symbol=symbol,
        side=side,
        quantity=quantity,
        target_price=price,
        confidence=0.8,
        strength=SignalStrength.STRONG,
        rationale="Test",
        market_regime=MarketRegime.UNKNOWN,
    )


def make_approved(signal: Signal, quantity: float | None = None) -> RiskCheckResult:
    return RiskCheckResult(
        decision=RiskDecision.APPROVED,
        original_signal=signal,
        approved_quantity=quantity or signal.quantity,
        risk_utilization_pct=10.0,
        portfolio_value=100000.0,
    )


# =====================================================================
# Stress Test tests
# =====================================================================

class TestStressScenarios:

    def test_flash_crash_rejects_all(self):
        """Flash crash: ALL signals must be rejected."""
        runner = StressTestRunner()
        scenario = build_flash_crash_scenario()
        result = runner._run_scenario(scenario)
        assert result.passed
        assert result.rejected == 3
        assert result.approved == 0

    def test_correlated_selloff_blocks_equities(self):
        """Correlated selloff: new equity trades blocked."""
        runner = StressTestRunner()
        scenario = build_correlated_selloff_scenario()
        result = runner._run_scenario(scenario)
        assert result.passed
        assert result.rejected >= 2

    def test_gap_down_hard_floor(self):
        """Gap down: hard floor halts everything."""
        runner = StressTestRunner()
        scenario = build_gap_down_scenario()
        result = runner._run_scenario(scenario)
        assert result.passed
        assert result.rejected == 3

    def test_cascading_circuit_breaker(self):
        """Cascading: circuit breaker blocks all."""
        runner = StressTestRunner()
        scenario = build_cascading_failure_scenario()
        result = runner._run_scenario(scenario)
        assert result.passed
        assert result.rejected == 2

    def test_concentration_drift(self):
        """Concentration: crypto trades blocked when BTC dominates."""
        runner = StressTestRunner()
        scenario = build_concentration_drift_scenario()
        result = runner._run_scenario(scenario)
        assert result.passed
        assert result.rejected >= 1

    def test_slow_bleed_reduces_not_rejects(self):
        """Slow bleed: positions reduced, not rejected."""
        runner = StressTestRunner()
        scenario = build_slow_bleed_scenario()
        result = runner._run_scenario(scenario)
        assert result.passed
        assert result.reduced >= 1
        assert result.rejected == 0


class TestStressTestRunner:

    def test_run_all_passes(self):
        """All default stress scenarios pass with default config."""
        runner = StressTestRunner()
        results = runner.run_all()
        assert all(r.passed for r in results), [
            f"{r.scenario_name}: {r.errors}" for r in results if not r.passed
        ]

    def test_summary_readable(self):
        """Summary produces readable output."""
        runner = StressTestRunner()
        results = runner.run_all()
        summary = runner.summary(results)
        assert "Stress Test Results" in summary
        assert "ALL PASSED" in summary

    def test_custom_config(self):
        """Custom risk config is applied to all scenarios."""
        # Very restrictive config — everything should still be rejected
        config = RiskConfig(
            hard_floor_pct=99.0,  # 1% max drawdown
            max_daily_loss_pct=0.5,
        )
        runner = StressTestRunner(config)
        results = runner.run_all()
        # Flash crash, gap down, etc. should still pass with stricter config
        assert len(results) == 6


# =====================================================================
# Shadow Mode tests
# =====================================================================

class TestShadowStats:

    def test_healthy_initial(self):
        """Fresh stats are healthy."""
        stats = ShadowStats()
        assert stats.is_healthy
        assert stats.divergence_count == 0

    def test_unhealthy_high_divergence(self):
        """Stats are unhealthy with high price divergence."""
        stats = ShadowStats(max_price_divergence_pct=3.0)
        assert not stats.is_healthy

    def test_unhealthy_low_fill_rate(self):
        """Stats are unhealthy with low fill rate match."""
        stats = ShadowStats(fill_rate_match=0.5)
        assert not stats.is_healthy

    def test_summary_dict(self):
        """Summary produces expected structure."""
        stats = ShadowStats(total_signals=10, live_executed=8, live_failed=2)
        s = stats.summary()
        assert s["total_signals"] == 10
        assert s["healthy"] is True


class TestShadowExecutor:

    def _make_mock_executor(self, fill_price: float = 450.5) -> Executor:
        """Build a mock executor that returns predictable fills."""
        executor = Executor()
        mock_adapter = AsyncMock()
        mock_adapter.platform_name = "mock_equities"
        mock_adapter.asset_class = AssetClass.EQUITIES
        mock_adapter.observe_only = False

        signal = make_signal()
        mock_adapter.execute_trade = AsyncMock(return_value=TradeResult(
            trade_id="live-001",
            signal=signal,
            risk_check=make_approved(signal),
            executed=True,
            fill_price=fill_price,
            fill_quantity=1.0,
            platform="mock_equities",
        ))

        executor._adapters[AssetClass.EQUITIES] = mock_adapter
        return executor

    async def test_paper_always_fills(self):
        """Paper simulation always fills at target price."""
        real_executor = self._make_mock_executor()
        shadow = ShadowExecutor(real_executor)

        signal = make_signal(price=450.0)
        approved = make_approved(signal)

        live, paper = await shadow.execute_shadow(approved)

        assert paper.executed
        assert paper.fill_price == 450.0
        assert paper.platform.startswith("paper_")

    async def test_live_executes_at_min_size(self):
        """Live execution uses minimum size."""
        real_executor = self._make_mock_executor()
        shadow = ShadowExecutor(real_executor)

        signal = make_signal(quantity=100.0, price=450.0)
        approved = make_approved(signal, quantity=100.0)

        live, paper = await shadow.execute_shadow(approved)

        # Check the adapter was called with min size
        adapter = real_executor._adapters[AssetClass.EQUITIES]
        call_args = adapter.execute_trade.call_args[0][0]
        assert call_args.approved_quantity == MIN_TRADE_SIZES[AssetClass.EQUITIES]

    async def test_no_divergence_on_close_prices(self):
        """No divergence when fill prices are close."""
        real_executor = self._make_mock_executor(fill_price=450.01)
        shadow = ShadowExecutor(real_executor)

        signal = make_signal(price=450.0)
        approved = make_approved(signal)

        await shadow.execute_shadow(approved)

        # 0.002% divergence — should be below 0.1% logging threshold
        assert shadow.stats.divergence_count == 0

    async def test_detects_price_divergence(self):
        """Detects price divergence when fills differ significantly."""
        # Large slippage: target 450, filled at 460
        real_executor = self._make_mock_executor(fill_price=460.0)
        shadow = ShadowExecutor(real_executor)

        signal = make_signal(price=450.0)
        approved = make_approved(signal)

        await shadow.execute_shadow(approved)

        assert shadow.stats.divergence_count == 1
        assert shadow.stats.divergences[0].divergence_type == "fill_price"
        assert shadow.stats.max_price_divergence_pct > 2.0

    async def test_detects_fill_rate_divergence(self):
        """Detects when paper fills but live doesn't."""
        real_executor = Executor()
        mock_adapter = AsyncMock()
        mock_adapter.platform_name = "mock"
        mock_adapter.asset_class = AssetClass.EQUITIES
        mock_adapter.observe_only = False

        signal = make_signal()
        mock_adapter.execute_trade = AsyncMock(return_value=TradeResult(
            trade_id="fail",
            signal=signal,
            risk_check=make_approved(signal),
            executed=False,  # Live fails
            platform="mock",
            error_message="Insufficient funds",
        ))
        real_executor._adapters[AssetClass.EQUITIES] = mock_adapter

        shadow = ShadowExecutor(real_executor)
        approved = make_approved(signal)
        await shadow.execute_shadow(approved)

        assert shadow.stats.divergence_count == 1
        assert shadow.stats.divergences[0].divergence_type == "fill_rate"

    async def test_auto_pause_on_high_divergence(self):
        """Live trading pauses on high divergence."""
        real_executor = self._make_mock_executor(fill_price=500.0)  # 11% off
        shadow = ShadowExecutor(real_executor, max_divergence_pct=2.0)

        signal = make_signal(price=450.0)
        approved = make_approved(signal)

        await shadow.execute_shadow(approved)

        assert shadow._live_paused

    async def test_resume_live(self):
        """Can resume live trading after pause."""
        real_executor = self._make_mock_executor()
        shadow = ShadowExecutor(real_executor)
        shadow._live_paused = True

        shadow.resume_live()
        assert not shadow._live_paused

    async def test_skips_live_when_paused(self):
        """Skips live execution when paused, still does paper."""
        real_executor = self._make_mock_executor()
        shadow = ShadowExecutor(real_executor)
        shadow._live_paused = True

        signal = make_signal()
        approved = make_approved(signal)

        live, paper = await shadow.execute_shadow(approved)

        assert paper.executed  # Paper still works
        assert not live.executed  # Live skipped
        assert "paused" in live.error_message.lower()

    async def test_stats_accumulate(self):
        """Stats track across multiple executions."""
        real_executor = self._make_mock_executor(fill_price=450.0)
        shadow = ShadowExecutor(real_executor)

        for _ in range(5):
            signal = make_signal(price=450.0)
            approved = make_approved(signal)
            await shadow.execute_shadow(approved)

        assert shadow.stats.total_signals == 5
        assert shadow.stats.live_executed == 5
        assert shadow.stats.paper_fills == 5

    async def test_status_output(self):
        """Status returns expected structure."""
        real_executor = self._make_mock_executor()
        shadow = ShadowExecutor(real_executor)

        status = shadow.status()
        assert status["mode"] == "shadow"
        assert "stats" in status
        assert "recent_divergences" in status

    async def test_reset_stats(self):
        """Reset clears all accumulated data."""
        real_executor = self._make_mock_executor()
        shadow = ShadowExecutor(real_executor)

        signal = make_signal()
        approved = make_approved(signal)
        await shadow.execute_shadow(approved)
        assert shadow.stats.total_signals == 1

        shadow.reset_stats()
        assert shadow.stats.total_signals == 0
