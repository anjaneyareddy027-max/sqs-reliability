"""Figures from the run manifests (+ the raw queue depth samples for the timeline).

    python analysis/plot_results.py --in results/ --out results/figures/

Every figure that is made from simulator data says so in its title.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from load_results import ROOT, floor_duplicate_rate, load_runs, recovery_lower_bound  # noqa: E402

plt.rcParams.update({"figure.dpi": 110, "savefig.dpi": 150, "axes.grid": True, "grid.alpha": 0.3,
                     "axes.spines.top": False, "axes.spines.right": False, "font.size": 9})


def _tag(df: pd.DataFrame) -> str:
    return " (local simulation)" if set(df["backend"]) == {"localsim"} else ""


def _mean_ci(values: np.ndarray) -> tuple[float, float]:
    values = values[~np.isnan(values)]
    if values.size == 0:
        return (np.nan, 0.0)
    if values.size == 1:
        return (float(values[0]), 0.0)
    half = 1.96 * values.std(ddof=1) / np.sqrt(values.size)
    return float(values.mean()), float(half)


def strip_with_mean(ax, df: pd.DataFrame, x: str, y: str, color: str = "C0", label: str | None = None,
                    offset: float = 0.0) -> None:
    levels = sorted(df[x].dropna().unique())
    pos = {lv: i for i, lv in enumerate(levels)}
    rng = np.random.default_rng(0)
    xs = df[x].map(pos).to_numpy(dtype=float) + offset + rng.uniform(-0.08, 0.08, len(df))
    ax.scatter(xs, df[y], s=12, alpha=0.45, color=color)
    means, halves = zip(*[_mean_ci(df.loc[df[x] == lv, y].to_numpy(dtype=float)) for lv in levels])
    ax.errorbar(np.arange(len(levels)) + offset, means, yerr=halves, fmt="o-", color=color, capsize=3, lw=1.5,
                label=label)
    ax.set_xticks(range(len(levels)))
    ax.set_xticklabels([str(int(lv)) if isinstance(lv, (int, float, np.integer)) else str(lv) for lv in levels])


def save(fig, out: Path, name: str) -> Path:
    path = out / name
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    print("wrote", path)
    return path


def fig_loss_vs_vt(df, out):
    sub = df[df["campaign"] == "A_vt_consumer_kill"]
    if sub.empty:
        return
    fig, ax = plt.subplots(figsize=(5.5, 3.4))
    strip_with_mean(ax, sub, "visibility_timeout", "loss_rate", "C3", "loss rate")
    strip_with_mean(ax, sub, "visibility_timeout", "stranded_rate", "C7", "stranded at horizon", offset=0.15)
    ax.set_xlabel("visibility timeout (s)")
    ax.set_ylabel("rate (share of produced)")
    ax.set_title("Message loss vs visibility timeout, consumer_kill" + _tag(sub))
    ax.legend(frameon=False)
    save(fig, out, "loss_vs_visibility.png")


def fig_recovery_vs_vt(df, out):
    fig, ax = plt.subplots(figsize=(5.5, 3.4))
    drawn = False
    for camp, color, label in (("A_vt_consumer_kill", "C0", "consumer_kill"),
                               ("D_vt_datastore_timeout", "C1", "datastore_timeout")):
        sub = df[df["campaign"] == camp].copy()
        if sub.empty:
            continue
        sub["rec"] = recovery_lower_bound(sub)
        strip_with_mean(ax, sub, "visibility_timeout", "rec", color, label, offset=0.12 if drawn else 0.0)
        drawn = True
    if not drawn:
        plt.close(fig)
        return
    ax.set_xlabel("visibility timeout (s)")
    ax.set_ylabel("recovery time (s)")
    ax.set_title("Recovery time vs visibility timeout" + _tag(df))
    ax.legend(frameon=False)
    save(fig, out, "recovery_vs_visibility.png")


def fig_by_mrc(df, out, y, ylabel, name, title):
    fig, ax = plt.subplots(figsize=(5.5, 3.4))
    drawn = False
    for camp, color, label in (("B_mrc_unhandled_error", "C2", "unhandled_error"),
                               ("C_mrc_datastore_reject", "C4", "datastore_reject")):
        sub = df[df["campaign"] == camp].copy()
        if sub.empty:
            continue
        sub["_y"] = recovery_lower_bound(sub) if y == "recovery_time_s" else sub[y]
        strip_with_mean(ax, sub, "max_receive_count", "_y", color, label, offset=0.12 if drawn else 0.0)
        drawn = True
    if not drawn:
        plt.close(fig)
        return
    ax.set_xlabel("maxReceiveCount")
    ax.set_ylabel(ylabel)
    ax.set_title(title + _tag(df))
    ax.legend(frameon=False)
    save(fig, out, name)


def fig_dlq_heatmap(df, out):
    sub = df[df["campaign"] == "H_vt_mrc_grid"]
    if sub.empty:
        return
    table = sub.pivot_table(index="max_receive_count", columns="visibility_timeout", values="dlq_capture_rate",
                            aggfunc="mean")
    fig, ax = plt.subplots(figsize=(5, 3.4))
    im = ax.imshow(table.to_numpy(), cmap="Reds", aspect="auto", origin="lower")
    ax.set_xticks(range(len(table.columns)), [str(c) for c in table.columns])
    ax.set_yticks(range(len(table.index)), [str(i) for i in table.index])
    for (i, j), v in np.ndenumerate(table.to_numpy()):
        ax.text(j, i, f"{v:.3f}", ha="center", va="center", fontsize=8)
    ax.set_xlabel("visibility timeout (s)")
    ax.set_ylabel("maxReceiveCount")
    ax.grid(False)
    fig.colorbar(im, ax=ax, label="DLQ capture rate")
    ax.set_title("DLQ capture, unhandled_error" + _tag(sub))
    save(fig, out, "dlq_heatmap.png")


def fig_baseline(df, out):
    sub = df[df["campaign"] == "baseline_vt_batch"]
    if sub.empty:
        return
    fig, axes = plt.subplots(1, 2, figsize=(9, 3.4))
    for vt, g in sub.groupby("visibility_timeout"):
        med = g.groupby("batch_size")["throughput_msg_s"].median()
        axes[0].plot(med.index, med.values, "o-", label=f"VT {int(vt)} s", alpha=0.8)
    axes[0].set_xlabel("batch size")
    axes[0].set_ylabel("throughput (msg/s, median)")
    axes[0].set_title("No-fault throughput" + _tag(sub))
    axes[0].legend(frameon=False, fontsize=7)
    lat = sub.groupby("batch_size")[["latency_p50_s", "latency_p95_s"]].median()
    axes[1].plot(lat.index, lat["latency_p50_s"], "o-", label="p50")
    axes[1].plot(lat.index, lat["latency_p95_s"], "s--", label="p95")
    axes[1].set_xlabel("batch size")
    axes[1].set_ylabel("produce -> processed latency (s)")
    axes[1].set_title("No-fault latency (all VT pooled)")
    axes[1].legend(frameon=False)
    save(fig, out, "baseline_throughput_latency.png")


def fig_arms(df, out):
    sub = df[df["campaign"] == "F_arms_under_fault"]
    if sub.empty:
        return
    modes = ["none", "consumer_kill", "unhandled_error", "datastore_reject", "datastore_timeout"]
    fig, axes = plt.subplots(1, 2, figsize=(9.5, 3.4))
    width = 0.38
    for k, (arm, color) in enumerate((("sync", "C3"), ("queue", "C0"))):
        g = sub[sub["arm"] == arm].groupby("fault_mode")
        loss = g["loss_rate"].mean().reindex(modes)
        lat = g["latency_p95_s"].mean().reindex(modes)
        x = np.arange(len(modes)) + (k - 0.5) * width
        axes[0].bar(x, loss.values, width, color=color, label=arm)
        axes[1].bar(x, lat.values, width, color=color, label=arm)
    for ax in axes:
        ax.set_xticks(range(len(modes)), [m.replace("_", "\n") for m in modes], fontsize=7)
        ax.legend(frameon=False)
    axes[0].set_ylabel("loss rate")
    axes[0].set_title("Loss: sync vs queue arm" + _tag(sub))
    axes[1].set_ylabel("p95 latency (s)")
    axes[1].set_yscale("log")
    axes[1].set_title("p95 latency (log scale)")
    save(fig, out, "arms_by_fault.png")


def fig_guidance(df, out):
    sub = df[df["campaign"] == "E_guidance_transfer"].copy()
    if sub.empty:
        return
    sub["config"] = "VT " + sub["visibility_timeout"].astype(int).astype(str) + " / B " + sub["batch_size"].astype(int).astype(str)
    sub["rec"] = recovery_lower_bound(sub)
    fig, axes = plt.subplots(1, 2, figsize=(9, 3.4))
    strip_with_mean(axes[0], sub, "config", "rec", "C0")
    axes[0].set_ylabel("recovery time (s)")
    axes[0].set_title("Steady-state optimum under consumer_kill" + _tag(sub))
    strip_with_mean(axes[1], sub, "config", "duplicate_rate", "C1")
    axes[1].set_ylabel("duplicate rate")
    axes[1].set_title("Duplicates")
    for ax in axes:
        ax.tick_params(axis="x", labelsize=7)
    save(fig, out, "guidance_transfer.png")


def fig_duplicates(df, out):
    floor = floor_duplicate_rate(df)
    sub = df[(df["fault_mode"] != "none") & (df["arm"] == "queue")]
    if sub.empty or np.isnan(floor):
        return
    g = sub.groupby("fault_mode")["duplicate_rate"].agg(["mean", "std", "count"])
    fig, ax = plt.subplots(figsize=(5.5, 3.4))
    ax.bar(g.index, g["mean"] - floor, yerr=1.96 * g["std"] / np.sqrt(g["count"]), color="C1", capsize=3)
    ax.axhline(0, color="k", lw=0.8)
    ax.set_ylabel(f"duplicate rate minus floor ({floor:.4f})")
    ax.set_title("Excess duplicates by fault type" + _tag(sub))
    ax.tick_params(axis="x", labelsize=7)
    save(fig, out, "duplicates_over_floor.png")


def _read_samples(results_root: Path, run_id: str) -> pd.DataFrame | None:
    for path in results_root.glob(f"**/raw/{run_id}/samples.csv"):
        with open(path, encoding="utf-8") as fh:
            return pd.DataFrame(list(csv.DictReader(fh))).astype(float)
    return None


def fig_timeline(df, out, results_root):
    sub = df[(df["campaign"] == "A_vt_consumer_kill") & (df["repeat"] == 0)]
    picks = [sub[sub["visibility_timeout"] == vt] for vt in (30, 600)]
    if any(p.empty for p in picks):
        return
    fig, axes = plt.subplots(1, 2, figsize=(9.5, 3.2), sharey=True)
    for ax, pick in zip(axes, picks):
        row = pick.iloc[0]
        s = _read_samples(results_root, row["run_id"])
        if s is None:
            plt.close(fig)
            return
        ax.plot(s["t"], s["visible"], label="visible (ApproximateNumberOfMessages)")
        ax.plot(s["t"], s["visible"] + s["inflight"] + s["delayed"], label="backlog (visible + in flight)")
        ax.axvspan(60, 120, color="C3", alpha=0.12, label="fault window")
        ax.set_title(f"VT {int(row['visibility_timeout'])} s, one run" + _tag(df))
        ax.set_xlabel("time since start (s)")
    axes[0].set_ylabel("messages")
    axes[0].legend(frameon=False, fontsize=7)
    save(fig, out, "timeline_visible_vs_backlog.png")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="plot experiment results")
    p.add_argument("--in", dest="inp", default=str(ROOT / "results"))
    p.add_argument("--out", default=str(ROOT / "results" / "figures"))
    args = p.parse_args(argv)
    df = load_runs(args.inp)
    if df.empty:
        print("no manifests found under", args.inp)
        return 1
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    fig_loss_vs_vt(df, out)
    fig_recovery_vs_vt(df, out)
    fig_by_mrc(df, out, "recovery_time_s", "recovery time (s)", "recovery_vs_max_receive_count.png",
               "Recovery time vs maxReceiveCount")
    fig_by_mrc(df, out, "dlq_capture_rate", "DLQ capture rate", "dlq_vs_max_receive_count.png",
               "DLQ capture vs maxReceiveCount")
    fig_dlq_heatmap(df, out)
    fig_baseline(df, out)
    fig_arms(df, out)
    fig_guidance(df, out)
    fig_duplicates(df, out)
    fig_timeline(df, out, Path(args.inp))
    return 0


if __name__ == "__main__":
    sys.exit(main())
