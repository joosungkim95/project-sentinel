"""Unit tests for the shared probability model module."""

import math
import numpy as np
import pytest

from engines.strategy.predictions.probability_model import (
    calc_realized_vol,
    calc_binary_probability,
    calc_half_kelly,
)


# ---------------------------------------------------------------------------
# calc_realized_vol
# ---------------------------------------------------------------------------

class TestCalcRealizedVol:

    def test_constant_prices_zero_vol(self):
        """50 identical prices should yield zero volatility."""
        prices = [100.0] * 50
        result = calc_realized_vol(prices)
        assert result == 0.0

    def test_known_volatility(self):
        """720 hourly prices generated with ~50% annualized vol should land in [0.30, 0.70]."""
        rng = np.random.default_rng(42)
        hourly_vol = 0.50 / math.sqrt(365 * 24)
        log_returns = rng.normal(0, hourly_vol, 719)
        prices = [70000.0]
        for r in log_returns:
            prices.append(prices[-1] * math.exp(r))
        result = calc_realized_vol(prices)
        assert result is not None
        assert 0.30 <= result <= 0.70

    def test_insufficient_data_returns_none(self):
        """Only 2 prices should return None."""
        result = calc_realized_vol([100.0, 101.0])
        assert result is None


# ---------------------------------------------------------------------------
# calc_binary_probability
# ---------------------------------------------------------------------------

class TestCalcBinaryProbability:

    def test_atm_near_50pct(self):
        """ATM option (spot == strike) at 24h should be near 0.50."""
        result = calc_binary_probability(spot=70000, strike=70000, vol=0.50, hours_to_expiry=24)
        assert 0.45 <= result <= 0.55

    def test_deep_itm_near_100pct(self):
        """Spot well above strike should yield probability > 0.95."""
        result = calc_binary_probability(spot=70000, strike=50000, vol=0.50, hours_to_expiry=24)
        assert result > 0.95

    def test_deep_otm_near_zero(self):
        """Spot well below strike should yield probability < 0.10."""
        result = calc_binary_probability(spot=70000, strike=100000, vol=0.50, hours_to_expiry=24)
        assert result < 0.10

    def test_longer_expiry_higher_uncertainty(self):
        """OTM strike: longer expiry should yield higher probability than shorter."""
        strike = 80000
        short_prob = calc_binary_probability(spot=70000, strike=strike, vol=0.50, hours_to_expiry=24)
        long_prob = calc_binary_probability(spot=70000, strike=strike, vol=0.50, hours_to_expiry=720)
        assert long_prob > short_prob

    def test_zero_hours_returns_deterministic(self):
        """At expiry, result is deterministic: 1.0 if spot > strike, else 0.0."""
        assert calc_binary_probability(spot=70000, strike=60000, vol=0.50, hours_to_expiry=0) == 1.0
        assert calc_binary_probability(spot=70000, strike=80000, vol=0.50, hours_to_expiry=0) == 0.0


# ---------------------------------------------------------------------------
# calc_half_kelly
# ---------------------------------------------------------------------------

class TestCalcHalfKelly:

    def test_positive_edge(self):
        """Model prob > market prob should yield a positive fraction in (0, 1)."""
        result = calc_half_kelly(model_prob=0.60, market_prob=0.40)
        assert 0.0 < result < 1.0

    def test_no_edge(self):
        """Equal probabilities should yield 0.0."""
        result = calc_half_kelly(model_prob=0.50, market_prob=0.50)
        assert result == 0.0

    def test_negative_edge(self):
        """Model prob < market prob should yield 0.0 (no bet)."""
        result = calc_half_kelly(model_prob=0.30, market_prob=0.50)
        assert result == 0.0
