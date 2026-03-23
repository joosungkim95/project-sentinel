# CLAUDE.md — Sentinel Trading Platform

## What Is Sentinel?

Sentinel is a personal autonomous trading platform that operates across three asset classes:
US equities/ETFs, cryptocurrency, and prediction markets (Polymarket/Kalshi).

It is built around four engines in a strict hierarchy:
1. **Risk Engine** — Absolute veto authority. Protects seed capital. Cannot be overridden.
2. **Strategy Engine** — Aggressive optimizer. Maximizes risk-adjusted returns within risk budgets.
3. **Execution Engine** — Places, monitors, and closes trades across all connected platforms.
4. **Learning Engine** — Evaluates performance, refines strategies, discovers new opportunities.

**Key design principle:** The Risk Engine runs as an independent process. The Strategy Engine
cannot bypass, modify, or influence its rules.

---

## Architecture Overview

```
Market Data → Strategy Engine → Risk Engine (approve/reject) → Execution Engine → Platforms
                    ↑                                                    |
                    └──────────── Learning Engine ←── Trade Outcomes ─────┘
```

Every trade flows: Signal → Risk Check → Execute (or Log Rejection) → Learn

---

## Code Organization Principles

### Module Boundaries
Each engine is a **self-contained module** with a clear interface. Engines communicate
ONLY through well-defined interfaces (Python protocols/ABCs). Never import internals
from another engine.

```
engines/risk/     → Exposes: RiskEngine.evaluate(signal) → Approved | Rejected
engines/strategy/ → Exposes: Strategy.generate_signals(market_state) → list[Signal]
engines/execution/→ Exposes: Executor.execute(approved_signal) → TradeResult
engines/learning/ → Exposes: LearningEngine.update(trade_results) → ParameterUpdates
memory/           → Exposes: ContextManager.build_context(decision_type) → Context
```

### File Size & Complexity Rules
- **Max 300 lines per file.** If a file exceeds this, split it.
- **Max 5 public methods per class.** If a class does more, it's doing too much.
- **One concept per file.** `rules.py` contains risk rules. `alerts.py` contains alerting.
  Don't mix concerns.
- **Every module gets a README.md** explaining what it does, its interface, and how to test it.

### Naming Conventions
- Files: `snake_case.py`
- Classes: `PascalCase`
- Functions/methods: `snake_case`
- Constants: `UPPER_SNAKE_CASE`
- Type aliases: `PascalCase` (e.g., `Signal`, `TradeResult`)

### Documentation Requirements
- Every public function gets a docstring with: purpose, args, returns, raises.
- Every module gets a module-level docstring.
- Complex logic gets inline comments explaining WHY, not WHAT.
- Use type hints everywhere. Run mypy in strict mode.

### Testing Requirements
- Every engine has unit tests in `tests/unit/test_{engine}.py`
- Integration tests cover cross-engine pipelines in `tests/integration/`
- Risk Engine gets the most tests — it's the safety net.
- Target: 80%+ coverage on Risk Engine, 60%+ on everything else.

---

## Memory & Context Management System

**CRITICAL: Claude's API is stateless. Sentinel's intelligence comes from its memory layer.**

The `memory/` module maintains all persistent state and assembles context packages
for each decision the system makes. This is how Sentinel "remembers" and "learns."

### Memory Architecture

```
memory/
├── context_manager.py  # Assembles context packages for different decision types
├── trade_journal.py    # Records every trade with full context and outcomes
├── strategy_journal.py # Records strategy hypotheses, test results, lessons
├── market_regime.py    # Tracks and classifies market conditions over time
└── query_builder.py    # Builds efficient DB queries for context assembly
```

### Context Package Types

When Claude's API is called (for strategy hypothesis generation, market analysis, etc.),
the ContextManager assembles a focused context package. Each package type includes only
what's relevant to that specific decision:

