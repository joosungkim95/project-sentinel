"""
Sentinel Trading Platform — API Entry Point.

Lightweight FastAPI app for:
- Health checks (Railway monitoring)
- Dashboard API endpoints
- Manual controls (emergency stop, etc.)
- Trading scheduler lifecycle
"""

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from config.risk_config import RiskConfig
from config.scheduler_config import SchedulerConfig
from data.database import engine, async_session_factory
from data.repositories.trades import get_recent_trades
from data.repositories.portfolio import get_latest_snapshot
from data.repositories.risk_events import get_recent_risk_events
from data.repositories.strategies import get_strategy_performance
from engines.execution.base import Executor
from engines.execution.shadow import ShadowExecutor
from engines.models import AssetClass
from engines.recovery import HealthMonitor
from engines.risk.engine import RiskEngine
from engines.scheduler import TradingScheduler

# Scouts (fast, loose, small bets)
from engines.strategy.equities.momentum import MomentumStrategy
from engines.strategy.equities.gap_and_go import GapAndGoStrategy
from engines.strategy.crypto.breakout import BreakoutStrategy
from engines.strategy.predictions.value_pricing import MarketSkimmerStrategy

# Core (confirmed setups, balanced)
from engines.strategy.equities.trend_following import EquityTrendFollowingStrategy
from engines.strategy.equities.mean_reversion import MeanReversionStrategy
from engines.strategy.equities.vwap import VWAPStrategy
from engines.strategy.equities.pullback import PullbackStrategy
from engines.strategy.crypto.trend_following import TrendFollowingStrategy
from engines.strategy.predictions.value_pricing import ValuePricingStrategy

# Snipers (rare, high-conviction)
from engines.strategy.equities.sma_crossover import SMACrossoverStrategy
from engines.strategy.crypto.volatility_harvest import VolatilityHarvestStrategy
from engines.strategy.predictions.news_driven import NewsDrivenStrategy

logger = logging.getLogger(__name__)


def _build_strategies() -> list:
    """Instantiate all 13 tiered strategies."""
    strategies = [
        # Scouts (fast, loose, small bets)
        MomentumStrategy(),              # 7 equities, 15min, OR-based
        GapAndGoStrategy(),              # 7 equities, 15min, gap continuation
        BreakoutStrategy(),              # 5 crypto, 1h, volume as confluence
        MarketSkimmerStrategy(),         # Kalshi scan, realtime

        # Core (confirmed setups, balanced)
        EquityTrendFollowingStrategy(),  # 4 equities, 4h, OR-relaxed
        MeanReversionStrategy(),         # 7 equities, 4h, OR-based
        VWAPStrategy(),                  # 7 equities, 15min, VWAP deviation
        PullbackStrategy(),              # 7 equities, 4h, trend pullback
        TrendFollowingStrategy(),        # 3 crypto, 4h, OR-relaxed
        ValuePricingStrategy(),          # Kalshi scan, realtime

        # Snipers (rare, high-conviction)
        SMACrossoverStrategy(),          # 3 equities, daily
        VolatilityHarvestStrategy(),     # 2 crypto, daily
        NewsDrivenStrategy(),            # Kalshi scan, realtime
    ]

    for s in strategies:
        s.activate()

    return strategies


