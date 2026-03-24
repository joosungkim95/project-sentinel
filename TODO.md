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
