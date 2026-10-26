"""Metrics + cost proxy for one run, and raw evidence dumps.

Works on the ``RunResult`` from the simulator and on the equivalent structure
the live backend builds from DynamoDB / SQS / CloudWatch.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import yaml

from common.metrics import compute_run_metrics

# rough USD list prices, only used as a cost *proxy* (see configs/pricing.yaml)
DEFAULT_PRICING = {
    "lambda_per_gb_s": 0.0000166667,
    "lambda_per_million_requests": 0.20,
    "sqs_per_million_requests": 0.40,
    "dynamodb_per_million_writes": 1.25,
    "api_per_million_requests": 1.00,
    # v2 additions (same conservative list-price style as above)
    "dynamodb_per_million_reads": 0.25,
    "logs_per_gb_ingested": 0.57,
    "cloudwatch_per_thousand_api_requests": 0.01,
}


def load_pricing(path: str | Path | None = None) -> dict[str, float]:
    if path and Path(path).exists():
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        return {**DEFAULT_PRICING, **{k: float(v) for k, v in data.get("prices", {}).items()}}
    return dict(DEFAULT_PRICING)


def metrics_from_result(spec, result) -> dict[str, Any]:
    metrics = compute_run_metrics(
        produced=result.produced,
        events=result.events,
        dlq_order_ids=result.dlq_order_ids,
        samples=result.samples,
        fault_window=result.fault_window,
        remaining_ids=result.remaining_ids,
        recovery_kwargs=spec.recovery,
        nominal_window=(float(spec.fault_start_s), float(spec.fault_start_s + spec.fault_window_s)),
    )
    # >1 means an order was really applied twice (only possible without idempotency)
    metrics["max_apply_count"] = result.max_apply_count
    return metrics


def estimate_cost(counters: dict[str, float], pricing: dict[str, float] | None = None) -> dict[str, Any]:
    """Cost proxy from request counters and list prices (free tier ignored).

    v2 corrections: orders-table writes are now counted by the collector (v1
    left them at 0), and the runner's own SQS calls, the event-log reads, the
    sync function, CloudWatch Logs ingestion and the CloudWatch API calls the
    collector makes are priced too. Still a proxy, not an AWS invoice.
    """
    p = {**DEFAULT_PRICING, **(pricing or {})}
    sqs_requests = (
        counters.get("sqs_send_batch_calls", 0)
        + counters.get("sqs_receive_calls", 0)
        + counters.get("delete_batch_calls", 0)
        + counters.get("sqs_moved_to_dlq", 0)
        + counters.get("runner_sqs_calls", 0)
    )
    lambda_requests = counters.get("invocations", 0) + counters.get("sync_invocations", 0)
    gb_s = counters.get("lambda_gb_s", 0.0) + counters.get("sync_lambda_gb_s", 0.0)
    order_writes = counters.get("dynamo_order_writes", 0)
    event_writes = counters.get("dynamo_event_writes", 0)
    ddb_writes = order_writes + event_writes
    ddb_reads = counters.get("dynamo_read_units", 0)
    api_requests = counters.get("api_requests", 0)
    logs_gb = counters.get("logs_bytes", 0.0) / 1e9
    cw_calls = counters.get("cloudwatch_api_calls", 0)
    usd = {
        "lambda_compute": gb_s * p["lambda_per_gb_s"],
        "lambda_requests": lambda_requests / 1e6 * p["lambda_per_million_requests"],
        "sqs": sqs_requests / 1e6 * p["sqs_per_million_requests"],
        "dynamodb_order_writes": order_writes / 1e6 * p["dynamodb_per_million_writes"],
        "dynamodb_event_writes": event_writes / 1e6 * p["dynamodb_per_million_writes"],
        "dynamodb_reads": ddb_reads / 1e6 * p["dynamodb_per_million_reads"],
        "api": api_requests / 1e6 * p["api_per_million_requests"],
        "logs_ingestion": logs_gb * p["logs_per_gb_ingested"],
        "cloudwatch_api": cw_calls / 1000.0 * p["cloudwatch_per_thousand_api_requests"],
    }
    return {
        "sqs_requests": sqs_requests,
        "lambda_requests": lambda_requests,
        "lambda_gb_s": gb_s,
        "dynamodb_writes": ddb_writes,
        "dynamodb_order_writes": order_writes,
        "dynamodb_event_writes": event_writes,
        "dynamodb_read_units": ddb_reads,
        "api_requests": api_requests,
        "logs_gb": logs_gb,
        "cloudwatch_api_calls": cw_calls,
        "usd_breakdown": usd,
        "usd_total": sum(usd.values()),
    }


def write_raw(out_dir: str | Path, run_id: str, result) -> Path:
    """events / samples / produced, plus (v2) DLQ ids, leftovers and sync calls."""
    folder = _write_raw_v1(out_dir, run_id, result)
    with open(folder / "dlq.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["order_id"])
        writer.writerows([[o] for o in sorted(result.dlq_order_ids)])
    with open(folder / "remaining.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["order_id"])
        writer.writerows([[o] for o in sorted(result.remaining_ids)])
    sync_rows = getattr(result, "sync_results", None)
    if sync_rows:
        with open(folder / "sync_calls.csv", "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=["order_id", "produced_at", "done_at", "status", "attempts"],
                                    extrasaction="ignore")
            writer.writeheader()
            writer.writerows(sync_rows)
    return folder


def _write_raw_v1(out_dir: str | Path, run_id: str, result) -> Path:
    """events.csv + samples.csv + produced.csv for one run."""
    folder = Path(out_dir) / "raw" / run_id
    folder.mkdir(parents=True, exist_ok=True)
    fields = ["ts", "run_id", "arm", "message_id", "order_id", "receive_count", "outcome", "fault_mode"]
    with open(folder / "events.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(result.events)
    with open(folder / "samples.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["t", "visible", "inflight", "delayed"])
        writer.writeheader()
        writer.writerows(result.samples)
    with open(folder / "produced.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["order_id", "produced_at"])
        writer.writerows(sorted(result.produced.items(), key=lambda kv: kv[1]))
    return folder
