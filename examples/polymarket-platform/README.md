# Polymarket Trading Platform

Modular Polymarket trading platform. The threshold strategy is the default plugin; the execution, risk, and feed layers are strategy-agnostic.

## Why not just a threshold bot?

A threshold strategy on public data has no information edge — it pays spread + fees on every trade. Every order must pass a **profitability gate**: expected edge (bps) > taker fees + spread buffer. If it doesn't, the order is skipped and logged.

## Architecture

```
Feed (WS primary, REST fallback)
  └─► asyncio.Queue[Quote]
        └─► TradingEngine._process_quote()
              ├─ StrategyPlugin.decide()      # swap without touching engine
              ├─ ProfitabilityGate.check()    # fee-aware; blocks sub-threshold trades
              ├─ RiskManager.allow()          # limits, cooldown; state persisted in SQLite
              ├─ CircuitBreaker.check()       # consec losses, drawdown, feed lag, latency
              └─ OrderManager.execute()       # dedupe, quantize, dry-run / live
```

**Key design decisions:**
- `ClobExecutor` bridges sync `py-clob-client` via `ThreadPoolExecutor(max_workers=1)` — serial without blocking the event loop
- Signal handling via `loop.add_signal_handler()` — asyncio-safe
- Risk state persisted as a SQLite singleton row — survives restarts
- Schema versioned with integer migrations — safe to extend
- All closures use explicit default-argument capture — no late-binding bugs

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -U pip
pip install -e ".[dev]"
cp .env.example .env
# Edit .env — at minimum set POLY_TOKEN_ID
```

## Usage

```bash
# Dry run (no credentials needed)
polymarket run

# Live
BOT_DRY_RUN=false polymarket run

# Cancel all open orders
polymarket cancel-all

# Status report (reads SQLite)
polymarket status

# Kill switch (from another terminal)
touch ./KILL_SWITCH
```

## Implementing a custom strategy

```python
from polymarket_platform.strategy.base import StrategyPlugin, MarketSnapshot, Decision

class MyStrategy(StrategyPlugin):
    @property
    def name(self) -> str:
        return "my-strategy"

    def decide(self, snap: MarketSnapshot) -> Decision:
        # Your logic here; return Decision with expected_edge_bps
        ...
```

Pass your instance to `TradingEngine(strategy=MyStrategy(...), ...)`.

## Tests

```bash
pytest                    # all tests
pytest tests/unit/        # unit only
pytest tests/integration/ # integration (mocked CLOB + feed)
```

## SQLite schema (v1)

| Table | Purpose |
|---|---|
| `quotes` | Every price tick (bid/ask/source/ts) — replay-ready |
| `decisions` | Strategy output + gate/risk block reason per quote |
| `orders` | Execution record with fill price, amount, fee |
| `pnl` | Realized PnL per fill |
| `risk_state` | Singleton row; persisted cumulative risk counters |
| `circuit_breaker_events` | Trip/reset audit log |
| `heartbeats` | Heartbeat responses |
| `schema_version` | Migration version |

## Live readiness checklist

- [ ] `POLY_TOKEN_ID` set to a live CLOB token (`enableOrderBook: true` in Gamma API)
- [ ] `POLY_PRIVATE_KEY` + `POLY_FUNDER_ADDRESS` set
- [ ] `BOT_DRY_RUN=false`
- [ ] `FEE_TAKER_BPS` verified for your specific market (check Gamma API)
- [ ] `RISK_MAX_USD_SPEND` sized for your actual capital
- [ ] `MIN_EXPECTED_EDGE_BPS` > `FEE_TAKER_BPS` (otherwise every trade is negative EV)
- [ ] Test `polymarket cancel-all` works before going live
- [ ] Set `BOT_KILL_SWITCH_PATH` and test kill switch
- [ ] Run `pytest` — all tests pass
- [ ] `py-clob-client` version pinned exactly in lockfile