1. **StrategyContext** — For generating new strategy ideas:
   - Current market regime (trending/ranging/volatile)
   - Top 5 performing strategies and their parameters
   - Bottom 5 strategies and why they failed
   - Asset class allocation and recent P&L
   - Last 10 strategy hypotheses tested and their outcomes

2. **TradeContext** — For evaluating individual trade signals:
   - Recent performance of this specific strategy
   - Current portfolio exposure and risk utilization
   - Relevant market conditions for this asset class
   - Similar past trades and their outcomes

3. **RiskContext** — For risk engine decisions:
   - Current portfolio state (positions, P&L, drawdown)
   - Correlation matrix across all positions
   - Recent risk events and circuit breaker history

4. **LearningContext** — For the weekly learning loop:
   - Full strategy performance over evaluation period
   - Market regime history
   - Parameter change history and their effects
   - Strategy graveyard (disabled strategies and why)

### Context Size Budget
Keep API calls cheap by budgeting context:
- Use Haiku ($1/$5 per MTok) for routine decisions (trade evaluation, risk checks)
- Use Sonnet ($3/$15 per MTok) for strategy generation and complex analysis
- NEVER use Opus for automated calls — reserve for development only
- Target: <4K tokens input per routine call, <8K for strategy generation
- Use prompt caching for system prompts that don't change between calls

---

## Cost Optimization Strategy

**Goal: Sentinel should fund its own operating costs from trading profits.**

### Monthly Cost Budget (Target: <$30/month)

| Service          | Tier        | Est. Cost  | Notes                                  |
|------------------|-------------|------------|----------------------------------------|
| Railway          | Hobby       | $5-10/mo   | App + Postgres + Redis                 |
| Claude API       | Pay-as-go   | $5-15/mo   | Haiku for routine, Sonnet for strategy |
| Polygon.io       | Free        | $0         | 5 calls/min, sufficient for our freq   |
| CoinGecko        | Free        | $0         | Rate-limited but adequate              |
| Alpaca           | Free        | $0         | Commission-free trading + data         |
| Coinbase         | Trading fees| ~0.5%/trade| Maker/taker fees on trades             |
| Polymarket       | Free        | $0         | No platform fees                       |
| Kalshi            | Free        | $0         | Fees built into spreads                |
| Discord webhooks | Free        | $0         | Alerts                                 |
| **Total**        |             | **~$15-25**| Before trading fees                    |

### Cost Rules Enforced in Code
- `config/costs.py` defines model selection per decision type
- NEVER call Claude API in a hot loop — batch decisions
- Cache market data aggressively in Redis (set appropriate TTLs)
- Strategy Engine runs on a schedule (e.g., every 15min for equities, every 5min for crypto)
  NOT on every price tick
- Learning Engine runs daily (fast loop) and weekly (slow loop), never more frequently
- Use Batch API (50% discount) for non-time-sensitive analysis
- Use prompt caching for system prompts (90% savings on repeated context)

### Minimizing Ops Complexity
- **Single Railway project** with all services (app, postgres, redis, scheduler)
- **No Kubernetes, no AWS, no multi-region** — keep it simple
- **Cron-based scheduling** via Railway's built-in cron, not a separate scheduler service
- **SQLite for backtesting** — don't hit the production DB
- **Health check endpoint** (`/health`) that Railway monitors automatically
- **Structured logging** to stdout — Railway captures it, no ELK stack needed
- **Discord alerts** for anything that needs human attention — no PagerDuty needed

---

## Database Schema (Key Tables)

