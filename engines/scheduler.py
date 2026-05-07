"""
Trading Scheduler — Runs strategies on cron via APScheduler.

Schedules are tier-based:
- Scout: fast scanning (equities 15min, crypto 5min, predictions 10min)
- Core: medium frequency (equities 30min, crypto 15min, predictions 10min)
- Sniper: slow, high-conviction (equities 60min, crypto 60min, predictions 30min)

Equities tiers only run during market hours (9:30–16:00 ET).
Learning engine jobs (fast loop, slow loop) are unchanged.

Each job gets its own DB session and runs the pipeline for
strategies in that (tier, asset_class) group. Errors are logged
and alerted but never crash the scheduler.
"""

import logging
from datetime import datetime
from typing import Any

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from config.scheduler_config import SchedulerConfig
from config.tiers import StrategyTier
from data.database import async_session_factory
from data.repositories.portfolio import insert_portfolio_snapshot
from engines.alerts import alert_system_error, send_alert, AlertLevel
from engines.execution.base import Executor
from engines.models import AssetClass, MarketRegime
from engines.pipeline import TradingPipeline
from engines.risk.engine import RiskEngine
from engines.strategy.base import Strategy
from memory.market_regime import MarketRegimeTracker

logger = logging.getLogger(__name__)

# Interval in minutes per (tier, asset_class) combination
TIER_INTERVALS: dict[StrategyTier, dict[str, int]] = {
    StrategyTier.SCOUT: {"equities": 15, "crypto": 5, "predictions": 10},
    StrategyTier.CORE: {"equities": 30, "crypto": 15, "predictions": 10},
    StrategyTier.SNIPER: {"equities": 60, "crypto": 60, "predictions": 30},
}


