import base64
import dataclasses
import json

import pytest

from common.faults import ConsumerKilled, InjectedError
from common.models import Outcome
from queue_consumer import handler as consumer
from sync_api import app as sync_app


def outcomes(deps):
    return [e["outcome"] for e in deps.events.events]


def test_happy_batch_has_no_failures(make_deps, orders, sqs_record):
    deps = make_deps()
    records = [sqs_record(o, f"m-{i}") for i, o in enumerate(orders[:3])]
    assert consumer.handle_batch(records, deps) == {"batchItemFailures": []}
    assert outcomes(deps) == [Outcome.FIRST_SUCCESS] * 3


def test_every_event_carries_the_required_fields(make_deps, orders, sqs_record):
    deps = make_deps()
    consumer.handle_batch([sqs_record(orders[0], "m-9", receive_count=2)], deps)
    ev = deps.events.events[0]
    for key in ("message_id", "order_id", "receive_count", "fault_mode", "ts"):
        assert key in ev
    assert ev["receive_count"] == 2
    assert ev["message_id"] == "m-9"


def test_rejected_writes_become_partial_batch_failures(make_deps, orders, sqs_record):
    deps = make_deps(mode="datastore_reject", rate=1.0)
    records = [sqs_record(o, f"m-{i}") for i, o in enumerate(orders[:2])]
    result = consumer.handle_batch(records, deps)
    assert [f["itemIdentifier"] for f in result["batchItemFailures"]] == ["m-0", "m-1"]
    assert outcomes(deps) == [Outcome.WRITE_REJECTED] * 2


def test_poison_order_is_reported_not_raised(make_deps, orders, sqs_record):
    deps = make_deps()
    poison = dataclasses.replace(orders[0], poison=True)
    result = consumer.handle_batch([sqs_record(poison, "p-1"), sqs_record(orders[1], "ok-1")], deps)
    assert result["batchItemFailures"] == [{"itemIdentifier": "p-1"}]
    assert outcomes(deps) == [Outcome.INVALID, Outcome.FIRST_SUCCESS]


def test_garbage_body_is_invalid(make_deps, sqs_record):
    deps = make_deps()
    result = consumer.handle_batch([sqs_record("{not json", "g-1")], deps)
    assert result["batchItemFailures"] == [{"itemIdentifier": "g-1"}]
    assert deps.events.events[0]["order_id"] == "unknown:g-1"


def test_consumer_kill_takes_down_the_whole_invocation(make_deps, orders, sqs_record):
    deps = make_deps(mode="consumer_kill", rate=1.0)
    with pytest.raises(ConsumerKilled):
        consumer.handle_batch([sqs_record(o, f"m-{i}") for i, o in enumerate(orders[:3])], deps)
    assert deps.store.writes == 0


def test_error_after_write_turns_redelivery_into_duplicate(make_deps, orders, sqs_record):
    deps = make_deps(mode="unhandled_error", rate=1.0, point="after_write")
    with pytest.raises(InjectedError):
        consumer.handle_batch([sqs_record(orders[0], "m-1")], deps)
    # the write happened before the crash
    assert deps.store.exists(orders[0].order_id)

    # redelivery with the fault gone -> safe duplicate
    deps.injector.config.mode = "none"
    consumer.handle_batch([sqs_record(orders[0], "m-1", receive_count=2)], deps)
    assert outcomes(deps) == [Outcome.FIRST_SUCCESS, Outcome.DUPLICATE_SUCCESS]
    assert deps.store.apply_count[orders[0].order_id] == 1


def test_sync_request_status_codes(make_deps, orders):
    deps = make_deps()
    assert sync_app.handle_request(orders[0].to_json(), "r-1", deps)[0] == 200
    assert sync_app.handle_request(orders[0].to_json(), "r-2", deps)[0] == 200  # duplicate is fine
    assert sync_app.handle_request("{bad", "r-3", deps)[0] == 422
    rejecting = make_deps(mode="datastore_reject", rate=1.0)
    assert sync_app.handle_request(orders[1].to_json(), "r-4", rejecting)[0] == 503


class FakeContext:
    aws_request_id = "req-123"

    def get_remaining_time_in_millis(self):
        return 5000


def test_sync_lambda_handler_decodes_base64(monkeypatch, make_deps, orders):
    deps = make_deps()
    monkeypatch.setattr(sync_app, "_live_deps", lambda ctx: deps)
    event = {"body": base64.b64encode(orders[0].to_json().encode()).decode(), "isBase64Encoded": True}
    resp = sync_app.lambda_handler(event, FakeContext())
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["outcome"] == Outcome.FIRST_SUCCESS


def test_queue_lambda_handler_uses_records(monkeypatch, make_deps, orders, sqs_record):
    deps = make_deps()
    monkeypatch.setattr(consumer, "_live_deps", lambda ctx: deps)
    resp = consumer.lambda_handler({"Records": [sqs_record(orders[0])]}, FakeContext())
    assert resp == {"batchItemFailures": []}
