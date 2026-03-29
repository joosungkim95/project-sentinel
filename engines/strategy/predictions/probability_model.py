"""
Shared probability model for Kalshi crypto prediction markets.

Provides:
- calc_realized_vol: annualized realized volatility from hourly close prices
- calc_binary_probability: P(S_T > K) using log-normal model
- calc_half_kelly: half-Kelly fraction for position sizing
"""

import math
import numpy as np
from scipy.stats import norm


def calc_realized_vol(
    closes: list[float],
    min_observations: int = 48,
) -> float | None:
    """Calculate annualized realized volatility from hourly close prices.

    Args:
        closes: Hourly close prices, oldest first.
        min_observations: Minimum prices needed (default 48 = 2 days).

    Returns:
        Annualized volatility as float (e.g., 0.50 = 50%), or None if insufficient data.
    """
    if len(closes) < min_observations:
        return None

    prices = np.array(closes, dtype=np.float64)
    if np.any(prices <= 0):
        return None

    log_returns = np.diff(np.log(prices))
    if len(log_returns) == 0:
        return 0.0

    hourly_std = float(np.std(log_returns, ddof=1))
    annualized = hourly_std * math.sqrt(365 * 24)
    return annualized


def calc_binary_probability(
    spot: float,
    strike: float,
    vol: float,
    hours_to_expiry: float,
    drift: float = 0.0,
) -> float:
    """Calculate P(S_T > K) under a log-normal model.

    d2 = [ln(S/K) + (μ - σ²/2) * T] / (σ * √T)
    P(S_T > K) = N(d2)

    Args:
        spot: Current price.
        strike: Contract strike price (threshold).
        vol: Annualized realized volatility.
        hours_to_expiry: Hours until contract settlement.
        drift: Expected drift (default 0).

    Returns:
        Probability between 0.0 and 1.0.
    """
    if hours_to_expiry <= 0:
        return 1.0 if spot > strike else 0.0
    if vol <= 0 or spot <= 0 or strike <= 0:
        return 0.5

    t_years = hours_to_expiry / (365 * 24)
    sqrt_t = math.sqrt(t_years)
    d2 = (math.log(spot / strike) + (drift - 0.5 * vol**2) * t_years) / (vol * sqrt_t)
    return float(norm.cdf(d2))


def calc_half_kelly(
    model_prob: float,
    market_prob: float,
) -> float:
    """Calculate half-Kelly fraction for a binary bet.

    Args:
        model_prob: Model's estimated probability of YES.
        market_prob: Market's implied probability (YES price).

    Returns:
        Half-Kelly fraction (0.0 to ~0.5). 0.0 if no edge.
    """
    if model_prob > market_prob and market_prob < 1.0:
        # Only bet when we have positive edge (model thinks YES is more likely than market)
        kelly = (model_prob - market_prob) / (1.0 - market_prob)
        return max(kelly / 2.0, 0.0)
    return 0.0
