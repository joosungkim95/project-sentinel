"""
Risk Engine Stress Testing — Validates safety under extreme conditions.

Simulates catastrophic market scenarios to verify that every risk rule
holds up when it matters most. Each scenario generates a portfolio state
and a stream of signals, then verifies the Risk Engine responds correctly.

Scenarios:
1. Flash crash — Portfolio drops 8% in minutes
2. Correlated selloff — All positions lose simultaneously
3. Gap down — Market opens 15% lower with no chance to exit
4. Cascading failures — Multiple circuit breakers fire in sequence
5. Position concentration — One winning position grows to dominate
6. Max drawdown breach — Slow bleed over a week

Run via: await StressTestRunner(risk_config).run_all()
"""

import logging
from dataclasses import dataclass, field
from typing import Any

from config.risk_config import RiskConfig
from engines.models import (
    AssetClass,
    MarketRegime,
    PortfolioSnapshot,
    PositionInfo,
    RiskCheckResult,
    RiskDecision,
    Side,
    Signal,
    SignalStrength,
)
from engines.risk.engine import RiskEngine

logger = logging.getLogger(__name__)


@dataclass
class StressScenario:
    """Definition of a stress test scenario."""
    name: str
    description: str
    portfolio: PortfolioSnapshot
    signals: list[Signal]
    expected_rejections: int  # Minimum number of signals that MUST be rejected
    expected_reductions: int = 0  # Minimum number that should be reduced


@dataclass
class StressTestResult:
    """Result of running a single stress scenario."""
    scenario_name: str
    passed: bool
    total_signals: int
    approved: int
    rejected: int
    reduced: int
    details: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _make_signal(
    symbol: str = "SPY",
    side: Side = Side.BUY,
    quantity: float = 100.0,
    price: float = 450.0,
    asset_class: AssetClass = AssetClass.EQUITIES,
    strategy_id: str = "stress_test",
    confidence: float = 0.8,
) -> Signal:
    """Helper to build test signals."""
    return Signal(
        strategy_id=strategy_id,
        asset_class=asset_class,
        symbol=symbol,
        side=side,
        quantity=quantity,
        target_price=price,
        confidence=confidence,
        strength=SignalStrength.STRONG,
        rationale="Stress test signal",
        market_regime=MarketRegime.HIGH_VOLATILITY,
    )


def _make_portfolio(
    total_value: float = 100_000.0,
    cash: float = 50_000.0,
    daily_pnl: float = 0.0,
    weekly_pnl: float = 0.0,
    drawdown: float = 0.0,
    positions: dict[str, PositionInfo] | None = None,
    risk_util: dict[str, float] | None = None,
) -> PortfolioSnapshot:
    """Helper to build test portfolios."""
    return PortfolioSnapshot(
        total_value=total_value,
        cash=cash,
        positions=positions or {},
        risk_utilization=risk_util or {},
        daily_pnl=daily_pnl,
        weekly_pnl=weekly_pnl,
        total_pnl=daily_pnl,
        drawdown_from_peak=drawdown,
    )


def build_flash_crash_scenario() -> StressScenario:
    """
    Scenario 1: Flash crash — Portfolio drops 8% in minutes.

    The daily loss circuit breaker (3%) should reject ALL signals.
    The hard floor (10%) should also activate.
    """
    portfolio = _make_portfolio(
        total_value=92_000.0,
        cash=10_000.0,
        daily_pnl=-8_000.0,  # -8.7% daily loss
        weekly_pnl=-8_000.0,
        drawdown=8.0,
        positions={
            "SPY": PositionInfo(
                symbol="SPY", asset_class=AssetClass.EQUITIES,
                side=Side.BUY, quantity=100, entry_price=500.0,
                current_price=420.0, unrealized_pnl=-8000.0,
                pnl_pct=-16.0, strategy_id="sma_crossover_spy",
            ),
        },
    )
    signals = [
        _make_signal(symbol="SPY", quantity=50, price=420.0),
        _make_signal(symbol="QQQ", quantity=100, price=350.0),
        _make_signal(symbol="BTC-USD", quantity=0.5, price=35000.0,
                     asset_class=AssetClass.CRYPTO),
    ]
    return StressScenario(
        name="flash_crash",
        description="Portfolio drops 8% in minutes — all trades should be rejected",
        portfolio=portfolio,
        signals=signals,
        expected_rejections=3,  # ALL must be rejected
    )


