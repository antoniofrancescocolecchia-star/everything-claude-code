from __future__ import annotations

import asyncio

import typer

app = typer.Typer(
    name="polymarket",
    help="Polymarket trading platform CLI",
    no_args_is_help=True,
)


@app.command()
def run(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to .env file"),
) -> None:
    """Start the trading engine in single-token mode (dry-run by default)."""
    import os

    from polymarket_platform.config import Settings
    from polymarket_platform.db.store import SqliteStore
    from polymarket_platform.engine import build_from_settings
    from polymarket_platform.logging_utils import configure_logging

    if config:
        os.environ.setdefault("DOTENV_PATH", config)

    cfg = Settings(_env_file=config) if config else Settings()  # type: ignore[call-arg]
    configure_logging(cfg.log_level, cfg.log_json)

    if not cfg.token_id:
        typer.echo(
            "Error: POLY_TOKEN_ID is required for single-token mode.\n"
            "  Set it in .env, or use 'polymarket scan' for autonomous scanner mode.",
            err=True,
        )
        raise typer.Exit(1)

    store = SqliteStore(cfg.sqlite_path)
    engine = build_from_settings(cfg, store)

    try:
        asyncio.run(engine.run())
    finally:
        store.close()


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
    typer.echo("\n[Risk State]")
    typer.echo(f"  USD spent (cumulative): ${rs.usd_spent:.2f}")
    import time
    last_order_age = time.time() - rs.last_order_ts if rs.last_order_ts > 0 else None
    if last_order_age is not None:
        typer.echo(f"  Last order: {last_order_age:.0f}s ago")
    else:
        typer.echo("  Last order: none")

    # Circuit breaker
    cb_event = store.get_last_circuit_breaker_event()
    typer.echo("\n[Circuit Breaker]")
    if cb_event:
        typer.echo(f"  Last event: {cb_event['reason']} (ts={cb_event['ts']:.0f})")
    else:
        typer.echo("  No events recorded")

    # PnL summary
    pnl = store.get_pnl_summary()
    typer.echo("\n[PnL Summary]")
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
    typer.echo("\n[Last 5 Decisions]")
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
    typer.echo("\n[Last 5 Orders]")
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


@app.command()
def scan(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to .env file"),
) -> None:
    """
    Start the autonomous market scanner (Layer 2).

    Scans Polymarket for active markets, detects opportunities, and
    manages execution workers automatically. No POLY_TOKEN_ID required.
    Works in dry-run mode without trading credentials.
    """
    import os

    from polymarket_platform.config import Settings
    from polymarket_platform.db.store import SqliteStore
    from polymarket_platform.logging_utils import configure_logging
    from polymarket_platform.scanner.orchestrator import Orchestrator

    if config:
        os.environ.setdefault("DOTENV_PATH", config)

    cfg = Settings(_env_file=config) if config else Settings()  # type: ignore[call-arg]
    configure_logging(cfg.log_level, cfg.log_json)

    typer.echo("")
    typer.echo("Polymarket Autonomous Scanner starting")
    typer.echo(f"  DB:           {cfg.sqlite_path}")
    typer.echo(f"  Dry-run:      {cfg.dry_run}")
    typer.echo(f"  Poll interval:{cfg.scanner_poll_interval:.0f}s")
    typer.echo(f"  Max markets:  {cfg.max_concurrent_markets}")
    typer.echo(f"  Max capital:  ${cfg.max_total_capital_usd:.0f}")
    typer.echo("")

    store = SqliteStore(cfg.sqlite_path)
    orchestrator = Orchestrator(cfg=cfg, store=store)

    try:
        asyncio.run(orchestrator.run())
    finally:
        store.close()


@app.command(name="scan-status")
def scan_status() -> None:
    """Print scanner status: catalog, active workers, recent opportunities."""
    import datetime

    from polymarket_platform.config import Settings
    from polymarket_platform.db.store import SqliteStore

    cfg = Settings()
    store = SqliteStore(cfg.sqlite_path)

    sep = "-" * 60
    typer.echo(f"\n{sep}")
    typer.echo(f"  Polymarket Scanner Status ({cfg.sqlite_path})")
    typer.echo(sep)

    # Active tracked positions
    positions = store.get_active_tracked_positions()
    typer.echo(f"\n[Active Workers: {len(positions)}]")
    if positions:
        for p in positions:
            age = datetime.datetime.fromtimestamp(p["started_ts"]).strftime("%H:%M:%S")
            typer.echo(
                f"  {p['token_id'][:24]}  slug={p.get('slug','?')}  "
                f"capital=${p['capital_usd']:.0f}  started={age}"
            )
    else:
        typer.echo("  None")

    # Market catalog top-10
    catalog = store.get_market_catalog(limit=10)
    typer.echo(f"\n[Top Market Catalog (by score): {len(catalog)} shown]")
    if catalog:
        for m in catalog:
            days = f"{m['days_to_expiry']:.1f}d" if m.get("days_to_expiry") else "?"
            typer.echo(
                f"  score={m['score']:.3f}  {m['outcome']}  liq=${m['liquidity']:.0f}"
                f"  vol24={m['volume_24h']:.0f}  exp={days}  {(m.get('slug') or '')[:30]}"
            )
    else:
        typer.echo("  No markets in catalog yet -- run 'polymarket scan' first")

    # Recent opportunities
    opportunities = store.get_recent_opportunities(limit=10)
    typer.echo(f"\n[Recent Opportunities: {len(opportunities)} shown]")
    if opportunities:
        for opp in opportunities:
            ts = datetime.datetime.fromtimestamp(opp["ts"]).strftime("%H:%M:%S")
            typer.echo(
                f"  {ts}  {opp['side']:4s}  conf={opp['confidence']:.2f}"
                f"  edge={opp['expected_edge_bps']:.0f}bps"
                f"  {opp['token_id'][:20]}  {opp['reason'][:50]}"
            )
    else:
        typer.echo("  No opportunities recorded yet")

    typer.echo(f"\n{sep}\n")
    store.close()


def main() -> None:
    app()
