#!/usr/bin/env python3
"""Refuse to go live when the cost guard is off or the plan is too expensive.

    python scripts/assert_free_tier_guard.py
    python scripts/assert_free_tier_guard.py --config configs/fault_campaigns.yaml

Checks:
  1. ENABLE_COST_GUARD is on (unless --allow-disabled)
  2. MAX_ESTIMATED_USD is set to something sensible (0 < x <= 50)
  3. STAGE is not "prod" - this rig must never point at production
  4. with --config: the planned runs are under the limit

Exit code 0 = ok to go, 1 = blocked.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import src  # noqa: E402,F401
from control.collect_metrics import load_pricing  # noqa: E402
from control.config import load_yaml, plan_runs  # noqa: E402
from control.cost_guard import CostGuardError, check_plan, guard_settings  # noqa: E402

HARD_CEILING_USD = 50.0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config")
    p.add_argument("--campaign", action="append")
    p.add_argument("--allow-disabled", action="store_true")
    args = p.parse_args(argv)

    problems = []
    enabled, limit = guard_settings()
    if not enabled and not args.allow_disabled:
        problems.append("ENABLE_COST_GUARD=0 - switch it back on (or pass --allow-disabled on purpose)")
    if not 0 < limit <= HARD_CEILING_USD:
        problems.append(f"MAX_ESTIMATED_USD={limit} is outside (0, {HARD_CEILING_USD}]")
    if os.environ.get("STAGE", "dev").lower() in ("prod", "production"):
        problems.append("STAGE looks like production - this rig is for a throwaway experiment account")

    if args.config and not problems:
        specs = plan_runs(load_yaml(args.config), select=args.campaign)
        try:
            est = check_plan(specs, load_pricing(ROOT / "configs" / "pricing.yaml"))
            print(f"plan ok: ~${est['usd_total']:.4f} for {est['runs']} runs (limit ${limit:.2f})")
        except CostGuardError as exc:
            problems.append(str(exc))

    if problems:
        for msg in problems:
            print("BLOCKED:", msg)
        return 1
    print("cost guard ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
