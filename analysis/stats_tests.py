"""Hypothesis tests following configs/analysis_plan.yaml.

    python analysis/stats_tests.py --in results/ --hypotheses H1,H2,H3 --alpha 0.05 --holm
    python analysis/stats_tests.py --in results/baseline --hypotheses H0_throughput_config
    python analysis/stats_tests.py --in results/pilot --power
    python analysis/stats_tests.py --self-check

Procedure (pre-registered):
  * per group Shapiro-Wilk; if every group looks normal -> one-way ANOVA + eta^2,
    otherwise Kruskal-Wallis + epsilon^2
  * recovery time is right-censored sometimes -> always rank based
  * two-group comparisons (H3) -> Mann-Whitney U + rank-biserial r
  * 95 % bootstrap CIs for every group mean
  * Holm-Bonferroni over the confirmatory family (H1, H2, H3_loss, H3_recovery)
  * reject only if adjusted p < alpha AND effect size above the negligible line
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml
from scipy import stats

sys.path.insert(0, str(Path(__file__).resolve().parent))
from load_results import ROOT, floor_duplicate_rate, load_runs, recovery_lower_bound  # noqa: E402

PLAN_PATH = ROOT / "configs" / "analysis_plan.yaml"


# ---------------------------------------------------------------------------
# small stats helpers
# ---------------------------------------------------------------------------

def bootstrap_ci(values, level: float = 0.95, n_resamples: int = 5000, seed: int = 1) -> tuple[float, float]:
    arr = np.asarray([v for v in values if not pd.isna(v)], dtype=float)
    if arr.size == 0:
        return (math.nan, math.nan)
    if arr.size == 1 or np.all(arr == arr[0]):
        return (float(arr[0]), float(arr[0]))
    rng = np.random.default_rng(seed)
    means = rng.choice(arr, size=(n_resamples, arr.size), replace=True).mean(axis=1)
    lo, hi = np.percentile(means, [(1 - level) / 2 * 100, (1 + level) / 2 * 100])
    return (float(lo), float(hi))


def eta_squared(groups: list[np.ndarray]) -> float:
    allv = np.concatenate(groups)
    grand = allv.mean()
    ss_total = ((allv - grand) ** 2).sum()
    if ss_total == 0:
        return 0.0
    ss_between = sum(len(g) * (g.mean() - grand) ** 2 for g in groups)
    return float(ss_between / ss_total)


def epsilon_squared(h: float, n: int) -> float:
    # Tomczak & Tomczak (2014): eps^2 = H / ((n^2 - 1) / (n + 1))
    if n <= 1:
        return 0.0
    return float(h / ((n**2 - 1) / (n + 1)))


def holm(pvalues: dict[str, float]) -> dict[str, float]:
    """Holm-Bonferroni step-down adjusted p-values (monotone, capped at 1)."""
    items = sorted(pvalues.items(), key=lambda kv: kv[1])
    m = len(items)
    adjusted: dict[str, float] = {}
    running = 0.0
    for i, (name, p) in enumerate(items):
        running = max(running, min(1.0, (m - i) * p))
        adjusted[name] = running
    return adjusted


def normal_enough(groups: list[np.ndarray], alpha: float) -> tuple[bool, list[float | None]]:
    pvals: list[float | None] = []
    ok = True
    for g in groups:
        if len(g) < 3 or np.all(g == g[0]):
            pvals.append(None)
            ok = False  # cannot check -> do not assume normality
            continue
        p = float(stats.shapiro(g).pvalue)
        pvals.append(p)
        if p < alpha:
            ok = False
    return ok, pvals


def omnibus(groups: list[np.ndarray], alpha: float = 0.05, force_rank: bool = False) -> dict[str, Any]:
    groups = [np.asarray(g, dtype=float) for g in groups if len(g) > 0]
    allv = np.concatenate(groups) if groups else np.array([])
    if len(groups) < 2 or allv.size == 0 or np.all(allv == allv[0]):
        return {"test": "none (no variance)", "statistic": 0.0, "p": 1.0, "effect": "n/a", "effect_size": 0.0,
                "normality_p": [], "note": "all values identical, nothing to test"}
    is_normal, norm_p = normal_enough(groups, alpha)
    if is_normal and not force_rank:
        res = stats.f_oneway(*groups)
        return {"test": "one-way ANOVA", "statistic": float(res.statistic), "p": float(res.pvalue),
                "effect": "eta_squared", "effect_size": eta_squared(groups), "normality_p": norm_p}
    res = stats.kruskal(*groups)
    return {"test": "Kruskal-Wallis", "statistic": float(res.statistic), "p": float(res.pvalue),
            "effect": "epsilon_squared", "effect_size": epsilon_squared(float(res.statistic), int(allv.size)),
            "normality_p": norm_p}


def two_group(a, b) -> dict[str, Any]:
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    both = np.concatenate([a, b])
    if a.size == 0 or b.size == 0 or np.all(both == both[0]):
        return {"test": "none (no variance)", "statistic": 0.0, "p": 1.0, "effect": "rank_biserial", "effect_size": 0.0}
    res = stats.mannwhitneyu(a, b, alternative="two-sided")
    r = 2.0 * float(res.statistic) / (a.size * b.size) - 1.0  # +1: a always bigger
    return {"test": "Mann-Whitney U", "statistic": float(res.statistic), "p": float(res.pvalue),
            "effect": "rank_biserial", "effect_size": r}


def describe(df: pd.DataFrame, by: str, dv: str, plan: dict[str, Any]) -> list[dict[str, Any]]:
    ci = plan.get("ci", {})
    out = []
    for level, sub in df.groupby(by):
        vals = sub[dv].dropna().to_numpy(dtype=float)
        lo, hi = bootstrap_ci(vals, ci.get("level", 0.95), ci.get("resamples", 5000), ci.get("seed", 1))
        out.append({
            by: level if not isinstance(level, np.generic) else level.item(),
            "n": int(vals.size),
            "mean": float(vals.mean()) if vals.size else math.nan,
            "median": float(np.median(vals)) if vals.size else math.nan,
            "sd": float(vals.std(ddof=1)) if vals.size > 1 else 0.0,
            "ci95": [lo, hi],
        })
    return out


# ---------------------------------------------------------------------------
# hypotheses
# ---------------------------------------------------------------------------

def _dv_values(sub: pd.DataFrame, dv: str) -> pd.Series:
    if dv == "recovery_time_s":
        return recovery_lower_bound(sub, dv)
    return sub[dv]


def test_grouped(df: pd.DataFrame, name: str, h: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    sub = df[df["campaign"] == h["campaign"]].copy()
    if sub.empty:
        return {"name": name, "status": "no data", **h}
    sub["_dv"] = _dv_values(sub, h["dv"])
    groups = [g["_dv"].dropna().to_numpy(dtype=float) for _, g in sub.groupby(h["iv"])]
    res = omnibus(groups, plan["alpha"], force_rank=h["dv"].startswith("recovery"))
    censored = int(sub["recovered"].eq(False).sum()) if "recovered" in sub else 0
    return {"name": name, **h, **res, "censored_runs": censored,
            "groups": describe(sub.assign(**{h["dv"]: sub["_dv"]}), h["iv"], h["dv"], plan)}


def test_optimal_vs_rest(df: pd.DataFrame, name: str, h: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    sub = df[df["campaign"] == h["campaign"]].copy()
    if sub.empty:
        return {"name": name, "status": "no data", **h}
    sub["_dv"] = _dv_values(sub, h["dv"])
    mask = np.ones(len(sub), dtype=bool)
    for key, value in h["optimal"].items():
        mask &= sub[key].to_numpy() == value
    opt = sub.loc[mask, "_dv"].dropna()
    rest = sub.loc[~mask, "_dv"].dropna()
    res = two_group(opt, rest)
    return {"name": name, **h, **res,
            "optimal_mean": float(opt.mean()) if len(opt) else math.nan,
            "rest_mean": float(rest.mean()) if len(rest) else math.nan,
            "optimal_ci95": list(bootstrap_ci(opt)), "rest_ci95": list(bootstrap_ci(rest)),
            "n_optimal": int(len(opt)), "n_rest": int(len(rest))}


def negligible_line(effect: str, plan: dict[str, Any]) -> float:
    return float(plan.get("negligible", {}).get(effect, 0.0))


def run_confirmatory(df: pd.DataFrame, wanted: list[str], plan: dict[str, Any], use_holm: bool) -> list[dict[str, Any]]:
    results = []
    for name, h in plan["confirmatory"].items():
        if not any(name == w or name.startswith(w + "_") for w in wanted):
            continue
        if "optimal" in h:
            results.append(test_optimal_vs_rest(df, name, h, plan))
        else:
            results.append(test_grouped(df, name, h, plan))
    testable = {r["name"]: r["p"] for r in results if "p" in r}
    adjusted = holm(testable) if use_holm else dict(testable)
    for r in results:
        if "p" not in r:
            r["decision"] = "not tested (no data)"
            continue
        r["p_adjusted"] = adjusted[r["name"]]
        big_enough = abs(r["effect_size"]) >= negligible_line(r["effect"], plan) and r["effect_size"] != 0.0
        r["reject_null"] = bool(r["p_adjusted"] < plan["alpha"] and big_enough)
        r["decision"] = "reject H0" if r["reject_null"] else "fail to reject H0"
    return results


def throughput_config(df: pd.DataFrame, plan: dict[str, Any]) -> dict[str, Any]:
    sub = df[df["campaign"] == "baseline_vt_batch"]
    if sub.empty:
        return {"status": "no baseline data"}
    by_batch = test_grouped(df, "H0_throughput_batch",
                            {"campaign": "baseline_vt_batch", "dv": "throughput_msg_s", "iv": "batch_size"}, plan)
    by_vt = test_grouped(df, "H0_throughput_vt",
                         {"campaign": "baseline_vt_batch", "dv": "throughput_msg_s", "iv": "visibility_timeout"}, plan)
    medians = sub.groupby(["visibility_timeout", "batch_size"])["throughput_msg_s"].median()
    best_vt, best_batch = medians.idxmax()
    best_batch_overall = int(sub.groupby("batch_size")["throughput_msg_s"].median().idxmax())
    return {
        "by_batch_size": by_batch,
        "by_visibility_timeout": by_vt,
        "best_cell_by_median": {"visibility_timeout": int(best_vt), "batch_size": int(best_batch),
                                "median_throughput": float(medians.max())},
        "best_batch_size": best_batch_overall,
        "vt_inert": bool(by_vt["p"] >= plan["alpha"] or by_vt["effect_size"] < negligible_line(by_vt["effect"], plan)),
        "duplicate_floor": floor_duplicate_rate(df),
    }


def run_exploratory(df: pd.DataFrame, plan: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for item in plan.get("exploratory", []):
        name = f"X_{item['campaign']}_{item['dv']}_by_{item['iv']}"
        out.append(test_grouped(df, name, item, plan))
    return out


def excess_duplicates(df: pd.DataFrame) -> list[dict[str, Any]]:
    floor = floor_duplicate_rate(df)
    rows = []
    cols = ["campaign", "fault_mode", "visibility_timeout", "max_receive_count", "batch_size", "arm"]
    for key, sub in df[df["fault_mode"] != "none"].groupby(cols):
        rows.append({**dict(zip(cols, [k.item() if isinstance(k, np.generic) else k for k in key])),
                     "duplicate_rate": float(sub["duplicate_rate"].mean()),
                     "excess_over_floor": float(sub["duplicate_rate"].mean() - floor)})
    return rows


# ---------------------------------------------------------------------------
# pilot based repeat check
# ---------------------------------------------------------------------------

def required_repeats(sd: float, mde: float, alpha: float = 0.05, power: float = 0.8) -> int:
    """Per group n for a two-sample comparison (normal approximation)."""
    if mde <= 0:
        raise ValueError("mde must be positive")
    if sd == 0:
        return 2
    z_a = stats.norm.ppf(1 - alpha / 2)
    z_b = stats.norm.ppf(power)
    return int(math.ceil(2 * ((z_a + z_b) * sd / mde) ** 2))


def power_report(df: pd.DataFrame, plan: dict[str, Any], mde_recovery: float = 30.0, mde_loss: float = 0.005,
                 mde_dup: float = 0.02) -> dict[str, Any]:
    """Repeats per cell from the pilot spread.

    The recommendation is sized on the confirmatory DVs (loss rate, recovery
    time). Duplicate rate is only exploratory, so its number is reported next
    to it but does not drive the repeat count.
    """
    fault = df[df["fault_mode"] != "none"]
    cells = fault.groupby(["fault_mode", "visibility_timeout", "max_receive_count", "batch_size"])

    def worst_sd(col: str) -> float:
        if not len(cells):
            return math.nan
        sds = cells[col].std(ddof=1).dropna()
        return float(sds.max()) if len(sds) else 0.0

    rec_sd, loss_sd, dup_sd = worst_sd("recovery_time_s"), worst_sd("loss_rate"), worst_sd("duplicate_rate")
    need_rec = required_repeats(0.0 if math.isnan(rec_sd) else rec_sd, mde_recovery, plan["alpha"])
    need_loss = required_repeats(0.0 if math.isnan(loss_sd) else loss_sd, mde_loss, plan["alpha"])
    need_dup = required_repeats(0.0 if math.isnan(dup_sd) else dup_sd, mde_dup, plan["alpha"])
    recommended = max(3, min(10, max(need_rec, need_loss)))
    return {"max_cell_sd_recovery_s": rec_sd, "max_cell_sd_loss_rate": loss_sd,
            "max_cell_sd_duplicate_rate": dup_sd, "mde_recovery_s": mde_recovery, "mde_loss_rate": mde_loss,
            "mde_duplicate_rate": mde_dup, "needed_for_recovery": need_rec, "needed_for_loss": need_loss,
            "recommended_repeats_confirmatory": recommended,
            "needed_for_duplicates_exploratory": need_dup,
            "plan_repeats": plan.get("repeats_per_cell"),
            "plan_ok": plan.get("repeats_per_cell", 0) >= recommended}


# ---------------------------------------------------------------------------
# self check
# ---------------------------------------------------------------------------

def self_check() -> int:
    rng = np.random.default_rng(0)
    checks = []
    same = [rng.normal(10, 1, 20) for _ in range(3)]
    shifted = [rng.normal(10, 1, 20), rng.normal(10, 1, 20), rng.normal(14, 1, 20)]
    checks.append(("identical groups are not significant", omnibus(same)["p"] > 0.05))
    checks.append(("clear shift is significant", omnibus(shifted)["p"] < 0.001))
    checks.append(("normal data -> ANOVA", omnibus(shifted)["test"] == "one-way ANOVA"))
    skewed = [rng.exponential(1, 25), rng.exponential(1, 25) + 3]
    checks.append(("skewed data -> Kruskal-Wallis", omnibus(skewed)["test"] == "Kruskal-Wallis"))
    checks.append(("constant data handled", omnibus([np.zeros(5), np.zeros(5)])["p"] == 1.0))
    adj = holm({"a": 0.01, "b": 0.03, "c": 0.04})
    checks.append(("holm adjustment", np.allclose([adj["a"], adj["b"], adj["c"]], [0.03, 0.06, 0.06])))
    eps = omnibus(shifted, force_rank=True)["effect_size"]
    checks.append(("epsilon^2 in [0, 1]", 0.0 <= eps <= 1.0))
    eta = eta_squared(shifted)
    checks.append(("eta^2 large for big shift", eta > 0.5))
    r = two_group(np.arange(10) + 100, np.arange(10))["effect_size"]
    checks.append(("rank biserial = +1 when a > b", math.isclose(r, 1.0)))
    lo, hi = bootstrap_ci(rng.normal(5, 1, 200))
    checks.append(("bootstrap CI covers the mean", lo < 5 < hi))
    checks.append(("required repeats grows with sd", required_repeats(20, 10) > required_repeats(5, 10)))

    width = max(len(n) for n, _ in checks)
    ok = True
    for name, passed in checks:
        ok &= bool(passed)
        print(f"  {name:<{width}}  {'ok' if passed else 'FAIL'}")
    print("self-check", "passed" if ok else "FAILED")
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# output
# ---------------------------------------------------------------------------

def _fmt(x: Any, nd: int = 4) -> str:
    if isinstance(x, float):
        return "nan" if math.isnan(x) else f"{x:.{nd}g}"
    return str(x)


def markdown_table(results: list[dict[str, Any]]) -> str:
    lines = [
        "| hypothesis | campaign | dv | test | stat | p | p (Holm) | effect | size | decision |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r['name']} | {r.get('campaign', '')} | {r.get('dv', '')} | {r.get('test', '-')} | "
            f"{_fmt(r.get('statistic', math.nan))} | {_fmt(r.get('p', math.nan))} | "
            f"{_fmt(r.get('p_adjusted', math.nan))} | {r.get('effect', '')} | "
            f"{_fmt(r.get('effect_size', math.nan), 3)} | {r.get('decision', '-')} |"
        )
    return "\n".join(lines)


def _jsonable(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, np.generic):
        obj = obj.item()
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    return obj


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--in", dest="inp", default=str(ROOT / "results"))
    p.add_argument("--hypotheses", default="H1,H2,H3")
    p.add_argument("--alpha", type=float)
    p.add_argument("--holm", action="store_true", help="Holm-Bonferroni over the family (plan default)")
    p.add_argument("--plan", default=str(PLAN_PATH))
    p.add_argument("--out", default=str(ROOT / "results" / "summary"))
    p.add_argument("--power", action="store_true", help="repeat count check from pilot data")
    p.add_argument("--self-check", action="store_true")
    args = p.parse_args(argv)

    if args.self_check:
        return self_check()

    with open(args.plan, encoding="utf-8") as fh:
        plan = yaml.safe_load(fh)
    if args.alpha is not None:
        plan["alpha"] = args.alpha
    use_holm = args.holm or plan.get("correction") == "holm"

    df = load_runs(args.inp)
    if df.empty:
        print(f"no manifests under {args.inp}")
        return 1
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    kinds = sorted(df["backend"].unique())
    print(f"{len(df)} runs from {args.inp} (backend: {', '.join(kinds)})")

    if args.power:
        report = power_report(df, plan)
        print(json.dumps(_jsonable(report), indent=2))
        (out / "power_from_pilot.json").write_text(json.dumps(_jsonable(report), indent=2) + "\n")
        return 0

    wanted = [h.strip() for h in args.hypotheses.split(",") if h.strip()]
    payload: dict[str, Any] = {"alpha": plan["alpha"], "holm": use_holm, "backend": kinds, "runs": len(df)}

    if "H0_throughput_config" in wanted:
        payload["throughput_config"] = throughput_config(df, plan)
        tc = payload["throughput_config"]
        if "best_batch_size" in tc:
            print(f"throughput-optimal batch size: {tc['best_batch_size']}  (VT inert: {tc['vt_inert']}, "
                  f"duplicate floor {tc['duplicate_floor']:.4f})")
        wanted = [w for w in wanted if w != "H0_throughput_config"]

    if wanted:
        results = run_confirmatory(df, wanted, plan, use_holm)
        payload["confirmatory"] = results
        payload["exploratory"] = run_exploratory(df, plan)
        payload["excess_duplicates"] = excess_duplicates(df)
        table = markdown_table(results)
        print(table)
        note = ("_Numbers come from the local simulator (DRY_RUN=1), not from AWS._\n\n"
                if kinds == ["localsim"] else "")
        (out / "hypotheses.md").write_text("# Confirmatory tests\n\n" + note + table + "\n\n## Exploratory\n\n"
                                           + markdown_table(payload["exploratory"]) + "\n")

    name = "stats_" + "_".join(args.hypotheses.replace(",", "_").split()) + ".json"
    (out / name).write_text(json.dumps(_jsonable(payload), indent=2) + "\n")
    print(f"wrote {out / name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())


