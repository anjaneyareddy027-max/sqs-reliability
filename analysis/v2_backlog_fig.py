"""Backlog over time for the median run of selected cells (v2 round).
usage: python analysis/v2_backlog_fig.py <results_dir> <out_dir>"""
import csv
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

src, out = Path(sys.argv[1]), Path(sys.argv[2])
runs = pd.read_csv(out / "v2_runs.csv")
runs = runs[runs["included"] & runs["drain_s"].notna()]
cells = ["Kill, VT 30", "Kill, VT 90", "Kill, VT 600", "Kill, batch 50, VT 600"]
cells = [c for c in cells if c in set(runs["cell"])]
fig, axes = plt.subplots(1, len(cells), figsize=(7.2, 2.6), sharey=True)
if len(cells) == 1:
    axes = [axes]
for ax, c in zip(axes, cells):
    g = runs[runs["cell"] == c].sort_values("drain_s")
    r = g.iloc[len(g) // 2]
    s = list(csv.DictReader(open(src / "raw" / r["run_id"] / "samples.csv")))
    t = [float(x["t"]) for x in s]
    vis = [float(x["visible"]) for x in s]
    inf = [float(x["inflight"]) for x in s]
    ax.axvspan(20, 50, color="#f2d7c9", lw=0)
    ax.stackplot(t, vis, inf, colors=["#2b6f97", "#e3a07a"], labels=["visible", "in flight"], step="post")
    ev = list(csv.DictReader(open(src / "raw" / r["run_id"] / "events.csv")))
    last = max(float(e["ts"]) for e in ev if e["outcome"] == "first_success")
    ax.axvline(last, color="black", lw=0.8, ls=":")
    ax.set_title(f"{c} (median run)", fontsize=7)
    ax.set_xlabel("time (s)")
    ax.spines[["top", "right"]].set_visible(False)
axes[0].set_ylabel("messages")
axes[0].legend(fontsize=6, frameon=False, loc="upper right")
fig.tight_layout()
fig.savefig(out / "v2_backlog.png", dpi=300)
print("ok", [(c) for c in cells])
