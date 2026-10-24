"""DynamoDB access used by the deployed Lambda functions.

Two tables:

* ``Orders`` (PK ``order_id``) - the business write. Conditional put gives us
  idempotency.
* ``ProcessingEvents`` (PK ``run_id``, SK ``event_id``) - one row per attempt,
  this is the evidence the collector reads after a live run. Rows expire via
  TTL after a week so the table does not grow forever.
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any

from common.faults import DatastoreError
from common.models import Order, ProcessingEvent


def _client(client=None):
    if client is not None:
        return client
    import boto3

    return boto3.client("dynamodb")


def _error_code(exc: Exception) -> str:
    response = getattr(exc, "response", None) or {}
    return response.get("Error", {}).get("Code", type(exc).__name__)


def order_item(order: Order, meta: dict[str, Any]) -> dict[str, dict[str, str]]:
    item = {
        "order_id": {"S": order.order_id},
        "customer_ref": {"S": order.customer_ref},
        "items": {"N": str(order.items)},
        "amount_cents": {"N": str(order.amount_cents)},
        "created_at": {"N": repr(float(order.created_at))},
        "run_id": {"S": order.run_id or "-"},
    }
    for key, value in meta.items():
        if key in item:
            continue
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            item[key] = {"N": repr(value)}
        else:
            item[key] = {"S": str(value)}
    return item


class DynamoOrderStore:
    def __init__(self, table_name: str, client=None) -> None:
        self.table_name = table_name
        self.client = _client(client)

    def put_if_absent(self, order: Order, meta: dict[str, Any]) -> bool:
        try:
            self.client.put_item(
                TableName=self.table_name,
                Item=order_item(order, meta),
                ConditionExpression="attribute_not_exists(order_id)",
            )
            return True
        except Exception as exc:  # botocore ClientError, kept generic for the simulator/tests
            code = _error_code(exc)
            if code == "ConditionalCheckFailedException":
                return False
            raise DatastoreError(code) from exc

    def put(self, order: Order, meta: dict[str, Any]) -> None:
        try:
            self.client.put_item(TableName=self.table_name, Item=order_item(order, meta))
        except Exception as exc:
            raise DatastoreError(_error_code(exc)) from exc

    def exists(self, order_id: str) -> bool:
        resp = self.client.get_item(
            TableName=self.table_name,
            Key={"order_id": {"S": order_id}},
            ConsistentRead=True,
            ProjectionExpression="order_id",
        )
        return "Item" in resp


class DynamoEventLog:
    def __init__(self, table_name: str, client=None, ttl_days: int = 7) -> None:
        self.table_name = table_name
        self.client = _client(client)
        self.ttl_s = ttl_days * 86400

    def log(self, event: ProcessingEvent) -> None:
        event_id = f"{event.ts:.6f}#{event.message_id}#{event.receive_count}#{uuid.uuid4().hex[:8]}"
        item = {
            "run_id": {"S": event.run_id or "-"},
            "event_id": {"S": event_id},
            "arm": {"S": event.arm},
            "message_id": {"S": event.message_id},
            "order_id": {"S": event.order_id},
            "receive_count": {"N": str(event.receive_count)},
            "outcome": {"S": event.outcome},
            "fault_mode": {"S": event.fault_mode},
            "ts": {"N": repr(float(event.ts))},
            "expires_at": {"N": str(int(time.time()) + self.ttl_s)},
        }
        try:
            self.client.put_item(TableName=self.table_name, Item=item)
        except Exception as exc:  # noqa: BLE001
            # losing an evidence row is bad but failing the business path because
            # of it would be worse, so just shout in the logs
            print(json.dumps({"level": "warn", "msg": "event log write failed", "error": str(exc)}))

    def query_run(self, run_id: str) -> list[dict[str, Any]]:
        paginator = self.client.get_paginator("query")
        rows: list[dict[str, Any]] = []
        for page in paginator.paginate(
            TableName=self.table_name,
            KeyConditionExpression="run_id = :r",
            ExpressionAttributeValues={":r": {"S": run_id}},
            ConsistentRead=True,
        ):
            for it in page.get("Items", []):
                rows.append(
                    {
                        "run_id": it["run_id"]["S"],
                        "arm": it.get("arm", {}).get("S", ""),
                        "message_id": it["message_id"]["S"],
                        "order_id": it["order_id"]["S"],
                        "receive_count": int(it["receive_count"]["N"]),
                        "outcome": it["outcome"]["S"],
                        "fault_mode": it.get("fault_mode", {}).get("S", "none"),
                        "ts": float(it["ts"]["N"]),
                    }
                )
        rows.sort(key=lambda r: r["ts"])
        return rows
