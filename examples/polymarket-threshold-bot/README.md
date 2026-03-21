# Polymarket Threshold Bot (Python)

## What it does

- Fetches best bid/ask from Polymarket CLOB
- Reads your position from Data API
- Threshold strategy:
  - **BUY** if `ask <= STRAT_BUY_THRESHOLD`
  - **SELL** if `bid >= STRAT_SELL_THRESHOLD` (and you hold shares)
- Supports DRY-RUN, sqlite persistence, risk controls, retries

## Architecture

```
polymarket_bot/
├── config.py            # Pydantic-settings config from .env
├── logging_utils.py     # Plain text or JSON structured logging
├── utils.py             # quantize, with_retries, Cooldown, Backoff
├── storage.py           # SQLite: decisions, orders, heartbeats
├── polymarket/
│   ├── clob_client.py   # AsyncClob wrapping py-clob-client
│   └── data_api.py      # DataApiClient for positions
├── strategy/
│   ├── base.py          # MarketSnapshot, Decision, Strategy ABC
│   └── threshold.py     # ThresholdStrategy (buy/sell/hold)
├── risk/
│   └── risk_manager.py  # RiskManager (cooldown, spend, position, open orders)
├── execution/
│   └── order_manager.py # OrderManager (dedupe, quantize, dry-run/live)
└── main.py              # Async event loop, heartbeat, graceful shutdown
```

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e ".[dev]"
cp .env.example .env
# Fill .env (token id + wallet info).
```

## Run

**DRY-RUN** (no real orders sent):
```bash
BOT_DRY_RUN=true python -m polymarket_bot.main run
```

**LIVE**:
```bash
BOT_DRY_RUN=false python -m polymarket_bot.main run
```

**Cancel all open orders**:
```bash
python -m polymarket_bot.main cancel-all
```

**Kill switch**: create a file at `BOT_KILL_SWITCH_PATH` (default: `./KILL_SWITCH`). The bot will cancel orders and exit gracefully.

## Notes

- For BUY market orders: `amount` = USD you want to spend.
- For SELL market orders: `amount` = number of shares you want to sell.
- `STRAT_ORDER_TYPE=FAK` (Fill-And-Kill) is recommended for market orders; `FOK` (Fill-Or-Kill) is stricter.
- Heartbeat (`BOT_HEARTBEAT_ENABLED=true`) is only needed if you place resting (GTC) orders.

## Risk Controls

| Param | Description |
|---|---|
| `RISK_MAX_POSITION_SHARES` | Max total shares held |
| `RISK_MAX_USD_SPEND` | Max cumulative USD spent (per session) |
| `RISK_MIN_SECONDS_BETWEEN_ORDERS` | Cooldown between orders |
| `RISK_MAX_OPEN_ORDERS` | Max live open orders on the book |

## Tests

```bash
pytest
```
