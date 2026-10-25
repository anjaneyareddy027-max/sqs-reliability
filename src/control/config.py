"""Loading experiment configs and expanding them into individual runs.

A config (or one campaign inside ``fault_campaigns.yaml``) looks like::

    name: A_vt_consumer_kill
    repeats: 5
    seed: 42
    randomise_order: true
    fixed:            # held constant for every run
      fault_mode: consumer_kill
      batch_size: 10
    matrix:           # every combination of these is one cell
      visibility_timeout: [30, 60, 120, 300, 600]

Each (cell, repeat) becomes one ``RunSpec`` with its own derived seed and a
deterministic RUN_ID, so re-running a config reproduces the same runs.
"""
from __future__ import annotations

import copy
import hashlib
import itertools
import json
import random
import uuid
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml

from common.faults import FAULT_MODES, POINTS
from producer.loadgen import PROFILES

ARMS = ("queue", "sync")
RUN_NAMESPACE = uuid.UUID("6f1c2b1e-3a57-4c1e-9d4e-5a0c0f5e7a11")


@dataclass
class SimParams:
    """Simulator timing assumptions. NOT measured AWS numbers - calibrate from the pilot."""

    cold_start_s: float = 0.35
    invoke_overhead_s: float = 0.02
    parse_s: float = 0.002
    write_median_s: float = 0.008
    write_sigma: float = 0.4
    dup_prob: float = 0.002
    error_backoff_s: float = 1.0
    long_poll_s: float = 20.0
    api_overhead_s: float = 0.03
    memory_mb: int = 256


def _default_recovery() -> dict[str, float]:
    return {"k_sd": 2.0, "min_tol": 5.0, "consecutive": 3, "pre_window_s": 30.0}


