import json

from control.collect_metrics import metrics_from_result
from control.config import RunSpec
from localsim.engine import simulate


def run(**kw):
    base = dict(run_id="eng", order_count=1200, rate_per_sec=20, fault_start_s=20, fault_window_s=20, drain_timeout_s=900)
    base.update(kw)
    spec = RunSpec(**base)
    res = simulate(spec)
    return spec, res, metrics_from_result(spec, res)


def test_same_spec_gives_the_same_numbers():
    _, _, a = run(fault_mode="consumer_kill", fault_rate=0.25)
    _, _, b = run(fault_mode="consumer_kill", fault_rate=0.25)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_no_fault_processes_everything():
    _, res, m = run()
    assert m["success_rate"] == 1.0
    assert m["loss_rate"] == 0.0
    assert m["dlq_capture_rate"] == 0.0
    assert res.counters.get("invocation_ok") == res.counters["invocations"]
    assert m["duplicate_rate"] < 0.02  # only the at-least-once floor


def test_consumer_kill_does_not_lose_messages():
    _, res, m = run(fault_mode="consumer_kill", fault_rate=0.25)
    assert res.counters["invocation_killed"] > 0
    assert m["loss_rate"] == 0.0
    assert m["duplicate_rate"] > 0.0


def test_low_max_receive_count_dead_letters_transient_failures():
    _, _, low = run(fault_mode="unhandled_error", fault_rate=0.25, max_receive_count=1, visibility_timeout=30)
    _, _, high = run(fault_mode="unhandled_error", fault_rate=0.25, max_receive_count=10, visibility_timeout=30)
    assert low["dlq_capture_rate"] > high["dlq_capture_rate"]


def test_longer_visibility_timeout_slows_recovery():
    _, _, short = run(fault_mode="consumer_kill", fault_rate=0.25, visibility_timeout=30)
    _, _, long_ = run(fault_mode="consumer_kill", fault_rate=0.25, visibility_timeout=300)
    assert long_["recovery_time_s"] > short["recovery_time_s"]


def test_sync_arm_loses_orders_when_retries_run_out():
    _, _, m = run(arm="sync", fault_mode="unhandled_error", fault_rate=1.0, fault_point="before_write",
                  sync_client_retries=0)
    assert m["loss_rate"] > 0.0
    assert m["success_rate"] > 0.5  # everything outside the window still works


def test_idempotency_off_double_applies():
    _, res, m = run(fault_mode="unhandled_error", fault_rate=0.25, fault_point="after_write", idempotency=False)
    assert res.max_apply_count > 1
    assert m["unsafe_double_apply"] > 0


def test_poison_orders_end_up_in_the_dlq():
    _, _, m = run(poison_rate=0.02, max_receive_count=3, visibility_timeout=30)
    assert m["dlq_capture_rate"] > 0.0
    assert m["loss_rate"] == 0.0


def test_batch_size_helps_throughput_on_a_backlog():
    _, _, b1 = run(load_profile="batch", batch_size=1, order_count=3000)
    _, _, b50 = run(load_profile="batch", batch_size=50, order_count=3000)
    assert b50["throughput_msg_s"] > b1["throughput_msg_s"]


def test_datastore_timeout_burns_the_function_timeout():
    _, res, _ = run(fault_mode="datastore_timeout", fault_rate=0.25)
    assert res.counters["invocation_timeout"] > 0


def test_lite_cells_zero_loss_at_reduced_poller_concurrency():
    """Beyond-CA2: ESM max_concurrency=2 still preserves queue-arm loss floor on lite load."""
    shared = dict(
        order_count=200,
        rate_per_sec=10,
        fault_start_s=20,
        fault_window_s=30,
        drain_timeout_s=180,
        fault_rate=0.25,
        max_concurrency=2,
        batch_size=10,
    )
    _, _, kill = run(fault_mode="consumer_kill", visibility_timeout=30, max_receive_count=5, **shared)
    _, _, err = run(fault_mode="unhandled_error", visibility_timeout=30, max_receive_count=5, **shared)
    assert kill["loss_rate"] == 0.0
    assert err["loss_rate"] == 0.0
