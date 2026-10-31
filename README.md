# SQS reliability and recovery under injected failures (artefact, v2)

MSc in Cloud Computing, Research Project, National College of Ireland
**Title:** Reliability and Recovery of Amazon SQS Messaging under Injected Consumer and Downstream Failures
**Student:** Anjaneya Reddy Gurram (24288853). **Supervisor:** Dr Aqeel Kazmi
**Repository:** https://github.com/anjaneyareddy027-max/sqs-reliability
**Baseline:** Kyrychenko et al. 2025 (batch 50, visibility timeout 600 s, delivery delay 300 s)

This folder is the artefact behind the final report (v2, 24 October 2026). It contains the
Terraform stack, the Lambda code, the fault injection switch, the resumable experiment runner,
the raw data of the live v2 round (170 runs on real AWS, eu-west-1) and the analysis scripts
that produce every table and figure in the report. The configuration manual (PDF) gives the
full step by step guide; this README is the short version.

## Layout

```text
code/
├── terraform/            the live stack (19 resources, tag Project=anji-thesis); no state or credentials in the zip
├── src/
│   ├── queue_consumer/   SQS event source Lambda (partial batch responses, idempotent write)
│   ├── sync_api/         HTTP API Lambda (synchronous control arm, same processing code)
│   ├── common/           models, faults, idempotency, DynamoDB access, metrics, processing
│   ├── producer/         synthetic orders and Poisson / burst load
│   ├── control/          experiment runner (--live, --resume), fault controller, collectors, cost proxy
│   └── localsim/         local simulator used when DRY_RUN=1 (tests, no AWS)
├── configs/              live_v2.yaml (the v2 plan), live_v2_smoke.yaml (smoke copy, other seed),
│                         live_final_n5.yaml (first round), pricing.yaml, ci_smoke.yaml, ...
├── scripts/              build_zips.py (Lambda packages), estimate_cost.py, deploy/destroy helpers
├── analysis/             v2_quality.py, v2_analysis.py, v2_backlog_fig.py, v2_tables.py, v1_recheck.py
├── results_live_v2/      v2 raw data: summary.csv, manifests/*.json, raw/<run_id>/*.csv  (never edited)
├── analysis_out_v2/      v2 outputs: v2_runs.csv, v2_cell_summary.csv, v2_contrasts.csv, figures, v2_extra.json
├── results_live/         first live round (14 Oct, 25 runs); analysis_out/v1_recheck.json re-analyses it
├── results/              simulated and smoke runs used as test fixtures
├── tests/                111 tests (unit + moto integration)
├── requirements-lock.txt pinned Python packages (Python 3.12)
└── STATUS.md             what was done in v2, what is left, known caveats
```

## Reproduce the analysis (no AWS needed)

```bash
cd code
python3.12 -m venv ../venv && ../venv/bin/pip install -r requirements-lock.txt
PYTHONPATH=src ../venv/bin/python -m pytest -q -p no:warnings          # 111 passed
mkdir -p analysis_out_v2
../venv/bin/python analysis/v2_analysis.py results_live_v2 analysis_out_v2 > analysis_out_v2/v2_console.txt
../venv/bin/python analysis/v2_backlog_fig.py results_live_v2 analysis_out_v2
../venv/bin/python analysis/v2_tables.py analysis_out_v2 <report>/tables   # LaTeX tables for the report
../venv/bin/python scripts/verify_reproducibility.py                       # 2,467 reproducibility checks
```

`v2_quality.py` applies the report's data quality rules to the raw files of each run (it never
changes them): Q1 identifier collision with a smoke run (events before the run start dropped,
first stored write relabelled, writes recounted), Q2 host stall (send gap > 5 s, fewer than three
depth samples, or the sampler stopped early with messages still queued; run excluded),
Q3 coarse drain (drain point inside a sample gap > 10 s; drain left empty).

## Rerun the live campaign (own AWS account, costs a few US cents)

```bash
export AWS_DEFAULT_REGION=eu-west-1 PYTHONPATH=src DRY_RUN=0 EXPECT_AWS_ACCOUNT=<account id>
python scripts/build_zips.py && (cd terraform && terraform init && terraform apply)
../venv/bin/python -m control.experiment_runner --config configs/live_v2.yaml --list
nohup ../venv/bin/python -u -m control.experiment_runner --config configs/live_v2.yaml \
      --live --resume --terraform-dir terraform --out results_live_v2 > campaign_v2.log 2>&1 &
```

If the job stops, run the same command again: `--resume` skips every run that already has a
manifest. Run the generator on an always on machine (host pauses cost 40 runs in v2), and use
`configs/live_v2_smoke.yaml` for smoke runs so their run identifiers never collide with the
campaign. Credentials come from the environment or `~/.aws`; never put them in the code.

## Key v2 results (kept runs, mean [95% bootstrap CI])

| Cell | n | Drain after window (s) | Duplicates (%) | Notes |
|------|---|------------------------|----------------|-------|
| No fault | 9 | 23.0 [8.9, 40.4] | 0 | reference, throughput 5.11 msg/s |
| Kill, VT 30 | 7 | 49.5 [44.5, 55.2] | 0.71 | p95 +15.3 s vs no fault (Holm p 0.042) |
| Kill, VT 90 | 9 | 80.0 [61.5, 95.2] | 0.72 | |
| Kill, VT 600 | 8 | 395 [210, 579] | 2.38 | 6 of 8 runs stored the last order 588 to 597 s after the window |
| Kill, batch 1 | 9 | 17.0 [8.2, 25.7] | 0.06 | 32.6 s faster than batch 10 (Holm p 0.034) |
| Kill, batch 50, VT 600 | 8 | 448 [254, 633] | 1.31 | baseline setting: +398 s vs batch 10 VT 30 (Holm p 0.035) |
| Error, MRC 1 | 8 | 35.2 [29.5, 42.3] | 0 | 5.69% of orders to the DLQ, 42 of 91 already stored |
| Sync arm, no fault / kill | 8 / 6 | n/a | n/a | 5.6% / 6.1% of orders lost (HTTP 503 at start up) |

No kept queue run lost an order; no order was applied twice in any of the 170 runs.
Total cost proxy of the round: US$0.1665 (sum of `usd_total` in `results_live_v2/summary.csv`).
Full tables: report Section 6 or `analysis_out_v2/v2_cell_summary.csv`.
