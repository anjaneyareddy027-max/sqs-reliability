import random

import pytest

from common.faults import (
    ConsumerKilled,
    DatastoreRejected,
    FaultConfig,
    FaultInjector,
    InjectedError,
    LambdaTimeout,
    load_fault_config,
)
from localsim.clock import SimSleeper


def injector(mode, rate=1.0, point="auto", window=(None, None), now=0.0, sleeper=None, seed=0):
    cfg = FaultConfig(mode=mode, rate=rate, window_start=window[0], window_end=window[1], point=point)
    return FaultInjector(cfg, rng=random.Random(seed), clock=lambda: now, sleeper=sleeper)


def test_unknown_mode_is_rejected():
    with pytest.raises(ValueError):
        FaultConfig(mode="meteor_strike")


@pytest.mark.parametrize("rate", [-0.1, 1.5])
def test_rate_must_be_a_probability(rate):
    with pytest.raises(ValueError):
        FaultConfig(mode="consumer_kill", rate=rate)


def test_window_edges():
    cfg = FaultConfig(mode="consumer_kill", rate=1.0, window_start=10, window_end=20)
    assert not cfg.active(9.99)
    assert cfg.active(10)
    assert cfg.active(19.99)
    assert not cfg.active(20)  # end is exclusive


def test_none_mode_is_never_active():
    assert not FaultConfig().active(5)
    assert not FaultConfig(mode="consumer_kill", rate=0.0).active(5)


def test_json_roundtrip():
    cfg = FaultConfig(mode="datastore_timeout", rate=0.3, window_start=1.5, window_end=9.0, point="before_write")
    assert FaultConfig.from_json(cfg.to_json()) == cfg


def test_from_env():
    cfg = FaultConfig.from_env({"FAULT_MODE": "unhandled_error", "FAULT_RATE": "0.4", "FAULT_WINDOW_START": "5"})
    assert cfg.mode == "unhandled_error"
    assert cfg.rate == pytest.approx(0.4)
    assert cfg.window_start == 5.0
    assert cfg.window_end is None


def test_consumer_kill_only_fires_before_process():
    inj = injector("consumer_kill")
    inj.check("before_write")
    inj.check("after_write")
    with pytest.raises(ConsumerKilled):
        inj.check("before_process")


def test_consumer_kill_is_not_an_exception_subclass():
    # so a handler's `except Exception` cannot swallow a "process kill"
    assert not issubclass(ConsumerKilled, Exception)


def test_unhandled_error_respects_fixed_point():
    inj = injector("unhandled_error", point="after_write")
    inj.check("before_write")
    with pytest.raises(InjectedError):
        inj.check("after_write")


def test_unhandled_error_auto_fires_at_most_once_per_record():
    fired_at = []
    for seed in range(50):
        inj = injector("unhandled_error", point="auto", seed=seed)
        try:
            inj.check("before_write")
        except InjectedError:
            fired_at.append("before")
            continue
        try:
            inj.check("after_write")
        except InjectedError:
            fired_at.append("after")
    # with rate 1 every record fails exactly once, sometimes before, sometimes after
    assert len(fired_at) == 50
    assert {"before", "after"} == set(fired_at)


def test_datastore_reject():
    with pytest.raises(DatastoreRejected):
        injector("datastore_reject").check("before_write")


def test_datastore_timeout_hits_the_sleeper_budget():
    sleeper = SimSleeper(start=0.0, budget_s=15.0)
    inj = injector("datastore_timeout", sleeper=sleeper)
    with pytest.raises(LambdaTimeout):
        inj.check("before_write", remaining_s=sleeper.remaining())
    assert sleeper.used == pytest.approx(15.0)


def test_nothing_fires_outside_the_window():
    inj = injector("consumer_kill", window=(100, 200), now=50)
    inj.check("before_process")
    assert inj.fired == 0


def test_rate_is_roughly_respected():
    inj = injector("datastore_reject", rate=0.3, seed=11)
    hits = 0
    for _ in range(4000):
        try:
            inj.check("before_write")
        except DatastoreRejected:
            hits += 1
    assert 0.27 < hits / 4000 < 0.33


def test_unknown_point_raises():
    with pytest.raises(ValueError):
        injector("consumer_kill").check("somewhere")


class FakeSsm:
    def __init__(self, value=None, fail=False):
        self.value = value
        self.fail = fail
        self.calls = 0

    def get_parameter(self, Name):
        self.calls += 1
        if self.fail:
            raise RuntimeError("no ssm here")
        return {"Parameter": {"Value": self.value}}


def test_load_fault_config_prefers_ssm_and_caches():
    ssm = FakeSsm(FaultConfig(mode="consumer_kill", rate=0.5).to_json())
    env = {"FAULT_PARAM_NAME": "/test/fault-a", "FAULT_MODE": "none"}
    first = load_fault_config(env, ssm_client=ssm)
    second = load_fault_config(env, ssm_client=ssm)
    assert first.mode == "consumer_kill"
    assert second.mode == "consumer_kill"
    assert ssm.calls == 1  # cached


def test_load_fault_config_falls_back_to_env():
    env = {"FAULT_PARAM_NAME": "/test/fault-b", "FAULT_MODE": "datastore_reject", "FAULT_RATE": "0.2"}
    cfg = load_fault_config(env, ssm_client=FakeSsm(fail=True))
    assert cfg.mode == "datastore_reject"
