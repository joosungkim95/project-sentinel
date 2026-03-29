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
    AssetClass,
    MarketRegime,
    RiskDecision,
    Signal,
    TradeResult,
)
from engines.risk.engine import RiskEngine
from engines.strategy.base import Strategy
from config.tiers import (
    COINBASE_TIMEFRAME_MAP,
    StrategyTier,
    TIER_TIMEFRAMES,
    TIMEFRAME_AGGREGATION,
)

logger = logging.getLogger(__name__)

# Default number of daily bars to fetch for strategies
DEFAULT_BARS_LIMIT = 100


def aggregate_bars(bars: list[dict], factor: int = 4) -> list[dict]:
    """Aggregate smaller timeframe bars into larger ones.

    Args:
        bars: List of OHLCV bar dicts, ordered chronologically.
        factor: Number of bars to combine (e.g., 4 for 1h->4h).

    Returns:
        Aggregated bars. Partial groups at the end are dropped.
    """
    if not bars:
        return []
    result = []
    for i in range(0, len(bars) - factor + 1, factor):
        group = bars[i : i + factor]
        result.append({
            "open": group[0]["open"],
            "high": max(b["high"] for b in group),
            "low": min(b["low"] for b in group),
            "close": group[-1]["close"],
            "volume": sum(b["volume"] for b in group),
            "timestamp": group[0]["timestamp"],
        })
    return result


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
                # 1. Fetch market data for this strategy
                market_data = await self._fetch_market_data(strategy)

                # 2. Generate signals
                signals = await strategy.generate_signals(
                    market_data=market_data,
                    market_regime=market_regime,
                )

                for signal in signals:
                    # 3. Risk check
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

                    # 4. Execute
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

    async def _fetch_market_data(
        self, strategy: Strategy
    ) -> dict[str, Any]:
        """
        Fetch market data for a strategy from the appropriate adapter.

        For equities/crypto: fetches historical bars for the strategy's symbol.
        For predictions: fetches live market listings (prices, volume, spreads).
        Returns empty dict if no adapter available.
        """
        adapter = self.executor._adapters.get(strategy.asset_class)
        if adapter is None:
            logger.debug(
                "No adapter for %s — skipping data fetch",
                strategy.asset_class.value,
            )
            return {}

        # Prediction markets: fetch market listings instead of bars
        if strategy.asset_class == AssetClass.PREDICTIONS:
            return await self._fetch_prediction_data(adapter, strategy)

        # Equities/crypto: fetch historical bars
        symbol = strategy.parameters.get("symbol")
        if not symbol:
            return {}

        fetch = getattr(adapter, "get_historical_bars", None)
        if fetch is None:
            return {}

        try:
            bars = await fetch(symbol, limit=DEFAULT_BARS_LIMIT)
            logger.debug(
                "Fetched %d bars for %s (%s)",
                len(bars),
                symbol,
                strategy.strategy_id,
            )
            return {"bars": bars}
        except Exception as e:
            logger.warning(
                "Failed to fetch bars for %s: %s", symbol, e
            )
            return {}

    async def _fetch_prediction_data(
        self, adapter: Any, strategy: Strategy
    ) -> dict[str, Any]:
        """Fetch market listings and quotes for prediction market strategies."""
        try:
            get_markets = getattr(adapter, "get_markets", None)
            get_quote = getattr(adapter, "get_quote", None)
            if get_markets is None:
                return {}

            limit = strategy.parameters.get("scan_limit", 50)
            markets = await get_markets(limit=limit)

            # Enrich with full quote data if adapter supports it
            if get_quote and markets:
                enriched = []
                for m in markets:
                    ticker = m.get("ticker", "")
                    if not ticker:
                        continue
                    try:
                        quote = await get_quote(ticker)
                        m.update(quote)
                    except Exception:
                        pass  # Use basic market data
                    enriched.append(m)
                markets = enriched

            logger.debug(
                "Fetched %d prediction markets (%s)",
                len(markets),
                strategy.strategy_id,
            )

            # Fetch crypto bars for probability-model strategies (KCS-02)
            if hasattr(strategy, 'strategy_id') and 'prob' in strategy.strategy_id:
                coinbase = self.executor._adapters.get(AssetClass.CRYPTO)
                if coinbase:
                    try:
                        fetch_bars = getattr(coinbase, "get_historical_bars", None)
                        if fetch_bars:
                            crypto_bars = await fetch_bars(
                                "BTC-USD",
                                granularity="ONE_HOUR",
                                limit=750,  # ~31 days of hourly data
                            )
                            logger.debug(
                                "Fetched %d crypto bars for prob model (%s)",
                                len(crypto_bars),
                                strategy.strategy_id,
                            )
                            return {"markets": markets, "crypto_bars": crypto_bars}
                    except Exception as e:
                        logger.warning(
                            "Failed to fetch crypto bars for prob model: %s", e
                        )

            return {"markets": markets}
        except Exception as e:
            logger.warning(
                "Failed to fetch prediction markets: %s", e
            )
            return {}

    async def run_tier(
        self,
        tier: StrategyTier,
        asset_class_str: str,
        strategies: list[Strategy],
        market_regime: MarketRegime,
    ) -> list[TradeResult]:
        """Run all strategies for one (tier, asset_class) combination.

        Args:
            tier: The strategy tier (scout, core, sniper).
            asset_class_str: Asset class string ("equities", "crypto", "predictions").
            strategies: Strategies to run (already filtered to this tier/asset class).
            market_regime: Current market regime classification.

        Returns:
            List of trade results from this tier run.
        """
        results: list[TradeResult] = []
        timeframe = TIER_TIMEFRAMES[tier][asset_class_str]

        active = [
            s for s in strategies
            if s.status.value in ("active", "paper_testing")
        ]
        if not active:
            return results

        # Prediction markets: fetch per-strategy, no shared bar data
        if timeframe == "realtime":
            for strategy in active:
                try:
                    adapter = self.executor._adapters.get(strategy.asset_class)
                    if adapter is None:
                        continue
                    pred_data = await self._fetch_prediction_data(
                        adapter, strategy,
                    )
                    signals = await strategy.generate_signals(
                        bars={"markets": pred_data.get("markets", [])},
                        market_regime=market_regime,
                    )
                    for signal in signals:
                        result = await self._evaluate_and_execute(signal)
                        if result is not None:
                            results.append(result)
                except Exception as e:
                    logger.error(
                        "Error in prediction strategy %s: %s",
                        strategy.strategy_id, e, exc_info=True,
                    )
                    await alert_system_error(
                        error=str(e),
                        component=f"Strategy: {strategy.strategy_id}",
                    )
            return results

        # Bar-based strategies: collect all symbols, fetch once per symbol
        asset_class = active[0].asset_class
        adapter = self.executor._adapters.get(asset_class)
        if adapter is None:
            logger.debug("No adapter for %s — skipping tier %s", asset_class_str, tier.value)
            return results

        all_symbols: set[str] = set()
        for strategy in active:
            all_symbols.update(strategy.symbols)
        all_symbols.discard("")

        bars_by_symbol = await self._fetch_bars_for_tier(
            adapter, list(all_symbols), timeframe, asset_class_str,
        )

        for strategy in active:
            try:
                strategy_bars = {
                    sym: bars_by_symbol[sym]
                    for sym in strategy.symbols
                    if sym in bars_by_symbol and bars_by_symbol[sym]
                }
                if not strategy_bars:
                    logger.debug(
                        "No bar data for strategy %s — skipping",
                        strategy.strategy_id,
                    )
                    continue

                signals = await strategy.generate_signals(
                    bars=strategy_bars,
                    market_regime=market_regime,
                )
                for signal in signals:
                    result = await self._evaluate_and_execute(signal)
                    if result is not None:
                        results.append(result)
            except Exception as e:
                logger.error(
                    "Error in strategy %s: %s",
                    strategy.strategy_id, e, exc_info=True,
                )
                await alert_system_error(
                    error=str(e),
                    component=f"Strategy: {strategy.strategy_id}",
                )

        return results

    async def _fetch_bars_for_tier(
        self,
        adapter: Any,
        symbols: list[str],
        timeframe: str,
        asset_class_str: str,
    ) -> dict[str, list[dict]]:
        """Fetch bar data for multiple symbols at a given timeframe.

        Args:
            adapter: Platform adapter (Alpaca or Coinbase).
            symbols: List of symbols to fetch.
            timeframe: Canonical timeframe string (e.g., "1Day", "4Hour").
            asset_class_str: Asset class string for timeframe mapping.

        Returns:
            Dict mapping symbol to list of bar dicts.
        """
        fetch = getattr(adapter, "get_historical_bars", None)
        if fetch is None:
            return {}

        result: dict[str, list[dict]] = {}
        agg_config = TIMEFRAME_AGGREGATION.get(timeframe)

        for symbol in symbols:
            try:
                if asset_class_str == "crypto" and agg_config:
                    # Fetch at source granularity and aggregate
                    source_granularity = agg_config["source"]
                    factor = agg_config["factor"]
                    raw_bars = await fetch(
                        symbol,
                        granularity=source_granularity,
                        limit=DEFAULT_BARS_LIMIT * factor,
                    )
                    result[symbol] = aggregate_bars(raw_bars, factor=factor)
                elif asset_class_str == "crypto":
                    # Direct fetch with Coinbase granularity mapping
                    cb_granularity = COINBASE_TIMEFRAME_MAP.get(timeframe, "ONE_DAY")
                    raw_bars = await fetch(
                        symbol,
                        granularity=cb_granularity,
                        limit=DEFAULT_BARS_LIMIT,
                    )
                    result[symbol] = raw_bars
                else:
                    # Equities: pass timeframe directly to Alpaca
                    raw_bars = await fetch(
                        symbol,
                        timeframe=timeframe,
                        limit=DEFAULT_BARS_LIMIT,
                    )
                    result[symbol] = raw_bars

                logger.debug(
                    "Fetched %d bars for %s (%s, %s)",
                    len(result[symbol]), symbol, timeframe, asset_class_str,
                )
            except Exception as e:
                logger.warning(
                    "Failed to fetch bars for %s (%s): %s",
                    symbol, timeframe, e,
                )
                result[symbol] = []

        return result

    async def _evaluate_and_execute(
        self, signal: Signal,
    ) -> TradeResult | None:
        """Evaluate a signal through risk engine and execute if approved.

        Args:
            signal: Trading signal to evaluate.

        Returns:
            TradeResult if a trade was attempted, None if rejected.
        """
        portfolio = await self.executor.get_portfolio_snapshot()
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
            return None

        # APPROVED or REDUCED — execute
        trade_result = await self.executor.execute(risk_result)

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

        # Check circuit breaker
        portfolio = await self.executor.get_portfolio_snapshot()
        if self._should_activate_circuit_breaker(portfolio):
            self.risk_engine.activate_circuit_breaker()
            await alert_circuit_breaker(
                reason="Automated circuit breaker trigger",
                portfolio_value=portfolio.total_value,
                daily_pnl=portfolio.daily_pnl,
            )

        return trade_result

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
