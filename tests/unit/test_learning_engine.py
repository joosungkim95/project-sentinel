"""
Unit tests for the Learning Engine (fast loop + slow loop).

Fast loop: tested with in-memory SQLite (no LLM calls).
Slow loop: tested with mocked Claude API responses.
"""

import json
import math
import pytest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from data.models import (
    Base,
    MarketRegimeRecord,
    PortfolioSnapshotRecord,
    StrategyPerformanceRecord,
    TradeRecord,
)
from engines.learning.fast_loop import FastLoop
from engines.learning.slow_loop import SlowLoop, WEEKLY_REVIEW_SYSTEM
from engines.models import (
    AssetClass,
    MarketRegime,
    PortfolioSnapshot,
    StrategyStatus,
)


# --- Fixtures ---

@pytest.fixture
async def db_session():
    """Create an in-memory SQLite session for testing."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_factory() as session:
        yield session

    await engine.dispose()


def make_portfolio() -> PortfolioSnapshot:
    return PortfolioSnapshot(
        total_value=100000.0,
        cash=50000.0,
        positions={},
        risk_utilization={"equities": 25.0},
        daily_pnl=500.0,
        weekly_pnl=1500.0,
        total_pnl=5000.0,
        drawdown_from_peak=2.0,
    )


async def seed_todays_trades(
    session: AsyncSession,
    strategy_id: str = "sma_crossover_spy",
    count: int = 6,
) -> None:
    """Insert trades dated today."""
    # Use midday today to avoid crossing midnight
    today_noon = datetime.combine(
        date.today(), datetime.min.time(), tzinfo=timezone.utc
    ) + timedelta(hours=12)
    for i in range(count):
        record = TradeRecord(
            strategy_id=strategy_id,
            asset_class="equities",
            symbol="SPY",
            side="buy" if i % 2 == 0 else "sell",
            quantity=10.0,
            price=450.0 + i,
            signal_confidence=0.75,
            risk_check_result="approved",
            risk_utilization_pct=15.0 + i,
            market_regime="trending_up",
            pnl=float(i * 10 - 25),  # -25, -15, -5, 5, 15, 25
            created_at=today_noon - timedelta(minutes=i * 10),
        )
        session.add(record)
    await session.commit()


# =====================================================================
# FastLoop tests
# =====================================================================

class TestFastLoopMetrics:

    async def test_sharpe_ratio_basic(self, db_session):
        """Sharpe ratio is computed correctly."""
        result = FastLoop._compute_sharpe([10.0, 20.0, 15.0, 25.0, 30.0])
        assert result is not None
        assert result > 0  # Positive returns → positive Sharpe

    async def test_sharpe_ratio_insufficient_data(self, db_session):
        """Sharpe returns None with < 2 data points."""
        assert FastLoop._compute_sharpe([10.0]) is None
        assert FastLoop._compute_sharpe([]) is None

    async def test_sharpe_ratio_zero_std(self, db_session):
        """Sharpe returns None when all values are the same."""
        assert FastLoop._compute_sharpe([5.0, 5.0, 5.0]) is None

    async def test_max_drawdown_basic(self, db_session):
        """Max drawdown computes correctly."""
        # P&L: +10, +5, -20, +15 → cumulative: 10, 15, -5, 10
        # Peak: 15, drawdown to -5 = 20
        dd = FastLoop._compute_max_drawdown([10.0, 5.0, -20.0, 15.0])
        assert dd == 20.0

    async def test_max_drawdown_no_drawdown(self, db_session):
        """No drawdown when all positive."""
        dd = FastLoop._compute_max_drawdown([10.0, 10.0, 10.0])
        assert dd == 0.0

    async def test_max_drawdown_empty(self, db_session):
        """Empty list returns 0."""
        dd = FastLoop._compute_max_drawdown([])
        assert dd == 0.0


class TestFastLoopRun:

    async def test_run_with_trades(self, db_session):
        """Fast loop processes today's trades."""
        await seed_todays_trades(db_session)

        loop = FastLoop(db_session)
        result = await loop.run()

        assert result["date"] == date.today().isoformat()
        assert "sma_crossover_spy" in result["strategies"]

        metrics = result["strategies"]["sma_crossover_spy"]
        assert metrics["trades_count"] == 6
        assert metrics["total_pnl"] == 0.0  # -25-15-5+5+15+25 = 0

    async def test_run_empty_day(self, db_session):
        """Fast loop handles days with no trades."""
        loop = FastLoop(db_session)
        result = await loop.run()

        assert result["strategies"] == {}

    async def test_persists_performance(self, db_session):
        """Fast loop writes performance records to DB."""
        await seed_todays_trades(db_session)

        loop = FastLoop(db_session)
        await loop.run()

        # Check a record was written
        from sqlalchemy import select
        stmt = select(StrategyPerformanceRecord).where(
            StrategyPerformanceRecord.strategy_id == "sma_crossover_spy"
        )
        result = await db_session.execute(stmt)
        records = list(result.scalars().all())
        assert len(records) == 1
        assert records[0].date == date.today()


