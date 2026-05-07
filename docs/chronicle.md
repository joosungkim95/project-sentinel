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

## Phase 9: The Weekend Review (April 5, 2026)

**First weekend operations check:** Jay asked to evaluate crypto and prediction strategies over the weekend (equities don't run on weekends). The data told a clearer story than expected — but required careful timeline analysis.

**The timeline trap:** At first glance, the last 24 hours of crypto trades looked terrible: 7 vol_harvest BUY signals, all losing, all in high_volatility regime despite a trend filter that should block exactly this. But cross-referencing with deploy times revealed the truth: commit 9cfe400 (which added the trend filter, tier-aware cooldowns, and live price refresh) didn't deploy until 04:25 UTC on Apr 5. Every single trade in the "last 24 hours" was running the *old* code.

**Post-deploy: silence is golden.** After the fix deployed, vol_harvest completed 11 sniper_crypto cycles with zero BUY signals — exactly correct behavior in a high_volatility regime. The trend filter is working. The stale price fix and 24h sniper cooldown are also deployed but untestable until the regime shifts and a signal actually fires.

**Cooldown archaeology:** Traced the cooldown through three evolutionary stages:
1. In-memory dict (pre Apr 3) — completely broken, reset every cycle when scheduler recreated TradingPipeline. Breakout_crypto fired 12 times in 55 minutes.
2. DB-backed, flat 4h (Apr 3) — survived restarts but wrong for snipers. Produced ~5h gaps.
3. DB-backed, tier-aware (Apr 5) — scout 2h, core 4h, sniper 24h. Current and correct. Also triggers on rejected signals (intentional — prevents hammering the risk engine with the same doomed signal).

**The prediction market mystery:** 5 strategies, 161+ scheduler cycles, zero trades. Ever. The scheduler was running, the cycles were completing, but every strategy was returning empty-handed. Root cause: **Kalshi demo API has no real trading activity.** The demo environment returns markets with zero volume/open_interest, and possibly no open KXBTC series at all. Every strategy's liquidity filters silently dropped every market. All of this was logged at DEBUG level — completely invisible in production logs.

**The fix (two parts):**
1. Jay switched Kalshi to the live API with observe-only mode (KALSHI_BASE_URL → trading-api.kalshi.com, KALSHI_OBSERVE_ONLY=true) — real market data, simulated fills.
2. Upgraded all prediction strategy failure-point logs from DEBUG to WARNING with detailed skip-reason breakdowns. Now Railway logs will show exactly which bottleneck kills signals: `{missing_fields: 5, low_volume: 12, no_edge: 3}` instead of silence.

**Lesson:** Demo APIs are great for testing auth and order flow, but worthless for testing signal generation that depends on real market activity. And when a subsystem produces zero output for 161 consecutive cycles, the logging should scream — not whisper at DEBUG level.

---

## Phase 10: The Four-Bug Audit (April 10, 2026)

**The check-in:** Jay asked for a trading progress review after ~5 days of runtime. The topline numbers were brutal: 93 trades, 71 closed, **0 winners, -$8,381 paper P&L**. On the surface, the system looked catastrophic. Digging in revealed four separate, unrelated bugs — none of which were visible from the high-level dashboard, all of which needed to be fixed before the system could even fairly demonstrate whether the strategies have any edge.

**Bug #1 — 20 stuck positions from April 2.** Checked `/trades` and found 20 identical vol_harvest BTC-USD BUYs at $87,609 entry, all still open 7+ days later. The position exit system should have closed them via the 168-hour max_hold_time rule. Traced the logic through PositionManager._check_single_exit and found the bug at line 121-123: the method fetched `current_price` first and `return None` if unavailable, **skipping all exit checks including max_hold_time**. Coinbase's `get_quote()` for BTC-USD was occasionally failing (maybe rate limiting, maybe transient network), and whenever it did, every stuck position remained stuck. Fix: reordered the method so max_hold_time runs first, independently of live price, using `entry_price` as fallback when no quote is available. Positions older than 7 days will now close regardless of adapter health.

**Bug #2 — Shadow mode live fills all failing.** `/shadow` showed 2 live attempts, 2 failures, 0 executions on breakout_crypto (ETH-USD Apr 6, AVAX-USD Apr 9). The shadow executor had a hardcoded `MIN_TRADE_SIZES[CRYPTO] = 0.00012` — calibrated for BTC at $85k (~$10.20). But 0.00012 ETH at $2,130 is $0.26, and 0.00012 AVAX at $9 is $0.001. Both are orders of magnitude below Coinbase's $10 market-order minimum. Every non-BTC live fill attempt hit the adapter's pre-flight `ValueError("Market buy $X below Coinbase minimum $10")`. The error was caught, logged as a generic failure, and recorded as a fill_rate divergence. Fix: removed the hardcoded quantity; added `_crypto_min_quantity(symbol)` that fetches the current price and computes `$11 / price` for a symbol-aware buffer above the $10 floor.

**Bug #3 — Dead code with wrong parameter name.** Found that `pipeline.run_cycle()` line 324 was passing `market_data=` to `strategy.generate_signals()`, but the base class signature expects `bars=`. Any strategy called via this path would raise TypeError. Turned out to be dead code — the production scheduler only calls `run_tier()` which uses the correct `bars=` parameter. Fixed anyway for correctness.

**Bug #4 — Prediction strategies fundamentally starved.** 5 strategies across 1,449+ scheduler cycles, zero trades. Ever. Investigated each strategy's signal generation pipeline and found two categories of issue:

- **Thresholds too high for Kalshi's liquidity reality.** The value_pricing and market_skimmer min_volume/min_open_interest thresholds (100/50 and 50/25 respectively) were set for idealized markets. Real Kalshi crypto markets have spottier liquidity. Lowered across the board: value_pricing to 20/10, skimmer to 10/5. Also lowered min_edge (value_pricing 0.05→0.03, skimmer 0.03→0.02), crypto_probability's min_edge_pp (8.0→5.0), and event_catalyst's min_edge_pp (6.0→4.0).

- **news_driven was fundamentally broken.** The strategy required `prev_yes_close` and `avg_daily_volume` fields that Kalshi's `/markets` endpoint simply doesn't provide. Every single market returned None at line 171 (`if prev_price > 0 ... else return None`). The strategy had never been able to fire against a real Kalshi response. Fix: added a fallback that uses the bid/ask midpoint vs implied fair (derived from `no_ask`) as a price-move proxy, and treats high absolute volume (500+ contracts) as a standalone activity signal when `avg_volume` is missing. This turns it from a broken strategy into one that can actually trigger on live data.

**The USDC detour.** Jay topped up Coinbase with $100 to unstick the shadow executor (the existing ~$10 balance couldn't cover even a single $11 min order). But the deposit landed as USDC, not USD — and the Sentinel strategies all use `-USD` trading pairs which require USD. A quick Convert → USD on the Coinbase app resolved it.

**Pre-existing test fixes.** While updating test assertions for the new thresholds, discovered the two "pre-existing test failures" from the previous session's TODO: `test_crypto_probability.py` had `CLOSE_TIME = "2026-04-01T23:59:59Z"` hardcoded in the past. The strategy's `_hours_to_expiry` filter was rejecting every test market with `min_hours_to_expiry: 6`, causing edge calculations to never run. Fixed by computing `CLOSE_TIME = now + 3 days`. Test suite: 396 → 405 passing.

**Lessons from this phase:**
- **Dashboards hide causation.** "93 trades, 0 winners" looks like a strategy problem. It was actually four independent infrastructure problems, none of which were about whether the strategies pick good trades.
- **Hardcoded constants are time bombs in multi-symbol systems.** Shadow mode's `0.00012` min size worked fine when we only traded BTC. The bug was dormant until breakout_crypto finally fired on ETH and AVAX — and even then, silently, until the divergence counter was checked.
- **Error handling that swallows context is worse than no error handling.** PositionManager's `return None` on missing price was defensive, but it hid the fact that the most important exit rule (max hold) was being skipped 100% of the time for the stuck positions.
- **Test data in the past is a landmine.** `CLOSE_TIME = "2026-04-01T23:59:59Z"` passed when written. Six months later, it silently broke unrelated tests by turning every test market into an expired contract.

---

## Phase 11: Five Silent Failures and the Hobby Plan Ambush (May 3, 2026)

**The setup:** Three weeks unattended. Jay opened the session expecting "let's review what the system did." First curl returned `404 Application not found` from Railway with a `x-railway-fallback: true` header — the production deploy was simply gone, with no error, no email, no alert. Cause: the Railway free trial expired silently around April 22 and de-routed the URL. Jay signed up for Hobby, build came back, and *then* the real archaeology began: the system had been broken in five separate ways, four of which were invisible until the production app was alive again to expose them.

**Failure #1 — Kalshi DNS typo, 32 days dead.** `KALSHI_BASE_URL` had been set to `https://trading-api.kalshi.co`. The correct prod TLD was `.com`; `.co` is the demo's TLD. The hostname returned NXDOMAIN, the adapter raised at startup, the registration code logged `"Kalshi adapter failed to connect — not registered"` at WARNING level — and that single line had been scrolling in the logs since April 4 with nobody watching. All five prediction strategies had been silently no-op'd for 32 days because the API client never even instantiated.

**Failure #2 — Coinbase 350-candle hard limit.** Every 15 minutes since at least Apr 22, Coinbase had been returning `HTTP 400: number of candles requested should be less than 350`. The 4h-aggregation path in `pipeline.py` multiplies `DEFAULT_BARS_LIMIT (100) × factor (4) = 400` 1h candles to compose 100 4h bars. Coinbase had either tightened the limit during our downtime or always enforced it — either way, `trend_crypto` (the only 4h-timeframe crypto strategy) had been silently getting `[]` from the adapter and producing nothing. Fix was a one-line clamp at the adapter boundary: `limit = min(limit, 349)`. The arithmetic of the bug is constant whether we're online for 1 day or 100 — billing being off didn't cause it, it just hid the symptoms.

**Failure #3 — Kalshi creds were the demo set.** Once the DNS fix landed, Kalshi returned `401 Unauthorized` on `/portfolio/balance`. The keys in Railway were demo-environment keys that had been sitting there since before the Apr 4 "switch to live" — a switch that never actually reached Kalshi (because of the URL typo). Jay generated fresh prod keys; we saved them to a new gitignored `.env.prod` file (with a `.gitignore` tightening to cover `.env.*` going forward). Verified the creds against the prod URL with a one-shot Python probe: still 401.

**Failure #4 — Kalshi's entire prod API has moved.** The 401 with the new prod creds wasn't an auth failure at all. The response body read:

> `API has been moved to https://api.elections.kalshi.com/ Please check our docs on how to migrate.`

Both authenticated AND public endpoints on `trading-api.kalshi.com` returned this exact string with HTTP 401. Kalshi had migrated their entire API to a new domain (presumably alongside their reorganization around election markets) and chose to communicate this via a misleading 401 status code on a dead host. Updated the env var. Local probe finally got a 200 back: `{"balance": 1000, "portfolio_value": 0}`. $10.00 funded.

**Failure #5 — Kalshi's response schema also changed.** With the new URL working, predictions cycles ran but every single market was still being skipped. KCS-02 reported `{missing_fields: 50}` (100% of scanned markets) and value/skimmer reported `{low_volume: 50}` (100%). The bytes were arriving from Kalshi correctly — the adapter was reading the wrong field names. Old API: `yes_bid` (cents int), `volume` (int), `strike_price`. New API: `yes_bid_dollars` (string `"0.8400"`), `volume_fp` (string `"0.00"`), `floor_strike` (number). Every numeric field was silently falling through to `0`, which made every market look thin AND missing-strike. A `_to_float` helper plus six field renames across `get_quote`, `get_markets`, and `get_crypto_markets` — verified end-to-end with a local probe before pushing. After deploy: KCS-02's skip breakdown went from `{missing_fields: 50}` to `{low_volume: 48, missing_fields: 1, no_edge: 1}`. The remaining `low_volume` is real — election markets are genuinely thin per-contract — and `missing_fields: 1` is an outlier ticker (`KXBTC-...-T68250`) that has no `floor_strike` for unclear reasons.

**The Polymarket detour.** Mid-session Jay asked about adding Polymarket back into Sentinel — Polymarket relaunched in the US in December 2025 via the QCX acquisition, and is now a CFTC-regulated DCM. NY isn't in the prohibited-states list (AZ, IL, MA, MD, MI, MT, NJ, NV, OH are). Started brainstorming the integration. The keys Jay had matched the international/legacy CLOB shape (UUID apiKey + `0x`-prefixed Polygon wallet private key) — but the international path is wallet-based and gray-zone for NY residents. Confirmed Jay is on the waitlist for the US-regulated path. Paused the work cleanly, saved his international keys to `.env.prod` with explicit "INCOMPLETE — won't be used" notation, captured the architectural finding in TODO.md.

**The architectural finding worth remembering:** While exploring how a second prediction-market adapter would integrate, found that `Executor.register_adapter` (`engines/execution/base.py:112-114`) keys adapters by `AssetClass` only. Adding Polymarket as a second `PREDICTIONS` adapter would silently overwrite Kalshi. The Phase-1 Polymarket build will need a multi-adapter routing fix (`(asset_class, platform_name)` keying + a `platform` field on Signal so strategies can target a specific venue) before it can land — captured in TODO so the next session doesn't have to rediscover it.

**The shape of the day:** Failure #1 revealed Failure #3, which revealed Failure #4, which revealed Failure #5. Each fix unblocked the *next* layer of the broken pipeline. The 4-bug audit on April 10 was at least four parallel bugs that we could see all at once; this session was a serial chain where each fix was a prerequisite for finding the next problem.

**Lessons from this phase:**
- **Silent failures cluster.** Five separate bugs had been hiding in the system, but the trial outage made them visible all at once because the restart forced reauthentication / refetching / reparsing across every code path. Long uptime is bad for observability; restarts surface latent breakage.
- **Misleading status codes from upstream APIs are particularly dangerous.** Kalshi returning 401 to mean "we moved" cost us hours of credential-debugging suspicion. The failure message in the body told us instantly — but we wouldn't have seen it without dropping into a Python probe to dump the response.
- **Field-name drift in third-party APIs is silent and total.** The schema change wouldn't have caused any test to fail (we mock with our own payloads). The strategies didn't crash; they just saw zeros everywhere and decided nothing was actionable. Worth running an integration test against the real upstream periodically — not just unit tests against our own mocks.
- **Hobby plan billing alerts are mandatory infrastructure, not nice-to-have.** Open item in TODO.md to set up Railway usage alerts so this can't recur silently.
- **"Demo → live" is one of the most error-prone transitions in trading systems.** Three of today's five failures (#1, #3, #4) all trace back to the original demo→live attempt on April 4: typo in URL, demo creds left in place, and an underlying API migration on the live side that we didn't know about. The "switch to live" was treated as a one-line config change; it's actually a verification campaign.

---

## Phase 12: The Logging That Hid Six Bugs (May 6, 2026)

**The setup:** Three days after the May 3 redeploy, a casual "let's check how the project is going" turned into a triage of the entire production stack. The /health endpoint reported all five components healthy and 15 strategies registered with non-zero cycle counts. But shadow_stats said `live_executed: 1, live_failed: 22, fill_rate_match: 4.3%, max_price_divergence_pct: 100.0`. The DB had 23 trades since the redeploy, all `paper_*`. Predictions cycled cleanly. Crypto cycled cleanly. Equities? Zero signals across 7 strategies × 3 days.

**The triage tactic that worked:** Pulled 5,000 lines of Railway deploy logs into JSONL. 4,994 of 5,000 were `error` level — Kalshi 429s, hundreds per minute. Six lines weren't 429s. Those six lines told the entire story.

**Bug #1 — Equity silence, root cause A: `TimeFrame(N, "Min")` enum mismatch.** alpaca-py 0.43.x's `TimeFrame.__init__` calls `validate_timeframe(amount, unit)` which compares `unit == TimeFrameUnit.Minute`. When `unit` is the string `"Min"`, none of the equality checks fire (silently passes), so the constructor returns successfully with `unit_value="Min"` (a str). The bug is dormant until alpaca-py serializes the request URL via `tf.value` → `f"{self.amount}{self.unit.value}"` → `"Min".value` → `AttributeError: 'str' object has no attribute 'value'`. Reproduced in five lines of REPL. The pattern — `TimeFrame(5, "Min")`, `TimeFrame(15, "Min")`, `TimeFrame(4, "Hour")` — had been in `engines/execution/alpaca.py` since commit `cbc509d` (the one that added 4Hour support). 210 of 225 bar-fetch failures in an 11-hour log window traced to this.

**Bug #2 — Equity silence, root cause B: missing `feed="iex"`.** The 15 *other* failures had a different message: `subscription does not permit querying recent SIP data`. Alpaca's `StockBarsRequest` defaults to the SIP (paid) feed; free-tier accounts have to explicitly request `feed="iex"`. The `1Day` codepath (sniper hourly cycles) used `TimeFrame.Day` — a properly-constructed enum — so it actually reached the API and got a clean 403-style rejection. Two distinct bugs hiding in the same function, surfacing as two error messages, both invisible because of...

**Bug #3 — The logging config that wasn't there.** Python's default root logger level is WARNING. `api/main.py` had no `logging.basicConfig` call. Every `logger.info(...)` in the entire pipeline — `Trade EXECUTED`, `Coinbase order submitted`, `Kalshi order submitted`, `Shadow mode ENABLED`, `Price refreshed`, `Fetched N bars for SPY` — was silently dropped at the root level for the entire deployment lifetime. We had been operating effectively blind on the success path. The 429 storm filled the WARNING/ERROR slots; nothing else got through.

**Bug #4 — The Kalshi 429 storm was redundant work, not API saturation.** The pipeline's `_fetch_prediction_data` called `get_markets(limit=N)` and then ran a per-market loop calling `get_quote(ticker)` "to enrich" each entry. Reading the two methods side by side: `get_markets` already returned `yes_bid`, `no_bid`, `yes_ask`, `no_ask`, `volume`, `open_interest`, and `status`. `get_quote` returned exactly the same fields with one tiny rename (`yes_price` instead of `yes_bid`) — and the strategies that consumed the markets already handled both names via fallback. The enrichment loop was 100% redundant. value_kalshi/skimmer_kalshi ran with `scan_limit=50/100` which meant 50–100 sequential extra GETs per cycle, every 10 minutes — exactly the 429 cadence. Fix was deletion, not throttling: one less code path, no semaphore, no batching. The strategies still see the same data.

**Bug #5 — `insert_portfolio_snapshot` was dead code.** `/portfolio` had been returning `"No portfolio snapshots yet"` for the entire deployment. Grepped for the writer function; found one definition in `data/repositories/portfolio.py` and *zero callers anywhere in the codebase*. Not a runtime failure — the function had simply never been wired up. Added a 5-minute APScheduler `IntervalTrigger` job calling `_persist_portfolio_snapshot` on the existing executor.

**Bug #6 — Shadow live fills, post-deploy hit a different wall.** Pre-fix shadow_stats: 22/23 signals failed on the live side (paper succeeded for all 23, that's how we have a trades table at all). Predictions *should* be routing through Kalshi `observe_only=True` → `_simulate_fill()` → `executed=True`, and 8 of the 23 signals are predictions, so the math says `live_executed >= 9`. But it was 1. After the 5/6 deploy: confirmed via `INFO api.main: Kalshi adapter connected and registered (observe-only)` that observe_only IS True at runtime — so the contradiction stands. Couldn't observe a fresh signal flow through because every existing crypto/predictions strategy was on cooldown for 1-4 hours after the redeploy (4h cooldown windows on 5/6's prior trades). Pushed a follow-up commit (b516248) adding `Shadow live OK / Shadow live FAILED ... error=...` log lines at the live-result boundary in `execute_shadow`, so the next non-cooldown signal will surface the exact failure path. Marked open; resolution waits on overnight signal flow.

**Adjacent finding worth its own bug** — While reading the prediction-strategy code paths, noticed that `engines/strategy/predictions/crypto_probability.py:320-324` emits `Side.SELL` for "BUY_NO" opportunities (BUY the NO contract when model says YES is overpriced). But `engines/pipeline.py:822-824` interprets any `Side.SELL` as "close an existing position" and routes it to `_handle_sell_signal`, which finds no matching position and returns None at DEBUG level. So every KCS-02/KCS-05 BUY_NO signal is silently dropped before reaching the executor. Watched it happen live on the 5/6 fresh container: KCS-02 generated 3 signals on cycle #1 (1 BUY_YES + 2 BUY_NO), and only the BUY_YES one made it to the cooldown check — the two BUY_NOs vanished without trace. The fix isn't trivial: prediction markets need a richer signal vocabulary than `Side.BUY/SELL` (BUY_NO ≠ close-existing-YES-position), and the Kalshi adapter currently hardcodes `side="yes"` so even if the SELL routed through, it'd be wrong semantically. Tracked separately in TODO.md; not addressed in this session.

**Real money tracking shipped.** Triggered by Jay reading the first post-deploy Discord risk alert ("Portfolio Value $100,132.08") and asking why we were calling out the paper portfolio value. Pulled `/portfolio` to see the breakdown: $100,113 cash (Alpaca paper $100k starting balance) + $18 BTC/ETH (Coinbase real positions) + $10 Kalshi balance, all summed into a single `total_value`. The Discord alert wasn't *wrong*; it was just dominated by paper capital, masking real-money exposure at-a-glance. Added an `is_paper` class attribute and `real_money_value()` method to each adapter (Alpaca flips on `'paper'` substring in URL, Coinbase/Kalshi explicitly real, Kalshi's observe-only doesn't count as paper because the $10 balance is real money — observe_only just blocks orders). `Executor.get_portfolio_snapshot` aggregates each adapter's contribution into a new `real_money_total` field on `PortfolioSnapshot`. Migration `c3d4e5f6a7b8` adds the column; the `/portfolio` endpoint exposes it; Discord alerts gain a "Real Money" field next to "Portfolio Value". Verified live: total $100,132.08 = $99,999.69 paper + $132.39 real ($103 Coinbase USD top-up + $18 BTC/ETH + $10 Kalshi). Test count: 414 → 420.

**Session end.** Bug #6's diagnostic logging is in place; cooldowns blocked observation tonight but the next non-cooldown signal will surface the failure path. Next session's focus is wiring Jay's Alpaca live account alongside the paper one — which will require the multi-adapter routing fix (`Executor.register_adapter` currently keys by `AssetClass` only, so a second EQUITIES adapter would silently overwrite paper). The same fix would unblock Polymarket as a second `PREDICTIONS` adapter, but Polymarket stays paused — Jay is still on the US waitlist and we shouldn't tackle it. Polymarket-international is wallet-based and gray-zone for NY residents; not worth the regulatory exposure for an experimental project.

**The TDD discipline:** Each fix went through a strict RED → GREEN cycle. New test files: `tests/unit/test_alpaca_adapter.py` (timeframe map serialization × 6, `feed="iex"` × 1), `tests/unit/test_prediction_fetch.py` (assert `get_quote` is *not* called per market), `tests/unit/test_snapshot_persistence.py` (assert the scheduler method calls the repository function). Every test failed once for the right reason — `_ALPACA_TIMEFRAME_MAP` doesn't exist, `get_quote was called 2 times`, `insert_portfolio_snapshot is not in engines.scheduler` — before turning green. Test count: 405 → 414 (+9 net new). Zero regressions.

**Lessons from this phase:**
- **Logs at WARNING level are like having one eye closed.** Three days of zero equity signals plus 22/23 shadow live failures plus an unwritten snapshot table — and from `/health` it all looked fine because the 5 components were "healthy" and the cycle counters were ticking. The success-path logs that would have flagged "trade submitted but didn't fill" never reached Railway. The triage took an hour; the missing visibility had been hiding the bugs for three weeks.
- **Permissive-by-default third-party SDKs are landmines.** alpaca-py's `validate_timeframe` checks `unit == TimeFrameUnit.X` for known units and silently accepts anything else. Strict validation would have raised on `TimeFrame(5, "Min")` immediately at construction time; the lenient version converts a typo into a deferred AttributeError at request-build time. The strictness should have been on the framework side, not on us reading the docs.
- **Redundant-work bugs are easier to find than rate-limit-saturation bugs.** When 100% of the 11-hour log window is one error message, the natural assumption is "we're hitting Kalshi's rate limit." But the question to ask is *why* are we calling so often. Reading `get_markets` and `get_quote` next to each other revealed they returned the same data — the loop wasn't enriching anything. A throttle would have hidden this; deletion exposed it.
- **"Defined but not called" is a category of bug worth grepping for periodically.** Five-minute audit: `git grep -l 'def insert_'` then for each repository function, `git grep -l '<func_name>'` to see if it's called. Fast, mechanical, surprisingly productive on systems that grew via incremental scaffolding.
- **Test-first works even under triage pressure.** Reading the tests that came out of this session, each one names *exactly* the bug it's catching. `test_timeframe_map_serializes_each_key` is a future regression catcher: if anyone ever puts a string in `_ALPACA_TIMEFRAME_MAP` again, it'll fail at CI, not in production after 22 days of silent equity dormancy.

---

## Running Themes

- **Cost obsession:** Everything is designed to stay under $30/mo. Haiku for routine calls, Sonnet only for strategy generation, prompt caching, no Kubernetes.
- **Safety-first architecture:** Risk Engine is the safety net. It gets the most tests, the most scrutiny, and can never be bypassed by the Strategy Engine.
- **Debug inputs before tuning outputs:** The signal drought taught us to verify the data pipeline is connected before assuming the processing logic is wrong.
- **Shadow mode as training wheels:** Real trades at minimum size build confidence in the system before scaling up.
- **Single-person ops:** Discord alerts instead of PagerDuty, Railway instead of AWS, SQLite for backtesting — complexity is the enemy.
