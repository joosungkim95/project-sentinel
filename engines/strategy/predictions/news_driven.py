"""
News-Driven Strategy — Detects rapid repricing in prediction markets.

SNIPER tier: high-conviction, event-driven signals.

Instead of directly consuming news, this strategy detects the *effects*
of news by monitoring prediction market price and volume movements.
When a market suddenly moves with high volume, someone knows something
we don't — but we can ride the momentum of the repricing.

Signal logic:
- BUY: Price moved significantly from recent average + volume spike
       (market is repricing — join the move)
- SELL: Price has mean-reverted or momentum exhausted

Key insight: Prediction markets reprice on news faster than they settle.
The initial repricing often overshoots, but the direction is usually right.
We buy the direction and take profit at a conservative target.

Default parameters:
- price_move_threshold: 0.06 (6 cent move from 5-period avg)
- volume_spike_mult: 2.5 (2.5x average volume)
- lookback: 5 (bars for recent average)
- take_profit_pct: 0.5 (take 50% of the remaining distance to 0 or 1)
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


class NewsDrivenStrategy(Strategy):
    """
    Detects news-driven repricing in prediction markets.

    Monitors for volume/price spikes that indicate rapid repricing,
    then rides the momentum of the move.

    SNIPER tier: fewer, higher-conviction event-driven signals.
    """

    def __init__(
        self,
        strategy_id: str = "news_driven_kalshi",
        parameters: dict[str, Any] | None = None,
    ):
        default_params = {
            "symbol": "",  # Dynamic — picks from market scan
            "price_move_threshold": 0.06,  # Min price move to trigger (was 0.08)
            "volume_spike_mult": 2.5,      # Volume must be 2.5x average (was 3.0)
            "lookback": 5,                 # Bars for recent average
            "take_profit_pct": 0.5,        # Take profit at 50% of remaining
            "min_volume": 100,             # Min absolute volume (was 200)
            "min_price": 0.15,             # Don't trade extremes
            "max_price": 0.85,
            "max_spread": 0.12,            # Slightly relaxed (was 0.10)
            "position_size_usd": 500.0,    # Larger for SNIPER (was 40)
            "max_signals": 2,              # Fewer, higher-conviction trades
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
            max_signals_per_cycle=2,
        )

    async def generate_signals(
        self,
        bars: dict[str, list[dict]],
        market_regime: MarketRegime,
    ) -> list[Signal]:
        """
        Scan prediction markets for news-driven repricing events.

        Args:
            bars: For prediction strategies, contains a "markets" key with
                  list of market dicts showing price/volume activity.
            market_regime: Current market regime classification.

        Returns:
            List of Signal objects for detected repricing events.
        """
        markets = bars.get("markets", [])
        if not markets:
            return []

        scored: list[tuple[float, dict, str]] = []

        for market in markets:
            result = self._evaluate_market(market)
            if result:
                score, direction = result
                scored.append((score, market, direction))

        # Sort by score, take top N
        scored.sort(key=lambda x: x[0], reverse=True)
        max_signals = self.parameters["max_signals"]

        signals: list[Signal] = []
        for score, market, direction in scored[:max_signals]:
            signal = self._create_signal(market, direction, score, market_regime)
            if signal:
                signals.append(signal)

        return signals

    def _evaluate_market(
        self, market: dict
    ) -> tuple[float, str] | None:
        """
        Evaluate a single market for news-driven repricing.

        Returns:
            Tuple of (score, direction) or None if no signal.
            direction is "yes" or "no".
        """
        # Extract market data
        yes_price = market.get("yes_price") or market.get("yes_bid", 0)
        no_price = market.get("no_price") or market.get("no_bid", 0)
        volume = market.get("volume", 0)
        prev_price = market.get("prev_yes_close") or market.get("prev_price", 0)
        avg_volume = market.get("avg_daily_volume") or market.get("volume_avg", 0)
        spread = market.get("spread", 1.0)
        ticker = market.get("ticker", "")

        if not yes_price or not ticker:
            return None

        # Price bounds filter
        if yes_price < self.parameters["min_price"] or yes_price > self.parameters["max_price"]:
            return None

        # Spread filter
        if spread > self.parameters["max_spread"]:
            return None

        # Volume filter
        if volume < self.parameters["min_volume"]:
            return None

        # Detect price move
        if prev_price > 0:
            price_move = yes_price - prev_price
        else:
            return None

        move_magnitude = abs(price_move)
        if move_magnitude < self.parameters["price_move_threshold"]:
            return None

        # Detect volume spike
        if avg_volume > 0:
            vol_ratio = volume / avg_volume
        else:
            vol_ratio = 1.0

        if vol_ratio < self.parameters["volume_spike_mult"]:
            return None

        # Score: combination of move magnitude and volume spike
        score = move_magnitude * vol_ratio

        # Direction: buy in the direction of the move
        direction = "yes" if price_move > 0 else "no"

        logger.info(
            "News signal: %s move=%.2f vol_ratio=%.1fx direction=%s",
            ticker, price_move, vol_ratio, direction,
        )

        return score, direction

    def _create_signal(
        self,
        market: dict,
        direction: str,
        score: float,
        market_regime: MarketRegime,
    ) -> Signal | None:
        """Create a Signal from an evaluated market."""
        ticker = market.get("ticker", "")
        yes_price = market.get("yes_price") or market.get("yes_bid", 0)
        no_price = market.get("no_price") or market.get("no_bid", 0)
        volume = market.get("volume", 0)
        prev_price = market.get("prev_yes_close") or market.get("prev_price", 0)

        if direction == "yes":
            side = Side.BUY
            price = yes_price
            # Take profit: 50% of distance to 1.0
            remaining = 1.0 - yes_price
            take_profit = yes_price + remaining * self.parameters["take_profit_pct"]
        else:
            side = Side.SELL
            price = no_price if no_price else (1.0 - yes_price)
            remaining = 1.0 - price
            take_profit = price + remaining * self.parameters["take_profit_pct"]

        if price <= 0:
            return None

        quantity = self.parameters["position_size_usd"] / max(price, 0.01)
        confidence = self._calc_confidence(score, volume, abs(yes_price - prev_price))
        price_move = yes_price - prev_price

        return Signal(
            strategy_id=self.strategy_id,
            asset_class=self.asset_class,
            symbol=ticker,
            side=side,
            quantity=round(quantity, 0),
            target_price=round(price, 4),
            take_profit=round(take_profit, 4),
            confidence=confidence,
            strength=self._classify_strength(confidence),
            rationale=(
                f"News-driven {'BUY' if side == Side.BUY else 'SELL'}: "
                f"{ticker} moved {price_move:+.2f} from {prev_price:.2f} "
                f"with {volume:,} vol. Riding repricing momentum."
            ),
            market_regime=market_regime,
            position_size_usd=self.parameters["position_size_usd"],
            tier=self.tier,
        )

    @staticmethod
    def _calc_confidence(
        score: float, volume: int, move_magnitude: float
    ) -> float:
        """Higher confidence for bigger moves with more volume."""
        # Score contribution (0-0.4)
        score_component = min(score / 2, 0.4)
        # Volume contribution (0-0.3)
        vol_component = min(volume / 10000, 0.3)
        # Move magnitude (0-0.3)
        move_component = min(move_magnitude / 0.3, 0.3)

        confidence = score_component + vol_component + move_component
        return min(max(confidence, 0.15), 1.0)

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

    @staticmethod
    def _classify_strength(confidence: float) -> SignalStrength:
        """Map confidence to signal strength."""
        if confidence >= 0.8:
            return SignalStrength.STRONG
        elif confidence >= 0.6:
            return SignalStrength.MODERATE
        return SignalStrength.WEAK
