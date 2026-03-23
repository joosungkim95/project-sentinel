"""
Discord Alerts — Sends notifications via webhook.

Used for: trade executions, risk events, system errors, daily summaries.
Free tier, no ops overhead.
"""

import logging
from datetime import datetime
from enum import Enum
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


class AlertLevel(str, Enum):
    INFO = "info"           # Trade executed, strategy activated
    WARNING = "warning"     # Risk limit approaching, API slowdown
    CRITICAL = "critical"   # Circuit breaker, hard floor hit, system error


# Discord embed colors by level
COLORS = {
    AlertLevel.INFO: 0x2ECC71,      # Green
    AlertLevel.WARNING: 0xF39C12,   # Orange
    AlertLevel.CRITICAL: 0xE74C3C,  # Red
}

EMOJIS = {
    AlertLevel.INFO: "\u2705",
    AlertLevel.WARNING: "\u26A0\uFE0F",
    AlertLevel.CRITICAL: "\U0001F6A8",
}


class AlertService:
    """Sends alerts to Discord via webhook."""

    def __init__(self, webhook_url: Optional[str] = None):
        self.webhook_url = webhook_url
        self._client = httpx.AsyncClient(timeout=10.0)

    async def send(
        self,
        title: str,
        message: str,
        level: AlertLevel = AlertLevel.INFO,
        fields: Optional[dict[str, str]] = None,
    ) -> bool:
        """
        Send an alert to Discord.

        Args:
            title: Alert title (bold in embed).
            message: Alert body text.
            level: Severity level (affects color and emoji).
            fields: Optional key-value pairs shown as embed fields.

        Returns:
            True if sent successfully, False otherwise.
        """
        if not self.webhook_url:
            logger.debug("Discord webhook not configured, skipping alert: %s", title)
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
            response = await self._client.post(
                self.webhook_url, json=payload
            )
            if response.status_code == 204:
                return True
            logger.warning(
                "Discord webhook returned %d: %s",
                response.status_code,
                response.text,
            )
            return False
        except httpx.HTTPError as e:
            logger.error("Failed to send Discord alert: %s", e)
            return False

    async def trade_executed(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        strategy: str,
        platform: str,
    ) -> bool:
        """Send a trade execution alert."""
        return await self.send(
            title=f"Trade Executed: {side.upper()} {symbol}",
            message=f"Strategy **{strategy}** executed on {platform}",
            level=AlertLevel.INFO,
            fields={
                "Symbol": symbol,
                "Side": side.upper(),
                "Quantity": f"{quantity:.4f}",
                "Price": f"${price:.2f}",
                "Platform": platform,
            },
        )

    async def risk_event(
        self,
        event_type: str,
        details: str,
        portfolio_value: float,
    ) -> bool:
        """Send a risk event alert."""
        return await self.send(
            title=f"Risk Event: {event_type}",
            message=details,
            level=AlertLevel.WARNING,
            fields={
                "Portfolio Value": f"${portfolio_value:,.2f}",
            },
        )

    async def circuit_breaker(
        self,
        reason: str,
        portfolio_value: float,
    ) -> bool:
        """Send a circuit breaker activation alert."""
        return await self.send(
            title="CIRCUIT BREAKER ACTIVATED",
            message=f"All trading halted. Reason: {reason}",
            level=AlertLevel.CRITICAL,
            fields={
                "Portfolio Value": f"${portfolio_value:,.2f}",
                "Action Required": "Review and manually reset when ready",
            },
        )

    async def daily_summary(
        self,
        portfolio_value: float,
        daily_pnl: float,
        trades_count: int,
        win_rate: float,
    ) -> bool:
        """Send end-of-day summary."""
        pnl_emoji = "\U0001F4C8" if daily_pnl >= 0 else "\U0001F4C9"
        return await self.send(
            title=f"{pnl_emoji} Daily Summary",
            message="End of day trading summary",
            level=AlertLevel.INFO,
            fields={
                "Portfolio": f"${portfolio_value:,.2f}",
                "Daily P&L": f"${daily_pnl:,.2f}",
                "Trades": str(trades_count),
                "Win Rate": f"{win_rate:.1f}%",
            },
        )

    async def system_error(self, error: str, context: str = "") -> bool:
        """Send a system error alert."""
        return await self.send(
            title="System Error",
            message=f"```{error}```\n{context}" if context else f"```{error}```",
            level=AlertLevel.CRITICAL,
        )

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()
