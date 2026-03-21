from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from typing import Any

from py_clob_client.clob_types import OrderType

from polymarket_platform.clob.client import ClobExecutor
from polymarket_platform.db.store import SqliteStore


def _quantize(value: float, decimals: int) -> float:
    q = Decimal("1").scaleb(-decimals)
    return float(Decimal(str(value)).quantize(q, rounding=ROUND_DOWN))


def _parse_order_type(s: str) -> OrderType:
    s = s.upper().strip()
    if s == "FOK":
        return OrderType.FOK
    if s == "FAK":
        return OrderType.FAK
    raise ValueError(f"STRAT_ORDER_TYPE must be FOK or FAK, got: {s!r}")


@dataclass(frozen=True)
class OrderSizing:
    buy_amount_usd: float
    sell_shares: float
    order_type: str  # FOK | FAK


class OrderManager:
    """
    Handles final pre-execution steps: deduplication, quantisation,
    dry-run simulation, live order submission, and SQLite persistence.
    """

    def __init__(
        self,
        *,
        clob: ClobExecutor,
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

    async def execute(
        self,
        *,
        token_id: str,
        action: str,
        sizing: OrderSizing,
        decision_id: int | None = None,
    ) -> dict[str, Any] | None:
        if action not in ("BUY", "SELL"):
            return None

        order_type = _parse_order_type(sizing.order_type)

        if action == "BUY":
            amount = _quantize(sizing.buy_amount_usd, 2)
            side = self._clob.side_buy()
        else:
            amount = _quantize(sizing.sell_shares, 2)
            side = self._clob.side_sell()

        sig = self._sig(token_id, side, amount, sizing.order_type)
        if not self._dedupe_ok(sig):
            self._log.info("dedupe: skipping repeated %s order", action)
            return None

        if self._dry_run:
            self._log.info(
                "DRY_RUN %s %s amount=%.4f type=%s",
                action,
                token_id,
                amount,
                sizing.order_type,
            )
            self._store.insert_order(
                decision_id=decision_id,
                token_id=token_id,
                side=side,
                order_type=sizing.order_type,
                amount=amount,
                status="DRY_RUN",
            )
            return {
                "dry_run": True,
                "action": action,
                "amount": amount,
                "order_type": sizing.order_type,
            }

        # Capture variables explicitly — no late-binding closure bugs
        _token_id = token_id
        _side = side
        _amount = amount
        _order_type = order_type

        resp = await self._clob.market_order(
            token_id=_token_id,
            side=_side,
            amount=_amount,
            order_type=_order_type,
        )

        raw: dict[str, Any] = resp if isinstance(resp, dict) else {"resp": str(resp)}

        # Attempt to extract fill info from response
        fill_price: float | None = None
        fill_amount: float | None = None
        fee_usd: float | None = None

        if isinstance(resp, dict):
            fill_price = float(resp["price"]) if resp.get("price") else None
            fill_amount = float(resp.get("size_matched") or resp.get("fillAmount") or 0) or None
            fee_usd = float(resp["fee"]) if resp.get("fee") else None

        order_id = self._store.insert_order(
            decision_id=decision_id,
            token_id=token_id,
            side=side,
            order_type=sizing.order_type,
            amount=amount,
            status="SENT",
            fill_price=fill_price,
            fill_amount=fill_amount,
            fee_usd=fee_usd,
            raw_response=raw,
        )

        self._log.info("LIVE order posted id=%s: %s", order_id, resp)
        return resp
