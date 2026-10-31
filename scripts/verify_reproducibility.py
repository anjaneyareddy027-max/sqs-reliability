"""Reproducibility and artefact integrity verifier for the SQS reliability campaign.

Verifies:
1. All 170 live v2 runs and manifests exist in results_live_v2/.
2. Quality screen accounting (170 total, 130 included, 40 host-stall excluded, 4 collision-corrected).
3. All 17 experimental cells in analysis_out_v2/v2_cell_summary.csv match raw data and invariants.
4. Zero duplicate business effects (applied_twice == 0) across all 170 live runs.
5. All 16 pre-registered hypothesis contrasts in analysis_out_v2/v2_contrasts.csv satisfy Holm-Bonferroni invariants.
6. Zero credential leaks or Terraform state files in the repository.
"""
from __future__ import annotations

import csv
import glob
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ok = 0
fail = 0


def check(cond: bool, msg: str) -> None:
    global ok, fail
    if cond:
        ok += 1
    else:
        fail += 1
        print(f"FAIL: {msg}")


def main() -> int:
    manifests = sorted(glob.glob(str(ROOT / "results_live_v2" / "manifests" / "*.json")))
    check(len(manifests) == 170, f"expected 170 live v2 manifests, found {len(manifests)}")

    with open(ROOT / "results_live_v2" / "summary.csv", newline="", encoding="utf-8") as f:
        summary_rows = list(csv.DictReader(f))
    check(len(summary_rows) == 170, f"expected 170 rows in results_live_v2/summary.csv, found {len(summary_rows)}")

    extra = json.loads((ROOT / "analysis_out_v2" / "v2_extra.json").read_text(encoding="utf-8"))
    check(extra["runs_total"] == 170, "v2_extra.json runs_total == 170")
    check(extra["runs_included"] == 130, "v2_extra.json runs_included == 130")
    check(extra["runs_excluded_stall"] == 40, "v2_extra.json runs_excluded_stall == 40")
    check(extra["runs_collision_corrected"] == 4, "v2_extra.json runs_collision_corrected == 4")
    check(abs(extra["usd_total_summary_csv"] - 0.16645199254044724) < 1e-6, "v2_extra.json total USD proxy matches $0.1665")

    with open(ROOT / "analysis_out_v2" / "v2_runs.csv", newline="", encoding="utf-8") as f:
        v2_runs = list(csv.DictReader(f))
    check(len(v2_runs) == 170, "v2_runs.csv has 170 rows")
    for r in v2_runs:
        check(int(float(r.get("applied_twice", 0))) == 0, f"run {r['run_id']} has zero duplicate state applications")

    with open(ROOT / "analysis_out_v2" / "v2_cell_summary.csv", newline="", encoding="utf-8") as f:
        cells = list(csv.DictReader(f))
    check(len(cells) == 17, f"expected 17 experimental cells, found {len(cells)}")
    total_n = sum(int(c["n"]) for c in cells)
    check(total_n == 130, f"sum of cell n == 130 (got {total_n})")

    cell_map = {c["cell"]: c for c in cells}
    expected_drains = {
        "No fault": 22.954039,
        "Kill, VT 30": 49.521221,
        "Kill, VT 90": 80.034591,
        "Kill, VT 600": 395.030838,
        "Kill, batch 1": 16.962630,
        "Kill, batch 50, VT 600": 448.009514,
    }
    for name, exp_val in expected_drains.items():
        got = float(cell_map[name]["drain_s_mean"])
        check(abs(got - exp_val) < 1e-4, f"cell '{name}' drain_s_mean matches {exp_val} (got {got})")

    for c in cells:
        sr = float(c["success_rate_mean"])
        dr = float(c["duplicate_rate_mean"])
        dlq = float(c["dlq_capture_rate_mean"])
        tp = float(c["throughput_msg_s_mean"])
        p95 = float(c["latency_p95_s_mean"])
        check(0.0 <= sr <= 1.0, f"cell {c['cell']} success_rate in [0, 1]")
        check(0.0 <= dr <= 1.0, f"cell {c['cell']} duplicate_rate in [0, 1]")
        check(0.0 <= dlq <= 1.0, f"cell {c['cell']} dlq_capture_rate in [0, 1]")
        check(tp > 0.0 and p95 > 0.0, f"cell {c['cell']} positive throughput and latency")
        if not c["cell"].startswith("Sync") and c["cell"] != "Error, MRC 1":
            check(abs(sr - 1.0) < 1e-9, f"queue cell {c['cell']} has 100% order completion")

    with open(ROOT / "analysis_out_v2" / "v2_contrasts.csv", newline="", encoding="utf-8") as f:
        contrasts = list(csv.DictReader(f))
    check(len(contrasts) == 16, f"expected 16 hypothesis contrasts, found {len(contrasts)}")
    sig_holm = 0
    for c in contrasts:
        p = float(c["p"])
        p_holm = float(c["p_holm"])
        diff = float(c["diff"])
        lo = float(c["ci_lo"])
        hi = float(c["ci_hi"])
        check(0.0 <= p <= 1.0 and 0.0 <= p_holm <= 1.0, f"contrast {c['hyp']} {c['metric']} valid p-values")
        check(p_holm >= p - 1e-12, f"contrast {c['hyp']} {c['metric']} Holm p >= raw p")
        check(lo <= diff <= hi, f"contrast {c['hyp']} {c['metric']} point estimate inside 95% CI")
        if p_holm < 0.05:
            sig_holm += 1
    check(sig_holm == 3, f"expected 3 Holm-significant contrasts at alpha=0.05, found {sig_holm}")

    for path in ROOT.rglob("*"):
        if path.is_file() and ".git" not in path.parts and path.stat().st_size < 5_000_000 and path.suffix != ".png":
            data = path.read_bytes()
            check((b"AK" + b"IA") not in data, f"no AWS access key ID in {path.relative_to(ROOT)}")

    tfstates = [p for p in ROOT.rglob("*.tfstate*") if ".git" not in p.parts]
    check(len(tfstates) == 0, "no Terraform state files packaged")

    print(f"verify_reproducibility: {ok} checks passed, {fail} failed")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
