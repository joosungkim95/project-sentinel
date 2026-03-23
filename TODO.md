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

## Phase 3 Backlog
- [ ] Memory/context management layer
- [ ] Learning Engine fast loop (daily parameter updates)
- [ ] Learning Engine slow loop (weekly strategy evaluation)
- [ ] Claude API integration for strategy hypothesis generation
- [ ] Backtesting framework
- [ ] Strategy graveyard and resurrection logic
- [ ] Mean reversion strategy (equities)
- [ ] Volatility harvesting strategy (crypto)
- [ ] News-driven trading strategy (prediction markets)

## Phase 4 Backlog
- [ ] Risk Engine stress testing (simulated crashes)
- [ ] Shadow mode (real trades at minimum size)
- [ ] Divergence detection (real vs paper)
- [ ] Error recovery and graceful degradation
- [ ] Full system monitoring dashboard
- [ ] Runbook for manual intervention

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