def build_correlated_selloff_scenario() -> StressScenario:
    """
    Scenario 2: Correlated selloff — All equity positions lose.

    Tests correlation rule + weekly drawdown rule together.
    """
    portfolio = _make_portfolio(
        total_value=85_000.0,
        cash=5_000.0,
        daily_pnl=-2_500.0,  # -2.9% daily
        weekly_pnl=-5_500.0,  # -6.5% weekly
        drawdown=15.0,
        positions={
            "SPY": PositionInfo(
                symbol="SPY", asset_class=AssetClass.EQUITIES,
                side=Side.BUY, quantity=80, entry_price=500.0,
                current_price=450.0, unrealized_pnl=-4000.0,
                pnl_pct=-10.0, strategy_id="sma_crossover_spy",
            ),
            "QQQ": PositionInfo(
                symbol="QQQ", asset_class=AssetClass.EQUITIES,
                side=Side.BUY, quantity=60, entry_price=400.0,
                current_price=360.0, unrealized_pnl=-2400.0,
                pnl_pct=-10.0, strategy_id="momentum_qqq",
            ),
        },
        risk_util={"equities": 80.0},  # Already 80% in equities
    )
    signals = [
        # Try to add more equities — should be rejected (concentration)
        _make_signal(symbol="IWM", quantity=100, price=200.0),
        # Try another correlated equity
        _make_signal(symbol="DIA", quantity=50, price=350.0),
    ]
    return StressScenario(
        name="correlated_selloff",
        description="All equity positions lose — correlation and concentration rules should block new equity trades",
        portfolio=portfolio,
        signals=signals,
        expected_rejections=2,
    )


def build_gap_down_scenario() -> StressScenario:
    """
    Scenario 3: Gap down — Market opens 15% lower.

    Hard floor rule should immediately halt everything since
    drawdown exceeds the 10% hard floor.
    """
    portfolio = _make_portfolio(
        total_value=85_000.0,
        cash=30_000.0,
        daily_pnl=-15_000.0,  # -17.6% daily loss
        weekly_pnl=-15_000.0,
        drawdown=15.0,  # Exceeds 10% hard floor
        positions={
            "SPY": PositionInfo(
                symbol="SPY", asset_class=AssetClass.EQUITIES,
                side=Side.BUY, quantity=200, entry_price=500.0,
                current_price=425.0, unrealized_pnl=-15000.0,
                pnl_pct=-15.0, strategy_id="sma_crossover_spy",
            ),
        },
    )
    signals = [
        _make_signal(symbol="SPY", side=Side.SELL, quantity=200, price=425.0),
        _make_signal(symbol="QQQ", quantity=50, price=300.0),
        _make_signal(symbol="BTC-USD", quantity=1, price=30000.0,
                     asset_class=AssetClass.CRYPTO),
    ]
    return StressScenario(
        name="gap_down",
        description="Market opens 15% lower — hard floor should reject ALL trades",
        portfolio=portfolio,
        signals=signals,
        expected_rejections=3,
    )


def build_cascading_failure_scenario() -> StressScenario:
    """
    Scenario 4: Cascading failures — Multiple rules trigger.

    Tests that when the circuit breaker is active, absolutely
    nothing gets through, regardless of signal quality.
    """
    # Normal-looking portfolio but circuit breaker will be active
    portfolio = _make_portfolio(
        total_value=98_000.0,
        cash=80_000.0,
        daily_pnl=-100.0,  # Mild loss
        weekly_pnl=-200.0,
        drawdown=2.0,
    )
    # "Great" signals that would normally be approved
    signals = [
        _make_signal(symbol="SPY", quantity=2, price=450.0, confidence=0.95),
        _make_signal(symbol="BTC-USD", quantity=0.01, price=40000.0,
                     asset_class=AssetClass.CRYPTO, confidence=0.9),
    ]
    return StressScenario(
        name="cascading_failure",
        description="Circuit breaker active — even great signals must be rejected",
        portfolio=portfolio,
        signals=signals,
        expected_rejections=2,
    )


def build_concentration_drift_scenario() -> StressScenario:
    """
    Scenario 5: One position grew to dominate the portfolio.

    Tests that new trades in the same asset class are blocked
    when concentration limits are exceeded.
    """
    portfolio = _make_portfolio(
        total_value=150_000.0,
        cash=10_000.0,
        daily_pnl=5_000.0,
        weekly_pnl=15_000.0,
        drawdown=0.0,
        positions={
            "BTC-USD": PositionInfo(
                symbol="BTC-USD", asset_class=AssetClass.CRYPTO,
                side=Side.BUY, quantity=2.5, entry_price=40000.0,
                current_price=56000.0, unrealized_pnl=40000.0,
                pnl_pct=40.0, strategy_id="trend_btc",
            ),
        },
        risk_util={"crypto": 93.3},  # BTC dominates portfolio
    )
    signals = [
        # Try to add more crypto
        _make_signal(symbol="ETH-USD", quantity=5, price=3000.0,
                     asset_class=AssetClass.CRYPTO),
    ]
    return StressScenario(
        name="concentration_drift",
        description="BTC grew to 93% of portfolio — crypto trades should be rejected",
        portfolio=portfolio,
        signals=signals,
        expected_rejections=1,
    )


