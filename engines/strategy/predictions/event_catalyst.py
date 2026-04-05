"""
KCS-05: Event Catalyst Pre-Positioning Strategy.

Enters Kalshi crypto positions 1–5 days before scheduled macro events
(FOMC, CPI, NFP) when the probability model detects a divergence between
fair value and market pricing. The thesis: markets under-react to upcoming
catalysts until ~24h before the event, creating a window for pre-positioning.

Uses:
- Macro calendar (get_upcoming_catalysts) for event timing
- KCS-02 probability model (realized vol + log-normal binary model)
- Half-Kelly position sizing
- Volatility boost: pre-event vol is typically elevated, which widens
  the model's probability distribution and creates more mispricing

Tier: SNIPER (high-conviction, event-driven, rare)
Timeframe: realtime
Asset class: PREDICTIONS
"""

import logging
from datetime import date, datetime, timezone
from typing import Any

from config.tiers import StrategyTier
from engines.models import (
    AssetClass,
    MarketRegime,
    Signal,
    SignalStrength,
    Side,
    StrategyPerformance,
    StrategyStatus,
)
from engines.strategy.base import Strategy
from engines.strategy.predictions.macro_calendar import (
    CatalystType,
    get_upcoming_catalysts,
)
from engines.strategy.predictions.probability_model import (
    calc_binary_probability,
    calc_half_kelly,
    calc_realized_vol,
)

logger = logging.getLogger(__name__)

# Impact multiplier: scale edge threshold by event impact
_IMPACT_MULTIPLIER = {
    "high": 0.8,    # FOMC/CPI: lower threshold (higher conviction)
    "medium": 1.0,  # NFP: standard threshold
}

# Vol bump: before major events, implied vol tends to exceed realized.
# We nudge the model vol up to reflect this, widening the distribution.
_EVENT_VOL_BUMP = {
    CatalystType.FOMC: 1.15,  # +15% vol bump before FOMC
    CatalystType.CPI: 1.10,   # +10% before CPI
    CatalystType.NFP: 1.05,   # +5% before NFP
}


