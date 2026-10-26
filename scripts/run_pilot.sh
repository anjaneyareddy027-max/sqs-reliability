#!/usr/bin/env bash
# Pilot runs + the repeat-count check. DRY_RUN=1 (default) uses the simulator.
#   scripts/run_pilot.sh
#   DRY_RUN=0 scripts/run_pilot.sh      (live, needs a deployed stack)
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python
export DRY_RUN=${DRY_RUN:-1}
EXTRA=()
[ "$DRY_RUN" = "0" ] && EXTRA+=(--live)

$PY -m src.control.experiment_runner --config configs/pilot.yaml --out results/pilot ${EXTRA[@]+"${EXTRA[@]}"} "$@"
$PY analysis/stats_tests.py --in results/pilot --power --out results/summary
