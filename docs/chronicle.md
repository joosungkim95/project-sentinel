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

## Phase 8: The Performance Audit (April 5, 2026)

**The first strategy review:** Pulled up the `/trades` and `/health` endpoints to see how Sentinel performed on April 3. The data was sobering: `vol_harvest_crypto` was the *only* strategy firing, generating 7 BUY signals on BTC-USD, all losing (-$491 paper P&L). Every other strategy — all 14 of them — produced zero signals across hundreds of cycles.

**Six bugs found, all different root causes:**

**Bug #1 — Stale prices:** All 7 trades showed identical entry prices ($87,171.68) despite being hours apart. Root cause: `vol_harvest_crypto` uses daily candles, and `current_price = closes[-1]` just grabbed the incomplete daily bar's close — which doesn't change until midnight. The quantity calculation and limit orders all used this frozen number. Fix: pipeline now fetches a live spot price via `adapter.get_quote()` before risk check.

**Bug #2 — Cooldown too short for snipers:** 7 signals in one day on the same symbol suggested cooldown wasn't working. But after checking deploy times, the first 4 trades were pre-cooldown-fix (old in-memory code). The last 3 were 5 hours apart — just past the 4-hour cooldown window. The 4h flat cooldown was wrong for a daily-bar strategy where conditions persist all day. Fix: tier-aware cooldowns (scout 2h, core 4h, sniper 24h).

**Bug #3 — Risk engine working as designed:** All signals were "reduced" (quantity halved). This was `WeeklyDrawdownRule` cutting position sizes by 50% due to accumulated losses. Not a bug — the risk engine was doing its job.

**Bug #4 — Shadow mode 0% live fills:** Shadow stats showed 3 live attempts, 3 failures, 0 executions. The shadow executor passed the original signal (with `target_price` set) to Coinbase, causing limit orders instead of market orders. At $10 minimum size, limit orders at stale prices are unreliable. Fix: clear `target_price` on shadow min-size trades so the adapter uses market orders.

**Bug #5 — 14 silent strategies:** This was the deepest investigation. All adapters connected, all cycles completing without errors, just no signals making it through. Root cause: confidence formulas were calibrated for the original gates (scout 0.3, core 0.5) but gates were lowered in Phase 6 (0.2, 0.4) without recalibrating the formulas. A core strategy with a single trigger typically produced 0.20-0.35 confidence — below the 0.4 gate. Fix: raised base component scores across 7 strategies so a single trigger + one confluence factor clears the gate.

**Bug #6 — Vol harvest buying into downtrends:** The strategy's thesis (buy after vol crush, expect mean reversion) only works in ranging/bullish markets. In a sustained downtrend, the "vol crush" is just a pause before more selling. Strategy had no trend filter at all. Fix: suppress BUY when regime is `trending_down`/`high_volatility` or SMA slope is negative.

**Lesson:** A trading system can be architecturally sound but operationally broken in six different ways simultaneously. The issues weren't in the framework — they were in the calibration, the data freshness, the order types, and the strategy logic. Infrastructure gets you running; operations reviews keep you profitable.

---

## Running Themes

- **Cost obsession:** Everything is designed to stay under $30/mo. Haiku for routine calls, Sonnet only for strategy generation, prompt caching, no Kubernetes.
- **Safety-first architecture:** Risk Engine is the safety net. It gets the most tests, the most scrutiny, and can never be bypassed by the Strategy Engine.
- **Debug inputs before tuning outputs:** The signal drought taught us to verify the data pipeline is connected before assuming the processing logic is wrong.
- **Shadow mode as training wheels:** Real trades at minimum size build confidence in the system before scaling up.
- **Single-person ops:** Discord alerts instead of PagerDuty, Railway instead of AWS, SQLite for backtesting — complexity is the enemy.
