"""Tests for the macro catalyst calendar."""
from datetime import date
import pytest
from engines.strategy.predictions.macro_calendar import get_upcoming_catalysts, CatalystType


class TestGetUpcomingCatalysts:
    def test_returns_catalysts_within_window(self):
        """Should return catalysts within the lookahead window."""
        catalysts = get_upcoming_catalysts(as_of=date(2026, 5, 1), lookahead_days=7)
        fomc = [c for c in catalysts if c["type"] == CatalystType.FOMC]
        assert len(fomc) >= 1
        assert fomc[0]["date"] >= date(2026, 5, 1)
        assert fomc[0]["date"] <= date(2026, 5, 8)

    def test_excludes_past_events(self):
        """Should not return catalysts before as_of date."""
        catalysts = get_upcoming_catalysts(as_of=date(2026, 12, 31), lookahead_days=7)
        for c in catalysts:
            assert c["date"] >= date(2026, 12, 31)

    def test_includes_btc_bias(self):
        """Each catalyst should have a btc_bias field."""
        catalysts = get_upcoming_catalysts(as_of=date(2026, 1, 1), lookahead_days=365)
        assert len(catalysts) > 0
        for c in catalysts:
            assert "btc_bias" in c
            assert c["btc_bias"] in ("bullish", "bearish", "neutral", "depends_on_outcome")

    def test_empty_for_distant_future(self):
        """Far future dates with no calendar → empty list."""
        catalysts = get_upcoming_catalysts(as_of=date(2030, 1, 1), lookahead_days=7)
        assert len(catalysts) == 0
