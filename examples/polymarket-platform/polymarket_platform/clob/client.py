from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Optional, TypeVar

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, MarketOrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY, SELL

T = TypeVar("T")

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClobConfig:
    host: str
    chain_id: int
    private_key: str | None
    funder: str | None
    signature_type: int
    api_key: str | None
    api_secret: str | None
    api_passphrase: str | None


class ClobExecutor:
    """
    Bridges the synchronous py-clob-client into asyncio without blocking the
    event loop.

    Uses a dedicated ThreadPoolExecutor(max_workers=1) so that:
    - CLOB calls are serialised (requests.Session is not thread-safe)
    - The asyncio event loop is never blocked waiting on network I/O
    - No asyncio.Lock is needed — the executor queue handles ordering

    This is strictly better than the asyncio.Lock + asyncio.to_thread pattern:
    the executor's internal queue is lighter than an asyncio primitive and
    avoids potential deadlocks when running from non-async contexts.
    """

    def __init__(self, cfg: ClobConfig) -> None:
        self._cfg = cfg
        # Single-threaded executor — serialises all sync HTTP calls safely
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="clob")
        self._client = self._build_client(cfg)

    def _build_client(self, cfg: ClobConfig) -> ClobClient:
        if cfg.private_key:
            client = ClobClient(
                cfg.host,
                key=cfg.private_key,
                chain_id=cfg.chain_id,
                signature_type=cfg.signature_type,
                funder=cfg.funder,
            )
            if cfg.api_key and cfg.api_secret and cfg.api_passphrase:
                client.set_api_creds(
                    ApiCreds(
                        api_key=cfg.api_key,
                        api_secret=cfg.api_secret,
                        api_passphrase=cfg.api_passphrase,
                    )
                )
            else:
                log.info("Deriving L2 API credentials from private key")
                client.set_api_creds(client.create_or_derive_api_creds())
            return client
        # Read-only client (dry run / no key)
        return ClobClient(cfg.host, chain_id=cfg.chain_id)

    async def _run(self, fn: Callable[[], T]) -> T:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, fn)

    async def get_price(self, token_id: str, side: str) -> Any:
        token_id_ = token_id
        side_ = side
        return await self._run(lambda: self._client.get_price(token_id_, side_))

    async def get_orders(self) -> list[dict[str, Any]]:
        return await self._run(lambda: self._client.get_orders())

    async def cancel_all(self) -> Any:
        return await self._run(lambda: self._client.cancel_all())

    async def post_heartbeat(self, heartbeat_id: Optional[str]) -> dict[str, Any]:
        hb_id = heartbeat_id
        return await self._run(lambda: self._client.post_heartbeat(hb_id))

    async def market_order(
        self,
        *,
        token_id: str,
        side: str,
        amount: float,
        order_type: OrderType,
    ) -> dict[str, Any]:
        # Capture all arguments explicitly to avoid late-binding closure bugs
        _token_id = token_id
        _side = side
        _amount = amount
        _order_type = order_type
        _client = self._client

        def _execute() -> dict[str, Any]:
            mo = MarketOrderArgs(
                token_id=_token_id,
                amount=_amount,
                side=_side,
                order_type=_order_type,
            )
            signed = _client.create_market_order(mo)
            return _client.post_order(signed, _order_type)

        return await self._run(_execute)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False)

    @staticmethod
    def side_buy() -> str:
        return BUY

    @staticmethod
    def side_sell() -> str:
        return SELL
