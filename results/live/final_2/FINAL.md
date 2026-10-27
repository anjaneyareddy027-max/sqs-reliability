# Anji final_2 of 3 (live key-cells)

**Date:** 2026-09-21T17:44:24Z  
**Config:** `configs/live_key_cells.yaml` (4 cells × n=1)  
**Region:** eu-west-1  
**Wall:** 774.5 s live (+ apply/destroy)  
**RUN_EXIT=0 DESTROY_EXIT=0** — TF resources left: **0**

## Cells (loss / dup / DLQ / thr)

| Campaign | VT | MRC | loss | dup | DLQ | thr msg/s |
|----------|---:|----:|-----:|----:|----:|----------:|
| L_vt_consumer_kill | 30 | 5 | 0.0 | 0.010 | 0.0 | 2.4 |
| L_vt_consumer_kill | 90 | 5 | 0.0 | 0.015 | 0.0 | 1.7 |
| L_mrc_unhandled_error | 30 | 1 | 0.0 | 0.000 | 0.17 | 4.0 |
| L_mrc_unhandled_error | 30 | 5 | 0.0 | 0.060 | 0.0 | 2.3 |

## Artefacts
- `summary.csv`, `manifests/` (4), `raw/` (4)
- Log: `/tmp/anji_final_2.log`

**CA2 re-check:** still **100%**. Final_3 next.
