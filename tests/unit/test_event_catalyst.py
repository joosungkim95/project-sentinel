"""
Unit tests for KCS-05: Event Catalyst Pre-Positioning Strategy.
"""

import pytest
from datetime import date, datetime, timezone, timedelta
from unittest.mock import patch

from engines.models import MarketRegime, Side, SignalStrength
from engines.strategy.predictions.event_catalyst import EventCatalystStrategy
from engines.strategy.predictions.macro_calendar import CatalystType


def _make_market(
    ticker: str = "KXBTCD-26APR05-B70000",
    yes_ask: float = 0.50,
    yes_bid: float = 0.48,
    no_ask: float = 0.51,
    volume: int = 200,
    strike_price: float = 70000.0,
    hours_ahead: float = 72.0,
) -> dict:
    """Build a Kalshi market dict for testing."""
    close_time = datetime.now(tz=timezone.utc) + timedelta(hours=hours_ahead)
    return {
        "ticker": ticker,
        "title": f"Will BTC > ${strike_price:,.0f}?",
        "yes_ask": yes_ask,
        "yes_bid": yes_bid,
        "no_ask": no_ask,
        "no_bid": 1.0 - yes_ask,
        "volume": volume,
        "strike_price": strike_price,
        "close_time": close_time.isoformat(),
    }


def _make_crypto_bars(count: int = 750, base_price: float = 72000.0) -> list[dict]:
    """Build synthetic hourly BTC bars."""
    bars = []
    for i in range(count):
        price = base_price + (i % 50 - 25) * 10  # ±$250 oscillation
        bars.append({
            "open": price - 5,
            "high": price + 50,
            "low": price - 50,
            "close": price,
            "volume": 100.0,
            "timestamp": (
                datetime.now(tz=timezone.utc) - timedelta(hours=count - i)
            ).isoformat(),
        })
    return bars


def _mock_catalysts(days_ahead: int = 3, catalyst_type: CatalystType = CatalystType.FOMC):
    """Return a mock catalyst list."""
    return [{
        "date": date.today() + timedelta(days=days_ahead),
        "type": catalyst_type,
        "name": f"FOMC Rate Decision (test)",
        "btc_bias": "depends_on_outcome",
        "impact": "high",
        "notes": "Test catalyst",
    }]


class TestEventCatalystStrategy:
    """Core strategy tests."""

    def test_init_defaults(self) -> None:
        s = EventCatalystStrategy()
        assert s.strategy_id == "event_catalyst_prob_kalshi"
        assert s.tier.value == "sniper"
        assert s.timeframe == "realtime"
        assert s.parameters["min_edge_pp"] == 4.0
        assert s.parameters["lookahead_days"] == 5

    def test_custom_params(self) -> None:
        s = EventCatalystStrategy(parameters={"min_edge_pp": 10.0})
        assert s.parameters["min_edge_pp"] == 10.0
        assert s.parameters["position_size_usd"] == 150.0  # default preserved


