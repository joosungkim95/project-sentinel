# Sentinel Project Chronicle

A running narrative of building a personal autonomous trading platform with Claude Code. Intended for future content (Twitter thread series).

---

## Phase 1: Scaffolding & Core Infrastructure (late March 2026)

**The Vision:** Build a fully autonomous trading bot that trades equities (Alpaca), crypto (Coinbase), and prediction markets (Kalshi) — all orchestrated by Claude's API for strategy generation and learning. Target: self-funding at <$30/mo operating cost.

**Key architectural decision:** Four-engine hierarchy where the Risk Engine has absolute veto authority and runs independently from the Strategy Engine. The Strategy Engine can never bypass or weaken risk rules.

**Built from scratch:**
- Risk Engine with hard floor, position sizing, asset class concentration, daily loss circuit breaker, weekly drawdown rules, and correlation limits
- Execution Engine with platform adapters for Alpaca, Coinbase, and Kalshi
- Trading Pipeline orchestrator wiring signal → risk check → execute → learn
- Memory/context system so Claude API calls get focused context packages (keeping costs low with Haiku for routine decisions)
- PostgreSQL schema for trades, strategy performance, risk events, portfolio snapshots
- Discord webhook alerts for all trading activity
- Health check endpoint for Railway monitoring

---

## Phase 2: The Signal Drought (late March 2026)

**The problem:** After deploying to Railway, zero trades were executing. Every strategy cycle produced zero signals despite markets being open and active.

**The debugging journey:**
- First suspicion: confidence gates too tight — spent time tuning thresholds
- Added more strategies (went from 5 → 8 → 15 strategies across three tiers: scout/core/sniper)
- Built a market regime classifier (SMA slope + ATR ratio) to help strategies adapt
- Added KCS-05 event catalyst strategy for prediction markets
- Still nothing. The "drought" persisted.

**The real bug (commit 3af1608):** Platform adapters were *registered* but `connect()` was never called on startup. The Alpaca `_data_client` and Coinbase `_client` were `None`. Every data fetch silently returned empty results. Every strategy saw no market data → produced no signals. The entire drought was caused by a single missing `await adapter.connect()` call.

**Lesson:** When debugging "no output" problems, check whether the *input* pipeline is actually connected before tuning the processing logic.

---

## Phase 3: Shadow Mode & Safety Hardening (March 29-31, 2026)

**Shadow mode:** All trades execute at minimum size (1 share / 0.0001 BTC / 1 contract) on real platforms, with full-size paper simulations tracked in parallel for divergence detection.

**Paper vs live trade distinction:** Added clear labeling in Discord alerts so Jay can tell at a glance whether a trade is shadow (real but min-size) or paper (simulated).

**Signal cooldown:** Added 4-hour cooldown per (strategy, symbol, side) to prevent duplicate trade alerts from the same signal firing repeatedly.

**Duplicate alert fix:** Discord was double-alerting on executed trades — one from `_evaluate_and_execute` and one from `run_cycle`. Fixed by removing the inner alert.

**Portfolio-relative position sizing:** Positions are now scaled as a % of portfolio value per tier (scout: 2%, core: 5%, sniper: 3%) instead of fixed dollar amounts. Added TierBudgetRule to the risk engine.

---

## Phase 4: P&L Safety Rules Fix (March 31 - April 1, 2026)

**The problem:** Three critical safety rules — HardFloor, DailyLoss circuit breaker, and WeeklyDrawdown — were non-functional. They all read from `portfolio.daily_pnl`, `portfolio.weekly_pnl`, and `portfolio.drawdown_from_peak`, which were hardcoded to `0.0` in the executor.

**The fix:** Added `_get_enriched_snapshot()` to the TradingPipeline that:
- Queries `TradeRecord` for realized P&L (daily/weekly/total windows)
- Queries `PortfolioSnapshotRecord` for historical peak portfolio value
- Computes drawdown as % decline from peak
- Patches the executor's real-time snapshot with computed values

