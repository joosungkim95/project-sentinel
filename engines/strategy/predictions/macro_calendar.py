"""
Macro catalyst calendar for crypto prediction markets.

Provides a static calendar of scheduled macro events that historically
move BTC. Used by KCS-05 (event catalyst pre-positioning) and eventually
the market regime classifier.

The calendar is hardcoded for 2026. Extend annually or replace with an
API source (CoinMarketCal, Federal Reserve, BLS) when available.
"""

from datetime import date, timedelta
from enum import Enum


class CatalystType(str, Enum):
    FOMC = "fomc"
    CPI = "cpi"
    NFP = "nfp"
    ETF_FLOW = "etf_flow"
    HALVING = "halving"
    REGULATORY = "regulatory"


# 2026 FOMC meeting dates (announcement days)
_FOMC_2026 = [
    date(2026, 1, 28), date(2026, 3, 18), date(2026, 5, 6),
    date(2026, 6, 17), date(2026, 7, 29), date(2026, 9, 16),
    date(2026, 11, 4), date(2026, 12, 16),
]

# 2026 CPI release dates (typically second week of month)
_CPI_2026 = [
    date(2026, 1, 14), date(2026, 2, 12), date(2026, 3, 11),
    date(2026, 4, 14), date(2026, 5, 13), date(2026, 6, 10),
    date(2026, 7, 15), date(2026, 8, 12), date(2026, 9, 16),
    date(2026, 10, 14), date(2026, 11, 12), date(2026, 12, 10),
]

# 2026 NFP release dates (first Friday of month)
_NFP_2026 = [
    date(2026, 1, 9), date(2026, 2, 6), date(2026, 3, 6),
    date(2026, 4, 3), date(2026, 5, 1), date(2026, 6, 5),
    date(2026, 7, 2), date(2026, 8, 7), date(2026, 9, 4),
    date(2026, 10, 2), date(2026, 11, 6), date(2026, 12, 4),
]


_CATALYST_CALENDAR: list[dict] = []

for d in _FOMC_2026:
    _CATALYST_CALENDAR.append({
        "date": d, "type": CatalystType.FOMC,
        "name": f"FOMC Rate Decision ({d.strftime('%b %d')})",
        "btc_bias": "depends_on_outcome", "impact": "high",
        "notes": "Dovish = BTC bullish, Hawkish = BTC bearish",
    })

for d in _CPI_2026:
    _CATALYST_CALENDAR.append({
        "date": d, "type": CatalystType.CPI,
        "name": f"CPI Release ({d.strftime('%b %d')})",
        "btc_bias": "depends_on_outcome", "impact": "high",
        "notes": "Below expectations = BTC bullish (dovish signal)",
    })

for d in _NFP_2026:
    _CATALYST_CALENDAR.append({
        "date": d, "type": CatalystType.NFP,
        "name": f"Non-Farm Payrolls ({d.strftime('%b %d')})",
        "btc_bias": "depends_on_outcome", "impact": "medium",
        "notes": "Weak jobs = dovish signal = BTC bullish",
    })

_CATALYST_CALENDAR.sort(key=lambda x: x["date"])


def get_upcoming_catalysts(as_of: date, lookahead_days: int = 7) -> list[dict]:
    """Get scheduled catalysts within a lookahead window.

    Args:
        as_of: Reference date (typically today).
        lookahead_days: How many days ahead to look.

    Returns:
        List of catalyst dicts sorted by date.
    """
    end = as_of + timedelta(days=lookahead_days)
    return [c for c in _CATALYST_CALENDAR if as_of <= c["date"] <= end]
