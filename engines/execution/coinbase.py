"""
Coinbase Platform Adapter — Cryptocurrency trading.

Connects to Coinbase Advanced Trade API for:
- Major cryptocurrencies (BTC, ETH, SOL, etc.)
- Limit and market orders via CDP API keys

Docs: https://docs.cdp.coinbase.com/advanced-trade/docs/welcome
"""

import logging
import time
from typing import Any

from engines.models import (
    AssetClass,
    PositionInfo,
    RiskCheckResult,
    Side,
    TradeResult,
)

logger = logging.getLogger(__name__)


class CoinbaseAdapter:
    """
    Coinbase Advanced Trade platform adapter.

    Handles connection, order placement, position management,
    and market data for cryptocurrencies.
    """

    platform_name = "coinbase"
    asset_class = AssetClass.CRYPTO

    def __init__(self, api_key: str, api_secret: str):
        """
        Initialize Coinbase adapter.

        Args:
            api_key: CDP API key (organizations/.../apiKeys/... format).
            api_secret: EC private key in PEM format.
        """
        self.api_key = api_key
        # Ensure PEM newlines are real, not escaped
        self.api_secret = api_secret.replace("\\n", "\n")
        self._client = None

    async def connect(self) -> bool:
        """
        Connect to Coinbase and verify credentials.

        Returns:
            True if connection is healthy.
        """
        try:
            from coinbase.rest import RESTClient

            self._client = RESTClient(
                api_key=self.api_key,
                api_secret=self.api_secret,
            )
            # Verify by fetching accounts
            accounts = self._client.get_accounts()
            logger.info(
                "Coinbase connected: %d accounts found",
                len(accounts.accounts),
            )
            return True
        except Exception as e:
            logger.error("Coinbase connection failed: %s", e)
            return False

    async def execute_trade(
        self, risk_result: RiskCheckResult
    ) -> TradeResult:
        """
        Place an order on Coinbase.

        Uses market orders by default. Limit orders when target_price set.
        """
        signal = risk_result.original_signal
        quantity = risk_result.approved_quantity or signal.quantity
        product_id = self._to_product_id(signal.symbol)

        try:
            import uuid

            client_order_id = str(uuid.uuid4())
            side_str = signal.side.value.upper()

            if signal.target_price:
                order = self._client.limit_order_gtc(
                    client_order_id=client_order_id,
                    product_id=product_id,
                    side=side_str,
                    base_size=str(quantity),
                    limit_price=str(signal.target_price),
                )
            else:
                if signal.side == Side.BUY:
                    # Market buy uses quote_size (USD amount)
                    quote_size = str(round(quantity * self._get_price(product_id), 2))
                    order = self._client.market_order_buy(
                        client_order_id=client_order_id,
                        product_id=product_id,
                        quote_size=quote_size,
                    )
                else:
                    order = self._client.market_order_sell(
                        client_order_id=client_order_id,
                        product_id=product_id,
                        base_size=str(quantity),
                    )

            order_id = ""
            if order.success and order.success_response:
                order_id = order.success_response.get("order_id", "")

            logger.info(
                "Coinbase order submitted: %s %s %s (id=%s)",
                signal.side.value,
                quantity,
                product_id,
                order_id,
            )

            if not order.success:
                raise RuntimeError(f"Order rejected: {order.to_dict()}")

            return TradeResult(
                trade_id=order_id or client_order_id,
                signal=signal,
                risk_check=risk_result,
                executed=True,
                fill_price=None,  # Fills asynchronously
                fill_quantity=quantity,
                commission=0.0,  # Calculated separately
                platform=self.platform_name,
            )
        except Exception as e:
            logger.error("Coinbase order failed: %s", e)
            return TradeResult(
                trade_id="",
                signal=signal,
                risk_check=risk_result,
                executed=False,
                error_message=str(e),
                platform=self.platform_name,
            )

    async def get_positions(self) -> list[PositionInfo]:
        """Get all crypto positions with non-zero balances."""
        try:
            accounts = self._client.get_accounts()
            positions = []
            for acct in accounts.accounts:
                balance = float(acct.available_balance["value"])
                if balance <= 0:
                    continue
                # Skip USD/USDC — those are cash, not positions
                if acct.currency in ("USD", "USDC", "USDT"):
                    continue

                price = self._get_price(f"{acct.currency}-USD")
                if price <= 0:
                    continue

                positions.append(
                    PositionInfo(
                        symbol=acct.currency,
                        asset_class=AssetClass.CRYPTO,
                        side=Side.BUY,
                        quantity=balance,
                        entry_price=price,  # Approx — no entry tracking
                        current_price=price,
                        unrealized_pnl=0.0,  # Can't calculate without entry
                        pnl_pct=0.0,
                        strategy_id="unknown",
                    )
                )
            return positions
        except Exception as e:
            logger.error("Failed to get Coinbase positions: %s", e)
            return []

    async def get_account_value(self) -> float:
        """Get total account value in USD."""
        try:
            accounts = self._client.get_accounts()
            total = 0.0
            for acct in accounts.accounts:
                balance = float(acct.available_balance["value"])
                if balance <= 0:
                    continue
                if acct.currency in ("USD", "USDC", "USDT"):
                    total += balance
                else:
                    price = self._get_price(f"{acct.currency}-USD")
                    total += balance * price
            return total
        except Exception as e:
            logger.error("Failed to get Coinbase account value: %s", e)
            return 0.0

    async def get_quote(self, symbol: str) -> dict[str, Any]:
        """
        Get current price for a crypto symbol.

        Args:
            symbol: Crypto symbol (e.g., "BTC", "ETH") or product
                    ID (e.g., "BTC-USD").
        """
        try:
            product_id = self._to_product_id(symbol)
            product = self._client.get_product(product_id)
            price = float(product.price)
            return {
                "price": price,
                "product_id": product_id,
                "bid": price,  # Approximation — use order book for exact
                "ask": price,
                "volume_24h": float(product.volume_24h),
                "change_24h_pct": float(product.price_percentage_change_24h),
            }
        except Exception as e:
            logger.error("Failed to get quote for %s: %s", symbol, e)
            return {}

    async def get_historical_bars(
        self,
        symbol: str,
        granularity: str = "ONE_DAY",
        limit: int = 100,
    ) -> list[dict]:
        """
        Get historical candles for a crypto symbol.

        Args:
            symbol: Crypto symbol or product ID.
            granularity: ONE_MINUTE, FIVE_MINUTE, FIFTEEN_MINUTE,
                         ONE_HOUR, SIX_HOUR, ONE_DAY.
            limit: Number of candles.

        Returns:
            List of bar dicts with open, high, low, close, volume.
        """
        try:
            product_id = self._to_product_id(symbol)
            now = int(time.time())

            granularity_seconds = {
                "ONE_MINUTE": 60,
                "FIVE_MINUTE": 300,
                "FIFTEEN_MINUTE": 900,
                "ONE_HOUR": 3600,
                "SIX_HOUR": 21600,
                "ONE_DAY": 86400,
            }
            seconds = granularity_seconds.get(granularity, 86400)
            start = now - (limit * seconds)

            candles = self._client.get_candles(
                product_id,
                start=str(start),
                end=str(now),
                granularity=granularity,
            )

            return [
                {
                    "timestamp": c["start"],
                    "open": float(c["open"]),
                    "high": float(c["high"]),
                    "low": float(c["low"]),
                    "close": float(c["close"]),
                    "volume": float(c["volume"]),
                }
                for c in candles.candles
            ]
        except Exception as e:
            logger.error("Failed to get candles for %s: %s", symbol, e)
            return []

    async def health_check(self) -> bool:
        """Check if Coinbase connection is healthy."""
        try:
            self._client.get_product("BTC-USD")
            return True
        except Exception:
            return False

    def _get_price(self, product_id: str) -> float:
        """Get current price for a product. Returns 0 on failure."""
        try:
            product = self._client.get_product(product_id)
            return float(product.price)
        except Exception:
            return 0.0

    @staticmethod
    def _to_product_id(symbol: str) -> str:
        """Convert symbol to Coinbase product ID (e.g., BTC -> BTC-USD)."""
        if "-" in symbol:
            return symbol
        return f"{symbol}-USD"
