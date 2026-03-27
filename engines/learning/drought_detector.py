"""Signal drought detector — monitors for zero-signal strategies and suggests adjustments."""

import logging

from engines.alerts import send_alert, AlertLevel

logger = logging.getLogger(__name__)

# Expected minimum signals per day by tier
EXPECTED_SIGNALS_PER_DAY = {
    "scout": 5,
    "core": 1,
    "sniper": 0,  # Snipers are expected to be rare — don't flag them
}

# Minimum cycles before flagging (don't alert on fresh deploys)
MIN_CYCLES_BEFORE_ALERT = 50

# Heuristic parameter suggestions per strategy type
PARAMETER_SUGGESTIONS: dict[str, list[str]] = {
    "momentum_scalp": [
        "Lower rsi_buy_low from 55 to 50",
        "Lower volume_multiplier from 1.0 to 0.8",
        "Add more symbols (sector ETFs: XLK, XLF, XLE)",
    ],
    "breakout_crypto": [
        "Lower volume_mult from 1.3 to 1.1",
        "Shorten lookback from 20 to 15 bars",
        "Lower breakout threshold (consider percentage-based instead of absolute)",
    ],
    "skimmer_kalshi": [
        "Lower min_edge from 0.03 to 0.02",
        "Lower min_volume from 50 to 25",
        "Increase scan_limit from 100 to 200",
    ],
    "trend_equities": [
        "Lower adx_trend_threshold from 20 to 15",
        "Remove price > fast EMA requirement",
        "Add more symbols (AMZN, GOOG, META)",
    ],
    "mean_reversion": [
        "Widen bb_std from 1.5 to 1.2",
        "Raise rsi_oversold from 40 to 45",
        "Allow signals in UNKNOWN regime (check regime classifier)",
    ],
    "trend_crypto": [
        "Lower adx_trend_threshold from 20 to 15",
        "Switch to 1Hour timeframe for faster signals",
        "Add DOGE-USD and AVAX-USD to symbol list",
    ],
    "value_kalshi": [
        "Lower min_edge from 0.05 to 0.04",
        "Raise max_spread from 0.15 to 0.20",
        "Lower min_open_interest from 50 to 25",
    ],
}


async def detect_and_alert(
    scheduler_status: dict, shadow_stats: dict
) -> list[dict]:
    """Check for signal droughts and send Discord alerts.

    Args:
        scheduler_status: From scheduler.status() — contains tier_jobs with cycle counts.
        shadow_stats: From shadow executor — contains total_signals.

    Returns:
        List of drought reports (for logging/testing).
    """
    droughts: list[dict] = []
    tier_jobs = scheduler_status.get("tier_jobs", {})
    total_signals = shadow_stats.get("total_signals", 0)

    for job_name, job_info in tier_jobs.items():
        tier = job_info.get("tier", "")
        cycles = job_info.get("cycles_completed", 0)
        expected = EXPECTED_SIGNALS_PER_DAY.get(tier, 0)

        # Skip if too few cycles to judge, or if tier doesn't expect signals
        if cycles < MIN_CYCLES_BEFORE_ALERT or expected == 0:
            continue

        # If we've run enough cycles and still have 0 total signals, flag it
        if total_signals == 0:
            strategies_in_job = job_info.get("strategies", 0)
            asset_class = job_info.get("asset_class", "unknown")

            droughts.append({
                "job": job_name,
                "tier": tier,
                "asset_class": asset_class,
                "cycles": cycles,
                "expected_signals": expected,
                "actual_signals": 0,
            })

    if droughts:
        await _send_drought_alert(droughts, total_signals)

    return droughts


async def _send_drought_alert(
    droughts: list[dict], total_signals: int
) -> None:
    """Send a Discord alert with drought details and parameter suggestions."""
    drought_lines: list[str] = []
    suggestion_lines: list[str] = []

    for d in droughts:
        drought_lines.append(
            f"**{d['job']}**: {d['cycles']} cycles, 0 signals "
            f"(expected ~{d['expected_signals']}/day)"
        )

    # Collect unique suggestions based on affected strategies
    seen_strategies: set[str] = set()
    for d in droughts:
        for strategy_id, suggestions in PARAMETER_SUGGESTIONS.items():
            if strategy_id not in seen_strategies:
                seen_strategies.add(strategy_id)
                suggestion_lines.append(f"**{strategy_id}:**")
                for s in suggestions[:2]:  # Top 2 suggestions per strategy
                    suggestion_lines.append(f"  \u2022 {s}")

    message = (
        "**Signal Drought Detected**\n\n"
        + "\n".join(drought_lines)
        + "\n\n**Suggested Adjustments:**\n"
        + "\n".join(suggestion_lines[:12])  # Cap at 12 lines
        + "\n\n_Review and apply via Claude Code._"
    )

    await send_alert(
        title="\u26a0\ufe0f Signal Drought",
        message=message,
        level=AlertLevel.WARNING,
        fields={
            "Total Signals": str(total_signals),
            "Affected Jobs": str(len(droughts)),
            "Action": "Review parameter suggestions above",
        },
    )
    logger.warning(
        "Signal drought alert sent: %d jobs affected", len(droughts)
    )
