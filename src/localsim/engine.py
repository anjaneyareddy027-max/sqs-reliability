"""Discrete-event simulation of one experiment run (DRY_RUN=1 backend).

Queue arm
    producer -> SimQueue (+ DLQ) -> N pollers (event source mapping) -> the real
    ``queue_consumer.handler.handle_batch`` -> in-memory Orders table

    Each poller loops: receive up to ``batch_size`` messages, wait for the
    batching window if the batch is not full, invoke the handler, then delete
    what succeeded. A failed invocation deletes nothing (the whole batch comes
    back after the visibility timeout) and the poller backs off a little.
    Idle pollers long-poll: they sleep until a message is sent or until the
    next in-flight message becomes visible again (max 20 s, like SQS).

Sync arm
    each order is a request to ``sync_api.app.handle_request``; on a 5xx the
    client retries with exponential backoff, after ``sync_client_retries``
    retries it gives up and the order is lost.

Everything is seeded, so the same RunSpec always gives the same result.
"""
from __future__ import annotations

import dataclasses
import heapq
import itertools
import json
import math
import random
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from common.faults import ConsumerKilled, FaultConfig, FaultInjector, LambdaTimeout
from common.processing import Deps
from localsim.clock import SimSleeper
from localsim.datastore import InMemoryOrderStore, ListEventLog
from localsim.sqs import SimQueue
from producer.generate_orders import make_orders
from producer.loadgen import arrival_times
from queue_consumer.handler import handle_batch
from sync_api.app import handle_request


@dataclass
class RunResult:
    produced: dict[str, float]
    events: list[dict[str, Any]]
    dlq_order_ids: list[str]
    remaining_ids: list[str]
    samples: list[dict[str, float]]
    fault_window: tuple[float, float] | None
    counters: dict[str, float]
    end_time: float
    max_apply_count: int


def _order_id_of(body: str) -> str:
    try:
        return str(json.loads(body)["order_id"])
    except (ValueError, KeyError, TypeError):
        return "unparseable"


class _Timing:
    """Draws how long each stage of the handler takes (virtual seconds)."""

    def __init__(self, sim, rng: random.Random) -> None:
        self.sim = sim
        self.rng = rng

    def stage(self, stage: str) -> float:
        if stage == "parse":
            return self.sim.parse_s
        if stage == "write":
            return self.rng.lognormvariate(math.log(self.sim.write_median_s), self.sim.write_sigma)
        return 0.0


def fault_config_for(spec) -> FaultConfig:
    window = spec.fault_window
    if window is None:
        return FaultConfig()
    return FaultConfig(
        mode=spec.fault_mode,
        rate=spec.fault_rate,
        window_start=window[0],
        window_end=window[1],
        point=spec.fault_point,
        adaptive_vt=getattr(spec, 'adaptive_vt', False),
    )


def simulate(spec) -> RunResult:
    if spec.arm == "sync":
        return _simulate_sync(spec)
    return _simulate_queue(spec)


import os