def build_slow_bleed_scenario() -> StressScenario:
    """
    Scenario 6: Slow weekly bleed that triggers drawdown reduction.

    The weekly drawdown rule should reduce position sizes, not reject.
    """
    portfolio = _make_portfolio(
        total_value=95_000.0,
        cash=40_000.0,
        daily_pnl=-500.0,
        weekly_pnl=-5_500.0,  # -5.8% weekly drawdown
        drawdown=5.0,
    )
    signals = [
        _make_signal(symbol="SPY", quantity=20, price=450.0),
    ]
    return StressScenario(
        name="slow_bleed",
        description="Slow weekly bleed — positions should be reduced, not rejected",
        portfolio=portfolio,
        signals=signals,
        expected_rejections=0,
        expected_reductions=1,
    )


class StressTestRunner:
    """
    Runs all stress test scenarios against the Risk Engine.

    Usage:
        runner = StressTestRunner()
        results = runner.run_all()
        for r in results:
            print(r.scenario_name, "PASS" if r.passed else "FAIL")
    """

    def __init__(self, config: RiskConfig | None = None):
        self.config = config or RiskConfig()
        self.scenarios = [
            build_flash_crash_scenario(),
            build_correlated_selloff_scenario(),
            build_gap_down_scenario(),
            build_cascading_failure_scenario(),
            build_concentration_drift_scenario(),
            build_slow_bleed_scenario(),
        ]

    def run_all(self) -> list[StressTestResult]:
        """Run all stress scenarios and return results."""
        results = []
        for scenario in self.scenarios:
            result = self._run_scenario(scenario)
            results.append(result)
            status = "PASS" if result.passed else "FAIL"
            logger.info(
                "Stress test [%s] %s: %d/%d rejected, %d reduced",
                status, scenario.name,
                result.rejected, result.total_signals, result.reduced,
            )
        return results

    def _run_scenario(self, scenario: StressScenario) -> StressTestResult:
        """Run a single stress scenario."""
        engine = RiskEngine(self.config)

        # Special case: cascading failure needs circuit breaker active
        if scenario.name == "cascading_failure":
            engine.activate_circuit_breaker()

        approved = 0
        rejected = 0
        reduced = 0
        details: list[str] = []
        errors: list[str] = []

        for signal in scenario.signals:
            result = engine.evaluate(signal, scenario.portfolio)

            if result.decision == RiskDecision.REJECTED:
                rejected += 1
                details.append(
                    f"REJECTED {signal.symbol}: {result.rejection_reasons}"
                )
            elif result.decision == RiskDecision.REDUCED:
                reduced += 1
                details.append(
                    f"REDUCED {signal.symbol}: {signal.quantity} → {result.approved_quantity}"
                )
            else:
                approved += 1
                details.append(f"APPROVED {signal.symbol}: qty={result.approved_quantity}")

        # Validate expectations
        passed = True

        if rejected < scenario.expected_rejections:
            passed = False
            errors.append(
                f"Expected at least {scenario.expected_rejections} rejections, "
                f"got {rejected}"
            )

        if reduced < scenario.expected_reductions:
            passed = False
            errors.append(
                f"Expected at least {scenario.expected_reductions} reductions, "
                f"got {reduced}"
            )

        return StressTestResult(
            scenario_name=scenario.name,
            passed=passed,
            total_signals=len(scenario.signals),
            approved=approved,
            rejected=rejected,
            reduced=reduced,
            details=details,
            errors=errors,
        )

    def summary(self, results: list[StressTestResult]) -> str:
        """Generate a human-readable summary of all stress test results."""
        lines = ["=== Risk Engine Stress Test Results ===", ""]
        all_passed = all(r.passed for r in results)

        for r in results:
            status = "PASS" if r.passed else "FAIL"
            lines.append(f"[{status}] {r.scenario_name}")
            lines.append(f"  Signals: {r.total_signals} | "
                         f"Rejected: {r.rejected} | Reduced: {r.reduced} | "
                         f"Approved: {r.approved}")
            if r.errors:
                for e in r.errors:
                    lines.append(f"  ERROR: {e}")
            lines.append("")

        lines.append(f"Overall: {'ALL PASSED' if all_passed else 'FAILURES DETECTED'}")
        return "\n".join(lines)
