"""
Unit tests for error recovery and graceful degradation.
"""

import pytest
from unittest.mock import AsyncMock

from engines.recovery import (
    ComponentStatus,
    ComponentHealth,
    HealthMonitor,
    GracefulDegradation,
)


class TestComponentHealth:

    def test_initial_state(self):
        comp = ComponentHealth(name="test")
        assert comp.status == ComponentStatus.HEALTHY
        assert comp.consecutive_failures == 0

    def test_backoff_exponential(self):
        comp = ComponentHealth(name="test", recovery_backoff_seconds=10.0)
        comp.consecutive_failures = 0
        assert comp.backoff_time == 10.0
        comp.consecutive_failures = 1
        assert comp.backoff_time == 20.0
        comp.consecutive_failures = 2
        assert comp.backoff_time == 40.0

    def test_backoff_capped(self):
        comp = ComponentHealth(
            name="test",
            recovery_backoff_seconds=10.0,
            max_backoff_seconds=100.0,
        )
        comp.consecutive_failures = 10
        assert comp.backoff_time <= 100.0

    def test_should_retry_when_healthy(self):
        comp = ComponentHealth(name="test")
        assert comp.should_retry is False  # Healthy, no need to retry


class TestHealthMonitor:

    async def test_register_component(self):
        monitor = HealthMonitor()
        monitor.register("database")
        assert monitor.get_status("database") == ComponentStatus.HEALTHY

    async def test_unknown_component(self):
        monitor = HealthMonitor()
        assert monitor.get_status("nonexistent") == ComponentStatus.DOWN

    async def test_record_success(self):
        monitor = HealthMonitor()
        monitor.register("database")
        await monitor.record_success("database")
        assert monitor.is_healthy("database")

    async def test_single_failure_degrades(self):
        monitor = HealthMonitor()
        monitor.register("database")
        await monitor.record_failure("database", "connection refused")
        assert monitor.get_status("database") == ComponentStatus.DEGRADED

    async def test_multiple_failures_down(self):
        monitor = HealthMonitor()
        monitor.register("database", max_failures=3)
        for _ in range(3):
            await monitor.record_failure("database", "timeout")
        assert monitor.get_status("database") == ComponentStatus.DOWN

    async def test_recovery_after_down(self):
        monitor = HealthMonitor()
        monitor.register("database", max_failures=2)
        await monitor.record_failure("database", "err")
        await monitor.record_failure("database", "err")
        assert monitor.get_status("database") == ComponentStatus.DOWN

        await monitor.record_success("database")
        assert monitor.get_status("database") == ComponentStatus.HEALTHY

    async def test_should_skip_when_in_backoff(self):
        monitor = HealthMonitor()
        monitor.register("alpaca", max_failures=1, backoff_seconds=9999)
        await monitor.record_failure("alpaca", "err")
        assert monitor.should_skip("alpaca")

    async def test_should_not_skip_healthy(self):
        monitor = HealthMonitor()
        monitor.register("database")
        assert not monitor.should_skip("database")

    async def test_system_status_structure(self):
        monitor = HealthMonitor()
        monitor.register("database")
        monitor.register("alpaca")
        await monitor.record_success("database")
        await monitor.record_failure("alpaca", "timeout")

        status = monitor.system_status()
        assert status["overall"] == "degraded"
        assert status["healthy"] == 1
        assert status["total"] == 2
        assert "database" in status["components"]
        assert "alpaca" in status["components"]

    async def test_all_healthy(self):
        monitor = HealthMonitor()
        monitor.register("a")
        monitor.register("b")
        await monitor.record_success("a")
        await monitor.record_success("b")
        assert monitor.system_status()["overall"] == "healthy"

    async def test_all_down(self):
        monitor = HealthMonitor()
        monitor.register("a", max_failures=1)
        monitor.register("b", max_failures=1)
        await monitor.record_failure("a", "err")
        await monitor.record_failure("b", "err")
        assert monitor.system_status()["overall"] == "down"

    async def test_events_logged(self):
        monitor = HealthMonitor()
        monitor.register("db", max_failures=1)
        await monitor.record_failure("db", "timeout")
        status = monitor.system_status()
        assert len(status["recent_events"]) >= 1


class TestGracefulDegradation:

    async def test_success_returns_result(self):
        monitor = HealthMonitor()
        monitor.register("database")
        degradation = GracefulDegradation(monitor)

        result = await degradation.execute_with_fallback(
            "database",
            AsyncMock(return_value=42),
            fallback=-1,
        )
        assert result == 42

    async def test_failure_returns_fallback(self):
        monitor = HealthMonitor()
        monitor.register("database")
        degradation = GracefulDegradation(monitor)

        result = await degradation.execute_with_fallback(
            "database",
            AsyncMock(side_effect=Exception("connection refused")),
            fallback=-1,
        )
        assert result == -1

    async def test_cached_value_on_failure(self):
        monitor = HealthMonitor()
        monitor.register("database")
        degradation = GracefulDegradation(monitor)

        # First call succeeds and caches
        result1 = await degradation.execute_with_fallback(
            "database",
            AsyncMock(return_value={"value": 100}),
            cache_key="portfolio",
        )
        assert result1 == {"value": 100}

        # Second call fails — returns cached
        result2 = await degradation.execute_with_fallback(
            "database",
            AsyncMock(side_effect=Exception("down")),
            cache_key="portfolio",
            fallback=None,
        )
        assert result2 == {"value": 100}

    async def test_skip_when_in_backoff(self):
        monitor = HealthMonitor()
        monitor.register("api", max_failures=1, backoff_seconds=9999)
        await monitor.record_failure("api", "err")
        degradation = GracefulDegradation(monitor)

        op = AsyncMock(return_value="should not be called")
        result = await degradation.execute_with_fallback(
            "api", op, fallback="default"
        )
        assert result == "default"
        op.assert_not_called()

    async def test_fallback_used_when_no_cache(self):
        monitor = HealthMonitor()
        monitor.register("db")
        degradation = GracefulDegradation(monitor)

        result = await degradation.execute_with_fallback(
            "db",
            AsyncMock(side_effect=Exception("err")),
            fallback={"empty": True},
            cache_key="missing_key",
        )
        assert result == {"empty": True}
