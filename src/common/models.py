"""Data model: synthetic orders and the processing events we log per attempt.

Everything here is synthetic. ``customer_ref`` is a made up id like
``CUST-00042`` so there is no personal data anywhere in the pipeline.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any


class InvalidOrder(ValueError):
    """Payload is broken. Retrying will never fix it (a poison message)."""


@dataclass(frozen=True)
class Order:
    order_id: str
    customer_ref: str
    items: int
    amount_cents: int
    created_at: float
    run_id: str = ""
    # poison orders fail validation on every attempt, they exist so we can
    # check that the DLQ really catches messages that can never succeed
    poison: bool = False

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def from_json(cls, raw: str) -> "Order":
        try:
            data = json.loads(raw)
            return cls(**data)
        except (TypeError, ValueError) as exc:
            raise InvalidOrder(f"could not parse order body: {exc}") from exc

    def validate(self) -> None:
        if self.poison:
            raise InvalidOrder(f"{self.order_id} is a poison order")
        if self.items <= 0 or self.amount_cents <= 0:
            raise InvalidOrder(f"{self.order_id} has non positive items/amount")


class Outcome:
    """What happened to one processing attempt of one message."""

    # business write happened for the first time (business_success_first)
    FIRST_SUCCESS = "first_success"
    # conditional put found the order already there -> safe no-op
    # (business_success_duplicate_attempt in the master prompt)
    DUPLICATE_SUCCESS = "duplicate_success"
    # only possible when idempotency is switched off: the order was applied twice
    UNSAFE_DOUBLE_APPLY = "unsafe_double_apply"
    # datastore said no (throttled / rejected) -> reported as a batch item failure
    WRITE_REJECTED = "write_rejected"
    # payload failed validation
    INVALID = "invalid"

    SUCCESSES = (FIRST_SUCCESS, DUPLICATE_SUCCESS, UNSAFE_DOUBLE_APPLY)


@dataclass
class ProcessingEvent:
    """One line of evidence per attempt. Metrics are computed from these."""

    run_id: str
    arm: str
    message_id: str
    order_id: str
    receive_count: int
    outcome: str
    fault_mode: str
    ts: float
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