def _simulate_queue(spec) -> RunResult:
    sim = spec.sim
    rng_queue = random.Random(spec.seed * 3 + 1)
    rng_fault = random.Random(spec.seed * 3 + 2)
    timing = _Timing(sim, random.Random(spec.seed * 3 + 3))

    dlq = SimQueue("orders-dlq", visibility_timeout=30, rng=rng_queue)
    queue = SimQueue(
        "orders",
        visibility_timeout=spec.visibility_timeout,
        dlq=dlq,
        max_receive_count=spec.max_receive_count,
        rng=rng_queue,
        dup_prob=sim.dup_prob,
    )
    store = InMemoryOrderStore()
    log = ListEventLog()
    orders = make_orders(spec.order_count, spec.run_id, spec.seed, spec.poison_rate)
    offsets = arrival_times(
        spec.order_count,
        spec.load_profile,
        spec.rate_per_sec,
        spec.seed,
        burst_start=spec.burst_start_s,
        burst_len=spec.burst_len_s,
        burst_factor=spec.burst_factor,
    )
    fcfg = fault_config_for(spec)
    window = spec.fault_window

    production_end = offsets[-1] if offsets else 0.0
    horizon = max(production_end, window[1] if window else 0.0) + spec.drain_timeout_s
    settle = spec.sample_interval_s * (int(spec.recovery["consecutive"]) + 1)

    heap: list[tuple[float, int, str, Any]] = []
    seq = itertools.count()

    def push(t: float, kind: str, data: Any = None) -> None:
        heapq.heappush(heap, (t, next(seq), kind, data))

    n_pollers = int(spec.max_concurrency)
    gen = [0] * n_pollers
    idle = [False] * n_pollers
    warm = [False] * n_pollers
    counters: dict[str, float] = defaultdict(float)
    memory_gb = sim.memory_mb / 1024.0
    produced: dict[str, float] = {}
    samples: list[dict[str, float]] = []
    sent = 0
    now = 0.0
    # how many samples in a row the run has looked finished (all sent, queue
    # empty). We only stop once that has held long enough for the recovery
    # rule (``consecutive`` samples inside the band) to be able to fire.
    finished_streak = 0

    for i, off in enumerate(offsets):
        push(off, "send", i)
    for p in range(n_pollers):
        push(0.0, "poll", (p, 0))
    push(0.0, "sample")

    def wake_one_idle(at: float) -> None:
        for p in range(n_pollers):
            if idle[p]:
                idle[p] = False
                gen[p] += 1
                push(at, "poll", (p, gen[p]))
                return

    def invoke(p: int, t: float, records: list[dict[str, Any]]) -> None:
        sleeper = SimSleeper(t, spec.consumer_timeout_s)
        injector = FaultInjector(fcfg, rng=rng_fault, clock=sleeper.now, sleeper=sleeper)
        deps = Deps(
            store=store,
            events=log,
            injector=injector,
            arm="queue",
            run_id=spec.run_id,
            now=sleeper.now,
            remaining_s=sleeper.remaining,
            idempotent=spec.idempotency,
            tick=lambda stage: sleeper.sleep(timing.stage(stage)),
            change_visibility=lambda handle, to_s: queue.change_visibility(handle, to_s, sleeper.now()),
        )
        failed: set[str] = set()
        reason = "ok"
        try:
            sleeper.sleep(sim.invoke_overhead_s if warm[p] else sim.cold_start_s)
            result = handle_batch(records, deps)
            failed = {f["itemIdentifier"] for f in result["batchItemFailures"]}
        except LambdaTimeout:
            reason = "timeout"
        except ConsumerKilled:
            reason = "killed"
        except Exception:  # noqa: BLE001 - unhandled error inside the function
            reason = "error"
        counters["invocations"] += 1
        counters[f"invocation_{reason}"] += 1
        counters["records_delivered"] += len(records)
        counters["lambda_gb_s"] += sleeper.used * memory_gb
        # a killed process or a timeout means a fresh environment next time
        warm[p] = reason in ("ok", "error")
        push(t + sleeper.used, "finish", (p, records, reason == "ok", failed))

    while heap:
        t, _, kind, data = heapq.heappop(heap)
        if t > horizon:
            break
        now = t

        if kind == "send":
            order = dataclasses.replace(orders[data], created_at=t)
            queue.send(order.to_json(), t, delay=spec.delivery_delay)
            produced[order.order_id] = t
            sent += 1
            wake_one_idle(t + spec.delivery_delay)

        elif kind == "sample":
            samples.append({"t": t, **queue.depth(t)})
            finished = sent == len(orders) and len(queue) == 0
            finished_streak = finished_streak + 1 if finished else 0
            if finished_streak > int(spec.recovery["consecutive"]) and (window is None or t >= window[1] + settle):
                break
            push(t + spec.sample_interval_s, "sample")

        elif kind == "poll":
            p, g = data
            if g != gen[p]:
                continue  # stale wake-up
            records = queue.receive(spec.batch_size, t)
            if not records:
                idle[p] = True
                gen[p] += 1
                nxt = queue.next_visible_at()
                wake = t + sim.long_poll_s if nxt is None else min(t + sim.long_poll_s, max(nxt, t))
                push(wake, "poll", (p, gen[p]))
                continue
            if len(records) < spec.batch_size and spec.batching_window_s > 0:
                push(t + spec.batching_window_s, "fill", (p, records))
            else:
                invoke(p, t, records)

        elif kind == "fill":
            p, records = data
            more = queue.receive(spec.batch_size - len(records), t)
            invoke(p, t, records + more)

        elif kind == "finish":
            p, records, ok, failed = data
            if ok:
                done = [r for r in records if r["messageId"] not in failed]
                for r in done:
                    queue.delete(r["receiptHandle"])
                if done:
                    counters["delete_batch_calls"] += math.ceil(len(done) / 10)
            gen[p] += 1
            idle[p] = False
            push(t + (0.0 if ok else sim.error_backoff_s), "poll", (p, gen[p]))

    for key, value in queue.counters.items():
        counters[f"sqs_{key}"] = value
    counters["sqs_send_batch_calls"] = math.ceil(len(produced) / 10)
    counters["dlq_messages"] = len(dlq)
    counters["dynamo_order_writes"] = store.writes
    counters["dynamo_event_writes"] = len(log.events)

    return RunResult(
        produced=produced,
        events=log.events,
        dlq_order_ids=[_order_id_of(b) for b in dlq.bodies()],
        remaining_ids=[_order_id_of(b) for b in queue.bodies()],
        samples=samples,
        fault_window=window,
        counters=dict(counters),
        end_time=now,
        max_apply_count=max(store.apply_count.values(), default=0),
    )


