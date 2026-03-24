"""
Backtesting Engine — Simulates strategy performance on historical data.

Feeds historical bars through Strategy → RiskEngine → simulated execution,
tracking equity curve, P&L, and risk metrics. Uses no database — everything
runs in memory.

Usage:
    engine = BacktestEngine(strategy, risk_engine)
    result = await engine.run(bars)
    print(result.summary())
"""

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
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
)
from engines.risk.engine import RiskEngine
from engines.strategy.base import Strategy

logger = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    """Results of a backtest run."""

    strategy_id: str
    num_bars: int
    total_trades: int
    winning_trades: int
    losing_trades: int
    total_pnl: float
    max_drawdown: float
    sharpe_ratio: float | None
    profit_factor: float | None
    win_rate: float
    avg_win: float
    avg_loss: float
    equity_curve: list[float] = field(default_factory=list)
    trades: list[dict] = field(default_factory=list)
    rejected_signals: int = 0

    def summary(self) -> str:
        """Human-readable summary of backtest results."""
        lines = [
            f"=== Backtest: {self.strategy_id} ===",
            f"Bars: {self.num_bars}",
            f"Trades: {self.total_trades} ({self.winning_trades}W / {self.losing_trades}L)",
            f"Win Rate: {self.win_rate:.1%}",
            f"Total P&L: ${self.total_pnl:,.2f}",
            f"Max Drawdown: ${self.max_drawdown:,.2f}",
            f"Sharpe Ratio: {self.sharpe_ratio:.2f}" if self.sharpe_ratio else "Sharpe: N/A",
            f"Profit Factor: {self.profit_factor:.2f}" if self.profit_factor else "Profit Factor: N/A",
            f"Avg Win: ${self.avg_win:,.2f}",
            f"Avg Loss: ${self.avg_loss:,.2f}",
            f"Rejected Signals: {self.rejected_signals}",
        ]
        return "\n".join(lines)


