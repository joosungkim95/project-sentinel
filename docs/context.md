# Sentinel — Operational Context

> **This file is the single source of truth for remote Claude sessions (dispatch/cowork).**
> It mirrors the local memory system. Updated at the end of every session.
>
> Last updated: 2026-05-06

---

## About Jay (User)

Jay is building Sentinel as a personal project. Experienced developer comfortable with Python, FastAPI, SQLAlchemy, Docker. Building across US equities (Alpaca), crypto (Coinbase), and prediction markets (Kalshi today; Polymarket once US-regulated invite arrives).

- Based in New York. Polymarket relaunched in the US Dec 2025 (CFTC-regulated DCM via QCX acquisition); Jay is on the waitlist for the US-regulated path. International polymarket.com works but is the legality-gray-area path for NY residents — not used.
- Uses Claude Desktop for research/brainstorming and Claude Code for implementation
- Prefers getting things done over extended discussion — "let's go for it" when direction is clear
- Comfortable with risk management concepts (Kelly criterion, ATR stops, tier-based budgets)

---

## Session Hygiene (Follow These Rules)

1. Update `TODO.md` by checking off completed items and adding new tasks after each chunk of work.
2. At session end, update this file (`docs/context.md`) with anything that changed.
3. At session end, update `docs/chronicle.md` with narrative entries for milestones, bugs, breakthroughs, or interesting moments.
4. At session end, `git push origin main` — Railway auto-deploys, and mobile dispatch reads from GitHub.
5. Do not add trailing summaries to responses — Jay can read the diff.

---

## Deployment Status

- **Railway URL:** https://sentinel-production-c4dd.up.railway.app
- **GitHub:** https://github.com/joosungkim95/project-sentinel (public, auto-deploys on push to main)
- **Railway project:** https://railway.com/project/f440a704-9375-4faf-9a3b-2e614980c437
- **Services:** sentinel (app), Postgres (DATABASE_URL linked), Redis (REDIS_URL linked)

**Current state (2026-05-06):**
- **Equity silence diagnosed and fixed.** Two distinct bugs in `engines/execution/alpaca.py`:
  - `tf_map` used `TimeFrame(N, "Min")` with a string second arg. alpaca-py 0.43.x's `validate_timeframe` lets the string through silently, but the later `tf.value` f-string raises `AttributeError: 'str' object has no attribute 'value'` — killing every scout/core equity bar fetch (210 errors/11h on the broken paths).
  - `StockBarsRequest` was constructed without `feed="iex"`, so it defaulted to SIP. Free-tier Alpaca subscription doesn't permit SIP, so the daily-bar/sniper paths returned `{"message":"subscription does not permit querying recent SIP data"}` — the 15 hourly errors on IWM/QQQ/SPY in the log window. Fix: extracted `_ALPACA_TIMEFRAME_MAP` to module level using `TimeFrameUnit` enum, added explicit `feed="iex"`. 7 new unit tests.
- **Kalshi 429 storm fixed at the source.** Pipeline's `_fetch_prediction_data` was running a per-market `get_quote(ticker)` enrichment loop on top of `get_markets(...)` — but `get_markets` already returns `yes_bid/no_bid/yes_ask/no_ask/volume/open_interest/status` directly. The loop was 100% redundant work. Removed it; 100+ calls/cycle drop to 1. value_kalshi/skimmer_kalshi keep the same scan depth.
- **Portfolio snapshot writer wired.** `data/repositories/portfolio.insert_portfolio_snapshot` had been defined but **never called** anywhere in the codebase — `/portfolio` returning "no snapshots yet" was literal nothing-being-written, not a runtime failure. Added `_persist_portfolio_snapshot` to the scheduler on a 5-min IntervalTrigger.
- **Logging now configured at startup.** `api/main.py` had no `logging.basicConfig`, so Python's default WARNING level was silently dropping every `logger.info` call: `Trade EXECUTED`, `Coinbase order submitted`, `Kalshi order submitted`, `Shadow mode ENABLED`, `Price refreshed`. Added `logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(), ...)`. **This was the prerequisite for triaging Bug #6 below — the failure-side log line wasn't reaching Railway.**
- **All 414 tests passing** (was 405; +9 new written test-first across 3 new test files).

