import pytest
from config.tiers import (
    StrategyTier, TIER_RISK_BUDGET, TIER_CONFIDENCE_THRESHOLD,
    TIER_TIMEFRAMES, COINBASE_TIMEFRAME_MAP,
)
from config.symbols import EQUITY_SYMBOLS, CRYPTO_SYMBOLS


def test_tier_enum_values():
    assert StrategyTier.SCOUT == "scout"
    assert StrategyTier.CORE == "core"
    assert StrategyTier.SNIPER == "sniper"


def test_tier_budgets_sum_to_one():
    total = sum(TIER_RISK_BUDGET.values())
    assert total == pytest.approx(1.0)


def test_tier_budgets_all_tiers_present():
    for tier in StrategyTier:
        assert tier in TIER_RISK_BUDGET


def test_confidence_thresholds_ascending():
    assert TIER_CONFIDENCE_THRESHOLD[StrategyTier.SCOUT] < TIER_CONFIDENCE_THRESHOLD[StrategyTier.CORE]
    assert TIER_CONFIDENCE_THRESHOLD[StrategyTier.CORE] < TIER_CONFIDENCE_THRESHOLD[StrategyTier.SNIPER]


def test_tier_timeframes_structure():
    for tier in StrategyTier:
        assert "equities" in TIER_TIMEFRAMES[tier]
        assert "crypto" in TIER_TIMEFRAMES[tier]


def test_coinbase_timeframe_map():
    assert COINBASE_TIMEFRAME_MAP["15Min"] == "FIFTEEN_MINUTE"
    assert COINBASE_TIMEFRAME_MAP["1Hour"] == "ONE_HOUR"
    assert COINBASE_TIMEFRAME_MAP["4Hour"] == "ONE_HOUR"
    assert COINBASE_TIMEFRAME_MAP["1Day"] == "ONE_DAY"


def test_equity_symbols():
    assert "SPY" in EQUITY_SYMBOLS
    assert "QQQ" in EQUITY_SYMBOLS
    assert "AAPL" in EQUITY_SYMBOLS
    assert "NVDA" in EQUITY_SYMBOLS
    assert len(EQUITY_SYMBOLS) == 7


def test_crypto_symbols():
    assert "BTC-USD" in CRYPTO_SYMBOLS
    assert "SOL-USD" in CRYPTO_SYMBOLS
    assert len(CRYPTO_SYMBOLS) == 5
