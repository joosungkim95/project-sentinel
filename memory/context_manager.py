"""
Context Manager — Assembles focused context packages for each decision type.

This is the system's "brain." It maintains persistent state and feeds
the Claude API exactly what it needs for each specific decision.

The LLM is stateless. The system is stateful. This module bridges the gap.
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Optional

from engines.models import (
    MarketRegime,
    PortfolioSnapshot,
    RiskContext,
    StrategyContext,
    StrategyPerformance,
    TradeContext,
    Signal,
)

logger = logging.getLogger(__name__)


class ContextManager:
    """
    Assembles context packages for Claude API calls and system decisions.

    Each context type includes ONLY what's relevant to that decision,
    keeping token counts (and costs) low.
    """

    def __init__(self, db_session: Any):
        """
        Args:
            db_session: SQLAlchemy async session for querying historical data.
        """
        self.db = db_session

    async def build_strategy_context(
        self,
        current_portfolio: PortfolioSnapshot,
        market_regime: MarketRegime,
    ) -> StrategyContext:
        """
        Build context for generating new strategy hypotheses.

        Used by: Learning Engine (weekly slow loop)
        Model: Sonnet ($3/$15 per MTok)
        Target: <8K input tokens
        """
        top_strategies = await self._get_top_strategies(n=5, days=30)
        bottom_strategies = await self._get_bottom_strategies(n=5, days=30)
        recent_hypotheses = await self._get_recent_hypotheses(n=10)
        allocation = await self._get_allocation_by_asset()

        return StrategyContext(
            market_regime=market_regime,
            top_strategies=top_strategies,
            bottom_strategies=bottom_strategies,
            allocation_by_asset=allocation,
            recent_pnl_7d=current_portfolio.weekly_pnl,
            recent_pnl_30d=await self._get_pnl_over_days(30),
            recent_hypotheses=recent_hypotheses,
            portfolio_snapshot=current_portfolio,
        )

    async def build_trade_context(
        self,
        signal: Signal,
        current_portfolio: PortfolioSnapshot,
    ) -> TradeContext:
        """
        Build context for evaluating a specific trade signal.

        Used by: Strategy Engine (when Claude assists with evaluation)
        Model: Haiku ($1/$5 per MTok)
        Target: <4K input tokens
        """
        strategy_perf = await self._get_strategy_performance(
            signal.strategy_id, days=14
        )
        similar_trades = await self._get_similar_trades(
            signal.symbol, signal.side, n=5
        )
        market_conditions = await self._get_market_conditions(
            signal.asset_class
        )

        return TradeContext(
            signal=signal,
            strategy_recent_performance=strategy_perf,
            portfolio_snapshot=current_portfolio,
            similar_past_trades=similar_trades,
            market_conditions=market_conditions,
        )

    async def build_risk_context(
        self,
        current_portfolio: PortfolioSnapshot,
    ) -> RiskContext:
        """
        Build context for risk engine decisions.

        Used by: Risk Engine (for correlation analysis)
        Model: Usually not needed — risk rules are deterministic.
        """
        correlation_matrix = await self._build_correlation_matrix()
        recent_risk_events = await self._get_recent_risk_events(days=7)
        circuit_breaker_history = await self._get_circuit_breaker_history()

        return RiskContext(
            portfolio_snapshot=current_portfolio,
            correlation_matrix=correlation_matrix,
            recent_risk_events=recent_risk_events,
            circuit_breaker_history=circuit_breaker_history,
        )

    def to_prompt_text(self, context: Any, max_tokens: int = 4000) -> str:
        """
        Serialize a context object to a concise text format for Claude API.

        Optimizes for information density within the token budget.
        """
        data = context.model_dump() if hasattr(context, "model_dump") else context

        # Convert to compact but readable format
        text = self._format_for_prompt(data)

        # Rough token estimate (1 token ≈ 4 chars)
        estimated_tokens = len(text) // 4
        if estimated_tokens > max_tokens:
            logger.warning(
                "Context exceeds token budget: ~%d tokens (budget: %d). Truncating.",
                estimated_tokens,
                max_tokens,
            )
            text = text[: max_tokens * 4]

        return text

    # --- Private helpers ---

    def _format_for_prompt(self, data: Any, indent: int = 0) -> str:
        """Format data as compact, readable text (not JSON — saves tokens)."""
        if isinstance(data, dict):
            lines = []
            for key, value in data.items():
                formatted_value = self._format_for_prompt(value, indent + 1)
                if "\n" in formatted_value:
                    lines.append(f"{'  ' * indent}{key}:")
                    lines.append(formatted_value)
                else:
                    lines.append(
                        f"{'  ' * indent}{key}: {formatted_value}"
                    )
            return "\n".join(lines)
        elif isinstance(data, list):
            if not data:
                return "[]"
            items = [
                self._format_for_prompt(item, indent + 1) for item in data
            ]
            return "\n".join(f"{'  ' * indent}- {item}" for item in items)
        elif isinstance(data, datetime):
            return data.strftime("%Y-%m-%d %H:%M")
        else:
            return str(data)

    # --- DB query stubs (implement when DB is connected) ---

    async def _get_top_strategies(
        self, n: int, days: int
    ) -> list[StrategyPerformance]:
        """Query top N strategies by Sharpe ratio over period."""
        # TODO: Implement with actual DB query
        return []

    async def _get_bottom_strategies(
        self, n: int, days: int
    ) -> list[StrategyPerformance]:
        """Query bottom N strategies by P&L over period."""
        return []

    async def _get_recent_hypotheses(self, n: int) -> list[str]:
        """Get the last N strategy hypotheses tested."""
        return []

    async def _get_allocation_by_asset(self) -> dict[str, float]:
        """Get current allocation percentages by asset class."""
        return {}

    async def _get_pnl_over_days(self, days: int) -> float:
        """Calculate total P&L over a given number of days."""
        return 0.0

    async def _get_strategy_performance(
        self, strategy_id: str, days: int
    ) -> StrategyPerformance:
        """Get performance for a specific strategy."""
        return StrategyPerformance(
            strategy_id=strategy_id,
            period_days=days,
            trades_count=0,
            win_rate=0.0,
            total_pnl=0.0,
            max_drawdown=0.0,
            risk_budget_used_pct=0.0,
            status="paper_testing",
        )

    async def _get_similar_trades(
        self, symbol: str, side: str, n: int
    ) -> list[dict]:
        """Find similar past trades for comparison."""
        return []

    async def _get_market_conditions(
        self, asset_class: str
    ) -> dict[str, str]:
        """Get current market conditions for an asset class."""
        return {}

    async def _build_correlation_matrix(
        self,
    ) -> dict[str, dict[str, float]]:
        """Build correlation matrix across all current positions."""
        return {}

    async def _get_recent_risk_events(self, days: int) -> list[dict]:
        """Get recent risk events."""
        return []

    async def _get_circuit_breaker_history(self) -> list[dict]:
        """Get circuit breaker activation history."""
        return []
