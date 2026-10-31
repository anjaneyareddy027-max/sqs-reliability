"""End to end in DRY_RUN mode: runner -> manifests -> stats -> figures."""
import json
import sys
from pathlib import Path

from control import experiment_runner

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "analysis"))

import load_results  # noqa: E402
import plot_results  # noqa: E402
import stats_tests  # noqa: E402


def test_pilot_dry_run_writes_manifests(tmp_path, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "1")
    rc = experiment_runner.main(["--config", str(ROOT / "configs/pilot.yaml"), "--out", str(tmp_path), "--quiet"])
    assert rc == 0
    manifests = list((tmp_path / "manifests").glob("*.json"))
    assert len(manifests) == 18
    m = json.loads(manifests[0].read_text())
    for key in ("git", "config_hash", "region", "runtime", "started_at", "finished_at", "fault_schedule", "seed"):
        assert key in m
    assert m["backend"] == "localsim"
    assert "not an AWS measurement" in m["measurement_kind"]
    assert (tmp_path / "summary.csv").exists()
    df = load_results.load_runs(tmp_path)
    assert len(df) == 18
    assert set(df["campaign"]) == {"pilot"}


def test_fault_and_vary_pick_the_one_factor_campaign(tmp_path):
    experiment_runner.main([
        "--config", str(ROOT / "configs/fault_campaigns.yaml"), "--fault", "consumer_kill",
        "--vary", "visibility_timeout", "--repeats", "1", "--orders", "600",
        "--out", str(tmp_path), "--quiet", "--no-raw",
    ])
    df = load_results.load_runs(tmp_path)
    assert set(df["campaign"]) == {"A_vt_consumer_kill"}
    assert len(df) == 5


def test_summary_is_rebuilt_from_all_manifests(tmp_path):
    base = ["--config", str(ROOT / "configs/fault_campaigns.yaml"), "--repeats", "1", "--orders", "400",
            "--out", str(tmp_path), "--quiet", "--no-raw"]
    experiment_runner.main(base + ["--campaign", "B_mrc_unhandled_error"])
    experiment_runner.main(base + ["--campaign", "C_mrc_datastore_reject"])
    lines = (tmp_path / "summary.csv").read_text().strip().splitlines()
    assert len(lines) == 1 + 8  # header + 4 + 4 runs


def test_stats_and_figures_on_small_campaigns(tmp_path):
    out = tmp_path / "res"
    experiment_runner.main([
        "--config", str(ROOT / "configs/fault_campaigns.yaml"),
        "--campaign", "A_vt_consumer_kill", "--campaign", "B_mrc_unhandled_error",
        "--campaign", "E_guidance_transfer", "--repeats", "3", "--out", str(out), "--quiet",
    ])
    summary = tmp_path / "summary"
    assert stats_tests.main(["--in", str(out), "--hypotheses", "H1,H2,H3", "--holm", "--out", str(summary)]) == 0
    payload = json.loads((summary / "stats_H1_H2_H3.json").read_text())
    names = {r["name"] for r in payload["confirmatory"]}
    assert names == {"H1", "H2", "H3_loss", "H3_recovery"}
    for r in payload["confirmatory"]:
        assert r["p_adjusted"] >= r["p"] - 1e-12
        assert r["decision"] in ("reject H0", "fail to reject H0")
    assert (summary / "hypotheses.md").exists()

    figs = tmp_path / "figs"
    assert plot_results.main(["--in", str(out), "--out", str(figs)]) == 0
    for name in ("loss_vs_visibility.png", "recovery_vs_max_receive_count.png", "guidance_transfer.png",
                 "timeline_visible_vs_backlog.png"):
        assert (figs / name).exists(), name


def test_single_run_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("FAULT_MODE", "unhandled_error")
    monkeypatch.setenv("FAULT_RATE", "0.2")
    monkeypatch.setenv("ORDER_COUNT", "300")
    monkeypatch.setenv("VISIBILITY_TIMEOUT", "30")
    monkeypatch.setenv("RUN_ID", "env-run-1")
    assert experiment_runner.main(["--from-env", "--out", str(tmp_path), "--quiet", "--no-raw"]) == 0
    m = json.loads((tmp_path / "manifests" / "env-run-1.json").read_text())
    assert m["spec"]["fault_mode"] == "unhandled_error"
    assert m["spec"]["order_count"] == 300


def test_live_flag_needs_dry_run_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DRY_RUN", "1")
    experiment_runner.main(["--config", str(ROOT / "configs/arms.yaml"), "--live", "--limit", "1",
                            "--out", str(tmp_path), "--quiet", "--no-raw"])
    assert "staying on the local simulator" in capsys.readouterr().out