class TradingScheduler:
    """
    Orchestrates scheduled trading cycles across tiers and asset classes.

    Each (tier, asset_class) pair with strategies gets its own APScheduler
    job at a tier-specific interval. The scheduler tracks errors per job
    and pauses after too many consecutive failures to prevent runaway issues.
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

        # Group strategies by asset class (kept for backward compat)
        self._strategies: dict[AssetClass, list[Strategy]] = {
            AssetClass.EQUITIES: [],
            AssetClass.CRYPTO: [],
            AssetClass.PREDICTIONS: [],
        }
        for s in strategies:
            self._strategies[s.asset_class].append(s)

        # Group strategies by (tier, asset_class) for tier-based scheduling
        self._tier_strategies: dict[
            tuple[StrategyTier, AssetClass], list[Strategy]
        ] = {}
        for s in strategies:
            key = (s.tier, s.asset_class)
            self._tier_strategies.setdefault(key, []).append(s)

        # Job keys for all possible (tier, asset_class) combos
        self._all_job_keys: list[tuple[StrategyTier, AssetClass]] = [
            (tier, ac)
            for tier in StrategyTier
            for ac in AssetClass
        ]

        # Error tracking per job key (tier, asset_class)
        self._consecutive_errors: dict[tuple[StrategyTier, AssetClass], int] = {
            k: 0 for k in self._all_job_keys
        }
        self._paused: dict[tuple[StrategyTier, AssetClass], bool] = {
            k: False for k in self._all_job_keys
        }

        # Cycle counters for observability
        self._cycle_counts: dict[tuple[StrategyTier, AssetClass], int] = {
            k: 0 for k in self._all_job_keys
        }
        self._last_run: dict[
            tuple[StrategyTier, AssetClass], datetime | None
        ] = {k: None for k in self._all_job_keys}

        self._scheduler = AsyncIOScheduler()
        self._running = False

    def _job_id(self, tier: StrategyTier, asset_class: AssetClass) -> str:
        """Build a job ID string for a (tier, asset_class) pair."""
        return f"{tier.value}_{asset_class.value}"

    def start(self) -> None:
        """Start the scheduler and register tier-based jobs."""
        if not self.config.enabled:
            logger.info("Scheduler disabled by config — not starting")
            return

        for (tier, asset_class), strategies in self._tier_strategies.items():
            if not strategies:
                continue

            interval = TIER_INTERVALS[tier][asset_class.value]
            job_id = self._job_id(tier, asset_class)

            self._scheduler.add_job(
                self._run_tier_cycle,
                trigger=IntervalTrigger(minutes=interval),
                args=[tier, asset_class.value, strategies],
                id=job_id,
                name=f"{tier.value}/{asset_class.value} trading cycle",
                max_instances=1,  # Never overlap
                misfire_grace_time=60,
            )
            logger.info(
                "Scheduled %s/%s cycle: every %d min (%d strategies)",
                tier.value,
                asset_class.value,
                interval,
                len(strategies),
            )

        # --- Portfolio snapshot persistence ---
        self._scheduler.add_job(
            self._persist_portfolio_snapshot,
            trigger=IntervalTrigger(minutes=5),
            id="portfolio_snapshot",
            name="Portfolio snapshot persistence",
            max_instances=1,
            misfire_grace_time=60,
        )
        logger.info("Scheduled portfolio snapshot: every 5 min")

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

    async def _run_tier_cycle(
        self,
        tier: StrategyTier,
        asset_class_str: str,
        strategies: list[Strategy],
    ) -> None:
        """
        Run one trading cycle for a (tier, asset_class) group.

        Creates a fresh DB session, checks pre-conditions (market hours,
        pause state), runs the tier pipeline, and handles errors.

        Args:
            tier: Strategy tier (scout, core, sniper).
            asset_class_str: Asset class value string.
            strategies: Strategies in this group.
        """
        asset_class = AssetClass(asset_class_str)
        job_key = (tier, asset_class)
        job_label = f"{tier.value}/{asset_class_str}"

        # Check if paused due to errors
        if self._paused[job_key]:
            logger.warning(
                "Skipping %s cycle — paused after %d consecutive errors",
                job_label,
                self.config.max_consecutive_errors,
            )
            return

        # Check market hours for equities
        if (
            asset_class == AssetClass.EQUITIES
            and self.config.respect_market_hours
            and not self._is_market_open()
        ):
            logger.debug("Skipping %s cycle — market closed", job_label)
            return

        self._cycle_counts[job_key] += 1
        cycle_num = self._cycle_counts[job_key]

        logger.info(
            "[%s] Starting cycle #%d (%d strategies)",
            job_label,
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

                # Read persisted regime from DB (set by fast loop or inline)
                tracker = MarketRegimeTracker(session)
                regime = await tracker.get_current_regime(asset_class)
                if regime != MarketRegime.UNKNOWN:
                    logger.debug(
                        "[%s] Using persisted regime: %s", job_label, regime.value,
                    )

                results = await pipeline.run_tier(
                    tier=tier,
                    asset_class_str=asset_class_str,
                    strategies=strategies,
                    market_regime=regime,
                )

                executed = sum(1 for r in results if r.executed)
                failed = sum(1 for r in results if not r.executed)

                logger.info(
                    "[%s] Cycle #%d complete: %d executed, %d failed",
                    job_label,
                    cycle_num,
                    executed,
                    failed,
                )

            # Reset error counter on success
            self._consecutive_errors[job_key] = 0
            self._last_run[job_key] = datetime.utcnow()

        except Exception as e:
            self._consecutive_errors[job_key] += 1
            error_count = self._consecutive_errors[job_key]

            logger.error(
                "[%s] Cycle #%d FAILED (error %d/%d): %s",
                job_label,
                cycle_num,
                error_count,
                self.config.max_consecutive_errors,
                e,
                exc_info=True,
            )

            await alert_system_error(
                error=f"Cycle error ({error_count}/{self.config.max_consecutive_errors}): {e}",
                component=f"Scheduler/{job_label}",
            )

            # Pause after too many consecutive errors
            if error_count >= self.config.max_consecutive_errors:
                self._paused[job_key] = True
                logger.critical(
                    "[%s] PAUSED — %d consecutive errors",
                    job_label,
                    error_count,
                )
                await send_alert(
                    title=f"Scheduler Paused: {job_label}",
                    message=(
                        f"{job_label} trading paused after "
                        f"{error_count} consecutive errors. "
                        f"Last error: {e}"
                    ),
                    level=AlertLevel.CRITICAL,
                )

    async def _persist_portfolio_snapshot(self) -> None:
        """Snapshot the current portfolio state into portfolio_snapshots."""
        try:
            snapshot = await self.executor.get_portfolio_snapshot()
            async with async_session_factory() as session:
                snapshot_id = await insert_portfolio_snapshot(session, snapshot)
                await session.commit()
            logger.info(
                "Portfolio snapshot persisted: id=%s total=$%.2f",
                snapshot_id, snapshot.total_value,
            )
        except Exception as e:
            logger.error("Failed to persist portfolio snapshot: %s", e)

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

            # Signal drought detection
            try:
                from engines.learning.drought_detector import detect_and_alert

                scheduler_status = self.status()
                shadow_stats = (
                    self.executor.stats.summary()
                    if hasattr(self.executor, "stats")
                    else {"total_signals": 0}
                )
                droughts = await detect_and_alert(
                    scheduler_status, shadow_stats
                )
                if droughts:
                    logger.info(
                        "Signal drought detected: %d jobs with 0 signals",
                        len(droughts),
                    )
            except Exception as e:
                logger.warning("Drought detection failed: %s", e)

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
        """Resume all paused tiers for an asset class after fixing issues."""
        for tier in StrategyTier:
            key = (tier, asset_class)
            self._paused[key] = False
            self._consecutive_errors[key] = 0
        logger.info("Resumed %s trading cycles (all tiers)", asset_class.value)

    def resume_job(
        self, tier: StrategyTier, asset_class: AssetClass
    ) -> None:
        """Resume a specific paused (tier, asset_class) job."""
        key = (tier, asset_class)
        self._paused[key] = False
        self._consecutive_errors[key] = 0
        logger.info(
            "Resumed %s/%s trading cycle", tier.value, asset_class.value
        )

    def pause(self, asset_class: AssetClass) -> None:
        """Manually pause all tiers for an asset class."""
        for tier in StrategyTier:
            self._paused[(tier, asset_class)] = True
        logger.info("Manually paused %s trading cycles (all tiers)", asset_class.value)

    def pause_job(
        self, tier: StrategyTier, asset_class: AssetClass
    ) -> None:
        """Manually pause a specific (tier, asset_class) job."""
        self._paused[(tier, asset_class)] = True
        logger.info(
            "Manually paused %s/%s trading cycle",
            tier.value,
            asset_class.value,
        )

    def pause_all(self) -> None:
        """Pause all jobs (emergency stop)."""
        for key in self._all_job_keys:
            self._paused[key] = True
        logger.critical("ALL trading cycles paused")

    def status(self) -> dict[str, Any]:
        """Return scheduler status for health check endpoint."""
        jobs: dict[str, Any] = {}

        # Per asset-class summary (backward compatible)
        for asset_class in AssetClass:
            strategies = self._strategies[asset_class]
            # Aggregate paused/errors across tiers
            ac_paused = all(
                self._paused[(tier, asset_class)] for tier in StrategyTier
            )
            ac_errors = sum(
                self._consecutive_errors[(tier, asset_class)]
                for tier in StrategyTier
            )
            ac_cycles = sum(
                self._cycle_counts[(tier, asset_class)]
                for tier in StrategyTier
            )
            last_runs = [
                self._last_run[(tier, asset_class)]
                for tier in StrategyTier
                if self._last_run[(tier, asset_class)] is not None
            ]
            latest = max(last_runs) if last_runs else None

            jobs[asset_class.value] = {
                "strategies": len(strategies),
                "paused": ac_paused,
                "consecutive_errors": ac_errors,
                "cycles_completed": ac_cycles,
                "last_run": latest.isoformat() if latest else None,
            }

        # Detailed per-tier breakdown
        tier_jobs: dict[str, dict[str, Any]] = {}
        for (tier, asset_class), strats in self._tier_strategies.items():
            key = (tier, asset_class)
            job_id = self._job_id(tier, asset_class)
            interval = TIER_INTERVALS[tier][asset_class.value]
            tier_jobs[job_id] = {
                "tier": tier.value,
                "asset_class": asset_class.value,
                "strategies": len(strats),
                "interval_minutes": interval,
                "paused": self._paused[key],
                "consecutive_errors": self._consecutive_errors[key],
                "cycles_completed": self._cycle_counts[key],
                "last_run": (
                    self._last_run[key].isoformat()
                    if self._last_run[key]
                    else None
                ),
            }

        return {
            "running": self._running,
            "enabled": self.config.enabled,
            "market_open": self._is_market_open(),
            "jobs": jobs,
            "tier_jobs": tier_jobs,
            "learning": {
                "enabled": self.config.learning_enabled,
                "fast_loop": f"daily at {self.config.fast_loop_hour:02d}:{self.config.fast_loop_minute:02d} ET",
                "slow_loop": f"{self.config.slow_loop_day} at {self.config.slow_loop_hour:02d}:{self.config.slow_loop_minute:02d} ET",
            },
        }
