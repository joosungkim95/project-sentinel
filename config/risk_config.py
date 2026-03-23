"""
Risk configuration — All tunable parameters for the Risk Engine.

These are the ONLY knobs that control risk behavior.
Changes here are logged and should be reviewed carefully.
"""

from pydantic import BaseModel, Field


class RiskConfig(BaseModel):
    """Configuration for the Risk Engine."""

    # Hard floor: portfolio cannot drop below this % of peak value
    hard_floor_pct: float = Field(
        default=90.0,
        description="Portfolio value floor as % of peak. Below this = halt all trading."
    )

    # Position sizing
    max_position_pct: float = Field(
        default=10.0,
        description="Max single position as % of portfolio value."
    )

    # Asset class concentration
    max_asset_class_pct: float = Field(
        default=40.0,
        description="Max allocation to any single asset class as % of portfolio."
    )

    # Daily loss circuit breaker
    max_daily_loss_pct: float = Field(
        default=3.0,
        description="Max daily loss as % of portfolio before halting trades."
    )

    # Weekly drawdown
    max_weekly_drawdown_pct: float = Field(
        default=5.0,
        description="Weekly drawdown threshold that triggers position reduction."
    )
    drawdown_reduction_factor: float = Field(
        default=0.5,
        description="Factor to reduce position sizes when weekly drawdown threshold is hit."
    )

    # Correlation
    max_correlated_exposure_pct: float = Field(
        default=50.0,
        description="Max combined exposure to correlated assets as % of portfolio."
    )

    # Circuit breaker duration
    circuit_breaker_hours: int = Field(
        default=24,
        description="Hours to keep circuit breaker active after triggering."
    )
