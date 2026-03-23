"""
Trading Pipeline — Wires all engines together.

This is the main orchestrator that runs the signal → risk → execute → learn loop.
It's intentionally simple — complexity belongs in the engines, not the glue.
"""

import logging
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from data.repositories.trades import insert_rejected_signal, insert_trade
from engines.alerts import (
    alert_circuit_breaker,
    alert_risk_event,
    alert_trade_executed,
    alert_system_error,
)
from engines.execution.base import Executor
from engines.models import (
    MarketRegime,
    RiskDecision,
    Signal,
    TradeResult,
)
from engines.risk.engine import RiskEngine
from engines.strategy.base import Strategy

logger = logging.getLogger(__name__)


class TradingPipeline:
    """
    Orchestrates the full trading loop:
    1. Strategies generate signals
    2. Risk Engine evaluates each signal
    3. Approved signals are executed
    4. All outcomes are logged for the Learning Engine
    """

    def __init__(
        self,
        risk_engine: RiskEngine,
        executor: Executor,
        strategies: list[Strategy],
        db_session: AsyncSession | None = None,
    ):
        self.risk_engine = risk_engine
        self.executor = executor
        self.strategies = strategies
        self._db_session = db_session
        self._trade_log: list[dict[str, Any]] = []

    async def run_cycle(self, market_regime: MarketRegime) -> list[TradeResult]:
        """
        Run one full trading cycle across all active strategies.

        Returns:
            List of trade results (executed and rejected).
        """
        results: list[TradeResult] = []
        portfolio = await self.executor.get_portfolio_snapshot()

        for strategy in self.strategies:
            if strategy.status.value not in ("active", "paper_testing"):
                continue

            try:
                # 1. Generate signals
                signals = await strategy.generate_signals(
                    market_data={},  # TODO: pass actual market data
                    market_regime=market_regime,
                )

                for signal in signals:
                    # 2. Risk check
                    risk_result = self.risk_engine.evaluate(signal, portfolio)

                    if risk_result.decision == RiskDecision.REJECTED:
                        logger.info(
                            "Signal REJECTED: %s %s %s — %s",
                            signal.side.value,
                            signal.symbol,
                            signal.strategy_id,
                            risk_result.rejection_reasons,
                        )
                        await alert_risk_event(
                            event_type="Signal Rejected",
                            details=(
                                f"{signal.strategy_id}: {signal.side.value} "
                                f"{signal.symbol} — "
                                f"{', '.join(risk_result.rejection_reasons)}"
                            ),
                            portfolio_value=portfolio.total_value,
                        )
                        await self._log_outcome(signal, risk_result, None)
                        continue

                    # 3. Execute
                    trade_result = await self.executor.execute(risk_result)
                    results.append(trade_result)

                    if trade_result.executed:
                        logger.info(
                            "Trade EXECUTED: %s %s @ %s (strategy: %s)",
                            signal.side.value,
                            signal.symbol,
                            trade_result.fill_price,
                            signal.strategy_id,
                        )
                        await alert_trade_executed(
                            symbol=signal.symbol,
                            side=signal.side.value,
                            quantity=trade_result.fill_quantity or 0,
                            price=trade_result.fill_price or 0,
                            strategy=signal.strategy_id,
                        )
                    else:
                        logger.warning(
                            "Trade FAILED: %s %s — %s",
                            signal.side.value,
                            signal.symbol,
                            trade_result.error_message,
                        )

                    await self._log_outcome(signal, risk_result, trade_result)

                    # Refresh portfolio after each trade
                    portfolio = await self.executor.get_portfolio_snapshot()

                    # Check if circuit breaker should activate
                    if self._should_activate_circuit_breaker(portfolio):
                        self.risk_engine.activate_circuit_breaker()
                        await alert_circuit_breaker(
                            reason="Automated circuit breaker trigger",
                            portfolio_value=portfolio.total_value,
                            daily_pnl=portfolio.daily_pnl,
                        )
                        return results

            except Exception as e:
                logger.error(
                    "Error in strategy %s: %s",
                    strategy.strategy_id,
                    e,
                    exc_info=True,
                )
                await alert_system_error(
                    error=str(e),
                    component=f"Strategy: {strategy.strategy_id}",
                )

        return results

    def _should_activate_circuit_breaker(self, portfolio) -> bool:
        """Check if conditions warrant activating the circuit breaker."""
        # This is a secondary check — the Risk Engine also checks on each signal.
        # This catches scenarios where rapid losses happen between signals.
        max_drawdown = 100.0 - self.risk_engine.config.hard_floor_pct
        return portfolio.drawdown_from_peak >= max_drawdown

    async def _log_outcome(self, signal, risk_result, trade_result) -> None:
        """Log the full outcome for the Learning Engine to ingest."""
        self._trade_log.append({
            "timestamp": datetime.utcnow().isoformat(),
            "signal": signal.model_dump(),
            "risk_decision": risk_result.decision.value,
            "risk_reasons": risk_result.rejection_reasons,
            "executed": trade_result.executed if trade_result else False,
            "fill_price": trade_result.fill_price if trade_result else None,
            "error": trade_result.error_message if trade_result else None,
        })

        if self._db_session is not None:
            if trade_result is not None and trade_result.executed:
                await insert_trade(self._db_session, trade_result)
            elif risk_result.decision == RiskDecision.REJECTED:
                await insert_rejected_signal(self._db_session, risk_result)
            await self._db_session.commit()

    def get_trade_log(self) -> list[dict[str, Any]]:
        """Get the trade log for the Learning Engine."""
        return self._trade_log
