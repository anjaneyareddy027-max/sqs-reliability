"""Queue consumer Lambda.

Wired to the orders queue by an event source mapping with
``FunctionResponseTypes: [ReportBatchItemFailures]``. Records that fail on
their own (bad payload, rejected write) are returned in ``batchItemFailures`` so
only they go back to the queue. Anything that takes the whole invocation down
(kill, unhandled error, timeout) makes Lambda treat the entire batch as failed.
"""
from __future__ import annotations

import os
import time
import boto3
from typing import Any

from common.faults import FaultInjector, load_fault_config
from common.models import Outcome
from common.processing import Deps, process_order_message

RECORD_FAILURES = (Outcome.WRITE_REJECTED, Outcome.INVALID)


def handle_batch(records: list[dict[str, Any]], deps: Deps) -> dict[str, Any]:
    failures = []
    adaptive_vt = deps.injector.config.adaptive_vt

    for record in records:
        receive_count = int(record.get("attributes", {}).get("ApproximateReceiveCount", "1"))

        try:
            outcome = process_order_message(record["body"], record["messageId"], receive_count, deps)
        except Exception:
            # For unhandled errors, if ADAPTIVE_VT is on, we try to adjust visibility before bubbling up
            if adaptive_vt and deps.change_visibility and "receiptHandle" in record:
                backoff = min(60, 2 ** receive_count)
                deps.change_visibility(record["receiptHandle"], float(backoff))
            raise

        if outcome in RECORD_FAILURES:
            if adaptive_vt and deps.change_visibility and "receiptHandle" in record:
                backoff = min(60, 2 ** receive_count)
                deps.change_visibility(record["receiptHandle"], float(backoff))
            failures.append({"itemIdentifier": record["messageId"]})

    return {"batchItemFailures": failures}


# boto3 clients are built once per execution environment (warm starts reuse them)
_STORE = None
_EVENTS = None
_SQS = None


def _live_change_visibility(receipt_handle: str, visibility_timeout: float) -> None:
    queue_url = os.environ.get("QUEUE_URL")
    if not queue_url or not _SQS:
        return
    try:
        _SQS.change_message_visibility(
            QueueUrl=queue_url,
            ReceiptHandle=receipt_handle,
            VisibilityTimeout=int(visibility_timeout)
        )
    except Exception as e:
        print(f"Failed to adapt visibility: {e}")


def _live_deps(context) -> Deps:
    global _STORE, _EVENTS, _SQS
    from common.dynamo import DynamoEventLog, DynamoOrderStore

    if _STORE is None:
        _STORE = DynamoOrderStore(os.environ["ORDERS_TABLE"])
        _EVENTS = DynamoEventLog(os.environ["EVENTS_TABLE"])
        _SQS = boto3.client("sqs", region_name=os.environ.get("AWS_REGION", "eu-west-1"))

    # fault config is re-read every invocation (SSM value is cached ~5 s)
    injector = FaultInjector(load_fault_config(), hard_kill=os.environ.get("FAULT_HARD_KILL", "1") == "1")
    return Deps(
        store=_STORE,
        events=_EVENTS,
        injector=injector,
        arm="queue",
        run_id=os.environ.get("RUN_ID", ""),
        now=time.time,
        remaining_s=lambda: context.get_remaining_time_in_millis() / 1000.0,
        idempotent=os.environ.get("IDEMPOTENCY", "on") != "off",
        change_visibility=_live_change_visibility,
    )


def lambda_handler(event: dict[str, Any], context) -> dict[str, Any]:
    return handle_batch(event.get("Records", []), _live_deps(context))
