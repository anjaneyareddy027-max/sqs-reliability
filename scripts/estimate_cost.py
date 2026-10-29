#!/usr/bin/env python3
"""Estimate what a campaign would cost on AWS before running it live.

    python scripts/estimate_cost.py --config configs/pilot.yaml
    python scripts/estimate_cost.py --config configs/fault_campaigns.yaml --campaign A_vt_consumer_kill
    python scripts/estimate_cost.py --orders 1000 --repeats 5

It simulates every planned run, prices the request counts with
configs/pricing.yaml and applies a x2 safety factor. Free tier is ignored.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import src  # noqa: E402,F401
from control.collect_metrics import load_pricing  # noqa: E402
from control.config import RunSpec, load_yaml, plan_runs  # noqa: E402
from control.cost_guard import estimate_plan, guard_settings  # noqa: E402


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config")
    p.add_argument("--campaign", action="append")
    p.add_argument("--orders", type=int)
    p.add_argument("--repeats", type=int)
    p.add_argument("--pricing", default=str(ROOT / "configs" / "pricing.yaml"))
    args = p.parse_args(argv)

    if args.config:
        specs = plan_runs(load_yaml(args.config), select=args.campaign,
                          overrides={"order_count": args.orders, "repeats": args.repeats})
    else:
        specs = [RunSpec(run_id=f"estimate-{i}", order_count=args.orders or 1000) for i in range(args.repeats or 1)]

    est = estimate_plan(specs, load_pricing(args.pricing))
    enabled, limit = guard_settings()
    print(f"runs planned        : {est['runs']}")
    print(f"estimated cost      : ${est['usd_total']:.4f}  (x{est['safety_factor']:.0f} safety factor, no free tier)")
    print(f"worst single run    : ${est['usd_worst_run']:.4f}")
    print(f"estimated wall time : {est['est_wall_hours']:.1f} h (live mode incl. purge cool-downs)")
    print(f"guard limit         : ${limit:.2f} ({'on' if enabled else 'OFF'})")
    if est["usd_total"] > limit:
        print("WARNING: above MAX_ESTIMATED_USD - the live runner will refuse this plan")
    return 0


if __name__ == "__main__":
    sys.exit(main())
