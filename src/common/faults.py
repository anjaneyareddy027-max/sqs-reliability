"""FAULT_MODE switch.

The handlers call ``injector.check(point)`` at a few places in the processing
path. Depending on the configured mode (and a coin flip with FAULT_RATE) the
call either does nothing or breaks the invocation in a specific way:

==================  ===============  =============================================
mode                point            what happens
==================  ===============  =============================================
none                -                nothing, happy path (gives the duplicate floor)
consumer_kill       before_process   process dies mid batch, before any delete
unhandled_error     before/after     exception escapes the handler -> whole batch
                    _write           goes back to the queue
datastore_reject    before_write     the write is refused -> only that record fails
datastore_timeout   before_write     the write hangs past the Lambda timeout
==================  ===============  =============================================

FAULT_RATE is the probability *per record* at the injection point, so bigger
batches get hit more often by the batch-wide faults (that is on purpose, it is
part of what batch size does under failure).

No AWS Fault Injection Simulator is used, everything is in-process.
"""
from __future__ import annotations

import json
import os
import random
import time
from dataclasses import asdict, dataclass
from typing import Callable

FAULT_MODES = (
    "none",
    "consumer_kill",
    "unhandled_error",
    "datastore_reject",
    "datastore_timeout",
)
POINTS = ("before_process", "before_write", "after_write")

# which point each mode is allowed to fire at
_MODE_POINTS = {
    "consumer_kill": ("before_process",),
    "unhandled_error": ("before_write", "after_write"),
    "datastore_reject": ("before_write",),
    "datastore_timeout": ("before_write",),
}


class ConsumerKilled(BaseException):
    """Stand-in for ``os._exit(1)`` when running in the simulator.

    It is a BaseException on purpose so a normal ``except Exception`` in the
    handler cannot swallow it, just like a real process kill.
    """


class InjectedError(RuntimeError):
    """The unhandled_error fault."""


class DatastoreError(Exception):
    """Any datastore failure the handler treats as a per record failure."""


class DatastoreRejected(DatastoreError):
    """The datastore_reject fault (looks like a throttle / refused write)."""


class LambdaTimeout(BaseException):
    """Raised by the simulated sleeper when the invocation runs out of time."""


@dataclass
class FaultConfig:
    mode: str = "none"
    rate: float = 0.0
    # absolute times (epoch seconds live, virtual seconds in the simulator).
    # None means "no window", i.e. the fault is on the whole time.
    window_start: float | None = None
    window_end: float | None = None
    # unhandled_error only: before_write, after_write or auto (pick randomly)
    point: str = "auto"
    # new feature: adaptive visibility timeout
    adaptive_vt: bool = False

    def __post_init__(self) -> None:
        if self.mode not in FAULT_MODES:
            raise ValueError(f"unknown FAULT_MODE {self.mode!r}, expected one of {FAULT_MODES}")
        if not 0.0 <= float(self.rate) <= 1.0:
            raise ValueError("FAULT_RATE must be between 0 and 1")
        self.rate = float(self.rate)
        if self.point not in ("auto",) + POINTS:
            raise ValueError(f"unknown fault point {self.point!r}")

    def active(self, now: float) -> bool:
        if self.mode == "none" or self.rate <= 0.0:
            return False
        if self.window_start is not None and now < self.window_start:
            return False
        if self.window_end is not None and now >= self.window_end:
            return False
        return True

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, raw: str) -> "FaultConfig":
        return cls(**json.loads(raw))

    @classmethod
    def from_env(cls, env: dict | None = None) -> "FaultConfig":
        env = os.environ if env is None else env
        start = env.get("FAULT_WINDOW_START")
        end = env.get("FAULT_WINDOW_END")
        return cls(
            mode=env.get("FAULT_MODE", "none"),
            rate=float(env.get("FAULT_RATE", "0") or 0),
            window_start=float(start) if start else None,
            window_end=float(end) if end else None,
            point=env.get("FAULT_POINT", "auto"),
            adaptive_vt=env.get("ADAPTIVE_VT", "0") == "1",
        )


class RealSleeper:
    """Plain time.sleep, used inside the real Lambda."""

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class FaultInjector:
    def __init__(
        self,
        config: FaultConfig,
        rng: random.Random | None = None,
        clock: Callable[[], float] = time.time,
        sleeper=None,
        hard_kill: bool = False,
    ) -> None:
        self.config = config
        self.rng = rng or random.Random()
        self.clock = clock
        self.sleeper = sleeper or RealSleeper()
        # hard_kill=True only in the deployed Lambda. Locally we raise
        # ConsumerKilled instead so the test process survives.
        self.hard_kill = hard_kill
        self.fired = 0
        self._chosen_point: str | None = None

    def _fires_here(self, point: str) -> bool:
        cfg = self.config
        allowed = _MODE_POINTS.get(cfg.mode, ())
        if point not in allowed:
            return False
        if cfg.mode == "unhandled_error":
            wanted = cfg.point
            if wanted == "auto":
                # decide once per record at the first point we see, so a single
                # record cannot fail twice (before and after the write)
                if point == "before_write":
                    self._chosen_point = self.rng.choice(("before_write", "after_write"))
                wanted = self._chosen_point or "before_write"
            if point != wanted:
                return False
        if not cfg.active(self.clock()):
            return False
        return self.rng.random() < cfg.rate

    def check(self, point: str, remaining_s: float | None = None) -> None:
        """Maybe break things at ``point``. Returns normally when no fault fires."""
        if point not in POINTS:
            raise ValueError(f"unknown injection point {point!r}")
        if not self._fires_here(point):
            return
        self.fired += 1
        mode = self.config.mode
        if mode == "consumer_kill":
            if self.hard_kill:
                os._exit(1)  # pragma: no cover - only ever runs inside Lambda
            raise ConsumerKilled("injected consumer_kill")
        if mode == "unhandled_error":
            raise InjectedError(f"injected unhandled_error at {point}")
        if mode == "datastore_reject":
            raise DatastoreRejected("injected datastore_reject")
        if mode == "datastore_timeout":
            # hang for longer than we have left; the runtime (or the simulated
            # sleeper) ends the invocation, the write never happens
            budget = remaining_s if remaining_s is not None else 900.0
            self.sleeper.sleep(budget + 1.0)
            # if the sleeper did not stop us, still fail rather than write
            raise LambdaTimeout("datastore_timeout outlived the sleeper")


_SSM_CACHE: dict[str, tuple[float, FaultConfig]] = {}


def load_fault_config(env: dict | None = None, ssm_client=None, ttl_s: float = 5.0) -> FaultConfig:
    """Fault config for the deployed functions.

    If FAULT_PARAM_NAME is set the JSON in that SSM parameter wins (so the
    runner can flip faults on and off mid run without redeploying). It is
    cached for a few seconds so we do not hit SSM for every record.
    """
    env = os.environ if env is None else env
    name = env.get("FAULT_PARAM_NAME")
    if not name:
        return FaultConfig.from_env(env)
    now = time.time()
    cached = _SSM_CACHE.get(name)
    if cached and now - cached[0] < ttl_s:
        return cached[1]
    try:
        if ssm_client is None:
            import boto3

            ssm_client = boto3.client("ssm")
        raw = ssm_client.get_parameter(Name=name)["Parameter"]["Value"]
        cfg = FaultConfig.from_json(raw)
    except Exception as exc:  # noqa: BLE001 - fall back to env, never crash on config
        print(json.dumps({"level": "warn", "msg": "fault param read failed", "error": str(exc)}))
        cfg = FaultConfig.from_env(env)
    _SSM_CACHE[name] = (now, cfg)
    return cfg