class TestSignalGeneration:
    """Test generate_signals under various conditions."""

    @pytest.mark.asyncio
    async def test_no_catalysts_returns_empty(self) -> None:
        s = EventCatalystStrategy()
        s.activate()
        with patch(
            "engines.strategy.predictions.event_catalyst.get_upcoming_catalysts",
            return_value=[],
        ):
            signals = await s.generate_signals(
                bars={"markets": [_make_market()], "crypto_bars": _make_crypto_bars()},
                market_regime=MarketRegime.UNKNOWN,
            )
        assert signals == []

    @pytest.mark.asyncio
    async def test_catalyst_outside_window_returns_empty(self) -> None:
        s = EventCatalystStrategy()
        s.activate()
        # Catalyst is 10 days away (max is 5)
        with patch(
            "engines.strategy.predictions.event_catalyst.get_upcoming_catalysts",
            return_value=_mock_catalysts(days_ahead=10),
        ):
            signals = await s.generate_signals(
                bars={"markets": [_make_market()], "crypto_bars": _make_crypto_bars()},
                market_regime=MarketRegime.UNKNOWN,
            )
        assert signals == []

    @pytest.mark.asyncio
    async def test_generates_signal_with_edge(self) -> None:
        """When market underprices a contract near an event, should signal."""
        s = EventCatalystStrategy()
        s.activate()
        # Market says 40% chance, but with BTC at 72000 and strike at 70000
        # the model should say much higher → BUY YES signal
        market = _make_market(
            strike_price=70000.0,
            yes_ask=0.40,
            yes_bid=0.38,
            no_ask=0.61,
            volume=200,
            hours_ahead=72.0,
        )
        with patch(
            "engines.strategy.predictions.event_catalyst.get_upcoming_catalysts",
            return_value=_mock_catalysts(days_ahead=3),
        ):
            signals = await s.generate_signals(
                bars={"markets": [market], "crypto_bars": _make_crypto_bars()},
                market_regime=MarketRegime.RANGING,
            )
        assert len(signals) >= 1
        assert signals[0].side == Side.BUY
        assert "PRE-EVENT" in signals[0].rationale
        assert "FOMC" in signals[0].rationale

    @pytest.mark.asyncio
    async def test_no_markets_returns_empty(self) -> None:
        s = EventCatalystStrategy()
        s.activate()
        with patch(
            "engines.strategy.predictions.event_catalyst.get_upcoming_catalysts",
            return_value=_mock_catalysts(days_ahead=3),
        ):
            signals = await s.generate_signals(
                bars={"markets": [], "crypto_bars": _make_crypto_bars()},
                market_regime=MarketRegime.UNKNOWN,
            )
        assert signals == []

    @pytest.mark.asyncio
    async def test_insufficient_bars_returns_empty(self) -> None:
        s = EventCatalystStrategy()
        s.activate()
        with patch(
            "engines.strategy.predictions.event_catalyst.get_upcoming_catalysts",
            return_value=_mock_catalysts(days_ahead=3),
        ):
            signals = await s.generate_signals(
                bars={"markets": [_make_market()], "crypto_bars": _make_crypto_bars(count=10)},
                market_regime=MarketRegime.UNKNOWN,
            )
        assert signals == []

    @pytest.mark.asyncio
    async def test_caps_at_max_signals(self) -> None:
        s = EventCatalystStrategy(parameters={"max_signals": 1})
        s.activate()
        markets = [
            _make_market(
                ticker=f"KXBTCD-TEST-B{strike}",
                strike_price=float(strike),
                yes_ask=0.30,
                yes_bid=0.28,
                no_ask=0.71,
                volume=200,
            )
            for strike in [65000, 60000, 55000]
        ]
        with patch(
            "engines.strategy.predictions.event_catalyst.get_upcoming_catalysts",
            return_value=_mock_catalysts(days_ahead=2),
        ):
            signals = await s.generate_signals(
                bars={"markets": markets, "crypto_bars": _make_crypto_bars()},
                market_regime=MarketRegime.UNKNOWN,
            )
        assert len(signals) <= 1


class TestConfidence:
    """Confidence calculation tests."""

    def test_confidence_bounded(self) -> None:
        opp = {
            "edge_pp": 50.0,
            "kelly": 1.0,
            "yes_spread": 0.0,
            "days_to_event": 1,
            "catalyst_type": CatalystType.FOMC,
        }
        conf = EventCatalystStrategy._calc_confidence(opp)
        assert 0.1 <= conf <= 1.0

    def test_fomc_higher_than_nfp(self) -> None:
        base = {
            "edge_pp": 10.0,
            "kelly": 0.15,
            "yes_spread": 0.02,
            "days_to_event": 2,
        }
        fomc = {**base, "catalyst_type": CatalystType.FOMC}
        nfp = {**base, "catalyst_type": CatalystType.NFP}
        assert EventCatalystStrategy._calc_confidence(fomc) > EventCatalystStrategy._calc_confidence(nfp)

    def test_closer_event_higher_confidence(self) -> None:
        base = {
            "edge_pp": 10.0,
            "kelly": 0.15,
            "yes_spread": 0.02,
            "catalyst_type": CatalystType.CPI,
        }
        close = {**base, "days_to_event": 1}
        far = {**base, "days_to_event": 5}
        assert EventCatalystStrategy._calc_confidence(close) > EventCatalystStrategy._calc_confidence(far)


class TestFilterActionable:
    """Test the event filtering logic."""

    def test_filters_within_window(self) -> None:
        s = EventCatalystStrategy()
        today = date(2026, 3, 29)
        catalysts = [
            {"date": date(2026, 3, 30), "type": CatalystType.CPI, "name": "CPI", "impact": "high"},  # 1 day
            {"date": date(2026, 4, 1), "type": CatalystType.FOMC, "name": "FOMC", "impact": "high"},  # 3 days
            {"date": date(2026, 4, 10), "type": CatalystType.NFP, "name": "NFP", "impact": "medium"},  # 12 days
        ]
        result = s._filter_actionable(catalysts, today)
        assert len(result) == 2  # CPI (1 day) and FOMC (3 days), not NFP (12 days)
        assert result[0]["days_to_event"] == 1
        assert result[1]["days_to_event"] == 3

    def test_today_excluded(self) -> None:
        """Events happening today (0 days) are excluded (min_days=1)."""
        s = EventCatalystStrategy()
        today = date(2026, 3, 29)
        catalysts = [
            {"date": date(2026, 3, 29), "type": CatalystType.CPI, "name": "CPI", "impact": "high"},
        ]
        result = s._filter_actionable(catalysts, today)
        assert len(result) == 0
