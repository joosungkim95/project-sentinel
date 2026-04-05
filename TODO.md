# TODO.md — Sentinel Development Tracker

## Current Sprint: Phase 2 — Multi-Platform & Strategies

### Priority 1: Foundation
- [x] Set up pyproject.toml with dependencies
- [x] Configure Docker Compose (app + postgres + redis)
- [x] Set up Alembic for database migrations
- [x] Create SQLAlchemy models for all core tables
- [x] Run initial migration

### Priority 2: Risk Engine
- [x] Define Signal and TradeResult data models (Pydantic)
- [x] Implement RiskEngine.evaluate() interface
- [x] Hard floor rule (seed capital protection)
- [x] Position sizing limits (per-position and per-asset-class)
- [x] Daily loss circuit breaker
- [x] Weekly drawdown limiter
- [x] Write comprehensive unit tests for all rules
- [x] Risk event logging to database

### Priority 3: First Platform Connection
- [x] Alpaca client wrapper (paper trading mode)
- [x] Fetch quotes, account info, positions
- [x] Place test order and confirm execution
- [x] Execution Engine adapter for Alpaca

### Priority 4: First Strategy
- [x] Abstract Strategy base class
- [x] SMA Crossover strategy for SPY
- [x] Wire: Strategy → Risk Engine → Execution → Logging
- [x] Verify full pipeline with paper trade

### Priority 5: Monitoring
- [x] Health check endpoint (/health)
- [x] Discord webhook alert utility
- [x] Alert on: trade executed, risk limit hit, system error

---

## Phase 2 — Next Up
- [x] Coinbase Advanced Trade API connection (CDP keys, market data, order placement)
- [x] Kalshi demo API connection (RSA auth, orders, positions, market data)
- ~~Polymarket API connection~~ (blocked — US trading restricted)
- [x] Scheduler: run strategies on cron (APScheduler — 15min equities, 5min crypto)
- [x] Momentum strategy (equities)
- [x] Trend following strategy (crypto)
- [x] Model-based pricing strategy (prediction markets / Kalshi)
- [x] React dashboard (portfolio, positions, P&L, trades)

## Phase 3 — Complete
- [x] Memory/context management layer (ContextManager wired to DB, TradeJournal, StrategyJournal, MarketRegimeTracker, LearningContext)
- [x] Learning Engine fast loop (daily: metrics aggregation, regime classification, performance persistence, daily summary alerts)
- [x] Learning Engine slow loop (weekly: Claude API analysis via Sonnet, hypothesis generation, strategy recommendations, prompt caching)
- [x] Claude API integration for strategy hypothesis generation (wired into slow loop with cost controls, JSON structured output)
- [x] Backtesting framework (BacktestEngine, synthetic data generators, walk-forward slicing, equity curve + Sharpe + max drawdown)
- [x] Strategy graveyard and resurrection logic (GraveyardManager: cooldown, regime matching, max resurrections, paper trading enforcement)
- [x] Mean reversion strategy (equities): Bollinger Bands + RSI, regime-aware (skips trending markets), take-profit at middle band
- [x] Volatility harvesting strategy (crypto): BB width spike/crush detection + ATR decline, enters after vol contraction
- [x] News-driven strategy (predictions): volume/price spike detection, momentum riding, ranked by score

## Phase 4 — Complete
- [x] Risk Engine stress testing (6 scenarios: flash crash, correlated selloff, gap down, cascading failure, concentration drift, slow bleed)
- [x] Shadow mode (ShadowExecutor: min-size live + full-size paper in parallel, auto-pause on divergence)
- [x] Divergence detection (fill price, fill rate, latency divergence tracking with rolling stats and alerting)
- [x] Error recovery and graceful degradation (HealthMonitor, exponential backoff, auto-recovery, GracefulDegradation with cache fallback)
- [x] Full system monitoring dashboard (risk events, system health, learning engine panels + /risk-events, /performance, /system-health, /learning API endpoints)
- [x] Runbook for manual intervention (docs/RUNBOOK.md — emergency procedures, routine ops, shadow mode promotion, monitoring checklists)

