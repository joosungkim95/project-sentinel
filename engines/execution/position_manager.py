"""
Position Manager — Tracks open positions and evaluates exit criteria.

Runs on each pipeline cycle to close positions that hit stop-loss,
take-profit, or match a strategy's SELL signal. Updates the trade
record with exit details and realized P&L.

Exit conditions (checked in order):
1. Stop-loss: current price <= entry_price * (1 - stop_pct)
2. Take-profit: current price >= entry_price * (1 + take_profit_pct)
3. Strategy SELL signal: strategy generated a sell for this symbol
4. Max hold time: position open longer than max_hold_hours (optional)
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from data.models import TradeRecord
from data.repositories.trades import close_trade, get_open_trades
from engines.alerts import alert_trade_executed
from engines.models import AssetClass, Signal, Side

logger = logging.getLogger(__name__)

# Default exit parameters when strategy doesn't specify
DEFAULT_STOP_LOSS_PCT = 5.0     # 5% stop-loss
DEFAULT_TAKE_PROFIT_PCT = 10.0  # 10% take-profit
DEFAULT_MAX_HOLD_HOURS = 168    # 7 days


class PositionManager:
    """Tracks open positions and closes them when exit criteria are met."""

    def __init__(
        self,
        db_session: AsyncSession,
        adapters: dict[AssetClass, Any],
        stop_loss_pct: float = DEFAULT_STOP_LOSS_PCT,
        take_profit_pct: float = DEFAULT_TAKE_PROFIT_PCT,
        max_hold_hours: float = DEFAULT_MAX_HOLD_HOURS,
    ):
        self._session = db_session
        self._adapters = adapters
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.max_hold_hours = max_hold_hours

    async def check_exits(
        self,
        asset_class: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Check all open positions for exit conditions.

        Args:
            asset_class: Only check positions in this asset class.

        Returns:
            List of dicts with details of closed positions.
        """
        open_trades = await get_open_trades(
            self._session, asset_class=asset_class,
        )
        if not open_trades:
            return []

        closed: list[dict[str, Any]] = []

        for trade in open_trades:
            result = await self._check_single_exit(trade)
            if result:
                closed.append(result)

        return closed

    async def close_for_sell_signal(
        self, signal: Signal,
    ) -> dict[str, Any] | None:
        """
        Close an open position matching a strategy's SELL signal.

        Looks for an open BUY trade from the same strategy and symbol.
        Returns close details or None if no matching position found.
        """
        open_trades = await get_open_trades(
            self._session,
            strategy_id=signal.strategy_id,
            symbol=signal.symbol,
        )
        if not open_trades:
            return None

        # Close the oldest matching position
        trade = open_trades[0]
        current_price = await self._get_current_price(trade)
        if current_price is None:
            current_price = signal.target_price or trade.price

        return await self._close_position(
            trade, current_price, f"strategy_sell: {signal.rationale[:80]}",
        )

    async def _check_single_exit(
        self, trade: TradeRecord,
    ) -> dict[str, Any] | None:
        """Check exit conditions for a single open trade."""
        current_price = await self._get_current_price(trade)
        if current_price is None:
            return None

        entry_price = trade.price

        # Stop-loss
        stop_price = entry_price * (1 - self.stop_loss_pct / 100)
        if current_price <= stop_price:
            return await self._close_position(
                trade, current_price,
                f"stop_loss: price {current_price:.2f} <= stop {stop_price:.2f}",
            )

        # Take-profit
        tp_price = entry_price * (1 + self.take_profit_pct / 100)
        if current_price >= tp_price:
            return await self._close_position(
                trade, current_price,
                f"take_profit: price {current_price:.2f} >= target {tp_price:.2f}",
            )

        # Max hold time
        if trade.entry_time:
            hold_duration = datetime.now(timezone.utc) - trade.entry_time
            if hold_duration > timedelta(hours=self.max_hold_hours):
                return await self._close_position(
                    trade, current_price,
                    f"max_hold: held {hold_duration.total_seconds() / 3600:.0f}h"
                    f" > {self.max_hold_hours}h limit",
                )

        return None

    async def _close_position(
        self,
        trade: TradeRecord,
        exit_price: float,
        reason: str,
    ) -> dict[str, Any]:
        """Close a position: update DB record and log."""
        pnl = (exit_price - trade.price) * trade.quantity
        pnl_pct = (exit_price - trade.price) / trade.price * 100 if trade.price > 0 else 0

        await close_trade(self._session, trade.id, exit_price)
        await self._session.commit()

        logger.info(
            "Position CLOSED: %s %s @ %.2f → %.2f (pnl=%.4f / %.2f%%) [%s]",
            trade.strategy_id,
            trade.symbol,
            trade.price,
            exit_price,
            pnl,
            pnl_pct,
            reason,
        )

        # Send Discord alert
        await alert_trade_executed(
            symbol=trade.symbol,
            side="sell",
            quantity=trade.quantity,
            price=exit_price,
            strategy=trade.strategy_id,
            platform=trade.platform or "unknown",
        )

        return {
            "trade_id": trade.id,
            "strategy_id": trade.strategy_id,
            "symbol": trade.symbol,
            "entry_price": trade.price,
            "exit_price": exit_price,
            "pnl": round(pnl, 4),
            "pnl_pct": round(pnl_pct, 2),
            "reason": reason,
        }

    async def _get_current_price(
        self, trade: TradeRecord,
    ) -> float | None:
        """Get current price for a trade's symbol via the platform adapter."""
        ac = AssetClass(trade.asset_class)
        adapter = self._adapters.get(ac)
        if adapter is None:
            return None

        try:
            quote = await adapter.get_quote(trade.symbol)
            return quote.get("price")
        except Exception as e:
            logger.warning(
                "Failed to get price for %s: %s", trade.symbol, e,
            )
            return None