**Bug #6 — shadow live fill failures, INSTRUMENTATION DEPLOYED, awaiting fresh signals.** Pre-fix shadow_stats showed `live_executed=1, live_failed=22, fill_rate_match=4.3%, max_price_divergence=100%` since the 5/3 redeploy. Every trade in the DB is `platform: paper_*` — nothing landing on real Coinbase. The actual failure path was invisible because of the missing logging config. Added `Shadow live OK / Shadow live FAILED` diagnostic lines to `engines/execution/shadow.py:execute_shadow` so the next signal that fires reveals the exact failure path (commit b516248). Cooldowns blocked live observation on the new container — 1-4 hours of natural cooldown elapse needed before fresh signals arrive. Theory still mismatches reality: predictions should hit Kalshi `observe_only=True` → `_simulate_fill()` → `executed=True` (live_executed should have been ≥ 8 from the 8 prior prediction trades), but actual was 1.

**NEW BUG (separate from #6) — KCS-02/05 BUY_NO signals silently dropped.** `engines/strategy/predictions/crypto_probability.py:320-324` emits `Side.SELL` for BUY_NO opportunities. `engines/pipeline.py:822-824` routes any `Side.SELL` to `_handle_sell_signal` which closes existing positions. BUY_NO signals never reach the executor: `_handle_sell_signal` finds no matching position and returns None at DEBUG level (silent at INFO). Witnessed live on the 5/6 fresh container: KCS-02 generates 3 signals per cycle (typically 1 BUY_YES + 2 BUY_NO); only the BUY_YES reaches the cooldown check, the BUY_NOs vanish without trace. Proper fix needs richer signal semantics (BUY_NO ≠ SELL on prediction markets) and Kalshi adapter changes (currently hardcodes `side="yes"`). Tracked in `TODO.md`.

**Real money tracking added.** Discord alerts and `/portfolio` endpoint now distinguish paper-account capital from real-money capital. Each adapter declares `is_paper` (Alpaca flips on `paper` substring in URL; Coinbase/Kalshi explicitly real) and exposes `real_money_value()`. `Executor.get_portfolio_snapshot` aggregates into `snapshot.real_money_total`. Verified live: total $100,132.08 = $99,999.69 Alpaca paper + $132.39 real (Coinbase $100 USD top-up + ~$18 BTC/ETH + $10 Kalshi). Migration `c3d4e5f6a7b8` added the column to `portfolio_snapshots`. Risk-event Discord alerts gain a "Real Money" field alongside "Portfolio Value".

**Open items carried forward:**
- Shadow executor discards live `TradeResult` after counter increment (no DB persistence). Coinbase account history confirmed zero real fills Apr 10–22 — can't audit from our DB. Persist live results.
- Health monitor reports `kalshi: healthy` even when adapter failed to register at startup (initial-state bug, not live state).
- Railway usage alerts / spend cap (carried from 5/3).

**Next session focus (2026-05-07):**
- Wire Alpaca **live** account alongside paper. Requires the multi-adapter routing fix (`Executor.register_adapter` currently keys by `AssetClass` only, so a second `EQUITIES` adapter would silently overwrite paper). Same fix would also unblock Polymarket — but Polymarket stays paused (still US-waitlisted).
- Catch and diagnose Bug #6 from the new `Shadow live OK / FAILED` diagnostic logs.
- Confirm equity signals fire on Thursday 5/7 market open (first market hours with the alpaca.py fixes).

---

**Prior current state (2026-04-10) — kept for context:**
- 15 tiered strategies active (v5): 4 scouts, 7 core, 4 snipers
- Confidence gates: scout 0.2, core 0.4, sniper 0.7
- Market regime classifier: SMA slope + ATR ratio
- Shadow mode ON
- All 3 platform adapters connected (Alpaca, Coinbase, Kalshi)
- P&L/drawdown enrichment live — HardFloor, DailyLoss, WeeklyDrawdown safety rules now read real values
- Repo is public (no secrets committed)
- Alembic migrations up to b2c3d4e5f6a7 (adds platform column to trades table ��� auto-runs on deploy)
- Dashboard redesigned: trades panel now has All/Live/Paper tabs, platform badges, asset class filter, day-grouped layout, shadow status bar
- Coinbase min order fix deployed: shadow crypto minimum bumped from 0.0001 to 0.00012 BTC (~$10.20) to clear Coinbase's $10 market order floor
- Coinbase adapter now validates USD amount before submitting market buy orders
- **Position exit system live** — PositionManager checks stop-loss (5%), take-profit (10%), max hold (7d) each cycle; SELL signals close matching open BUY positions; trade records get exit_time/exit_price/pnl populated
- **Signal cooldown fixed (DB-backed, tier-aware)** — replaced in-memory cooldown dict (was reset every cycle because scheduler recreates TradingPipeline) with DB query against trades table; tier-aware windows: scout 2h, core 4h, sniper 24h; rejected signals also trigger cooldown (intentional — prevents hammering risk engine)
- **Batched Discord alerts** — position closes send one summary message instead of per-trade alerts
- **Prediction strategies unblocked** — KCS-02/KCS-05 now use get_crypto_markets() for full schema (strike_price, close_time); run_tier passes full pred_data dict (was dropping crypto_bars); get_markets() now includes yes_ask/no_ask/open_interest for value_pricing
- **Live price refresh** — pipeline now fetches spot price via adapter.get_quote() before risk check and execution, replacing stale bar-close prices (especially on daily-bar strategies)
- **Shadow market orders** — shadow executor now clears target_price on min-size live trades, forcing market orders instead of limit orders; fixes 0% live fill rate
- **Confidence recalibration** — raised base scores on 7 core/scout strategy confidence formulas so single-trigger-plus-confluence signals clear tier gates; addresses 14 silent strategies
- **Vol harvest trend filter** — BUY suppressed when regime is trending_down/high_volatility or 20-period SMA is declining; stops buying vol crush into downtrends. Verified working post-deploy: 0 signals across 11 sniper_crypto cycles since Apr 5 04:25 UTC deploy
- **Prediction strategy diagnostics** — upgraded all silent failure logs from DEBUG to WARNING/INFO with skip-reason breakdowns ({missing_fields, low_volume, low_oi, no_edge}) so Railway logs show exactly where each prediction strategy's signal pipeline breaks down
- **Stuck positions fixed** — PositionManager._check_single_exit() was returning early when _get_current_price() returned None, skipping the max_hold_time check. Max hold now runs first and uses entry_price as fallback exit price. 20 stuck BTC-USD positions from Apr 2 should auto-close on next cycle.
- **Shadow live fills fixed** — MIN_TRADE_SIZES[CRYPTO] was hardcoded at 0.00012 (BTC-specific, ~$10 at $85k BTC). For ETH (~$0.26) and AVAX (~$0.001) this was far below Coinbase's $10 minimum. Replaced with _crypto_min_quantity() that fetches live price and computes a symbol-aware quantity targeting $11 USD with buffer.
- **Prediction thresholds lowered** — value_pricing (min_edge 0.05→0.03, min_volume 100→20, min_oi 50→10), market_skimmer (min_edge 0.03→0.02, min_volume 50→10, min_oi 25→5), news_driven (min_volume 100→50), crypto_probability/KCS-02 (min_edge_pp 8.0→5.0, min_volume 50→10), event_catalyst/KCS-05 (min_edge_pp 6.0→4.0, min_volume 30→10).
- **news_driven fallback logic** — Kalshi adapter doesn't provide prev_yes_close or avg_daily_volume, so the strategy was returning None for every market. Now uses yes_ask/no_ask midpoint vs implied fair as a price-move proxy, and treats high absolute volume (500+ contracts) as a proxy for activity when avg_volume is missing.
- **run_cycle parameter fix** — dead code path in pipeline.py was passing `market_data=` to `generate_signals()` but the base class expects `bars=`. Scheduler uses `run_tier()` in production so this was never hit, but fixed for correctness.
- **Coinbase capital top-up** — Jay added $100 to Coinbase (2026-04-10) so shadow mode can actually execute the $11 min-size trades. Previously ~$10 balance couldn't cover even a single order. Deposited as USDC then converted to USD via Coinbase Convert (the -USD trading pairs need USD, not USDC).
- **All 405 tests passing** (up from 396 — the 9 previously-failing tests, including 2 pre-existing KCS-02 failures due to hardcoded past CLOSE_TIME, are now green)

**Env vars on Railway:** ALPACA_API_KEY, ALPACA_SECRET_KEY, COINBASE_API_KEY, COINBASE_API_SECRET, KALSHI_API_KEY, KALSHI_BASE_URL, KALSHI_PRIVATE_KEY, KALSHI_OBSERVE_ONLY, DATABASE_URL, REDIS_URL, DISCORD_WEBHOOK_URL, SHADOW_MODE, ANTHROPIC_API_KEY

**Deploy gotcha:** Railway aggressively caches Docker layers. If pip install or COPY layers show "cached" when they shouldn't, change the Dockerfile comment near that line to bust the cache.

---

## Platform Status

| Platform | Status | Mode | Notes |
|----------|--------|------|-------|
| Alpaca | Connected on Railway | Paper trading | `ALPACA_BASE_URL=https://paper-api.alpaca.markets` |
| Coinbase | Connected on Railway | Real money (shadow min-size ~$10) | COINBASE_API_SECRET PEM added via Railway Variables |
| Kalshi | Connected on Railway | Live (observe-only) | `KALSHI_BASE_URL=https://api.elections.kalshi.com` (migrated from `trading-api.kalshi.com` 2026-05-03 — old domain returns 401 with migration notice), `KALSHI_OBSERVE_ONLY=true` |
| Polymarket | Waitlisted for US (regulated) | N/A | Polymarket US (QCX/CFTC-regulated) relaunched Dec 2025; Jay on waitlist. International polymarket.com works (CLOB + wallet) but legality-gray for NY — not used. Will need multi-adapter routing fix in `Executor` before integrating (would overwrite Kalshi as the `PREDICTIONS` adapter). |

**Shadow mode:** All trades execute at minimum size (1 share / 0.00012 BTC / 1 contract) on real platforms, full-size paper simulations tracked in parallel.

**To switch to live:** Alpaca → change ALPACA_BASE_URL to `https://api.alpaca.markets`. Kalshi → change KALSHI_BASE_URL to `https://api.elections.kalshi.com`. Only after 2+ weeks clean shadow mode.

---

## Local Environment

- Python 3.12.13 via Homebrew
- Virtual env at `.venv/` (activate with `source .venv/bin/activate`)
- PostgreSQL 16 via Homebrew (not Docker — Docker is not on this machine)
- Postgres user: sentinel, password: sentinel_dev, db: sentinel, port: 5432
- All dependencies: `pip install -e ".[dev]"`
- Coinbase SDK: `coinbase-advanced-py` (CDP keys with EC PEM auth)
- Kalshi: RSA-PSS signed requests via `cryptography` library

---

## Strategy Portfolio (v5)

**15 strategies across 3 tiers:**
- **Scout (4):** momentum scalp, gap-and-go, crypto breakout, market skimmer
- **Core (7):** equity trend, mean reversion, VWAP, pullback, crypto trend, value pricing, crypto probability (KCS-02)
- **Sniper (4):** SMA crossover, vol harvest, news driven, event catalyst (KCS-05)

**Confidence gates:** scout 0.2, core 0.4, sniper 0.7

**Kalshi strategy roadmap:**
- KCS-02 done (probability divergence)
- KCS-05 done (event catalyst pre-positioning)
- KCS-07 next (crypto spot hedge — Risk Engine integration)
- KCS-04 later (range straddle — multi-leg)
- KCS-03/06 blocked on WebSocket support

**Key upcoming events for KCS-05:** NFP Apr 3, CPI Apr 14, FOMC May 6

---

## Planned Work (Not Yet Implemented)

**Claude API trading integrations (~$3-5/mo additional):**
1. Kalshi probability estimator — Haiku assesses true probability before value pricing evaluates edge
2. Pre-trade signal review — Haiku sanity check before execution (earnings, Fed, unusual conditions)

**Tuning levers if signal drought returns (in order):**
1. Market regime classifier (done in v5)
2. Relax strategy-level parameters (RSI thresholds, volume multipliers, BB widths)
3. Add more symbols (sector ETFs: XLK, XLF, XLE; more crypto: LINK, DOT)
4. Lower confidence gates further (floor: scout 0.15, core 0.30)

---

## Post-Deploy Review Checklist

Resolved from the 2026-04-10 deploy — kept for historical context:
- ✅ **Stuck positions closed.** All Apr 2 vol_harvest BTC entries (~$87,609 entry, 20+ rows) and Mar 31 vol_harvest ETH entries ($3,007.69 entry) have `pnl` populated → closed via max_hold_time. Realized losses (~$140 per BTC, ~$187 per ETH) since they sat through the late-March/early-April crypto downtrend.
- ❌ **Shadow live fills NOT working** (Apr 10 fix didn't land). Coinbase account history shows zero real fills Apr 10–22. The shadow executor discards live `TradeResult` after counter increment, so we couldn't see this from our DB — confirmed via Jay's Coinbase account directly. Open item: persist live results.
- ✅/❌ **Prediction strategies firing — partially.** Adapter URL/creds/schema all fixed 2026-05-03. KCS-02 etc. now run clean cycles, but produce zero signals because election markets are genuinely thin (`low_volume` on 95%+ of scanned markets) and the few that pass volume thresholds fail `no_edge`. Signal flow is healthy; market characteristics are the bottleneck.
- ⚠️ **KCS-05 catalyst window for CPI Apr 14 was missed** — Kalshi adapter was offline due to the URL typo, so the 5-day pre-positioning window had no working pipeline. Next macro catalyst: FOMC May 6.
- ✅ **Vol harvest trend filter holding** (no signals fired in high_volatility regime through Apr 22).
- 🔍 **Sniper cooldown enforcement** — still untested; vol_harvest hasn't fired since Apr 22 (regime + trial outage).

Next live-state checks (post 2026-05-03 deploy):
1. **Equity strategies on Monday 5/4 market open** — 0 trades across 7 equity strategies for 22 days pre-trial. Need market-hours data to diagnose whether it's data-fetch, signal-gen, or risk-rejection.
2. **Predictions volume reality** — confirm whether persistent `low_volume` is genuine market thinness (don't lower threshold) or a calibration miss (should lower).
3. **Kalshi 429 rate limiting** — value/skimmer per-market quote enrichment is hitting Kalshi's rate limit. Either batch via `/markets?tickers=` or add throttle.
4. **Shadow live fill observability fix** — persist live `TradeResult` so we can audit real Coinbase fill activity.
5. **Portfolio snapshots persistence** — `/portfolio` returns "no snapshots yet"; diagnose whether snapshot persistence is broken or never ran.
