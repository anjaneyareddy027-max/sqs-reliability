import math

import pytest

from common.metrics import compute_run_metrics, percentile, recovery_time
from common.models import Outcome


def ev(oid, outcome, ts):
    return {"order_id": oid, "outcome": outcome, "ts": ts}


def samples_from(values, step=5.0):
    return [{"t": i * step, "visible": v, "inflight": 0, "delayed": 0} for i, v in enumerate(values)]


def test_percentile():
    assert math.isnan(percentile([], 50))
    assert percentile([5], 95) == 5
    assert percentile([1, 2, 3, 4], 50) == 2.5
    assert percentile(list(range(101)), 95) == 95


def test_rates_add_up():
    produced = {"a": 0, "b": 0, "c": 0, "d": 0, "e": 1}
    events = [
        ev("a", Outcome.FIRST_SUCCESS, 1),
        ev("a", Outcome.DUPLICATE_SUCCESS, 2),
        ev("b", Outcome.FIRST_SUCCESS, 3),
        ev("c", Outcome.WRITE_REJECTED, 1),
    ]
    m = compute_run_metrics(produced, events, dlq_order_ids=["c"], remaining_ids=["d"])
    assert m["unique_success"] == 2
    assert m["success_rate"] == pytest.approx(0.4)
    assert m["dlq_capture_rate"] == pytest.approx(0.2)
    assert m["duplicate_rate"] == pytest.approx(0.5)  # 1 extra event / 2 unique
    assert m["loss_rate"] == pytest.approx(0.4)  # d and e are not accounted for
    assert m["stranded_rate"] == pytest.approx(0.2)  # d is still queued
    assert m["true_loss_rate"] == pytest.approx(0.2)  # e just vanished
    assert m["latency_p50_s"] == pytest.approx(2.0)


def test_processed_and_dead_lettered_counts_once():
    m = compute_run_metrics({"a": 0}, [ev("a", Outcome.FIRST_SUCCESS, 1)], ["a"])
    assert m["loss_rate"] == 0
    assert m["accounted"] == 1


def test_unsafe_double_apply_is_counted():
    events = [ev("a", Outcome.FIRST_SUCCESS, 1), ev("a", Outcome.UNSAFE_DOUBLE_APPLY, 2)]
    m = compute_run_metrics({"a": 0}, events, [])
    assert m["unsafe_double_apply"] == 1
    assert m["duplicate_rate"] == 1.0


def test_recovery_time_basic():
    # t 0-55 depth 2, t 60-85 depth 50, from t 90 back to 2; fault off at 75
    s = samples_from([2] * 12 + [50] * 6 + [2] * 6)
    rec, upper = recovery_time(s, fault_on=60, fault_off=75)
    assert rec == 15.0
    assert upper == pytest.approx(7.0)  # mean 2 + min tolerance 5


def test_recovery_needs_consecutive_samples():
    # dips back into the band at 75 but jumps out again at 80
    s = samples_from([0] * 12 + [40] * 3 + [0, 40, 0, 0, 0])
    rec, _ = recovery_time(s, fault_on=60, fault_off=75)
    assert rec == 10.0


def test_never_recovers():
    rec, _ = recovery_time(samples_from([1] * 12 + [30] * 10), 60, 70)
    assert rec is None


def test_visible_depth_can_hide_the_backlog():
    # failed messages sit invisible (in flight) until t=600: visible depth
    # never moves, the real backlog does
    s = [{"t": t, "visible": 0, "inflight": 100 if 60 <= t < 600 else 0, "delayed": 0} for t in range(0, 700, 5)]
    assert recovery_time(s, 60, 120, key="visible")[0] == 0.0
    assert recovery_time(s, 60, 120, key="backlog")[0] == 480.0


def test_run_metrics_with_fault_window():
    s = samples_from([1] * 12 + [30] * 4 + [1] * 5)
    m = compute_run_metrics({"a": 0}, [ev("a", Outcome.FIRST_SUCCESS, 1)], [], samples=s, fault_window=(60, 70))
    assert m["recovered"] is True
    assert m["recovery_time_s"] == 10.0


def test_censored_recovery_is_flagged():
    s = samples_from([1] * 12 + [30] * 10)
    m = compute_run_metrics({"a": 0}, [ev("a", Outcome.FIRST_SUCCESS, 1)], [], samples=s, fault_window=(60, 70))
    assert m["recovered"] is False
    assert math.isnan(m["recovery_time_s"])
    assert m["recovery_censored_at_s"] == pytest.approx(105 - 70)
