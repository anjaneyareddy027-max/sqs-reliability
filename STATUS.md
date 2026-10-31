# STATUS (v2, 24 October 2026)

## Done
- Live v2 round on real AWS (eu-west-1, stack applied 21 Oct 2026 and still deployed):
  170 of 170 planned runs, 17 cells x 10 repeats, seed 2310, randomised order.
  First run started 23:54 IST 23 Oct 2026, last run finished 15:07 IST 24 Oct 2026 (15.2 h).
  Plan: `configs/live_v2.yaml`. Raw data: `results_live_v2/` (not edited after collection).
- New metrics: drain time of the full backlog (visible + in flight + delayed) after the fault
  window, last stored order after the window (from the event log), loss, duplicates, DLQ,
  success, throughput, p50/p95 latency and a cost proxy that now counts every orders table write.
- Analysis: `analysis/v2_quality.py` (rules Q1 to Q3), `v2_analysis.py` (run level bootstrap
  intervals, 10,000 resamples; permutation tests, 20,000 permutations; Holm over 16 contrasts),
  `v2_backlog_fig.py`, `v2_tables.py`. Outputs in `analysis_out_v2/`.
- First round re-analysed with the v2 metric and corrected cost (`analysis/v1_recheck.py`,
  `analysis_out/v1_recheck.json`): cost US$0.0091 -> US$0.0156 (5,014 order writes were missing).
- Tests: 111 passed (`PYTHONPATH=src python -m pytest -q`).
- Final report (21 pages) and configuration manual rewritten for v2.

## Data quality outcome
- 130 runs kept, 40 excluded as host stalls (Q2), 4 kept after the smoke collision fix (Q1:
  3ba822fc, 2a8aee88, a35d885d, edecdf58), 12 kept runs with no drain value (Q3).
- Host stalls: send gaps of 18 to 1004 s and/or the depth sampler stopping early. In 16 excluded
  runs 2,189 orders were delivered twice under different message/request ids (client resends);
  the idempotency check stored each once. Cause is the experiment host pausing, not AWS.
- Sensitivity over all 170 runs is in `analysis_out_v2/v2_sensitivity_all_runs.csv`.

## Headline results (see README for the table)
- Drain after a kill grows with VT: 49.5 s (VT 30), 80.0 s (VT 90), 395 s (VT 600).
- Baseline setting batch 50 + VT 600: +398 s drain vs batch 10 VT 30 (Holm p 0.035).
- Batch 1 drains 32.6 s faster than batch 10 under a kill (Holm p 0.034).
- Kill raises p95 latency by 15.3 s (Holm p 0.042).
- MRC 1 sends 5.69% of good orders to the DLQ (Holm p 0.060).
- Sync arm loses 5.6 to 6.1% of orders; queue arm loses none.
- Cost proxy: about 0.38 US cents per 1,000 orders; whole round US$0.1665 (summary.csv sum),
  US$0.1644 collision corrected.

## Caveats / not done
- Several cells have 6 to 8 kept runs; burst kill only 3. Only 3 of 16 contrasts survive Holm.
- Drain time uses approximate SQS counts, which lag about 20 s in healthy runs; read it relative
  to the no fault reference and check against the last store time.
- p50 latency can be slightly negative (about -0.01 s) in batch 1/50 cells: clock offset between
  the experiment host and Lambda.
- Sync arm 503s are consistent with Lambda throttling at the account limit of 10 concurrent
  executions; the throttle itself was not logged.
- The baseline's 300 s delivery delay was not run.
- Cost is a list price proxy (free tier ignored), not an invoice.
- The GitHub Actions pipeline is defined but has never run (account billing limit).
- The AWS CLI screenshots in the manual were taken in the first round (14 Oct); the v2 stack is the same code reapplied.
- The AWS stack is still deployed; nothing was destroyed. Teardown steps are in the manual.
