"""
Scheduler configuration — Intervals and market hours.

Controls how often each asset class runs its trading cycle
and when equities strategies should be active (market hours only).
"""

from pydantic import BaseModel, Field


class MarketHours(BaseModel):
    """US equity market hours (Eastern Time)."""

    open_hour: int = Field(default=9, description="Market open hour (ET)")
    open_minute: int = Field(default=30, description="Market open minute (ET)")
    close_hour: int = Field(default=16, description="Market close hour (ET)")
    close_minute: int = Field(default=0, description="Market close minute (ET)")
    timezone: str = Field(default="US/Eastern")


class SchedulerConfig(BaseModel):
    """Configuration for the trading scheduler."""

    # Interval in minutes per asset class
    equities_interval_minutes: int = Field(
        default=15,
        description="How often to run equity strategies (minutes).",
    )
    crypto_interval_minutes: int = Field(
        default=5,
        description="How often to run crypto strategies (minutes).",
    )
    predictions_interval_minutes: int = Field(
        default=10,
        description="How often to run prediction market strategies (minutes).",
    )

    # Market hours — only run equities during open market
    market_hours: MarketHours = Field(default_factory=MarketHours)
    respect_market_hours: bool = Field(
        default=True,
        description="Skip equity cycles outside market hours.",
    )

    # Safety
    max_consecutive_errors: int = Field(
        default=5,
        description="Pause a job after this many consecutive errors.",
    )
    enabled: bool = Field(
        default=True,
        description="Master switch — set False to disable all scheduled jobs.",
    )
