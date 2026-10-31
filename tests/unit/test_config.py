from pathlib import Path

import pytest

from control.config import RunSpec, apply_overrides, campaigns_in, expand_campaign, load_yaml, plan_runs

CONFIGS = Path(__file__).resolve().parents[2] / "configs"


def test_matrix_times_repeats():
    specs = expand_campaign("x", {"repeats": 3, "matrix": {"visibility_timeout": [30, 60], "batch_size": [1, 10]}})
    assert len(specs) == 12
    assert len({s.run_id for s in specs}) == 12


def test_run_ids_and_seeds_are_deterministic():
    cfg = {"repeats": 2, "seed": 5, "matrix": {"max_receive_count": [1, 3]}}
    a = [(s.run_id, s.seed) for s in expand_campaign("x", cfg)]
    b = [(s.run_id, s.seed) for s in expand_campaign("x", cfg)]
    assert a == b
    assert len({seed for _, seed in a}) == 4


def test_config_hash_ignores_the_repeat():
    first, second = expand_campaign("x", {"repeats": 2})
    assert first.config_hash() == second.config_hash()
    assert first.run_id != second.run_id


def test_aws_rules_are_enforced():
    with pytest.raises(ValueError):
        RunSpec(batch_size=50, batching_window_s=0)
    with pytest.raises(ValueError):
        RunSpec(visibility_timeout=10, consumer_timeout_s=15)
    # the queue rule does not apply to the sync arm
    RunSpec(arm="sync", visibility_timeout=10, consumer_timeout_s=15)


def test_typos_fail_loudly():
    with pytest.raises(ValueError):
        expand_campaign("x", {"fixed": {"visiblity_timeout": 30}})
    with pytest.raises(ValueError):
        expand_campaign("x", {"matrix": {"nope": [1]}})


def test_override_removes_the_variable_from_the_matrix():
    cfg = apply_overrides({"matrix": {"fault_mode": ["none", "consumer_kill"]}}, {"fault_mode": "none"})
    assert cfg["matrix"] == {}
    assert cfg["fixed"]["fault_mode"] == "none"


def test_randomised_order_is_reproducible():
    cfg = {"seed": 3, "randomise_order": True, "repeats": 2, "matrix": {"visibility_timeout": [30, 60, 120]}}
    a = [s.run_id for s in plan_runs(cfg)]
    b = [s.run_id for s in plan_runs(cfg)]
    assert a == b
    assert sorted(a) == sorted(s.run_id for s in plan_runs({**cfg, "randomise_order": False}))


def test_campaign_defaults_are_merged():
    camps = campaigns_in(
        {"defaults": {"fixed": {"batch_size": 25}}, "campaigns": {"a": {"fixed": {"fault_mode": "consumer_kill"}}}}
    )
    assert camps["a"]["fixed"] == {"batch_size": 25, "fault_mode": "consumer_kill"}


@pytest.mark.parametrize(
    "name,expected",
    [
        ("pilot", 18),
        ("arms", 10),
        ("key_cells", 20),
        ("fault_campaigns", 187),
        ("baseline_kyrchenko", 109),
        ("live_key_cells", 4),
        ("live_key_cells_n3", 12),
        ("live_key_cells_concurrency_sensitivity", 8),
    ],
)
def test_shipped_configs_expand(name, expected):
    assert len(plan_runs(load_yaml(CONFIGS / f"{name}.yaml"))) == expected


def test_live_key_cells_n3_matches_lite_cells():
    lite = plan_runs(load_yaml(CONFIGS / "live_key_cells.yaml"))
    n3 = plan_runs(load_yaml(CONFIGS / "live_key_cells_n3.yaml"))
    lite_cells = {(s.campaign, s.fault_mode, s.visibility_timeout, s.max_receive_count) for s in lite}
    n3_cells = {(s.campaign, s.fault_mode, s.visibility_timeout, s.max_receive_count) for s in n3}
    assert lite_cells == n3_cells
    assert {s.repeat for s in n3} == {0, 1, 2}
    assert all(s.max_concurrency == 5 for s in n3)
    assert all(s.order_count == 200 for s in n3)
