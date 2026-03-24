"""
Backtesting framework — Simulate strategies on historical data.
"""

from backtesting.data_loader import (
    generate_ranging_bars,
    generate_synthetic_bars,
    generate_trending_bars,
    generate_volatile_bars,
    load_bars_from_csv,
    slice_walk_forward,
)
from backtesting.engine import BacktestEngine, BacktestResult

__all__ = [
    "BacktestEngine",
    "BacktestResult",
    "generate_ranging_bars",
    "generate_synthetic_bars",
    "generate_trending_bars",
    "generate_volatile_bars",
    "load_bars_from_csv",
    "slice_walk_forward",
]
