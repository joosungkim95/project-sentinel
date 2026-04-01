# Sentinel — Operational Context

> **This file is the single source of truth for remote Claude sessions (dispatch/cowork).**
> It mirrors the local memory system. Updated at the end of every session.
>
> Last updated: 2026-04-01

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

**Current state (2026-04-01):**
- 15 tiered strategies active (v5): 4 scouts, 7 core, 4 snipers
- Confidence gates: scout 0.2, core 0.4, sniper 0.7
- Market regime classifier: SMA slope + ATR ratio
- Shadow mode ON
- All 3 platform adapters connected (Alpaca, Coinbase, Kalshi)
- P&L/drawdown enrichment live — HardFloor, DailyLoss, WeeklyDrawdown safety rules now read real values
- Repo is public (no secrets committed)
- Alembic migration a1b2c3d4e5f6 pending auto-deploy (adds pnl/drawdown columns to portfolio_snapshots)

**Env vars on Railway:** ALPACA_API_KEY, ALPACA_SECRET_KEY, COINBASE_API_KEY, COINBASE_API_SECRET, KALSHI_API_KEY, KALSHI_BASE_URL, KALSHI_PRIVATE_KEY, DATABASE_URL, REDIS_URL, DISCORD_WEBHOOK_URL, SHADOW_MODE, ANTHROPIC_API_KEY

**Deploy gotcha:** Railway aggressively caches Docker layers. If pip install or COPY layers show "cached" when they shouldn't, change the Dockerfile comment near that line to bust the cache.

---

## Platform Status

| Platform | Status | Mode | Notes |
|----------|--------|------|-------|
| Alpaca | Connected on Railway | Paper trading | `ALPACA_BASE_URL=https://paper-api.alpaca.markets` |
| Coinbase | Connected on Railway | Real money (shadow min-size ~$4) | COINBASE_API_SECRET PEM added via Railway Variables |
| Kalshi | Connected on Railway | Demo | `KALSHI_BASE_URL=https://demo-api.kalshi.co` |
| Polymarket | Blocked | N/A | US trading restricted |

**Shadow mode:** All trades execute at minimum size (1 share / 0.0001 BTC / 1 contract) on real platforms, full-size paper simulations tracked in parallel.

**To switch to live:** Alpaca → change ALPACA_BASE_URL to `https://api.alpaca.markets`. Kalshi → change KALSHI_BASE_URL to `https://trading-api.kalshi.co`. Only after 2+ weeks clean shadow mode.

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

Adapter connect fix deployed 2026-03-30. P&L enrichment deployed 2026-04-01. Review after ~24h runtime:

1. **Data feeds working?** — No `Failed to get candles/bars` errors in Railway logs
2. **Signals generating?** — GET /health → shadow_stats.total_signals > 0
3. **Confidence distribution?** — Check if signals cluster below gates vs passing through
4. **Regime classifier?** — Look for regime classifications in logs/market_regimes table
5. **KCS-05?** — NFP Apr 3 window open since Mar 29, look for signals
6. **P&L safety rules?** — Check that daily_pnl/weekly_pnl/drawdown_from_peak are non-zero in portfolio snapshots after trades close
