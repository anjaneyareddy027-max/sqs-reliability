#!/usr/bin/env bash
# Phase 3: replicate the Kyrychenko et al. (2025) steady-state study (no faults).
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python
export DRY_RUN=${DRY_RUN:-1}
EXTRA=()
[ "$DRY_RUN" = "0" ] && EXTRA+=(--live)

$PY -m src.control.experiment_runner \
  --config configs/baseline_kyrchenko.yaml \
  --fault none \
  --randomise-order \
  --repeats 5 \
  --out results/baseline/ ${EXTRA[@]+"${EXTRA[@]}"} "$@"
$PY analysis/plot_results.py --in results/baseline --out results/figures/baseline
$PY analysis/stats_tests.py --in results/baseline --hypotheses H0_throughput_config --out results/summary
