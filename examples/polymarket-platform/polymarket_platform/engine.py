from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import time
from dataclasses import dataclass, field
from typing import Optional

from polymarket_platform.circuit_breaker import CBConfig, CBState, CircuitBreaker
from polymarket_platform.clob.client import ClobConfig, ClobExecutor
from polymarket_platform.config import Settings
from polymarket_platform.db.store import SqliteStore
from polymarket_platform.execution.gate import ProfitabilityGate
from polymarket_platform.execution.order_manager import OrderManager, OrderSizing
from polymarket_platform.feed.base import FeedSource, Quote
from polymarket_platform.feed.rest import RestFeed
from polymarket_platform.feed.websocket import WsFeed
from polymarket_platform.market.data_api import DataApiClient
from polymarket_platform.risk.manager import RiskLimits, RiskManager
from polymarket_platform.strategy.base import MarketSnapshot, StrategyPlugin
from polymarket_platform.strategy.threshold import ThresholdParams, ThresholdStrategy
from polymarket_platform.utils import with_retries

log = logging.getLogger(__name__)


@dataclass
class _EngineState:
    position_cache: dict[str, float] = field(default_factory=dict)
    last_quote_ts: float = 0.0


def build_from_settings(cfg: Settings, store: SqliteStore) -> "TradingEngine":
    """Convenience factory — builds a fully-wired engine from Settings."""
    clob_cfg = ClobConfig(
        host=cfg.clob_host,
        chain_id=cfg.chain_id,
        private_key=(cfg.private_key.get_secret_value() if cfg.private_key else None),
        funder=cfg.funder_address,
        signature_type=cfg.signature_type,
        api_key=cfg.api_key,
        api_secret=(cfg.api_secret.get_secret_value() if cfg.api_secret else None),
        api_passphrase=(cfg.api_passphrase.get_secret_value() if cfg.api_passphrase else None),
    )
    clob = ClobExecutor(clob_cfg)
    data_api = DataApiClient(cfg.data_host, timeout_seconds=cfg.request_timeout_seconds)

    strategy: StrategyPlugin = ThresholdStrategy(
        ThresholdParams(buy_threshold=cfg.buy_threshold, sell_threshold=cfg.sell_threshold)
    )
    risk = RiskManager(
        RiskLimits(
            max_position_shares=cfg.max_position_shares,
            max_usd_spend=cfg.max_usd_spend,
            min_seconds_between_orders=cfg.min_seconds_between_orders,
            max_open_orders=cfg.max_open_orders,
        ),
        store,
    )
    gate = ProfitabilityGate(
        fee_taker_bps=cfg.fee_taker_bps,
        min_edge_bps=cfg.min_expected_edge_bps,
    )
    cb = CircuitBreaker(
        CBConfig(
            max_consecutive_losses=cfg.cb_max_consecutive_losses,
            max_daily_drawdown_usd=cfg.cb_max_daily_drawdown_usd,
            max_decision_latency_ms=cfg.cb_max_decision_latency_ms,
        ),
        store,
    )
    orders = OrderManager(clob=clob, store=store, dry_run=cfg.dry_run)

    return TradingEngine(
        cfg=cfg,
        clob=clob,
        data_api=data_api,
        store=store,
        strategy=strategy,
        risk=risk,
        gate=gate,
        cb=cb,
        orders=orders,
        quote_queue=asyncio.Queue(maxsize=256),
    )