def _simulate_sync(spec) -> RunResult:
    sim = spec.sim
    rng_fault = random.Random(spec.seed * 3 + 2)
    timing = _Timing(sim, random.Random(spec.seed * 3 + 3))
    store = InMemoryOrderStore()
    log = ListEventLog()
    orders = make_orders(spec.order_count, spec.run_id, spec.seed, spec.poison_rate)
    offsets = arrival_times(
        spec.order_count,
        spec.load_profile,
        spec.rate_per_sec,
        spec.seed,
        burst_start=spec.burst_start_s,
        burst_len=spec.burst_len_s,
        burst_factor=spec.burst_factor,
    )
    fcfg = fault_config_for(spec)
    counters: dict[str, float] = defaultdict(float)
    memory_gb = sim.memory_mb / 1024.0
    produced: dict[str, float] = {}

    heap: list[tuple[float, int, int, int]] = []
    seq = itertools.count()
    for i, off in enumerate(offsets):
        heapq.heappush(heap, (off, next(seq), i, 0))

    first_call = True
    now = 0.0
    while heap:
        t, _, i, attempt = heapq.heappop(heap)
        now = t
        order = dataclasses.replace(orders[i], created_at=t)
        if attempt == 0:
            produced[order.order_id] = t
        sleeper = SimSleeper(t, spec.sync_timeout_s)
        injector = FaultInjector(fcfg, rng=rng_fault, clock=sleeper.now, sleeper=sleeper)
        deps = Deps(
            store=store,
            events=log,
            injector=injector,
            arm="sync",
            run_id=spec.run_id,
            now=sleeper.now,
            remaining_s=sleeper.remaining,
            idempotent=spec.idempotency,
            tick=lambda stage, s=sleeper: s.sleep(timing.stage(stage)),
        )
        try:
            sleeper.sleep(sim.api_overhead_s + (sim.cold_start_s if first_call else sim.invoke_overhead_s))
            status, _ = handle_request(order.to_json(), f"{order.order_id}#a{attempt}", deps)
        except LambdaTimeout:
            status = 504
        except ConsumerKilled:
            status = 502
        except Exception:  # noqa: BLE001
            status = 500
        first_call = False
        counters["api_requests"] += 1
        counters["invocations"] += 1
        counters[f"http_{status}"] += 1
        counters["lambda_gb_s"] += sleeper.used * memory_gb

        done_at = t + sleeper.used
        if status >= 500 and attempt < spec.sync_client_retries:
            heapq.heappush(heap, (done_at + spec.sync_backoff_s * (2**attempt), next(seq), i, attempt + 1))
        elif status != 200:
            counters["client_gave_up"] += 1

    counters["dynamo_order_writes"] = store.writes
    counters["dynamo_event_writes"] = len(log.events)
    return RunResult(
        produced=produced,
        events=log.events,
        dlq_order_ids=[],
        remaining_ids=[],
        samples=[],
        fault_window=spec.fault_window,
        counters=dict(counters),
        end_time=now,
        max_apply_count=max(store.apply_count.values(), default=0),
    )
