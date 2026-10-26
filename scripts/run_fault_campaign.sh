#!/usr/bin/env bash
# Phase 5 fault campaigns.
#   scripts/run_fault_campaign.sh --fault consumer_kill --vary visibility_timeout      # A
#   scripts/run_fault_campaign.sh --fault unhandled_error --vary max_receive_count     # B
#   scripts/run_fault_campaign.sh --fault datastore_reject --vary max_receive_count    # C
#   scripts/run_fault_campaign.sh --fault datastore_timeout --vary visibility_timeout  # D
#   scripts/run_fault_campaign.sh --campaign E_guidance_transfer
#   scripts/run_fault_campaign.sh --load burst --subset configs/key_cells.yaml         # burst
#   scripts/run_fault_campaign.sh --all
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python

CONFIG=configs/fault_campaigns.yaml
OUT=results/campaigns
ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --subset)  CONFIG="$2"; OUT=results/burst; shift 2 ;;
    --out)     OUT="$2"; shift 2 ;;
    --load)    ARGS+=(--load "$2"); shift 2 ;;
    --fault|--vary|--campaign|--repeats|--limit) ARGS+=("$1" "$2"); shift 2 ;;
    --all)     shift ;;
    *) echo "unknown option: $1"; exit 2 ;;
  esac
done

export DRY_RUN=${DRY_RUN:-1}
[ "$DRY_RUN" = "0" ] && ARGS+=(--live)

$PY -m src.control.experiment_runner --config "$CONFIG" --out "$OUT" ${ARGS[@]+"${ARGS[@]}"}
