"""Processing of one order message. Shared by the queue consumer and the sync API.

Order of steps (the fault points are marked):

    [before_process]  -> parse + validate -> [before_write] -> conditional put
                      -> log the outcome  -> [after_write]

The success is logged *before* the after_write point on purpose: if an
unhandled error fires after the write, the business write already happened, so
the redelivery later shows up as a duplicate attempt - which is exactly the
"partial work then crash" case we want to see.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from common.faults import DatastoreError, FaultInjector
from common.idempotency import OrderStore, record_order
from common.models import InvalidOrder, Order, Outcome, ProcessingEvent


def _no_tick(stage: str) -> None:  # real Lambda: work takes real time by itself
    return None


@dataclass
class Deps:
    store: OrderStore
    events: Any  # anything with .log(ProcessingEvent)
    injector: FaultInjector
    arm: str
    run_id: str = ""
    now: Callable[[], float] = time.time
    remaining_s: Callable[[], float] = lambda: 900.0
    idempotent: bool = True
    # the simulator charges virtual time for "parse" and "write" through this
    tick: Callable[[str], None] = field(default=_no_tick)
    change_visibility: Callable[[str, float], None] | None = None


def _guess_order_id(body: str, message_id: str) -> str:
    try:
        return str(json.loads(body).get("order_id") or f"unknown:{message_id}")
    except (ValueError, AttributeError):
        return f"unknown:{message_id}"


def process_order_message(body: str, message_id: str, receive_count: int, deps: Deps) -> str:
    """Process one message and return its Outcome.

    Record level problems (bad payload, datastore refused the write) come back
    as an Outcome so the caller can report a partial batch failure. Invocation
    level faults (kill, unhandled error, timeout) are raised and take the whole
    invocation down with them.
    """
    mode = deps.injector.config.mode

    def log(order_id: str, run_id: str, outcome: str, **extra: Any) -> None:
        deps.events.log(
            ProcessingEvent(
                run_id=run_id or deps.run_id,
                arm=deps.arm,
                message_id=message_id,
                order_id=order_id,
                receive_count=receive_count,
                outcome=outcome,
                fault_mode=mode,
                ts=deps.now(),
                extra=extra,
            )
        )

    deps.injector.check("before_process", deps.remaining_s())
    deps.tick("parse")
    try:
        order = Order.from_json(body)
        order.validate()
    except InvalidOrder as exc:
        log(_guess_order_id(body, message_id), "", Outcome.INVALID, error=str(exc))
        return Outcome.INVALID

    try:
        deps.injector.check("before_write", deps.remaining_s())
        deps.tick("write")
        outcome = record_order(
            deps.store,
            order,
            meta={"message_id": message_id, "receive_count": receive_count, "processed_at": deps.now()},
            idempotent=deps.idempotent,
        )
    except DatastoreError as exc:
        log(order.order_id, order.run_id, Outcome.WRITE_REJECTED, error=str(exc))
        return Outcome.WRITE_REJECTED

    log(order.order_id, order.run_id, outcome)
    deps.injector.check("after_write", deps.remaining_s())
    return outcome
