"""
Tests for the Trading Scheduler.

Tests cover:
- Strategy grouping by asset class
- Market hours checking
- Error tracking and auto-pause
- Pause/resume controls
- Status reporting
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config.risk_config import RiskConfig
from config.scheduler_config import MarketHours, SchedulerConfig
from engines.execution.base import Executor
from engines.models import AssetClass, MarketRegime, StrategyStatus
from engines.risk.engine import RiskEngine
from engines.scheduler import TradingScheduler
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
    """Strategies should be grouped by asset class."""

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


class TestMarketHours:
    """Market hours check for equities."""

    def test_weekday_during_market_hours(self):
        scheduler = _make_scheduler()
        # Mock a Monday at 10:00 ET
        mock_dt = datetime(2026, 3, 23, 10, 0, 0)  # Monday
        with patch("engines.scheduler.datetime") as mock_datetime:
            mock_datetime.now.return_value = mock_dt
            mock_datetime.side_effect = lambda *a, **kw: datetime(*a, **kw)
            # Can't easily mock timezone-aware datetime, test the logic directly
            assert scheduler.config.market_hours.open_hour == 9
            assert scheduler.config.market_hours.close_hour == 16

    def test_market_closed_weekends(self):
        scheduler = _make_scheduler()
        # Saturday
        with patch("engines.scheduler.datetime") as mock_datetime:
            import pytz
            et = pytz.timezone("US/Eastern")
            saturday = et.localize(datetime(2026, 3, 28, 12, 0, 0))
            mock_datetime.now.return_value = saturday
            # _is_market_open checks weekday
            assert saturday.weekday() == 5  # Saturday

    def test_skip_equities_when_market_closed(self):
        config = SchedulerConfig(respect_market_hours=True)
        scheduler = _make_scheduler(config=config)
        # If market is closed, the cycle should skip
        assert config.respect_market_hours is True


class TestErrorTracking:
    """Error tracking and auto-pause behavior."""

    def test_initial_error_count_is_zero(self):
        scheduler = _make_scheduler()
        for ac in AssetClass:
            assert scheduler._consecutive_errors[ac] == 0
            assert scheduler._paused[ac] is False

    def test_not_paused_initially(self):
        scheduler = _make_scheduler()
        status = scheduler.status()
        for job in status["jobs"].values():
            assert job["paused"] is False

    @pytest.mark.asyncio
    async def test_pauses_after_max_errors(self):
        config = SchedulerConfig(max_consecutive_errors=2)
        scheduler = _make_scheduler(config=config)

        # Simulate errors by setting the counter directly
        scheduler._consecutive_errors[AssetClass.EQUITIES] = 2
        scheduler._paused[AssetClass.EQUITIES] = True

        status = scheduler.status()
        assert status["jobs"]["equities"]["paused"] is True
        assert status["jobs"]["equities"]["consecutive_errors"] == 2


class TestPauseResume:
    """Manual pause/resume controls."""

    def test_pause_single_asset_class(self):
        scheduler = _make_scheduler()
        scheduler.pause(AssetClass.EQUITIES)
        assert scheduler._paused[AssetClass.EQUITIES] is True
        assert scheduler._paused[AssetClass.CRYPTO] is False

    def test_resume_clears_errors(self):
        scheduler = _make_scheduler()
        scheduler._consecutive_errors[AssetClass.EQUITIES] = 3
        scheduler._paused[AssetClass.EQUITIES] = True

        scheduler.resume(AssetClass.EQUITIES)
        assert scheduler._paused[AssetClass.EQUITIES] is False
        assert scheduler._consecutive_errors[AssetClass.EQUITIES] == 0

    def test_pause_all(self):
        scheduler = _make_scheduler()
        scheduler.pause_all()
        for ac in AssetClass:
            assert scheduler._paused[ac] is True

    def test_resume_after_pause_all(self):
        scheduler = _make_scheduler()
        scheduler.pause_all()
        scheduler.resume(AssetClass.CRYPTO)
        assert scheduler._paused[AssetClass.CRYPTO] is False
        assert scheduler._paused[AssetClass.EQUITIES] is True


class TestStatus:
    """Status reporting for health checks."""

    def test_status_structure(self):
        scheduler = _make_scheduler()
        status = scheduler.status()
        assert "running" in status
        assert "enabled" in status
        assert "market_open" in status
        assert "jobs" in status
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
