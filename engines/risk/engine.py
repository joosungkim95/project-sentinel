"""
Risk Engine — The immovable guardrail.

This engine has ABSOLUTE VETO AUTHORITY over all trades. No other engine
can bypass, modify, or influence its rules. It runs as an independent
evaluation layer that every signal must pass through.

Interface:
    RiskEngine.evaluate(signal, portfolio) → RiskCheckResult
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

from engines.models import (
    AssetClass,
    PortfolioSnapshot,
    RiskCheckResult,
    RiskDecision,
    Signal,
)
from engines.risk.rules import (
    RiskRule,
    HardFloorRule,
    PositionSizeRule,
    AssetClassConcentrationRule,
    DailyLossCircuitBreaker,
    WeeklyDrawdownRule,
    CorrelationRule,
)

logger = logging.getLogger(__name__)


class RiskEngine:
    """
    Evaluates trade signals against all active risk rules.

    Rules are evaluated in priority order. If ANY rule rejects,
    the trade is rejected. Rules can also reduce position size.
    """

    def __init__(self, config: "RiskConfig"):
        self.config = config
        self.rules: list[RiskRule] = self._initialize_rules()
        self._circuit_breaker_active = False
        self._circuit_breaker_until: Optional[datetime] = None
        logger.info(
            "Risk Engine initialized with %d rules", len(self.rules)
        )

    def _initialize_rules(self) -> list[RiskRule]:
        """Initialize rules in priority order (most restrictive first)."""
        return [
            HardFloorRule(
                floor_pct=self.config.hard_floor_pct,
            ),
            DailyLossCircuitBreaker(
                max_daily_loss_pct=self.config.max_daily_loss_pct,
            ),
            WeeklyDrawdownRule(
                max_weekly_drawdown_pct=self.config.max_weekly_drawdown_pct,
                reduction_factor=self.config.drawdown_reduction_factor,
            ),
            PositionSizeRule(
                max_position_pct=self.config.max_position_pct,
            ),
            AssetClassConcentrationRule(
                max_asset_class_pct=self.config.max_asset_class_pct,
            ),
            CorrelationRule(
                max_correlated_exposure_pct=self.config.max_correlated_exposure_pct,
            ),
        ]

    def evaluate(
        self,
        signal: Signal,
        portfolio: PortfolioSnapshot,
    ) -> RiskCheckResult:
        """
        Evaluate a trade signal against all risk rules.

        Args:
            signal: The proposed trade signal from Strategy Engine.
            portfolio: Current portfolio state.

        Returns:
            RiskCheckResult with approval, rejection, or size reduction.
        """
        # Check circuit breaker first
        if self._is_circuit_breaker_active():
            logger.warning("Circuit breaker active — rejecting all trades")
            return RiskCheckResult(
                decision=RiskDecision.REJECTED,
                original_signal=signal,
                rejection_reasons=["Circuit breaker active until "
                                   f"{self._circuit_breaker_until}"],
                risk_utilization_pct=0.0,
                portfolio_value=portfolio.total_value,
            )

        rejection_reasons: list[str] = []
        approved_quantity = signal.quantity

        for rule in self.rules:
            result = rule.check(signal, portfolio, approved_quantity)

            if result.rejected:
                rejection_reasons.append(result.reason)
                logger.info(
                    "Signal REJECTED by %s: %s",
                    rule.__class__.__name__,
                    result.reason,
                )
                # Hard rules stop evaluation immediately
                if rule.is_hard_rule:
                    return RiskCheckResult(
                        decision=RiskDecision.REJECTED,
                        original_signal=signal,
                        rejection_reasons=rejection_reasons,
                        risk_utilization_pct=self._calc_risk_utilization(
                            signal, portfolio
                        ),
                        portfolio_value=portfolio.total_value,
                    )

            elif result.reduced_quantity is not None:
                approved_quantity = min(
                    approved_quantity, result.reduced_quantity
                )
                logger.info(
                    "Signal REDUCED by %s: %s → %s",
                    rule.__class__.__name__,
                    signal.quantity,
                    approved_quantity,
                )

        # If any soft rules rejected, reject the trade
        if rejection_reasons:
            return RiskCheckResult(
                decision=RiskDecision.REJECTED,
                original_signal=signal,
                rejection_reasons=rejection_reasons,
                risk_utilization_pct=self._calc_risk_utilization(
                    signal, portfolio
                ),
                portfolio_value=portfolio.total_value,
            )

        # Determine if size was reduced
        decision = (
            RiskDecision.REDUCED
            if approved_quantity < signal.quantity
            else RiskDecision.APPROVED
        )

        return RiskCheckResult(
            decision=decision,
            original_signal=signal,
            approved_quantity=approved_quantity,
            risk_utilization_pct=self._calc_risk_utilization(
                signal, portfolio, approved_quantity
            ),
            portfolio_value=portfolio.total_value,
        )

    def activate_circuit_breaker(self, duration_hours: int = 24) -> None:
        """Activate the circuit breaker to halt all trading."""
        self._circuit_breaker_active = True
        self._circuit_breaker_until = (
            datetime.utcnow() + timedelta(hours=duration_hours)
        )
        logger.critical(
            "CIRCUIT BREAKER ACTIVATED until %s",
            self._circuit_breaker_until,
        )

    def _is_circuit_breaker_active(self) -> bool:
        """Check if circuit breaker is currently active."""
        if not self._circuit_breaker_active:
            return False
        if (
            self._circuit_breaker_until
            and datetime.utcnow() > self._circuit_breaker_until
        ):
            self._circuit_breaker_active = False
            self._circuit_breaker_until = None
            logger.info("Circuit breaker expired — trading resumed")
            return False
        return True

    def _calc_risk_utilization(
        self,
        signal: Signal,
        portfolio: PortfolioSnapshot,
        quantity: Optional[float] = None,
    ) -> float:
        """Calculate what percentage of risk budget this trade uses."""
        qty = quantity or signal.quantity
        price = signal.target_price or 0.0
        trade_value = qty * price
        if portfolio.total_value == 0:
            return 100.0
        return (trade_value / portfolio.total_value) * 100.0
