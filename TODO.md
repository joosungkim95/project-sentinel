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
- [x] Prediction strategy diagnostic logging — upgraded DEBUG→WARNING with skip-reason breakdowns
- [ ] Verify prediction strategies generate signals on live Kalshi API (check Railway logs after deploy)
- [x] Fix pre-existing test failures in test_crypto_probability.py — CLOSE_TIME was hardcoded in the past, now uses now+3d
- [x] Fix stuck positions — PositionManager was returning early when live price unavailable, skipping max_hold check
- [x] Fix shadow live fill failures — MIN_TRADE_SIZES[CRYPTO] was BTC-specific (0.00012), now USD-based per symbol
- [x] Fix run_cycle parameter mismatch (market_data= → bars=) — dead code but correct now
- [x] Lower prediction strategy thresholds (min_volume, min_edge) across value_pricing, market_skimmer, news_driven, KCS-02, KCS-05
- [x] news_driven fallback — uses yes_ask/no_ask midpoint vs implied fair when Kalshi doesn't provide prev_price/avg_volume
- [x] Verify 20 stuck vol_harvest BTC positions auto-close on next cycle after deploy (confirmed: all Apr 2 BTC + Mar 31 ETH entries have pnl populated → closed)
- [ ] Verify shadow live fills succeed on Coinbase after $100 top-up (breakout_crypto should clear) — **FAILED**: Coinbase account shows zero real fills Apr 10-22

## Discovered 2026-05-03 (post-trial-expiry triage)
- [x] Fix Kalshi URL typo (`.kalshi.co` → `.kalshi.com`) — DNS NXDOMAIN was causing adapter to fail at startup, all 5 prediction strategies silent for 22 days
- [x] Clamp Coinbase candle requests to 349 (API rejects ≥350) — was causing trend_crypto (4h aggregated, 100*4=400 candles) to silently get empty bars and produce zero signals
- [x] Migrate Kalshi prod URL to `api.elections.kalshi.com` — old `trading-api.kalshi.com` returns 401 with migration notice on every endpoint (auth + public)
- [x] Replace demo Kalshi creds with prod creds (stored in gitignored `.env.prod`); tighten `.gitignore` to cover `.env.*`
- [x] Update Kalshi adapter for new API schema — dollar strings (`yes_bid_dollars`), float-precision strings (`volume_fp`, `open_interest_fp`), and `floor_strike` instead of `strike_price`. All three reader methods (get_quote, get_markets, get_crypto_markets) updated.
- [x] Investigate equity strategy silence — root cause: `TimeFrame(N, "Min")` enum mismatch + missing `feed="iex"` in `StockBarsRequest` (alpaca-py 0.43.x). Fixed in `engines/execution/alpaca.py`.
- [ ] Fix shadow executor live-fill observability — live `TradeResult` is currently discarded after counter increment; persist to DB or Redis so we can audit whether real Coinbase orders fire
- [x] Diagnose portfolio_snapshots persistence — `/portfolio` returned "no snapshots yet" because `insert_portfolio_snapshot` had **zero callers**. Wired into scheduler on 5-min IntervalTrigger.
- [ ] Set Railway usage alerts / spend cap to prevent silent trial / billing suspension
- [x] Kalshi 429 rate limiting — root cause: pipeline ran a redundant per-market `get_quote` loop on top of `get_markets` which already returns full pricing. Loop removed; 100+ calls/cycle → 1.
- [ ] Kalshi `low_volume` skips: 100% of value/skimmer markets fail volume threshold — confirm whether this is real (election markets are thin) or a calibration issue

## Discovered 2026-05-06
- [x] Add `logging.basicConfig` at app startup — Python default = WARNING was silently dropping all `logger.info` lines (Trade EXECUTED, order submitted, etc.) for the entire deployment lifetime. Configurable via `LOG_LEVEL` env var.
- [ ] **Bug #6 — Shadow live fill failures (22/23, fill_rate_match=4.3%).** Every signal since 5/3 redeploy went `paper_*` only; nothing on real Coinbase or Kalshi observe-only. Diagnostic logging added in `engines/execution/shadow.py` to log per-signal `Shadow live OK` / `Shadow live FAILED: ... error=...` lines (commit b516248). Theory says predictions in observe-only mode should always succeed (live_executed should be ≥ 8 from the 8 prior prediction trades), but actual was 1; need to see fresh signals fire to identify the path. Cooldowns are blocking live observation for the next 1-4 hours after each redeploy.
- [ ] **NEW BUG (separate from #6): KCS-02/05 BUY_NO signals silently dropped.** `engines/strategy/predictions/crypto_probability.py:320-324` emits `Side.SELL` for BUY_NO opportunities. `engines/pipeline.py:822-824` routes any `Side.SELL` to `_handle_sell_signal` which closes existing positions. So BUY_NO signals never reach the executor — `_handle_sell_signal` finds no matching position and returns None at DEBUG level. Witnessed live: KCS-02 generated 3 signals (1 BUY_YES, 2 BUY_NO), only the BUY_YES one ever reaches the cooldown check, the other 2 vanish. Proper fix needs richer signal semantics (BUY_NO ≠ SELL) and Kalshi adapter changes (currently hardcodes `side="yes"`).
- [ ] **Verify equity signals fire post-deploy.** Watch the next equity scout/core cycle on the new container — should see `Fetched N bars for SPY` style lines and signal/trade output.
- [ ] **Verify portfolio_snapshots populates** — should see "Portfolio snapshot persisted" log every 5 min; `/portfolio` should return data within 5 min of deploy.

## Polymarket (paused 2026-05-03)
- [ ] **Blocker:** Jay is on the waitlist for Polymarket US (CFTC-regulated). The international polymarket.com path is wallet-based + legality-gray-area for NY, so we're waiting for the US-regulated invite before building the adapter.
- [ ] When invite lands: re-research Polymarket US API (it's likely different from the international CLOB docs at docs.polymarket.com — the wallet-based auth probably doesn't apply on the regulated path).
- [ ] **Architectural prerequisite:** `Executor.register_adapter` (`engines/execution/base.py:112-114`) keys adapters by `asset_class`, so adding Polymarket as a second `PREDICTIONS` adapter would silently overwrite Kalshi. Need a multi-adapter routing fix (likely `(asset_class, platform_name)` key + `platform` field on Signal). Solve before landing Phase 1.
- [ ] Phase 1 scope (when ready): standalone Polymarket adapter + `polymarket_value_pricing` strategy mirroring `value_kalshi`. Phase 2 (cross-venue divergence/arb) is a separate spec on top.

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
