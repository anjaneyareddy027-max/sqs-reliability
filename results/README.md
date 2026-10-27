# results/

> **Everything committed in this folder comes from the local simulator
> (`DRY_RUN=1`, `backend: localsim` in every manifest). These are NOT
> measurements of Amazon SQS.** They show that the harness, metrics and
> analysis work end to end and what the modelled SQS semantics imply. The
> numbers depend on the simulator's timing assumptions (`sim:` block in
> `control/config.py`). Live AWS runs go into the same structure with
> `backend: live` and have to be reported separately.

## Layout

| Folder | What | Design cells | On-disk manifests |
|--------|------|-------------:|------------------:|
| `pilot/` | pilot (`configs/pilot.yaml`) | 18 | 36 |
| `baseline/` | no-fault replication of Kyrychenko et al. (2025) (`configs/baseline_kyrchenko.yaml`, `--repeats 5`) | 115 | 230 |
| `arms/` | sync vs queue arm, no fault (`configs/arms.yaml`) | 10 | 10 |
| `campaigns/` | fault campaigns A–H (`configs/fault_campaigns.yaml`) | 187 | 374 |
| `burst/` | burst replication of key cells (`configs/key_cells.yaml`) | 20 | 40 |
| `summary/` | `hypotheses.md`, `stats_*.json`, `power_from_pilot.json` | - | - |
| `figures/` | all figures (`analysis/plot_results.py`) | - | - |
| **Total** | unique `(campaign, cell, repeat)` after packaging-dedup | **350** | **690** |
| `live/key_cells/` | live AWS lite 4-cell × n=1 (destroyed after round) | 4 | 4 |
| `live/initial_eval_1/` | CA2 gate: ONE live initial eval (same lite config; destroyed) | 4 | 4 |
| `localsim/key_cells_n3/` | beyond-CA2 localsim n=3 on the same 4 lite cells | 12 | 12 |
| `localsim/key_cells_concurrency/` | beyond-CA2 localsim ESM poller 2 vs 5 | 8 | 8 |
| `beyond_ca2/` | concurrency probe + localsim cell aggregation | - | - |

On-disk extras are **adaptive_vt packaging twins** (same seed/metrics; distinct
`run_id` because `spec.adaptive_vt: false` was added later). Analysis
(`analysis/load_results.py`) keeps one row per design cell. Do not treat 690
as 690 independent repeats.

Each step folder has `manifests/<RUN_ID>.json` (source of truth) and a
`summary.csv` rebuilt from them. Raw per-run dumps (`raw/`) are gitignored;
`make experiments` regenerates them.

## What the simulated runs show (short)

* **No faults (baseline):** visibility timeout has no effect on throughput;
  batch size dominates (median ~161 msg/s at batch 1 vs ~375 msg/s at batch 50
  with 5 pollers). Duplicate floor from at-least-once delivery ~0.002.
  Delivery delay only shifts latency by the delay.
* **Loss:** zero in the queue arm for every visibility timeout and every fault
  type (DLQ + redelivery, 900 s horizon), so H1 has nothing to test. The sync
  arm loses 0.1-0.6 % of orders under faults once the client retries run out.
* **Recovery time follows the visibility timeout almost 1:1** (consumer_kill:
  ~33 s at VT 30 up to 600 s at VT 600; datastore_timeout similar). The
  visible-only measure says 0-24 s for all of them because the failed messages
  sit *in flight*, see `figures/timeline_visible_vs_backlog.png`.
* **maxReceiveCount does not change recovery** after a transient fault
  (~31-36 s at VT 30 for 1/3/5/10, H2 not rejected; H=2.43, p_adj=1.0 after dedup) but it decides where the
  failed messages end up: maxReceiveCount = 1 dead-letters ~29 % of all orders
  under unhandled_error (~17 % under datastore_reject) that would have succeeded
  on a retry; 3 or more sends almost nothing to the DLQ but brings duplicates
  (~0.14 per processed order).
* **Steady-state optimum under fault (H3):** From packaging-deduped
  `stats_H1_H2_H3.json` (`runs: 350`), H3_loss is not estimable (loss
  identically 0). H3_recovery **fails to reject H0 after Holm**: Mann–Whitney
  U = 62.5, raw p = 0.02106, adjusted p = 0.08425, rank-biserial r = 0.667;
  optimal (VT 600 / batch 50) mean recovery 600 s vs alternatives mean 224 s
  (n_opt=5, n_rest=15). Twin-inflated n=10 / U=250 / p_adj=0.003 drafts are
  withdrawn.

Full tables: `summary/hypotheses.md`; group means and 95 % bootstrap CIs:
`summary/stats_H1_H2_H3.json`.

## Regenerate

```bash
make experiments stats figures
```

Run ids and seeds are derived from the configs, so the same manifests (same
ids, same metrics) come out again; only timestamps and the git sha differ.
