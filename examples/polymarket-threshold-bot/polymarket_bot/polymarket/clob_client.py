from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Optional

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, MarketOrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY, SELL


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


class AsyncClob:
    def __init__(self, cfg: ClobConfig) -> None:
        self._log = logging.getLogger(self.__class__.__name__)
        self._cfg = cfg
        self._lock = asyncio.Lock()

        if cfg.private_key:
            self._client = ClobClient(
                cfg.host,
                key=cfg.private_key,
                chain_id=cfg.chain_id,
                signature_type=cfg.signature_type,
                funder=cfg.funder,
            )
            if cfg.api_key and cfg.api_secret and cfg.api_passphrase:
                self._client.set_api_creds(
                    ApiCreds(
                        api_key=cfg.api_key,
                        api_secret=cfg.api_secret,
                        api_passphrase=cfg.api_passphrase,
                    )
                )
            else:
                self._client.set_api_creds(self._client.create_or_derive_api_creds())
        else:
            self._client = ClobClient(cfg.host, chain_id=cfg.chain_id)

    @property
    def raw(self) -> ClobClient:
        return self._client

    async def get_price(self, token_id: str, side: str) -> Any:
        async with self._lock:
            return await asyncio.to_thread(self._client.get_price, token_id, side)

    async def get_orders(self) -> list[dict[str, Any]]:
        async with self._lock:
            return await asyncio.to_thread(self._client.get_orders)

    async def cancel_all(self) -> Any:
        async with self._lock:
            return await asyncio.to_thread(self._client.cancel_all)

    async def post_heartbeat(self, heartbeat_id: Optional[str]) -> dict[str, Any]:
        async with self._lock:
            return await asyncio.to_thread(self._client.post_heartbeat, heartbeat_id)

    async def market_order(
        self,
        *,
        token_id: str,
        side: str,
        amount: float,
        order_type: OrderType,
    ) -> dict[str, Any]:
        async with self._lock:
            def _run() -> dict[str, Any]:
                mo = MarketOrderArgs(
                    token_id=token_id, amount=amount, side=side, order_type=order_type
                )
                signed = self._client.create_market_order(mo)
                return self._client.post_order(signed, order_type)

            return await asyncio.to_thread(_run)

    @staticmethod
    def side_buy() -> str:
        return BUY

    @staticmethod
    def side_sell() -> str:
        return SELL
