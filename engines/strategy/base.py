"""
Abstract Strategy base class.

All strategies must subclass this and implement generate_signals().
This enforces a consistent interface that the pipeline depends on.

Adding a new strategy:
1. Create a new file in the appropriate asset class directory
2. Subclass Strategy
3. Implement generate_signals() and get_performance()
4. Register it in config/strategies.py
5. Write tests in tests/unit/test_strategies.py
"""

from abc import ABC, abstractmethod
from typing import Any

from config.tiers import StrategyTier
from engines.models import (
    AssetClass,
    MarketRegime,
    Signal,
    StrategyPerformance,
    StrategyStatus,
)


class Strategy(ABC):
    """Abstract base class for all trading strategies."""

    def __init__(
        self,
        strategy_id: str,
        asset_class: AssetClass,
        parameters: dict[str, Any],
        tier: StrategyTier = StrategyTier.CORE,
        symbols: list[str] | None = None,
        timeframe: str = "1Day",
        max_signals_per_cycle: int = 3,
    ):
        self.strategy_id = strategy_id
        self.asset_class = asset_class
        self.parameters = parameters
        self.tier = tier
        self.symbols = symbols if symbols is not None else [parameters.get("symbol", "")]
        self.timeframe = timeframe
        self.max_signals_per_cycle = max_signals_per_cycle
        self.status: StrategyStatus = StrategyStatus.PAPER_TESTING

    @abstractmethod
    async def generate_signals(
        self,
        bars: dict[str, list[dict]],
        market_regime: MarketRegime,
    ) -> list[Signal]:
        """
        Generate trading signals from bar data.

        Args:
            bars: Bar data keyed by symbol. Each value is a list of
                OHLCV dicts sorted oldest-first.
            market_regime: Current classified market regime.

        Returns:
            List of Signal objects (may be empty if no opportunities).
        """
        ...

    @abstractmethod
    async def get_performance(self, period_days: int) -> StrategyPerformance:
        """
        Calculate performance metrics over a given period.

        Args:
            period_days: Number of days to look back.

        Returns:
            StrategyPerformance with all metrics populated.
        """
        ...

    def update_parameters(self, new_params: dict[str, Any]) -> None:
        """Update strategy parameters (called by Learning Engine)."""
        self.parameters.update(new_params)

    def disable(self, reason: str) -> None:
        """Disable this strategy and move to graveyard."""
        self.status = StrategyStatus.GRAVEYARD

    def activate(self) -> None:
        """Promote from paper testing to active."""
        self.status = StrategyStatus.ACTIVE

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__} id={self.strategy_id} "
            f"asset={self.asset_class.value} status={self.status.value}>"
        )
