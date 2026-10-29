"""Write the LaTeX tables of the v2 report from analysis_out_v2 (no number is typed by hand).
usage: python analysis/v2_tables.py analysis_out_v2 ../report/tables"""
import json
import sys
from pathlib import Path

import pandas as pd

src, out = Path(sys.argv[1]), Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
s = pd.read_csv(src / "v2_cell_summary.csv")


def ci(r, m, d=1, sc=1.0):
    return f"{r[m + '_mean'] * sc:.{d}f} \\scriptsize[{r[m + '_lo'] * sc:.{d}f}, {r[m + '_hi'] * sc:.{d}f}]"


lines = []
for _, r in s.iterrows():
    lines.append(" & ".join([
        r["cell"], f"{int(r['n'])}/{int(r['n_drain'])}", ci(r, "drain_s"), ci(r, "last_store_after_window_s"),
        ci(r, "duplicate_rate", 2, 100), ci(r, "throughput_msg_s", 2), ci(r, "latency_p95_s", 1)]) + " \\\\")
(out / "results_main.tex").write_text("\n".join(lines) + "\n\\bottomrule\n")

lines = []
for _, r in s.iterrows():
    lines.append(" & ".join([
        r["cell"], str(int(r["n"])), ci(r, "loss_rate", 2, 100), ci(r, "dlq_capture_rate", 2, 100),
        ci(r, "success_rate", 1, 100), str(int(r["unsafe_double_apply"])), ci(r, "usd_per_1k_orders", 3, 100)]) + " \\\\")
(out / "results_rel.tex").write_text("\n".join(lines) + "\n\\bottomrule\n")

c = pd.read_csv(src / "v2_contrasts.csv")
NAMES = {"drain_s": "drain (s)", "latency_p95_s": "p95 (s)", "duplicate_rate": "duplicates (pp)",
         "dlq_capture_rate": "DLQ (pp)", "success_rate": "success (pp)"}
lines = []
for _, r in c.iterrows():
    sc = 100.0 if NAMES[r["metric"]].endswith("(pp)") else 1.0
    d = 2 if sc == 100.0 else 1
    p = f"{r['p']:.4f}" if r["p"] >= 0.0001 else "$<$0.0001"
    ph = f"{r['p_holm']:.3f}"
    if r["significant"]:
        ph = "\\textbf{" + ph + "}"
    lines.append(f"{r['hyp']} & {NAMES[r['metric']]} & {r['b']} vs {r['a']} & {int(r['n_b'])}/{int(r['n_a'])} & "
                 f"{r['diff'] * sc:+.{d}f} & [{r['ci_lo'] * sc:.{d}f}, {r['ci_hi'] * sc:.{d}f}] & {p} & {ph} \\\\")
(out / "contrasts.tex").write_text("\n".join(lines) + "\n\\bottomrule\n")

cb = pd.read_csv(src / "v2_cost_breakdown_per_1k.csv", index_col=0)
lines = []
for cell, r in cb.iterrows():
    lam = (r["lambda_compute"] + r["lambda_requests"]) * 100
    other = (r["dynamodb_reads"] + r["api"] + r["logs_ingestion"] + r["cloudwatch_api"]) * 100
    lines.append(f"{cell} & {lam:.3f} & {r['sqs'] * 100:.3f} & {r['dynamodb_order_writes'] * 100:.3f} & "
                 f"{r['dynamodb_event_writes'] * 100:.3f} & {other:.3f} & {r['total'] * 100:.3f} \\\\")
(out / "cost.tex").write_text("\n".join(lines) + "\n\\bottomrule\n")

runs = pd.read_csv(src / "v2_runs.csv")
ex = runs[runs["q_stalled"]].sort_values("order_position")
items = []
for _, r in ex.iterrows():
    why = []
    if r["q_producer_gap_s"] > 5:
        why.append(f"G{r['q_producer_gap_s']:.0f}")
    if r["arm"] == "queue" and r["q_samples"] < 3:
        why.append(f"S{int(r['q_samples'])}")
    if r["q_sampler_early_stop"]:
        why.append("E")
    items.append(f"{int(r['order_position']) + 1} & \\texttt{{{r['run_id'][:8]}}} & {r['cell']} & {' '.join(why)} & "
                 f"{int(r['q_multi_msg_orders'])}")
half = (len(items) + 1) // 2
lines = []
for i in range(half):
    right = items[i + half] if i + half < len(items) else "& & & &"
    lines.append(items[i] + " & " + right + " \\\\")
(out / "excluded.tex").write_text("\n".join(lines) + "\n\\bottomrule\n")
x = json.loads((src / "v2_extra.json").read_text())
print(len(ex), x["runs_included"])