@dataclass
class RunSpec:
    run_id: str = ""
    campaign: str = "adhoc"
    repeat: int = 0
    seed: int = 42
    # architecture + workload
    arm: str = "queue"
    load_profile: str = "normal"
    order_count: int = 1000
    rate_per_sec: float = 20.0
    burst_start_s: float = 30.0
    burst_len_s: float = 15.0
    burst_factor: float = 5.0
    poison_rate: float = 0.0
    # SQS / event source mapping
    visibility_timeout: int = 30
    adaptive_vt: bool = False
    max_receive_count: int = 5
    batch_size: int = 10
    batching_window_s: float = 1.0
    delivery_delay: int = 0
    consumer_timeout_s: int = 15
    max_concurrency: int = 5
    idempotency: bool = True
    # sync arm client behaviour
    sync_client_retries: int = 2
    sync_backoff_s: float = 0.2
    sync_timeout_s: int = 10
    # fault schedule (seconds from run start)
    fault_mode: str = "none"
    fault_rate: float = 0.0
    fault_start_s: float = 60.0
    fault_window_s: float = 60.0
    fault_point: str = "auto"
    # observation
    drain_timeout_s: float = 600.0
    sample_interval_s: float = 5.0
    recovery: dict[str, float] = field(default_factory=_default_recovery)
    region: str = "eu-west-1"
    sim: SimParams = field(default_factory=SimParams)

    def __post_init__(self) -> None:
        if isinstance(self.sim, dict):
            self.sim = SimParams(**self.sim)
        self.recovery = {**_default_recovery(), **(self.recovery or {})}
        if self.arm not in ARMS:
            raise ValueError(f"arm must be one of {ARMS}")
        if self.load_profile not in PROFILES:
            raise ValueError(f"load_profile must be one of {PROFILES}")
        if self.fault_mode not in FAULT_MODES:
            raise ValueError(f"fault_mode must be one of {FAULT_MODES}")
        if self.fault_point not in ("auto",) + POINTS:
            raise ValueError("bad fault_point")
        if not 1 <= int(self.batch_size) <= 10000:
            raise ValueError("batch_size must be 1..10000 for a standard queue")
        # the two rules below are enforced by AWS when you create the mapping,
        # so a config that breaks them could never be deployed anyway
        if int(self.batch_size) > 10 and self.batching_window_s < 1:
            raise ValueError("batch_size > 10 needs a batching window of at least 1 s")
        if self.arm == "queue" and self.visibility_timeout < self.consumer_timeout_s:
            raise ValueError("visibility timeout must be >= the consumer function timeout")
        if not 1 <= int(self.max_receive_count) <= 1000:
            raise ValueError("maxReceiveCount must be 1..1000")
        if self.order_count <= 0:
            raise ValueError("order_count must be positive")

    @property
    def fault_window(self) -> tuple[float, float] | None:
        if self.fault_mode == "none":
            return None
        return (float(self.fault_start_s), float(self.fault_start_s + self.fault_window_s))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def config_hash(self) -> str:
        """Hash of the configuration only (not of run id / repeat / seed)."""
        data = self.to_dict()
        for key in ("run_id", "repeat", "seed", "campaign"):
            data.pop(key, None)
        raw = json.dumps(data, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()[:12]

    def cell(self) -> dict[str, Any]:
        """The independent variable values of this run."""
        return {
            "arm": self.arm,
            "load_profile": self.load_profile,
            "fault_mode": self.fault_mode,
            "visibility_timeout": self.visibility_timeout,
            "max_receive_count": self.max_receive_count,
            "batch_size": self.batch_size,
            "delivery_delay": self.delivery_delay,
        }


SPEC_FIELDS = {f.name for f in fields(RunSpec)}


def load_yaml(path: str | Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top level must be a mapping")
    return data


def deep_merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in (extra or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def campaigns_in(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """A file is either one experiment or a ``campaigns:`` mapping with ``defaults:``."""
    if "campaigns" in config:
        defaults = config.get("defaults", {})
        return {name: deep_merge(defaults, camp) for name, camp in config["campaigns"].items()}
    return {config.get("name", "adhoc"): config}


def derive_seed(seed: int, name: str, values: dict[str, Any], rep: int) -> int:
    raw = f"{seed}|{name}|{json.dumps(values, sort_keys=True)}|{rep}"
    return int(hashlib.sha256(raw.encode()).hexdigest()[:8], 16)


def expand_campaign(name: str, cfg: dict[str, Any]) -> list[RunSpec]:
    base: dict[str, Any] = {}
    # scalars written at the top level of the campaign count as fixed values too
    for key, value in cfg.items():
        if key in SPEC_FIELDS and key not in ("run_id", "repeat", "seed", "campaign"):
            base[key] = value
    base = deep_merge(base, cfg.get("fixed", {}))
    unknown = set(base) - SPEC_FIELDS
    if unknown:
        raise ValueError(f"{name}: unknown settings {sorted(unknown)}")

    matrix = cfg.get("matrix", {}) or {}
    for key, levels in matrix.items():
        if key not in SPEC_FIELDS:
            raise ValueError(f"{name}: unknown matrix variable {key!r}")
        if not isinstance(levels, list) or not levels:
            raise ValueError(f"{name}: matrix variable {key!r} needs a non empty list")

    repeats = int(cfg.get("repeats", 1))
    seed = int(cfg.get("seed", 42))
    keys = sorted(matrix)
    combos = list(itertools.product(*(matrix[k] for k in keys))) if keys else [()]

    specs: list[RunSpec] = []
    for combo in combos:
        values = dict(zip(keys, combo))
        for rep in range(repeats):
            params = {**base, **values}
            spec = RunSpec(**params, campaign=name, repeat=rep, seed=derive_seed(seed, name, values, rep))
            spec.run_id = str(uuid.uuid5(RUN_NAMESPACE, f"{name}|{spec.config_hash()}|{rep}|{spec.seed}"))
            specs.append(spec)
    return specs


def apply_overrides(cfg: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """CLI flags (--fault, --arm, --load, --repeats ...) beat the file.

    A fixed override also removes that variable from the matrix, otherwise
    ``--fault none`` on a campaign that varies fault_mode would be ignored.
    """
    cfg = copy.deepcopy(cfg)
    fixed = cfg.setdefault("fixed", {})
    matrix = cfg.setdefault("matrix", {})
    for key, value in overrides.items():
        if value is None:
            continue
        if key in ("repeats", "seed", "randomise_order"):
            cfg[key] = value
            continue
        if key not in SPEC_FIELDS:
            raise ValueError(f"cannot override unknown setting {key!r}")
        fixed[key] = value
        matrix.pop(key, None)
        cfg.pop(key, None)
    return cfg


def plan_runs(config: dict[str, Any], select: list[str] | None = None, overrides: dict[str, Any] | None = None) -> list[RunSpec]:
    """All runs of a config file, filtered by campaign name, in execution order."""
    camps = campaigns_in(config)
    if select:
        missing = [s for s in select if s not in camps]
        if missing:
            raise KeyError(f"no such campaign(s): {missing}; have {sorted(camps)}")
        camps = {k: v for k, v in camps.items() if k in select}
    specs: list[RunSpec] = []
    randomise = False
    seed = int(config.get("seed", 42))
    for name, camp in camps.items():
        camp = apply_overrides(camp, overrides or {})
        randomise = randomise or bool(camp.get("randomise_order", False))
        seed = int(camp.get("seed", seed))
        specs.extend(expand_campaign(name, camp))
    if randomise:
        # randomised run order spreads provider drift over all cells
        # (Eismann et al., 2022) instead of confounding it with one of them
        random.Random(seed).shuffle(specs)
    return specs
