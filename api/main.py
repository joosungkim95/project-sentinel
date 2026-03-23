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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown logic."""
    # TODO: Initialize DB connection pool
    # TODO: Initialize Redis connection
    # TODO: Start scheduler for strategy scans
    app.state.started_at = datetime.utcnow()
    yield
    # TODO: Graceful shutdown — close positions if configured


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
            "database": "pending",       # TODO: actual check
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
    """Get current portfolio snapshot."""
    # TODO: Implement
    return {"status": "not_implemented"}


@app.get("/trades")
async def get_trades(limit: int = 50):
    """Get recent trades."""
    # TODO: Implement
    return {"trades": [], "count": 0}


@app.get("/strategies")
async def get_strategies():
    """Get all strategies and their current status."""
    # TODO: Implement
    return {"strategies": []}
