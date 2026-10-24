"""Sync processor behind an HTTP API (control arm).

Same processing code as the queue consumer, the only difference is that the
producer waits for the answer. If the function breaks there is no queue to hold
the order: the client gets a 5xx and it is up to the client to retry. Whatever
it gives up on is lost - that is the contrast the queue arm is measured against.
"""
from __future__ import annotations

import base64
import json
import os
import time
import uuid
from typing import Any

from common.faults import FaultInjector, load_fault_config
from common.models import Outcome
from common.processing import Deps, process_order_message

STATUS = {
    Outcome.FIRST_SUCCESS: 200,
    Outcome.DUPLICATE_SUCCESS: 200,
    Outcome.UNSAFE_DOUBLE_APPLY: 200,
    Outcome.INVALID: 422,
    Outcome.WRITE_REJECTED: 503,
}


def handle_request(body: str, request_id: str, deps: Deps) -> tuple[int, dict[str, Any]]:
    """Returns (status, payload). Invocation level faults are NOT caught here -
    they escape so API Gateway answers 500/502 like it would for a real crash."""
    outcome = process_order_message(body, request_id, 1, deps)
    return STATUS.get(outcome, 500), {"outcome": outcome}


_STORE = None
_EVENTS = None


def _live_deps(context) -> Deps:
    global _STORE, _EVENTS
    from common.dynamo import DynamoEventLog, DynamoOrderStore

    if _STORE is None:
        _STORE = DynamoOrderStore(os.environ["ORDERS_TABLE"])
        _EVENTS = DynamoEventLog(os.environ["EVENTS_TABLE"])
    injector = FaultInjector(load_fault_config(), hard_kill=os.environ.get("FAULT_HARD_KILL", "1") == "1")
    return Deps(
        store=_STORE,
        events=_EVENTS,
        injector=injector,
        arm="sync",
        run_id=os.environ.get("RUN_ID", ""),
        now=time.time,
        remaining_s=lambda: context.get_remaining_time_in_millis() / 1000.0,
        idempotent=os.environ.get("IDEMPOTENCY", "on") != "off",
    )


def lambda_handler(event: dict[str, Any], context) -> dict[str, Any]:
    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode()
    request_id = (event.get("requestContext") or {}).get("requestId") or getattr(
        context, "aws_request_id", str(uuid.uuid4())
    )
    status, payload = handle_request(body, request_id, _live_deps(context))
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(payload),
    }
