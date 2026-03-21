from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Optional

import typer

app = typer.Typer(
    name="polymarket",
    help="Polymarket trading platform CLI",
    no_args_is_help=True,
)


@app.command()
def run(
    config: Optional[str] = typer.Option(None, "--config", "-c", help="Path to .env file"),
) -> None:
    """Start the trading engine (dry-run by default)."""
    import os

    from polymarket_platform.config import Settings
    from polymarket_platform.db.store import SqliteStore
    from polymarket_platform.engine import build_from_settings
    from polymarket_platform.logging_utils import configure_logging

    if config:
        os.environ.setdefault("DOTENV_PATH", config)

    cfg = Settings(_env_file=config) if config else Settings()  # type: ignore[call-arg]
    configure_logging(cfg.log_level, cfg.log_json)

    store = SqliteStore(cfg.sqlite_path)
    engine = build_from_settings(cfg, store)

    asyncio.run(engine.run())


@app.command(name="cancel-all")
def cancel_all() -> None:
    """Cancel all open orders (live mode only)."""
    from polymarket_platform.clob.client import ClobConfig, ClobExecutor
    from polymarket_platform.config import Settings
    from polymarket_platform.logging_utils import configure_logging

    cfg = Settings()
    configure_logging(cfg.log_level, cfg.log_json)

    if not cfg.private_key:
        typer.echo("Error: POLY_PRIVATE_KEY required to cancel orders", err=True)
        raise typer.Exit(1)

    async def _cancel() -> None:
        clob = ClobExecutor(
            ClobConfig(
                host=cfg.clob_host,
                chain_id=cfg.chain_id,
                private_key=cfg.private_key.get_secret_value(),  # type: ignore[union-attr]
                funder=cfg.funder_address,
                signature_type=cfg.signature_type,
                api_key=cfg.api_key,
                api_secret=(cfg.api_secret.get_secret_value() if cfg.api_secret else None),
                api_passphrase=(
                    cfg.api_passphrase.get_secret_value() if cfg.api_passphrase else None
                ),
            )
        )
        try:
            resp = await clob.cancel_all()
            typer.echo(f"cancel_all response: {resp}")
        finally:
            clob.shutdown()

    asyncio.run(_cancel())


@app.command()
def status() -> None:
    """Print a status report from the SQLite database."""
    from polymarket_platform.config import Settings
    from polymarket_platform.db.store import SqliteStore

    cfg = Settings()
    store = SqliteStore(cfg.sqlite_path)

    typer.echo(f"\n{'─'*60}")
    typer.echo(f"  Polymarket Platform — Status ({cfg.sqlite_path})")
    typer.echo(f"{'─'*60}")

    # Risk state
    rs = store.load_risk_state()
    typer.echo(f"\n[Risk State]")
    typer.echo(f"  USD spent (cumulative): ${rs.usd_spent:.2f}")
    import time
    last_order_age = time.time() - rs.last_order_ts if rs.last_order_ts > 0 else None
    if last_order_age is not None:
        typer.echo(f"  Last order: {last_order_age:.0f}s ago")
    else:
        typer.echo("  Last order: none")

    # Circuit breaker
    cb_event = store.get_last_circuit_breaker_event()
    typer.echo(f"\n[Circuit Breaker]")
    if cb_event:
        typer.echo(f"  Last event: {cb_event['reason']} (ts={cb_event['ts']:.0f})")
    else:
        typer.echo("  No events recorded")

    # PnL summary
    pnl = store.get_pnl_summary()
    typer.echo(f"\n[PnL Summary]")
    if pnl.get("total_fills"):
        typer.echo(f"  Total fills:   {pnl['total_fills']}")
        typer.echo(f"  Gross PnL:     ${pnl['gross_pnl_usd'] or 0:.4f}")
        typer.echo(f"  Total fees:    ${pnl['total_fees_usd'] or 0:.4f}")
        typer.echo(f"  Net PnL:       ${pnl['net_pnl_usd'] or 0:.4f}")
        typer.echo(f"  Win/Loss:      {pnl['wins']}/{pnl['losses']}")
        typer.echo(f"  Best trade:    ${pnl['best_trade_usd'] or 0:.4f}")
        typer.echo(f"  Worst trade:   ${pnl['worst_trade_usd'] or 0:.4f}")
    else:
        typer.echo("  No fills recorded yet")

    # Last decisions
    decisions = store.get_decisions(limit=5)
    typer.echo(f"\n[Last 5 Decisions]")
    if decisions:
        for d in decisions:
            import datetime
            ts = datetime.datetime.fromtimestamp(d["ts"]).strftime("%H:%M:%S")
            gate = " [GATE_BLOCKED]" if d["gate_blocked"] else ""
            risk = " [RISK_BLOCKED]" if d["risk_blocked"] else ""
            edge = f" edge={d['expected_edge_bps']:.0f}bps" if d["expected_edge_bps"] else ""
            typer.echo(f"  {ts} {d['action']:4s}{edge}{gate}{risk} — {d['reason']}")
    else:
        typer.echo("  No decisions recorded yet")

    # Last orders
    orders = store.get_orders(limit=5)
    typer.echo(f"\n[Last 5 Orders]")
    if orders:
        for o in orders:
            import datetime
            ts = datetime.datetime.fromtimestamp(o["ts"]).strftime("%H:%M:%S")
            fill_info = f" fill={o['fill_price']:.4f}" if o.get("fill_price") else ""
            typer.echo(
                f"  {ts} {o['side']:4s} amount={o['amount']:.2f} "
                f"status={o['status']}{fill_info}"
            )
    else:
        typer.echo("  No orders recorded yet")

    typer.echo(f"\n{'─'*60}\n")

    store.close()


@app.command(name="reset-cb")
def reset_circuit_breaker() -> None:
    """Manually reset the circuit breaker (use after investigating a trip)."""
    from polymarket_platform.circuit_breaker import CBConfig, CircuitBreaker
    from polymarket_platform.config import Settings
    from polymarket_platform.db.store import SqliteStore

    cfg = Settings()
    store = SqliteStore(cfg.sqlite_path)
    cb = CircuitBreaker(
        CBConfig(
            max_consecutive_losses=cfg.cb_max_consecutive_losses,
            max_daily_drawdown_usd=cfg.cb_max_daily_drawdown_usd,
        ),
        store,
    )
    cb.reset()
    typer.echo("Circuit breaker reset.")
    store.close()


def main() -> None:
    app()
