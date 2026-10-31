"""Packaging-twin dedupe keeps one row per design cell."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "analysis"))
sys.path.insert(0, str(ROOT))

from load_results import dedupe_packaging_twins, load_runs  # noqa: E402


def test_dedupe_prefers_explicit_adaptive_vt_false():
    twins = [
        {
            "campaign": "A_vt_consumer_kill",
            "repeat": 0,
            "cell": {"visibility_timeout": 30, "batch_size": 10},
            "spec": {},
            "git": {"sha": "aaa"},
            "run_id": "old",
        },
        {
            "campaign": "A_vt_consumer_kill",
            "repeat": 0,
            "cell": {"visibility_timeout": 30, "batch_size": 10},
            "spec": {"adaptive_vt": False},
            "git": {"sha": "bbb"},
            "run_id": "new",
        },
    ]
    out = dedupe_packaging_twins(twins)
    assert len(out) == 1
    assert out[0]["run_id"] == "new"


# the five simulator folders the 350/690 figures refer to; results/live and
# results/localsim were added later and are counted separately
CORE = ["pilot", "baseline", "arms", "campaigns", "burst"]


def test_committed_results_dedupe_to_350():
    df_len = sum(len(load_runs(ROOT / "results" / f)) for f in CORE)
    assert df_len == 350
    raw_len = sum(len(load_runs(ROOT / "results" / f, dedupe_packaging=False)) for f in CORE)
    assert raw_len == 690
