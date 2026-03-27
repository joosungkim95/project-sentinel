"""
Buy-the-Pullback Strategy — Trend continuation on equity pullbacks.

CORE tier: moderate frequency signals on 4-hour bars.

In a confirmed uptrend (price above 50 EMA), buys when price pulls
back to the 20 EMA or 38.2% Fibonacci retracement level. Volume
should be declining during pullback (healthy correction, not distribution).

Signal logic (OR-based):
- BUY: Price in uptrend AND (touches 20 EMA OR hits 38.2% Fib level)
  Declining volume during pullback = confluence boost.
- SELL: Trend structure breaks (price falls below 50 EMA).

Default parameters:
- symbols: all 7 equity symbols
- fast_ema: 20, slow_ema: 50
- fib_level: 0.382
- pullback_proximity_pct: 0.5 (within 0.5% of EMA or Fib)
"""

import logging
from typing import Any

import numpy as np

from config.symbols import EQUITY_SYMBOLS
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


class PullbackStrategy(Strategy):
    """
    Buy-the-pullback strategy for equities.

    Enters during temporary dips in confirmed uptrends, targeting
    a return to the recent high. Only active when price is above
    the slow EMA (confirming uptrend).
    """

    def __init__(
        self,
        strategy_id: str = "pullback_equity",
        parameters: dict[str, Any] | None = None,
    ):
        default_params = {
            "fast_ema_period": 20,
            "slow_ema_period": 50,
            "fib_level": 0.382,
            "pullback_proximity_pct": 0.5,  # Within 0.5% of support level
            "swing_lookback": 20,            # Bars to find swing high/low
            "position_size_usd": 300.0,
            "stop_loss_pct": 2.5,
        }
        if parameters:
            default_params.update(parameters)

        super().__init__(
            strategy_id=strategy_id,
            asset_class=AssetClass.EQUITIES,
            parameters=default_params,
            tier=StrategyTier.CORE,
            symbols=EQUITY_SYMBOLS,
            timeframe="4Hour",
            max_signals_per_cycle=2,
        )

    async def generate_signals(
        self,
        bars: dict[str, list[dict]],
        market_regime: MarketRegime,
    ) -> list[Signal]:
        """Generate pullback buy signals in confirmed uptrends."""
        # Skip in downtrend regime
        if market_regime == MarketRegime.TRENDING_DOWN:
            return []

        all_signals: list[Signal] = []

        for symbol in self.symbols:
            symbol_bars = bars.get(symbol, [])
            min_bars = self.parameters["slow_ema_period"] + 5
            if len(symbol_bars) < min_bars:
                continue

            signals = self._analyze_symbol(symbol, symbol_bars, market_regime)
            all_signals.extend(signals)
            if len(all_signals) >= self.max_signals_per_cycle:
                break

        return all_signals[: self.max_signals_per_cycle]

    def _analyze_symbol(
        self,
        symbol: str,
        symbol_bars: list[dict],
        market_regime: MarketRegime,
    ) -> list[Signal]:
        """Analyze a single symbol for pullback opportunity."""
        closes = np.array([b["close"] for b in symbol_bars])
        volumes = np.array([b["volume"] for b in symbol_bars])
        current_price = closes[-1]

        # Calculate EMAs
        fast_ema = self._calc_ema(closes, self.parameters["fast_ema_period"])
        slow_ema = self._calc_ema(closes, self.parameters["slow_ema_period"])

        if fast_ema is None or slow_ema is None:
            return []

        current_fast = fast_ema[-1]
        current_slow = slow_ema[-1]

        # Uptrend confirmation: price above slow EMA
        in_uptrend = current_price > current_slow and current_fast > current_slow

        if not in_uptrend:
            # SELL signal if we were in uptrend and it broke
            if current_price < current_slow:
                return [
                    Signal(
                        strategy_id=self.strategy_id,
                        asset_class=self.asset_class,
                        symbol=symbol,
                        side=Side.SELL,
                        quantity=0,
                        target_price=current_price,
                        confidence=0.5,
                        strength=SignalStrength.MODERATE,
                        rationale=(
                            f"Pullback SELL {symbol}: trend broken, "
                            f"price ${current_price:.2f} < "
                            f"50 EMA ${current_slow:.2f}"
                        ),
                        market_regime=market_regime,
                        position_size_usd=self.parameters["position_size_usd"],
                        tier=self.tier,
                    )
                ]
            return []

        # Check pullback conditions (OR-based)
        proximity = self.parameters["pullback_proximity_pct"] / 100.0
        lookback = self.parameters["swing_lookback"]

        # Condition 1: Price near fast EMA (pullback to moving average)
        ema_distance = abs(current_price - current_fast) / current_fast
        near_fast_ema = ema_distance <= proximity

        # Condition 2: Price near Fibonacci level
        swing_high = np.max(closes[-lookback:])
        swing_low = np.min(closes[-lookback:])
        swing_range = swing_high - swing_low

        fib_level = self.parameters["fib_level"]
        fib_price = swing_high - (swing_range * fib_level)
        fib_distance = abs(current_price - fib_price) / fib_price if fib_price > 0 else 1.0
        near_fib = fib_distance <= proximity

        # Check declining volume during pullback (confluence)
        vol_declining = False
        if len(volumes) >= 5:
            recent_vol = np.mean(volumes[-3:])
            prior_vol = np.mean(volumes[-8:-3]) if len(volumes) >= 8 else np.mean(volumes[:-3])
            vol_declining = recent_vol < prior_vol * 0.85  # 15% volume decline

        logger.debug(
            "Pullback %s: near_ema=%s near_fib=%s vol_decline=%s "
            "price=%.2f fast_ema=%.2f fib=%.2f",
            symbol, near_fast_ema, near_fib, vol_declining,
            current_price, current_fast, fib_price,
        )

        if not (near_fast_ema or near_fib):
            return []

        # Calculate confidence
        confidence = self._calc_buy_confidence(
            near_fast_ema, near_fib, vol_declining,
            ema_distance, fib_distance, current_price, current_slow,
        )

        position_size = self.parameters["position_size_usd"]
        quantity = position_size / current_price
        stop_loss = current_price * (1 - self.parameters["stop_loss_pct"] / 100)
        take_profit = swing_high  # Target: prior swing high

        triggers = []
        if near_fast_ema:
            triggers.append(f"near 20 EMA ${current_fast:.2f}")
        if near_fib:
            triggers.append(f"near {fib_level:.1%} Fib ${fib_price:.2f}")
        if vol_declining:
            triggers.append("declining volume")

        logger.info(
            "Pullback BUY: %s %s conf=%.2f",
            symbol, " + ".join(triggers), confidence,
        )

        return [
            Signal(
                strategy_id=self.strategy_id,
                asset_class=self.asset_class,
                symbol=symbol,
                side=Side.BUY,
                quantity=round(quantity, 2),
                target_price=current_price,
                take_profit=round(take_profit, 2),
                stop_loss=round(stop_loss, 2),
                confidence=confidence,
                strength=self._classify_strength(confidence),
                rationale=(
                    f"Pullback BUY {symbol}: uptrend intact "
                    f"(price > 50 EMA ${current_slow:.2f}). "
                    f"Pullback to {' + '.join(triggers)}. "
                    f"Target: swing high ${swing_high:.2f}"
                ),
                market_regime=market_regime,
                position_size_usd=position_size,
                tier=self.tier,
            )
        ]

    @staticmethod
    def _calc_buy_confidence(
        near_ema: bool,
        near_fib: bool,
        vol_declining: bool,
        ema_distance: float,
        fib_distance: float,
        price: float,
        slow_ema: float,
    ) -> float:
        """
        OR-based confidence with confluence boosting.

        Single trigger: 0.25-0.40. Multiple: 0.40-0.75.
        """
        ema_score = 0.0
        fib_score = 0.0

        if near_ema:
            # Closer to EMA = higher score (0.20-0.35)
            ema_score = max(0.35 - ema_distance * 50, 0.20)

        if near_fib:
            # Closer to Fib = higher score (0.20-0.35)
            fib_score = max(0.35 - fib_distance * 50, 0.20)

        # Volume declining = healthy pullback (0.10 bonus)
        vol_bonus = 0.10 if vol_declining else 0.0

        # Trend strength: how far above slow EMA (0-0.10)
        if slow_ema > 0:
            trend_pct = (price - slow_ema) / slow_ema * 100
            trend_score = min(trend_pct / 10.0, 0.10)
        else:
            trend_score = 0.0

        # Confluence bonus: both EMA and Fib triggering
        confluence = 0.10 if (near_ema and near_fib) else 0.0

        confidence = ema_score + fib_score + vol_bonus + trend_score + confluence
        return min(max(confidence, 0.20), 1.0)

    @staticmethod
    def _calc_ema(prices: np.ndarray, period: int) -> np.ndarray | None:
        """Calculate Exponential Moving Average."""
        if len(prices) < period:
            return None

        alpha = 2.0 / (period + 1)
        ema = np.zeros(len(prices) - period + 1)
        ema[0] = np.mean(prices[:period])

        for i in range(1, len(ema)):
            ema[i] = alpha * prices[period - 1 + i] + (1 - alpha) * ema[i - 1]

        return ema

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
