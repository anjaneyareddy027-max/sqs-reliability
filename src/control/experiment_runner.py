"""Experiment runner: config matrix -> randomised runs -> manifests.

Examples (from the project root)::

    DRY_RUN=1 python -m src.control.experiment_runner --config configs/pilot.yaml
    python -m src.control.experiment_runner --config configs/baseline_kyrchenko.yaml \
        --fault none --randomise-order --repeats 5 --out results/baseline/
    python -m src.control.experiment_runner --config configs/fault_campaigns.yaml \
        --fault consumer_kill --vary visibility_timeout --out results/campaigns/
    python -m src.control.experiment_runner --arm sync --load normal --fault none --repeats 5

DRY_RUN defaults to 1 (local simulator). Live runs need DRY_RUN=0 *and*
``--live`` *and* a stack name, and they go through the cost guard first.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from pathlib import Path
from typing import Any

from control.collect_metrics import estimate_cost, load_pricing, metrics_from_result, write_raw
from control.config import load_yaml, plan_runs
from control.manifest import build_manifest, git_commit, now_iso, write_manifest
from localsim.engine import simulate

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "configs" / "pilot.yaml"
SUMMARY_FIELDS = [
    "run_id", "campaign", "arm", "load_profile", "fault_mode", "visibility_timeout",
    "max_receive_count", "batch_size", "delivery_delay", "repeat", "loss_rate",
    "duplicate_rate", "dlq_capture_rate", "success_rate", "stranded_rate",
    "drain_after_window_s", "drain_censored", "last_store_after_window_s",
    "recovery_time_s", "recovery_time_visible_s", "throughput_msg_s",
    "latency_p50_s", "latency_p95_s", "usd_total",
]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="run SQS reliability experiments")
    p.add_argument("--config", default=str(DEFAULT_CONFIG))
    p.add_argument("--campaign", action="append", help="campaign name inside the config (repeatable)")
    p.add_argument("--fault", help="fault mode: filters campaigns, or overrides a single config")
    p.add_argument("--vary", help="only campaigns whose matrix varies this IV")
    p.add_argument("--arm", choices=["queue", "sync"])
    p.add_argument("--load", choices=["normal", "burst", "batch"])
    p.add_argument("--repeats", type=int)
    p.add_argument("--orders", type=int, help="override order_count")
    p.add_argument("--randomise-order", action="store_true", default=None)
    p.add_argument("--out", default=str(PROJECT_ROOT / "results"))
    p.add_argument("--no-raw", action="store_true", help="do not write results/raw dumps")
    p.add_argument("--list", action="store_true", help="only print the planned runs")
    p.add_argument("--limit", type=int, help="stop after N runs (smoke tests)")
    p.add_argument("--live", action="store_true", help="really use AWS (also needs DRY_RUN=0)")
    p.add_argument("--stack-name", default=os.environ.get("STACK_NAME", "sqs-rr-dev"))
    p.add_argument(
        "--terraform-dir",
        default=os.environ.get("TERRAFORM_DIR", str(PROJECT_ROOT / "terraform")),
        help="load StackOutputs from terraform output -json (preferred over CloudFormation)",
    )
    p.add_argument(
        "--from-cfn",
        action="store_true",
        help="use CloudFormation stack outputs instead of terraform (legacy)",
    )
    p.add_argument("--pricing", default=str(PROJECT_ROOT / "configs" / "pricing.yaml"))
    p.add_argument("--from-env", action="store_true", help="one ad-hoc run built from .env / environment variables")
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--resume", action="store_true", help="skip runs whose manifest already exists in --out")
    p.add_argument("--expect-account", default=os.environ.get("EXPECT_AWS_ACCOUNT"),
                   help="live only: abort unless sts get-caller-identity returns this account id")
    return p.parse_args(argv)


ENV_KEYS = {
    "FAULT_MODE": ("fault_mode", str),
    "FAULT_RATE": ("fault_rate", float),
    "FAULT_WINDOW_SEC": ("fault_window_s", float),
    "VISIBILITY_TIMEOUT": ("visibility_timeout", int),
    "MAX_RECEIVE_COUNT": ("max_receive_count", int),
    "BATCH_SIZE": ("batch_size", int),
    "ORDER_COUNT": ("order_count", int),
    "LOAD_PROFILE": ("load_profile", str),
    "AWS_REGION": ("region", str),
}


def config_from_env(env: dict[str, str] | None = None) -> dict[str, Any]:
    """The .env variables (see .env.example) as a one-run config."""
    env = dict(os.environ) if env is None else env
    fixed: dict[str, Any] = {}
    for var, (key, cast) in ENV_KEYS.items():
        raw = (env.get(var) or "").split("#")[0].strip()
        if raw:
            fixed[key] = cast(raw)
    return {"name": "adhoc_env", "repeats": 1, "fixed": fixed}


def load_dotenv_if_present() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(PROJECT_ROOT / ".env", override=False)


def rebuild_summary(out_dir: Path) -> Path:
    """summary.csv is always rebuilt from every manifest in out_dir, so several
    invocations into the same folder do not overwrite each other."""
    from control.manifest import read_manifests

    rows = []
    for m in read_manifests(out_dir):
        rows.append({**m["cell"], **m["metrics"], "run_id": m["run_id"], "campaign": m["campaign"],
                     "repeat": m["repeat"], "usd_total": m["cost"]["usd_total"]})
    rows.sort(key=lambda r: (r["campaign"], r["run_id"]))
    path = out_dir / "summary.csv"
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=SUMMARY_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path


def select_campaigns(config: dict[str, Any], args: argparse.Namespace) -> tuple[list[str] | None, dict[str, Any]]:
    """Decide which campaigns to run and what to override.

    For a file with several campaigns ``--fault``/``--vary`` *pick* campaigns.
    For a single experiment file they *override* the settings.
    """
    overrides: dict[str, Any] = {
        "arm": args.arm,
        "load_profile": args.load,
        "repeats": args.repeats,
        "order_count": args.orders,
        "randomise_order": args.randomise_order,
    }
    select = list(args.campaign) if args.campaign else None
    if "campaigns" not in config:
        overrides["fault_mode"] = args.fault
        return select, overrides

    if select is None and (args.fault or args.vary):
        from control.config import campaigns_in

        select = []
        for name, camp in campaigns_in(config).items():
            fixed_fault = (camp.get("fixed") or {}).get("fault_mode", camp.get("fault_mode"))
            matrix = camp.get("matrix") or {}
            if args.fault and fixed_fault != args.fault and args.fault not in (matrix.get("fault_mode") or []):
                continue
            # --vary X means "the campaign whose one primary IV is X", so the
            # two-factor campaigns (E, H) are only run by name or with --all
            if args.vary and set(matrix) != {args.vary}:
                continue
            select.append(name)
        if not select:
            raise SystemExit(f"no campaign matches --fault {args.fault} --vary {args.vary}")
    return select, overrides


def dry_run_enabled() -> bool:
    return os.environ.get("DRY_RUN", "1") != "0"


def main(argv: list[str] | None = None) -> int:
    load_dotenv_if_present()
    args = parse_args(argv)
    if args.from_env:
        config = config_from_env()
        args.config = str(PROJECT_ROOT / ".env")
    else:
        config = load_yaml(args.config)
    select, overrides = select_campaigns(config, args)
    specs = plan_runs(config, select=select, overrides=overrides)
    if args.from_env and os.environ.get("RUN_ID", "").split("#")[0].strip():
        specs[0].run_id = os.environ["RUN_ID"].split("#")[0].strip()
    if args.limit:
        specs = specs[: args.limit]

    live = args.live and not dry_run_enabled()
    backend = "live" if live else "localsim"

    if args.list:
        for i, s in enumerate(specs):
            print(f"{i:4d} {s.campaign:22s} {s.arm:5s} {s.load_profile:6s} {s.fault_mode:17s} "
                  f"vt={s.visibility_timeout:<4d} mrc={s.max_receive_count:<3d} bs={s.batch_size:<3d} rep={s.repeat}")
        print(f"{len(specs)} runs planned ({backend})")
        return 0

    out_dir = Path(args.out)
    if args.resume:
        done = {p.stem for p in (out_dir / "manifests").glob("*.json")}
        before = len(specs)
        specs = [s for s in specs if s.run_id not in done]
        print(f"[resume] {before - len(specs)} runs already have manifests, {len(specs)} left")

    pricing = load_pricing(args.pricing)
    outputs = None
    provenance: dict[str, Any] = {}
    if live:
        import boto3

        account = boto3.client("sts").get_caller_identity()["Account"]
        if args.expect_account and account != args.expect_account:
            raise SystemExit(f"[abort] credentials belong to account {account}, expected {args.expect_account}")
        print(f"[live] caller identity account {account}")
        from control.cost_guard import check_plan
        from control.live_backend import StackOutputs

        estimate = check_plan(specs, pricing)
        print(f"[cost guard] ~${estimate['usd_total']:.2f} for {estimate['runs']} runs "
              f"(~{estimate['est_wall_hours']:.1f} h), limit ${estimate['limit_usd']:.2f}")
        if args.from_cfn:
            outputs = StackOutputs.from_stack(args.stack_name)
        else:
            outputs = StackOutputs.from_terraform(args.terraform_dir)
            print(f"[live] stack from terraform dir {args.terraform_dir}")
        lam = boto3.client("lambda")
        provenance = {
            "aws_account": account,
            "deployed_code_sha256": {
                fn: lam.get_function_configuration(FunctionName=fn)["CodeSha256"]
                for fn in (outputs.consumer_function, outputs.sync_function) if fn
            },
            "lambda_source_git": os.environ.get("LAMBDA_SOURCE_GIT", ""),
        }
    elif args.live:
        print("[info] --live given but DRY_RUN is not 0 -> staying on the local simulator")

    git = git_commit()
    t_start = time.time()
    for pos, spec in enumerate(specs):
        started = now_iso()
        if live:
            from control.live_backend import run_live

            result = run_live(spec, outputs)
        else:
            result = simulate(spec)
        metrics = metrics_from_result(spec, result)
        cost = estimate_cost(result.counters, pricing)
        manifest = build_manifest(
            spec, metrics, cost, result.counters, backend, started, now_iso(), pos,
            os.path.relpath(args.config, PROJECT_ROOT), git=git, provenance=provenance or None,
        )
        write_manifest(out_dir, manifest)
        if not args.no_raw:
            write_raw(out_dir, spec.run_id, result)
        if not args.quiet:
            rec = metrics.get("drain_after_window_s", float("nan"))
            print(f"[{pos + 1}/{len(specs)}] {now_iso()} {spec.campaign} {spec.arm} {spec.load_profile} "
                  f"{spec.fault_mode} vt={spec.visibility_timeout} "
                  f"mrc={spec.max_receive_count} bs={spec.batch_size} loss={metrics['loss_rate']:.4f} "
                  f"dup={metrics['duplicate_rate']:.4f} dlq={metrics['dlq_capture_rate']:.4f} "
                  f"drain={'-' if rec != rec else f'{rec:.0f}s'} thr={metrics['throughput_msg_s']:.2f}/s "
                  f"usd={cost['usd_total']:.6f}", flush=True)

    summary = rebuild_summary(out_dir)
    print(f"{len(specs)} runs done in {time.time() - t_start:.1f}s ({backend}); "
          f"manifests in {out_dir / 'manifests'}, summary {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