## Phase 5 — Complete
- [x] Tiered strategy portfolio v2: scout/core/sniper system with non-overlapping signal types
- [x] Symbol universe expansion: 7 equities (SPY, QQQ, AAPL, MSFT, NVDA, IWM, DIA), 5 crypto (BTC, ETH, SOL, AVAX, DOGE)
- [x] Multi-timeframe support: 15min (scouts), 4h (core), daily (snipers) — pipeline passes timeframe to adapters
- [x] Bar aggregation utility: 1h→4h for Coinbase (no native 4h granularity)
- [x] Tier-aware risk rules: TierBudgetRule (20/50/30%), ConfidenceGateRule (0.3/0.5/0.7), expanded correlation groups
- [x] Tier-based scheduler: separate job groups per (tier, asset_class) with different intervals
- [x] 10 strategies: momentum scalp, breakout detector, market skimmer, equity trend, mean reversion, crypto trend, value pricing, SMA cross, vol regime shift, news catalyst
- [x] Signal drought detector: daily monitoring, Discord alerts with parameter adjustment suggestions

## Phase 6 — Complete
- [x] Signal drought fix: lowered confidence gates (scout 0.3→0.2, core 0.5→0.4)
- [x] Drought detector fix: per-job signal tracking instead of global count
- [x] Rejection logging: ConfidenceGateRule now logs every rejection for diagnosis
- [x] KCS-02: Implied probability vs spot divergence strategy (log-normal model, half-Kelly sizing)
- [x] Shared probability model: calc_realized_vol, calc_binary_probability, calc_half_kelly
- [x] Kalshi adapter: get_crypto_markets with series_ticker filter, expiry/strike data
- [x] Pipeline: crypto bars fetched from Coinbase for probability-model strategies
- [x] Macro catalyst calendar: FOMC, CPI, NFP dates for 2026

## Next Up
- [ ] Monitor tiered strategies for 1 week (rollback if shadow P&L > -5%)
- [ ] Promote from shadow mode to larger position sizes once strategies prove profitable
- [x] Implement proper market regime classifier (SMA slope + ATR ratio, persisted to DB, inline + daily)
- [x] Re-evaluate vol_harvest_crypto strategy quality: added trend filter — BUY suppressed when regime=trending_down/high_volatility or 20-period SMA is declining. SELL signals still allowed.

## Kalshi Crypto Strategy Roadmap (KCS)
- [x] KCS-02: Implied probability vs spot divergence (probability model + strategy)
- [x] Macro catalyst calendar (FOMC, CPI, NFP dates for 2026)
- [x] KCS-05: Event catalyst pre-positioning (sniper tier, vol-bumped prob model + macro calendar)
- [ ] KCS-07: Crypto spot hedge via Kalshi binary contracts (Risk Engine integration)
- [ ] KCS-04: Bracketed range straddle (multi-leg, uses vol model)
- [ ] KCS-03: 15-minute momentum scalp (requires WebSocket)
- [ ] KCS-06: Passive market making (requires WebSocket + elevated rate limits)

---

## Completed
- [x] Project scaffolding and directory structure
- [x] CLAUDE.md development guide
- [x] TODO.md task tracker
- [x] SQLAlchemy ORM models for all 6 core tables (trades, strategy_performance, risk_events, strategy_hypotheses, market_regimes, portfolio_snapshots)
- [x] Alembic async migrations with initial schema
- [x] Repository layer (trades, risk events, portfolio, strategy performance)
- [x] Database persistence wired into trading pipeline
- [x] FastAPI endpoints wired to database (/portfolio, /trades, /health)
- [x] Alpaca paper trading verified (connect, quote, order, position, close)
- [x] Full pipeline end-to-end: Signal → Risk Engine → Alpaca Execute → DB Persist
- [x] Coinbase adapter: CDP auth, market data, BUY/SELL verified with real money
- [x] Kalshi adapter: RSA-PSS auth, market data, limit orders verified on demo
- [x] DB migration: widened symbol column for Kalshi tickers (VARCHAR 20→100)
- [x] APScheduler: equities/15min, crypto/5min, predictions/10min with market hours, error tracking, pause/resume
- [x] FastAPI wired: scheduler lifecycle, emergency stop, per-asset pause/resume, /strategies endpoint
- [x] Momentum strategy (QQQ): ROC + RSI + volume confirmation, registered in app startup
- [x] Pipeline: auto-fetches historical bars from adapters before running strategies
- [x] Trend following strategy (BTC-USD, ETH-USD): EMA crossover + ADX trend strength + ATR stop-loss
- [x] Value pricing strategy (Kalshi): spread analysis, edge detection, liquidity filters, ranked signals
- [x] Pipeline: prediction market data fetching (market listings + quotes instead of bars)
- [x] React dashboard: Vite+React+TS+Tailwind, dark mode, polls API, emergency stop, scheduler controls
- [x] Railway deployment: Dockerfile, Postgres, Redis, auto-deploy from GitHub, live at sentinel-production-c4dd.up.railway.app
