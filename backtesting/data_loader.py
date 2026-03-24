"""
Backtesting Data Loader — Loads or generates historical bar data.

Supports:
- CSV files (standard OHLCV format)
- Synthetic data generation for testing
- Slicing into walk-forward windows
"""

import csv
import math
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


def load_bars_from_csv(
    filepath: str | Path,
    date_col: str = "date",
    open_col: str = "open",
    high_col: str = "high",
    low_col: str = "low",
    close_col: str = "close",
    volume_col: str = "volume",
) -> list[dict[str, Any]]:
    """
    Load OHLCV bars from a CSV file.

    Args:
        filepath: Path to the CSV file.
        date_col: Name of the date column.
        open_col: Name of the open price column.
        high_col: Name of the high price column.
        low_col: Name of the low price column.
        close_col: Name of the close price column.
        volume_col: Name of the volume column.

    Returns:
        List of bar dicts with keys: date, open, high, low, close, volume.
    """
    bars: list[dict[str, Any]] = []
    path = Path(filepath)

    with path.open("r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            bars.append({
                "date": row[date_col],
                "open": float(row[open_col]),
                "high": float(row[high_col]),
                "low": float(row[low_col]),
                "close": float(row[close_col]),
                "volume": float(row[volume_col]),
            })

    return bars


def generate_synthetic_bars(
    num_bars: int = 200,
    start_price: float = 100.0,
    volatility: float = 0.02,
    trend: float = 0.0005,
    start_date: datetime | None = None,
    seed: int | None = None,
) -> list[dict[str, Any]]:
    """
    Generate synthetic OHLCV bars for backtesting.

    Uses geometric Brownian motion for realistic price action.

    Args:
        num_bars: Number of bars to generate.
        start_price: Starting price.
        volatility: Daily volatility (std dev of returns). 0.02 = 2%.
        trend: Daily drift. Positive = uptrend.
        start_date: Start date for bars. Defaults to num_bars days ago.
        seed: Random seed for reproducibility.

    Returns:
        List of bar dicts with OHLCV data.
    """
    if seed is not None:
        random.seed(seed)

    if start_date is None:
        start_date = datetime.now() - timedelta(days=num_bars)

    bars: list[dict[str, Any]] = []
    price = start_price

    for i in range(num_bars):
        # Geometric Brownian motion
        daily_return = trend + volatility * random.gauss(0, 1)
        close = price * (1 + daily_return)

        # Generate OHLV around the close
        intraday_vol = volatility * 0.5
        high = max(price, close) * (1 + abs(random.gauss(0, intraday_vol)))
        low = min(price, close) * (1 - abs(random.gauss(0, intraday_vol)))
        open_price = price * (1 + random.gauss(0, intraday_vol * 0.3))

        # Ensure OHLC consistency
        high = max(high, open_price, close)
        low = min(low, open_price, close)

        volume = max(1000, int(1_000_000 * (1 + random.gauss(0, 0.3))))

        bar_date = start_date + timedelta(days=i)
        bars.append({
            "date": bar_date.strftime("%Y-%m-%d"),
            "open": round(open_price, 2),
            "high": round(high, 2),
            "low": round(low, 2),
            "close": round(close, 2),
            "volume": volume,
        })

        price = close

    return bars


def generate_trending_bars(
    num_bars: int = 200,
    start_price: float = 100.0,
    direction: str = "up",
    seed: int | None = None,
) -> list[dict[str, Any]]:
    """Generate bars with a clear trend for testing trend-following strategies."""
    trend = 0.003 if direction == "up" else -0.003
    return generate_synthetic_bars(
        num_bars=num_bars,
        start_price=start_price,
        volatility=0.015,
        trend=trend,
        seed=seed,
    )


def generate_ranging_bars(
    num_bars: int = 200,
    center_price: float = 100.0,
    seed: int | None = None,
) -> list[dict[str, Any]]:
    """Generate bars that oscillate around a center price (mean-reverting)."""
    return generate_synthetic_bars(
        num_bars=num_bars,
        start_price=center_price,
        volatility=0.015,
        trend=0.0,
        seed=seed,
    )


def generate_volatile_bars(
    num_bars: int = 200,
    start_price: float = 100.0,
    seed: int | None = None,
) -> list[dict[str, Any]]:
    """Generate highly volatile bars for stress testing."""
    return generate_synthetic_bars(
        num_bars=num_bars,
        start_price=start_price,
        volatility=0.05,
        trend=0.0,
        seed=seed,
    )


def slice_walk_forward(
    bars: list[dict[str, Any]],
    train_size: int,
    test_size: int,
) -> list[tuple[list[dict], list[dict]]]:
    """
    Split bars into walk-forward train/test windows.

    Args:
        bars: Full bar history.
        train_size: Number of bars for training (in-sample).
        test_size: Number of bars for testing (out-of-sample).

    Returns:
        List of (train_bars, test_bars) tuples.
    """
    windows: list[tuple[list[dict], list[dict]]] = []
    total = len(bars)
    step = train_size + test_size

    for start in range(0, total - step + 1, test_size):
        train_end = start + train_size
        test_end = train_end + test_size
        if test_end > total:
            break
        windows.append((bars[start:train_end], bars[train_end:test_end]))

    return windows
