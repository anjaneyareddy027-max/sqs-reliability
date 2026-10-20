# Shortcuts. Everything defaults to DRY_RUN=1 (local simulator, free).
PY     ?= .venv/bin/python
STACK  ?= sqs-rr-dev
export DRY_RUN ?= 1

.PHONY: help setup test lint validate build deploy destroy cost guard pilot baseline arms \
        campaigns burst experiments stats figures self-check gates clean aws-verify \
        beyond-ca2-localsim

help:
	@echo "setup | test | lint | validate | pilot | baseline | arms | campaigns | burst"
	@echo "experiments (all of the above runs) | stats | figures | gates"
	@echo "beyond-ca2-localsim   (n=3 lite cells + poller sensitivity, simulator)"
	@echo "deploy | destroy | cost | guard   (live AWS helpers)"

setup:
	scripts/scaffold.sh

test:
	$(PY) -m pytest

lint:
	$(PY) -m ruff check src tests analysis scripts

validate:
	.venv/bin/cfn-lint template.yaml

build:
	sam build

deploy:
	scripts/deploy.sh

destroy:
	scripts/destroy.sh $(STACK)

cost:
	$(PY) scripts/estimate_cost.py --config configs/fault_campaigns.yaml

guard:
	$(PY) scripts/assert_free_tier_guard.py --config configs/pilot.yaml

pilot:
	scripts/run_pilot.sh

baseline:
	scripts/run_baseline.sh

arms:
	$(PY) -m src.control.experiment_runner --config configs/arms.yaml --out results/arms

campaigns:
	scripts/run_fault_campaign.sh --all

burst:
	scripts/run_fault_campaign.sh --load burst --subset configs/key_cells.yaml

experiments: pilot baseline arms campaigns burst

# Beyond-CA2: same 4 lite cells × n=3 + poller-concurrency sensitivity (localsim).
beyond-ca2-localsim:
	$(PY) -m src.control.experiment_runner --config configs/live_key_cells_n3.yaml --out results/localsim/key_cells_n3
	$(PY) -m src.control.experiment_runner --config configs/live_key_cells_concurrency_sensitivity.yaml --out results/localsim/key_cells_concurrency
	$(PY) scripts/summarize_key_cells.py \
		--in results/localsim/key_cells_n3 \
		--in results/localsim/key_cells_concurrency \
		--out results/beyond_ca2/localsim_key_cells_summary.json \
		--csv results/beyond_ca2/localsim_key_cells_summary.csv \
		--note "localsim only; not live AWS" \
		--note "n=3 confirmatory on the same 4 lite cells as configs/live_key_cells.yaml"

stats:
	$(PY) analysis/stats_tests.py --in results --hypotheses H1,H2,H3 --alpha 0.05 --holm

figures:
	$(PY) analysis/plot_results.py --in results --out results/figures

self-check:
	$(PY) analysis/stats_tests.py --self-check

# Phase 8 quality gates from the master prompt
gates: test validate self-check
	test -f docs/CONFIGURATION_MANUAL.md
	test -f results/figures/loss_vs_visibility.png
	@echo "all quality gates passed"

clean:
	rm -rf .aws-sam .pytest_cache results/raw

aws-verify:
	@bash ../../scripts/aws-verify/anji-sqs.sh
