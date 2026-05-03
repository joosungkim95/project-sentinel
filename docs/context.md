# Sentinel — Operational Context

> **This file is the single source of truth for remote Claude sessions (dispatch/cowork).**
> It mirrors the local memory system. Updated at the end of every session.
>
> Last updated: 2026-04-10

---

## About Jay (User)

Jay is building Sentinel as a personal project. Experienced developer comfortable with Python, FastAPI, SQLAlchemy, Docker. Building across US equities (Alpaca), crypto (Coinbase), and prediction markets (Kalshi only).

- Based in New York — cannot use Polymarket (NY residents restricted). Kalshi is the only prediction market platform.
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

**Current state (2026-04-10):**
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
| Kalshi | Connected on Railway | Live (observe-only) | `KALSHI_BASE_URL=https://trading-api.kalshi.com`, `KALSHI_OBSERVE_ONLY=true` (switched from demo Apr 4) |
| Polymarket | Blocked | N/A | US trading restricted |

**Shadow mode:** All trades execute at minimum size (1 share / 0.00012 BTC / 1 contract) on real platforms, full-size paper simulations tracked in parallel.

**To switch to live:** Alpaca → change ALPACA_BASE_URL to `https://api.alpaca.markets`. Kalshi → change KALSHI_BASE_URL to `https://trading-api.kalshi.com`. Only after 2+ weeks clean shadow mode.

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

Four-bug fix deployed 2026-04-10. Review after next few cycles:

1. **Stuck positions closing?** — 20 vol_harvest BTC-USD positions opened Apr 2 at $87,609 should auto-close via max_hold_time (7d) on the next sniper_crypto cycle (runs hourly). Check /trades — these should move from open to closed with pnl populated. Discord will alert with batched exit summary.
2. **Shadow live fills working?** — Coinbase now has $100 USD (converted from USDC). Next breakout_crypto or any crypto signal should produce a successful live fill at ~$11 symbol-aware min size. Check /shadow — `live_executed` should increment, `fill_rate_match` should rise above 0.
3. **Prediction strategies firing?** — Lowered thresholds across all 5 prediction strategies. Check Railway logs for WARNING-level skip reasons (should see fewer low_volume/low_oi/no_edge skips) and /trades for any prediction-asset-class trades. news_driven should now work on live Kalshi data (fallback logic handles missing prev_price/avg_volume).
4. **KCS-05 catalyst window?** — CPI Apr 14 is 4 days out (Apr 10). 5-day pre-positioning window opened Apr 9. Should start generating event_catalyst signals with the lowered 4pp edge threshold.
5. **Vol harvest trend filter holding?** — Confirmed working Apr 5: 0 BUY signals in high_volatility regime. Should stay quiet until regime shifts to ranging/trending_up.
6. **Cooldown enforcement?** — Sniper 24h DB-backed cooldown deployed Apr 5. Testable when a new vol_harvest signal finally fires post-regime-shift.
