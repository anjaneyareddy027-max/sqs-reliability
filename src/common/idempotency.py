"""Idempotent order write.

SQS Standard is at-least-once, so the same message can reach the consumer more
than once even when nothing is broken. The business write is a conditional put
on ``order_id``: the first delivery creates the order, later deliveries find it
already there and become a harmless no-op. Counting the two cases separately is
what lets us tell *transport duplicates* apart from *unsafe double apply*.
"""
from __future__ import annotations

from typing import Any, Protocol

from common.models import Order, Outcome


class OrderStore(Protocol):
    def put_if_absent(self, order: Order, meta: dict[str, Any]) -> bool:
        """Create the order. False if it already existed (conditional check failed)."""

    def put(self, order: Order, meta: dict[str, Any]) -> None:
        """Unconditional write, only used with idempotency switched off."""

    def exists(self, order_id: str) -> bool: ...


def record_order(store: OrderStore, order: Order, meta: dict[str, Any], idempotent: bool = True) -> str:
    """Write the order and say which Outcome it was."""
    if idempotent:
        created = store.put_if_absent(order, meta)
        return Outcome.FIRST_SUCCESS if created else Outcome.DUPLICATE_SUCCESS

    # idempotency off: blind write. Only here so we can show what goes wrong.
    already_there = store.exists(order.order_id)
    store.put(order, meta)
    return Outcome.UNSAFE_DOUBLE_APPLY if already_there else Outcome.FIRST_SUCCESS
