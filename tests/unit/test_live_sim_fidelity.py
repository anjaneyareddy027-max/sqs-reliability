"""Live↔sim fidelity script: relative ranks, not confirmatory n>1."""
import importlib.util
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("live_sim_fidelity", ROOT / "scripts/live_sim_fidelity.py")
fid = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fid)


def test_match_sim_nearest_vt():
    sim = pd.DataFrame([
        {"run_id": "a", "fault_mode": "consumer_kill", "visibility_timeout": 120,
         "max_receive_count": 5, "arm": "queue", "campaign": "A"},
    ])
    m, kind, vt = fid.match_sim(sim, "consumer_kill", 90, 5)
    assert kind.startswith("nearest_vt")
    assert vt == 120
    assert list(m["run_id"]) == ["a"]

    sim = pd.DataFrame([
        {"run_id": "a", "fault_mode": "consumer_kill", "visibility_timeout": 30,
         "max_receive_count": 5, "arm": "queue", "campaign": "A"},
        {"run_id": "b", "fault_mode": "consumer_kill", "visibility_timeout": 30,
         "max_receive_count": 5, "arm": "sync", "campaign": "A"},
    ])
    m = fid.match_sim(sim, "consumer_kill", 30, 5)[0]
    assert list(m["run_id"]) == ["a"]


def test_fidelity_writes(tmp_path):
    live = ROOT / "results/live/key_cells/summary.csv"
    sim = ROOT / "results/campaigns/summary.csv"
    out = tmp_path / "fidelity.json"
    assert fid.main(["--live", str(live), "--sim", str(sim), "--out", str(out)]) == 0
    report = json.loads(out.read_text())
    assert report["confirmatory_live"] is False
    assert len(report["cells"]) == 4
    assert "relative_ranks_agree" in report["direction"]