class BacktestEngine:
    """
    Simulates strategy execution on historical data.

    Walks through bars one at a time, running the strategy's
    generate_signals() at each step, checking risk, and simulating fills.
    """

    def __init__(
        self,
        strategy: Strategy,
        risk_config: RiskConfig | None = None,
        initial_capital: float = 100_000.0,
    ):
        self.strategy = strategy
        self.risk_engine = RiskEngine(risk_config or RiskConfig())
        self.initial_capital = initial_capital

    async def run(
        self,
        bars: list[dict[str, Any]],
        market_regime: MarketRegime = MarketRegime.UNKNOWN,
        warmup_bars: int = 50,
    ) -> BacktestResult:
        """
        Run a backtest on historical bar data.

        Args:
            bars: List of OHLCV bar dicts.
            market_regime: Regime to use for the entire backtest.
            warmup_bars: Number of bars to skip before trading (for indicator warmup).

        Returns:
            BacktestResult with full performance metrics.
        """
        cash = self.initial_capital
        positions: dict[str, _Position] = {}
        equity_curve: list[float] = []
        trade_log: list[dict] = []
        rejected = 0

        for i in range(warmup_bars, len(bars)):
            current_bar = bars[i]
            current_price = current_bar["close"]
            symbol = self.strategy.parameters.get("symbol", "UNKNOWN")

            # Build market data window (all bars up to and including current)
            window = bars[: i + 1]

            # Update position mark-to-market
            for pos in positions.values():
                pos.current_price = current_price

            # Build portfolio snapshot
            portfolio = self._build_portfolio(cash, positions)
            equity_curve.append(portfolio.total_value)

            # Generate signals
            market_data = self._format_market_data(window)
            signals = await self.strategy.generate_signals(
                market_data, market_regime
            )

            for signal in signals:
                # Risk check
                risk_result = self.risk_engine.evaluate(signal, portfolio)

                if risk_result.decision == RiskDecision.REJECTED:
                    rejected += 1
                    continue

                qty = risk_result.approved_quantity or signal.quantity

                if signal.side == Side.BUY:
                    cost = qty * current_price
                    if cost > cash:
                        qty = cash / current_price
                        cost = qty * current_price

                    if qty > 0:
                        cash -= cost
                        if symbol in positions:
                            positions[symbol].add(qty, current_price)
                        else:
                            positions[symbol] = _Position(
                                symbol=symbol,
                                quantity=qty,
                                avg_entry=current_price,
                                current_price=current_price,
                                asset_class=signal.asset_class,
                                strategy_id=signal.strategy_id,
                            )
                        trade_log.append({
                            "bar": i,
                            "date": current_bar.get("date"),
                            "side": "buy",
                            "symbol": symbol,
                            "quantity": qty,
                            "price": current_price,
                        })

                elif signal.side == Side.SELL and symbol in positions:
                    pos = positions[symbol]
                    sell_qty = min(qty if qty > 0 else pos.quantity, pos.quantity)
                    proceeds = sell_qty * current_price
                    pnl = (current_price - pos.avg_entry) * sell_qty
                    cash += proceeds

                    trade_log.append({
                        "bar": i,
                        "date": current_bar.get("date"),
                        "side": "sell",
                        "symbol": symbol,
                        "quantity": sell_qty,
                        "price": current_price,
                        "pnl": pnl,
                    })

                    pos.quantity -= sell_qty
                    if pos.quantity <= 0.001:
                        del positions[symbol]

                # Update portfolio after trade
                portfolio = self._build_portfolio(cash, positions)

        # Final mark-to-market
        final_portfolio = self._build_portfolio(cash, positions)
        equity_curve.append(final_portfolio.total_value)

        return self._compute_result(
            equity_curve=equity_curve,
            trade_log=trade_log,
            rejected=rejected,
            num_bars=len(bars),
        )

    def _format_market_data(
        self, bars: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Format bars into the market_data dict strategies expect."""
        asset_class = self.strategy.asset_class
        if asset_class == AssetClass.PREDICTIONS:
            return {"markets": bars}
        return {"bars": bars}

    def _build_portfolio(
        self, cash: float, positions: dict[str, "_Position"]
    ) -> PortfolioSnapshot:
        """Build a PortfolioSnapshot from current state."""
        pos_infos: dict[str, PositionInfo] = {}
        for sym, pos in positions.items():
            pos_infos[sym] = PositionInfo(
                symbol=sym,
                asset_class=pos.asset_class,
                side=Side.BUY,
                quantity=pos.quantity,
                entry_price=pos.avg_entry,
                current_price=pos.current_price,
                unrealized_pnl=(pos.current_price - pos.avg_entry) * pos.quantity,
                pnl_pct=(
                    (pos.current_price - pos.avg_entry) / pos.avg_entry * 100
                    if pos.avg_entry > 0 else 0
                ),
                strategy_id=pos.strategy_id,
            )

        positions_value = sum(
            p.current_price * p.quantity for p in positions.values()
        )
        total_value = cash + positions_value
        pnl = total_value - self.initial_capital
        peak = self.initial_capital  # Simplified — real peak tracking is in equity curve

        return PortfolioSnapshot(
            total_value=total_value,
            cash=cash,
            positions=pos_infos,
            risk_utilization={},
            daily_pnl=pnl,
            weekly_pnl=pnl,
            total_pnl=pnl,
            drawdown_from_peak=max(0, (peak - total_value) / peak * 100) if peak > 0 else 0,
        )

    def _compute_result(
        self,
        equity_curve: list[float],
        trade_log: list[dict],
        rejected: int,
        num_bars: int,
    ) -> BacktestResult:
        """Compute final backtest metrics from equity curve and trade log."""
        sells = [t for t in trade_log if t["side"] == "sell"]
        pnl_values = [t.get("pnl", 0.0) for t in sells]

        wins = [p for p in pnl_values if p > 0]
        losses = [p for p in pnl_values if p <= 0]

        total_pnl = sum(pnl_values)
        gross_profit = sum(wins) if wins else 0.0
        gross_loss = abs(sum(losses)) if losses else 0.0

        return BacktestResult(
            strategy_id=self.strategy.strategy_id,
            num_bars=num_bars,
            total_trades=len(sells),
            winning_trades=len(wins),
            losing_trades=len(losses),
            total_pnl=round(total_pnl, 2),
            max_drawdown=round(self._max_drawdown(equity_curve), 2),
            sharpe_ratio=self._sharpe(equity_curve),
            profit_factor=(
                round(gross_profit / gross_loss, 2)
                if gross_loss > 0 else None
            ),
            win_rate=len(wins) / len(sells) if sells else 0.0,
            avg_win=round(sum(wins) / len(wins), 2) if wins else 0.0,
            avg_loss=round(sum(losses) / len(losses), 2) if losses else 0.0,
            equity_curve=equity_curve,
            trades=trade_log,
            rejected_signals=rejected,
        )

    @staticmethod
    def _max_drawdown(equity_curve: list[float]) -> float:
        """Compute max drawdown from equity curve."""
        if not equity_curve:
            return 0.0
        peak = equity_curve[0]
        max_dd = 0.0
        for val in equity_curve:
            if val > peak:
                peak = val
            dd = peak - val
            if dd > max_dd:
                max_dd = dd
        return max_dd

    @staticmethod
    def _sharpe(equity_curve: list[float]) -> float | None:
        """Compute annualized Sharpe ratio from equity curve."""
        if len(equity_curve) < 3:
            return None
        returns = [
            (equity_curve[i] - equity_curve[i - 1]) / equity_curve[i - 1]
            for i in range(1, len(equity_curve))
            if equity_curve[i - 1] > 0
        ]
        if len(returns) < 2:
            return None
        mean = sum(returns) / len(returns)
        var = sum((r - mean) ** 2 for r in returns) / len(returns)
        std = math.sqrt(var)
        if std == 0:
            return None
        return round((mean / std) * math.sqrt(252), 4)


@dataclass
class _Position:
    """Internal position tracker for backtesting."""
    symbol: str
    quantity: float
    avg_entry: float
    current_price: float
    asset_class: AssetClass
    strategy_id: str

    def add(self, qty: float, price: float) -> None:
        """Add to position (average in)."""
        total_cost = self.avg_entry * self.quantity + price * qty
        self.quantity += qty
        self.avg_entry = total_cost / self.quantity if self.quantity > 0 else 0
