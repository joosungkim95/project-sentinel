"""
Learning Engine — Fast Loop (Daily).

Runs once per day after market close. Pure math, no Claude API calls.

Responsibilities:
1. Aggregate today's trades per strategy → compute performance metrics
2. Persist daily strategy_performance records
3. Update market regime classifications
4. Send daily performance summary alert
5. Auto-adjust strategy parameters based on recent performance

Cost: $0 (no LLM calls)
"""

import logging
import math
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from data.models import (
    MarketRegimeRecord,
    StrategyPerformanceRecord,
    TradeRecord,
)
from engines.alerts import alert_daily_summary, send_alert, AlertLevel
from engines.models import AssetClass, MarketRegime
from memory.market_regime import MarketRegimeTracker

logger = logging.getLogger(__name__)


class FastLoop:
    """
    Daily learning loop — computes metrics and adjusts parameters.

    Runs after market close each day. No LLM calls, just math.
    """

    def __init__(self, db_session: AsyncSession):
        self.db = db_session
        self.regime_tracker = MarketRegimeTracker(db_session)

    async def run(self) -> dict:
        """
        Execute the full daily fast loop.

        Returns:
            Summary dict with metrics for each strategy.
        """
        logger.info("Fast loop starting — daily performance aggregation")

        today = date.today()
        strategy_ids = await self._get_active_strategy_ids(today)

        if not strategy_ids:
            logger.info("No trades today — skipping fast loop")
            return {"date": today.isoformat(), "strategies": {}}

        summaries: dict[str, dict] = {}

        for strategy_id in strategy_ids:
            metrics = await self._compute_daily_metrics(strategy_id, today)
            await self._persist_performance(strategy_id, today, metrics)
            summaries[strategy_id] = metrics

        # Update market regimes based on today's data
        await self._update_regimes()

        # Send daily summary alert
        await self._send_summary(today, summaries)

        await self.db.commit()

        logger.info(
            "Fast loop complete: %d strategies evaluated", len(summaries)
        )
        return {"date": today.isoformat(), "strategies": summaries}

    async def _get_active_strategy_ids(self, today: date) -> list[str]:
        """Get distinct strategy IDs that traded today."""
        start = datetime.combine(today, datetime.min.time()).replace(
            tzinfo=timezone.utc
        )
        end = start + timedelta(days=1)

        stmt = (
            select(TradeRecord.strategy_id)
            .where(
                TradeRecord.created_at >= start,
                TradeRecord.created_at < end,
            )
            .distinct()
        )
        result = await self.db.execute(stmt)
        return [row[0] for row in result.all()]

    async def _compute_daily_metrics(
        self, strategy_id: str, today: date
    ) -> dict:
        """
        Compute performance metrics for a strategy on a given day.

        Returns:
            Dict with trades_count, win_rate, total_pnl, sharpe_ratio,
            max_drawdown, risk_budget_used.
        """
        start = datetime.combine(today, datetime.min.time()).replace(
            tzinfo=timezone.utc
        )
        end = start + timedelta(days=1)

        # Get all trades for this strategy today
        stmt = (
            select(TradeRecord)
            .where(
                TradeRecord.strategy_id == strategy_id,
                TradeRecord.created_at >= start,
                TradeRecord.created_at < end,
            )
            .order_by(TradeRecord.created_at)
        )
        result = await self.db.execute(stmt)
        trades = list(result.scalars().all())

        approved = [t for t in trades if t.risk_check_result == "approved"]
        closed = [t for t in approved if t.pnl is not None]
        wins = [t for t in closed if (t.pnl or 0) > 0]

        pnl_values = [t.pnl for t in closed if t.pnl is not None]
        total_pnl = sum(pnl_values)

        # Sharpe ratio (annualized from daily return)
        sharpe = self._compute_sharpe(pnl_values)

        # Max drawdown from cumulative P&L
        max_dd = self._compute_max_drawdown(pnl_values)

        # Average risk utilization
        risk_utils = [t.risk_utilization_pct for t in approved]
        avg_risk = sum(risk_utils) / len(risk_utils) if risk_utils else 0.0

        return {
            "trades_count": len(approved),
            "win_rate": len(wins) / len(closed) if closed else 0.0,
            "total_pnl": total_pnl,
            "sharpe_ratio": sharpe,
            "max_drawdown": max_dd,
            "risk_budget_used": avg_risk,
        }

    @staticmethod
    def _compute_sharpe(pnl_values: list[float]) -> float | None:
        """
        Compute Sharpe ratio from a list of P&L values.

        Uses daily returns, annualized with sqrt(252).
        Returns None if insufficient data.
        """
        if len(pnl_values) < 2:
            return None

        mean = sum(pnl_values) / len(pnl_values)
        variance = sum((p - mean) ** 2 for p in pnl_values) / len(pnl_values)
        std = math.sqrt(variance)

        if std == 0:
            return None

        # Annualized: daily Sharpe * sqrt(252)
        daily_sharpe = mean / std
        return round(daily_sharpe * math.sqrt(252), 4)

    @staticmethod
    def _compute_max_drawdown(pnl_values: list[float]) -> float:
        """
        Compute max drawdown from cumulative P&L series.

        Returns drawdown as a positive number (e.g., 5.0 means 5% drawdown).
        """
        if not pnl_values:
            return 0.0

        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0

        for pnl in pnl_values:
            cumulative += pnl
            if cumulative > peak:
                peak = cumulative
            drawdown = peak - cumulative
            if drawdown > max_dd:
                max_dd = drawdown

        return round(max_dd, 4)

    async def _persist_performance(
        self, strategy_id: str, today: date, metrics: dict
    ) -> None:
        """Write daily performance record to the database."""
        record = StrategyPerformanceRecord(
            strategy_id=strategy_id,
            date=today,
            trades_count=metrics["trades_count"],
            win_rate=metrics["win_rate"],
            total_pnl=metrics["total_pnl"],
            sharpe_ratio=metrics["sharpe_ratio"],
            max_drawdown=metrics["max_drawdown"],
            risk_budget_used=metrics["risk_budget_used"],
        )
        self.db.add(record)
        await self.db.flush()
        logger.info(
            "Persisted performance for %s on %s: pnl=%.2f, wr=%.1f%%",
            strategy_id, today, metrics["total_pnl"],
            metrics["win_rate"] * 100,
        )

    async def _update_regimes(self) -> None:
        """
        Update market regime for each asset class based on recent data.

        Uses a simple heuristic: look at the distribution of regimes
        recorded in today's trades. In future, this will use indicator
        data (VIX, ADX, etc.) for more sophisticated classification.
        """
        for asset_class in AssetClass:
            regime, confidence = await self._classify_regime(asset_class)
            if regime != MarketRegime.UNKNOWN:
                changed = await self.regime_tracker.update_regime(
                    asset_class, regime, confidence
                )
                if changed:
                    logger.info(
                        "Regime changed: %s → %s (%.0f%% confidence)",
                        asset_class.value, regime.value, confidence * 100,
                    )

    async def _classify_regime(
        self, asset_class: AssetClass
    ) -> tuple[MarketRegime, float]:
        """
        Classify current market regime for an asset class.

        Simple heuristic based on recent trade outcomes:
        - Mostly winning buys → trending_up
        - Mostly winning sells → trending_down
        - Mixed results → ranging
        - High loss rate → high_volatility

        Returns:
            Tuple of (regime, confidence).
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=3)
        stmt = (
            select(TradeRecord)
            .where(
                TradeRecord.asset_class == asset_class.value,
                TradeRecord.created_at >= cutoff,
                TradeRecord.pnl.isnot(None),
                TradeRecord.risk_check_result == "approved",
            )
        )
        result = await self.db.execute(stmt)
        trades = list(result.scalars().all())

        if len(trades) < 3:
            return MarketRegime.UNKNOWN, 0.0

        buy_wins = sum(
            1 for t in trades if t.side == "buy" and (t.pnl or 0) > 0
        )
        sell_wins = sum(
            1 for t in trades if t.side == "sell" and (t.pnl or 0) > 0
        )
        total_wins = buy_wins + sell_wins
        win_rate = total_wins / len(trades)
        loss_rate = 1 - win_rate

        # High loss rate → volatility
        if loss_rate > 0.7:
            return MarketRegime.HIGH_VOLATILITY, min(loss_rate, 0.95)

        # Strong buy wins → trending up
        if buy_wins > sell_wins * 2 and win_rate > 0.5:
            confidence = min(buy_wins / len(trades), 0.95)
            return MarketRegime.TRENDING_UP, confidence

        # Strong sell wins → trending down
        if sell_wins > buy_wins * 2 and win_rate > 0.5:
            confidence = min(sell_wins / len(trades), 0.95)
            return MarketRegime.TRENDING_DOWN, confidence

        # Mixed → ranging
        return MarketRegime.RANGING, 0.6

    async def _send_summary(
        self, today: date, summaries: dict[str, dict]
    ) -> None:
        """Send a daily performance summary to Discord."""
        total_pnl = sum(s["total_pnl"] for s in summaries.values())
        total_trades = sum(s["trades_count"] for s in summaries.values())
        closed_with_wr = [
            s for s in summaries.values() if s["trades_count"] > 0
        ]
        avg_wr = (
            sum(s["win_rate"] for s in closed_with_wr) / len(closed_with_wr)
            if closed_with_wr
            else 0.0
        )

        # Find top strategy by P&L
        if summaries:
            top = max(summaries.items(), key=lambda x: x[1]["total_pnl"])
            top_strategy = f"{top[0]} (${top[1]['total_pnl']:,.2f})"
        else:
            top_strategy = "N/A"

        # Get portfolio value from latest snapshot
        from data.repositories.portfolio import get_latest_snapshot
        snapshot = await get_latest_snapshot(self.db)
        portfolio_value = snapshot.total_value if snapshot else 0.0

        await alert_daily_summary(
            portfolio_value=portfolio_value,
            daily_pnl=total_pnl,
            trades_count=total_trades,
            win_rate=avg_wr * 100,
            top_strategy=top_strategy,
        )
