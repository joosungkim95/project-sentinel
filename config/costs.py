"""
Cost configuration — Controls which Claude model is used for each decision type.

GOAL: Keep monthly Claude API costs under $15.
Strategy: Use Haiku for routine decisions, Sonnet for complex analysis.
Never use Opus in automated calls.
"""

from enum import Enum

from pydantic import BaseModel, Field


class ClaudeModel(str, Enum):
    """Available Claude models with their costs per million tokens."""
    HAIKU = "claude-haiku-4-5-20251001"     # $1 input / $5 output
    SONNET = "claude-sonnet-4-6"             # $3 input / $15 output
    # Opus intentionally excluded from automated use


class DecisionType(str, Enum):
    """Types of decisions that may use Claude API."""
    TRADE_EVALUATION = "trade_evaluation"
    STRATEGY_HYPOTHESIS = "strategy_hypothesis"
    MARKET_REGIME_ANALYSIS = "market_regime_analysis"
    WEEKLY_REVIEW = "weekly_review"
    NEWS_INTERPRETATION = "news_interpretation"


# Map each decision type to the appropriate model and token budget
DECISION_MODEL_MAP: dict[DecisionType, dict] = {
    DecisionType.TRADE_EVALUATION: {
        "model": ClaudeModel.HAIKU,
        "max_input_tokens": 2000,
        "max_output_tokens": 500,
        "est_cost_per_call": 0.005,  # ~$0.005
    },
    DecisionType.STRATEGY_HYPOTHESIS: {
        "model": ClaudeModel.SONNET,
        "max_input_tokens": 6000,
        "max_output_tokens": 2000,
        "est_cost_per_call": 0.05,  # ~$0.05
    },
    DecisionType.MARKET_REGIME_ANALYSIS: {
        "model": ClaudeModel.HAIKU,
        "max_input_tokens": 4000,
        "max_output_tokens": 1000,
        "est_cost_per_call": 0.01,
    },
    DecisionType.WEEKLY_REVIEW: {
        "model": ClaudeModel.SONNET,
        "max_input_tokens": 8000,
        "max_output_tokens": 3000,
        "est_cost_per_call": 0.07,
    },
    DecisionType.NEWS_INTERPRETATION: {
        "model": ClaudeModel.HAIKU,
        "max_input_tokens": 3000,
        "max_output_tokens": 500,
        "est_cost_per_call": 0.006,
    },
}


class CostConfig(BaseModel):
    """Monthly cost budget and limits."""

    monthly_api_budget: float = Field(
        default=15.0,
        description="Max monthly spend on Claude API calls in USD."
    )

    # Frequency limits to prevent runaway costs
    max_trade_evaluations_per_hour: int = Field(
        default=20,
        description="Max Claude API calls for trade evaluation per hour."
    )
    max_strategy_hypotheses_per_day: int = Field(
        default=3,
        description="Max strategy generation calls per day."
    )
    max_news_interpretations_per_hour: int = Field(
        default=10,
        description="Max news analysis calls per hour."
    )

    # Schedule controls
    strategy_scan_interval_minutes: int = Field(
        default=15,
        description="How often strategies scan for signals (equities)."
    )
    crypto_scan_interval_minutes: int = Field(
        default=5,
        description="How often crypto strategies scan."
    )
    prediction_scan_interval_minutes: int = Field(
        default=10,
        description="How often prediction market strategies scan."
    )

    # Market data cache TTLs (seconds)
    equity_quote_cache_ttl: int = 60
    crypto_quote_cache_ttl: int = 30
    prediction_market_cache_ttl: int = 120

    # Use Batch API for non-urgent work
    use_batch_api_for_weekly_review: bool = True
    use_batch_api_for_backtesting: bool = True
