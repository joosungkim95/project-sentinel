# Sentinel Operations Runbook

Manual intervention procedures for common scenarios.
All commands assume you have Railway CLI access and the dashboard open.

---

## Emergency Procedures

### 1. Emergency Stop (all trading halted)

**When:** Something is seriously wrong — unexpected losses, platform issues, system errors.

**Via Dashboard:**
- Click the red "Emergency Stop" button in the header

**Via API:**
```bash
curl -X POST https://sentinel-production-c4dd.up.railway.app/emergency-stop
```

**What it does:**
- Pauses all scheduler jobs (equities, crypto, predictions)
- Activates the Risk Engine circuit breaker (24h)
- Logs and sends Discord alert

**To resume after investigation:**
```bash
# Resume individual asset classes
curl -X POST .../scheduler/resume/equities
curl -X POST .../scheduler/resume/crypto
curl -X POST .../scheduler/resume/predictions
```

Note: The circuit breaker auto-expires after 24 hours. It cannot be manually cleared via API (by design — prevents accidental re-enabling).

---

### 2. Single Platform Down

**When:** One trading platform (Alpaca, Coinbase, Kalshi) is unreachable.

**Diagnosis:**
```bash
curl https://sentinel-production-c4dd.up.railway.app/system-health
```
Check `health.components` for which platform shows `"status": "down"`.

**Action:**
- The system auto-degrades — other platforms keep trading
- The HealthMonitor uses exponential backoff to retry
- No manual action needed unless it persists > 1 hour

**If persistent:**
1. Check the platform's status page (Alpaca, Coinbase, Kalshi)
2. Check Railway logs: `railway logs --tail 50`
3. If API credentials expired, update in Railway dashboard → Variables
4. Redeploy: `railway up` or push to GitHub (auto-deploys)

---

### 3. Database Connection Lost

**When:** PostgreSQL is unreachable.

**Diagnosis:**
```bash
curl .../system-health  # Check database component
curl .../health         # Check connections.database
```

**Impact:**
- Trades cannot be persisted (but can still execute via adapters)
- Dashboard shows stale data
- Learning engine cannot run

**Action:**
1. Check Railway PostgreSQL service status
2. Railway dashboard → PostgreSQL → check if instance is running
3. If Railway maintenance, wait for it to complete
4. If disk full, Railway auto-scales — check billing limits
5. After recovery, the system auto-reconnects (SQLAlchemy connection pool)

---

### 4. Circuit Breaker Triggered

**When:** Daily loss exceeds 3% or hard floor (10% drawdown) is breached.

**Diagnosis:**
```bash
curl .../system-health  # Check risk_engine.circuit_breaker_active
curl .../risk-events?limit=5  # See what triggered it
```

**Action:**
1. **Do NOT immediately resume** — investigate first
2. Review recent trades: `curl .../trades?limit=20`
3. Check if a strategy is malfunctioning
4. Check market conditions (flash crash? sector selloff?)
5. If a strategy bug: pause that asset class, fix, redeploy
6. Circuit breaker auto-expires in 24h — if safe to resume earlier, wait for expiry or redeploy

---

## Routine Operations

### 5. Pause/Resume an Asset Class

**Pause equities (e.g., before a known event):**
```bash
curl -X POST .../scheduler/pause/equities
```

**Resume:**
```bash
curl -X POST .../scheduler/resume/equities
```

Asset classes: `equities`, `crypto`, `predictions`

---

### 6. Check Strategy Performance

```bash
curl ".../performance?strategy_id=sma_crossover_spy&limit=7"
```

Look for:
- `win_rate` < 0.4 sustained → consider disabling
- `sharpe_ratio` < 0.5 → not worth the risk
- `max_drawdown` > 5% → too volatile

---

### 7. View Learning Engine Status

```bash
curl .../learning
```

Shows:
- Whether learning loops are enabled
- Fast loop schedule (daily 5pm ET)
- Slow loop schedule (Sunday 8pm ET)

The slow loop calls Claude API — check `ANTHROPIC_API_KEY` is set in Railway if it's failing.

---

### 8. Deploy a New Version

Sentinel auto-deploys from GitHub `main` branch.

```bash
git add -A && git commit -m "[fix] description"
git push origin main
# Railway auto-deploys — check dashboard for build status
```

For manual deploy:
```bash
railway up
```

---

## Monitoring Checklist

### Daily (automated by fast loop)
- [ ] Daily P&L within normal range
- [ ] No circuit breaker activations
- [ ] All platforms connected
- [ ] Discord daily summary received

### Weekly (automated by slow loop)
- [ ] Weekly review completed (check Discord)
- [ ] Strategy recommendations reviewed
- [ ] No strategies stuck in paper_testing > 30 days
- [ ] Monthly API cost under $15

### Monthly
- [ ] Review strategy graveyard — any worth resurrecting?
- [ ] Check Railway billing
- [ ] Update API credentials if expiring
- [ ] Run stress tests: `python -c "from engines.risk.stress_test import StressTestRunner; print(StressTestRunner().summary(StressTestRunner().run_all()))"`

---

## Shadow Mode Operations

### Enable Shadow Mode

Shadow mode runs live trades at minimum size alongside paper trades.

Currently configured in code — to enable:
1. Replace `Executor` with `ShadowExecutor` in `api/main.py`
2. Set min trade sizes per asset class
3. Monitor via `/system-health` for divergence stats

### Divergence Thresholds
- **Price divergence > 2%**: Live trading auto-pauses
- **Fill rate match < 80%**: Investigate platform issues
- **> 10 divergences**: Review and reset stats

### Promote to Full Live
Requirements before removing shadow mode:
1. Shadow mode running > 2 weeks
2. Max price divergence < 1%
3. Fill rate match > 95%
4. No auto-pause events
5. Stress tests all passing

---

## Key Environment Variables

| Variable | Where | Purpose |
|----------|-------|---------|
| `ALPACA_API_KEY` | Railway | Equities trading |
| `ALPACA_SECRET_KEY` | Railway | Equities trading |
| `COINBASE_API_KEY` | Railway | Crypto trading |
| `COINBASE_API_SECRET` | Railway | Crypto trading |
| `KALSHI_API_KEY` | Railway | Prediction markets |
| `KALSHI_PRIVATE_KEY` | Railway | Kalshi RSA auth (PEM) |
| `ANTHROPIC_API_KEY` | Railway | Learning Engine (Claude) |
| `DATABASE_URL` | Railway (auto) | PostgreSQL connection |
| `REDIS_URL` | Railway (auto) | Cache |
| `DISCORD_WEBHOOK_URL` | Railway | Alert notifications |

---

## Escalation

If you can't resolve an issue:
1. Emergency stop first
2. Check Railway logs
3. Check Discord for recent alerts
4. Review this runbook
5. If data integrity is at risk, stop the Railway service entirely from the dashboard
