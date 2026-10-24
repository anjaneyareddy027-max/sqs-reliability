"""Raw evidence -> dependent variables.

The simulator and the live collector both call ``compute_run_metrics`` so the
maths is exactly the same in both modes. Definitions follow the master prompt
(section 4.4):

* loss rate        = (produced - |processed unique  U  DLQ unique|) / produced
* duplicate rate   = extra successful process events per unique processed msg
* DLQ capture rate = unique messages in DLQ / produced
* success rate     = unique successful business writes / produced
* drain time (v2 PRIMARY recovery metric)
                   = fault OFF -> backlog (visible + in flight + delayed) is 0
                     and stays 0; no-fault runs use the same nominal window so
                     every queue run gets a value
* last store       = fault OFF -> last first successful write (from the event
                     log, exact; also defined for the sync arm)
* recovery time    = fault OFF -> queue depth back inside the pre-fault band
                     (v1 metric, kept as secondary; the visible-only variant
                     is blind to in-flight messages)
* throughput       = unique successes / wall time
* latency          = produce -> first successful process (p50 / p95)

Loss is measured at the end of the observation horizon, so anything still
sitting in the main queue at that point counts as not accounted for. That part
is also reported on its own as ``stranded_rate`` so it is not hidden.
"""
from __future__ import annotations

import math
import statistics
from typing import Any, Iterable, Sequence

from common.models import Outcome

NAN = float("nan")


def percentile(values: Sequence[float], q: float) -> float:
    """Linear interpolation percentile, q in [0, 100]. NaN for empty input."""
    if not values:
        return NAN
    data = sorted(values)
    if len(data) == 1:
        return float(data[0])
    pos = (len(data) - 1) * (q / 100.0)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return float(data[lo])
    return float(data[lo] + (data[hi] - data[lo]) * (pos - lo))


def _depth(sample: dict[str, float], key: str) -> float:
    if key == "visible":
        return float(sample["visible"])
    # backlog = everything still owned by the main queue (visible + in flight + delayed)
    return float(sample["visible"] + sample.get("inflight", 0) + sample.get("delayed", 0))


def recovery_time(
    samples: Sequence[dict[str, float]],
    fault_on: float,
    fault_off: float,
    key: str = "backlog",
    k_sd: float = 2.0,
    min_tol: float = 5.0,
    consecutive: int = 3,
    pre_window_s: float = 30.0,
) -> tuple[float | None, float]:
    """Seconds from fault OFF until depth is back in the pre-fault band.

    Band upper edge = mean + k_sd * sd of the depth samples in the
    ``pre_window_s`` seconds before the fault, with at least ``min_tol``
    messages of slack (a nearly empty queue has sd ~ 0 which would make the
    band silly narrow). Depth has to stay inside for ``consecutive`` samples in
    a row. Returns (None, upper) if it never recovers inside the horizon.
    """
    ordered = sorted(samples, key=lambda s: s["t"])
    pre = [_depth(s, key) for s in ordered if fault_on - pre_window_s <= s["t"] < fault_on]
    if not pre:
        pre = [_depth(s, key) for s in ordered if s["t"] < fault_on]
    mean = statistics.fmean(pre) if pre else 0.0
    sd = statistics.pstdev(pre) if len(pre) > 1 else 0.0
    upper = max(mean + k_sd * sd, mean + min_tol)

    streak = 0
    first_t = None
    for s in ordered:
        if s["t"] < fault_off:
            continue
        if _depth(s, key) <= upper:
            streak += 1
            if streak == 1:
                first_t = s["t"]
            if streak >= consecutive:
                return float(first_t - fault_off), upper
        else:
            streak = 0
            first_t = None
    return None, upper


def backlog_drain(samples: Sequence[dict[str, float]], ref_off: float, last_send: float) -> tuple[float | None, float | None]:
    """When did the backlog empty for good?

    Returns (seconds after ``ref_off`` (floored at 0), absolute time) of the
    first sample at or after the last send from which visible + in flight +
    delayed is 0 in that sample and every later one. (None, None) if the run
    ended with messages still owned by the queue (censored).
    """
    ordered = sorted(samples, key=lambda s: s["t"])
    if not ordered:
        return None, None
    drained_at = None
    for s in reversed(ordered):
        if _depth(s, "backlog") == 0 and s["t"] >= last_send:
            drained_at = s["t"]
        else:
            break
    if drained_at is None:
        return None, None
    return max(0.0, float(drained_at - ref_off)), float(drained_at)


