"""Live AWS backend (DRY_RUN=0). Needs a deployed stack in YOUR OWN account.

This path produced every live result in the report (14 Oct 2026 campaign,
21 Oct pilot and the 23 to 24 Oct v2 campaign). The pieces are unit tested
with moto where moto supports the API.

One run:

1. apply the run's SQS/ESM settings (visibility timeout, maxReceiveCount,
   batch size) and wait until the event source mapping is back to Enabled
2. purge the queues only if something is left over, and wait 61 s if a purge
   or an attribute change happened (purge cool-down and SQS propagation)
3. write the fault schedule to SSM (absolute on/off times)
4. send the orders following the load profile, sample queue depth every
   ``sample_interval_s`` on the main thread while a sender thread produces
5. drain until the queue is empty or the drain timeout passes
6. collect: processing events (DynamoDB), DLQ contents, leftover messages,
   Lambda/SQS request counts from CloudWatch (for the cost proxy)
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from control.fault_controller import SsmFaultController, schedule_for_run
from localsim.engine import RunResult
from producer.generate_orders import make_orders
from producer.loadgen import arrival_times, post_to_api, send_to_sqs


@dataclass
class StackOutputs:
    queue_url: str
    dlq_url: str
    queue_arn: str
    api_url: str
    orders_table: str
    events_table: str
    consumer_function: str
    mapping_id: str
    fault_param: str
    sync_function: str = ""

    @classmethod
    def from_stack(cls, stack_name: str, cfn_client=None) -> "StackOutputs":
        if cfn_client is None:
            import boto3

            cfn_client = boto3.client("cloudformation")
        stack = cfn_client.describe_stacks(StackName=stack_name)["Stacks"][0]
        out = {o["OutputKey"]: o["OutputValue"] for o in stack.get("Outputs", [])}
        return cls(
            queue_url=out["OrdersQueueUrl"],
            dlq_url=out["OrdersDlqUrl"],
            queue_arn=out["OrdersQueueArn"],
            api_url=out["SyncApiUrl"],
            orders_table=out["OrdersTableName"],
            events_table=out["EventsTableName"],
            consumer_function=out["QueueConsumerFunctionName"],
            mapping_id=out["ConsumerMappingId"],
            fault_param=out["FaultParamName"],
        )

    @classmethod
    def from_terraform(cls, terraform_dir: str | Path) -> "StackOutputs":
        """Load outputs from ``terraform output -json`` (preferred live IaC path)."""
        import subprocess

        tf_dir = Path(terraform_dir)
        proc = subprocess.run(
            ["terraform", "output", "-json"],
            cwd=tf_dir,
            check=True,
            capture_output=True,
            text=True,
        )
        raw = json.loads(proc.stdout or "{}")

        def _val(key: str) -> str:
            entry = raw.get(key)
            if entry is None:
                raise KeyError(f"terraform output missing: {key}")
            value = entry.get("value") if isinstance(entry, dict) else entry
            if value is None:
                raise KeyError(f"terraform output {key} is null (Lambda packages missing?)")
            return str(value)

        return cls(
            queue_url=_val("orders_queue_url"),
            dlq_url=_val("orders_dlq_url"),
            queue_arn=_val("orders_queue_arn"),
            api_url=_val("sync_api_url"),
            orders_table=_val("orders_table_name"),
            events_table=_val("events_table_name"),
            consumer_function=_val("consumer_function_name"),
            mapping_id=_val("consumer_mapping_id"),
            fault_param=_val("fault_param_name"),
            sync_function=str((raw.get("sync_function_name") or {}).get("value") or ""),
        )


def apply_queue_settings(sqs, lam, outputs: StackOutputs, spec, wait_s: float = 120.0) -> dict[str, bool]:
    """Set VT / redrive / delay on the queue and batch settings on the mapping.

    v2: only writes what actually differs and says so, so the caller knows
    whether it has to wait for SQS attribute propagation (up to 60 s).
    """
    redrive = {"deadLetterTargetArn": _dlq_arn(sqs, outputs.dlq_url), "maxReceiveCount": int(spec.max_receive_count)}
    wanted = {
        "VisibilityTimeout": str(spec.visibility_timeout),
        "RedrivePolicy": json.dumps(redrive),
        "DelaySeconds": str(spec.delivery_delay),
    }
    current = sqs.get_queue_attributes(
        QueueUrl=outputs.queue_url, AttributeNames=["VisibilityTimeout", "RedrivePolicy", "DelaySeconds"]
    ).get("Attributes", {})

    def _same(key: str) -> bool:
        if key == "RedrivePolicy":
            try:
                cur = json.loads(current.get(key, "{}"))
                return int(cur.get("maxReceiveCount", -1)) == redrive["maxReceiveCount"] and cur.get(
                    "deadLetterTargetArn") == redrive["deadLetterTargetArn"]
            except ValueError:
                return False
        return current.get(key) == wanted[key]

    attrs_changed = not all(_same(k) for k in wanted)
    if attrs_changed:
        sqs.set_queue_attributes(QueueUrl=outputs.queue_url, Attributes=wanted)

    mapping = lam.get_event_source_mapping(UUID=outputs.mapping_id)
    esm_changed = not (
        mapping.get("BatchSize") == int(spec.batch_size)
        and mapping.get("MaximumBatchingWindowInSeconds") == int(spec.batching_window_s)
    )
    if esm_changed:
        lam.update_event_source_mapping(
            UUID=outputs.mapping_id,
            BatchSize=spec.batch_size,
            MaximumBatchingWindowInSeconds=int(spec.batching_window_s),
        )
    deadline = time.time() + wait_s
    while time.time() < deadline:
        state = lam.get_event_source_mapping(UUID=outputs.mapping_id)["State"]
        if state == "Enabled":
            return {"attrs_changed": attrs_changed, "esm_changed": esm_changed}
        time.sleep(5)
    raise TimeoutError("event source mapping did not return to Enabled")


def _dlq_arn(sqs, dlq_url: str) -> str:
    return sqs.get_queue_attributes(QueueUrl=dlq_url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]


def queue_depth(sqs, queue_url: str) -> dict[str, int]:
    attrs = sqs.get_queue_attributes(
        QueueUrl=queue_url,
        AttributeNames=[
            "ApproximateNumberOfMessages",
            "ApproximateNumberOfMessagesNotVisible",
            "ApproximateNumberOfMessagesDelayed",
        ],
    )["Attributes"]
    return {
        "visible": int(attrs.get("ApproximateNumberOfMessages", 0)),
        "inflight": int(attrs.get("ApproximateNumberOfMessagesNotVisible", 0)),
        "delayed": int(attrs.get("ApproximateNumberOfMessagesDelayed", 0)),
    }


def drain_order_ids(sqs, queue_url: str, delete: bool = True, max_empty: int = 3,
                    calls: dict[str, int] | None = None) -> list[str]:
    """Read every message left in a queue and return their order ids."""
    ids: list[str] = []
    empty = 0
    calls = calls if calls is not None else {}
    while empty < max_empty:
        calls["receive"] = calls.get("receive", 0) + 1
        resp = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=10, WaitTimeSeconds=1, VisibilityTimeout=60)
        msgs = resp.get("Messages", [])
        if not msgs:
            empty += 1
            continue
        empty = 0
        for msg in msgs:
            try:
                ids.append(json.loads(msg["Body"])["order_id"])
            except (ValueError, KeyError):
                ids.append("unparseable")
        if delete:
            calls["delete_batch"] = calls.get("delete_batch", 0) + 1
            sqs.delete_message_batch(
                QueueUrl=queue_url,
                Entries=[{"Id": str(i), "ReceiptHandle": m["ReceiptHandle"]} for i, m in enumerate(msgs)],
            )
    return ids


def cloudwatch_counters(cw, function_name: str, queue_name: str, start: float, end: float,
                        sync_function: str | None = None, memory_mb: int = 256) -> dict[str, float]:
    """Request/duration totals for the cost proxy, from CloudWatch.

    v2: the window is the run itself, rounded out to whole minutes (the old
    version added 300 s after the run, which could pull in the next run once
    the data had landed). Per-minute data can still share one boundary minute
    with the neighbouring run, so these are proxies, not invoice lines. The
    sync function and CloudWatch Logs ingestion are now counted too.
    """
    import datetime as dt

    t0 = dt.datetime.fromtimestamp(int(start // 60) * 60, tz=dt.timezone.utc)
    t1 = dt.datetime.fromtimestamp((int(end // 60) + 1) * 60, tz=dt.timezone.utc)
    calls = {"n": 0}

    def total(namespace: str, metric: str, dims: list[dict[str, str]], stat: str = "Sum") -> float:
        calls["n"] += 1
        resp = cw.get_metric_statistics(
            Namespace=namespace, MetricName=metric, Dimensions=dims,
            StartTime=t0, EndTime=t1, Period=60, Statistics=[stat],
        )
        return float(sum(p[stat] for p in resp.get("Datapoints", [])))

    fn = [{"Name": "FunctionName", "Value": function_name}]
    q = [{"Name": "QueueName", "Value": queue_name}]
    gb = memory_mb / 1024.0
    try:
        out = {
            "invocations": total("AWS/Lambda", "Invocations", fn),
            "invocation_error": total("AWS/Lambda", "Errors", fn),
            "lambda_gb_s": total("AWS/Lambda", "Duration", fn) / 1000.0 * gb,
            # every ReceiveMessage call returns 0..10 messages; empty receives
            # are counted by SQS itself, full ones are bounded by messages/10
            "sqs_receive_calls": total("AWS/SQS", "NumberOfEmptyReceives", q)
            + total("AWS/SQS", "NumberOfMessagesReceived", q) / 10.0,
            "delete_batch_calls": total("AWS/SQS", "NumberOfMessagesDeleted", q) / 10.0,
            "logs_bytes": total("AWS/Logs", "IncomingBytes",
                                [{"Name": "LogGroupName", "Value": f"/aws/lambda/{function_name}"}]),
        }
        if sync_function:
            sfn = [{"Name": "FunctionName", "Value": sync_function}]
            out["sync_invocations"] = total("AWS/Lambda", "Invocations", sfn)
            out["sync_errors"] = total("AWS/Lambda", "Errors", sfn)
            out["sync_lambda_gb_s"] = total("AWS/Lambda", "Duration", sfn) / 1000.0 * gb
            out["logs_bytes"] += total("AWS/Logs", "IncomingBytes",
                                       [{"Name": "LogGroupName", "Value": f"/aws/lambda/{sync_function}"}])
        out["cloudwatch_api_calls"] = calls["n"]
        out["cw_window"] = [t0.timestamp(), t1.timestamp()]
        return out
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] cloudwatch counters unavailable: {exc}")
        return {"cloudwatch_api_calls": calls["n"]}


def _is_empty(sqs, url: str) -> bool:
    d = queue_depth(sqs, url)
    return d["visible"] + d["inflight"] + d["delayed"] == 0


def run_live(spec, outputs: StackOutputs, clients: dict[str, Any] | None = None, cooldown_s: float = 61.0) -> RunResult:
    if clients is None:
        import boto3

        clients = {name: boto3.client(name) for name in ("sqs", "lambda", "ssm", "dynamodb", "cloudwatch")}
    sqs = clients["sqs"]
    faults = SsmFaultController(outputs.fault_param, clients["ssm"])
    faults.disable()
    runner_sqs = {"get_attributes": 0, "set_attributes": 0, "purge": 0, "receive": 0, "delete_batch": 0}
    setup: dict[str, Any] = {}

    if spec.arm == "queue":
        changed = apply_queue_settings(sqs, clients["lambda"], outputs, spec)
        runner_sqs["get_attributes"] += 2
        runner_sqs["set_attributes"] += int(changed["attrs_changed"])
        # v2: purge only when something is left over (the collector drains both
        # queues at the end of every run, so normally nothing is). One wait of
        # 61 s covers both the purge and SQS attribute propagation.
        empty = _is_empty(sqs, outputs.queue_url) and _is_empty(sqs, outputs.dlq_url)
        runner_sqs["get_attributes"] += 2
        purged = False
        if not empty:
            sqs.purge_queue(QueueUrl=outputs.queue_url)
            sqs.purge_queue(QueueUrl=outputs.dlq_url)
            runner_sqs["purge"] += 2
            purged = True
        if purged or changed["attrs_changed"]:
            time.sleep(cooldown_s)
        setup = {**changed, "purged": purged, "waited_s": cooldown_s if (purged or changed["attrs_changed"]) else 0.0}

    orders = make_orders(spec.order_count, spec.run_id, spec.seed, spec.poison_rate)
    offsets = arrival_times(
        spec.order_count, spec.load_profile, spec.rate_per_sec, spec.seed,
        burst_start=spec.burst_start_s, burst_len=spec.burst_len_s, burst_factor=spec.burst_factor,
    )
    run_start = time.time()
    faults.enable(schedule_for_run(spec.fault_mode, spec.fault_rate, run_start, spec.fault_start_s, spec.fault_window_s, spec.fault_point))

    produced: dict[str, float] = {}
    sync_results: list[dict[str, Any]] = []
    send_calls = {"n": 0}

    def producer() -> None:
        if spec.arm == "queue":
            produced.update(send_to_sqs(sqs, outputs.queue_url, orders, offsets, delay_s=spec.delivery_delay,
                                        calls=send_calls))
        else:
            sync_results.extend(post_to_api(outputs.api_url, orders, offsets, retries=spec.sync_client_retries,
                                            backoff_s=spec.sync_backoff_s))

    sender = threading.Thread(target=producer, daemon=True)
    sender.start()

    samples: list[dict[str, float]] = []
    horizon = run_start + max(offsets[-1], spec.fault_start_s + spec.fault_window_s) + spec.drain_timeout_s
    zero_streak = 0
    while time.time() < horizon:
        if spec.arm == "queue":
            samples.append({"t": time.time() - run_start, **queue_depth(sqs, outputs.queue_url)})
            runner_sqs["get_attributes"] += 1
            backlog = samples[-1]["visible"] + samples[-1]["inflight"] + samples[-1]["delayed"]
            zero_streak = zero_streak + 1 if backlog == 0 else 0
            # v2: stop only after two empty samples in a row, past the window
            if (not sender.is_alive() and zero_streak >= 2
                    and time.time() - run_start > spec.fault_start_s + spec.fault_window_s + 30):
                break
        elif not sender.is_alive():
            break
        time.sleep(spec.sample_interval_s)
    sender.join(timeout=120)
    run_end = time.time()
    faults.disable()

    if spec.arm == "sync":
        produced = {r["order_id"]: r["produced_at"] for r in sync_results}

    from common.dynamo import DynamoEventLog

    events = DynamoEventLog(outputs.events_table, clients["dynamodb"]).query_run(spec.run_id)
    for ev in events:  # make times relative to the run start like the simulator
        ev["ts"] -= run_start
    produced = {k: v - run_start for k, v in produced.items()}

    dlq_ids: list[str] = []
    remaining: list[str] = []
    if spec.arm == "queue":
        drain_calls: dict[str, int] = {"receive": 0, "delete_batch": 0}
        remaining = drain_order_ids(sqs, outputs.queue_url, calls=drain_calls)
        dlq_ids = drain_order_ids(sqs, outputs.dlq_url, calls=drain_calls)
        runner_sqs["receive"] += drain_calls["receive"]
        runner_sqs["delete_batch"] += drain_calls["delete_batch"]

    counters = cloudwatch_counters(
        clients["cloudwatch"], outputs.consumer_function, outputs.queue_url.rsplit("/", 1)[-1], run_start, run_end,
        sync_function=outputs.sync_function if spec.arm == "sync" else None,
    )
    # v2 cost fix: every conditional put on the orders table is a billed write,
    # whether it creates the order (first success) or is refused because the
    # order exists (duplicate). v1 only counted the event-log writes.
    write_outcomes = {"first_success", "duplicate_success", "unsafe_double_apply"}
    counters["dynamo_order_writes"] = sum(1 for ev in events if ev["outcome"] in write_outcomes)
    counters["dynamo_event_writes"] = len(events)
    # consistent Query reads: 1 RCU per 4 KB, event rows are ~0.3 KB
    counters["dynamo_read_units"] = max(1.0, len(events) * 0.3 / 4.0)
    counters["sqs_send_batch_calls"] = float(send_calls["n"]) if spec.arm == "queue" else 0.0
    counters["runner_sqs_calls"] = float(sum(runner_sqs.values()))
    counters["runner_sqs_breakdown"] = runner_sqs
    if spec.arm == "sync":
        counters["api_requests"] = float(sum(r["attempts"] for r in sync_results))
        counters["sync_failed_orders"] = float(sum(1 for r in sync_results if r["status"] != 200))
    counters["setup"] = setup
    counters["run_start_epoch"] = run_start
    counters["run_end_epoch"] = run_end

    window = spec.fault_window
    result = RunResult(
        produced=produced,
        events=events,
        dlq_order_ids=dlq_ids,
        remaining_ids=remaining,
        samples=samples,
        fault_window=window,
        counters=counters,
        end_time=run_end - run_start,
        max_apply_count=0,
    )
    result.sync_results = [{k: v for k, v in r.items() if k != "body"} for r in sync_results]
    for r in result.sync_results:
        r["produced_at"] -= run_start
        r["done_at"] -= run_start
    return result
