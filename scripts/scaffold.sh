#!/usr/bin/env bash
# One-time local setup: virtualenv, dependencies, .env and result folders.
#   scripts/scaffold.sh            (uses python3.12 by default)
#   PYTHON=python3.11 scripts/scaffold.sh
set -euo pipefail
cd "$(dirname "$0")/.."

PY=${PYTHON:-python3.12}
if [ ! -d .venv ]; then
  "$PY" -m venv .venv
fi
.venv/bin/pip install -q -U pip
.venv/bin/pip install -q -r requirements-dev.txt

[ -f .env ] || cp .env.example .env
mkdir -p results/manifests results/figures results/summary

echo "setup done. next: make test && make pilot"
