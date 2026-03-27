"""Centralized symbol lists per asset class."""

EQUITY_SYMBOLS: list[str] = [
    "SPY", "QQQ", "AAPL", "MSFT", "NVDA", "IWM", "DIA",
]

CRYPTO_SYMBOLS: list[str] = [
    "BTC-USD", "ETH-USD", "SOL-USD", "AVAX-USD", "DOGE-USD",
]

# Subsets for specific strategies
EQUITY_TREND_SYMBOLS: list[str] = ["SPY", "QQQ", "NVDA", "IWM"]
EQUITY_SNIPER_SYMBOLS: list[str] = ["SPY", "QQQ", "IWM"]
CRYPTO_TREND_SYMBOLS: list[str] = ["BTC-USD", "ETH-USD", "SOL-USD"]
CRYPTO_SNIPER_SYMBOLS: list[str] = ["BTC-USD", "ETH-USD"]
