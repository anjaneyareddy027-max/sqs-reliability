"""v2 analysis: run-level inference for the live v2 round (configs/live_v2.yaml).

The unit of analysis is the run. Every statistic below is computed from one
number per run, so messages inside a run are never treated as independent
(the v1 pooled Fisher test did that and is dropped).

* per cell: mean and a 95 % percentile bootstrap CI over runs (10,000 resamples)
* contrasts: difference in means, bootstrap CI (runs resampled within each
  cell), two-sided permutation p value over run labels (20,000 permutations),
  Holm correction over the whole pre-registered family
* censored drain times (backlog not empty at the horizon) are kept at the
  horizon value and flagged; none is silently dropped

usage: python analysis/v2_analysis.py <results_dir> <out_dir>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v2_quality import load_run  # noqa: E402

B = 10_000
PERM = 20_000
RNG = np.random.default_rng(20261023)

src = Path(sys.argv[1])
out = Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)


def label(m: dict) -> str:
    s = m["spec"]
    f = s["fault_mode"]
    if s["arm"] == "sync":
        return "Sync, no fault" if f == "none" else "Sync, kill"
    if s["load_profile"] == "burst":
        return "Burst, no fault" if f == "none" else "Burst, kill"
    if f == "datastore_reject":
        return "Store reject"
    if f == "datastore_timeout":
        return "Store timeout"
    bs, vt, mrc = s["batch_size"], s["visibility_timeout"], s["max_receive_count"]
    if f == "none":
        return "No fault" if bs == 10 else f"No fault, batch {bs}"
    if f == "unhandled_error":
        return f"Error, MRC {mrc}"
    if bs == 10:
        return f"Kill, VT {vt}"
    return f"Kill, batch {bs}" + (f", VT {vt}" if vt != 30 else "")


ORDER = ["No fault", "Kill, VT 30", "Kill, VT 90", "Kill, VT 600", "Error, MRC 1", "Error, MRC 5",
         "No fault, batch 1", "Kill, batch 1", "No fault, batch 50", "Kill, batch 50", "Kill, batch 50, VT 600",
         "Burst, no fault", "Burst, kill", "Store reject", "Store timeout", "Sync, no fault", "Sync, kill"]

rows = []
for p in sorted((src / "manifests").glob("*.json")):
    m = json.loads(p.read_text())
    qrun = load_run(m, src / "raw")
    s, met, c = m["spec"], qrun["metrics"], qrun["cost"]
    row = {
        "run_id": m["run_id"], "campaign": m["campaign"], "repeat": m["repeat"], "cell": label(m),
        "order_position": m["order_position"], "started_at": m["started_at"],
        "arm": s["arm"], "fault_mode": s["fault_mode"], "vt": s["visibility_timeout"],
        "mrc": s["max_receive_count"], "batch": s["batch_size"], "load": s["load_profile"],
        "orders": s["order_count"],
        "usd_total": c["usd_total"], **{f"usd_{k}": v for k, v in c["usd_breakdown"].items()},
    }
    for k in ["drain_after_window_s", "drain_censored", "drain_censored_at_s", "last_store_after_window_s",
              "recovery_time_s", "recovery_time_visible_s", "loss_rate", "duplicate_rate",
              "dlq_capture_rate", "success_rate", "stranded_rate", "throughput_msg_s",
              "latency_p50_s", "latency_p95_s"]:
        row[k] = met.get(k)
    for k in ["q_collision_events", "q_producer_gap_s", "q_samples", "q_multi_msg_orders",
              "q_sampler_early_stop", "q_stalled", "q_drain_resolution_s", "q_drain_coarse"]:
        row[k] = qrun[k]
    row["manifest_usd_total"] = m["cost"]["usd_total"]
    row["unsafe_double_apply"] = met.get("unsafe_double_apply")
    cn = qrun["counters"]
    row["order_writes"] = cn.get("dynamo_order_writes")
    row["event_writes"] = cn.get("dynamo_event_writes")
    row["api_requests"] = cn.get("api_requests", 0)
    row["sync_failed_orders"] = cn.get("sync_failed_orders", 0)
    rows.append(row)
df_all = pd.DataFrame(rows)
df_all["usd_per_1k_orders"] = df_all["usd_total"] / df_all["orders"] * 1000
df = df_all
for k in ["drain_after_window_s", "drain_censored_at_s", "last_store_after_window_s", "recovery_time_visible_s",
          "recovery_time_s", "latency_p95_s", "latency_p50_s"]:
    df[k] = pd.to_numeric(df[k], errors="coerce")
df = df_all
# censored drains: keep the horizon value as a lower bound
df["drain_s"] = df["drain_after_window_s"]
cens = df["drain_censored"].map(lambda v: v is True or v == "True")
df.loc[cens, "drain_s"] = df.loc[cens, "drain_censored_at_s"]
# the sync arm has no queue: its recovery is the last successful store after the window
df.loc[df["arm"] == "sync", "drain_s"] = df.loc[df["arm"] == "sync", "last_store_after_window_s"].clip(lower=0)
# Q3: drain point not resolved by the samples -> drain unknown for this run
df["drain_coarse_s"] = df["drain_s"]
df.loc[df["q_drain_coarse"] == True, "drain_s"] = np.nan  # noqa: E712
df["included"] = ~df["q_stalled"].astype(bool)
df["cell"] = pd.Categorical(df["cell"], [c for c in ORDER if c in set(df["cell"])], ordered=True)
df = df.sort_values(["cell", "repeat"])
df.to_csv(out / "v2_runs.csv", index=False)
df_all = df
cens_all = cens.copy()
df = df_all[df_all["included"]].copy()
cens = cens_all[df.index]


def boot_mean(x: np.ndarray) -> tuple[float, float]:
    if len(x) == 0:
        return (np.nan, np.nan)
    idx = RNG.integers(0, len(x), size=(B, len(x)))
    means = x[idx].mean(axis=1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


METRICS = ["drain_s", "last_store_after_window_s", "usd_per_1k_orders", "recovery_time_visible_s", "duplicate_rate",
           "dlq_capture_rate", "success_rate", "loss_rate", "throughput_msg_s", "latency_p50_s",
           "latency_p95_s", "usd_total"]
summ = []
for cname, g in df.groupby("cell", observed=True):
    ga = df_all[df_all["cell"] == cname]
    r = {"cell": cname, "n_planned": len(ga), "n": len(g), "excluded_stall": int(ga["q_stalled"].sum()),
         "n_drain": int(g["drain_s"].notna().sum()), "censored": int(cens[g.index].sum()),
         "multi_msg_orders_excluded_runs": int(ga.loc[ga["q_stalled"], "q_multi_msg_orders"].sum()),
         "unsafe_double_apply": int(g["unsafe_double_apply"].fillna(0).sum())}
    for k in METRICS:
        x = g[k].dropna().to_numpy(dtype=float)
        lo, hi = boot_mean(x)
        r[f"{k}_mean"], r[f"{k}_lo"], r[f"{k}_hi"] = (float(x.mean()) if len(x) else np.nan), lo, hi
    summ.append(r)
summ = pd.DataFrame(summ)
summ.to_csv(out / "v2_cell_summary.csv", index=False)

# sensitivity: every run, stalled ones included (collision-corrected)
sens = []
for cname, g in df_all.groupby("cell", observed=True):
    r = {"cell": cname, "n": len(g)}
    for k in ["drain_s", "duplicate_rate", "loss_rate", "dlq_capture_rate", "success_rate", "throughput_msg_s"]:
        x = g[k].dropna().to_numpy(dtype=float)
        r[f"{k}_mean"] = float(x.mean()) if len(x) else np.nan
        r[f"{k}_n"] = len(x)
    sens.append(r)
pd.DataFrame(sens).to_csv(out / "v2_sensitivity_all_runs.csv", index=False)

# cost breakdown per cell, per 1,000 orders (kept runs)
items = [c for c in df.columns if c.startswith("usd_") and c not in ("usd_total", "usd_per_1k_orders")]
cb = df.groupby("cell", observed=True).apply(
    lambda g: pd.Series({i[4:]: (g[i] / g["orders"] * 1000).mean() for i in items}), include_groups=False)
cb["total"] = cb.sum(axis=1)
cb.to_csv(out / "v2_cost_breakdown_per_1k.csv")


def contrast(a: np.ndarray, b: np.ndarray) -> dict:
    diff = float(b.mean() - a.mean())
    ia = RNG.integers(0, len(a), size=(B, len(a)))
    ib = RNG.integers(0, len(b), size=(B, len(b)))
    d = b[ib].mean(axis=1) - a[ia].mean(axis=1)
    pooled = np.concatenate([a, b])
    if np.all(pooled == pooled[0]):
        p = 1.0
    else:
        perms = np.array([RNG.permutation(pooled) for _ in range(PERM)])
        pd_ = perms[:, len(a):].mean(axis=1) - perms[:, :len(a)].mean(axis=1)
        p = float((np.sum(np.abs(pd_) >= abs(diff) - 1e-12) + 1) / (PERM + 1))
    return {"diff": diff, "ci_lo": float(np.percentile(d, 2.5)), "ci_hi": float(np.percentile(d, 97.5)), "p": p}


TESTS = [
    ("H1", "drain_s", "Kill, VT 30", "Kill, VT 90"),
    ("H1", "drain_s", "Kill, VT 30", "Kill, VT 600"),
    ("H1", "duplicate_rate", "Kill, VT 30", "Kill, VT 90"),
    ("H1", "duplicate_rate", "Kill, VT 30", "Kill, VT 600"),
    ("H2", "dlq_capture_rate", "Error, MRC 5", "Error, MRC 1"),
    ("H2", "success_rate", "Error, MRC 5", "Error, MRC 1"),
    ("H3", "drain_s", "No fault", "Kill, VT 30"),
    ("H3", "latency_p95_s", "No fault", "Kill, VT 30"),
    ("H4", "drain_s", "Kill, VT 30", "Kill, batch 50"),
    ("H4", "duplicate_rate", "Kill, VT 30", "Kill, batch 50"),
    ("H4", "drain_s", "Kill, batch 1", "Kill, VT 30"),
    ("H4", "drain_s", "Kill, VT 30", "Kill, batch 50, VT 600"),
    ("H5", "drain_s", "Burst, no fault", "Burst, kill"),
    ("H5", "drain_s", "No fault", "Store timeout"),
    ("H6", "success_rate", "Kill, VT 30", "Sync, kill"),
    ("H6", "latency_p95_s", "No fault", "Sync, no fault"),
]
res = []
for h, k, a, b in TESTS:
    xa = df.loc[df["cell"] == a, k].dropna().to_numpy(dtype=float)
    xb = df.loc[df["cell"] == b, k].dropna().to_numpy(dtype=float)
    if len(xa) < 2 or len(xb) < 2:
        res.append({"hyp": h, "metric": k, "a": a, "b": b, "n_a": len(xa), "n_b": len(xb), "p": np.nan})
        continue
    res.append({"hyp": h, "metric": k, "a": a, "b": b, "n_a": len(xa), "n_b": len(xb),
                "mean_a": float(xa.mean()), "mean_b": float(xb.mean()), **contrast(xa, xb)})
res = pd.DataFrame(res)
ok = res["p"].notna()
ps = res.loc[ok, "p"].to_numpy()
order = np.argsort(ps)
adj = np.empty_like(ps)
run = 0.0
for rank, i in enumerate(order):
    run = max(run, (len(ps) - rank) * ps[i])
    adj[i] = min(1.0, run)
res.loc[ok, "p_holm"] = adj
res["significant"] = res["p_holm"] < 0.05
res.to_csv(out / "v2_contrasts.csv", index=False)

summary_csv = pd.read_csv(src / "summary.csv")
cost = {
    "runs_total": int(len(df_all)),
    "runs_included": int(len(df)),
    "runs_excluded_stall": int(df_all["q_stalled"].sum()),
    "runs_collision_corrected": int((df_all["q_collision_events"] > 0).sum()),
    "runs_drain_coarse_included": int((df["q_drain_coarse"] == True).sum()),  # noqa: E712
    "usd_total_summary_csv": float(summary_csv["usd_total"].sum()),
    "usd_total_corrected": float(df_all["usd_total"].sum()),
    "usd_by_item_corrected": {c[4:]: float(df_all[c].sum()) for c in df_all.columns
                              if c.startswith("usd_") and c not in ("usd_total", "usd_per_1k_orders")},
    "order_writes": int(df_all["order_writes"].sum()),
    "event_writes": int(df_all["event_writes"].sum()),
    "censored_drains_included": int(cens.sum()),
    "first_start": df_all["started_at"].min(),
    "last_start": df_all["started_at"].max(),
}
(out / "v2_extra.json").write_text(json.dumps(cost, indent=2))

# ---------------------------------------------------------------- figures
plt.rcParams.update({"font.size": 9, "axes.spines.top": False, "axes.spines.right": False})
cells = list(df["cell"].cat.categories)


def strip(col, ylabel, fname, subset=None, log=False):
    use = [c for c in cells if subset is None or c in subset]
    fig, ax = plt.subplots(figsize=(7.2, 3.4))
    for i, c in enumerate(use):
        y = df.loc[df["cell"] == c, col].dropna().to_numpy(dtype=float)
        x = i + RNG.uniform(-0.15, 0.15, len(y))
        ax.scatter(x, y, s=12, alpha=0.6, color="#2b6f97")
        if len(y):
            lo, hi = boot_mean(y)
            ax.errorbar(i + 0.3, y.mean(), yerr=[[y.mean() - lo], [hi - y.mean()]], fmt="o", color="#b5542c",
                        capsize=3, ms=4)
    ax.set_xticks(range(len(use)))
    ax.set_xticklabels(use, rotation=35, ha="right")
    ax.set_ylabel(ylabel)
    if log:
        ax.set_yscale("symlog", linthresh=10)
    fig.tight_layout()
    fig.savefig(out / fname, dpi=300)
    plt.close(fig)


strip("drain_s", "Drain time after window (s)", "v2_drain.png", log=True)
strip("duplicate_rate", "Duplicate rate", "v2_duplicates.png",
      subset=[c for c in cells if c.startswith("Kill") or c.startswith("Error") or c.startswith("Burst")
              or c.startswith("Store") or c == "No fault"])
strip("success_rate", "Success rate", "v2_success.png")
strip("throughput_msg_s", "Throughput (orders/s)", "v2_throughput.png")
strip("last_store_after_window_s", "Last order stored after window (s)", "v2_completion.png", log=True)

# cost per 1,000 orders, stacked by item (kept runs)
fig, ax = plt.subplots(figsize=(7.2, 3.4))
groups = {"Lambda": ["lambda_compute", "lambda_requests"], "SQS": ["sqs"],
          "DynamoDB order writes": ["dynamodb_order_writes"], "DynamoDB event writes": ["dynamodb_event_writes"],
          "Other (reads, API, logs, CloudWatch)": ["dynamodb_reads", "api", "logs_ingestion", "cloudwatch_api"]}
bottom = np.zeros(len(cb))
colours = ["#2b6f97", "#7fb3d5", "#b5542c", "#e3a07a", "#9e9e9e"]
for (name, cols), colour in zip(groups.items(), colours):
    v = cb[[c for c in cols if c in cb.columns]].sum(axis=1).to_numpy() * 100  # US cents
    ax.bar(range(len(cb)), v, bottom=bottom, label=name, color=colour)
    bottom += v
ax.set_xticks(range(len(cb)))
ax.set_xticklabels(list(cb.index), rotation=35, ha="right")
ax.set_ylabel("US cents per 1,000 orders")
ax.legend(fontsize=6, frameon=False, ncol=2)
fig.tight_layout()
fig.savefig(out / "v2_cost.png", dpi=300)
plt.close(fig)

# forest plot of the contrasts in their own units: seconds left, percentage points right
NAMES = {"drain_s": "drain", "latency_p95_s": "p95", "duplicate_rate": "duplicates",
         "dlq_capture_rate": "DLQ", "success_rate": "success"}
fig, (axs, axp) = plt.subplots(1, 2, figsize=(7.2, 3.6), gridspec_kw={"width_ratios": [1.3, 1]})
for ax, secs in ((axs, True), (axp, False)):
    sub = res[res["metric"].isin(["drain_s", "latency_p95_s"]) == secs].iloc[::-1].reset_index(drop=True)
    scale = 1.0 if secs else 100.0
    for i, r in sub.iterrows():
        colour = "#b5542c" if r["significant"] else "#7f7f7f"
        ax.errorbar(r["diff"] * scale, i, xerr=[[(r["diff"] - r["ci_lo"]) * scale], [(r["ci_hi"] - r["diff"]) * scale]],
                    fmt="o", color=colour, capsize=2, ms=3)
    ax.set_yticks(range(len(sub)))
    ax.set_yticklabels([f"{r['hyp']} {NAMES[r['metric']]}: {r['b']} vs {r['a']}" for _, r in sub.iterrows()],
                       fontsize=6)
    ax.axvline(0, color="black", lw=0.6)
    if secs:
        ax.set_xscale("symlog", linthresh=10)
        ax.set_xlabel("difference (s)")
    else:
        ax.set_xlabel("difference (pp)")
fig.tight_layout()
fig.savefig(out / "v2_contrasts.png", dpi=300)
plt.close(fig)
print(summ[["cell", "n", "censored", "drain_s_mean", "duplicate_rate_mean", "dlq_capture_rate_mean",
            "success_rate_mean", "throughput_msg_s_mean", "latency_p95_s_mean", "usd_total_mean"]].to_string())
print(res[["hyp", "metric", "a", "b", "mean_a", "mean_b", "diff", "ci_lo", "ci_hi", "p", "p_holm"]].to_string())
print(json.dumps(cost, indent=1))