class TestRegimeClassification:

    async def test_unknown_with_few_trades(self, db_session):
        """Returns UNKNOWN with fewer than 3 trades."""
        # Add just 1 trade
        record = TradeRecord(
            strategy_id="test",
            asset_class="equities",
            symbol="SPY",
            side="buy",
            quantity=10.0,
            price=450.0,
            signal_confidence=0.75,
            risk_check_result="approved",
            risk_utilization_pct=15.0,
            market_regime="unknown",
            pnl=10.0,
        )
        db_session.add(record)
        await db_session.commit()

        loop = FastLoop(db_session)
        regime, conf = await loop._classify_regime(AssetClass.EQUITIES)
        assert regime == MarketRegime.UNKNOWN

    async def test_trending_up_detection(self, db_session):
        """Detects trending up when buy trades are winning."""
        for i in range(5):
            record = TradeRecord(
                strategy_id="test",
                asset_class="equities",
                symbol="SPY",
                side="buy",
                quantity=10.0,
                price=450.0,
                signal_confidence=0.75,
                risk_check_result="approved",
                risk_utilization_pct=15.0,
                market_regime="unknown",
                pnl=100.0,  # All winners
            )
            db_session.add(record)
        await db_session.commit()

        loop = FastLoop(db_session)
        regime, conf = await loop._classify_regime(AssetClass.EQUITIES)
        assert regime == MarketRegime.TRENDING_UP
        assert conf > 0.5

    async def test_high_volatility_detection(self, db_session):
        """Detects high volatility when most trades lose."""
        for i in range(5):
            record = TradeRecord(
                strategy_id="test",
                asset_class="crypto",
                symbol="BTC-USD",
                side="buy",
                quantity=0.1,
                price=40000.0,
                signal_confidence=0.6,
                risk_check_result="approved",
                risk_utilization_pct=20.0,
                market_regime="unknown",
                pnl=-500.0,  # All losers
            )
            db_session.add(record)
        await db_session.commit()

        loop = FastLoop(db_session)
        regime, conf = await loop._classify_regime(AssetClass.CRYPTO)
        assert regime == MarketRegime.HIGH_VOLATILITY


# =====================================================================
# SlowLoop tests
# =====================================================================

MOCK_CLAUDE_RESPONSE = json.dumps({
    "analysis": "Equities strategies performed well in the trending up regime. Crypto underperformed due to volatility.",
    "hypotheses": [
        {
            "text": "Mean reversion on VIX spikes for equities",
            "rationale": "VIX mean-reverts within 5 days 80% of the time",
            "target_regime": "high_volatility",
            "test_criteria": "Sharpe > 1.0 over 30 days of paper trading",
        }
    ],
    "recommendations": [
        {
            "strategy_id": "sma_crossover_spy",
            "action": "promote",
            "reason": "Consistent 0.6 win rate with 1.2 Sharpe over 4 weeks",
            "params": {},
        },
        {
            "strategy_id": "trend_btc",
            "action": "adjust_params",
            "reason": "High volatility is causing stop-outs. Widen ATR multiplier from 2.0 to 2.5",
            "params": {"atr_multiplier": 2.5},
        },
    ],
    "lessons": [
        "Crypto trend-following needs wider stops in volatile regimes",
        "Equities momentum captured the tech rally — stay allocated",
    ],
})