class EventCatalystStrategy(Strategy):
    """
    Kalshi crypto event catalyst pre-positioning (KCS-05).

    Scans Kalshi crypto contracts near upcoming macro events. When the
    probability model (with event vol adjustment) shows divergence from
    market pricing, generates a pre-positioning signal.

    SNIPER tier: rare, high-conviction, event-driven.
    """

    def __init__(
        self,
        strategy_id: str = "event_catalyst_prob_kalshi",
        parameters: dict[str, Any] | None = None,
    ):
        default_params: dict[str, Any] = {
            "min_edge_pp": 6.0,           # Lower than KCS-02 (event conviction)
            "min_volume": 30,             # Lower volume OK pre-event
            "max_spread": 0.06,           # Slightly wider spread OK
            "min_hours_to_expiry": 12,    # Don't trade < 12h to expiry
            "max_hours_to_expiry": 168,   # Don't trade > 7 days out
            "lookahead_days": 5,          # Look 5 days ahead for events
            "min_days_before_event": 1,   # Earliest: 1 day before
            "max_days_before_event": 5,   # Latest: 5 days before
            "position_size_usd": 150.0,   # Larger than KCS-02 (sniper)
            "max_signals": 2,             # Max 2 signals per cycle
            "scan_limit": 50,
        }
        if parameters:
            default_params.update(parameters)

        super().__init__(
            strategy_id=strategy_id,
            asset_class=AssetClass.PREDICTIONS,
            parameters=default_params,
            tier=StrategyTier.SNIPER,
            timeframe="realtime",
            max_signals_per_cycle=default_params["max_signals"],
        )

    async def generate_signals(
        self,
        bars: dict[str, list[dict]],
        market_regime: MarketRegime,
    ) -> list[Signal]:
        """
        Scan Kalshi crypto markets for pre-event positioning opportunities.

        Only generates signals when a macro catalyst is within the
        lookahead window AND the probability model shows divergence.

        Args:
            bars: Dict with "markets" (Kalshi contracts) and
                  "crypto_bars" (hourly BTC OHLCV).
            market_regime: Current market regime.

        Returns:
            0 to max_signals Signal objects.
        """
        # Check for upcoming catalysts
        today = date.today()
        catalysts = get_upcoming_catalysts(
            as_of=today,
            lookahead_days=self.parameters["lookahead_days"],
        )
        if not catalysts:
            logger.info("KCS-05: no catalysts in %d-day window", self.parameters["lookahead_days"])
            return []

        # Filter to actionable events (within pre-positioning window)
        actionable = self._filter_actionable(catalysts, today)
        if not actionable:
            logger.info(
                "KCS-05: %d catalysts found but none in %d–%d day pre-positioning window",
                len(catalysts),
                self.parameters["min_days_before_event"],
                self.parameters["max_days_before_event"],
            )
            return []

        logger.info(
            "KCS-05: %d actionable catalysts — %s",
            len(actionable),
            ", ".join(f"{c['name']} in {c['days_to_event']}d" for c in actionable),
        )

        markets = bars.get("markets", [])
        crypto_bars = bars.get("crypto_bars", [])

        if not markets:
            logger.warning("KCS-05: no markets to scan — check Kalshi API response")
            return []

        closes = [float(b["close"]) for b in crypto_bars if "close" in b]
        base_vol = calc_realized_vol(closes)
        if base_vol is None:
            logger.warning(
                "KCS-05: insufficient bars for vol (%d bars, need 48) — crypto_bars may be missing",
                len(closes),
            )
            return []

        spot = closes[-1] if closes else None
        if not spot or spot <= 0:
            return []

        # For each actionable event, scan markets with event-adjusted vol
        opportunities: list[dict] = []
        for catalyst in actionable:
            vol_bump = _EVENT_VOL_BUMP.get(catalyst["type"], 1.0)
            event_vol = base_vol * vol_bump
            impact_mult = _IMPACT_MULTIPLIER.get(catalyst["impact"], 1.0)
            effective_min_edge = self.parameters["min_edge_pp"] * impact_mult

            for market in markets[: self.parameters["scan_limit"]]:
                opp = self._evaluate_market(
                    market, spot, event_vol, effective_min_edge, catalyst,
                )
                if opp is not None:
                    opportunities.append(opp)

        if not opportunities:
            logger.warning(
                "KCS-05: 0 opportunities (events=%d, markets=%d, spot=%.0f, vol=%.2f)",
                len(actionable), len(markets), spot, base_vol,
            )
            return []

        # Sort by edge magnitude, take top signals
        opportunities.sort(key=lambda x: abs(x["edge_pp"]), reverse=True)

        # Deduplicate by ticker (same market might match multiple events)
        seen_tickers: set[str] = set()
        unique_opps: list[dict] = []
        for opp in opportunities:
            if opp["ticker"] not in seen_tickers:
                seen_tickers.add(opp["ticker"])
                unique_opps.append(opp)

        signals = []
        for opp in unique_opps[: self.parameters["max_signals"]]:
            signal = self._build_signal(opp, market_regime)
            signals.append(signal)
            logger.info(
                "KCS-05 %s %s: edge=%.1fpp event=%s days_to=%d model=%.1f%% market=%.1f%%",
                opp["side"], opp["ticker"], opp["edge_pp"],
                opp["catalyst_name"], opp["days_to_event"],
                opp["model_prob"] * 100, opp["market_prob"] * 100,
            )

        return signals

    def _filter_actionable(
        self, catalysts: list[dict], today: date,
    ) -> list[dict]:
        """Filter catalysts to those within the pre-positioning window.

        Args:
            catalysts: Upcoming catalyst dicts from the macro calendar.
            today: Current date.

        Returns:
            Catalysts where we should be pre-positioning.
        """
        min_days = self.parameters["min_days_before_event"]
        max_days = self.parameters["max_days_before_event"]
        result = []
        for c in catalysts:
            days_to = (c["date"] - today).days
            if min_days <= days_to <= max_days:
                result.append({**c, "days_to_event": days_to})
        return result

    def _evaluate_market(
        self,
        market: dict[str, Any],
        spot: float,
        event_vol: float,
        min_edge_pp: float,
        catalyst: dict,
    ) -> dict[str, Any] | None:
        """
        Evaluate a single Kalshi market for pre-event divergence.

        Args:
            market: Kalshi market dict.
            spot: Current BTC spot price.
            event_vol: Volatility adjusted for upcoming event.
            min_edge_pp: Minimum edge threshold (event-adjusted).
            catalyst: The catalyst dict driving this evaluation.

        Returns:
            Opportunity dict if edge is sufficient, None otherwise.
        """
        ticker = market.get("ticker", "")
        if not ticker:
            return None

        strike_price = market.get("strike_price")
        close_time_str = market.get("close_time")
        if strike_price is None or close_time_str is None:
            return None

        strike = float(strike_price)
        hours = self._hours_to_expiry(close_time_str)
        if hours is None:
            return None
        if hours < self.parameters["min_hours_to_expiry"]:
            return None
        if hours > self.parameters["max_hours_to_expiry"]:
            return None

        volume = int(market.get("volume", 0))
        if volume < self.parameters["min_volume"]:
            return None

        yes_ask = float(market.get("yes_ask", 0))
        yes_bid = float(market.get("yes_bid", 0))
        no_ask = float(market.get("no_ask", 0))
        yes_spread = yes_ask - yes_bid
        if yes_spread > self.parameters["max_spread"]:
            return None
        if yes_ask <= 0 or yes_ask >= 1:
            return None

        model_prob = calc_binary_probability(spot, strike, event_vol, hours)
        market_prob = yes_ask
        edge_pp = (model_prob - market_prob) * 100.0

        if abs(edge_pp) < min_edge_pp:
            return None

        if edge_pp >= min_edge_pp:
            side = "BUY_YES"
            trade_price = yes_ask
        else:
            side = "BUY_NO"
            trade_price = no_ask if no_ask > 0 else (1.0 - yes_bid)

        kelly = calc_half_kelly(model_prob, market_prob)

        return {
            "ticker": ticker,
            "title": market.get("title", ""),
            "side": side,
            "model_prob": model_prob,
            "market_prob": market_prob,
            "edge_pp": edge_pp,
            "kelly": kelly,
            "trade_price": trade_price,
            "yes_spread": yes_spread,
            "volume": volume,
            "hours_to_expiry": hours,
            "catalyst_type": catalyst["type"],
            "catalyst_name": catalyst["name"],
            "days_to_event": catalyst["days_to_event"],
            "event_vol": event_vol,
        }

    @staticmethod
    def _hours_to_expiry(close_time_str: str) -> float | None:
        """Parse ISO close_time and return hours until expiry."""
        try:
            close_dt = datetime.fromisoformat(
                close_time_str.replace("Z", "+00:00")
            )
            now = datetime.now(tz=timezone.utc)
            delta = close_dt - now
            return max(delta.total_seconds() / 3600.0, 0.0)
        except (ValueError, AttributeError):
            return None

    def _build_signal(
        self, opp: dict[str, Any], market_regime: MarketRegime,
    ) -> Signal:
        """Build a Signal from an evaluated opportunity."""
        is_yes = opp["side"] == "BUY_YES"
        price = opp["trade_price"]
        position_size_usd = self.parameters["position_size_usd"]

        kelly = opp["kelly"]
        kelly_scaled_usd = position_size_usd * min(kelly * 2, 1.0)
        effective_usd = max(kelly_scaled_usd, position_size_usd * 0.25)
        effective_usd = min(effective_usd, position_size_usd)

        contracts = int(effective_usd / price) if price > 0 else 1
        contracts = max(contracts, 1)

        confidence = self._calc_confidence(opp)

        rationale = (
            f"PRE-EVENT {'YES' if is_yes else 'NO'} @ ${price:.3f} "
            f"[{opp['catalyst_name']}] "
            f"(model: {opp['model_prob']*100:.1f}%, "
            f"market: {opp['market_prob']*100:.1f}%, "
            f"edge: {opp['edge_pp']:.1f}pp, "
            f"event_vol: {opp['event_vol']:.2f}, "
            f"days_to_event: {opp['days_to_event']})"
        )

        return Signal(
            strategy_id=self.strategy_id,
            asset_class=self.asset_class,
            symbol=opp["ticker"],
            side=Side.BUY if is_yes else Side.SELL,
            quantity=float(contracts),
            target_price=price,
            confidence=confidence,
            strength=self._classify_strength(confidence),
            rationale=rationale,
            market_regime=market_regime,
            position_size_usd=effective_usd,
            tier=self.tier,
        )

    @staticmethod
    def _calc_confidence(opp: dict[str, Any]) -> float:
        """
        Calculate confidence from edge, kelly, spread, event proximity.

        Components:
        - Edge magnitude: 0–0.35 (capped at 15pp)
        - Kelly fraction: 0–0.20 (capped at 0.3 kelly)
        - Spread tightness: 0–0.15
        - Event proximity: 0–0.15 (closer = more confident)
        - Event impact: 0–0.15 (high impact = bonus)
        """
        edge_pp = abs(opp["edge_pp"])
        kelly = opp["kelly"]
        yes_spread = opp["yes_spread"]
        days_to = opp["days_to_event"]
        catalyst_type = opp["catalyst_type"]

        edge_score = min(edge_pp / 15.0, 1.0) * 0.35
        kelly_score = min(kelly / 0.3, 1.0) * 0.20
        spread_score = max(1.0 - yes_spread / 0.06, 0.0) * 0.15

        # Closer to event = more confidence (1 day = full, 5 days = low)
        proximity_score = max(1.0 - (days_to - 1) / 4.0, 0.0) * 0.15

        # High-impact events get a bonus
        impact_bonus = 0.15 if catalyst_type in (CatalystType.FOMC, CatalystType.CPI) else 0.08

        confidence = edge_score + kelly_score + spread_score + proximity_score + impact_bonus
        return min(max(confidence, 0.1), 1.0)

    @staticmethod
    def _classify_strength(confidence: float) -> SignalStrength:
        if confidence >= 0.8:
            return SignalStrength.STRONG
        elif confidence >= 0.6:
            return SignalStrength.MODERATE
        return SignalStrength.WEAK

    async def get_performance(self, period_days: int) -> StrategyPerformance:
        return StrategyPerformance(
            strategy_id=self.strategy_id,
            period_days=period_days,
            trades_count=0,
            win_rate=0.0,
            total_pnl=0.0,
            max_drawdown=0.0,
            risk_budget_used_pct=0.0,
            status=self.status,
        )