```sql
-- Every trade with full context
trades (
  id, strategy_id, asset_class, symbol, side, quantity, price,
  signal_confidence, risk_check_result, risk_utilization_pct,
  entry_time, exit_time, exit_price, pnl, pnl_pct,
  market_regime, context_snapshot_id, created_at
)

-- Strategy performance tracking
strategy_performance (
  id, strategy_id, date, trades_count, win_rate,
  total_pnl, sharpe_ratio, max_drawdown, risk_budget_used,
  parameters_json, created_at
)

-- Risk events and circuit breakers
risk_events (
  id, event_type, severity, details_json,
  portfolio_value_at_event, action_taken, created_at
)

-- Strategy hypotheses and test results (Learning Engine)
strategy_hypotheses (
  id, hypothesis_text, source, market_regime,
  backtest_sharpe, backtest_max_dd, paper_trade_days,
  paper_trade_pnl, status, created_at, updated_at
)

-- Market regime classification
market_regimes (
  id, asset_class, regime_type, confidence,
  indicators_json, started_at, ended_at
)

-- Portfolio snapshots for context assembly
portfolio_snapshots (
  id, total_value, cash, positions_json,
  risk_utilization_json, created_at
)
```

---

## Platform API Quick Reference

### Alpaca (Equities)
- Paper: `APCA_API_KEY_ID` + `APCA_API_SECRET_KEY` with `base_url=https://paper-api.alpaca.markets`
- Docs: https://docs.alpaca.markets/
- Key: Commission-free, built-in paper trading, market data included

### Coinbase (Crypto)
- Sandbox: `https://api-sandbox.coinbase.com`
- Docs: https://docs.cdp.coinbase.com/advanced-trade/docs/welcome
- Key: Maker 0.4%, Taker 0.6% fees. Use limit orders.

### Polymarket (Prediction Markets)
- API: https://docs.polymarket.com/
- Key: Polygon-based, needs MATIC for gas. CLOB (central limit order book) API.
- No built-in paper trading — we simulate it.

### Kalshi (Prediction Markets)
- API: https://trading-api.readme.io/reference/getting-started
- Docs: https://kalshi.com/docs
- Key: US-regulated, direct USD deposits. Has demo environment.

---

## Development Workflow

### Starting a Claude Code Session
1. Read this CLAUDE.md first
2. Check `git log --oneline -10` to see recent changes
3. Check `TODO.md` for the current priority task
4. Run tests before and after changes: `pytest tests/ -v`
5. Update TODO.md when completing tasks

### PR/Commit Standards
- One logical change per commit
- Commit message format: `[engine] description` (e.g., `[risk] add daily loss circuit breaker`)
- Run `mypy . --strict` and `pytest` before committing
- Update relevant README.md files if interfaces change

### When Modifying the Risk Engine
**EXTRA CAUTION REQUIRED.** The Risk Engine is the safety net.
- Write the test FIRST (TDD)
- Get existing tests passing before adding new logic
- Never weaken a risk rule without explicit human approval
- Log every risk rule change in `CHANGELOG.md`

---

## Environment Variables

```bash
# Alpaca
ALPACA_API_KEY=
ALPACA_SECRET_KEY=
ALPACA_BASE_URL=https://paper-api.alpaca.markets  # Switch for live

# Coinbase
COINBASE_API_KEY=
COINBASE_API_SECRET=

# Polymarket
POLYMARKET_API_KEY=
POLYMARKET_PRIVATE_KEY=  # Polygon wallet

# Kalshi
KALSHI_API_KEY=
KALSHI_BASE_URL=https://demo-api.kalshi.co  # Switch for live

# Claude API (for Learning Engine)
ANTHROPIC_API_KEY=

# Database
DATABASE_URL=postgresql://...

# Redis
REDIS_URL=redis://...

# Discord Alerts
DISCORD_WEBHOOK_URL=
```

---

## Current Phase & Priorities

**Phase 1: Core Infrastructure (Week 1)**

Priority order:
1. ✅ Project scaffolding (this setup)
2. Database models and migrations
3. Risk Engine with hard floor + position limits
4. Alpaca paper trading connection
5. First strategy (SMA crossover on SPY) through full pipeline
6. Basic health check + Discord alerts

See `TODO.md` for detailed task breakdown.
