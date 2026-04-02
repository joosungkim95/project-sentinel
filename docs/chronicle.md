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

## Running Themes

- **Cost obsession:** Everything is designed to stay under $30/mo. Haiku for routine calls, Sonnet only for strategy generation, prompt caching, no Kubernetes.
- **Safety-first architecture:** Risk Engine is the safety net. It gets the most tests, the most scrutiny, and can never be bypassed by the Strategy Engine.
- **Debug inputs before tuning outputs:** The signal drought taught us to verify the data pipeline is connected before assuming the processing logic is wrong.
- **Shadow mode as training wheels:** Real trades at minimum size build confidence in the system before scaling up.
- **Single-person ops:** Discord alerts instead of PagerDuty, Railway instead of AWS, SQLite for backtesting — complexity is the enemy.
