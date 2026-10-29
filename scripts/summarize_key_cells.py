#!/usr/bin/env python3
"""Aggregate per-cell mean/sd from experiment manifests (localsim or live)."""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _num(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and math.isnan(value):
            return None
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def load_manifests(out_dir: Path) -> list[dict]:
    rows = []
    for path in sorted((out_dir / "manifests").glob("*.json")):
        with open(path, encoding="utf-8") as fh:
            m = json.load(fh)
        cell = m.get("cell") or {}
        metrics = m.get("metrics") or {}
        cost = m.get("cost") or {}
        rows.append(
            {
                "run_id": m.get("run_id"),
                "backend": m.get("backend"),
                "campaign": m.get("campaign"),
                "repeat": m.get("repeat"),
                "fault_mode": cell.get("fault_mode"),
                "visibility_timeout": cell.get("visibility_timeout"),
                "max_receive_count": cell.get("max_receive_count"),
                "batch_size": cell.get("batch_size"),
                "max_concurrency": (m.get("spec") or {}).get("max_concurrency", cell.get("max_concurrency")),
                "loss_rate": _num(metrics.get("loss_rate")),
                "duplicate_rate": _num(metrics.get("duplicate_rate")),
                "dlq_capture_rate": _num(metrics.get("dlq_capture_rate")),
                "recovery_time_s": _num(metrics.get("recovery_time_s")),
                "throughput_msg_s": _num(metrics.get("throughput_msg_s")),
                "success_rate": _num(metrics.get("success_rate")),
                "usd_total": _num(cost.get("usd_total")),
                "manifest": str(path.relative_to(ROOT)),
            }
        )
    return rows


def _stats(values: list[float | None]) -> dict:
    xs = [v for v in values if v is not None]
    if not xs:
        return {"n": 0, "mean": None, "sd": None, "min": None, "max": None}
    mean = statistics.fmean(xs)
    sd = statistics.stdev(xs) if len(xs) > 1 else 0.0
    return {
        "n": len(xs),
        "n_missing": len(values) - len(xs),
        "mean": mean,
        "sd": sd,
        "min": min(xs),
        "max": max(xs),
    }


def aggregate(rows: list[dict]) -> list[dict]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        key = (
            row["campaign"],
            row["fault_mode"],
            row["visibility_timeout"],
            row["max_receive_count"],
            row.get("max_concurrency"),
        )
        groups[key].append(row)
    out = []
    for key, items in sorted(groups.items()):
        campaign, fault, vt, mrc, conc = key
        out.append(
            {
                "campaign": campaign,
                "fault_mode": fault,
                "visibility_timeout": vt,
                "max_receive_count": mrc,
                "max_concurrency": conc,
                "n_manifests": len(items),
                "backend": items[0]["backend"],
                "loss_rate": _stats([i["loss_rate"] for i in items]),
                "duplicate_rate": _stats([i["duplicate_rate"] for i in items]),
                "dlq_capture_rate": _stats([i["dlq_capture_rate"] for i in items]),
                "recovery_time_s": _stats([i["recovery_time_s"] for i in items]),
                "throughput_msg_s": _stats([i["throughput_msg_s"] for i in items]),
                "success_rate": _stats([i["success_rate"] for i in items]),
                "run_ids": [i["run_id"] for i in items],
            }
        )
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--in", dest="inputs", action="append", required=True, help="results dir with manifests/")
    p.add_argument("--out", required=True, help="JSON summary path")
    p.add_argument("--csv", help="optional CSV of per-cell stats")
    p.add_argument("--note", action="append", default=[], help="notes stored on the summary")
    args = p.parse_args(argv)

    campaigns = []
    all_rows = []
    for raw in args.inputs:
        d = Path(raw)
        if not d.is_absolute():
            d = ROOT / d
        rows = load_manifests(d)
        all_rows.extend(rows)
        campaigns.append(
            {
                "dir": str(d.relative_to(ROOT)) if ROOT in d.parents or d == ROOT else str(d),
                "runs": len(rows),
                "backend": sorted({r["backend"] for r in rows}),
                "cells": aggregate(rows),
            }
        )

    summary = {
        "kind": "beyond-CA2 key-cell aggregation",
        "inputs": [c["dir"] for c in campaigns],
        "total_manifests": len(all_rows),
        "campaigns": campaigns,
        "notes": args.note,
    }
    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = ROOT / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    if args.csv:
        csv_path = Path(args.csv)
        if not csv_path.is_absolute():
            csv_path = ROOT / csv_path
        fields = [
            "dir",
            "campaign",
            "fault_mode",
            "visibility_timeout",
            "max_receive_count",
            "max_concurrency",
            "n_manifests",
            "backend",
            "loss_mean",
            "dup_mean",
            "dup_sd",
            "dlq_mean",
            "dlq_sd",
            "recovery_mean",
            "recovery_sd",
            "recovery_n",
            "thr_mean",
        ]
        with open(csv_path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=fields)
            w.writeheader()
            for camp in campaigns:
                for cell in camp["cells"]:
                    rec = cell["recovery_time_s"]
                    w.writerow(
                        {
                            "dir": camp["dir"],
                            "campaign": cell["campaign"],
                            "fault_mode": cell["fault_mode"],
                            "visibility_timeout": cell["visibility_timeout"],
                            "max_receive_count": cell["max_receive_count"],
                            "max_concurrency": cell["max_concurrency"],
                            "n_manifests": cell["n_manifests"],
                            "backend": cell["backend"],
                            "loss_mean": (cell["loss_rate"] or {}).get("mean"),
                            "dup_mean": (cell["duplicate_rate"] or {}).get("mean"),
                            "dup_sd": (cell["duplicate_rate"] or {}).get("sd"),
                            "dlq_mean": (cell["dlq_capture_rate"] or {}).get("mean"),
                            "dlq_sd": (cell["dlq_capture_rate"] or {}).get("sd"),
                            "recovery_mean": rec.get("mean"),
                            "recovery_sd": rec.get("sd"),
                            "recovery_n": rec.get("n"),
                            "thr_mean": (cell["throughput_msg_s"] or {}).get("mean"),
                        }
                    )
    print(f"wrote {out_path} ({len(all_rows)} manifests)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
