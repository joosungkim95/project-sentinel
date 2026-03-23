# TODO.md — Sentinel Development Tracker

## Current Sprint: Phase 1 — Core Infrastructure

### Priority 1: Foundation
- [ ] Set up pyproject.toml with dependencies
- [ ] Configure Docker Compose (app + postgres + redis)
- [ ] Set up Alembic for database migrations
- [ ] Create SQLAlchemy models for all core tables
- [ ] Run initial migration

### Priority 2: Risk Engine
- [ ] Define Signal and TradeResult data models (Pydantic)
- [ ] Implement RiskEngine.evaluate() interface
- [ ] Hard floor rule (seed capital protection)
- [ ] Position sizing limits (per-position and per-asset-class)
- [ ] Daily loss circuit breaker
- [ ] Weekly drawdown limiter
- [ ] Write comprehensive unit tests for all rules
- [ ] Risk event logging to database

### Priority 3: First Platform Connection
- [ ] Alpaca client wrapper (paper trading mode)
- [ ] Fetch quotes, account info, positions
- [ ] Place test order and confirm execution
- [ ] Execution Engine adapter for Alpaca

### Priority 4: First Strategy
- [ ] Abstract Strategy base class
- [ ] SMA Crossover strategy for SPY
- [ ] Wire: Strategy → Risk Engine → Execution → Logging
- [ ] Verify full pipeline with paper trade

### Priority 5: Monitoring
- [ ] Health check endpoint (/health)
- [ ] Discord webhook alert utility
- [ ] Alert on: trade executed, risk limit hit, system error

---

## Phase 2 Backlog
- [ ] Coinbase sandbox connection
- [ ] Polymarket API connection + paper trade simulator
- [ ] Kalshi demo API connection
- [ ] Momentum strategy (equities)
- [ ] Trend following strategy (crypto)
- [ ] Model-based pricing strategy (prediction markets)
- [ ] Cross-platform arbitrage detection (Polymarket vs Kalshi)
- [ ] React dashboard (portfolio, positions, P&L, trades)

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
