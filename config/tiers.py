"""Tier definitions, budgets, timeframes, and confidence thresholds."""

from enum import Enum


class StrategyTier(str, Enum):
    SCOUT = "scout"
    CORE = "core"
    SNIPER = "sniper"


TIER_RISK_BUDGET: dict[StrategyTier, float] = {
    StrategyTier.SCOUT: 0.20,
    StrategyTier.CORE: 0.50,
    StrategyTier.SNIPER: 0.30,
}

TIER_CONFIDENCE_THRESHOLD: dict[StrategyTier, float] = {
    StrategyTier.SCOUT: 0.2,
    StrategyTier.CORE: 0.4,
    StrategyTier.SNIPER: 0.7,
}

# Canonical timeframe strings use Alpaca convention
TIER_TIMEFRAMES: dict[StrategyTier, dict[str, str]] = {
    StrategyTier.SCOUT: {
        "equities": "15Min",
        "crypto": "1Hour",
        "predictions": "realtime",
    },
    StrategyTier.CORE: {
        "equities": "4Hour",
        "crypto": "4Hour",
        "predictions": "realtime",
    },
    StrategyTier.SNIPER: {
        "equities": "1Day",
        "crypto": "1Day",
        "predictions": "realtime",
    },
}

# Maps canonical timeframe strings to Coinbase granularity values
COINBASE_TIMEFRAME_MAP: dict[str, str] = {
    "15Min": "FIFTEEN_MINUTE",
    "1Hour": "ONE_HOUR",
    "4Hour": "ONE_HOUR",  # Fetch 1h, aggregate to 4h in pipeline
    "1Day": "ONE_DAY",
}

# Whether a timeframe needs bar aggregation (e.g., 4h from 1h on Coinbase)
TIMEFRAME_AGGREGATION: dict[str, dict] = {
    "4Hour": {"source": "ONE_HOUR", "factor": 4},
}
