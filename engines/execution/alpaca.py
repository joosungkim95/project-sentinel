"""
Alpaca Platform Adapter — Equities and ETFs.

Connects to Alpaca's paper trading (or live) API for:
- US equities and ETFs
- Commission-free trading
- Real-time and historical market data

Docs: https://docs.alpaca.markets/
"""

import logging
from typing import Any, Optional
from datetime import datetime

from engines.models import (
    AssetClass,
    PositionInfo,
    RiskCheckResult,
    RiskDecision,
    Side,
    TradeResult,
)

logger = logging.getLogger(__name__)


class AlpacaAdapter:
    """
    Alpaca trading platform adapter.

    Handles connection, order placement, position management,
    and market data retrieval for US equities/ETFs.
    """

    platform_name = "alpaca"
    asset_class = AssetClass.EQUITIES
    is_paper = True

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        base_url: str = "https://paper-api.alpaca.markets",
    ):
        self.api_key = api_key
        self.secret_key = secret_key
        self.base_url = base_url
        self.is_paper = "paper" in base_url
        self._client = None  # Initialized in connect()

    async def connect(self) -> bool:
        """
        Connect to Alpaca and verify credentials.

        Uses alpaca-py SDK for all interactions.
        """
        try:
            from alpaca.trading.client import TradingClient
            from alpaca.data.historical import StockHistoricalDataClient

            self._trading_client = TradingClient(
                api_key=self.api_key,
                secret_key=self.secret_key,
                paper=self.is_paper,
            )
            self._data_client = StockHistoricalDataClient(
                api_key=self.api_key,
                secret_key=self.secret_key,
            )

            # Verify connection by fetching account
            account = self._trading_client.get_account()
            logger.info(
                "Alpaca connected: paper=%s, equity=$%s, buying_power=$%s",
                self.is_paper,
                account.equity,
                account.buying_power,
            )
            return True

        except Exception as e:
            logger.error("Alpaca connection failed: %s", e)
            return False

    async def execute_trade(
        self, risk_result: RiskCheckResult
    ) -> TradeResult:
        """
        Place an order on Alpaca.

        Uses limit orders by default to control slippage.
        Falls back to market order if no target price specified.
        """
        signal = risk_result.original_signal
        quantity = risk_result.approved_quantity or signal.quantity

        try:
            from alpaca.trading.requests import (
                LimitOrderRequest,
                MarketOrderRequest,
            )
            from alpaca.trading.enums import OrderSide, TimeInForce

            side = OrderSide.BUY if signal.side == Side.BUY else OrderSide.SELL

            if signal.target_price:
                order_request = LimitOrderRequest(
                    symbol=signal.symbol,
                    qty=quantity,
                    side=side,
                    time_in_force=TimeInForce.DAY,
                    limit_price=signal.target_price,
                )
            else:
                order_request = MarketOrderRequest(
                    symbol=signal.symbol,
                    qty=quantity,
                    side=side,
                    time_in_force=TimeInForce.DAY,
                )

            order = self._trading_client.submit_order(order_request)

            logger.info(
                "Alpaca order submitted: %s %s %s @ %s (id=%s)",
                signal.side.value,
                quantity,
                signal.symbol,
                signal.target_price or "market",
                order.id,
            )

            return TradeResult(
                trade_id=str(order.id),
                signal=signal,
                risk_check=risk_result,
                executed=True,
                fill_price=float(order.filled_avg_price) if order.filled_avg_price else None,
                fill_quantity=float(order.filled_qty) if order.filled_qty else None,
                commission=0.0,  # Alpaca is commission-free
                platform=self.platform_name,
            )

        except Exception as e:
            logger.error("Alpaca order failed: %s", e)
            return TradeResult(
                trade_id="",
                signal=signal,
                risk_check=risk_result,
                executed=False,
                error_message=str(e),
                platform=self.platform_name,
            )

    async def get_positions(self) -> list[PositionInfo]:
        """Get all open positions from Alpaca."""
        try:
            positions = self._trading_client.get_all_positions()
            return [
                PositionInfo(
                    symbol=p.symbol,
                    asset_class=AssetClass.EQUITIES,
                    side=Side.BUY if float(p.qty) > 0 else Side.SELL,
                    quantity=abs(float(p.qty)),
                    entry_price=float(p.avg_entry_price),
                    current_price=float(p.current_price),
                    unrealized_pnl=float(p.unrealized_pl),
                    pnl_pct=float(p.unrealized_plpc) * 100,
                    strategy_id="unknown",  # TODO: Track via trade journal
                )
                for p in positions
            ]
        except Exception as e:
            logger.error("Failed to get Alpaca positions: %s", e)
            return []

    async def get_account_value(self) -> float:
        """Get total account equity."""
        try:
            account = self._trading_client.get_account()
            return float(account.equity)
        except Exception as e:
            logger.error("Failed to get Alpaca account value: %s", e)
            return 0.0

    async def get_quote(self, symbol: str) -> dict[str, Any]:
        """Get latest quote for a symbol."""
        try:
            from alpaca.data.requests import StockLatestQuoteRequest

            quote = self._data_client.get_stock_latest_quote(
                StockLatestQuoteRequest(symbol_or_symbols=symbol)
            )
            q = quote[symbol]
            return {
                "bid": float(q.bid_price),
                "ask": float(q.ask_price),
                "bid_size": float(q.bid_size),
                "ask_size": float(q.ask_size),
                "timestamp": q.timestamp.isoformat(),
            }
        except Exception as e:
            logger.error("Failed to get quote for %s: %s", symbol, e)
            return {}

    async def close_position(
        self, symbol: str, quantity: Optional[float] = None
    ) -> dict[str, Any]:
        """
        Close a position on Alpaca.

        Args:
            symbol: Stock symbol to close.
            quantity: Shares to close. None = close all.

        Returns:
            Dict with execution details (order_id, executed, error).
        """
        try:
            if quantity:
                order = self._trading_client.close_position(
                    symbol, qty=str(quantity)
                )
            else:
                order = self._trading_client.close_position(symbol)

            logger.info("Closed position: %s (qty=%s)", symbol, quantity or "all")
            return {
                "order_id": str(order.id) if order else None,
                "executed": True,
                "symbol": symbol,
            }
        except Exception as e:
            logger.error("Failed to close position %s: %s", symbol, e)
            return {
                "executed": False,
                "symbol": symbol,
                "error": str(e),
            }

    async def cancel_all_orders(self) -> int:
        """Cancel all open orders."""
        try:
            self._trading_client.cancel_orders()
            logger.info("All Alpaca orders cancelled")
            return -1  # Alpaca doesn't return count
        except Exception as e:
            logger.error("Failed to cancel orders: %s", e)
            return 0

    async def get_historical_bars(
        self,
        symbol: str,
        timeframe: str = "1Day",
        limit: int = 100,
    ) -> list[dict]:
        """
        Get historical price bars for backtesting and indicators.

        Args:
            symbol: Stock symbol.
            timeframe: Bar size (1Min, 5Min, 15Min, 1Hour, 1Day).
            limit: Number of bars to fetch.

        Returns:
            List of bar dicts with open, high, low, close, volume.
        """
        try:
            from datetime import datetime, timedelta

            from alpaca.data.requests import StockBarsRequest
            from alpaca.data.timeframe import TimeFrame

            tf_map = {
                "1Min": TimeFrame.Minute,
                "5Min": TimeFrame(5, "Min"),
                "15Min": TimeFrame(15, "Min"),
                "1Hour": TimeFrame.Hour,
                "1Day": TimeFrame.Day,
            }

            # Use explicit date range — free tier needs it for > 1 bar
            # Trading days ~= calendar days * 5/7, so multiply by ~1.5
            tf = tf_map.get(timeframe, TimeFrame.Day)
            days_back = int(limit * 1.5) + 30 if tf == TimeFrame.Day else limit
            end = datetime.now()
            start = end - timedelta(days=days_back)

            request = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=tf,
                start=start,
                end=end,
            )
            bars = self._data_client.get_stock_bars(request)

            return [
                {
                    "timestamp": bar.timestamp.isoformat(),
                    "open": float(bar.open),
                    "high": float(bar.high),
                    "low": float(bar.low),
                    "close": float(bar.close),
                    "volume": int(bar.volume),
                }
                for bar in bars[symbol]
            ]
        except Exception as e:
            logger.error("Failed to get bars for %s: %s", symbol, e)
            return []
