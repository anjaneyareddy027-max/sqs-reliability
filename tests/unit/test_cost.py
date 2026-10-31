import pytest

from control.collect_metrics import DEFAULT_PRICING, estimate_cost, load_pricing
from control.config import RunSpec
from control.cost_guard import CostGuardError, check_plan, estimate_plan


def test_cost_math():
    c = estimate_cost({"invocations": 1_000_000, "lambda_gb_s": 400_000, "sqs_receive_calls": 1_000_000})
    assert c["usd_breakdown"]["lambda_requests"] == pytest.approx(0.20)
    assert c["usd_breakdown"]["lambda_compute"] == pytest.approx(6.66668, rel=1e-4)
    assert c["usd_breakdown"]["sqs"] == pytest.approx(0.40)
    assert c["usd_total"] == pytest.approx(sum(c["usd_breakdown"].values()))


def test_pricing_file_overrides_defaults(tmp_path):
    path = tmp_path / "p.yaml"
    path.write_text("prices:\n  sqs_per_million_requests: 0.5\n")
    assert load_pricing(path)["sqs_per_million_requests"] == 0.5
    assert load_pricing(tmp_path / "missing.yaml") == DEFAULT_PRICING


def test_guard_blocks_expensive_plans(monkeypatch):
    monkeypatch.setenv("MAX_ESTIMATED_USD", "0.0000001")
    monkeypatch.setenv("ENABLE_COST_GUARD", "1")
    with pytest.raises(CostGuardError):
        check_plan([RunSpec(run_id="g", order_count=500)], DEFAULT_PRICING)


def test_guard_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("MAX_ESTIMATED_USD", "0.0000001")
    monkeypatch.setenv("ENABLE_COST_GUARD", "0")
    est = check_plan([RunSpec(run_id="g", order_count=500)], DEFAULT_PRICING)
    assert est["usd_total"] > 0


def test_pilot_sized_plan_is_cheap():
    specs = [RunSpec(run_id=f"p{i}", order_count=900, rate_per_sec=10) for i in range(3)]
    assert estimate_plan(specs, DEFAULT_PRICING)["usd_total"] < 0.10
