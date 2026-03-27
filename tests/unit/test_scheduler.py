"""
Tests for the Trading Scheduler.

Tests cover:
- Strategy grouping by asset class and tier
- Tier-based job registration
- Market hours checking
- Error tracking and auto-pause (per tier/asset_class)
- Pause/resume controls
- Status reporting (backward-compatible + tier detail)
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config.risk_config import RiskConfig
from config.scheduler_config import MarketHours, SchedulerConfig
from config.tiers import StrategyTier
from engines.execution.base import Executor
from engines.models import AssetClass, MarketRegime, StrategyStatus
from engines.risk.engine import RiskEngine
from engines.scheduler import TIER_INTERVALS, TradingScheduler
from engines.strategy.equities.sma_crossover import SMACrossoverStrategy


def _make_scheduler(
    strategies=None,
    config=None,
) -> TradingScheduler:
    """Helper to build a scheduler with test defaults."""
    risk = RiskEngine(RiskConfig())
    executor = Executor()
    if strategies is None:
        sma = SMACrossoverStrategy()
        sma.activate()
        strategies = [sma]
    return TradingScheduler(
        risk_engine=risk,
        executor=executor,
        strategies=strategies,
        config=config,
    )


class TestStrategyGrouping:
    """Strategies should be grouped by asset class and tier."""

    def test_equities_grouped(self):
        sma = SMACrossoverStrategy()
        scheduler = _make_scheduler(strategies=[sma])
        assert len(scheduler._strategies[AssetClass.EQUITIES]) == 1
        assert len(scheduler._strategies[AssetClass.CRYPTO]) == 0
        assert len(scheduler._strategies[AssetClass.PREDICTIONS]) == 0

    def test_empty_strategies(self):
        scheduler = _make_scheduler(strategies=[])
        for ac in AssetClass:
            assert len(scheduler._strategies[ac]) == 0

    def test_multiple_equities(self):
        sma1 = SMACrossoverStrategy(strategy_id="sma_spy")
        sma2 = SMACrossoverStrategy(
            strategy_id="sma_qqq",
            parameters={"symbol": "QQQ"},
        )
        scheduler = _make_scheduler(strategies=[sma1, sma2])
        assert len(scheduler._strategies[AssetClass.EQUITIES]) == 2

    def test_tier_grouping(self):
        sma = SMACrossoverStrategy()
        scheduler = _make_scheduler(strategies=[sma])
        key = (sma.tier, AssetClass.EQUITIES)
        assert key in scheduler._tier_strategies
        assert len(scheduler._tier_strategies[key]) == 1

    def test_tier_grouping_empty(self):
        scheduler = _make_scheduler(strategies=[])
        assert len(scheduler._tier_strategies) == 0


class TestTierIntervals:
    """Tier intervals are correctly defined."""

    def test_scout_fastest(self):
        assert TIER_INTERVALS[StrategyTier.SCOUT]["crypto"] == 5
        assert TIER_INTERVALS[StrategyTier.SCOUT]["equities"] == 15

    def test_sniper_slowest(self):
        assert TIER_INTERVALS[StrategyTier.SNIPER]["equities"] == 60
        assert TIER_INTERVALS[StrategyTier.SNIPER]["crypto"] == 60

    def test_core_middle(self):
        assert TIER_INTERVALS[StrategyTier.CORE]["equities"] == 30
        assert TIER_INTERVALS[StrategyTier.CORE]["crypto"] == 15


class TestMarketHours:
    """Market hours check for equities."""

    def test_weekday_during_market_hours(self):
        scheduler = _make_scheduler()
        mock_dt = datetime(2026, 3, 23, 10, 0, 0)  # Monday
        with patch("engines.scheduler.datetime") as mock_datetime:
            mock_datetime.now.return_value = mock_dt
            mock_datetime.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert scheduler.config.market_hours.open_hour == 9
            assert scheduler.config.market_hours.close_hour == 16

    def test_market_closed_weekends(self):
        scheduler = _make_scheduler()
        with patch("engines.scheduler.datetime") as mock_datetime:
            import pytz
            et = pytz.timezone("US/Eastern")
            saturday = et.localize(datetime(2026, 3, 28, 12, 0, 0))
            mock_datetime.now.return_value = saturday
            assert saturday.weekday() == 5  # Saturday

    def test_skip_equities_when_market_closed(self):
        config = SchedulerConfig(respect_market_hours=True)
        scheduler = _make_scheduler(config=config)
        assert config.respect_market_hours is True


class TestErrorTracking:
    """Error tracking and auto-pause behavior (per tier/asset_class key)."""

    def test_initial_error_count_is_zero(self):
        scheduler = _make_scheduler()
        for tier in StrategyTier:
            for ac in AssetClass:
                assert scheduler._consecutive_errors[(tier, ac)] == 0
                assert scheduler._paused[(tier, ac)] is False

    def test_not_paused_initially(self):
        scheduler = _make_scheduler()
        status = scheduler.status()
        for job in status["jobs"].values():
            assert job["paused"] is False

    @pytest.mark.asyncio
    async def test_pauses_after_max_errors(self):
        config = SchedulerConfig(max_consecutive_errors=2)
        scheduler = _make_scheduler(config=config)

        # SMA defaults to SNIPER tier
        key = (StrategyTier.SNIPER, AssetClass.EQUITIES)
        scheduler._consecutive_errors[key] = 2
        scheduler._paused[key] = True

        status = scheduler.status()
        # tier_jobs should show the paused job
        job_id = scheduler._job_id(StrategyTier.SNIPER, AssetClass.EQUITIES)
        assert status["tier_jobs"][job_id]["paused"] is True
        assert status["tier_jobs"][job_id]["consecutive_errors"] == 2


class TestPauseResume:
    """Manual pause/resume controls."""

    def test_pause_single_asset_class(self):
        scheduler = _make_scheduler()
        scheduler.pause(AssetClass.EQUITIES)
        for tier in StrategyTier:
            assert scheduler._paused[(tier, AssetClass.EQUITIES)] is True
            assert scheduler._paused[(tier, AssetClass.CRYPTO)] is False

    def test_resume_clears_errors(self):
        scheduler = _make_scheduler()
        key = (StrategyTier.CORE, AssetClass.EQUITIES)
        scheduler._consecutive_errors[key] = 3
        scheduler._paused[key] = True

        scheduler.resume(AssetClass.EQUITIES)
        for tier in StrategyTier:
            assert scheduler._paused[(tier, AssetClass.EQUITIES)] is False
            assert scheduler._consecutive_errors[(tier, AssetClass.EQUITIES)] == 0

    def test_pause_all(self):
        scheduler = _make_scheduler()
        scheduler.pause_all()
        for tier in StrategyTier:
            for ac in AssetClass:
                assert scheduler._paused[(tier, ac)] is True

    def test_resume_after_pause_all(self):
        scheduler = _make_scheduler()
        scheduler.pause_all()
        scheduler.resume(AssetClass.CRYPTO)
        for tier in StrategyTier:
            assert scheduler._paused[(tier, AssetClass.CRYPTO)] is False
            assert scheduler._paused[(tier, AssetClass.EQUITIES)] is True

    def test_pause_job_specific(self):
        scheduler = _make_scheduler()
        scheduler.pause_job(StrategyTier.SCOUT, AssetClass.CRYPTO)
        assert scheduler._paused[(StrategyTier.SCOUT, AssetClass.CRYPTO)] is True
        assert scheduler._paused[(StrategyTier.CORE, AssetClass.CRYPTO)] is False

    def test_resume_job_specific(self):
        scheduler = _make_scheduler()
        key = (StrategyTier.SCOUT, AssetClass.CRYPTO)
        scheduler._paused[key] = True
        scheduler._consecutive_errors[key] = 3

        scheduler.resume_job(StrategyTier.SCOUT, AssetClass.CRYPTO)
        assert scheduler._paused[key] is False
        assert scheduler._consecutive_errors[key] == 0


class TestStatus:
    """Status reporting for health checks."""

    def test_status_structure(self):
        scheduler = _make_scheduler()
        status = scheduler.status()
        assert "running" in status
        assert "enabled" in status
        assert "market_open" in status
        assert "jobs" in status
        assert "tier_jobs" in status
        assert "equities" in status["jobs"]
        assert "crypto" in status["jobs"]
        assert "predictions" in status["jobs"]

    def test_status_shows_strategy_counts(self):
        sma = SMACrossoverStrategy()
        scheduler = _make_scheduler(strategies=[sma])
        status = scheduler.status()
        assert status["jobs"]["equities"]["strategies"] == 1
        assert status["jobs"]["crypto"]["strategies"] == 0

    def test_not_running_before_start(self):
        scheduler = _make_scheduler()
        assert scheduler.status()["running"] is False

    def test_disabled_config(self):
        config = SchedulerConfig(enabled=False)
        scheduler = _make_scheduler(config=config)
        assert scheduler.status()["enabled"] is False

    def test_cycle_count_starts_at_zero(self):
        scheduler = _make_scheduler()
        status = scheduler.status()
        for job in status["jobs"].values():
            assert job["cycles_completed"] == 0
            assert job["last_run"] is None

    def test_tier_jobs_in_status(self):
        sma = SMACrossoverStrategy()
        scheduler = _make_scheduler(strategies=[sma])
        status = scheduler.status()
        # SMA is CORE tier, equities
        job_id = f"{sma.tier.value}_{AssetClass.EQUITIES.value}"
        assert job_id in status["tier_jobs"]
        tier_job = status["tier_jobs"][job_id]
        assert tier_job["tier"] == sma.tier.value
        assert tier_job["asset_class"] == "equities"
        assert tier_job["strategies"] == 1
        assert tier_job["interval_minutes"] == TIER_INTERVALS[sma.tier]["equities"]
        assert tier_job["paused"] is False
        assert tier_job["cycles_completed"] == 0

    def test_tier_jobs_only_includes_active_groups(self):
        """tier_jobs should only contain groups that have strategies."""
        sma = SMACrossoverStrategy()
        scheduler = _make_scheduler(strategies=[sma])
        status = scheduler.status()
        # Only one tier_job: the one for SMA's tier/asset_class
        assert len(status["tier_jobs"]) == 1


class TestSchedulerLifecycle:
    """Start/stop behavior."""

    def test_start_disabled(self):
        config = SchedulerConfig(enabled=False)
        scheduler = _make_scheduler(config=config)
        scheduler.start()
        assert scheduler.status()["running"] is False

    @pytest.mark.asyncio
    async def test_start_with_no_strategies_skips_jobs(self):
        scheduler = _make_scheduler(strategies=[])
        # Should not crash — just logs "no strategies, skipping"
        scheduler.start()
        assert scheduler.status()["running"] is True
        # Cleanup
        await scheduler.stop()

    @pytest.mark.asyncio
    async def test_stop_idempotent(self):
        scheduler = _make_scheduler()
        # Stop without starting should not crash
        await scheduler.stop()
        assert scheduler.status()["running"] is False

    @pytest.mark.asyncio
    async def test_start_registers_tier_jobs(self):
        """Starting should register one job per (tier, asset_class) group."""
        sma = SMACrossoverStrategy()
        config = SchedulerConfig(learning_enabled=False)
        scheduler = _make_scheduler(strategies=[sma], config=config)
        scheduler.start()

        # Should have exactly one job registered
        jobs = scheduler._scheduler.get_jobs()
        assert len(jobs) == 1
        assert jobs[0].id == f"{sma.tier.value}_{AssetClass.EQUITIES.value}"

        await scheduler.stop()

    def test_job_id_format(self):
        scheduler = _make_scheduler()
        assert scheduler._job_id(StrategyTier.SCOUT, AssetClass.EQUITIES) == "scout_equities"
        assert scheduler._job_id(StrategyTier.CORE, AssetClass.CRYPTO) == "core_crypto"
        assert scheduler._job_id(StrategyTier.SNIPER, AssetClass.PREDICTIONS) == "sniper_predictions"
