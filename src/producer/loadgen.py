"""Load profiles and the live senders.

Profiles (all seeded, so the same config always gives the same schedule):

* ``normal`` - Poisson arrivals at ``rate`` msg/s
* ``burst``  - same, but ``burst_factor`` x faster during a burst window
* ``batch``  - the whole workload is pushed as fast as SendMessageBatch
  allows, before/while consumers drain it. This mirrors the "process a big
  batch of records" setup of the Kyrychenko et al. (2025) baseline.
"""
from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.request
from typing import Any, Sequence

from common.models import Order

PROFILES = ("normal", "burst", "batch")


def arrival_times(
    n: int,
    profile: str,
    rate: float,
    seed: int,
    burst_start: float = 30.0,
    burst_len: float = 15.0,
    burst_factor: float = 5.0,
    preload_rate: float = 500.0,
) -> list[float]:
    """Send offsets in seconds from the start of the run."""
    if profile not in PROFILES:
        raise ValueError(f"unknown load profile {profile!r}")
    if n <= 0:
        return []
    if profile == "batch":
        return [i / preload_rate for i in range(n)]

    rng = random.Random(seed)
    times: list[float] = []
    t = 0.0
    while len(times) < n:
        current = rate
        if profile == "burst" and burst_start <= t < burst_start + burst_len:
            current = rate * burst_factor
        t += rng.expovariate(current)
        times.append(t)
    return times


# ---------------------------------------------------------------------------
# live senders (DRY_RUN=0 only)
# ---------------------------------------------------------------------------

def send_to_sqs(
    sqs_client,
    queue_url: str,
    orders: Sequence[Order],
    offsets: Sequence[float],
    delay_s: int = 0,
    clock=time.time,
    sleep=time.sleep,
    calls: dict[str, int] | None = None,
) -> dict[str, float]:
    """Send orders following the schedule. Returns order_id -> produce time.

    ``calls["n"]`` (if given) counts SendMessageBatch requests for the cost proxy.
    """
    produced: dict[str, float] = {}
    start = clock()
    i = 0
    while i < len(orders):
        wait = start + offsets[i] - clock()
        if wait > 0:
            sleep(wait)
        # group everything that is already due into one SendMessageBatch (max 10)
        now_rel = clock() - start
        batch = []
        while i < len(orders) and len(batch) < 10 and offsets[i] <= now_rel + 0.05:
            batch.append(orders[i])
            i += 1
        if not batch:
            batch.append(orders[i])
            i += 1
        entries = [
            {"Id": str(k), "MessageBody": o.to_json(), "DelaySeconds": int(delay_s)}
            for k, o in enumerate(batch)
        ]
        resp = sqs_client.send_message_batch(QueueUrl=queue_url, Entries=entries)
        if calls is not None:
            calls["n"] = calls.get("n", 0) + 1
        sent_at = clock()
        failed = {f["Id"] for f in resp.get("Failed", [])}
        for k, o in enumerate(batch):
            if str(k) not in failed:
                produced[o.order_id] = sent_at
    return produced


def _post_one(api_url: str, order: Order, retries: int, backoff_s: float, timeout_s: float,
              clock=time.time, sleep=time.sleep) -> dict[str, Any]:
    produced_at = clock()
    status = None
    attempts = 0
    for attempt in range(retries + 1):
        attempts += 1
        req = urllib.request.Request(
            api_url,
            data=order.to_json().encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                status = resp.status
        except urllib.error.HTTPError as err:
            status = err.code
        except (urllib.error.URLError, TimeoutError, OSError):
            status = 599
        if status == 200:
            break
        if attempt < retries:
            sleep(backoff_s * (2**attempt))
    return {
        "order_id": order.order_id,
        "produced_at": produced_at,
        "done_at": clock(),
        "status": status,
        "attempts": attempts,
        "body": json.loads(order.to_json()),
    }


def post_to_api(
    api_url: str,
    orders: Sequence[Order],
    offsets: Sequence[float],
    retries: int = 2,
    backoff_s: float = 0.2,
    timeout_s: float = 30.0,
    clock=time.time,
    sleep=time.sleep,
    max_workers: int = 32,
) -> list[dict[str, Any]]:
    """Sync arm: POST each order at its planned time, retrying a couple of times.

    v2: open loop. Each order is handed to a thread pool at its scheduled
    offset, so a slow or failing request does not hold back the ones behind it
    (the v1 client was sequential, which would have slowed arrivals down
    exactly when the backend failed and hidden part of the fault).
    """
    from concurrent.futures import ThreadPoolExecutor

    start = clock()
    futures = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for order, offset in zip(orders, offsets):
            wait = start + offset - clock()
            if wait > 0:
                sleep(wait)
            futures.append(pool.submit(_post_one, api_url, order, retries, backoff_s, timeout_s, clock, sleep))
        results = [f.result() for f in futures]
    return results