def _build_executor() -> Executor:
    """Build the executor with all available platform adapters."""
    executor = Executor()

    # Register adapters based on available credentials.
    # Import lazily so missing optional deps don't crash startup.
    if os.getenv("ALPACA_API_KEY"):
        try:
            from engines.execution.alpaca import AlpacaAdapter

            executor.register_adapter(AlpacaAdapter(
                api_key=os.environ["ALPACA_API_KEY"],
                secret_key=os.environ["ALPACA_SECRET_KEY"],
                base_url=os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets"),
            ))
            logger.info("Registered Alpaca adapter")
        except Exception as e:
            logger.warning("Failed to register Alpaca adapter: %s", e)

    if os.getenv("COINBASE_API_KEY"):
        try:
            from engines.execution.coinbase import CoinbaseAdapter

            executor.register_adapter(CoinbaseAdapter(
                api_key=os.environ["COINBASE_API_KEY"],
                api_secret=os.environ["COINBASE_API_SECRET"],
            ))
            logger.info("Registered Coinbase adapter")
        except Exception as e:
            logger.warning("Failed to register Coinbase adapter: %s", e)

    if os.getenv("KALSHI_API_KEY"):
        try:
            from engines.execution.kalshi import KalshiAdapter

            executor.register_adapter(KalshiAdapter(
                api_key=os.environ["KALSHI_API_KEY"],
                private_key_pem=os.environ["KALSHI_PRIVATE_KEY"],
                base_url=os.getenv("KALSHI_BASE_URL", "https://demo-api.kalshi.co"),
            ))
            logger.info("Registered Kalshi adapter")
        except Exception as e:
            logger.warning("Failed to register Kalshi adapter: %s", e)

    return executor


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown logic."""
    # --- Startup ---

    # Verify database connection
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        app.state.db_connected = True
    except Exception as e:
        app.state.db_connected = False
        logger.warning("Database connection failed: %s", e)

    app.state.started_at = datetime.utcnow()

    # Build engines
    risk_engine = RiskEngine(RiskConfig())
    real_executor = _build_executor()
    strategies = _build_strategies()

    # Shadow mode: wrap executor if SHADOW_MODE=true
    shadow_mode = os.getenv("SHADOW_MODE", "").lower() in ("true", "1", "yes")
    shadow_executor = None

    if shadow_mode:
        shadow_executor = ShadowExecutor(
            real_executor=real_executor,
            max_divergence_pct=float(os.getenv("SHADOW_MAX_DIVERGENCE_PCT", "2.0")),
            auto_pause_on_divergence=True,
        )
        # Pipeline uses shadow executor (returns paper results, runs live at min size)
        executor = shadow_executor
        logger.info("Shadow mode ENABLED (max divergence: %.1f%%)", shadow_executor.max_divergence_pct)
    else:
        executor = real_executor
        logger.info("Shadow mode disabled — using standard executor")

    # Build and start scheduler
    scheduler_config = SchedulerConfig()
    scheduler = TradingScheduler(
        risk_engine=risk_engine,
        executor=executor,
        strategies=strategies,
        config=scheduler_config,
    )
    scheduler.start()

    # Health monitor for graceful degradation
    health_monitor = HealthMonitor()
    health_monitor.register("database", max_failures=3, backoff_seconds=30)
    health_monitor.register("alpaca", max_failures=5, backoff_seconds=60)
    health_monitor.register("coinbase", max_failures=5, backoff_seconds=60)
    health_monitor.register("kalshi", max_failures=5, backoff_seconds=60)
    health_monitor.register("scheduler", max_failures=3, backoff_seconds=15)

    # Store references for endpoints
    app.state.risk_engine = risk_engine
    app.state.executor = executor
    app.state.shadow_executor = shadow_executor
    app.state.shadow_mode = shadow_mode
    app.state.scheduler = scheduler
    app.state.strategies = strategies
    app.state.health_monitor = health_monitor

    logger.info(
        "Sentinel started: %d strategies, scheduler %s, shadow %s",
        len(strategies),
        "enabled" if scheduler_config.enabled else "disabled",
        "ON" if shadow_mode else "OFF",
    )

    yield

    # --- Shutdown ---
    await scheduler.stop()
    await engine.dispose()
    logger.info("Sentinel shut down")


app = FastAPI(
    title="Sentinel",
    description="Autonomous Trading Platform",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health_check():
    """
    Health check endpoint for Railway monitoring.

    Returns system status, uptime, engine states, and scheduler info.
    """
    scheduler: TradingScheduler = app.state.scheduler
    result = {
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat(),
        "started_at": getattr(app.state, "started_at", None),
        "strategies": len(getattr(app.state, "strategies", [])),
        "scheduler": scheduler.status(),
        "shadow_mode": getattr(app.state, "shadow_mode", False),
        "connections": {
            "database": (
                "connected"
                if getattr(app.state, "db_connected", False)
                else "disconnected"
            ),
        },
    }
    if app.state.shadow_mode and app.state.shadow_executor:
        result["shadow_stats"] = app.state.shadow_executor.stats.summary()
    return result


@app.post("/emergency-stop")
async def emergency_stop():
    """
    Emergency stop — halt all trading immediately.

    Pauses all scheduler jobs and activates the Risk Engine circuit breaker.
    """
    scheduler: TradingScheduler = app.state.scheduler
    risk_engine: RiskEngine = app.state.risk_engine

    scheduler.pause_all()
    risk_engine.activate_circuit_breaker()

    logger.critical("EMERGENCY STOP activated via API")

    return {
        "status": "emergency_stop_activated",
        "message": "All trading halted. Scheduler paused. Circuit breaker active.",
        "circuit_breaker_active": True,
        "scheduler": scheduler.status(),
    }


@app.post("/scheduler/resume/{asset_class}")
async def resume_asset_class(asset_class: str):
    """Resume a paused asset class."""
    scheduler: TradingScheduler = app.state.scheduler
    try:
        ac = AssetClass(asset_class)
    except ValueError:
        return {"error": f"Unknown asset class: {asset_class}"}

    scheduler.resume(ac)
    return {"status": "resumed", "asset_class": asset_class}


@app.post("/scheduler/pause/{asset_class}")
async def pause_asset_class(asset_class: str):
    """Pause a specific asset class."""
    scheduler: TradingScheduler = app.state.scheduler
    try:
        ac = AssetClass(asset_class)
    except ValueError:
        return {"error": f"Unknown asset class: {asset_class}"}

    scheduler.pause(ac)
    return {"status": "paused", "asset_class": asset_class}


@app.get("/portfolio")
async def get_portfolio():
    """Get latest portfolio snapshot."""
    async with async_session_factory() as session:
        snapshot = await get_latest_snapshot(session)
        if snapshot is None:
            return {"status": "no_data", "message": "No portfolio snapshots yet"}
        return {
            "total_value": snapshot.total_value,
            "cash": snapshot.cash,
            "positions": snapshot.positions,
            "risk_utilization": snapshot.risk_utilization,
            "timestamp": snapshot.created_at.isoformat() if snapshot.created_at else None,
        }


@app.get("/trades")
async def get_trades(limit: int = 50):
    """Get recent trades."""
    async with async_session_factory() as session:
        trades = await get_recent_trades(session, limit=limit)
        return {
            "trades": [
                {
                    "id": t.id,
                    "strategy_id": t.strategy_id,
                    "symbol": t.symbol,
                    "side": t.side,
                    "quantity": t.quantity,
                    "price": t.price,
                    "risk_check_result": t.risk_check_result,
                    "pnl": t.pnl,
                    "market_regime": t.market_regime,
                    "created_at": t.created_at.isoformat() if t.created_at else None,
                }
                for t in trades
            ],
            "count": len(trades),
        }


@app.get("/strategies")
async def get_strategies():
    """Get all strategies and their current status."""
    strategies = getattr(app.state, "strategies", [])
    return {
        "strategies": [
            {
                "id": s.strategy_id,
                "tier": s.tier.value,
                "asset_class": s.asset_class.value,
                "status": s.status.value,
                "parameters": s.parameters,
            }
            for s in strategies
        ],
    }


@app.get("/risk-events")
async def get_risk_events_endpoint(limit: int = 20):
    """Get recent risk events."""
    async with async_session_factory() as session:
        events = await get_recent_risk_events(session, limit=limit)
        return {
            "events": [
                {
                    "id": e.id,
                    "event_type": e.event_type,
                    "severity": e.severity,
                    "details": e.details,
                    "portfolio_value": e.portfolio_value_at_event,
                    "action_taken": e.action_taken,
                    "created_at": e.created_at.isoformat() if e.created_at else None,
                }
                for e in events
            ],
            "count": len(events),
        }


@app.get("/performance")
async def get_performance(strategy_id: str, limit: int = 30):
    """Get performance history for a strategy."""
    async with async_session_factory() as session:
        records = await get_strategy_performance(session, strategy_id, limit=limit)
        return {
            "strategy_id": strategy_id,
            "records": [
                {
                    "date": r.date.isoformat() if r.date else None,
                    "trades_count": r.trades_count,
                    "win_rate": r.win_rate,
                    "total_pnl": r.total_pnl,
                    "sharpe_ratio": r.sharpe_ratio,
                    "max_drawdown": r.max_drawdown,
                    "risk_budget_used": r.risk_budget_used,
                }
                for r in records
            ],
            "count": len(records),
        }


@app.get("/learning")
async def get_learning_status():
    """Get learning engine status and recent activity."""
    scheduler: TradingScheduler = app.state.scheduler
    status = scheduler.status()
    return {
        "learning": status.get("learning", {}),
        "scheduler_running": status.get("running", False),
    }


@app.get("/shadow")
async def get_shadow_status():
    """Get shadow mode status and divergence stats."""
    if not app.state.shadow_mode:
        return {"shadow_mode": False, "message": "Shadow mode is not enabled. Set SHADOW_MODE=true to enable."}

    shadow: ShadowExecutor = app.state.shadow_executor
    return {
        "shadow_mode": True,
        **shadow.status(),
    }


@app.post("/shadow/pause")
async def pause_shadow_live():
    """Pause live trading in shadow mode (paper continues)."""
    if not app.state.shadow_mode or not app.state.shadow_executor:
        return {"error": "Shadow mode not enabled"}
    app.state.shadow_executor._live_paused = True
    return {"status": "live_paused", "message": "Live shadow trades paused. Paper continues."}


@app.post("/shadow/resume")
async def resume_shadow_live():
    """Resume live trading in shadow mode."""
    if not app.state.shadow_mode or not app.state.shadow_executor:
        return {"error": "Shadow mode not enabled"}
    app.state.shadow_executor.resume_live()
    return {"status": "live_resumed"}


@app.post("/shadow/reset")
async def reset_shadow_stats():
    """Reset shadow mode statistics."""
    if not app.state.shadow_mode or not app.state.shadow_executor:
        return {"error": "Shadow mode not enabled"}
    app.state.shadow_executor.reset_stats()
    return {"status": "stats_reset"}


@app.get("/system-health")
async def get_system_health():
    """Get detailed system health with recovery status."""
    monitor: HealthMonitor = app.state.health_monitor
    scheduler: TradingScheduler = app.state.scheduler

    # Check DB health
    try:
        async with async_session_factory() as session:
            from sqlalchemy import text
            await session.execute(text("SELECT 1"))
        await monitor.record_success("database")
    except Exception as e:
        await monitor.record_failure("database", str(e))

    return {
        "health": monitor.system_status(),
        "scheduler": scheduler.status(),
        "risk_engine": {
            "circuit_breaker_active": app.state.risk_engine._is_circuit_breaker_active(),
        },
    }


# --- Static dashboard files ---
# Serve the built React dashboard from api/static/ (built by Vite).
# Mount AFTER API routes so /health, /trades, etc. take priority.

_static_dir = Path(__file__).parent / "static"
if _static_dir.is_dir():
    app.mount(
        "/assets",
        StaticFiles(directory=_static_dir / "assets"),
        name="static-assets",
    )

    @app.get("/{path:path}")
    async def serve_dashboard(path: str):
        """Serve the React SPA for any non-API path."""
        # Try serving the exact file first (e.g., favicon.ico)
        file_path = _static_dir / path
        if path and file_path.is_file():
            return FileResponse(file_path)
        # Otherwise serve index.html (SPA routing)
        return FileResponse(_static_dir / "index.html")
