"""Tests for the v2 fixes: drain metric, cost proxy, provenance, open-loop sync client."""
import math
import time

from common.metrics import backlog_drain, compute_run_metrics
from control.collect_metrics import estimate_cost
from control.manifest import git_commit, source_tree_hash


def _s(t, v, i=0, d=0):
    return {"t": t, "visible": v, "inflight": i, "delayed": d}


def test_drain_counts_inflight_not_just_visible():
    # visible is 0 from t=52 but 10 messages are still in flight until t=80
    samples = [_s(40, 5, 5), _s(52, 0, 10), _s(60, 0, 10), _s(80, 0, 0), _s(82, 0, 0)]
    d, at = backlog_drain(samples, ref_off=50.0, last_send=20.0)
    assert at == 80 and d == 30


def test_drain_ignores_temporary_zero():
    samples = [_s(52, 0, 0), _s(54, 3, 0), _s(60, 0, 0), _s(62, 0, 0)]
    d, _ = backlog_drain(samples, 50.0, 20.0)
    assert d == 10


def test_drain_censored_when_backlog_left():
    samples = [_s(52, 0, 0), _s(60, 0, 4)]
    assert backlog_drain(samples, 50.0, 20.0) == (None, None)


def test_run_metrics_report_drain_for_no_fault_runs():
    produced = {"a": 0.0, "b": 1.0}
    events = [{"order_id": "a", "outcome": "first_success", "ts": 2.0},
              {"order_id": "b", "outcome": "first_success", "ts": 3.0}]
    m = compute_run_metrics(produced, events, [], samples=[_s(1, 1), _s(51, 0), _s(53, 0)],
                            fault_window=None, nominal_window=(20.0, 50.0))
    assert m["drain_after_window_s"] == 1.0
    assert m["drain_censored"] is False
    assert m["last_store_after_window_s"] == -47.0


def test_cost_counts_order_table_writes():
    base = {"dynamo_event_writes": 400}
    without = estimate_cost(base)
    with_orders = estimate_cost({**base, "dynamo_order_writes": 200})
    diff = with_orders["usd_total"] - without["usd_total"]
    assert math.isclose(diff, 200 / 1e6 * 1.25)
    assert with_orders["dynamodb_writes"] == 600


def test_cost_counts_sync_api_and_logs():
    c = estimate_cost({"api_requests": 1e6, "logs_bytes": 1e9, "sync_invocations": 1e6})
    assert math.isclose(c["usd_breakdown"]["api"], 1.0)
    assert math.isclose(c["usd_breakdown"]["logs_ingestion"], 0.57)
    assert math.isclose(c["usd_breakdown"]["lambda_requests"], 0.20)


def test_provenance_never_unknown():
    g = git_commit()
    assert g["sha"] != "unknown"
    assert len(source_tree_hash()) == 64


def test_sync_client_is_open_loop(monkeypatch):
    from producer import loadgen
    from producer.generate_orders import make_orders

    def slow_post(api_url, order, retries, backoff_s, timeout_s, clock, sleep):
        start = clock()
        time.sleep(0.3)
        return {"order_id": order.order_id, "produced_at": start, "done_at": clock(),
                "status": 200, "attempts": 1, "body": {}}

    monkeypatch.setattr(loadgen, "_post_one", slow_post)
    orders = make_orders(5, "r", 1, 0.0)
    t0 = time.time()
    out = loadgen.post_to_api("http://x", orders, [0.0, 0.01, 0.02, 0.03, 0.04])
    assert len(out) == 5
    assert time.time() - t0 < 1.0  # sequential would take 1.5 s
    assert max(r["produced_at"] for r in out) - t0 < 0.2
