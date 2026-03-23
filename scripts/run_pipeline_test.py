"""
End-to-end pipeline test — runs SMA crossover through the full pipeline.

This script validates: Strategy -> Risk Engine -> Execution -> DB Logging.
Uses Alpaca paper trading with real market data.

Usage: python scripts/run_pipeline_test.py
"""

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

from config.risk_config import RiskConfig
from data.database import async_session_factory
from data.repositories.trades import get_recent_trades
from engines.execution.alpaca import AlpacaAdapter
from engines.execution.base import Executor
from engines.models import MarketRegime, RiskDecision
from engines.pipeline import TradingPipeline
from engines.risk.engine import RiskEngine
from engines.strategy.equities.sma_crossover import SMACrossoverStrategy

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("pipeline_test")


async def main() -> None:
    """Run full pipeline test with real Alpaca data."""
    logger.info("=== Sentinel Pipeline End-to-End Test ===")

    # 1. Initialize Alpaca adapter
    api_key = os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        logger.error("ALPACA_API_KEY and ALPACA_SECRET_KEY must be set")
        return

    adapter = AlpacaAdapter(api_key=api_key, secret_key=secret_key)
    connected = await adapter.connect()
    if not connected:
        logger.error("Failed to connect to Alpaca")
        return

    account_value = await adapter.get_account_value()
    logger.info("Account value: $%.2f", account_value)

    # 2. Fetch market data
    symbol = "SPY"
    bars = await adapter.get_historical_bars(symbol, "1Day", 100)
    logger.info("Fetched %d daily bars for %s", len(bars), symbol)

    if len(bars) < 51:
        logger.error("Not enough bars for SMA(50). Got %d, need 51+", len(bars))
        return

    latest_price = bars[-1]["close"]
    logger.info("Latest %s close: $%.2f", symbol, latest_price)

    # 3. Set up engines
    risk_engine = RiskEngine(RiskConfig())
    executor = Executor()
    executor.register_adapter(adapter)

    strategy = SMACrossoverStrategy(
        parameters={"position_size_usd": 500.0}
    )

    # 4. Open DB session and create pipeline
    async with async_session_factory() as session:
        pipeline = TradingPipeline(
            risk_engine=risk_engine,
            executor=executor,
            strategies=[strategy],
            db_session=session,
        )

        # 5. Generate signals from strategy directly (to see what it produces)
        market_data = {"bars": bars}
        signals = await strategy.generate_signals(
            market_data=market_data,
            market_regime=MarketRegime.UNKNOWN,
        )

        if signals:
            for sig in signals:
                logger.info(
                    "Signal: %s %s %.2f shares @ $%.2f (confidence: %.2f)",
                    sig.side.value,
                    sig.symbol,
                    sig.quantity,
                    sig.target_price or 0,
                    sig.confidence,
                )
                logger.info("Rationale: %s", sig.rationale)

                # 6. Run through risk engine
                portfolio = await executor.get_portfolio_snapshot()
                risk_result = risk_engine.evaluate(sig, portfolio)
                logger.info(
                    "Risk decision: %s (utilization: %.1f%%)",
                    risk_result.decision.value,
                    risk_result.risk_utilization_pct,
                )

                if risk_result.decision != RiskDecision.REJECTED:
                    # 7. Execute the trade
                    trade_result = await executor.execute(risk_result)
                    logger.info(
                        "Trade executed: %s (id: %s, fill: %s)",
                        trade_result.executed,
                        trade_result.trade_id,
                        trade_result.fill_price,
                    )

                    # 8. Log to DB
                    await pipeline._log_outcome(sig, risk_result, trade_result)
                    logger.info("Trade persisted to database")
                else:
                    logger.info(
                        "Rejected: %s",
                        ", ".join(risk_result.rejection_reasons),
                    )
                    await pipeline._log_outcome(sig, risk_result, None)
        else:
            logger.info(
                "No signal generated — SMA crossover not triggered today. "
                "This is normal; the strategy only fires on crossover events."
            )

        # 9. Verify DB persistence
        trades = await get_recent_trades(session, limit=5)
        logger.info("Trades in database: %d", len(trades))
        for t in trades:
            logger.info(
                "  [%s] %s %s %.1f @ $%.2f — %s",
                t.created_at,
                t.side,
                t.symbol,
                t.quantity,
                t.price,
                t.risk_check_result,
            )

    # 10. Final account state
    final_value = await adapter.get_account_value()
    positions = await adapter.get_positions()
    logger.info("Final account value: $%.2f", final_value)
    logger.info("Open positions: %d", len(positions))
    for p in positions:
        logger.info(
            "  %s: %.1f shares @ $%.2f (P&L: $%.2f)",
            p.symbol,
            p.quantity,
            p.entry_price,
            p.unrealized_pnl,
        )

    logger.info("=== Pipeline test complete ===")


if __name__ == "__main__":
    asyncio.run(main())