class TradingEngine:
    """
    Orchestrates the full decision pipeline:
        Feed → Strategy → ProfitabilityGate → Risk → CircuitBreaker → OrderManager

    The engine is independent of the feed implementation (WS / REST / test queue)
    and the strategy implementation. Both are injected.

    Signal handling uses loop.add_signal_handler() — the asyncio-safe approach.
    """

    def __init__(
        self,
        *,
        cfg: Settings,
        clob: ClobExecutor,
        data_api: DataApiClient,
        store: SqliteStore,
        strategy: StrategyPlugin,
        risk: RiskManager,
        gate: ProfitabilityGate,
        cb: CircuitBreaker,
        orders: OrderManager,
        quote_queue: "asyncio.Queue[Quote]",
    ) -> None:
        self._cfg = cfg
        self._clob = clob
        self._data_api = data_api
        self._store = store
        self._strategy = strategy
        self._risk = risk
        self._gate = gate
        self._cb = cb
        self._orders = orders
        self._queue = quote_queue
        self._state = _EngineState()

    # ── Public entry point ────────────────────────────────────────────────────

    async def run(self, stop: Optional[asyncio.Event] = None) -> None:
        """
        Run the engine until `stop` is set, SIGINT, or SIGTERM.
        If `stop` is not provided, one is created internally.
        """
        if stop is None:
            stop = asyncio.Event()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)

        log.info(
            "engine starting strategy=%s dry_run=%s token_id=%s",
            self._strategy.name,
            self._cfg.dry_run,
            self._cfg.token_id,
        )

        try:
            await self._run_loop(stop)
        finally:
            for sig in (signal.SIGINT, signal.SIGTERM):
                with contextlib.suppress(Exception):
                    loop.remove_signal_handler(sig)
            await self._shutdown()

    # ── Internal loop ─────────────────────────────────────────────────────────

    async def _run_loop(self, stop: asyncio.Event) -> None:
        ws_feed = WsFeed(
            ws_url=self._cfg.clob_ws_url,
            token_id=self._cfg.token_id,
            fallback_after_failures=self._cfg.feed_fallback_after_failures,
            max_backoff_seconds=self._cfg.feed_ws_max_reconnect_backoff,
        )
        rest_feed = RestFeed(
            clob_host=self._cfg.clob_host,
            token_id=self._cfg.token_id,
            poll_interval_seconds=self._cfg.feed_poll_interval_seconds,
            timeout_seconds=self._cfg.request_timeout_seconds,
        )

        tasks: list[asyncio.Task] = []

        # Primary WS feed; falls back to REST automatically after repeated failures
        feed_task = asyncio.create_task(
            self._managed_feed(ws_feed, rest_feed, stop), name="feed"
        )
        tasks.append(feed_task)
        tasks.append(
            asyncio.create_task(self._position_loop(stop), name="position-refresh")
        )
        tasks.append(
            asyncio.create_task(self._kill_switch_loop(stop), name="kill-switch")
        )
        if self._cfg.heartbeat_enabled and not self._cfg.dry_run:
            tasks.append(
                asyncio.create_task(self._heartbeat_loop(stop), name="heartbeat")
            )

        await self._consume_quotes(stop)

        stop.set()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

        await rest_feed.close()

    async def _managed_feed(
        self, ws_feed: WsFeed, rest_feed: RestFeed, stop: asyncio.Event
    ) -> None:
        """Run WS feed; fall back to REST if WS repeatedly fails."""
        await ws_feed.run(self._queue, stop)
        if not stop.is_set():
            log.warning(
                "WS feed gave up after %d failures; switching to REST polling",
                ws_feed.consecutive_failures,
            )
            await rest_feed.run(self._queue, stop)

    # ── Quote consumer ────────────────────────────────────────────────────────

    async def _consume_quotes(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                quote = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                # Check feed staleness
                if self._state.last_quote_ts > 0:
                    staleness = time.time() - self._state.last_quote_ts
                    if staleness > self._cfg.feed_max_staleness_seconds:
                        log.warning("feed stale: %.1fs since last quote", staleness)
                        self._cb.on_feed_stale()
                continue

            self._state.last_quote_ts = quote.ts

            # Kill switch check is in its own task; also check here for fast path
            if self._cfg.kill_switch_path and os.path.exists(self._cfg.kill_switch_path):
                log.warning("kill switch detected — stopping")
                stop.set()
                return

            if self._cb.state == CBState.OPEN:
                log.debug("circuit breaker OPEN — skipping quote")
                continue

            await self._process_quote(quote)

    # ── Decision pipeline ─────────────────────────────────────────────────────

    async def _process_quote(self, quote: Quote) -> None:
        t0 = time.monotonic()

        position_shares = self._state.position_cache.get(quote.token_id, 0.0)
        snap = MarketSnapshot(
            token_id=quote.token_id,
            best_bid=quote.best_bid,
            best_ask=quote.best_ask,
            position_shares=position_shares,
            ts=quote.ts,
        )

        decision = self._strategy.decide(snap)

        # Persist quote
        quote_id = self._store.insert_quote(
            token_id=quote.token_id,
            best_bid=quote.best_bid,
            best_ask=quote.best_ask,
            feed_source=quote.source.value,
            ts=quote.ts,
        )

        # Gate check
        gate_ok, gate_reason = self._gate.check(decision)

        # Risk check (only counts open orders in live mode)
        open_orders_count = 0
        if not self._cfg.dry_run and self._cfg.private_key:
            with contextlib.suppress(Exception):
                open_orders = await with_retries(
                    lambda: self._clob.get_orders(),  # noqa: B023 — no loop variable
                    max_retries=self._cfg.max_retries,
                )
                open_orders_count = len(open_orders)

        risk_ok, risk_reason = self._risk.allow(
            action=decision.action,
            position_shares=position_shares,
            best_ask=quote.best_ask,
            buy_amount_usd=self._cfg.buy_amount_usd,
            open_orders_count=open_orders_count,
        )

        # Persist decision with gate/risk outcomes
        decision_id = self._store.insert_decision(
            quote_id=quote_id,
            token_id=quote.token_id,
            action=decision.action,
            reason=decision.reason,
            best_bid=quote.best_bid,
            best_ask=quote.best_ask,
            position_shares=position_shares,
            expected_edge_bps=decision.expected_edge_bps,
            gate_blocked=not gate_ok,
            gate_block_reason=gate_reason if not gate_ok else None,
            risk_blocked=not risk_ok,
            risk_block_reason=risk_reason if not risk_ok else None,
        )

        if not decision.is_actionable():
            return

        if not gate_ok:
            log.debug("gate blocked %s: %s", decision.action, gate_reason)
            return

        if not risk_ok:
            log.info("risk blocked %s: %s", decision.action, risk_reason)
            return

        # Determine sell size: never sell more than current position
        sell_shares = (
            min(self._cfg.sell_shares, position_shares)
            if decision.action == "SELL"
            else self._cfg.sell_shares
        )
        sizing = OrderSizing(
            buy_amount_usd=self._cfg.buy_amount_usd,
            sell_shares=sell_shares,
            order_type=self._cfg.order_type,
        )

        # Explicit capture — no late-binding closure bugs
        _token_id = quote.token_id
        _action = decision.action
        _sizing = sizing
        _decision_id = decision_id

        resp = await with_retries(
            lambda tid=_token_id, act=_action, sz=_sizing, did=_decision_id: self._orders.execute(
                token_id=tid, action=act, sizing=sz, decision_id=did
            ),
            max_retries=self._cfg.max_retries,
        )

        if resp is not None:
            self._risk.on_order_sent(
                action=decision.action,
                buy_amount_usd=self._cfg.buy_amount_usd,
            )

        # Check decision latency
        latency_ms = (time.monotonic() - t0) * 1000
        if latency_ms > self._cfg.cb_max_decision_latency_ms:
            log.warning("high decision latency: %.1f ms", latency_ms)
            self._cb.on_high_latency(latency_ms)

    # ── Background tasks ──────────────────────────────────────────────────────

    async def _position_loop(self, stop: asyncio.Event) -> None:
        """Periodically refresh position from Data API."""
        while not stop.is_set():
            user = self._cfg.funder_address or ""
            if user:
                with contextlib.suppress(Exception):
                    positions = await with_retries(
                        lambda u=user: self._data_api.get_positions(u),
                        max_retries=self._cfg.max_retries,
                    )
                    for p in positions:
                        self._state.position_cache[p.asset] = float(p.size)
                    log.debug(
                        "position refreshed: %s",
                        self._state.position_cache.get(self._cfg.token_id, 0.0),
                    )
            try:
                await asyncio.wait_for(
                    stop.wait(), timeout=self._cfg.position_refresh_interval_seconds
                )
            except asyncio.TimeoutError:
                pass

    async def _heartbeat_loop(self, stop: asyncio.Event) -> None:
        """Send heartbeats to keep resting orders alive (GTC strategies)."""
        heartbeat_id: Optional[str] = ""
        while not stop.is_set():
            with contextlib.suppress(Exception):
                resp = await with_retries(
                    lambda hid=heartbeat_id: self._clob.post_heartbeat(hid),
                    max_retries=self._cfg.max_retries,
                )
                if isinstance(resp, dict):
                    heartbeat_id = str(
                        resp.get("heartbeat_id") or resp.get("heartbeatId") or heartbeat_id
                    )
                self._store.insert_heartbeat(
                    heartbeat_id,
                    resp if isinstance(resp, dict) else {"resp": str(resp)},
                )
            try:
                await asyncio.wait_for(
                    stop.wait(), timeout=self._cfg.heartbeat_interval_seconds
                )
            except asyncio.TimeoutError:
                pass

    async def _kill_switch_loop(self, stop: asyncio.Event) -> None:
        """Watch for kill switch file; cancel orders and stop if found."""
        while not stop.is_set():
            if self._cfg.kill_switch_path and os.path.exists(self._cfg.kill_switch_path):
                log.warning(
                    "KILL SWITCH detected at %s — cancelling orders and stopping",
                    self._cfg.kill_switch_path,
                )
                if not self._cfg.dry_run and self._cfg.private_key:
                    with contextlib.suppress(Exception):
                        await self._clob.cancel_all()
                stop.set()
                return
            await asyncio.sleep(0.5)

    # ── Shutdown ──────────────────────────────────────────────────────────────

    async def _shutdown(self) -> None:
        log.info("engine shutting down")
        if not self._cfg.dry_run and self._cfg.private_key:
            with contextlib.suppress(Exception):
                await self._clob.cancel_all()
                log.info("cancel_all sent")
        self._clob.shutdown()
        with contextlib.suppress(Exception):
            await self._data_api.close()
        self._store.close()
        log.info("engine stopped")
