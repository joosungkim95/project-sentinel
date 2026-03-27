"""
Value Pricing Strategy — Find mispriced prediction markets on Kalshi.

Scans open Kalshi markets for pricing inefficiencies:

1. Spread capture — where yes_bid + no_bid < 1.0 (free edge)
2. Extreme value — contracts priced near 0 or 1 that have clear edges
3. Volume/liquidity filter — only trade markets with enough volume

Signal logic:
- BUY YES: when yes_bid is significantly below implied fair value
- BUY NO (SELL): when no_bid is significantly below implied fair value
  (equivalent to selling YES)

Pricing model (simple, pre-Learning Engine):
- Fair value = 1 - no_ask (if buying YES) or 1 - yes_ask (if buying NO)
- Edge = fair_value - market_price
- Only trade when edge > min_edge threshold

Future enhancement: Claude-powered probability estimation replaces
the simple fair-value model.

Two tiers:
- ValuePricingStrategy (CORE): conservative parameters
- MarketSkimmerStrategy (SCOUT): looser params, wider net
"""

import logging
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

logger = logging.getLogger(__name__)


class ValuePricingStrategy(Strategy):
    """
    Scans prediction markets for mispriced contracts.

    Identifies edges by comparing bid/ask spreads to implied
    fair values, filtered by volume and price bounds.

    CORE tier: conservative thresholds, fewer but higher-quality signals.
    """

    def __init__(
        self,
        strategy_id: str = "value_kalshi",
        parameters: dict[str, Any] | None = None,
    ):
        default_params = {
            "symbol": "",  # Dynamic — strategy picks markets
            "min_edge": 0.05,          # Min edge (dollars) to trigger signal
            "min_volume": 100,         # Min contracts traded
            "min_open_interest": 50,   # Min open interest
            "min_price": 0.10,         # Don't trade sub-10-cent contracts
            "max_price": 0.90,         # Don't trade near-certainties
            "max_spread": 0.15,        # Skip illiquid wide-spread markets
            "position_size_usd": 50.0, # Conservative for prediction markets
            "max_signals": 3,          # Max signals per cycle (diversify)
            "scan_limit": 50,          # Markets to scan per cycle
        }
        if parameters:
            default_params.update(parameters)

        super().__init__(
            strategy_id=strategy_id,
            asset_class=AssetClass.PREDICTIONS,
            parameters=default_params,
            tier=StrategyTier.CORE,
            timeframe="realtime",
            max_signals_per_cycle=3,
        )

    async def generate_signals(
        self,
        bars: dict[str, list[dict]],
        market_regime: MarketRegime,
    ) -> list[Signal]:
        """
        Scan prediction markets and generate signals for mispriced contracts.

        Args:
            bars: For prediction strategies, contains a "markets" key with
                  list of market dicts. Each market: {ticker, title, yes_bid,
                  no_bid, yes_ask, no_ask, volume, open_interest}
            market_regime: Current market regime (less relevant for predictions).

        Returns:
            List of 0 to max_signals Signal objects, ranked by edge size.
        """
        markets = bars.get("markets", [])
        if not markets:
            logger.debug("No prediction markets available to scan")
            return []

        opportunities: list[dict[str, Any]] = []

        for market in markets:
            opp = self._evaluate_market(market)
            if opp is not None:
                opportunities.append(opp)

        if not opportunities:
            logger.debug("No prediction market opportunities found in %d markets", len(markets))
            return []

        # Sort by edge (best opportunities first)
        opportunities.sort(key=lambda x: x["edge"], reverse=True)

        # Cap at max_signals
        max_signals = self.parameters["max_signals"]
        top = opportunities[:max_signals]

        signals = []
        for opp in top:
            signal = self._build_signal(opp, market_regime)
            signals.append(signal)

            logger.info(
                "Prediction %s: %s %s edge=%.2f%% price=%.2f",
                opp["side"],
                opp["ticker"],
                opp["title"][:40],
                opp["edge"] * 100,
                opp["price"],
            )

        return signals

    def _evaluate_market(self, market: dict[str, Any]) -> dict[str, Any] | None:
        """
        Evaluate a single market for trading opportunity.

        Returns opportunity dict if edge exists, None otherwise.
        """
        ticker = market.get("ticker", "")
        title = market.get("title", "")
        if not ticker:
            return None

        # Extract prices (already in dollars from adapter)
        yes_bid = market.get("yes_bid") or market.get("yes_price", 0)
        no_bid = market.get("no_bid") or market.get("no_price", 0)
        yes_ask = market.get("yes_ask", 0)
        no_ask = market.get("no_ask", 0)
        volume = market.get("volume", 0)
        open_interest = market.get("open_interest", 0)

        # Convert to float safely
        yes_bid = float(yes_bid)
        no_bid = float(no_bid)
        yes_ask = float(yes_ask)
        no_ask = float(no_ask)
        volume = int(volume)
        open_interest = int(open_interest)

        # Skip if no valid prices
        if yes_bid <= 0 and no_bid <= 0:
            return None

        # Liquidity filters
        if volume < self.parameters["min_volume"]:
            return None
        if open_interest < self.parameters["min_open_interest"]:
            return None

        # Price bounds filter
        min_price = self.parameters["min_price"]
        max_price = self.parameters["max_price"]

        # Check YES side opportunity
        yes_opp = self._check_yes_opportunity(
            yes_bid, yes_ask, no_bid, no_ask, min_price, max_price
        )

        # Check NO side opportunity
        no_opp = self._check_no_opportunity(
            yes_bid, yes_ask, no_bid, no_ask, min_price, max_price
        )

        # Pick the better opportunity
        best = None
        if yes_opp and no_opp:
            best = yes_opp if yes_opp["edge"] > no_opp["edge"] else no_opp
        elif yes_opp:
            best = yes_opp
        elif no_opp:
            best = no_opp

        if best is None:
            return None

        best["ticker"] = ticker
        best["title"] = title
        best["volume"] = volume
        best["open_interest"] = open_interest
        return best

    def _check_yes_opportunity(
        self,
        yes_bid: float,
        yes_ask: float,
        no_bid: float,
        no_ask: float,
        min_price: float,
        max_price: float,
    ) -> dict[str, Any] | None:
        """Check if buying YES contracts has an edge."""
        if yes_ask <= 0:
            return None
        if not (min_price <= yes_ask <= max_price):
            return None

        # Spread check
        spread = yes_ask - yes_bid
        if spread > self.parameters["max_spread"]:
            return None

        # Fair value of YES = 1 - no_ask (what you'd pay to replicate via NO)
        if no_ask > 0:
            fair_value = 1.0 - no_ask
        else:
            fair_value = 1.0 - no_bid if no_bid > 0 else 0.5

        edge = fair_value - yes_ask

        if edge < self.parameters["min_edge"]:
            return None

        return {
            "side": "BUY_YES",
            "price": yes_ask,
            "fair_value": fair_value,
            "edge": edge,
            "spread": spread,
        }

    def _check_no_opportunity(
        self,
        yes_bid: float,
        yes_ask: float,
        no_bid: float,
        no_ask: float,
        min_price: float,
        max_price: float,
    ) -> dict[str, Any] | None:
        """Check if buying NO contracts (selling YES) has an edge."""
        if no_ask <= 0:
            return None
        if not (min_price <= no_ask <= max_price):
            return None

        spread = no_ask - no_bid
        if spread > self.parameters["max_spread"]:
            return None

        # Fair value of NO = 1 - yes_ask
        if yes_ask > 0:
            fair_value = 1.0 - yes_ask
        else:
            fair_value = 1.0 - yes_bid if yes_bid > 0 else 0.5

        edge = fair_value - no_ask

        if edge < self.parameters["min_edge"]:
            return None

        return {
            "side": "BUY_NO",
            "price": no_ask,
            "fair_value": fair_value,
            "edge": edge,
            "spread": spread,
        }

    def _build_signal(
        self, opp: dict[str, Any], market_regime: MarketRegime
    ) -> Signal:
        """Build a Signal from an evaluated opportunity."""
        is_yes = opp["side"] == "BUY_YES"
        price = opp["price"]
        contracts = int(self.parameters["position_size_usd"] / price) if price > 0 else 1
        contracts = max(contracts, 1)

        confidence = self._calc_confidence(opp)

        return Signal(
            strategy_id=self.strategy_id,
            asset_class=self.asset_class,
            symbol=opp["ticker"],
            side=Side.BUY if is_yes else Side.SELL,
            quantity=float(contracts),
            target_price=price,
            confidence=confidence,
            strength=self._classify_strength(confidence),
            rationale=(
                f"{'YES' if is_yes else 'NO'} @ ${price:.2f} "
                f"(fair: ${opp['fair_value']:.2f}, edge: {opp['edge']*100:.1f}%, "
                f"spread: ${opp['spread']:.2f}, "
                f"vol: {opp['volume']}, OI: {opp['open_interest']})"
            ),
            market_regime=market_regime,
            position_size_usd=self.parameters["position_size_usd"],
            tier=self.tier,
        )

    @staticmethod
    def _calc_confidence(opp: dict[str, Any]) -> float:
        """
        Calculate confidence from edge size, spread, and volume.

        Larger edge + tighter spread + higher volume = higher confidence.
        """
        # Edge contribution (0-0.5): bigger edge = better
        edge_score = min(opp["edge"] / 0.20, 0.5)

        # Spread contribution (0-0.25): tighter = better
        spread = opp.get("spread", 0.10)
        spread_score = max(0.25 - (spread / 0.20) * 0.25, 0.0)

        # Volume contribution (0-0.25): more volume = more reliable pricing
        volume = opp.get("volume", 0)
        vol_score = min(volume / 2000.0, 0.25)

        confidence = edge_score + spread_score + vol_score
        return min(max(confidence, 0.1), 1.0)

    @staticmethod
    def _classify_strength(confidence: float) -> SignalStrength:
        """Map confidence to signal strength."""
        if confidence >= 0.8:
            return SignalStrength.STRONG
        elif confidence >= 0.6:
            return SignalStrength.MODERATE
        return SignalStrength.WEAK

    async def get_performance(self, period_days: int) -> StrategyPerformance:
        """Calculate performance metrics. TODO: implement with DB."""
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


