"""The AWS-facing code against moto (no real account, no cost)."""
import json

import boto3
import pytest
from moto import mock_aws

from common.dynamo import DynamoEventLog, DynamoOrderStore
from common.faults import DatastoreError, FaultConfig
from common.models import ProcessingEvent
from control.config import RunSpec
from control.fault_controller import SsmFaultController
from control.live_backend import StackOutputs, apply_queue_settings, drain_order_ids, queue_depth
from producer.loadgen import send_to_sqs

REGION = "eu-west-1"


@pytest.fixture
def aws(monkeypatch):
    for var, value in (("AWS_ACCESS_KEY_ID", "testing"), ("AWS_SECRET_ACCESS_KEY", "testing"),
                       ("AWS_SESSION_TOKEN", "testing"), ("AWS_DEFAULT_REGION", REGION)):
        monkeypatch.setenv(var, value)
    with mock_aws():
        yield


def make_tables(ddb):
    ddb.create_table(
        TableName="orders", BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[{"AttributeName": "order_id", "AttributeType": "S"}],
        KeySchema=[{"AttributeName": "order_id", "KeyType": "HASH"}],
    )
    ddb.create_table(
        TableName="events", BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[{"AttributeName": "run_id", "AttributeType": "S"},
                              {"AttributeName": "event_id", "AttributeType": "S"}],
        KeySchema=[{"AttributeName": "run_id", "KeyType": "HASH"}, {"AttributeName": "event_id", "KeyType": "RANGE"}],
    )


def test_conditional_put_is_idempotent(aws, orders):
    ddb = boto3.client("dynamodb", region_name=REGION)
    make_tables(ddb)
    store = DynamoOrderStore("orders", ddb)
    assert store.put_if_absent(orders[0], {"message_id": "m1", "receive_count": 1}) is True
    assert store.put_if_absent(orders[0], {"message_id": "m2", "receive_count": 2}) is False
    assert store.exists(orders[0].order_id)
    assert not store.exists("no-such-order")


def test_datastore_errors_are_wrapped(aws, orders):
    store = DynamoOrderStore("missing-table", boto3.client("dynamodb", region_name=REGION))
    with pytest.raises(DatastoreError):
        store.put_if_absent(orders[0], {})


def test_event_log_roundtrip(aws):
    ddb = boto3.client("dynamodb", region_name=REGION)
    make_tables(ddb)
    log = DynamoEventLog("events", ddb)
    for i in range(3):
        log.log(ProcessingEvent(run_id="r1", arm="queue", message_id=f"m{i}", order_id=f"o{i}", receive_count=1,
                                outcome="first_success", fault_mode="none", ts=100.0 + i))
    rows = log.query_run("r1")
    assert [r["order_id"] for r in rows] == ["o0", "o1", "o2"]
    assert log.query_run("other") == []


class Ctx:
    aws_request_id = "ctx-1"

    def get_remaining_time_in_millis(self):
        return 10_000


def test_deployed_consumer_against_moto(aws, orders, monkeypatch, sqs_record):
    ddb = boto3.client("dynamodb", region_name=REGION)
    make_tables(ddb)
    monkeypatch.setenv("ORDERS_TABLE", "orders")
    monkeypatch.setenv("EVENTS_TABLE", "events")
    monkeypatch.setenv("FAULT_HARD_KILL", "0")
    monkeypatch.setenv("FAULT_MODE", "none")
    monkeypatch.delenv("FAULT_PARAM_NAME", raising=False)
    from queue_consumer import handler

    monkeypatch.setattr(handler, "_STORE", None)
    monkeypatch.setattr(handler, "_EVENTS", None)
    event = {"Records": [sqs_record(orders[0], "m-1"), sqs_record(orders[0], "m-1", receive_count=2)]}
    assert handler.lambda_handler(event, Ctx()) == {"batchItemFailures": []}
    rows = DynamoEventLog("events", ddb).query_run(orders[0].run_id)
    assert [r["outcome"] for r in rows] == ["first_success", "duplicate_success"]


def test_ssm_fault_controller(aws):
    ssm = boto3.client("ssm", region_name=REGION)
    ssm.put_parameter(Name="/sqs-rr/test/fault", Value=FaultConfig().to_json(), Type="String")
    ctl = SsmFaultController("/sqs-rr/test/fault", ssm)
    ctl.enable(FaultConfig(mode="consumer_kill", rate=0.5))
    assert ctl.current().mode == "consumer_kill"
    ctl.disable()
    assert ctl.current().mode == "none"


def test_queue_depth_and_drain(aws):
    sqs = boto3.client("sqs", region_name=REGION)
    url = sqs.create_queue(QueueName="t")["QueueUrl"]
    for i in range(3):
        sqs.send_message(QueueUrl=url, MessageBody=json.dumps({"order_id": f"o{i}"}))
    assert queue_depth(sqs, url)["visible"] == 3
    assert sorted(drain_order_ids(sqs, url, max_empty=1)) == ["o0", "o1", "o2"]
    assert queue_depth(sqs, url)["visible"] == 0


def test_send_to_sqs_uses_batches(aws, orders):
    sqs = boto3.client("sqs", region_name=REGION)
    url = sqs.create_queue(QueueName="send")["QueueUrl"]
    produced = send_to_sqs(sqs, url, orders, [0.0] * len(orders), sleep=lambda s: None)
    assert set(produced) == {o.order_id for o in orders}
    assert queue_depth(sqs, url)["visible"] == len(orders)


class FakeLambda:
    def __init__(self):
        self.kw = None

    def update_event_source_mapping(self, **kw):
        self.kw = kw

    def get_event_source_mapping(self, UUID):
        return {"State": "Enabled"}


def test_apply_queue_settings(aws):
    sqs = boto3.client("sqs", region_name=REGION)
    url = sqs.create_queue(QueueName="main")["QueueUrl"]
    dlq = sqs.create_queue(QueueName="main-dlq")["QueueUrl"]
    outputs = StackOutputs(queue_url=url, dlq_url=dlq, queue_arn="arn", api_url="http://x", orders_table="o",
                           events_table="e", consumer_function="f", mapping_id="uuid-1", fault_param="/p")
    lam = FakeLambda()
    apply_queue_settings(sqs, lam, outputs, RunSpec(visibility_timeout=120, max_receive_count=3, batch_size=25))
    attrs = sqs.get_queue_attributes(QueueUrl=url, AttributeNames=["All"])["Attributes"]
    assert attrs["VisibilityTimeout"] == "120"
    assert int(json.loads(attrs["RedrivePolicy"])["maxReceiveCount"]) == 3
    assert lam.kw == {"UUID": "uuid-1", "BatchSize": 25, "MaximumBatchingWindowInSeconds": 1}
