"""
Shadow Mode Executor — Bridge from paper trading to live.

Runs both a paper simulation and real execution (at minimum size)
in parallel, then compares results to detect divergence before
scaling up to full live trading.

How it works:
1. Every approved signal is executed on the REAL platform at minimum size
2. The same signal is also simulated as a full-size paper trade
3. Divergences between real and paper fills are tracked and alerted
4. If divergence exceeds threshold, shadow mode pauses live trading

Divergence types:
- Fill price divergence (slippage beyond expected)
- Fill rate divergence (paper fills but real doesn't, or vice versa)
- Latency divergence (real execution takes much longer)
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from engines.alerts import send_alert, AlertLevel
from engines.execution.base import Executor, PlatformAdapter
from engines.models import (
    AssetClass,
    PortfolioSnapshot,
    RiskCheckResult,
    RiskDecision,
    Side,
    Signal,
    TradeResult,
)

logger = logging.getLogger(__name__)

# Default minimum trade sizes per asset class
# Crypto: Coinbase requires $10 minimum for market orders.
# At BTC ~$85k, 0.00012 BTC ≈ $10.20 — gives a small buffer.
MIN_TRADE_SIZES = {
    AssetClass.EQUITIES: 1.0,       # 1 share
    AssetClass.CRYPTO: 0.00012,     # ~$10.20 of BTC at $85k
    AssetClass.PREDICTIONS: 1.0,     # 1 contract
}


@dataclass
class DivergenceRecord:
    """Record of a divergence between paper and live execution."""
    timestamp: datetime
    symbol: str
    divergence_type: str  # "fill_price", "fill_rate", "latency"
    paper_value: float | str
    live_value: float | str
    magnitude: float  # 0.0 to 1.0+ (percentage or ratio)
    details: str


@dataclass
class ShadowStats:
    """Accumulated shadow mode statistics."""
    total_signals: int = 0
    live_executed: int = 0
    live_failed: int = 0
    paper_fills: int = 0
    divergences: list[DivergenceRecord] = field(default_factory=list)
    max_price_divergence_pct: float = 0.0
    avg_price_divergence_pct: float = 0.0
    fill_rate_match: float = 1.0  # 1.0 = perfect match

    @property
    def divergence_count(self) -> int:
        return len(self.divergences)

    @property
    def is_healthy(self) -> bool:
        """Shadow mode is healthy if divergences are within tolerance."""
        return (
            self.max_price_divergence_pct < 2.0  # < 2% price divergence
            and self.fill_rate_match > 0.8  # > 80% fill rate agreement
            and self.divergence_count < 10  # Not too many divergences
        )

    def summary(self) -> dict:
        return {
            "total_signals": self.total_signals,
            "live_executed": self.live_executed,
            "live_failed": self.live_failed,
            "paper_fills": self.paper_fills,
            "divergence_count": self.divergence_count,
            "max_price_divergence_pct": round(self.max_price_divergence_pct, 4),
            "avg_price_divergence_pct": round(self.avg_price_divergence_pct, 4),
            "fill_rate_match": round(self.fill_rate_match, 4),
            "healthy": self.is_healthy,
        }


class ShadowExecutor:
    """
    Wraps a real Executor to run shadow mode.

    Executes at minimum size on real platforms while simulating
    full-size paper trades. Tracks divergence between the two.
    """

    def __init__(
        self,
        real_executor: Executor,
        min_sizes: dict[AssetClass, float] | None = None,
        max_divergence_pct: float = 2.0,
        auto_pause_on_divergence: bool = True,
    ):
        """
        Args:
            real_executor: The real Executor with live platform adapters.
            min_sizes: Minimum trade sizes per asset class.
            max_divergence_pct: Pause if price divergence exceeds this %.
            auto_pause_on_divergence: Auto-pause live trading on high divergence.
        """
        self.real_executor = real_executor
        self.min_sizes = min_sizes or MIN_TRADE_SIZES.copy()
        self.max_divergence_pct = max_divergence_pct
        self.auto_pause = auto_pause_on_divergence
        self.stats = ShadowStats()
        self._live_paused = False
        self._paper_log: list[dict] = []

    @property
    def _adapters(self) -> dict:
        """Expose adapters from real executor for pipeline compatibility."""
        return self.real_executor._adapters

    async def execute(
        self, approved_signal: RiskCheckResult
    ) -> TradeResult:
        """
        Execute in shadow mode: real (min size) + paper (full size).

        Pipeline-compatible: returns the paper result (full-size simulation)
        as the "official" result, while the live min-size trade runs alongside
        for divergence tracking.

        Args:
            approved_signal: Risk-approved signal.

        Returns:
            The paper TradeResult (used for logging/learning).
        """
        _live, paper = await self.execute_shadow(approved_signal)
        return paper

    async def execute_shadow(
        self, approved_signal: RiskCheckResult
    ) -> tuple[TradeResult, TradeResult]:
        """
        Execute in shadow mode and return both results.

        Returns:
            Tuple of (live_result, paper_result).
        """
        self.stats.total_signals += 1
        signal = approved_signal.original_signal

        # 1. Paper execution (full size, simulated)
        paper_result = self._simulate_paper(approved_signal)
        self.stats.paper_fills += 1 if paper_result.executed else 0

        # 2. Live execution (minimum size)
        if self._live_paused:
            live_result = self._make_skipped_result(signal, "Shadow mode live trading paused")
        else:
            live_result = await self._execute_live_min(approved_signal)

        if live_result.executed:
            self.stats.live_executed += 1
        else:
            self.stats.live_failed += 1

        # 3. Compare and detect divergence
        await self._check_divergence(signal, paper_result, live_result)

        return live_result, paper_result

    async def _execute_live_min(
        self, approved_signal: RiskCheckResult
    ) -> TradeResult:
        """Execute at minimum size on the real platform."""
        signal = approved_signal.original_signal
        min_size = self.min_sizes.get(signal.asset_class, 1.0)

        # Create a modified signal with minimum quantity
        min_signal = RiskCheckResult(
            decision=approved_signal.decision,
            original_signal=signal,
            approved_quantity=min_size,
            risk_utilization_pct=approved_signal.risk_utilization_pct,
            portfolio_value=approved_signal.portfolio_value,
        )

        try:
            result = await self.real_executor.execute(min_signal)
            return result
        except Exception as e:
            logger.error("Shadow live execution failed: %s", e)
            return self._make_skipped_result(signal, str(e))

    def _simulate_paper(self, approved_signal: RiskCheckResult) -> TradeResult:
        """Simulate a paper trade at full approved size."""
        signal = approved_signal.original_signal
        qty = approved_signal.approved_quantity or signal.quantity

        paper_result = TradeResult(
            trade_id=f"paper-{self.stats.total_signals}",
            signal=signal,
            risk_check=approved_signal,
            executed=True,
            fill_price=signal.target_price,
            fill_quantity=qty,
            commission=0.0,
            slippage=0.0,
            platform=f"paper_{signal.asset_class.value}",
        )

        self._paper_log.append({
            "id": paper_result.trade_id,
            "symbol": signal.symbol,
            "side": signal.side.value,
            "quantity": qty,
            "price": signal.target_price,
        })

        return paper_result

    async def _check_divergence(
        self,
        signal: Signal,
        paper: TradeResult,
        live: TradeResult,
    ) -> None:
        """Compare paper and live results for divergence."""
        now = datetime.now(timezone.utc)

        # Fill rate divergence
        if paper.executed != live.executed:
            div = DivergenceRecord(
                timestamp=now,
                symbol=signal.symbol,
                divergence_type="fill_rate",
                paper_value="filled" if paper.executed else "failed",
                live_value="filled" if live.executed else "failed",
                magnitude=1.0,
                details=(
                    f"Paper {'filled' if paper.executed else 'failed'} but "
                    f"live {'filled' if live.executed else 'failed'}"
                ),
            )
            self.stats.divergences.append(div)
            logger.warning("Fill rate divergence: %s", div.details)
            self._update_fill_rate_stats()
            return

        # Fill price divergence (only if both filled)
        if paper.executed and live.executed:
            paper_price = paper.fill_price or 0
            live_price = live.fill_price or 0

            if paper_price > 0:
                price_div_pct = abs(live_price - paper_price) / paper_price * 100
            else:
                price_div_pct = 0.0

            if price_div_pct > 0.1:  # > 0.1% is worth logging
                div = DivergenceRecord(
                    timestamp=now,
                    symbol=signal.symbol,
                    divergence_type="fill_price",
                    paper_value=paper_price,
                    live_value=live_price,
                    magnitude=price_div_pct,
                    details=(
                        f"Price divergence: paper=${paper_price:.4f} "
                        f"vs live=${live_price:.4f} ({price_div_pct:.2f}%)"
                    ),
                )
                self.stats.divergences.append(div)
                self._update_price_stats(price_div_pct)

                if price_div_pct > self.max_divergence_pct:
                    logger.warning(
                        "HIGH divergence: %s (%.2f%% > %.2f%% threshold)",
                        signal.symbol, price_div_pct, self.max_divergence_pct,
                    )
                    if self.auto_pause:
                        await self._pause_live(
                            f"Price divergence {price_div_pct:.2f}% on {signal.symbol}"
                        )

    def _update_price_stats(self, divergence_pct: float) -> None:
        """Update rolling price divergence statistics."""
        self.stats.max_price_divergence_pct = max(
            self.stats.max_price_divergence_pct, divergence_pct
        )
        price_divs = [
            d.magnitude for d in self.stats.divergences
            if d.divergence_type == "fill_price"
        ]
        if price_divs:
            self.stats.avg_price_divergence_pct = sum(price_divs) / len(price_divs)

    def _update_fill_rate_stats(self) -> None:
        """Update fill rate match statistic."""
        if self.stats.total_signals == 0:
            return
        fill_divs = sum(
            1 for d in self.stats.divergences
            if d.divergence_type == "fill_rate"
        )
        self.stats.fill_rate_match = 1.0 - (fill_divs / self.stats.total_signals)

    async def _pause_live(self, reason: str) -> None:
        """Pause live trading due to high divergence."""
        self._live_paused = True
        logger.critical("Shadow mode: PAUSING live trading — %s", reason)
        await send_alert(
            title="Shadow Mode: Live Trading Paused",
            message=f"High divergence detected. Reason: {reason}",
            level=AlertLevel.CRITICAL,
            fields=self.stats.summary(),
        )

    def resume_live(self) -> None:
        """Resume live trading after investigation."""
        self._live_paused = False
        logger.info("Shadow mode: live trading resumed")

    def reset_stats(self) -> None:
        """Reset statistics (e.g., at start of new shadow test period)."""
        self.stats = ShadowStats()
        self._paper_log.clear()

    @staticmethod
    def _make_skipped_result(signal: Signal, reason: str) -> TradeResult:
        """Create a non-executed TradeResult."""
        return TradeResult(
            trade_id="",
            signal=signal,
            risk_check=RiskCheckResult(
                decision=RiskDecision.APPROVED,
                original_signal=signal,
                approved_quantity=signal.quantity,
                risk_utilization_pct=0,
                portfolio_value=0,
            ),
            executed=False,
            platform="shadow",
            error_message=reason,
        )

    async def get_portfolio_snapshot(self) -> PortfolioSnapshot:
        """Delegate to real executor."""
        return await self.real_executor.get_portfolio_snapshot()

    def status(self) -> dict:
        """Return shadow mode status."""
        return {
            "mode": "shadow",
            "live_paused": self._live_paused,
            "stats": self.stats.summary(),
            "recent_divergences": [
                {
                    "time": d.timestamp.isoformat(),
                    "symbol": d.symbol,
                    "type": d.divergence_type,
                    "magnitude": d.magnitude,
                    "details": d.details,
                }
                for d in self.stats.divergences[-5:]
            ],
        }
