"""Cost estimate for a planned set of runs, and the guard that blocks big ones.

The trick: we already have a simulator that counts requests, so the estimate
for a live campaign is "simulate every planned run, price the request counts,
multiply by a safety factor". It is only a proxy but it catches the obvious
mistakes (someone typing 500000 orders x 5 repeats x 20 cells).
"""
from __future__ import annotations

import os
from typing import Any

from control.collect_metrics import estimate_cost
from localsim.engine import simulate

SAFETY_FACTOR = 2.0  # real Lambda durations/cold starts are usually worse than the model
LIVE_OVERHEAD_S = 90.0  # purge cool-down + waiting for the mapping update, per run


class CostGuardError(RuntimeError):
    pass


def estimate_plan(specs: list, pricing: dict[str, float]) -> dict[str, Any]:
    total = 0.0
    worst = 0.0
    wall_s = 0.0
    for spec in specs:
        result = simulate(spec)
        usd = estimate_cost(result.counters, pricing)["usd_total"] * SAFETY_FACTOR
        total += usd
        worst = max(worst, usd)
        wall_s += result.end_time + LIVE_OVERHEAD_S
    return {
        "runs": len(specs),
        "usd_total": total,
        "usd_worst_run": worst,
        "safety_factor": SAFETY_FACTOR,
        "est_wall_hours": wall_s / 3600.0,
    }


def guard_settings() -> tuple[bool, float]:
    enabled = os.environ.get("ENABLE_COST_GUARD", "1") != "0"
    limit = float(os.environ.get("MAX_ESTIMATED_USD", "5.00"))
    return enabled, limit


def check_plan(specs: list, pricing: dict[str, float], max_usd: float | None = None) -> dict[str, Any]:
    enabled, limit = guard_settings()
    if max_usd is not None:
        limit = max_usd
    estimate = estimate_plan(specs, pricing)
    estimate["limit_usd"] = limit
    if not enabled:
        print("[warn] ENABLE_COST_GUARD=0, not enforcing the budget")
        return estimate
    if estimate["usd_total"] > limit:
        raise CostGuardError(
            f"estimated ${estimate['usd_total']:.2f} for {estimate['runs']} runs is above "
            f"MAX_ESTIMATED_USD=${limit:.2f}. Cut orders/repeats or raise the limit on purpose."
        )
    return estimate