**Design decision:** Kept the Executor DB-free (it's a platform-communication layer). The Pipeline owns the DB session and handles enrichment — clean separation of concerns.

**Also:** Added 4 new columns to `PortfolioSnapshotRecord` + Alembic migration so the computed values get persisted with each snapshot.

**Repo went public:** Made `joosungkim95/project-sentinel` public to enable Claude Code mobile dispatch. Verified no API keys or secrets were ever committed — all credentials live in Railway env vars and local `.env` (gitignored).

---

## Phase 5: Coinbase Fix & Dashboard Overhaul (April 1, 2026)

**The Coinbase divergence bug:** Shadow mode was reporting 100% divergence on crypto — 19/19 paper trades filled but only 1/19 live trades succeeded. System correctly auto-paused live trading. Root cause: shadow mode's crypto minimum was 0.0001 BTC (~$8.74 at $87k BTC), but Coinbase requires $10 minimum for market buy orders. Paper simulation blindly returned `executed=True` with no platform validation, so it never caught the mismatch.

**The fix:** Bumped crypto minimum to 0.00012 BTC (~$10.20) and added a pre-flight USD check in the Coinbase adapter that raises a clear error before sending undersized orders.

**The dashboard problem:** Trades panel was a flat laundry list with no way to distinguish paper from live trades. The `TradeResult.platform` field ("coinbase", "paper_crypto", etc.) was available in the execution engine but got dropped during database persistence.

**The fix:** Full-stack change across all three layers:
- Data: Added `platform` column to `TradeRecord` + Alembic migration, wired through `insert_trade`
- API: Added server-side filtering (`/trades?platform=paper&asset_class=crypto`) 
- Dashboard: Redesigned trades panel with All/Live/Paper tab bar, platform badges (blue for live, gray for paper), asset class dropdown filter, day-grouped layout with daily P&L summaries, and a shadow status bar showing health/divergence metrics from the `/shadow` endpoint

**Also evaluated:** "Smart money" wallet-following strategy for crypto. Decided against — survivorship bias, front-running by existing copy-trade bots, execution gap on whale-sized trades, and wash trading contamination make it an unreliable edge. Better to sharpen our own 15 strategies.

---

## Phase 6: The Missing Exit System (April 2, 2026)

**The discovery:** Checked `/health` — system was running, crypto and predictions schedulers cycling normally, but only 1 signal generated in 2 hours. Dug into `/trades` and found the real problem: **positions were never being closed.** Every single trade had `pnl: null`. The DB columns `exit_time`, `exit_price`, `pnl` existed from day one but nothing ever populated them. The `close_position()` methods on the adapters existed but were never called.

**The cascade:** This meant:
- The P&L enrichment from the previous session was querying `WHERE pnl IS NOT NULL` — always returning 0
- Risk Engine's daily/weekly P&L safety rules were seeing 0.0 forever
- Portfolio value never reflected realized gains or losses
- Positions accumulated indefinitely with no way to free up capital

**Bug #1 — Signal cooldown broken:** `vol_harvest_crypto` was spamming BTC-USD BUY signals every hour despite a 4-hour cooldown. Root cause: `datetime.utcnow()` (naive) vs `datetime.now(timezone.utc)` (aware) — the subtraction silently failed or raised a TypeError, and the cooldown was never enforced.

**Bug #2 — No exit logic anywhere:** Built a complete position exit system:
- `PositionManager` checks stop-loss (5%), take-profit (10%), and max hold time (7 days) on every pipeline cycle
- SELL signals from strategies (e.g., vol harvest detecting new volatility expansion) now close the matching open BUY position instead of trying to open a new trade
- Trade records get `exit_time`, `exit_price`, `pnl`, `pnl_pct` populated

**The alert flood:** First deploy closed ~56 accumulated open trades simultaneously, each sending its own Discord alert. Jay got flooded with messages tagged as "LIVE" trades (because `platform: null` on pre-tracking trades mapped to "unknown" which wasn't recognized as paper). Fixed by batching all exit alerts into a single summary message per cycle.

**Lesson:** Build the full trade lifecycle (open → monitor → close) before deploying. Entry without exit isn't trading — it's hoarding.

---

## Phase 7: The Cooldown That Never Cooled & The Silent Strategies (April 3, 2026)

**The first real "operations review":** Checked in on Sentinel after ~24 hours of runtime. Jay noticed risk stops firing. The data told the story: vol_harvest_crypto was the only strategy generating signals, and the Risk Engine had rejected 20 out of 26 signals (77% blocked) as BTC trended down. Total paper P&L: -$639.61. The Risk Engine was doing its job — but the cooldown wasn't.

**Bug #1 — Cooldown was a no-op:** The datetime fix from the previous session (naive vs aware) was correct but irrelevant. The real bug was architectural: the scheduler creates a **new TradingPipeline instance every cycle** (`_run_tier_cycle()` at scheduler.py:250). The `_last_executed` dict lived on the pipeline instance, so it was born empty every 60 minutes. The cooldown could never remember a previous execution.

**The fix:** Replaced the in-memory dict with a DB query against the trades table. `_is_on_cooldown()` checks `WHERE strategy_id = ? AND symbol = ? AND side = ? AND created_at >= (now - 4 hours)`. Survives pipeline recreation, process restarts, and deploys. Also catches rejected signals (which are stored in the same table), so vol_harvest won't spam the Risk Engine with hourly rejections either.

**Bug #2 — 14/15 strategies were silent:** Dug into why only vol_harvest_crypto fired across 403 crypto cycles, 331 prediction cycles, and 45 equity cycles. Found three cascading data pipeline issues in the prediction strategies:

1. **`run_tier` was dropping `crypto_bars`:** Line 581 re-wrapped `pred_data` as `{"markets": ...}`, stripping out the `crypto_bars` key. KCS-02 and KCS-05 always saw empty crypto bars → failed vol calculation → returned zero signals.

2. **Wrong market schema for KCS-02/KCS-05:** The pipeline called `get_markets()` (minimal schema: ticker, title, yes_bid, no_bid, volume, status) for all prediction strategies. But KCS-02 and KCS-05 need `strike_price` and `close_time` — fields only available from `get_crypto_markets()`. Every market failed the null check at line 184 and returned None.

3. **Value pricing starved of fields:** `get_markets()` was also missing `yes_ask`, `no_ask`, and `open_interest` — all required by the value_pricing strategy's liquidity filters.

**The fix:** Routed crypto prediction strategies to `get_crypto_markets()`, passed the full `pred_data` dict instead of re-wrapping, and added the missing fields to `get_markets()`.

**Not bugs:** Equities only running during market hours (correct), other crypto strategies (breakout, trend_following) not firing in a trending-down/high-volatility regime (their conditions are genuinely strict for current market conditions).

**Lesson:** In-memory state in a system where instances are recreated is no state at all. And when debugging "no output" from multiple independent subsystems, check whether each one is receiving the data schema it expects — a shared data fetch function serving different consumers can silently starve some of them.

---

## Running Themes

- **Cost obsession:** Everything is designed to stay under $30/mo. Haiku for routine calls, Sonnet only for strategy generation, prompt caching, no Kubernetes.
- **Safety-first architecture:** Risk Engine is the safety net. It gets the most tests, the most scrutiny, and can never be bypassed by the Strategy Engine.
- **Debug inputs before tuning outputs:** The signal drought taught us to verify the data pipeline is connected before assuming the processing logic is wrong.
- **Shadow mode as training wheels:** Real trades at minimum size build confidence in the system before scaling up.
- **Single-person ops:** Discord alerts instead of PagerDuty, Railway instead of AWS, SQLite for backtesting — complexity is the enemy.