def compute_run_metrics(
    produced: dict[str, float],
    events: Iterable[dict[str, Any]],
    dlq_order_ids: Iterable[str],
    samples: Sequence[dict[str, float]] = (),
    fault_window: tuple[float, float] | None = None,
    remaining_ids: Iterable[str] = (),
    recovery_kwargs: dict[str, Any] | None = None,
    nominal_window: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """``remaining_ids`` = order ids still sitting in the main queue at the end."""
    n = len(produced)
    first_success: dict[str, float] = {}
    duplicate_events = 0
    unsafe_events = 0
    rejected = 0
    invalid = 0
    attempts = 0

    for ev in events:
        attempts += 1
        oid = ev["order_id"]
        outcome = ev["outcome"]
        if outcome == Outcome.FIRST_SUCCESS:
            if oid in first_success:
                # can only happen if two first writes raced; count it as unsafe
                unsafe_events += 1
                duplicate_events += 1
            else:
                first_success[oid] = float(ev["ts"])
        elif outcome == Outcome.DUPLICATE_SUCCESS:
            duplicate_events += 1
        elif outcome == Outcome.UNSAFE_DOUBLE_APPLY:
            unsafe_events += 1
            duplicate_events += 1
        elif outcome == Outcome.WRITE_REJECTED:
            rejected += 1
        elif outcome == Outcome.INVALID:
            invalid += 1

    produced_ids = set(produced)
    success_ids = set(first_success) & produced_ids
    dlq_ids = set(dlq_order_ids) & produced_ids
    accounted = success_ids | dlq_ids
    # stranded = still in the main queue when we stopped watching, never
    # processed and never dead-lettered
    stranded = len((set(remaining_ids) & produced_ids) - accounted)

    unique_success = len(success_ids)
    latencies = [first_success[o] - produced[o] for o in success_ids]
    if success_ids:
        t0 = min(produced.values())
        t1 = max(first_success[o] for o in success_ids)
        wall = max(t1 - t0, 1e-9)
        throughput = unique_success / wall
    else:
        wall = NAN
        throughput = 0.0

    def rate(x: float) -> float:
        return x / n if n else NAN

    out: dict[str, Any] = {
        "produced": n,
        "unique_success": unique_success,
        "dlq_unique": len(dlq_ids),
        "accounted": len(accounted),
        "attempts": attempts,
        "duplicate_events": duplicate_events,
        "unsafe_double_apply": unsafe_events,
        "write_rejected_events": rejected,
        "invalid_events": invalid,
        "stranded": stranded,
        "loss_rate": rate(n - len(accounted)),
        "success_rate": rate(unique_success),
        "dlq_capture_rate": rate(len(dlq_ids)),
        "duplicate_rate": duplicate_events / unique_success if unique_success else 0.0,
        "stranded_rate": rate(stranded),
        # gone without being processed, dead-lettered or still queued
        "true_loss_rate": rate(n - len(accounted) - stranded),
        "throughput_msg_s": throughput,
        "wall_time_s": wall,
        "latency_p50_s": percentile(latencies, 50),
        "latency_p95_s": percentile(latencies, 95),
        "latency_mean_s": statistics.fmean(latencies) if latencies else NAN,
        "recovery_time_s": NAN,
        "recovery_time_visible_s": NAN,
        "recovered": None,
        "recovery_band_upper": NAN,
    }

    # v2 primary recovery metric: backlog drain after the (nominal) window
    ref = fault_window or nominal_window
    out["drain_after_window_s"] = NAN
    out["drain_from_start_s"] = NAN
    out["drain_censored"] = None
    out["last_store_after_window_s"] = NAN
    if ref is not None and success_ids:
        out["last_store_after_window_s"] = float(max(first_success[o] for o in success_ids) - ref[1])
    if ref is not None and samples and produced:
        last_send = max(produced.values())
        d, at = backlog_drain(samples, ref[1], last_send)
        out["drain_censored"] = d is None
        if d is not None:
            out["drain_after_window_s"] = d
            out["drain_from_start_s"] = at - min(produced.values())
        else:
            out["drain_censored_at_s"] = float(max(s["t"] for s in samples) - ref[1])

    if fault_window is not None and samples:
        kw = dict(recovery_kwargs or {})
        on, off = fault_window
        rec, upper = recovery_time(samples, on, off, key="backlog", **kw)
        rec_vis, _ = recovery_time(samples, on, off, key="visible", **kw)
        out["recovery_time_s"] = rec if rec is not None else NAN
        out["recovery_time_visible_s"] = rec_vis if rec_vis is not None else NAN
        out["recovered"] = rec is not None
        out["recovery_band_upper"] = upper
        if rec is None:
            # censored: never recovered before the horizon ended. Store how long
            # we watched so the analysis can treat it as ">= this value".
            last_t = max(s["t"] for s in samples)
            out["recovery_censored_at_s"] = float(last_t - off)
    return out
