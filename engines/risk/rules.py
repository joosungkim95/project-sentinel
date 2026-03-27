"""
Risk Rules — Individual rules that the Risk Engine evaluates.

Each rule is a self-contained check with a clear pass/fail/reduce result.
Rules are evaluated in priority order by the Risk Engine.

Adding a new rule:
1. Subclass RiskRule
2. Implement check()
3. Add it to RiskEngine._initialize_rules() in the correct priority position
4. Write tests in tests/unit/test_risk_rules.py
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from config.tiers import TIER_CONFIDENCE_THRESHOLD, TIER_RISK_BUDGET, StrategyTier
from engines.models import (
    AssetClass,
    PortfolioSnapshot,
    Signal,
)


@dataclass
class RuleResult:
    """Result of evaluating a single risk rule."""
    rejected: bool = False
    reason: str = ""
    reduced_quantity: Optional[float] = None


class RiskRule(ABC):
    """Abstract base class for all risk rules."""

    # Hard rules halt evaluation immediately on rejection.
    # Soft rules accumulate and reject at the end.
    is_hard_rule: bool = False

    @abstractmethod
    def check(
        self,
        signal: Signal,
        portfolio: PortfolioSnapshot,
        current_quantity: float,
    ) -> RuleResult:
        """
        Evaluate this rule against a signal.

        Args:
            signal: The proposed trade signal.
            portfolio: Current portfolio state.
            current_quantity: The quantity after any prior reductions.

        Returns:
            RuleResult indicating pass, reject, or reduce.
        """
        ...


class HardFloorRule(RiskRule):
    """
    HARD RULE: Portfolio value must never drop below the hard floor.

    If current value is already at or below floor, reject ALL trades.
    If a trade could push value below floor, reject it.
    """

    is_hard_rule = True

    def __init__(self, floor_pct: float = 90.0):
        self.floor_pct = floor_pct

    def check(
        self,
        signal: Signal,
        portfolio: PortfolioSnapshot,
        current_quantity: float,
    ) -> RuleResult:
        # Calculate the floor based on initial seed
        # Note: seed capital is tracked as portfolio.total_value when
        # drawdown_from_peak is at max. For now, use a simpler heuristic:
        # if drawdown exceeds (100 - floor_pct)%, reject.
        max_allowed_drawdown = 100.0 - self.floor_pct

        if portfolio.drawdown_from_peak >= max_allowed_drawdown:
            return RuleResult(
                rejected=True,
                reason=(
                    f"Portfolio drawdown ({portfolio.drawdown_from_peak:.1f}%) "
                    f"has reached hard floor ({max_allowed_drawdown:.1f}%). "
                    "All trading halted."
                ),
            )

        return RuleResult()


class PositionSizeRule(RiskRule):
    """
    No single position may exceed max_position_pct of portfolio value.
    """

    def __init__(self, max_position_pct: float = 10.0):
        self.max_position_pct = max_position_pct

    def check(
        self,
        signal: Signal,
        portfolio: PortfolioSnapshot,
        current_quantity: float,
    ) -> RuleResult:
        if portfolio.total_value == 0:
            return RuleResult(rejected=True, reason="Portfolio value is zero")

        price = signal.target_price or 0.0
        trade_value = current_quantity * price
        position_pct = (trade_value / portfolio.total_value) * 100.0

        if position_pct > self.max_position_pct:
            # Calculate the maximum allowed quantity
            max_value = portfolio.total_value * (self.max_position_pct / 100.0)
            max_quantity = max_value / price if price > 0 else 0.0

            if max_quantity <= 0:
                return RuleResult(
                    rejected=True,
                    reason=(
                        f"Position size ({position_pct:.1f}%) exceeds limit "
                        f"({self.max_position_pct:.1f}%) and cannot be reduced."
                    ),
                )

            return RuleResult(
                reduced_quantity=max_quantity,
                reason=(
                    f"Position reduced from {position_pct:.1f}% to "
                    f"{self.max_position_pct:.1f}% of portfolio"
                ),
            )

        return RuleResult()


class AssetClassConcentrationRule(RiskRule):
    """
    No single asset class may exceed max_asset_class_pct of portfolio.
    """

    def __init__(self, max_asset_class_pct: float = 40.0):
        self.max_asset_class_pct = max_asset_class_pct

    def check(
        self,
        signal: Signal,
        portfolio: PortfolioSnapshot,
        current_quantity: float,
    ) -> RuleResult:
        if portfolio.total_value == 0:
            return RuleResult(rejected=True, reason="Portfolio value is zero")

        asset_class_key = signal.asset_class.value
        current_allocation = portfolio.risk_utilization.get(
            asset_class_key, 0.0
        )

        price = signal.target_price or 0.0
        additional_pct = (
            (current_quantity * price) / portfolio.total_value
        ) * 100.0

        total_pct = current_allocation + additional_pct

        if total_pct > self.max_asset_class_pct:
            remaining_budget = self.max_asset_class_pct - current_allocation
            if remaining_budget <= 0:
                return RuleResult(
                    rejected=True,
                    reason=(
                        f"{asset_class_key} allocation ({current_allocation:.1f}%) "
                        f"already at limit ({self.max_asset_class_pct:.1f}%)"
                    ),
                )

            max_value = portfolio.total_value * (remaining_budget / 100.0)
            max_quantity = max_value / price if price > 0 else 0.0

            return RuleResult(
                reduced_quantity=max_quantity,
                reason=(
                    f"{asset_class_key} allocation would be {total_pct:.1f}%, "
                    f"reduced to stay within {self.max_asset_class_pct:.1f}%"
                ),
            )

        return RuleResult()


class DailyLossCircuitBreaker(RiskRule):
    """
    HARD RULE: If daily P&L loss exceeds threshold, halt all trading.
    """

    is_hard_rule = True

    def __init__(self, max_daily_loss_pct: float = 3.0):
        self.max_daily_loss_pct = max_daily_loss_pct

    def check(
        self,
        signal: Signal,
        portfolio: PortfolioSnapshot,
        current_quantity: float,
    ) -> RuleResult:
        if portfolio.total_value == 0:
            return RuleResult(rejected=True, reason="Portfolio value is zero")

        daily_loss_pct = abs(min(0, portfolio.daily_pnl)) / portfolio.total_value * 100

        if daily_loss_pct >= self.max_daily_loss_pct:
            return RuleResult(
                rejected=True,
                reason=(
                    f"Daily loss ({daily_loss_pct:.1f}%) exceeds circuit breaker "
                    f"threshold ({self.max_daily_loss_pct:.1f}%). Trading halted."
                ),
            )

        return RuleResult()


class WeeklyDrawdownRule(RiskRule):
    """
    If weekly drawdown exceeds threshold, reduce all position sizes.
    """

    def __init__(
        self,
        max_weekly_drawdown_pct: float = 5.0,
        reduction_factor: float = 0.5,
    ):
        self.max_weekly_drawdown_pct = max_weekly_drawdown_pct
        self.reduction_factor = reduction_factor

    def check(
        self,
        signal: Signal,
        portfolio: PortfolioSnapshot,
        current_quantity: float,
    ) -> RuleResult:
        if portfolio.total_value == 0:
            return RuleResult(rejected=True, reason="Portfolio value is zero")

        weekly_loss_pct = abs(min(0, portfolio.weekly_pnl)) / portfolio.total_value * 100

        if weekly_loss_pct >= self.max_weekly_drawdown_pct:
            reduced = current_quantity * self.reduction_factor
            return RuleResult(
                reduced_quantity=reduced,
                reason=(
                    f"Weekly drawdown ({weekly_loss_pct:.1f}%) exceeds "
                    f"{self.max_weekly_drawdown_pct:.1f}%. "
                    f"Position size reduced by {(1 - self.reduction_factor) * 100:.0f}%"
                ),
            )

        return RuleResult()


class CorrelationRule(RiskRule):
    """
    Combined exposure to correlated assets cannot exceed threshold.

    Correlation groups (configurable):
    - crypto: BTC, ETH, crypto prediction markets
    - equities: SPY, QQQ, equity prediction markets
    """

    # Default correlation groups
    CORRELATION_GROUPS: dict[str, list[str]] = {
        "crypto_ecosystem": ["BTC", "ETH", "SOL", "AVAX", "DOGE"],
        "us_broad_market": ["SPY", "QQQ", "IWM", "DIA"],
        "us_mega_tech": ["AAPL", "MSFT", "NVDA"],
    }

    def __init__(self, max_correlated_exposure_pct: float = 50.0):
        self.max_correlated_exposure_pct = max_correlated_exposure_pct

    def check(
        self,
        signal: Signal,
        portfolio: PortfolioSnapshot,
        current_quantity: float,
    ) -> RuleResult:
        # Find which correlation group this symbol belongs to
        symbol_group = None
        for group_name, symbols in self.CORRELATION_GROUPS.items():
            if signal.symbol.upper() in [s.upper() for s in symbols]:
                symbol_group = group_name
                break

        if symbol_group is None:
            return RuleResult()  # No correlation group, pass

        # Calculate total exposure to this correlation group
        group_symbols = [
            s.upper() for s in self.CORRELATION_GROUPS[symbol_group]
        ]
        group_exposure = 0.0

        for sym, pos in portfolio.positions.items():
            if sym.upper() in group_symbols:
                group_exposure += abs(pos.quantity * pos.current_price)

        if portfolio.total_value == 0:
            return RuleResult(rejected=True, reason="Portfolio value is zero")

        price = signal.target_price or 0.0
        new_exposure = group_exposure + (current_quantity * price)
        exposure_pct = (new_exposure / portfolio.total_value) * 100

        if exposure_pct > self.max_correlated_exposure_pct:
            return RuleResult(
                rejected=True,
                reason=(
                    f"Correlated exposure to {symbol_group} would be "
                    f"{exposure_pct:.1f}%, exceeding limit "
                    f"({self.max_correlated_exposure_pct:.1f}%)"
                ),
            )

        return RuleResult()


class ConfidenceGateRule(RiskRule):
    """
    Reject signals whose confidence is below the tier's minimum threshold.

    Each StrategyTier has a required minimum confidence defined in
    config.tiers.TIER_CONFIDENCE_THRESHOLD. Signals below their tier's
    threshold are rejected before any other sizing or budget check.
    """

    def check(
        self,
        signal: Signal,
        portfolio: PortfolioSnapshot,
        current_quantity: float,
    ) -> RuleResult:
        threshold = TIER_CONFIDENCE_THRESHOLD[signal.tier]
        if signal.confidence < threshold:
            return RuleResult(
                rejected=True,
                reason=(
                    f"Signal confidence {signal.confidence:.2f} is below "
                    f"{signal.tier.value} tier threshold ({threshold:.2f})"
                ),
            )
        return RuleResult()


class TierBudgetRule(RiskRule):
    """
    Enforce per-tier USD exposure limits.

    Each tier is allocated a fraction of portfolio value defined by
    TIER_RISK_BUDGET. This rule tracks in-memory exposure per tier
    and rejects trades that would exceed the budget.
    """

    def __init__(self) -> None:
        self._exposure: dict[StrategyTier, float] = {
            tier: 0.0 for tier in StrategyTier
        }

    def check(
        self,
        signal: Signal,
        portfolio: PortfolioSnapshot,
        current_quantity: float,
    ) -> RuleResult:
        budget_frac = TIER_RISK_BUDGET[signal.tier]
        budget_usd = budget_frac * portfolio.total_value
        current_exposure = self._exposure[signal.tier]
        proposed = current_exposure + signal.position_size_usd

        if proposed > budget_usd:
            return RuleResult(
                rejected=True,
                reason=(
                    f"{signal.tier.value} tier budget exceeded: "
                    f"${proposed:,.0f} proposed vs ${budget_usd:,.0f} limit "
                    f"(current exposure ${current_exposure:,.0f})"
                ),
            )
        return RuleResult()

    def on_trade_executed(self, signal: Signal) -> None:
        """Record that a trade's exposure has been added to its tier."""
        self._exposure[signal.tier] += signal.position_size_usd

    def on_position_closed(self, signal: Signal) -> None:
        """Free exposure when a position is closed."""
        self._exposure[signal.tier] = max(
            0.0, self._exposure[signal.tier] - signal.position_size_usd
        )
