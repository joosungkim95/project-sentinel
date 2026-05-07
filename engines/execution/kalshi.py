"""
Kalshi Platform Adapter — Prediction markets (US-regulated).

Connects to Kalshi's API for:
- Event-based prediction markets
- Yes/No binary contracts
- CFTC-regulated, legal in the US

Docs: https://docs.kalshi.com
"""

import base64
import logging
import time
from typing import Any

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from engines.models import (
    AssetClass,
    PositionInfo,
    RiskCheckResult,
    Side,
    TradeResult,
)

logger = logging.getLogger(__name__)


def _to_float(value: Any) -> float:
    """Coerce Kalshi's stringified numeric fields (e.g. "0.8400") to float."""
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


class KalshiAdapter:
    is_paper = False

    async def real_money_value(self) -> float:
        """Kalshi balance is real even when observe_only blocks orders."""
        return await self.get_account_value()

    """
    Kalshi prediction market adapter.

    Handles authentication via RSA-PSS signed requests,
    market data, order placement, and position tracking.
    """

    platform_name = "kalshi"
    asset_class = AssetClass.PREDICTIONS

    def __init__(
        self,
        api_key: str,
        private_key_pem: str,
        base_url: str = "https://demo-api.kalshi.co",
        observe_only: bool = False,
    ):
        """
        Initialize Kalshi adapter.

        Args:
            api_key: API Key ID (UUID format).
            private_key_pem: RSA private key in PEM format.
            base_url: API base URL (demo or production).
            observe_only: If True, use live data but simulate fills.
        """
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.api_base = f"{self.base_url}/trade-api/v2"
        self._client = httpx.AsyncClient(timeout=30.0)
        self.observe_only = observe_only

        # Parse the PEM private key
        pem = private_key_pem.replace("\\n", "\n").encode()
        self._private_key = serialization.load_pem_private_key(pem, password=None)

    def _sign_request(
        self, method: str, path: str
    ) -> dict[str, str]:
        """
        Generate authentication headers for a request.

        Args:
            method: HTTP method (GET, POST, DELETE).
            path: Request path (e.g., /trade-api/v2/portfolio/balance).

        Returns:
            Dict of authentication headers.
        """
        timestamp = str(int(time.time() * 1000))
        path_clean = path.split("?")[0]
        message = f"{timestamp}{method}{path_clean}".encode()

        signature = self._private_key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )

        return {
            "KALSHI-ACCESS-KEY": self.api_key,
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode(),
            "Content-Type": "application/json",
        }

    async def _get(self, path: str, params: dict | None = None) -> dict:
        """Make authenticated GET request."""
        url = f"{self.api_base}{path}"
        headers = self._sign_request("GET", f"/trade-api/v2{path}")
        resp = await self._client.get(url, headers=headers, params=params)
        resp.raise_for_status()
        return resp.json()

    async def _post(self, path: str, body: dict) -> dict:
        """Make authenticated POST request."""
        url = f"{self.api_base}{path}"
        headers = self._sign_request("POST", f"/trade-api/v2{path}")
        resp = await self._client.post(url, headers=headers, json=body)
        resp.raise_for_status()
        return resp.json()

    async def connect(self) -> bool:
        """
        Verify Kalshi credentials by fetching account balance.

        Returns:
            True if authentication succeeds.
        """
        try:
            data = await self._get("/portfolio/balance")
            balance_cents = data.get("balance", 0)
            portfolio_cents = data.get("portfolio_value", 0)
            logger.info(
                "Kalshi connected: balance=$%.2f, portfolio=$%.2f",
                balance_cents / 100,
                portfolio_cents / 100,
            )
            return True
        except Exception as e:
            logger.error("Kalshi connection failed: %s", e)
            return False

    async def get_account_value(self) -> float:
        """Get total account value in dollars."""
        try:
            data = await self._get("/portfolio/balance")
            balance = data.get("balance", 0)
            portfolio = data.get("portfolio_value", 0)
            return (balance + portfolio) / 100
        except Exception as e:
            logger.error("Failed to get Kalshi balance: %s", e)
            return 0.0

    async def get_positions(self) -> list[PositionInfo]:
        """Get all open positions."""
        try:
            data = await self._get("/portfolio/positions")
            positions = []
            for pos in data.get("market_positions", []):
                qty = pos.get("position", 0)
                if qty == 0:
                    continue
                side = Side.BUY if qty > 0 else Side.SELL
                # Price is in cents
                market_price = pos.get("market_price", 50) / 100
                positions.append(
                    PositionInfo(
                        symbol=pos.get("ticker", ""),
                        asset_class=AssetClass.PREDICTIONS,
                        side=side,
                        quantity=abs(qty),
                        entry_price=market_price,
                        current_price=market_price,
                        unrealized_pnl=0.0,
                        pnl_pct=0.0,
                        strategy_id="unknown",
                    )
                )
            return positions
        except Exception as e:
            logger.error("Failed to get Kalshi positions: %s", e)
            return []

    async def get_quote(self, ticker: str) -> dict[str, Any]:
        """
        Get current market data for a ticker.

        Args:
            ticker: Kalshi market ticker (e.g., "KXBTCD-25MAR21-B70500").
        """
        try:
            data = await self._get(f"/markets/{ticker}")
            market = data.get("market", {})
            return {
                "ticker": market.get("ticker"),
                "title": market.get("title"),
                "yes_price": _to_float(market.get("yes_bid_dollars")),
                "no_price": _to_float(market.get("no_bid_dollars")),
                "yes_ask": _to_float(market.get("yes_ask_dollars")),
                "no_ask": _to_float(market.get("no_ask_dollars")),
                "volume": _to_float(market.get("volume_fp")),
                "open_interest": _to_float(market.get("open_interest_fp")),
                "status": market.get("status"),
            }
        except Exception as e:
            logger.error("Failed to get quote for %s: %s", ticker, e)
            return {}

    async def get_events(
        self, limit: int = 10, status: str = "open"
    ) -> list[dict]:
        """
        Get available events/markets.

        Args:
            limit: Max events to return.
            status: Filter by status ("open", "closed", etc.).

        Returns:
            List of event dicts with title, ticker, markets.
        """
        try:
            data = await self._get(
                "/events", params={"limit": limit, "status": status}
            )
            return [
                {
                    "event_ticker": e.get("event_ticker"),
                    "title": e.get("title"),
                    "category": e.get("category"),
                    "markets_count": len(e.get("markets", [])),
                }
                for e in data.get("events", [])
            ]
        except Exception as e:
            logger.error("Failed to get events: %s", e)
            return []

    async def get_markets(
        self, event_ticker: str | None = None, limit: int = 20
    ) -> list[dict]:
        """
        Get available markets, optionally filtered by event.

        Returns:
            List of market dicts with ticker, title, prices.
        """
        try:
            params: dict[str, Any] = {"limit": limit, "status": "open"}
            if event_ticker:
                params["event_ticker"] = event_ticker
            data = await self._get("/markets", params=params)
            return [
                {
                    "ticker": m.get("ticker"),
                    "title": m.get("title"),
                    "yes_bid": _to_float(m.get("yes_bid_dollars")),
                    "no_bid": _to_float(m.get("no_bid_dollars")),
                    "yes_ask": _to_float(m.get("yes_ask_dollars")),
                    "no_ask": _to_float(m.get("no_ask_dollars")),
                    "volume": _to_float(m.get("volume_fp")),
                    "open_interest": _to_float(m.get("open_interest_fp")),
                    "status": m.get("status"),
                }
                for m in data.get("markets", [])
            ]
        except Exception as e:
            logger.error("Failed to get markets: %s", e)
            return []

    async def get_crypto_markets(
        self, series_ticker: str = "KXBTC", limit: int = 50,
    ) -> list[dict]:
        """Get crypto price-level markets filtered by series ticker.

        Returns markets with full pricing data including close_time and
        strike_price for probability model consumption.

        Args:
            series_ticker: Kalshi series ticker to filter by (e.g., "KXBTC", "KXETH").
            limit: Maximum number of markets to return.

        Returns:
            List of market dicts with prices converted from cents to dollars,
            including yes_bid, no_bid, yes_ask, no_ask, close_time, strike_price.
        """
        try:
            params: dict[str, Any] = {
                "limit": limit, "status": "open", "series_ticker": series_ticker,
            }
            data = await self._get("/markets", params=params)
            return [
                {
                    "ticker": m.get("ticker"),
                    "title": m.get("title"),
                    "yes_bid": _to_float(m.get("yes_bid_dollars")),
                    "no_bid": _to_float(m.get("no_bid_dollars")),
                    "yes_ask": _to_float(m.get("yes_ask_dollars")),
                    "no_ask": _to_float(m.get("no_ask_dollars")),
                    "volume": _to_float(m.get("volume_fp")),
                    "open_interest": _to_float(m.get("open_interest_fp")),
                    "status": m.get("status"),
                    "close_time": m.get("close_time"),
                    # New API: floor_strike is the threshold for "greater"/"less" binary markets.
                    "strike_price": m.get("floor_strike"),
                }
                for m in data.get("markets", [])
            ]
        except Exception as e:
            logger.error("Failed to get crypto markets (series=%s): %s", series_ticker, e)
            return []

    async def execute_trade(
        self, risk_result: RiskCheckResult
    ) -> TradeResult:
        """
        Place an order on Kalshi.

        Prediction markets use yes/no sides and prices in cents.
        The signal's side (BUY/SELL) maps to action, and we default
        to buying "yes" contracts.
        """
        signal = risk_result.original_signal
        count = int(risk_result.approved_quantity or signal.quantity)

        try:
            body: dict[str, Any] = {
                "ticker": signal.symbol,
                "action": "buy" if signal.side == Side.BUY else "sell",
                "side": "yes",
                "count": max(count, 1),
                "type": "market",
            }

            if signal.target_price:
                # Convert dollar price to cents
                body["type"] = "limit"
                body["yes_price"] = int(signal.target_price * 100)

            data = await self._post("/portfolio/orders", body)
            order = data.get("order", {})
            order_id = order.get("order_id", "")

            logger.info(
                "Kalshi order submitted: %s %s %s x%d (id=%s)",
                body["action"],
                body["side"],
                signal.symbol,
                count,
                order_id,
            )

            return TradeResult(
                trade_id=order_id,
                signal=signal,
                risk_check=risk_result,
                executed=True,
                fill_price=(order.get("avg_price", 0) or 0) / 100,
                fill_quantity=float(order.get("fill_count", count)),
                commission=0.0,
                platform=self.platform_name,
            )
        except Exception as e:
            logger.error("Kalshi order failed: %s", e)
            return TradeResult(
                trade_id="",
                signal=signal,
                risk_check=risk_result,
                executed=False,
                error_message=str(e),
                platform=self.platform_name,
            )

    async def health_check(self) -> bool:
        """Check if Kalshi connection is healthy."""
        try:
            await self._get("/portfolio/balance")
            return True
        except Exception:
            return False
