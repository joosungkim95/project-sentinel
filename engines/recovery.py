"""
Error Recovery and Graceful Degradation.

Monitors system health and automatically handles failures:
1. Platform adapter failures — disable adapter, continue with others
2. Database outages — queue writes, trade from cache
3. Scheduler errors — auto-restart after backoff
4. API failures — return degraded responses instead of 500s

Design principle: Sentinel should keep running in a degraded state
rather than crash entirely. Better to trade on 2 of 3 platforms
than to halt everything because one API is down.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional

from engines.alerts import send_alert, AlertLevel

logger = logging.getLogger(__name__)


class ComponentStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DOWN = "down"
    RECOVERING = "recovering"


@dataclass
class ComponentHealth:
    """Health state for a single system component."""
    name: str
    status: ComponentStatus = ComponentStatus.HEALTHY
    last_check: Optional[datetime] = None
    last_error: Optional[str] = None
    consecutive_failures: int = 0
    total_failures: int = 0
    last_recovery: Optional[datetime] = None

    # Recovery settings
    max_failures_before_down: int = 3
    recovery_backoff_seconds: float = 30.0
    max_backoff_seconds: float = 300.0

    @property
    def backoff_time(self) -> float:
        """Exponential backoff based on failure count."""
        backoff = self.recovery_backoff_seconds * (2 ** min(self.consecutive_failures, 5))
        return min(backoff, self.max_backoff_seconds)

    @property
    def should_retry(self) -> bool:
        """Whether enough time has passed to retry a down component."""
        if self.status not in (ComponentStatus.DOWN, ComponentStatus.DEGRADED):
            return False
        if self.last_check is None:
            return True
        elapsed = (datetime.now(timezone.utc) - self.last_check).total_seconds()
        return elapsed >= self.backoff_time


@dataclass
class RecoveryEvent:
    """Log of a recovery action."""
    timestamp: datetime
    component: str
    event_type: str  # "failure", "recovery", "degraded", "down"
    details: str


class HealthMonitor:
    """
    Monitors and manages health of all system components.

    Tracks: platform adapters, database, scheduler, Redis cache.
    Automatically degrades gracefully and attempts recovery.
    """

    def __init__(self):
        self._components: dict[str, ComponentHealth] = {}
        self._events: list[RecoveryEvent] = []
        self._max_events = 100

    def register(
        self,
        name: str,
        max_failures: int = 3,
        backoff_seconds: float = 30.0,
    ) -> None:
        """Register a component for health monitoring."""
        self._components[name] = ComponentHealth(
            name=name,
            max_failures_before_down=max_failures,
            recovery_backoff_seconds=backoff_seconds,
        )

    async def record_success(self, name: str) -> None:
        """Record a successful health check or operation."""
        comp = self._components.get(name)
        if not comp:
            return

        was_down = comp.status in (ComponentStatus.DOWN, ComponentStatus.DEGRADED)
        comp.status = ComponentStatus.HEALTHY
        comp.consecutive_failures = 0
        comp.last_check = datetime.now(timezone.utc)
        comp.last_error = None

        if was_down:
            comp.last_recovery = datetime.now(timezone.utc)
            self._log_event(name, "recovery", f"{name} recovered")
            await send_alert(
                title=f"Recovered: {name}",
                message=f"{name} is healthy again after {comp.total_failures} total failures.",
                level=AlertLevel.INFO,
            )

    async def record_failure(self, name: str, error: str) -> None:
        """Record a failed health check or operation."""
        comp = self._components.get(name)
        if not comp:
            return

        comp.consecutive_failures += 1
        comp.total_failures += 1
        comp.last_check = datetime.now(timezone.utc)
        comp.last_error = error

        if comp.consecutive_failures >= comp.max_failures_before_down:
            if comp.status != ComponentStatus.DOWN:
                comp.status = ComponentStatus.DOWN
                self._log_event(name, "down", f"{name} is DOWN: {error}")
                await send_alert(
                    title=f"DOWN: {name}",
                    message=f"{name} has failed {comp.consecutive_failures} times. Last error: {error}",
                    level=AlertLevel.CRITICAL,
                )
        elif comp.consecutive_failures >= 1:
            comp.status = ComponentStatus.DEGRADED
            self._log_event(name, "degraded", f"{name} degraded: {error}")

    def get_status(self, name: str) -> ComponentStatus:
        """Get current status of a component."""
        comp = self._components.get(name)
        return comp.status if comp else ComponentStatus.DOWN

    def is_healthy(self, name: str) -> bool:
        """Check if a component is healthy or recovering."""
        status = self.get_status(name)
        return status in (ComponentStatus.HEALTHY, ComponentStatus.RECOVERING)

    def should_skip(self, name: str) -> bool:
        """Check if a component should be skipped (down, not ready to retry)."""
        comp = self._components.get(name)
        if not comp:
            return True
        if comp.status == ComponentStatus.HEALTHY:
            return False
        if comp.status == ComponentStatus.DOWN and not comp.should_retry:
            return True  # Still in backoff
        return False

    def system_status(self) -> dict[str, Any]:
        """Return full system health status."""
        components = {}
        for name, comp in self._components.items():
            components[name] = {
                "status": comp.status.value,
                "consecutive_failures": comp.consecutive_failures,
                "total_failures": comp.total_failures,
                "last_error": comp.last_error,
                "last_check": comp.last_check.isoformat() if comp.last_check else None,
                "backoff_seconds": comp.backoff_time if comp.status == ComponentStatus.DOWN else 0,
            }

        healthy_count = sum(
            1 for c in self._components.values()
            if c.status == ComponentStatus.HEALTHY
        )
        total = len(self._components)

        if healthy_count == total:
            overall = "healthy"
        elif healthy_count == 0:
            overall = "down"
        else:
            overall = "degraded"

        return {
            "overall": overall,
            "healthy": healthy_count,
            "total": total,
            "components": components,
            "recent_events": [
                {
                    "time": e.timestamp.isoformat(),
                    "component": e.component,
                    "type": e.event_type,
                    "details": e.details,
                }
                for e in self._events[-10:]
            ],
        }

    def _log_event(self, component: str, event_type: str, details: str) -> None:
        """Log a recovery event."""
        self._events.append(RecoveryEvent(
            timestamp=datetime.now(timezone.utc),
            component=component,
            event_type=event_type,
            details=details,
        ))
        if len(self._events) > self._max_events:
            self._events = self._events[-self._max_events:]
        logger.info("Recovery event [%s] %s: %s", event_type, component, details)


class GracefulDegradation:
    """
    Wraps operations with graceful degradation.

    If a component is down, operations that depend on it
    return cached/default values instead of crashing.
    """

    def __init__(self, monitor: HealthMonitor):
        self.monitor = monitor
        self._cache: dict[str, Any] = {}

    async def execute_with_fallback(
        self,
        component: str,
        operation: Any,
        fallback: Any = None,
        cache_key: str | None = None,
    ) -> Any:
        """
        Execute an operation with fallback on failure.

        Args:
            component: Name of the component this depends on.
            operation: Async callable to execute.
            fallback: Value to return on failure.
            cache_key: If set, cache successful results and return cached on failure.

        Returns:
            Operation result, cached value, or fallback.
        """
        if self.monitor.should_skip(component):
            logger.debug("Skipping %s (component down, in backoff)", component)
            if cache_key and cache_key in self._cache:
                return self._cache[cache_key]
            return fallback

        try:
            result = await operation()
            await self.monitor.record_success(component)
            if cache_key:
                self._cache[cache_key] = result
            return result
        except Exception as e:
            await self.monitor.record_failure(component, str(e))
            if cache_key and cache_key in self._cache:
                logger.info(
                    "Using cached value for %s (component %s failed)",
                    cache_key, component,
                )
                return self._cache[cache_key]
            return fallback