class TestSlowLoopParsing:

    def test_parse_valid_json(self):
        """Parses valid JSON response correctly."""
        from engines.learning.slow_loop import SlowLoop
        # SlowLoop needs a session but _parse_response is pure
        loop = SlowLoop.__new__(SlowLoop)
        result = loop._parse_response(MOCK_CLAUDE_RESPONSE)

        assert "analysis" in result
        assert len(result["hypotheses"]) == 1
        assert len(result["recommendations"]) == 2
        assert len(result["lessons"]) == 2

    def test_parse_markdown_wrapped_json(self):
        """Handles JSON wrapped in markdown code fences."""
        loop = SlowLoop.__new__(SlowLoop)
        wrapped = f"```json\n{MOCK_CLAUDE_RESPONSE}\n```"
        result = loop._parse_response(wrapped)

        assert result["analysis"].startswith("Equities")
        assert len(result["hypotheses"]) == 1

    def test_parse_invalid_json(self):
        """Returns error structure for invalid JSON."""
        loop = SlowLoop.__new__(SlowLoop)
        result = loop._parse_response("This is not JSON at all")

        assert "parse_error" in result
        assert result["hypotheses"] == []

    def test_parse_missing_keys(self):
        """Handles response with missing expected keys."""
        loop = SlowLoop.__new__(SlowLoop)
        result = loop._parse_response('{"analysis": "Just analysis"}')

        assert result["analysis"] == "Just analysis"


class TestSlowLoopPrompt:

    async def test_build_prompt_under_budget(self, db_session):
        """Prompt stays under token budget."""
        loop = SlowLoop(db_session)
        from engines.models import LearningContext

        context = LearningContext(
            strategy_performances=[],
            market_regime_history=[],
            parameter_change_history=[],
            strategy_graveyard=[],
            portfolio_snapshot=make_portfolio(),
            total_pnl_period=1500.0,
            period_days=7,
        )
        prompt = loop._build_prompt(context)

        # Should be well under 8K tokens (32K chars)
        assert len(prompt) < 32000


class TestSlowLoopRun:

    async def test_full_run_with_mock_api(self, db_session):
        """Slow loop runs end-to-end with mocked Claude API."""
        await seed_todays_trades(db_session)

        loop = SlowLoop(db_session)

        # Mock the Claude API call
        mock_message = MagicMock()
        mock_message.content = [MagicMock(text=MOCK_CLAUDE_RESPONSE)]
        mock_message.usage = MagicMock(
            input_tokens=3000,
            output_tokens=500,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=800,
        )

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_message)
        loop._client = mock_client

        result = await loop.run(make_portfolio(), period_days=7)

        assert "analysis" in result
        assert len(result["hypotheses"]) == 1
        assert len(result["recommendations"]) == 2

        # Verify hypotheses were recorded in the DB
        from sqlalchemy import select
        from data.models import StrategyHypothesisRecord

        stmt = select(StrategyHypothesisRecord)
        db_result = await db_session.execute(stmt)
        hypotheses = list(db_result.scalars().all())
        # 1 hypothesis + 2 recommendations recorded as hypotheses
        assert len(hypotheses) == 3

    async def test_api_error_handling(self, db_session):
        """Slow loop propagates API errors."""
        import anthropic

        loop = SlowLoop(db_session)

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            side_effect=anthropic.APIError(
                message="Rate limited",
                request=MagicMock(),
                body=None,
            )
        )
        loop._client = mock_client

        with pytest.raises(anthropic.APIError):
            await loop.run(make_portfolio())


class TestSlowLoopSystemPrompt:

    def test_system_prompt_requests_json(self):
        """System prompt tells Claude to respond with JSON."""
        assert "JSON" in WEEKLY_REVIEW_SYSTEM
        assert "hypotheses" in WEEKLY_REVIEW_SYSTEM
        assert "recommendations" in WEEKLY_REVIEW_SYSTEM

    def test_system_prompt_safety_rules(self):
        """System prompt includes safety guardrails."""
        assert "risk controls" in WEEKLY_REVIEW_SYSTEM.lower()
        assert "paper testing" in WEEKLY_REVIEW_SYSTEM.lower()
