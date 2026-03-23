"""
Execution Engine — Abstract adapter interface.

Each trading platform gets its own adapter that implements this interface.
The main Executor routes approved signals to the correct adapter.
"""

from abc import ABC, abstractmethod
from typing import Any

from engines.models import (
    AssetClass,
    PortfolioSnapshot,
    PositionInfo,
    RiskCheckResult,
    TradeResult,
)


class PlatformAdapter(ABC):
    """
    Abstract adapter for a trading platform.

    Implement one per platform: Alpaca, Coinbase, Polymarket, Kalshi.
    """

    @property
    @abstractmethod
    def platform_name(self) -> str:
        """Human-readable platform name."""
        ...

    @property
    @abstractmethod
    def asset_class(self) -> AssetClass:
        """Which asset class this adapter handles."""
        ...

    @abstractmethod
    async def connect(self) -> bool:
        """
        Establish connection and verify credentials.

        Returns:
            True if connection is healthy.
        """
        ...

    @abstractmethod
    async def execute_trade(
        self, approved_signal: RiskCheckResult
    ) -> TradeResult:
        """
        Execute an approved trade on the platform.

        Args:
            approved_signal: Risk-approved signal with final quantity.

        Returns:
            TradeResult with fill details or error info.
        """
        ...

    @abstractmethod
    async def get_positions(self) -> list[PositionInfo]:
        """Get all open positions on this platform."""
        ...

    @abstractmethod
    async def get_account_value(self) -> float:
        """Get total account value (cash + positions)."""
        ...

    @abstractmethod
    async def close_position(self, symbol: str) -> TradeResult:
        """Close a specific position."""
        ...

    @abstractmethod
    async def close_all_positions(self) -> list[TradeResult]:
        """Emergency: close all positions on this platform."""
        ...

    @abstractmethod
    async def get_quote(self, symbol: str) -> dict[str, Any]:
        """Get current quote for a symbol."""
        ...

    async def health_check(self) -> bool:
        """Check if the platform connection is healthy."""
        try:
            await self.get_account_value()
            return True
        except Exception:
            return False


class Executor:
    """
    Routes approved signals to the correct platform adapter.
    """

    def __init__(self):
        self._adapters: dict[AssetClass, PlatformAdapter] = {}

    def register_adapter(self, adapter: PlatformAdapter) -> None:
        """Register a platform adapter for an asset class."""
        self._adapters[adapter.asset_class] = adapter

    async def execute(self, approved_signal: RiskCheckResult) -> TradeResult:
        """
        Execute an approved trade on the appropriate platform.

        Args:
            approved_signal: Must have decision == APPROVED or REDUCED.

        Returns:
            TradeResult with execution details.
        """
        asset_class = approved_signal.original_signal.asset_class
        adapter = self._adapters.get(asset_class)

        if adapter is None:
            return TradeResult(
                trade_id="",
                signal=approved_signal.original_signal,
                risk_check=approved_signal,
                executed=False,
                platform="unknown",
                error_message=f"No adapter registered for {asset_class.value}",
            )

        return await adapter.execute_trade(approved_signal)

    async def get_portfolio_snapshot(self) -> PortfolioSnapshot:
        """Aggregate portfolio across all platforms."""
        total_value = 0.0
        total_cash = 0.0
        all_positions: dict[str, PositionInfo] = {}
        risk_utilization: dict[str, float] = {}

        for asset_class, adapter in self._adapters.items():
            try:
                value = await adapter.get_account_value()
                positions = await adapter.get_positions()

                total_value += value
                positions_value = sum(
                    p.quantity * p.current_price for p in positions
                )

                for pos in positions:
                    all_positions[pos.symbol] = pos

                risk_utilization[asset_class.value] = (
                    (positions_value / total_value * 100)
                    if total_value > 0
                    else 0.0
                )
            except Exception:
                continue  # Log and continue — one platform down shouldn't break all

        total_cash = total_value - sum(
            p.quantity * p.current_price for p in all_positions.values()
        )

        return PortfolioSnapshot(
            total_value=total_value,
            cash=total_cash,
            positions=all_positions,
            risk_utilization=risk_utilization,
            daily_pnl=0.0,   # TODO: calculate from trade history
            weekly_pnl=0.0,
            total_pnl=0.0,
            drawdown_from_peak=0.0,  # TODO: track peak value
        )

    async def health_check_all(self) -> dict[str, bool]:
        """Check health of all registered adapters."""
        results = {}
        for asset_class, adapter in self._adapters.items():
            results[adapter.platform_name] = await adapter.health_check()
        return results
