"""
KCS-02: Implied Probability vs. Spot Price Divergence Model.

Compares Kalshi crypto contract prices (implied probabilities) against a
log-normal model probability derived from realized volatility. Signals when
the divergence (edge) between model and market exceeds a threshold.

Strategy logic:
- Uses hourly OHLCV crypto bars to estimate realized volatility.
- Applies a log-normal binary probability model to estimate fair value.
- Compares model probability to Kalshi market's yes_ask price.
- BUY YES when model_prob >> market_prob (market underpricing YES).
- BUY NO (Side.SELL) when model_prob << market_prob (market overpricing YES).
- Sizes positions using half-Kelly criterion.

Tier: CORE
Timeframe: realtime
Asset class: PREDICTIONS
"""

import logging
from datetime import datetime, timezone
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
from engines.strategy.predictions.probability_model import (
    calc_binary_probability,
    calc_half_kelly,
    calc_realized_vol,
)

logger = logging.getLogger(__name__)


class CryptoProbabilityStrategy(Strategy):
    """
    Kalshi crypto probability divergence strategy (KCS-02).

    Finds Kalshi crypto contracts where the market-implied probability
    diverges significantly from a log-normal model probability. Trades
    the mispriced side, sized by half-Kelly.

    CORE tier: conservative thresholds, up to 3 signals per cycle.
    """

    def __init__(
        self,
        strategy_id: str = "crypto_probability_kalshi",
        parameters: dict[str, Any] | None = None,
    ):
        """
        Initialize KCS-02 strategy.

        Args:
            strategy_id: Unique identifier for this strategy instance.
            parameters: Override default parameters (merged into defaults).
        """
        default_params: dict[str, Any] = {
            "min_edge_pp": 8.0,         # Minimum edge in percentage points
            "min_volume": 50,           # Minimum contracts traded
            "max_spread": 0.05,         # Max yes bid-ask spread (dollars)
            "min_hours_to_expiry": 6,   # Don't trade < 6h to expiry
            "position_size_usd": 100.0,
            "max_signals": 3,
            "scan_limit": 50,
        }
        if parameters:
            default_params.update(parameters)

        super().__init__(
            strategy_id=strategy_id,
            asset_class=AssetClass.PREDICTIONS,
            parameters=default_params,
            tier=StrategyTier.CORE,
            timeframe="realtime",
            max_signals_per_cycle=default_params["max_signals"],
        )

    async def generate_signals(
        self,
        bars: dict[str, list[dict]],
        market_regime: MarketRegime,
    ) -> list[Signal]:
        """
        Scan Kalshi crypto markets and generate divergence signals.

        Args:
            bars: Dict containing:
                  - "markets": list of Kalshi market dicts (ticker, yes_ask, etc.)
                  - "crypto_bars": list of hourly OHLCV dicts for the crypto asset
            market_regime: Current market regime classification.

        Returns:
            List of 0 to max_signals Signal objects, ranked by edge magnitude.
        """
        markets = bars.get("markets", [])
        crypto_bars = bars.get("crypto_bars", [])

        if not markets:
            logger.warning("KCS-02: no markets to scan — check Kalshi API response")
            return []

        # Extract close prices once for vol calculation
        closes = [float(b["close"]) for b in crypto_bars if "close" in b]
        vol = calc_realized_vol(closes)

        if vol is None:
            logger.warning(
                "KCS-02: insufficient bars for vol (%d bars, need 48) — crypto_bars may be missing",
                len(closes),
            )
            return []

        # Current spot = last close price
        spot = closes[-1] if closes else None
        if spot is None or spot <= 0:
            logger.warning("KCS-02: no valid spot price from crypto bars")
            return []

        opportunities: list[dict[str, Any]] = []
        scan_limit = self.parameters["scan_limit"]
        skip_reasons: dict[str, int] = {}

        for market in markets[:scan_limit]:
            opp = self._evaluate_market(market, spot, vol)
            if opp is not None:
                opportunities.append(opp)
            else:
                # Track why markets were skipped
                ticker = market.get("ticker", "?")
                if not market.get("strike_price") or not market.get("close_time"):
                    skip_reasons["missing_fields"] = skip_reasons.get("missing_fields", 0) + 1
                elif int(market.get("volume", 0)) < self.parameters["min_volume"]:
                    skip_reasons["low_volume"] = skip_reasons.get("low_volume", 0) + 1
                else:
                    skip_reasons["no_edge"] = skip_reasons.get("no_edge", 0) + 1

        if not opportunities:
            logger.warning(
                "KCS-02: 0 opportunities in %d markets (spot=%.0f vol=%.2f) — skips: %s",
                len(markets), spot, vol, dict(skip_reasons),
            )
            return []

        # Sort by absolute edge magnitude (best first)
        opportunities.sort(key=lambda x: abs(x["edge_pp"]), reverse=True)

        max_signals = self.parameters["max_signals"]
        signals = []
        for opp in opportunities[:max_signals]:
            signal = self._build_signal(opp, market_regime)
            signals.append(signal)
            logger.info(
                "KCS-02 %s %s: model=%.1f%% market=%.1f%% edge=%.1fpp kelly=%.3f",
                opp["side"],
                opp["ticker"],
                opp["model_prob"] * 100,
                opp["market_prob"] * 100,
                opp["edge_pp"],
                opp["kelly"],
            )

        return signals

    def _evaluate_market(
        self,
        market: dict[str, Any],
        spot: float,
        vol: float,
    ) -> dict[str, Any] | None:
        """
        Evaluate a single Kalshi crypto market for a divergence opportunity.

        Args:
            market: Kalshi market dict (ticker, yes_ask, yes_bid, volume, etc.).
            spot: Current crypto spot price.
            vol: Annualized realized volatility.

        Returns:
            Opportunity dict if edge >= min_edge_pp, None otherwise.
        """
        ticker = market.get("ticker", "")
        if not ticker:
            return None

        strike_price = market.get("strike_price")
        close_time_str = market.get("close_time")
        if strike_price is None or close_time_str is None:
            return None

        strike = float(strike_price)

        # Calculate hours to expiry
        hours_to_expiry = self._hours_to_expiry(close_time_str)
        if hours_to_expiry is None:
            return None
        if hours_to_expiry < self.parameters["min_hours_to_expiry"]:
            logger.debug("KCS-02: skipping %s — %.1fh to expiry", ticker, hours_to_expiry)
            return None

        # Liquidity filter
        volume = int(market.get("volume", 0))
        if volume < self.parameters["min_volume"]:
            return None

        yes_ask = float(market.get("yes_ask", 0))
        yes_bid = float(market.get("yes_bid", 0))
        no_ask = float(market.get("no_ask", 0))

        # Spread filter (yes side)
        yes_spread = yes_ask - yes_bid
        if yes_spread > self.parameters["max_spread"]:
            return None

        if yes_ask <= 0 or yes_ask >= 1:
            return None

        # Model probability
        model_prob = calc_binary_probability(spot, strike, vol, hours_to_expiry)

        # Market implied probability = yes_ask price
        market_prob = yes_ask
        edge_pp = (model_prob - market_prob) * 100.0
        abs_edge = abs(edge_pp)

        if abs_edge < self.parameters["min_edge_pp"]:
            return None

        # Determine side
        if edge_pp >= self.parameters["min_edge_pp"]:
            # Model says higher than market → BUY YES
            side = "BUY_YES"
            trade_price = yes_ask
        else:
            # Model says lower than market → BUY NO
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
            "hours_to_expiry": hours_to_expiry,
        }

    @staticmethod
    def _hours_to_expiry(close_time_str: str) -> float | None:
        """
        Parse ISO 8601 close_time and return hours until expiry.

        Args:
            close_time_str: ISO 8601 datetime string (e.g. "2026-04-01T23:59:59Z").

        Returns:
            Hours as float, or None if parsing fails.
        """
        try:
            close_dt = datetime.fromisoformat(close_time_str.replace("Z", "+00:00"))
            now = datetime.now(tz=timezone.utc)
            delta = close_dt - now
            return max(delta.total_seconds() / 3600.0, 0.0)
        except (ValueError, AttributeError):
            return None

    def _build_signal(
        self, opp: dict[str, Any], market_regime: MarketRegime
    ) -> Signal:
        """
        Build a Signal from an evaluated opportunity.

        Args:
            opp: Opportunity dict from _evaluate_market.
            market_regime: Current market regime.

        Returns:
            Signal with all fields populated.
        """
        is_yes = opp["side"] == "BUY_YES"
        price = opp["trade_price"]
        position_size_usd = self.parameters["position_size_usd"]

        # Scale position by Kelly fraction (capped at full size)
        kelly = opp["kelly"]
        kelly_scaled_usd = position_size_usd * min(kelly * 2, 1.0)  # scale up from half-Kelly
        effective_usd = max(kelly_scaled_usd, position_size_usd * 0.25)  # floor at 25%
        effective_usd = min(effective_usd, position_size_usd)

        contracts = int(effective_usd / price) if price > 0 else 1
        contracts = max(contracts, 1)

        confidence = self._calc_confidence(opp)

        rationale = (
            f"{'YES' if is_yes else 'NO'} @ ${price:.3f} "
            f"(model: {opp['model_prob']*100:.1f}%, market: {opp['market_prob']*100:.1f}%, "
            f"edge: {opp['edge_pp']:.1f}pp, kelly: {opp['kelly']:.3f}, "
            f"spread: ${opp['yes_spread']:.3f}, vol: {opp['volume']}, "
            f"expiry: {opp['hours_to_expiry']:.1f}h)"
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
        Calculate signal confidence from edge, Kelly fraction, spread, and volume.

        Components:
        - Edge magnitude: 0–0.45 (capped at 15pp edge)
        - Kelly fraction: 0–0.25 (capped at 0.3 kelly)
        - Spread tightness: 0–0.15 (tighter = better)
        - Volume: 0–0.15 (capped at 500 contracts)

        Args:
            opp: Opportunity dict with edge_pp, kelly, yes_spread, volume.

        Returns:
            Confidence float in [0.1, 1.0].
        """
        edge_pp = abs(opp["edge_pp"])
        kelly = opp["kelly"]
        yes_spread = opp["yes_spread"]
        volume = opp["volume"]

        # Edge contribution (0-0.50): 12pp = full score
        edge_score = min(edge_pp / 12.0, 1.0) * 0.50

        # Kelly contribution (0-0.25): kelly=0.25 = full score
        kelly_score = min(kelly / 0.25, 1.0) * 0.25

        # Spread tightness (0-0.15): 0 spread = full, max_spread = 0
        max_spread = 0.05
        spread_score = max(1.0 - yes_spread / max_spread, 0.0) * 0.15

        # Volume contribution (0-0.10): 400 contracts = full score
        vol_score = min(volume / 400.0, 1.0) * 0.10

        confidence = edge_score + kelly_score + spread_score + vol_score
        return min(max(confidence, 0.15), 1.0)

    @staticmethod
    def _classify_strength(confidence: float) -> SignalStrength:
        """
        Map confidence score to signal strength category.

        Args:
            confidence: Float in [0, 1].

        Returns:
            SignalStrength enum value.
        """
        if confidence >= 0.8:
            return SignalStrength.STRONG
        elif confidence >= 0.6:
            return SignalStrength.MODERATE
        return SignalStrength.WEAK

    async def get_performance(self, period_days: int) -> StrategyPerformance:
        """
        Calculate performance metrics over a given period.

        Args:
            period_days: Number of days to look back.

        Returns:
            StrategyPerformance with stub values (DB not yet wired).
        """
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
