"""Re-analyse the v1 live round (results_live, 14 Oct 2026) with the v2 fixes:
backlog drain time from the raw depth samples and the corrected cost proxy
(orders-table writes counted from the event log; runner SQS calls added).
usage: python analysis/v1_recheck.py results_live analysis_out"""
import csv
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from common.metrics import backlog_drain  # noqa: E402
from control.collect_metrics import DEFAULT_PRICING  # noqa: E402

src, out = Path(sys.argv[1]), Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
rows = []
for p in sorted((src / "manifests").glob("*.json")):
    m = json.loads(p.read_text())
    raw = src / "raw" / m["run_id"]
    samples = [{k: float(v) for k, v in r.items()} for r in csv.DictReader(open(raw / "samples.csv"))]
    produced = [float(r["produced_at"]) for r in csv.DictReader(open(raw / "produced.csv"))]
    events = list(csv.DictReader(open(raw / "events.csv")))
    s = m["spec"]
    ref = s["fault_start_s"] + s["fault_window_s"]
    d, _ = backlog_drain(samples, ref, max(produced))
    order_writes = sum(1 for e in events if e["outcome"] in ("first_success", "duplicate_success", "unsafe_double_apply"))
    # runner SQS calls that v1 did not price: one GetQueueAttributes per depth
    # sample, set_queue_attributes once, two purges; the end-of-run drains are
    # at least 3 empty receives per queue (lower bound)
    runner_calls = len(samples) + 1 + 2 + 6
    extra = order_writes / 1e6 * DEFAULT_PRICING["dynamodb_per_million_writes"] \
        + runner_calls / 1e6 * DEFAULT_PRICING["sqs_per_million_requests"]
    f = s["fault_mode"]
    cell = "No fault" if f == "none" else (f"Kill, VT {s['visibility_timeout']}" if f == "consumer_kill"
                                           else f"Error, MRC {s['max_receive_count']}")
    rows.append({"run_id": m["run_id"], "cell": cell, "drain_s": d, "drain_censored": d is None,
                 "visible_recovery_s": m["metrics"].get("recovery_time_visible_s"),
                 "order_writes_missing": order_writes, "runner_sqs_calls": runner_calls,
                 "usd_v1": m["cost"]["usd_total"], "usd_corrected": m["cost"]["usd_total"] + extra})
df = pd.DataFrame(rows)
df.to_csv(out / "v1_recheck_runs.csv", index=False)
g = df.groupby("cell").agg(n=("drain_s", "size"), drain_mean=("drain_s", "mean"), drain_sd=("drain_s", "std"),
                           visible_mean=("visible_recovery_s", "mean"))
print(g.to_string())
tot = {"runs": len(df), "usd_v1": df["usd_v1"].sum(), "usd_corrected": df["usd_corrected"].sum(),
       "order_writes_missing": int(df["order_writes_missing"].sum()), "runner_sqs_calls": int(df["runner_sqs_calls"].sum()),
       "censored": int(df["drain_censored"].sum())}
print(tot)
(out / "v1_recheck.json").write_text(json.dumps({"cells": g.reset_index().to_dict("records"), **tot}, indent=2, default=float))
