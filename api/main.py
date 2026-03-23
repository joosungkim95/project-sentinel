"""
Sentinel Trading Platform — API Entry Point.

Lightweight FastAPI app for:
- Health checks (Railway monitoring)
- Dashboard API endpoints
- Manual controls (emergency stop, etc.)
"""

from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from data.database import engine, async_session_factory
from data.repositories.trades import get_recent_trades
from data.repositories.portfolio import get_latest_snapshot
from data.repositories.risk_events import get_recent_risk_events


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown logic."""
    # Verify database connection
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        app.state.db_connected = True
    except Exception as e:
        app.state.db_connected = False
        print(f"WARNING: Database connection failed: {e}")

    app.state.started_at = datetime.utcnow()
    yield
    await engine.dispose()


app = FastAPI(
    title="Sentinel",
    description="Autonomous Trading Platform",
    version="0.1.0",
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

    Returns system status, uptime, and engine states.
    """
    return {
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat(),
        "started_at": getattr(app.state, "started_at", None),
        "engines": {
            "risk": "initialized",      # TODO: actual status
            "strategy": "initialized",
            "execution": "initialized",
            "learning": "initialized",
        },
        "connections": {
            "database": "connected" if getattr(app.state, "db_connected", False) else "disconnected",
            "redis": "pending",
            "alpaca": "pending",
            "coinbase": "pending",
            "polymarket": "pending",
            "kalshi": "pending",
        },
    }


@app.post("/emergency-stop")
async def emergency_stop():
    """
    Emergency stop — halt all trading immediately.

    Activates the Risk Engine circuit breaker and optionally
    liquidates all positions.
    """
    # TODO: Implement
    return {"status": "circuit_breaker_activated", "message": "All trading halted"}


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
    # TODO: Implement
    return {"strategies": []}
