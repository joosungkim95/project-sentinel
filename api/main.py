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
from engines.execution.base import Executor
from engines.models import AssetClass
from engines.risk.engine import RiskEngine
from engines.scheduler import TradingScheduler
from engines.strategy.crypto.trend_following import TrendFollowingStrategy
from engines.strategy.equities.momentum import MomentumStrategy
from engines.strategy.equities.sma_crossover import SMACrossoverStrategy
from engines.strategy.predictions.value_pricing import ValuePricingStrategy

logger = logging.getLogger(__name__)


def _build_strategies() -> list:
    """Instantiate all active strategies."""
    strategies = []

    # --- Equities ---
    sma = SMACrossoverStrategy()
    sma.activate()
    strategies.append(sma)

    momentum = MomentumStrategy()
    momentum.activate()
    strategies.append(momentum)

    # --- Crypto ---
    trend_btc = TrendFollowingStrategy()
    trend_btc.activate()
    strategies.append(trend_btc)

    trend_eth = TrendFollowingStrategy(
        strategy_id="trend_eth",
        parameters={"symbol": "ETH-USD", "position_size_usd": 150.0},
    )
    trend_eth.activate()
    strategies.append(trend_eth)

    # --- Predictions ---
    value_kalshi = ValuePricingStrategy()
    value_kalshi.activate()
    strategies.append(value_kalshi)

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
    executor = _build_executor()
    strategies = _build_strategies()

    # Build and start scheduler
    scheduler_config = SchedulerConfig()
    scheduler = TradingScheduler(
        risk_engine=risk_engine,
        executor=executor,
        strategies=strategies,
        config=scheduler_config,
    )
    scheduler.start()

    # Store references for endpoints
    app.state.risk_engine = risk_engine
    app.state.executor = executor
    app.state.scheduler = scheduler
    app.state.strategies = strategies

    logger.info(
        "Sentinel started: %d strategies, scheduler %s",
        len(strategies),
        "enabled" if scheduler_config.enabled else "disabled",
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
    return {
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat(),
        "started_at": getattr(app.state, "started_at", None),
        "strategies": len(getattr(app.state, "strategies", [])),
        "scheduler": scheduler.status(),
        "connections": {
            "database": (
                "connected"
                if getattr(app.state, "db_connected", False)
                else "disconnected"
            ),
        },
    }


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
                "asset_class": s.asset_class.value,
                "status": s.status.value,
                "parameters": s.parameters,
            }
            for s in strategies
        ],
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
