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
    ):
        self.strategy_id = strategy_id
        self.asset_class = asset_class
        self.parameters = parameters
        self.status: StrategyStatus = StrategyStatus.PAPER_TESTING

    @abstractmethod
    async def generate_signals(
        self,
        market_data: dict[str, Any],
        market_regime: MarketRegime,
    ) -> list[Signal]:
        """
        Analyze market data and generate trade signals.

        Args:
            market_data: Current market data (prices, volumes, indicators).
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
