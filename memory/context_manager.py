"""
Context Manager — Assembles focused context packages for each decision type.

This is the system's "brain." It maintains persistent state and feeds
the Claude API exactly what it needs for each specific decision.

The LLM is stateless. The system is stateful. This module bridges the gap.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from data.models import (
    MarketRegimeRecord,
    PortfolioSnapshotRecord,
    RiskEventRecord,
    StrategyHypothesisRecord,
    StrategyPerformanceRecord,
    TradeRecord,
)
from engines.models import (
    LearningContext,
    MarketRegime,
    PortfolioSnapshot,
    RiskContext,
    StrategyContext,
    StrategyPerformance,
    StrategyStatus,
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

    def __init__(self, db_session: AsyncSession):
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

    async def build_learning_context(
        self,
        current_portfolio: PortfolioSnapshot,
        period_days: int = 7,
    ) -> LearningContext:
        """
        Build context for the weekly learning review loop.

        Used by: Learning Engine (weekly slow loop)
        Model: Sonnet ($3/$15 per MTok)
        Target: <8K input tokens
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=period_days)

        performances = await self._get_all_strategy_performances(days=period_days)
        regime_history = await self._get_regime_history(days=period_days)
        param_changes = await self._get_parameter_changes(days=period_days)
        graveyard = await self._get_strategy_graveyard()
        best = await self._get_best_trade(since=cutoff)
        worst = await self._get_worst_trade(since=cutoff)
        total_pnl = await self._get_pnl_over_days(period_days)

        return LearningContext(
            strategy_performances=performances,
            market_regime_history=regime_history,
            parameter_change_history=param_changes,
            strategy_graveyard=graveyard,
            portfolio_snapshot=current_portfolio,
            total_pnl_period=total_pnl,
            best_trade=best,
            worst_trade=worst,
            period_days=period_days,
        )

    def to_prompt_text(self, context: object, max_tokens: int = 4000) -> str:
        """
        Serialize a context object to a concise text format for Claude API.

        Optimizes for information density within the token budget.
        """
        data = context.model_dump() if hasattr(context, "model_dump") else context

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

    # --- Private helpers: formatting ---

    def _format_for_prompt(self, data: object, indent: int = 0) -> str:
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

    # --- Private helpers: DB queries ---

    async def _get_top_strategies(
        self, n: int, days: int
    ) -> list[StrategyPerformance]:
        """Query top N strategies by Sharpe ratio over period."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        stmt = (
            select(
                StrategyPerformanceRecord.strategy_id,
                func.avg(StrategyPerformanceRecord.sharpe_ratio).label("avg_sharpe"),
                func.sum(StrategyPerformanceRecord.trades_count).label("total_trades"),
                func.avg(StrategyPerformanceRecord.win_rate).label("avg_win_rate"),
                func.sum(StrategyPerformanceRecord.total_pnl).label("sum_pnl"),
                func.max(StrategyPerformanceRecord.max_drawdown).label("max_dd"),
                func.avg(StrategyPerformanceRecord.risk_budget_used).label("avg_risk"),
            )
            .where(StrategyPerformanceRecord.created_at >= cutoff)
            .group_by(StrategyPerformanceRecord.strategy_id)
            .order_by(func.avg(StrategyPerformanceRecord.sharpe_ratio).desc().nulls_last())
            .limit(n)
        )
        result = await self.db.execute(stmt)
        rows = result.all()
        return [
            StrategyPerformance(
                strategy_id=r.strategy_id,
                period_days=days,
                trades_count=int(r.total_trades or 0),
                win_rate=float(r.avg_win_rate or 0),
                total_pnl=float(r.sum_pnl or 0),
                sharpe_ratio=float(r.avg_sharpe) if r.avg_sharpe else None,
                max_drawdown=float(r.max_dd or 0),
                risk_budget_used_pct=float(r.avg_risk or 0),
                status=StrategyStatus.ACTIVE,
            )
            for r in rows
        ]

    async def _get_bottom_strategies(
        self, n: int, days: int
    ) -> list[StrategyPerformance]:
        """Query bottom N strategies by P&L over period."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        stmt = (
            select(
                StrategyPerformanceRecord.strategy_id,
                func.avg(StrategyPerformanceRecord.sharpe_ratio).label("avg_sharpe"),
                func.sum(StrategyPerformanceRecord.trades_count).label("total_trades"),
                func.avg(StrategyPerformanceRecord.win_rate).label("avg_win_rate"),
                func.sum(StrategyPerformanceRecord.total_pnl).label("sum_pnl"),
                func.max(StrategyPerformanceRecord.max_drawdown).label("max_dd"),
                func.avg(StrategyPerformanceRecord.risk_budget_used).label("avg_risk"),
            )
            .where(StrategyPerformanceRecord.created_at >= cutoff)
            .group_by(StrategyPerformanceRecord.strategy_id)
            .order_by(func.sum(StrategyPerformanceRecord.total_pnl).asc())
            .limit(n)
        )
        result = await self.db.execute(stmt)
        rows = result.all()
        return [
            StrategyPerformance(
                strategy_id=r.strategy_id,
                period_days=days,
                trades_count=int(r.total_trades or 0),
                win_rate=float(r.avg_win_rate or 0),
                total_pnl=float(r.sum_pnl or 0),
                sharpe_ratio=float(r.avg_sharpe) if r.avg_sharpe else None,
                max_drawdown=float(r.max_dd or 0),
                risk_budget_used_pct=float(r.avg_risk or 0),
                status=StrategyStatus.ACTIVE,
            )
            for r in rows
        ]

    async def _get_recent_hypotheses(self, n: int) -> list[str]:
        """Get the last N strategy hypotheses tested."""
        stmt = (
            select(StrategyHypothesisRecord.hypothesis_text)
            .order_by(StrategyHypothesisRecord.created_at.desc())
            .limit(n)
        )
        result = await self.db.execute(stmt)
        return [row[0] for row in result.all()]

    async def _get_allocation_by_asset(self) -> dict[str, float]:
        """Get current allocation percentages by asset class from latest snapshot."""
        stmt = (
            select(PortfolioSnapshotRecord)
            .order_by(PortfolioSnapshotRecord.created_at.desc())
            .limit(1)
        )
        result = await self.db.execute(stmt)
        snapshot = result.scalar_one_or_none()
        if not snapshot or not snapshot.positions:
            return {}

        # Sum position values by asset class
        totals: dict[str, float] = {}
        for _symbol, pos in snapshot.positions.items():
            ac = pos.get("asset_class", "unknown")
            value = pos.get("current_price", 0) * pos.get("quantity", 0)
            totals[ac] = totals.get(ac, 0) + value

        total = sum(totals.values())
        if total == 0:
            return {}
        return {ac: round(v / total * 100, 2) for ac, v in totals.items()}

    async def _get_pnl_over_days(self, days: int) -> float:
        """Calculate total realized P&L over a given number of days."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        stmt = select(func.coalesce(func.sum(TradeRecord.pnl), 0.0)).where(
            TradeRecord.created_at >= cutoff,
            TradeRecord.pnl.isnot(None),
        )
        result = await self.db.execute(stmt)
        return float(result.scalar_one())

    async def _get_strategy_performance(
        self, strategy_id: str, days: int
    ) -> StrategyPerformance:
        """Get aggregated performance for a specific strategy."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        stmt = (
            select(
                func.sum(StrategyPerformanceRecord.trades_count).label("total_trades"),
                func.avg(StrategyPerformanceRecord.win_rate).label("avg_win_rate"),
                func.sum(StrategyPerformanceRecord.total_pnl).label("sum_pnl"),
                func.avg(StrategyPerformanceRecord.sharpe_ratio).label("avg_sharpe"),
                func.max(StrategyPerformanceRecord.max_drawdown).label("max_dd"),
                func.avg(StrategyPerformanceRecord.risk_budget_used).label("avg_risk"),
            )
            .where(
                StrategyPerformanceRecord.strategy_id == strategy_id,
                StrategyPerformanceRecord.created_at >= cutoff,
            )
        )
        result = await self.db.execute(stmt)
        row = result.one()

        return StrategyPerformance(
            strategy_id=strategy_id,
            period_days=days,
            trades_count=int(row.total_trades or 0),
            win_rate=float(row.avg_win_rate or 0),
            total_pnl=float(row.sum_pnl or 0),
            sharpe_ratio=float(row.avg_sharpe) if row.avg_sharpe else None,
            max_drawdown=float(row.max_dd or 0),
            risk_budget_used_pct=float(row.avg_risk or 0),
            status=StrategyStatus.ACTIVE,
        )

    async def _get_similar_trades(
        self, symbol: str, side: str, n: int
    ) -> list[dict]:
        """Find similar past trades for comparison."""
        stmt = (
            select(TradeRecord)
            .where(TradeRecord.symbol == symbol, TradeRecord.side == side)
            .order_by(TradeRecord.created_at.desc())
            .limit(n)
        )
        result = await self.db.execute(stmt)
        return [
            {
                "symbol": t.symbol,
                "side": t.side,
                "price": t.price,
                "pnl": t.pnl,
                "pnl_pct": t.pnl_pct,
                "strategy_id": t.strategy_id,
                "confidence": t.signal_confidence,
                "regime": t.market_regime,
                "date": t.created_at.isoformat() if t.created_at else None,
            }
            for t in result.scalars().all()
        ]

    async def _get_market_conditions(
        self, asset_class: str
    ) -> dict[str, str]:
        """Get current market conditions for an asset class."""
        # Get the most recent regime for this asset class
        ac_value = asset_class.value if hasattr(asset_class, "value") else asset_class
        stmt = (
            select(MarketRegimeRecord)
            .where(MarketRegimeRecord.asset_class == ac_value)
            .order_by(MarketRegimeRecord.started_at.desc())
            .limit(1)
        )
        result = await self.db.execute(stmt)
        regime = result.scalar_one_or_none()
        if not regime:
            return {"regime": "unknown", "confidence": "0"}

        conditions: dict[str, str] = {
            "regime": regime.regime_type,
            "confidence": str(regime.confidence),
            "since": regime.started_at.isoformat() if regime.started_at else "unknown",
        }
        if regime.indicators:
            for k, v in regime.indicators.items():
                conditions[k] = str(v)
        return conditions

    async def _build_correlation_matrix(
        self,
    ) -> dict[str, dict[str, float]]:
        """Build correlation matrix across all current positions."""
        # Get the latest portfolio snapshot for position list
        stmt = (
            select(PortfolioSnapshotRecord)
            .order_by(PortfolioSnapshotRecord.created_at.desc())
            .limit(1)
        )
        result = await self.db.execute(stmt)
        snapshot = result.scalar_one_or_none()
        if not snapshot or not snapshot.positions:
            return {}

        symbols = list(snapshot.positions.keys())
        if len(symbols) < 2:
            return {}

        # Get recent P&L series for each symbol to compute correlations
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        matrix: dict[str, dict[str, float]] = {}

        # Fetch daily P&L for each symbol
        pnl_series: dict[str, list[float]] = {}
        for sym in symbols:
            stmt_trades = (
                select(TradeRecord.pnl)
                .where(
                    TradeRecord.symbol == sym,
                    TradeRecord.created_at >= cutoff,
                    TradeRecord.pnl.isnot(None),
                )
                .order_by(TradeRecord.created_at)
            )
            res = await self.db.execute(stmt_trades)
            pnl_series[sym] = [float(r[0]) for r in res.all()]

        # Simple pairwise correlation (Pearson)
        for s1 in symbols:
            matrix[s1] = {}
            for s2 in symbols:
                if s1 == s2:
                    matrix[s1][s2] = 1.0
                elif len(pnl_series.get(s1, [])) >= 3 and len(pnl_series.get(s2, [])) >= 3:
                    matrix[s1][s2] = self._pearson(
                        pnl_series[s1], pnl_series[s2]
                    )
                else:
                    matrix[s1][s2] = 0.0

        return matrix

    @staticmethod
    def _pearson(x: list[float], y: list[float]) -> float:
        """Compute Pearson correlation between two series (truncated to min length)."""
        n = min(len(x), len(y))
        if n < 3:
            return 0.0
        x, y = x[:n], y[:n]
        mx = sum(x) / n
        my = sum(y) / n
        cov = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
        sx = sum((xi - mx) ** 2 for xi in x) ** 0.5
        sy = sum((yi - my) ** 2 for yi in y) ** 0.5
        if sx == 0 or sy == 0:
            return 0.0
        return round(cov / (sx * sy), 4)

    async def _get_recent_risk_events(self, days: int) -> list[dict]:
        """Get recent risk events."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        stmt = (
            select(RiskEventRecord)
            .where(RiskEventRecord.created_at >= cutoff)
            .order_by(RiskEventRecord.created_at.desc())
            .limit(20)
        )
        result = await self.db.execute(stmt)
        return [
            {
                "type": e.event_type,
                "severity": e.severity,
                "details": e.details,
                "portfolio_value": e.portfolio_value_at_event,
                "action": e.action_taken,
                "date": e.created_at.isoformat() if e.created_at else None,
            }
            for e in result.scalars().all()
        ]

    async def _get_circuit_breaker_history(self) -> list[dict]:
        """Get circuit breaker activation history."""
        stmt = (
            select(RiskEventRecord)
            .where(RiskEventRecord.event_type == "circuit_breaker")
            .order_by(RiskEventRecord.created_at.desc())
            .limit(10)
        )
        result = await self.db.execute(stmt)
        return [
            {
                "severity": e.severity,
                "details": e.details,
                "portfolio_value": e.portfolio_value_at_event,
                "action": e.action_taken,
                "date": e.created_at.isoformat() if e.created_at else None,
            }
            for e in result.scalars().all()
        ]

    # --- Learning context helpers ---

    async def _get_all_strategy_performances(
        self, days: int
    ) -> list[StrategyPerformance]:
        """Get performance for all strategies over period."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        stmt = (
            select(
                StrategyPerformanceRecord.strategy_id,
                func.sum(StrategyPerformanceRecord.trades_count).label("total_trades"),
                func.avg(StrategyPerformanceRecord.win_rate).label("avg_win_rate"),
                func.sum(StrategyPerformanceRecord.total_pnl).label("sum_pnl"),
                func.avg(StrategyPerformanceRecord.sharpe_ratio).label("avg_sharpe"),
                func.max(StrategyPerformanceRecord.max_drawdown).label("max_dd"),
                func.avg(StrategyPerformanceRecord.risk_budget_used).label("avg_risk"),
            )
            .where(StrategyPerformanceRecord.created_at >= cutoff)
            .group_by(StrategyPerformanceRecord.strategy_id)
            .order_by(func.sum(StrategyPerformanceRecord.total_pnl).desc())
        )
        result = await self.db.execute(stmt)
        return [
            StrategyPerformance(
                strategy_id=r.strategy_id,
                period_days=days,
                trades_count=int(r.total_trades or 0),
                win_rate=float(r.avg_win_rate or 0),
                total_pnl=float(r.sum_pnl or 0),
                sharpe_ratio=float(r.avg_sharpe) if r.avg_sharpe else None,
                max_drawdown=float(r.max_dd or 0),
                risk_budget_used_pct=float(r.avg_risk or 0),
                status=StrategyStatus.ACTIVE,
            )
            for r in result.all()
        ]

    async def _get_regime_history(self, days: int) -> list[dict]:
        """Get market regime changes over period."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        stmt = (
            select(MarketRegimeRecord)
            .where(MarketRegimeRecord.started_at >= cutoff)
            .order_by(MarketRegimeRecord.started_at.desc())
        )
        result = await self.db.execute(stmt)
        return [
            {
                "asset_class": r.asset_class,
                "regime": r.regime_type,
                "confidence": r.confidence,
                "started": r.started_at.isoformat() if r.started_at else None,
                "ended": r.ended_at.isoformat() if r.ended_at else None,
            }
            for r in result.scalars().all()
        ]

    async def _get_parameter_changes(self, days: int) -> list[dict]:
        """Get strategy parameter changes over period (from performance records)."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        stmt = (
            select(StrategyPerformanceRecord)
            .where(
                StrategyPerformanceRecord.created_at >= cutoff,
                StrategyPerformanceRecord.parameters.isnot(None),
            )
            .order_by(StrategyPerformanceRecord.created_at.desc())
        )
        result = await self.db.execute(stmt)
        return [
            {
                "strategy_id": r.strategy_id,
                "date": r.date.isoformat() if r.date else None,
                "parameters": r.parameters,
                "pnl": r.total_pnl,
            }
            for r in result.scalars().all()
        ]

    async def _get_strategy_graveyard(self) -> list[dict]:
        """Get disabled/graveyarded strategy hypotheses."""
        stmt = (
            select(StrategyHypothesisRecord)
            .where(StrategyHypothesisRecord.status.in_(["disabled", "graveyard"]))
            .order_by(StrategyHypothesisRecord.updated_at.desc())
            .limit(10)
        )
        result = await self.db.execute(stmt)
        return [
            {
                "hypothesis": h.hypothesis_text,
                "source": h.source,
                "regime": h.market_regime,
                "backtest_sharpe": h.backtest_sharpe,
                "paper_pnl": h.paper_trade_pnl,
                "status": h.status,
            }
            for h in result.scalars().all()
        ]

    async def _get_best_trade(self, since: datetime) -> dict | None:
        """Get the best trade (highest P&L) since cutoff."""
        stmt = (
            select(TradeRecord)
            .where(TradeRecord.created_at >= since, TradeRecord.pnl.isnot(None))
            .order_by(TradeRecord.pnl.desc())
            .limit(1)
        )
        result = await self.db.execute(stmt)
        t = result.scalar_one_or_none()
        if not t:
            return None
        return {
            "symbol": t.symbol,
            "side": t.side,
            "pnl": t.pnl,
            "strategy_id": t.strategy_id,
            "regime": t.market_regime,
        }

    async def _get_worst_trade(self, since: datetime) -> dict | None:
        """Get the worst trade (lowest P&L) since cutoff."""
        stmt = (
            select(TradeRecord)
            .where(TradeRecord.created_at >= since, TradeRecord.pnl.isnot(None))
            .order_by(TradeRecord.pnl.asc())
            .limit(1)
        )
        result = await self.db.execute(stmt)
        t = result.scalar_one_or_none()
        if not t:
            return None
        return {
            "symbol": t.symbol,
            "side": t.side,
            "pnl": t.pnl,
            "strategy_id": t.strategy_id,
            "regime": t.market_regime,
        }
