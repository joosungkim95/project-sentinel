"""
Trend Following Strategy — EMA crossover + ADX for crypto.

CORE tier: multi-symbol trend detection on 4-hour bars.

Captures sustained trends in crypto markets by combining:
1. EMA Crossover — fast/slow exponential MA for direction
2. ADX (Average Directional Index) — confirms trend strength
3. ATR (Average True Range) — sets dynamic stop-loss distance

Signal logic:
- BUY: Fast EMA > Slow EMA AND ADX > 20 AND price > Fast EMA
- SELL: Fast EMA < Slow EMA OR ADX < 15 (trend fading)

Uses EMAs instead of SMAs because crypto is more volatile and
EMAs react faster to price changes.

Default parameters:
- symbols: BTC-USD, ETH-USD, SOL-USD
- fast_ema: 12 (bars)
- slow_ema: 26 (bars)
- adx_period: 14
- adx_trend_threshold: 20
- adx_fade_threshold: 15
"""

import logging
from typing import Any

import numpy as np

from config.symbols import CRYPTO_TREND_SYMBOLS
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


class TrendFollowingStrategy(Strategy):
    """
    Trend following strategy using EMA crossover and ADX.

    Enters when a confirmed trend is detected, exits when
    trend strength fades. Designed for crypto's 24/7 markets
    where trends can persist for extended periods.
    """

    def __init__(
        self,
        strategy_id: str = "trend_crypto",
        parameters: dict[str, Any] | None = None,
    ):
        default_params = {
            "fast_ema_period": 12,
            "slow_ema_period": 26,
            "adx_period": 14,
            "adx_trend_threshold": 20.0,  # ADX above = trending (was 25)
            "adx_fade_threshold": 15.0,   # ADX below = trend fading (was 20)
            "atr_period": 14,
            "atr_stop_multiplier": 2.0,   # Stop-loss = ATR * multiplier
            "position_size_usd": 200.0,   # Smaller for crypto volatility
        }
        if parameters:
            default_params.update(parameters)

        super().__init__(
            strategy_id=strategy_id,
            asset_class=AssetClass.CRYPTO,
            parameters=default_params,
            tier=StrategyTier.CORE,
            symbols=CRYPTO_TREND_SYMBOLS,
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

        for symbol, symbol_bars in bars.items():
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
                "Not enough bars for trend following %s: %d < %d",
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
        atr = self._calc_atr(highs, lows, closes, self.parameters["atr_period"])

        if fast_ema is None or slow_ema is None or adx is None or atr is None:
            return []

        current_fast = fast_ema[-1]
        current_slow = slow_ema[-1]
        current_adx = adx[-1]
        current_atr = atr[-1]

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
            "Trend indicators for %s: FastEMA=%.2f SlowEMA=%.2f ADX=%.1f ATR=%.2f",
            symbol, current_fast, current_slow, current_adx, current_atr,
        )

        adx_trend = self.parameters["adx_trend_threshold"]
        adx_fade = self.parameters["adx_fade_threshold"]

        # --- BUY signal (OR-relaxed: EMA primary, ADX/price boost) ---
        ema_bullish = current_fast > current_slow
        ema_just_crossed = prev_fast <= prev_slow and current_fast > current_slow
        adx_trending = current_adx > adx_trend
        price_above_ema = current_price > current_fast

        if ema_bullish and (adx_trending or price_above_ema):
            confidence = self._calc_buy_confidence(
                current_fast, current_slow, current_adx,
                current_price, ema_just_crossed,
                adx_trending, price_above_ema,
            )
            quantity = self.parameters["position_size_usd"] / current_price
            stop_loss = current_price - (
                current_atr * self.parameters["atr_stop_multiplier"]
            )

            triggers = [f"EMA bullish ({current_fast:.2f}>{current_slow:.2f})"]
            if adx_trending:
                triggers.append(f"ADX={current_adx:.1f}")
            if price_above_ema:
                triggers.append("price>EMA")

            logger.info(
                "Trend BUY: %s %s conf=%.2f",
                symbol, " + ".join(triggers), confidence,
            )

            return [
                Signal(
                    strategy_id=self.strategy_id,
                    asset_class=self.asset_class,
                    symbol=symbol,
                    side=Side.BUY,
                    quantity=round(quantity, 8),
                    target_price=current_price,
                    stop_loss=round(stop_loss, 2),
                    confidence=confidence,
                    strength=self._classify_strength(confidence),
                    rationale=(
                        f"Trend BUY: {' + '.join(triggers)}, "
                        f"ATR stop @ ${stop_loss:,.2f}"
                    ),
                    market_regime=market_regime,
                    position_size_usd=self.parameters["position_size_usd"],
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
                "Trend SELL: %s %s", symbol, "; ".join(sell_reasons),
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
                    rationale=f"Trend SELL: {'; '.join(sell_reasons)}",
                    market_regime=market_regime,
                    position_size_usd=self.parameters["position_size_usd"],
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
    def _calc_atr(
        highs: np.ndarray,
        lows: np.ndarray,
        closes: np.ndarray,
        period: int,
    ) -> np.ndarray | None:
        """
        Calculate Average True Range for stop-loss sizing.

        ATR = Wilder-smoothed True Range over N periods.
        """
        n = len(closes)
        if n < period + 1:
            return None

        tr = np.zeros(n - 1)
        for i in range(1, n):
            tr[i - 1] = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )

        # Wilder's smoothing for ATR
        atr = np.zeros(len(tr) - period + 1)
        atr[0] = np.mean(tr[:period])
        for i in range(1, len(atr)):
            atr[i] = ((atr[i - 1] * (period - 1)) + tr[period - 1 + i]) / period

        return atr

    @staticmethod
    def _calc_buy_confidence(
        fast_ema: float,
        slow_ema: float,
        adx: float,
        price: float,
        just_crossed: bool,
        adx_trending: bool,
        price_above_ema: bool,
    ) -> float:
        """
        OR-relaxed confidence: EMA bullish is primary, ADX and price
        are confluence boosts.
        """
        # EMA spread (0.15-0.30): wider = stronger trend
        if slow_ema > 0:
            spread_pct = abs(fast_ema - slow_ema) / slow_ema * 100
        else:
            spread_pct = 0.0
        ema_score = min(0.15 + spread_pct / 6.0, 0.30)

        # Fresh crossover bonus (0 or 0.10)
        cross_bonus = 0.10 if just_crossed else 0.0

        # ADX confluence (0 or 0.10-0.25)
        adx_score = 0.0
        if adx_trending:
            adx_score = min(0.10 + (adx - 20.0) / 50.0, 0.25)

        # Price above EMA confluence (0 or 0.05-0.15)
        price_score = 0.0
        if price_above_ema and fast_ema > 0:
            above_pct = (price - fast_ema) / fast_ema * 100
            price_score = min(0.05 + above_pct / 4.0, 0.15)

        confidence = ema_score + cross_bonus + adx_score + price_score
        return min(max(confidence, 0.15), 1.0)

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