class MarketSkimmerStrategy(ValuePricingStrategy):
    """
    SCOUT tier market skimmer — wider net, looser parameters.

    Scans more markets with lower thresholds to find opportunities
    the CORE strategy would miss. Smaller position sizes compensate
    for the lower-conviction signals.
    """

    def __init__(
        self,
        strategy_id: str = "skimmer_kalshi",
        parameters: dict[str, Any] | None = None,
    ):
        scout_params = {
            "symbol": "",
            "min_edge": 0.03,          # Looser: was 0.05
            "min_volume": 50,          # Looser: was 100
            "min_open_interest": 25,   # Proportional to volume change
            "min_price": 0.10,
            "max_price": 0.90,
            "max_spread": 0.25,        # Looser: was 0.15
            "position_size_usd": 50.0, # Same small size
            "max_signals": 3,
            "scan_limit": 100,         # Wider: was 50
        }
        if parameters:
            scout_params.update(parameters)

        # Skip ValuePricingStrategy.__init__, call Strategy directly
        Strategy.__init__(
            self,
            strategy_id=strategy_id,
            asset_class=AssetClass.PREDICTIONS,
            parameters=scout_params,
            tier=StrategyTier.SCOUT,
            timeframe="realtime",
            max_signals_per_cycle=3,
        )
