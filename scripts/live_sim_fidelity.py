#!/usr/bin/env python
"""Compare live lite n=1 key-cells to matching localsim rows (no new AWS).

Not confirmatory live n>1. Reports direction agreement and where absolute
recovery times differ because live used 200 orders vs localsim campaign ~3600.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / "results/live/key_cells/summary.csv"
SIM = ROOT / "results/campaigns/summary.csv"


def _pctile(sim: np.ndarray, live: float) -> float | None:
    sim = sim[np.isfinite(sim)]
    if live is None or (isinstance(live, float) and math.isnan(live)) or len(sim) == 0:
        return None
    return float((sim <= live).mean())


def match_sim(sim: pd.DataFrame, fault: str, vt: int, mrc: int) -> tuple[pd.DataFrame, str, int]:
    """Exact (fault, VT, MRC, queue) else nearest VT among same fault/MRC."""
    base = sim[(sim["fault_mode"] == fault) & (sim["max_receive_count"] == mrc) & (sim["arm"] == "queue")]
    exact = base[base["visibility_timeout"] == vt].drop_duplicates("run_id")
    if len(exact):
        return exact, "exact", vt
    if base.empty:
        return exact, "unmatched", vt
    vts = sorted(base["visibility_timeout"].unique())
    nearest = min(vts, key=lambda v: (abs(int(v) - vt), int(v)))
    near = base[base["visibility_timeout"] == nearest].drop_duplicates("run_id")
    return near, f"nearest_vt={int(nearest)}", int(nearest)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--live", default=str(LIVE))
    ap.add_argument("--sim", default=str(SIM))
    ap.add_argument("--out", default=str(ROOT / "results/live/key_cells/fidelity.json"))
    args = ap.parse_args(argv)
    live = pd.read_csv(args.live)
    sim = pd.read_csv(args.sim)
    rows = []
    for r in live.itertuples(index=False):
        s, match, sim_vt = match_sim(sim, r.fault_mode, int(r.visibility_timeout), int(r.max_receive_count))
        rec_live = None if pd.isna(r.recovery_time_s) else float(r.recovery_time_s)
        rec_sim = s["recovery_time_s"].to_numpy(float) if len(s) else np.array([])
        rec_sim = rec_sim[np.isfinite(rec_sim)]
        rows.append({
            "fault_mode": r.fault_mode,
            "visibility_timeout": int(r.visibility_timeout),
            "max_receive_count": int(r.max_receive_count),
            "live_n": 1,
            "sim_n": int(len(s)),
            "sim_visibility_timeout": sim_vt,
            "match": match,
            "live_order_count": 200,
            "sim_campaigns": sorted(s["campaign"].unique().tolist()) if len(s) else [],
            "live_loss": float(r.loss_rate),
            "sim_loss_mean": float(s["loss_rate"].mean()) if len(s) else None,
            "live_dup": float(r.duplicate_rate),
            "sim_dup_mean": float(s["duplicate_rate"].mean()) if len(s) else None,
            "sim_dup_p_live": _pctile(s["duplicate_rate"].to_numpy(float), float(r.duplicate_rate)) if len(s) else None,
            "live_dlq": float(r.dlq_capture_rate),
            "sim_dlq_mean": float(s["dlq_capture_rate"].mean()) if len(s) else None,
            "sim_dlq_p_live": _pctile(s["dlq_capture_rate"].to_numpy(float), float(r.dlq_capture_rate)) if len(s) else None,
            "live_recovery_s": rec_live,
            "sim_recovery_mean_s": float(np.mean(rec_sim)) if len(rec_sim) else None,
            "note": "absolute recovery not scale-matched (live 200 orders vs localsim ~3600)",
        })
    vt30 = next(x for x in rows if x["fault_mode"] == "consumer_kill" and x["visibility_timeout"] == 30)
    vt90 = next(x for x in rows if x["fault_mode"] == "consumer_kill" and x["visibility_timeout"] == 90)
    mrc1 = next(x for x in rows if x["fault_mode"] == "unhandled_error" and x["max_receive_count"] == 1)
    mrc5 = next(x for x in rows if x["fault_mode"] == "unhandled_error" and x["max_receive_count"] == 5)
    direction = {
        "consumer_kill_recovery_vt90_gt_vt30": {
            "live": vt90["live_recovery_s"] is not None and vt30["live_recovery_s"] is not None
                    and vt90["live_recovery_s"] > vt30["live_recovery_s"],
            "sim": (vt90["sim_recovery_mean_s"] or 0) > (vt30["sim_recovery_mean_s"] or 0),
        },
        "unhandled_error_dlq_mrc1_gt_mrc5": {
            "live": mrc1["live_dlq"] > mrc5["live_dlq"],
            "sim": (mrc1["sim_dlq_mean"] or 0) > (mrc5["sim_dlq_mean"] or 0),
        },
        "unhandled_error_dup_mrc5_gt_mrc1": {
            "live": mrc5["live_dup"] > mrc1["live_dup"],
            "sim": (mrc5["sim_dup_mean"] or 0) > (mrc1["sim_dup_mean"] or 0),
        },
        "loss_zero_all_four_live": all(x["live_loss"] == 0.0 for x in rows),
    }
    direction["relative_ranks_agree"] = all(
        direction[k]["live"] == direction[k]["sim"]
        for k in ("consumer_kill_recovery_vt90_gt_vt30",
                  "unhandled_error_dlq_mrc1_gt_mrc5",
                  "unhandled_error_dup_mrc5_gt_mrc1")
    )
    report = {
        "measurement_kind": "live vs localsim fidelity (lite n=1 vs campaign rows)",
        "confirmatory_live": False,
        "cells": rows,
        "direction": direction,
        "claims_not_made": [
            "live confirmatory H1-H3",
            "live n>1",
            "absolute recovery-time equality (order_count mismatch)",
        ],
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    md = out.with_suffix(".md")
    lines = ["# Live lite ↔ localsim fidelity (not confirmatory n>1)", "",
             f"Relative ranks agree: **{direction['relative_ranks_agree']}**.",
             "Absolute recovery seconds are **not** comparable (live 200 orders vs localsim ~3600).",
             "", "| cell | live loss/dup/DLQ/rec | sim n | sim dup mean | sim DLQ mean | sim rec mean |",
             "|---|---|---:|---:|---:|---:|"]
    for x in rows:
        rec = "—" if x["live_recovery_s"] is None else f"{x['live_recovery_s']:.3f}"
        srec = "—" if x["sim_recovery_mean_s"] is None else f"{x['sim_recovery_mean_s']:.3f}"
        sdup = "—" if x["sim_dup_mean"] is None else f"{x['sim_dup_mean']:.4f}"
        sdlq = "—" if x["sim_dlq_mean"] is None else f"{x['sim_dlq_mean']:.4f}"
        match = x.get("match", "exact")
        lines.append(
            f"| {x['fault_mode']} VT{x['visibility_timeout']} MRC{x['max_receive_count']} ({match}) | "
            f"{x['live_loss']}/{x['live_dup']}/{x['live_dlq']}/{rec} | {x['sim_n']} | "
            f"{sdup} | {sdlq} | {srec} |"
        )
    md.write_text("\n".join(lines) + "\n")
    print(json.dumps({"out": str(out), "relative_ranks_agree": direction["relative_ranks_agree"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
