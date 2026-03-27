"""
VWAP Strategy — Volume Weighted Average Price for equities.

CORE tier: intraday mean-reversion around VWAP anchor on 15-min bars.

VWAP acts as an institutional fair-value anchor. Price deviating far
from VWAP tends to revert. This strategy uses VWAP deviation bands
(standard deviations) for entries and VWAP itself as the target.

Signal logic (OR-based):
- BUY: Price below VWAP -1.5σ band OR price touched VWAP from below
  after extended deviation (first kiss). Confluence boosts confidence.
- SELL: Price above VWAP +1.5σ band OR VWAP crossunder after long hold.

Default parameters:
- symbols: all 7 equity symbols
- vwap_std_entry: 1.5 (standard deviations for band entry)
- vwap_std_exit: 0.5 (exit when price returns near VWAP)
- position_size_usd: 250.0
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


class VWAPStrategy(Strategy):
    """
    VWAP mean-reversion strategy for equities.

    Uses VWAP as a fair-value anchor with standard deviation bands.
    Buys when price deviates below the lower band, targets VWAP return.
    """

    def __init__(
        self,
        strategy_id: str = "vwap_equity",
        parameters: dict[str, Any] | None = None,
    ):
        default_params = {
            "vwap_std_entry": 1.5,       # Bands for entry trigger
            "vwap_std_exit": 0.5,        # Close to VWAP = exit zone
            "min_deviation_pct": 0.3,    # Min % deviation from VWAP to trigger
            "position_size_usd": 250.0,
            "stop_loss_std": 2.5,        # Stop at 2.5σ from VWAP
        }
        if parameters:
            default_params.update(parameters)

        super().__init__(
            strategy_id=strategy_id,
            asset_class=AssetClass.EQUITIES,
            parameters=default_params,
            tier=StrategyTier.CORE,
            symbols=EQUITY_SYMBOLS,
            timeframe="15Min",
            max_signals_per_cycle=2,
        )

    async def generate_signals(
        self,
        bars: dict[str, list[dict]],
        market_regime: MarketRegime,
    ) -> list[Signal]:
        """Generate VWAP-based signals across equity symbols."""
        all_signals: list[Signal] = []

        for symbol in self.symbols:
            symbol_bars = bars.get(symbol, [])
            if len(symbol_bars) < 20:
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
        """Analyze a single symbol for VWAP deviation signals."""
        closes = np.array([b["close"] for b in symbol_bars])
        highs = np.array([b["high"] for b in symbol_bars])
        lows = np.array([b["low"] for b in symbol_bars])
        volumes = np.array([b["volume"] for b in symbol_bars])

        current_price = closes[-1]

        # Calculate VWAP and bands
        vwap, upper_band, lower_band, std = self._calc_vwap_bands(
            highs, lows, closes, volumes,
            self.parameters["vwap_std_entry"],
        )

        if vwap is None:
            return []

        current_vwap = vwap[-1]
        current_upper = upper_band[-1]
        current_lower = lower_band[-1]
        current_std = std[-1]

        if current_vwap <= 0 or current_std <= 0:
            return []

        # How far price is from VWAP in standard deviations
        z_score = (current_price - current_vwap) / current_std
        deviation_pct = abs(current_price - current_vwap) / current_vwap * 100

        logger.debug(
            "VWAP %s: price=%.2f vwap=%.2f z=%.2f dev=%.2f%%",
            symbol, current_price, current_vwap, z_score, deviation_pct,
        )

        min_dev = self.parameters["min_deviation_pct"]
        position_size = self.parameters["position_size_usd"]
        stop_std = self.parameters["stop_loss_std"]

        # --- BUY: price below lower VWAP band ---
        below_band = z_score < -self.parameters["vwap_std_entry"]
        significant_dev = deviation_pct >= min_dev

        if below_band and significant_dev:
            confidence = self._calc_confidence(z_score, deviation_pct, volumes)
            quantity = position_size / current_price
            stop_loss = current_vwap - (current_std * stop_std)
            take_profit = current_vwap  # Target: return to VWAP

            logger.info(
                "VWAP BUY: %s z=%.2f dev=%.2f%% conf=%.2f",
                symbol, z_score, deviation_pct, confidence,
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
                        f"VWAP BUY {symbol}: price ${current_price:.2f} "
                        f"is {z_score:.1f}σ below VWAP ${current_vwap:.2f} "
                        f"({deviation_pct:.1f}% deviation). "
                        f"Target: VWAP return ${current_vwap:.2f}"
                    ),
                    market_regime=market_regime,
                    position_size_usd=position_size,
                    tier=self.tier,
                )
            ]

        # --- SELL: price above upper VWAP band ---
        above_band = z_score > self.parameters["vwap_std_entry"]

        if above_band and significant_dev:
            confidence = self._calc_confidence(-z_score, deviation_pct, volumes)

            logger.info(
                "VWAP SELL: %s z=%.2f dev=%.2f%% conf=%.2f",
                symbol, z_score, deviation_pct, confidence,
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
                    rationale=(
                        f"VWAP SELL {symbol}: price ${current_price:.2f} "
                        f"is {z_score:.1f}σ above VWAP ${current_vwap:.2f} "
                        f"({deviation_pct:.1f}% deviation)."
                    ),
                    market_regime=market_regime,
                    position_size_usd=position_size,
                    tier=self.tier,
                )
            ]

        return []

    @staticmethod
    def _calc_vwap_bands(
        highs: np.ndarray,
        lows: np.ndarray,
        closes: np.ndarray,
        volumes: np.ndarray,
        num_std: float,
    ) -> tuple[np.ndarray | None, np.ndarray | None, np.ndarray | None, np.ndarray | None]:
        """
        Calculate cumulative VWAP and standard deviation bands.

        VWAP = cumulative(typical_price * volume) / cumulative(volume)
        Bands = VWAP ± num_std * rolling_std(typical_price)
        """
        n = len(closes)
        if n < 10:
            return None, None, None, None

        typical_price = (highs + lows + closes) / 3.0

        # Cumulative VWAP
        cum_tp_vol = np.cumsum(typical_price * volumes)
        cum_vol = np.cumsum(volumes)

        # Avoid division by zero
        safe_cum_vol = np.where(cum_vol > 0, cum_vol, 1.0)
        vwap = cum_tp_vol / safe_cum_vol

        # Rolling standard deviation of typical price around VWAP (20-bar window)
        window = min(20, n)
        std = np.zeros(n)
        for i in range(window - 1, n):
            start = max(0, i - window + 1)
            window_tp = typical_price[start:i + 1]
            window_vwap = vwap[i]
            std[i] = np.sqrt(np.mean((window_tp - window_vwap) ** 2))

        # Fill early values with the first computed std
        if window > 1:
            std[:window - 1] = std[window - 1]

        upper = vwap + num_std * std
        lower = vwap - num_std * std

        return vwap, upper, lower, std

    @staticmethod
    def _calc_confidence(
        z_score_abs: float,
        deviation_pct: float,
        volumes: np.ndarray,
    ) -> float:
        """
        Confidence based on deviation magnitude and volume.

        Larger deviations from VWAP = higher probability of reversion.
        """
        # Z-score magnitude (0.20-0.45)
        z_abs = abs(z_score_abs)
        z_score_comp = min(0.20 + (z_abs - 1.5) / 5.0, 0.45)

        # Deviation percentage (0.10-0.25)
        dev_score = min(0.10 + deviation_pct / 5.0, 0.25)

        # Volume trend: increasing volume near VWAP band = more conviction
        if len(volumes) >= 5:
            recent_vol = np.mean(volumes[-5:])
            avg_vol = np.mean(volumes)
            vol_ratio = recent_vol / avg_vol if avg_vol > 0 else 1.0
            vol_score = min(max(vol_ratio - 0.8, 0) / 3.0, 0.20)
        else:
            vol_score = 0.0

        confidence = z_score_comp + dev_score + vol_score
        return min(max(confidence, 0.20), 1.0)

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
