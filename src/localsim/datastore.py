"""In-memory stand-ins for the Orders table and the events log."""
from __future__ import annotations

from collections import Counter
from typing import Any

from common.models import Order, ProcessingEvent


class InMemoryOrderStore:
    """Same interface as common.dynamo.DynamoOrderStore."""

    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}
        self.writes = 0
        self.conditional_failures = 0
        # how many times each order was actually applied (should stay at 1)
        self.apply_count: Counter[str] = Counter()

    def put_if_absent(self, order: Order, meta: dict[str, Any]) -> bool:
        self.writes += 1
        if order.order_id in self.items:
            self.conditional_failures += 1
            return False
        self.items[order.order_id] = {"order": order, **meta}
        self.apply_count[order.order_id] += 1
        return True

    def put(self, order: Order, meta: dict[str, Any]) -> None:
        self.writes += 1
        self.items[order.order_id] = {"order": order, **meta}
        self.apply_count[order.order_id] += 1

    def exists(self, order_id: str) -> bool:
        return order_id in self.items


class ListEventLog:
    """Keeps processing events in a list (live mode writes them to DynamoDB)."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def log(self, event: ProcessingEvent) -> None:
        self.events.append(event.to_dict())
