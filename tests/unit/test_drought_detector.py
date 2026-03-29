"""Tests for signal drought detector."""

import pytest

from engines.learning.drought_detector import (
    EXPECTED_SIGNALS_PER_DAY,
    MIN_CYCLES_BEFORE_ALERT,
    detect_and_alert,
)


@pytest.mark.asyncio
async def test_no_drought_with_few_cycles():
    """Don't alert on fresh deploys with few cycles."""
    status = {
        "tier_jobs": {
            "scout_crypto": {
                "tier": "scout",
                "asset_class": "crypto",
                "cycles_completed": 10,
                "strategies": 1,
            }
        }
    }
    shadow = {"total_signals": 0}
    droughts = await detect_and_alert(status, shadow)
    assert len(droughts) == 0  # Too few cycles


@pytest.mark.asyncio
async def test_drought_detected_after_many_cycles():
    """Alert when enough cycles have run with 0 signals."""
    status = {
        "tier_jobs": {
            "scout_crypto": {
                "tier": "scout",
                "asset_class": "crypto",
                "cycles_completed": 100,
                "strategies": 1,
            }
        }
    }
    shadow = {"total_signals": 0}

    # Mock send_alert to avoid actual Discord call
    import engines.learning.drought_detector as dd

    alerts_sent: list[tuple] = []
    original = dd.send_alert

    async def mock_alert(*args, **kwargs):  # type: ignore[no-untyped-def]
        alerts_sent.append((args, kwargs))
        return True

    dd.send_alert = mock_alert  # type: ignore[assignment]
    try:
        droughts = await detect_and_alert(status, shadow)
        assert len(droughts) == 1
        assert droughts[0]["job"] == "scout_crypto"
        assert len(alerts_sent) == 1
    finally:
        dd.send_alert = original


@pytest.mark.asyncio
async def test_no_drought_when_signals_exist():
    """No alert when there are signals."""
    status = {
        "tier_jobs": {
            "scout_crypto": {
                "tier": "scout",
                "asset_class": "crypto",
                "cycles_completed": 100,
                "strategies": 1,
            }
        }
    }
    shadow = {"total_signals": 5}
    droughts = await detect_and_alert(status, shadow)
    assert len(droughts) == 0


@pytest.mark.asyncio
async def test_drought_detects_per_job_zero_signals():
    """Per-job drought: one job with 0 signals should be flagged even if global total is nonzero."""
    status = {
        "tier_jobs": {
            "scout_equities": {
                "tier": "scout",
                "asset_class": "equities",
                "cycles_completed": 100,
                "strategies": 1,
                "signals_generated": 0,
            },
            "scout_crypto": {
                "tier": "scout",
                "asset_class": "crypto",
                "cycles_completed": 100,
                "strategies": 1,
                "signals_generated": 15,
            },
        }
    }
    shadow = {"total_signals": 15}  # Nonzero globally — old code would miss the drought

    import engines.learning.drought_detector as dd

    alerts_sent: list[tuple] = []
    original = dd.send_alert

    async def mock_alert(*args, **kwargs):  # type: ignore[no-untyped-def]
        alerts_sent.append((args, kwargs))
        return True

    dd.send_alert = mock_alert  # type: ignore[assignment]
    try:
        droughts = await detect_and_alert(status, shadow)
        assert len(droughts) == 1, f"Expected 1 drought, got {len(droughts)}: {droughts}"
        assert droughts[0]["job"] == "scout_equities"
    finally:
        dd.send_alert = original


@pytest.mark.asyncio
async def test_drought_no_false_alarm_when_job_has_signals():
    """No drought when the job itself has produced signals."""
    status = {
        "tier_jobs": {
            "core_equities": {
                "tier": "core",
                "asset_class": "equities",
                "cycles_completed": 100,
                "strategies": 1,
                "signals_generated": 5,
            }
        }
    }
    shadow = {"total_signals": 5}
    droughts = await detect_and_alert(status, shadow)
    assert len(droughts) == 0, f"Expected 0 droughts, got {len(droughts)}: {droughts}"


@pytest.mark.asyncio
async def test_sniper_not_flagged():
    """Snipers are expected to be rare — don't flag them."""
    status = {
        "tier_jobs": {
            "sniper_equities": {
                "tier": "sniper",
                "asset_class": "equities",
                "cycles_completed": 200,
                "strategies": 1,
            }
        }
    }
    shadow = {"total_signals": 0}
    droughts = await detect_and_alert(status, shadow)
    assert len(droughts) == 0
