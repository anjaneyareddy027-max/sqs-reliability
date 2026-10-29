"""Figures and tests for the final live AWS round (configs/live_final_n5.yaml)."""
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats

src = Path(sys.argv[1])
out = Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
df = pd.read_csv(src / "summary.csv")


def cell(r):
    if r.fault_mode == "none":
        return "No fault"
    if r.fault_mode == "consumer_kill":
        return f"Kill, VT {r.visibility_timeout}"
    return f"Error, MRC {r.max_receive_count}"


df["cell"] = df.apply(cell, axis=1)
order = ["No fault", "Kill, VT 30", "Kill, VT 90", "Error, MRC 1", "Error, MRC 5"]
df["cell"] = pd.Categorical(df["cell"], order, ordered=True)
df = df.sort_values(["cell", "repeat"])
df.to_csv(out / "live_runs.csv", index=False)

cols = ["loss_rate", "duplicate_rate", "dlq_capture_rate", "success_rate",
        "recovery_time_visible_s", "throughput_msg_s", "latency_p50_s", "latency_p95_s", "usd_total"]
desc = df.groupby("cell", observed=True)[cols].agg(["mean", "std", "count"])
desc.to_csv(out / "cell_summary.csv")


def mw(a, b):
    a, b = np.asarray(a.dropna()), np.asarray(b.dropna())
    if len(a) < 2 or len(b) < 2:
        return None
    if np.all(a == a[0]) and np.all(b == b[0]) and a[0] == b[0]:
        return 1.0
    return float(stats.mannwhitneyu(a, b, alternative="two-sided").pvalue)


def pick(name, col):
    return df.loc[df["cell"] == name, col]


tests = [
    ("H1", "recovery after kill, VT 30 vs VT 90", "recovery_time_visible_s", "Kill, VT 30", "Kill, VT 90"),
    ("H1", "duplicates after kill, VT 30 vs VT 90", "duplicate_rate", "Kill, VT 30", "Kill, VT 90"),
    ("H1", "throughput after kill, VT 30 vs VT 90", "throughput_msg_s", "Kill, VT 30", "Kill, VT 90"),
    ("H2", "DLQ capture, MRC 1 vs MRC 5", "dlq_capture_rate", "Error, MRC 1", "Error, MRC 5"),
    ("H2", "success, MRC 1 vs MRC 5", "success_rate", "Error, MRC 1", "Error, MRC 5"),
    ("H3", "p95 latency, no fault vs kill VT 30", "latency_p95_s", "No fault", "Kill, VT 30"),
    ("H3", "p95 latency, no fault vs error MRC 5", "latency_p95_s", "No fault", "Error, MRC 5"),
    ("H3", "throughput, no fault vs kill VT 30", "throughput_msg_s", "No fault", "Kill, VT 30"),
    ("H3", "success, no fault vs error MRC 1", "success_rate", "No fault", "Error, MRC 1"),
]
rows = []
for h, label, col, a, b in tests:
    p = mw(pick(a, col), pick(b, col))
    rows.append({"hyp": h, "test": label, "metric": col, "a": a, "b": b,
                 "mean_a": float(pick(a, col).mean()), "mean_b": float(pick(b, col).mean()),
                 "n_a": int(pick(a, col).notna().sum()), "n_b": int(pick(b, col).notna().sum()), "p": p})
res = pd.DataFrame(rows)
# Holm over all tests that produced a p value
valid = res["p"].notna()
ps = res.loc[valid, "p"].values
idx = np.argsort(ps)
adj = np.empty_like(ps)
running = 0.0
for rank, i in enumerate(idx):
    running = max(running, (len(ps) - rank) * ps[i])
    adj[i] = min(1.0, running)
res.loc[valid, "p_holm"] = adj
res["significant"] = res["p_holm"] < 0.05
res.to_csv(out / "stats_tests.csv", index=False)

# Fisher on pooled message counts for DLQ capture
n_msgs = 200
m1 = pick("Error, MRC 1", "dlq_capture_rate") * n_msgs
m5 = pick("Error, MRC 5", "dlq_capture_rate") * n_msgs
tab = [[int(round(m1.sum())), int(n_msgs * len(m1) - round(m1.sum()))],
       [int(round(m5.sum())), int(n_msgs * len(m5) - round(m5.sum()))]]
fisher_p = float(stats.fisher_exact(tab).pvalue)
json.dump({"runs": int(len(df)), "fisher_dlq_table": tab, "fisher_dlq_p": fisher_p,
           "usd_sum": float(df["usd_total"].sum())}, open(out / "extra_stats.json", "w"), indent=2)

plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False})
colours = ["#4d4d4d", "#2b6f97", "#7fb3d5", "#b5542c", "#e3a07a"]


def strip(col, ylabel, fname, title):
    fig, ax = plt.subplots(figsize=(6.4, 3.6))
    for i, c in enumerate(order):
        v = pick(c, col).dropna().values
        ax.scatter(np.full(len(v), i) + np.linspace(-0.12, 0.12, len(v)) if len(v) else [], v,
                   color=colours[i], s=28, zorder=3)
        if len(v):
            ax.hlines(v.mean(), i - 0.25, i + 0.25, color="black", lw=1.4)
    ax.set_xticks(range(len(order)), order, rotation=15)
    ax.set_ylabel(ylabel)
    ax.set_title(title, fontsize=10)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out / fname, dpi=300)
    plt.close(fig)


strip("dlq_capture_rate", "Share of orders sent to DLQ", "fig_dlq.png", "DLQ capture per run (bar = mean)")
strip("duplicate_rate", "Share of orders processed twice", "fig_dup.png", "Duplicate processing per run")
strip("recovery_time_visible_s", "Seconds until backlog cleared", "fig_recovery.png", "Recovery time after the fault window")
strip("latency_p95_s", "p95 end-to-end latency (s)", "fig_p95.png", "Tail latency per run")
strip("success_rate", "Share of orders stored", "fig_success.png", "Success rate per run")
strip("throughput_msg_s", "Messages per second", "fig_thr.png", "Throughput per run")
print(res[["hyp", "test", "mean_a", "mean_b", "p", "p_holm"]].to_string())
print("fisher", tab, fisher_p, "usd", df["usd_total"].sum())

# backlog over time, first repeat of each cell
fig, ax = plt.subplots(figsize=(6.4, 3.4))
for i, c in enumerate(order):
    rid = df.loc[(df["cell"] == c)].iloc[0]["run_id"]
    smp = pd.read_csv(src / "raw" / rid / "samples.csv")
    ax.plot(smp["t"], smp["visible"] + smp["inflight"], color=colours[i], lw=1.2, label=c)
ax.axvspan(20, 50, color="#f2c94c", alpha=0.25, lw=0)
ax.text(35, ax.get_ylim()[1] * 0.92, "fault window", ha="center", fontsize=8)
ax.set_xlabel("Seconds since run start")
ax.set_ylabel("Messages in queue (visible + in flight)")
ax.legend(fontsize=7.5, frameon=False, ncol=2)
ax.grid(alpha=0.3)
fig.tight_layout()
fig.savefig(out / "fig_backlog.png", dpi=300)
