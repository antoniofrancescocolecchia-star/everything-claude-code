from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass

from py_clob_client.clob_types import OrderType

from polymarket_bot.polymarket.clob_client import AsyncClob
from polymarket_bot.storage import OrderRecord, SqliteStore
from polymarket_bot.utils import quantize


@dataclass(frozen=True)
class OrderSizing:
    buy_amount_usd: float
    sell_shares: float
    order_type: str  # FOK|FAK


class OrderManager:
    def __init__(
        self,
        *,
        clob: AsyncClob,
        store: SqliteStore,
        dry_run: bool,
        dedupe_window_seconds: float = 10.0,
    ) -> None:
        self._log = logging.getLogger(self.__class__.__name__)
        self._clob = clob
        self._store = store
        self._dry_run = dry_run
        self._dedupe_window = dedupe_window_seconds
        self._last_sig: dict[str, float] = {}

    @staticmethod
    def _sig(token_id: str, side: str, amount: float, order_type: str) -> str:
        raw = f"{token_id}:{side}:{amount:.8f}:{order_type}".encode()
        return hashlib.sha256(raw).hexdigest()

    def _dedupe_ok(self, sig: str) -> bool:
        now = time.time()
        last = self._last_sig.get(sig)
        if last is not None and (now - last) < self._dedupe_window:
            return False
        self._last_sig[sig] = now
        return True

    @staticmethod
    def _parse_order_type(s: str) -> OrderType:
        s = s.upper().strip()
        if s == "FOK":
            return OrderType.FOK
        if s == "FAK":
            return OrderType.FAK
        raise ValueError("STRAT_ORDER_TYPE must be FOK or FAK for this MVP")

    async def execute(
        self,
        *,
        token_id: str,
        action: str,
        sizing: OrderSizing,
    ) -> dict | None:
        if action not in ("BUY", "SELL"):
            return None

        order_type = self._parse_order_type(sizing.order_type)

        if action == "BUY":
            amount = quantize(sizing.buy_amount_usd, 2)
            side = self._clob.side_buy()
        else:
            amount = quantize(sizing.sell_shares, 2)
            side = self._clob.side_sell()

        sig = self._sig(token_id, side, amount, sizing.order_type)
        if not self._dedupe_ok(sig):
            self._log.info("dedupe: skipping repeated order")
            return None

        if self._dry_run:
            self._log.info(
                "DRY_RUN order: %s %s amount=%s type=%s",
                action,
                token_id,
                amount,
                sizing.order_type,
            )
            self._store.insert_order(
                OrderRecord(
                    ts=time.time(),
                    token_id=token_id,
                    side=str(side),
                    order_type=sizing.order_type,
                    amount=amount,
                    status="DRY_RUN",
                    raw_response=None,
                )
            )
            return {"dry_run": True, "action": action, "amount": amount, "order_type": sizing.order_type}

        resp = await self._clob.market_order(
            token_id=token_id, side=side, amount=amount, order_type=order_type
        )
        self._store.insert_order(
            OrderRecord(
                ts=time.time(),
                token_id=token_id,
                side=str(side),
                order_type=sizing.order_type,
                amount=amount,
                status="SENT",
                raw_response=resp if isinstance(resp, dict) else {"resp": resp},
            )
        )
        self._log.info("LIVE order posted: %s", resp)
        return resp
