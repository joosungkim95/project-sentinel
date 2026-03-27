"""
Equity Trend Following Strategy — EMA crossover + ADX for equities.

CORE tier: multi-symbol trend detection on 4-hour bars.

Captures sustained trends in equity markets by combining:
1. EMA Crossover — fast/slow exponential MA for direction
2. ADX (Average Directional Index) — confirms trend strength

Signal logic:
- BUY: Fast EMA > Slow EMA AND ADX > 20 AND price > Fast EMA
- SELL: Fast EMA crosses below Slow EMA OR ADX < 15

Default parameters:
- symbols: SPY, QQQ, NVDA, IWM
- fast_ema: 12 (bars)
- slow_ema: 26 (bars)
- adx_period: 14
- adx_trend_threshold: 20
- adx_fade_threshold: 15
- stop_loss_pct: 3.0%
"""

import logging
from typing import Any

import numpy as np

from config.symbols import EQUITY_TREND_SYMBOLS
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


class EquityTrendFollowingStrategy(Strategy):
    """
    Trend following strategy using EMA crossover and ADX for equities.

    Enters when a confirmed trend is detected, exits when
    trend strength fades. Designed for equity markets where
    trends tend to persist on 4-hour timeframes.
    """

    def __init__(
        self,
        strategy_id: str = "trend_equities",
        parameters: dict[str, Any] | None = None,
    ):
        default_params = {
            "fast_ema_period": 12,
            "slow_ema_period": 26,
            "adx_period": 14,
            "adx_trend_threshold": 20.0,
            "adx_fade_threshold": 15.0,
            "position_size_usd": 300.0,
            "stop_loss_pct": 3.0,
        }
        if parameters:
            default_params.update(parameters)

        super().__init__(
            strategy_id=strategy_id,
            asset_class=AssetClass.EQUITIES,
            parameters=default_params,
            tier=StrategyTier.CORE,
            symbols=EQUITY_TREND_SYMBOLS,
            timeframe="4Hour",
            max_signals_per_cycle=2,
        )

    async def generate_signals(
        self,
        bars: dict[str, list[dict]],
        market_regime: MarketRegime,
    ) -> list[Signal]:
        """
        Generate signals from trend indicators across all symbols.

        Args:
            bars: Bar data keyed by symbol, each value a list of OHLCV dicts.
            market_regime: Current market regime classification.

        Returns:
            List of Signal objects (may be empty if no opportunities).
        """
        all_signals: list[Signal] = []

        for symbol in self.symbols:
            symbol_bars = bars.get(symbol, [])
            if not symbol_bars:
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
        """Analyze a single symbol for trend signals."""
        min_bars = (
            self.parameters["slow_ema_period"]
            + self.parameters["adx_period"]
            + 2
        )

        if len(symbol_bars) < min_bars:
            logger.debug(
                "Not enough bars for equity trend %s: %d < %d",
                symbol, len(symbol_bars), min_bars,
            )
            return []

        closes = np.array([b["close"] for b in symbol_bars])
        highs = np.array([b["high"] for b in symbol_bars])
        lows = np.array([b["low"] for b in symbol_bars])
        current_price = closes[-1]

        # Calculate indicators
        fast_ema = self._calc_ema(closes, self.parameters["fast_ema_period"])
        slow_ema = self._calc_ema(closes, self.parameters["slow_ema_period"])
        adx = self._calc_adx(highs, lows, closes, self.parameters["adx_period"])

        if fast_ema is None or slow_ema is None or adx is None:
            return []

        current_fast = fast_ema[-1]
        current_slow = slow_ema[-1]
        current_adx = adx[-1]

        # Align EMAs — slow EMA is shorter, use last N values of fast
        ema_diff = len(fast_ema) - len(slow_ema)
        if ema_diff > 0:
            fast_aligned = fast_ema[ema_diff:]
        else:
            fast_aligned = fast_ema
            slow_ema = slow_ema[-len(fast_ema):]

        prev_fast = fast_aligned[-2] if len(fast_aligned) >= 2 else current_fast
        prev_slow = slow_ema[-2] if len(slow_ema) >= 2 else current_slow

        logger.debug(
            "Equity trend indicators for %s: FastEMA=%.2f SlowEMA=%.2f ADX=%.1f",
            symbol, current_fast, current_slow, current_adx,
        )

        adx_trend = self.parameters["adx_trend_threshold"]
        adx_fade = self.parameters["adx_fade_threshold"]
        position_size = self.parameters["position_size_usd"]
        stop_loss_pct = self.parameters["stop_loss_pct"]

        # --- BUY signal ---
        ema_bullish = current_fast > current_slow
        ema_just_crossed = prev_fast <= prev_slow and current_fast > current_slow
        adx_trending = current_adx > adx_trend
        price_above_ema = current_price > current_fast

        if ema_bullish and adx_trending and price_above_ema:
            confidence = self._calc_buy_confidence(
                current_fast, current_slow, current_adx,
                current_price, ema_just_crossed,
            )
            quantity = position_size / current_price
            stop_loss = current_price * (1.0 - stop_loss_pct / 100.0)

            logger.info(
                "Equity Trend BUY: %s FastEMA=%.2f > SlowEMA=%.2f, ADX=%.1f",
                symbol, current_fast, current_slow, current_adx,
            )

            return [
                Signal(
                    strategy_id=self.strategy_id,
                    asset_class=self.asset_class,
                    symbol=symbol,
                    side=Side.BUY,
                    quantity=round(quantity, 2),
                    target_price=current_price,
                    stop_loss=round(stop_loss, 2),
                    confidence=confidence,
                    strength=self._classify_strength(confidence),
                    rationale=(
                        f"Equity Trend BUY: EMA({self.parameters['fast_ema_period']})="
                        f"{current_fast:.2f} > EMA({self.parameters['slow_ema_period']})="
                        f"{current_slow:.2f}, "
                        f"ADX({self.parameters['adx_period']})={current_adx:.1f} "
                        f"(>{adx_trend}), price ${current_price:.2f} > fast EMA"
                    ),
                    market_regime=market_regime,
                    position_size_usd=position_size,
                    tier=self.tier,
                )
            ]

        # --- SELL signal ---
        ema_bearish_cross = prev_fast >= prev_slow and current_fast < current_slow
        adx_fading = current_adx < adx_fade

        if ema_bearish_cross or (not ema_bullish and adx_fading):
            sell_reasons = []
            if ema_bearish_cross:
                sell_reasons.append(
                    f"EMA bearish cross: {current_fast:.2f} < {current_slow:.2f}"
                )
            if adx_fading:
                sell_reasons.append(f"ADX fading: {current_adx:.1f} < {adx_fade}")

            confidence = self._calc_sell_confidence(
                current_fast, current_slow, current_adx, ema_bearish_cross,
            )

            logger.info(
                "Equity Trend SELL: %s %s", symbol, "; ".join(sell_reasons),
            )

            return [
                Signal(
                    strategy_id=self.strategy_id,
                    asset_class=self.asset_class,
                    symbol=symbol,
                    side=Side.SELL,
                    quantity=0,  # Sell entire position
                    target_price=current_price,
                    confidence=confidence,
                    strength=self._classify_strength(confidence),
                    rationale=f"Equity Trend SELL: {'; '.join(sell_reasons)}",
                    market_regime=market_regime,
                    position_size_usd=position_size,
                    tier=self.tier,
                )
            ]

        return []

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
    def _calc_ema(prices: np.ndarray, period: int) -> np.ndarray | None:
        """
        Calculate Exponential Moving Average.

        Uses standard EMA formula: EMA_t = alpha * price_t + (1 - alpha) * EMA_{t-1}
        Seeded with SMA of first `period` values.
        """
        if len(prices) < period:
            return None

        alpha = 2.0 / (period + 1)
        ema = np.zeros(len(prices) - period + 1)
        ema[0] = np.mean(prices[:period])

        for i in range(1, len(ema)):
            ema[i] = alpha * prices[period - 1 + i] + (1 - alpha) * ema[i - 1]

        return ema

    @staticmethod
    def _calc_adx(
        highs: np.ndarray,
        lows: np.ndarray,
        closes: np.ndarray,
        period: int,
    ) -> np.ndarray | None:
        """
        Calculate Average Directional Index.

        ADX measures trend strength (not direction):
        - > 20: trending
        - < 15: ranging/weak
        - > 40: strong trend

        Uses Wilder's smoothing method.
        """
        n = len(closes)
        if n < period * 2 + 1:
            return None

        # True Range
        tr = np.zeros(n - 1)
        for i in range(1, n):
            tr[i - 1] = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )

        # Directional Movement
        plus_dm = np.zeros(n - 1)
        minus_dm = np.zeros(n - 1)
        for i in range(1, n):
            up_move = highs[i] - highs[i - 1]
            down_move = lows[i - 1] - lows[i]
            if up_move > down_move and up_move > 0:
                plus_dm[i - 1] = up_move
            if down_move > up_move and down_move > 0:
                minus_dm[i - 1] = down_move

        # Wilder's smoothing
        def wilder_smooth(data: np.ndarray, period: int) -> np.ndarray:
            smoothed = np.zeros(len(data) - period + 1)
            smoothed[0] = np.sum(data[:period])
            for i in range(1, len(smoothed)):
                smoothed[i] = (
                    smoothed[i - 1]
                    - (smoothed[i - 1] / period)
                    + data[period - 1 + i]
                )
            return smoothed

        atr_smooth = wilder_smooth(tr, period)
        plus_dm_smooth = wilder_smooth(plus_dm, period)
        minus_dm_smooth = wilder_smooth(minus_dm, period)

        # Directional Indicators
        plus_di = np.divide(
            plus_dm_smooth * 100, atr_smooth,
            out=np.zeros_like(atr_smooth), where=atr_smooth != 0,
        )
        minus_di = np.divide(
            minus_dm_smooth * 100, atr_smooth,
            out=np.zeros_like(atr_smooth), where=atr_smooth != 0,
        )

        # DX
        di_sum = plus_di + minus_di
        dx = np.divide(
            np.abs(plus_di - minus_di) * 100, di_sum,
            out=np.zeros_like(di_sum), where=di_sum != 0,
        )

        # ADX = Wilder smoothed DX
        if len(dx) < period:
            return None

        adx = np.zeros(len(dx) - period + 1)
        adx[0] = np.mean(dx[:period])
        for i in range(1, len(adx)):
            adx[i] = ((adx[i - 1] * (period - 1)) + dx[period - 1 + i]) / period

        return adx

    @staticmethod
    def _calc_buy_confidence(
        fast_ema: float,
        slow_ema: float,
        adx: float,
        price: float,
        just_crossed: bool,
    ) -> float:
        """Calculate buy confidence from trend indicators."""
        # EMA spread (0-0.3): wider spread = stronger trend
        if slow_ema > 0:
            spread_pct = abs(fast_ema - slow_ema) / slow_ema * 100
        else:
            spread_pct = 0.0
        ema_score = min(spread_pct / 5.0, 0.3)

        # ADX contribution (0-0.4): stronger trend = higher
        adx_score = min((adx - 15.0) / 40.0, 0.4)
        adx_score = max(adx_score, 0.0)

        # Fresh crossover bonus (0 or 0.15)
        cross_bonus = 0.15 if just_crossed else 0.0

        # Price above EMA bonus (0-0.15)
        if fast_ema > 0:
            above_pct = (price - fast_ema) / fast_ema * 100
        else:
            above_pct = 0.0
        price_score = min(above_pct / 3.0, 0.15)
        price_score = max(price_score, 0.0)

        confidence = ema_score + adx_score + cross_bonus + price_score
        return min(max(confidence, 0.1), 1.0)

    @staticmethod
    def _calc_sell_confidence(
        fast_ema: float,
        slow_ema: float,
        adx: float,
        bearish_cross: bool,
    ) -> float:
        """Calculate sell confidence from bearish indicators."""
        cross_score = 0.4 if bearish_cross else 0.1
        adx_score = max((20.0 - adx) / 20.0, 0.0) * 0.4
        if slow_ema > 0:
            spread = abs(fast_ema - slow_ema) / slow_ema * 100
        else:
            spread = 0.0
        spread_score = min(spread / 5.0, 0.2)
        confidence = cross_score + adx_score + spread_score
        return min(max(confidence, 0.2), 1.0)

    @staticmethod
    def _classify_strength(confidence: float) -> SignalStrength:
        """Map confidence to signal strength."""
        if confidence >= 0.8:
            return SignalStrength.STRONG
        elif confidence >= 0.6:
            return SignalStrength.MODERATE
        return SignalStrength.WEAK
