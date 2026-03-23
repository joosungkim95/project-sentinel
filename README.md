# Sentinel

Autonomous trading platform for US equities, crypto, and prediction markets.

## Quick Start

```bash
# 1. Copy environment template
cp .env.example .env
# Fill in your API keys

# 2. Start services
docker compose up -d

# 3. Run tests
pip install -e ".[dev]"
pytest tests/ -v

# 4. Start the app
uvicorn api.main:app --reload
```

## Architecture

Four engines in a strict hierarchy:

| Engine | Role | Analogy |
|--------|------|---------|
| Risk Engine | Absolute veto authority | The brake |
| Strategy Engine | Maximize returns within risk budget | The gas pedal |
| Execution Engine | Place and manage trades | The hands |
| Learning Engine | Improve over time | The feedback loop |

See [CLAUDE.md](CLAUDE.md) for the full development guide.

## Project Structure

```
sentinel/
├── engines/          # The four trading engines
│   ├── risk/         # Risk Engine (independent process)
│   ├── strategy/     # Strategy Engine + all strategies
│   ├── execution/    # Platform adapters (Alpaca, Coinbase, etc.)
│   └── learning/     # Performance evaluation + optimization
├── memory/           # Context management for stateful decisions
├── data/             # Market data providers + DB models
├── api/              # FastAPI endpoints + dashboard API
├── config/           # Risk params, cost controls, strategy configs
├── tests/            # Unit + integration tests
└── backtesting/      # Backtesting framework
```

## Supported Platforms

- **Alpaca** — US equities & ETFs (paper + live)
- **Coinbase** — Cryptocurrency (sandbox + live)
- **Polymarket** — Prediction markets
- **Kalshi** — Prediction markets (US-regulated)
