"""
Discord webhook alerts for Sentinel.

Sends notifications for:
- Trade executed
- Risk limit hit / circuit breaker activated
- System errors
- Daily performance summary

Keeps ops simple — no PagerDuty, no email, just Discord.
"""

import logging
import os
from datetime import datetime
from enum import Enum

import httpx

logger = logging.getLogger(__name__)


class AlertLevel(str, Enum):
    INFO = "info"         # Trade executed, routine events
    WARNING = "warning"   # Risk limit approached, degraded performance
    CRITICAL = "critical" # Circuit breaker, system error, emergency


# Discord embed colors
COLORS = {
    AlertLevel.INFO: 0x2ECC71,      # Green
    AlertLevel.WARNING: 0xF39C12,   # Orange
    AlertLevel.CRITICAL: 0xE74C3C,  # Red
}

EMOJIS = {
    AlertLevel.INFO: "✅",
    AlertLevel.WARNING: "⚠️",
    AlertLevel.CRITICAL: "🚨",
}


async def send_alert(
    title: str,
    message: str,
    level: AlertLevel = AlertLevel.INFO,
    fields: dict[str, str] | None = None,
) -> bool:
    """
    Send an alert to Discord.

    Args:
        title: Alert title.
        message: Alert body text.
        level: Severity level.
        fields: Optional key-value pairs to display as embed fields.

    Returns:
        True if sent successfully, False otherwise.
    """
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        logger.warning("DISCORD_WEBHOOK_URL not set — alert suppressed: %s", title)
        return False

    embed = {
        "title": f"{EMOJIS[level]} {title}",
        "description": message,
        "color": COLORS[level],
        "timestamp": datetime.utcnow().isoformat(),
        "footer": {"text": "Sentinel Trading Platform"},
    }

    if fields:
        embed["fields"] = [
            {"name": k, "value": str(v), "inline": True}
            for k, v in fields.items()
        ]

    payload = {"embeds": [embed]}

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                webhook_url,
                json=payload,
                timeout=10.0,
            )
            response.raise_for_status()
            return True
    except httpx.HTTPError as e:
        logger.error("Failed to send Discord alert: %s", e)
        return False


async def _send_alert_with_color(
    title: str,
    message: str,
    color: int,
    fields: dict[str, str] | None = None,
) -> bool:
    """Send a Discord alert with an explicit embed color."""
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        logger.warning("DISCORD_WEBHOOK_URL not set — alert suppressed: %s", title)
        return False

    embed = {
        "title": title,
        "description": message,
        "color": color,
        "timestamp": datetime.utcnow().isoformat(),
        "footer": {"text": "Sentinel Trading Platform"},
    }

    if fields:
        embed["fields"] = [
            {"name": k, "value": str(v), "inline": True}
            for k, v in fields.items()
        ]

    payload = {"embeds": [embed]}

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                webhook_url,
                json=payload,
                timeout=10.0,
            )
            response.raise_for_status()
            return True
    except httpx.HTTPError as e:
        logger.error("Failed to send Discord alert: %s", e)
        return False


# --- Convenience functions ---

async def alert_trade_executed(
    symbol: str,
    side: str,
    quantity: float,
    price: float,
    strategy: str,
    pnl: float | None = None,
    platform: str = "",
) -> bool:
    """Alert when a trade is executed.

    Args:
        symbol: Trading pair (e.g. BTC-USD).
        side: BUY or SELL.
        quantity: Fill quantity.
        price: Fill price.
        strategy: Strategy ID that generated the signal.
        pnl: Realized P&L if closing a position.
        platform: Execution platform — starts with "paper_" for shadow sims.
    """
    is_paper = platform.startswith("paper_")
    mode_label = "PAPER" if is_paper else "LIVE"
    mode_emoji = "\U0001f4dd" if is_paper else "\u2705"  # 📝 vs ✅
    mode_color = 0x3498DB if is_paper else COLORS[AlertLevel.INFO]  # Blue vs Green

    fields = {
        "Symbol": symbol,
        "Side": side.upper(),
        "Quantity": f"{quantity:.4f}",
        "Price": f"${price:,.2f}",
        "Strategy": strategy,
        "Mode": mode_label,
    }
    if pnl is not None:
        fields["P&L"] = f"${pnl:,.2f}"

    title = f"{mode_emoji} {mode_label} Trade: {side.upper()} {symbol}"
    message = f"{strategy} executed {side} {quantity:.4f} {symbol} @ ${price:,.2f}"

    return await _send_alert_with_color(
        title=title,
        message=message,
        color=mode_color,
        fields=fields,
    )


async def alert_risk_event(
    event_type: str,
    details: str,
    portfolio_value: float,
) -> bool:
    """Alert when a risk event occurs."""
    return await send_alert(
        title=f"Risk Event: {event_type}",
        message=details,
        level=AlertLevel.WARNING,
        fields={"Portfolio Value": f"${portfolio_value:,.2f}"},
    )


async def alert_circuit_breaker(
    reason: str,
    portfolio_value: float,
    daily_pnl: float,
) -> bool:
    """Alert when circuit breaker activates."""
    return await send_alert(
        title="CIRCUIT BREAKER ACTIVATED",
        message=f"All trading halted. Reason: {reason}",
        level=AlertLevel.CRITICAL,
        fields={
            "Portfolio Value": f"${portfolio_value:,.2f}",
            "Daily P&L": f"${daily_pnl:,.2f}",
        },
    )


async def alert_system_error(error: str, component: str) -> bool:
    """Alert on system errors."""
    return await send_alert(
        title=f"System Error in {component}",
        message=error,
        level=AlertLevel.CRITICAL,
    )


async def alert_daily_summary(
    portfolio_value: float,
    daily_pnl: float,
    trades_count: int,
    win_rate: float,
    top_strategy: str,
) -> bool:
    """Send daily performance summary."""
    pnl_emoji = "📈" if daily_pnl >= 0 else "📉"
    return await send_alert(
        title=f"{pnl_emoji} Daily Summary",
        message=f"Portfolio: ${portfolio_value:,.2f} | Day P&L: ${daily_pnl:,.2f}",
        level=AlertLevel.INFO,
        fields={
            "Trades": str(trades_count),
            "Win Rate": f"{win_rate:.1f}%",
            "Top Strategy": top_strategy,
        },
    )
