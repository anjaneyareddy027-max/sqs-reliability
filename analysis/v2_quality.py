"""v2 data-quality screen, applied before any statistic is computed.

Rules (fixed before looking at outcome differences between cells, written
24 Oct 2026 after the round finished):

Q1 smoke-test id collision. The four smoke runs of 23 Oct (smoke_v2/) used the
   same deterministic run ids as four campaign runs, so the event-log query for
   those campaign runs also returned the smoke run's rows. Those rows carry
   timestamps before the campaign run started (ts < -1 s relative to the run
   start) and their message ids match smoke_v2/raw/<run>/events.csv exactly.
   Fix: drop events with ts < -1 s; because the smoke run had already created
   the same order ids in the orders table, the campaign run's own first write
   per order was logged as a duplicate, so the first successful write per order
   after the run start is relabelled as the first success; then recompute every metric with the unchanged
   metric code (common.metrics.compute_run_metrics) and the DynamoDB counters
   with the collector's own rule. The run is then kept.

Q2 runner stall. The load generator runs on the experiment host. If the gap
   between two consecutive successful sends exceeds 5 s (the Poisson schedule
   at 10 msg/s never has gaps above about 1 s, and the burst profile is faster),
   or a queue run recorded fewer than 3 depth samples, or the depth sampler
   stopped well before its horizon while messages were still in the queue (the
   loop ends when the host wall clock passes the horizon, so a host pause or
   clock jump ends it early), the host stalled: either the designed load was
   not delivered or the run was collected before the backlog could drain. In these runs the SDK's automatic retries
   after the stall resent whole SendMessageBatch requests, which shows up as
   one order arriving under two SQS message ids. These runs are excluded from
   the primary analysis and reported separately (every run, kept or not, is
   still counted in the campaign cost).

Q3 drain resolution. Drain time is read from the 2 s depth samples. If the
   sample interval that brackets the drain point is longer than 10 s (sampler
   delayed), the drain time is only known to lie in that interval. Such runs
   keep every other metric but are left out of drain-time statistics.

Q4 censoring. A queue run whose backlog never reached zero before the horizon
   is right-censored. Its drain time is kept at the horizon value as a lower
   bound and counted in the 'censored' column.
"""
from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))
from common.metrics import compute_run_metrics  # noqa: E402
from control.collect_metrics import estimate_cost  # noqa: E402

PRODUCER_GAP_S = 5.0
DRAIN_RES_S = 10.0
COLLISION_TS = -1.0
WRITE_OUTCOMES = {"first_success", "duplicate_success", "unsafe_double_apply"}


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def load_run(manifest: dict, raw_dir: Path) -> dict:
    """Return corrected metrics, cost and the quality flags for one run."""
    s = manifest["spec"]
    raw = raw_dir / manifest["run_id"]
    events = _read(raw / "events.csv")
    for e in events:
        e["ts"] = float(e["ts"])
        e["receive_count"] = int(float(e["receive_count"] or 0))
    produced = {r["order_id"]: float(r["produced_at"]) for r in _read(raw / "produced.csv")}
    samples = [{k: float(v) for k, v in r.items()} for r in _read(raw / "samples.csv")]
    dlq = [r["order_id"] for r in _read(raw / "dlq.csv")]
    remaining = [r["order_id"] for r in _read(raw / "remaining.csv")]

    collided = [e for e in events if e["ts"] < COLLISION_TS]
    metrics, cost, counters = manifest["metrics"], manifest["cost"], dict(manifest["counters"])
    if collided:
        events = sorted((e for e in events if e["ts"] >= COLLISION_TS), key=lambda e: e["ts"])
        # the smoke run already created these order ids in the orders table, so
        # the campaign run's own first write per order was logged as a
        # duplicate; relabel the first successful write per order as the first
        # success, later ones stay duplicates
        seen: set[str] = set()
        for e in events:
            if e["outcome"] in ("first_success", "duplicate_success"):
                e["outcome"] = "duplicate_success" if e["order_id"] in seen else "first_success"
                seen.add(e["order_id"])
        fs = manifest.get("fault_schedule") or {}
        window = None if s["fault_mode"] == "none" else (float(fs.get("on_s", s["fault_start_s"])),
                                                          float(fs.get("off_s", s["fault_start_s"] + s["fault_window_s"])))
        metrics = compute_run_metrics(
            produced=produced, events=events, dlq_order_ids=dlq, samples=samples, fault_window=window,
            remaining_ids=remaining, recovery_kwargs=s.get("recovery"),
            nominal_window=(float(s["fault_start_s"]), float(s["fault_start_s"] + s["fault_window_s"])),
        )
        metrics["max_apply_count"] = manifest["metrics"].get("max_apply_count", 0)
        counters["dynamo_order_writes"] = sum(1 for e in events if e["outcome"] in WRITE_OUTCOMES)
        counters["dynamo_event_writes"] = len(events)
        counters["dynamo_read_units"] = max(1.0, len(events) * 0.3 / 4.0)
        cost = estimate_cost(counters)

    # Q2 producer / sampler stall
    if s["arm"] == "sync":
        sends = sorted(float(r["produced_at"]) for r in _read(raw / "sync_calls.csv")) or sorted(produced.values())
    else:
        sends = sorted(produced.values())
    gap = max((b - a for a, b in zip(sends, sends[1:])), default=0.0)
    mids = defaultdict(set)
    for e in events:
        mids[e["order_id"]].add(e["message_id"])
    multi_msg = sum(1 for v in mids.values() if len(v) > 1)
    early_stop = False
    if s["arm"] == "queue" and samples and sends:
        horizon = max(sends[-1], s["fault_start_s"] + s["fault_window_s"]) + s["drain_timeout_s"]
        last = samples[-1]
        backlog = last["visible"] + last["inflight"] + last["delayed"]
        early_stop = backlog > 0 and last["t"] + 3 * s["sample_interval_s"] < horizon
    stalled = gap > PRODUCER_GAP_S or (s["arm"] == "queue" and len(samples) < 3) or early_stop

    # Q3 resolution of the drain point
    res = None
    if s["arm"] == "queue" and samples and sends:
        off = s["fault_start_s"] + s["fault_window_s"]
        for a, b in zip(samples, samples[1:]):
            if b["t"] > off and b["t"] >= sends[-1] and b["visible"] + b["inflight"] + b["delayed"] == 0:
                res = b["t"] - a["t"]
                break
    return {
        "metrics": metrics, "cost": cost, "counters": counters,
        "q_collision_events": len(collided), "q_producer_gap_s": gap, "q_samples": len(samples),
        "q_multi_msg_orders": multi_msg, "q_sampler_early_stop": early_stop, "q_stalled": stalled, "q_drain_resolution_s": res,
        "q_drain_coarse": res is not None and res > DRAIN_RES_S,
    }
