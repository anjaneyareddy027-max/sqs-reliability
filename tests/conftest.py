"""Shared test fixtures."""
from __future__ import annotations

import random

import pytest

import src  # noqa: F401  - puts src/ on sys.path so `common.*` imports work
from common.faults import FaultConfig, FaultInjector
from common.processing import Deps
from localsim.datastore import InMemoryOrderStore, ListEventLog
from producer.generate_orders import make_orders


class FixedClock:
    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def orders():
    return make_orders(5, "test-run", seed=1)


@pytest.fixture
def make_deps():
    """Factory for handler dependencies with in-memory fakes."""

    def _make(mode: str = "none", rate: float = 0.0, point: str = "auto", idempotent: bool = True,
              sleeper=None, clock=None, seed: int = 3):
        clock = clock or FixedClock(0.0)
        cfg = FaultConfig(mode=mode, rate=rate, point=point) if mode != "none" else FaultConfig()
        injector = FaultInjector(cfg, rng=random.Random(seed), clock=clock, sleeper=sleeper)
        return Deps(
            store=InMemoryOrderStore(),
            events=ListEventLog(),
            injector=injector,
            arm="queue",
            run_id="test-run",
            now=clock,
            remaining_s=lambda: 10.0,
            idempotent=idempotent,
        )

    return _make


def _sqs_record(order, message_id: str = "m-1", receive_count: int = 1) -> dict:
    return {
        "messageId": message_id,
        "receiptHandle": f"rh-{message_id}",
        "body": order.to_json() if hasattr(order, "to_json") else order,
        "attributes": {"ApproximateReceiveCount": str(receive_count)},
        "eventSource": "aws:sqs",
    }


@pytest.fixture
def sqs_record():
    """Builds one record of a Lambda SQS event."""
    return _sqs_record
