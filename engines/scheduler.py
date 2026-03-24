"""
Trading Scheduler — Runs strategies on cron via APScheduler.

Schedules:
- Equities: every 15 min during market hours (9:30–16:00 ET)
- Crypto: every 5 min (24/7)
- Predictions: every 10 min (24/7)

Each job gets its own DB session and runs the pipeline for
strategies in that asset class. Errors are logged and alerted
but never crash the scheduler.
"""

import logging
from datetime import datetime

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from config.scheduler_config import SchedulerConfig
from data.database import async_session_factory
from engines.alerts import alert_system_error, send_alert, AlertLevel
from engines.execution.base import Executor
from engines.models import AssetClass, MarketRegime
from engines.pipeline import TradingPipeline
from engines.risk.engine import RiskEngine
from engines.strategy.base import Strategy

logger = logging.getLogger(__name__)


class TradingScheduler:
    """
    Orchestrates scheduled trading cycles across asset classes.

    Each asset class gets its own APScheduler job at a configured interval.
    The scheduler tracks errors per job and pauses after too many consecutive
    failures to prevent runaway issues.
    """

    def __init__(
        self,
        risk_engine: RiskEngine,
        executor: Executor,
        strategies: list[Strategy],
        config: SchedulerConfig | None = None,
    ):
        self.risk_engine = risk_engine
        self.executor = executor
        self.config = config or SchedulerConfig()

        # Group strategies by asset class
        self._strategies: dict[AssetClass, list[Strategy]] = {
            AssetClass.EQUITIES: [],
            AssetClass.CRYPTO: [],
            AssetClass.PREDICTIONS: [],
        }
        for s in strategies:
            self._strategies[s.asset_class].append(s)

        # Error tracking per asset class
        self._consecutive_errors: dict[AssetClass, int] = {
            ac: 0 for ac in AssetClass
        }
        self._paused: dict[AssetClass, bool] = {
            ac: False for ac in AssetClass
        }

        # Cycle counters for observability
        self._cycle_counts: dict[AssetClass, int] = {
            ac: 0 for ac in AssetClass
        }
        self._last_run: dict[AssetClass, datetime | None] = {
            ac: None for ac in AssetClass
        }

        self._scheduler = AsyncIOScheduler()
        self._running = False

    def start(self) -> None:
        """Start the scheduler and register all jobs."""
        if not self.config.enabled:
            logger.info("Scheduler disabled by config — not starting")
            return

        intervals = {
            AssetClass.EQUITIES: self.config.equities_interval_minutes,
            AssetClass.CRYPTO: self.config.crypto_interval_minutes,
            AssetClass.PREDICTIONS: self.config.predictions_interval_minutes,
        }

        for asset_class, interval in intervals.items():
            strategies = self._strategies[asset_class]
            if not strategies:
                logger.info(
                    "No strategies for %s — skipping job", asset_class.value
                )
                continue

            self._scheduler.add_job(
                self._run_cycle,
                trigger=IntervalTrigger(minutes=interval),
                args=[asset_class],
                id=f"cycle_{asset_class.value}",
                name=f"{asset_class.value} trading cycle",
                max_instances=1,  # Never overlap
                misfire_grace_time=60,
            )
            logger.info(
                "Scheduled %s cycle: every %d min (%d strategies)",
                asset_class.value,
                interval,
                len(strategies),
            )

        # --- Learning Engine jobs ---
        if self.config.learning_enabled:
            et = pytz.timezone(self.config.market_hours.timezone)

            # Fast loop: daily after market close
            self._scheduler.add_job(
                self._run_fast_loop,
                trigger=CronTrigger(
                    hour=self.config.fast_loop_hour,
                    minute=self.config.fast_loop_minute,
                    timezone=et,
                ),
                id="learning_fast_loop",
                name="Daily learning fast loop",
                max_instances=1,
                misfire_grace_time=300,
            )
            logger.info(
                "Scheduled fast loop: daily at %02d:%02d ET",
                self.config.fast_loop_hour,
                self.config.fast_loop_minute,
            )

            # Slow loop: weekly
            self._scheduler.add_job(
                self._run_slow_loop,
                trigger=CronTrigger(
                    day_of_week=self.config.slow_loop_day,
                    hour=self.config.slow_loop_hour,
                    minute=self.config.slow_loop_minute,
                    timezone=et,
                ),
                id="learning_slow_loop",
                name="Weekly learning slow loop",
                max_instances=1,
                misfire_grace_time=600,
            )
            logger.info(
                "Scheduled slow loop: %s at %02d:%02d ET",
                self.config.slow_loop_day,
                self.config.slow_loop_hour,
                self.config.slow_loop_minute,
            )

        self._scheduler.start()
        self._running = True
        logger.info("Trading scheduler started")

    async def stop(self) -> None:
        """Gracefully stop the scheduler."""
        if self._running:
            self._scheduler.shutdown(wait=True)
            self._running = False
            logger.info("Trading scheduler stopped")

    async def _run_cycle(self, asset_class: AssetClass) -> None:
        """
        Run one trading cycle for an asset class.

        Creates a fresh DB session, checks pre-conditions (market hours,
        pause state), runs the pipeline, and handles errors.
        """
        # Check if paused due to errors
        if self._paused[asset_class]:
            logger.warning(
                "Skipping %s cycle — paused after %d consecutive errors",
                asset_class.value,
                self.config.max_consecutive_errors,
            )
            return

        # Check market hours for equities
        if (
            asset_class == AssetClass.EQUITIES
            and self.config.respect_market_hours
            and not self._is_market_open()
        ):
            logger.debug("Skipping equities cycle — market closed")
            return

        strategies = self._strategies[asset_class]
        self._cycle_counts[asset_class] += 1
        cycle_num = self._cycle_counts[asset_class]

        logger.info(
            "[%s] Starting cycle #%d (%d strategies)",
            asset_class.value,
            cycle_num,
            len(strategies),
        )

        try:
            async with async_session_factory() as session:
                pipeline = TradingPipeline(
                    risk_engine=self.risk_engine,
                    executor=self.executor,
                    strategies=strategies,
                    db_session=session,
                )

                # TODO: replace with real regime classification
                regime = MarketRegime.UNKNOWN

                results = await pipeline.run_cycle(market_regime=regime)

                executed = sum(1 for r in results if r.executed)
                failed = sum(1 for r in results if not r.executed)

                logger.info(
                    "[%s] Cycle #%d complete: %d executed, %d failed",
                    asset_class.value,
                    cycle_num,
                    executed,
                    failed,
                )

            # Reset error counter on success
            self._consecutive_errors[asset_class] = 0
            self._last_run[asset_class] = datetime.utcnow()

        except Exception as e:
            self._consecutive_errors[asset_class] += 1
            error_count = self._consecutive_errors[asset_class]

            logger.error(
                "[%s] Cycle #%d FAILED (error %d/%d): %s",
                asset_class.value,
                cycle_num,
                error_count,
                self.config.max_consecutive_errors,
                e,
                exc_info=True,
            )

            await alert_system_error(
                error=f"Cycle error ({error_count}/{self.config.max_consecutive_errors}): {e}",
                component=f"Scheduler/{asset_class.value}",
            )

            # Pause after too many consecutive errors
            if error_count >= self.config.max_consecutive_errors:
                self._paused[asset_class] = True
                logger.critical(
                    "[%s] PAUSED — %d consecutive errors",
                    asset_class.value,
                    error_count,
                )
                await send_alert(
                    title=f"Scheduler Paused: {asset_class.value}",
                    message=(
                        f"{asset_class.value} trading paused after "
                        f"{error_count} consecutive errors. "
                        f"Last error: {e}"
                    ),
                    level=AlertLevel.CRITICAL,
                )

    async def _run_fast_loop(self) -> None:
        """Run the daily learning fast loop."""
        logger.info("Starting daily fast loop")
        try:
            from engines.learning.fast_loop import FastLoop

            async with async_session_factory() as session:
                loop = FastLoop(session)
                result = await loop.run()
                logger.info(
                    "Fast loop complete: %d strategies",
                    len(result.get("strategies", {})),
                )
        except Exception as e:
            logger.error("Fast loop failed: %s", e, exc_info=True)
            await alert_system_error(
                error=f"Daily fast loop failed: {e}",
                component="LearningEngine/FastLoop",
            )

    async def _run_slow_loop(self) -> None:
        """Run the weekly learning slow loop."""
        logger.info("Starting weekly slow loop")
        try:
            from engines.learning.slow_loop import SlowLoop

            async with async_session_factory() as session:
                # Get current portfolio from executor
                portfolio = await self.executor.get_portfolio_snapshot()

                loop = SlowLoop(session)
                result = await loop.run(portfolio, period_days=7)
                logger.info(
                    "Slow loop complete: %d hypotheses, %d recommendations",
                    len(result.get("hypotheses", [])),
                    len(result.get("recommendations", [])),
                )
        except Exception as e:
            logger.error("Slow loop failed: %s", e, exc_info=True)
            await alert_system_error(
                error=f"Weekly slow loop failed: {e}",
                component="LearningEngine/SlowLoop",
            )

    def _is_market_open(self) -> bool:
        """Check if US equity market is currently open."""
        mh = self.config.market_hours
        et = pytz.timezone(mh.timezone)
        now = datetime.now(et)

        # Weekends
        if now.weekday() >= 5:
            return False

        market_open = now.replace(
            hour=mh.open_hour, minute=mh.open_minute, second=0, microsecond=0
        )
        market_close = now.replace(
            hour=mh.close_hour, minute=mh.close_minute, second=0, microsecond=0
        )

        return market_open <= now <= market_close

    def resume(self, asset_class: AssetClass) -> None:
        """Resume a paused asset class after fixing issues."""
        self._paused[asset_class] = False
        self._consecutive_errors[asset_class] = 0
        logger.info("Resumed %s trading cycles", asset_class.value)

    def pause(self, asset_class: AssetClass) -> None:
        """Manually pause an asset class."""
        self._paused[asset_class] = True
        logger.info("Manually paused %s trading cycles", asset_class.value)

    def pause_all(self) -> None:
        """Pause all asset classes (emergency stop)."""
        for ac in AssetClass:
            self._paused[ac] = True
        logger.critical("ALL trading cycles paused")

    def status(self) -> dict:
        """Return scheduler status for health check endpoint."""
        jobs = {}
        for asset_class in AssetClass:
            strategies = self._strategies[asset_class]
            jobs[asset_class.value] = {
                "strategies": len(strategies),
                "paused": self._paused[asset_class],
                "consecutive_errors": self._consecutive_errors[asset_class],
                "cycles_completed": self._cycle_counts[asset_class],
                "last_run": (
                    self._last_run[asset_class].isoformat()
                    if self._last_run[asset_class]
                    else None
                ),
            }

        return {
            "running": self._running,
            "enabled": self.config.enabled,
            "market_open": self._is_market_open(),
            "jobs": jobs,
            "learning": {
                "enabled": self.config.learning_enabled,
                "fast_loop": f"daily at {self.config.fast_loop_hour:02d}:{self.config.fast_loop_minute:02d} ET",
                "slow_loop": f"{self.config.slow_loop_day} at {self.config.slow_loop_hour:02d}:{self.config.slow_loop_minute:02d} ET",
            },
        }
