# Anji final_1 of 3 (live key-cells)

**Date:** 2026-09-21T17:27:52Z  
**Config:** `configs/live_key_cells.yaml` (4 cells × n=1)  
**Region:** eu-west-1  
**Wall:** 773.9 s live (+ apply/destroy)  
**RUN_EXIT=0 DESTROY_EXIT=0** — TF resources left: **0**

## Cells (loss / dup / DLQ / thr)

| Campaign | VT | MRC | loss | dup | DLQ | thr msg/s |
|----------|---:|----:|-----:|----:|----:|----------:|
| L_vt_consumer_kill | 30 | 5 | 0.0 | 0.000 | 0.0 | 2.38 |
| L_vt_consumer_kill | 90 | 5 | 0.0 | 0.015 | 0.0 | 1.55 |
| L_mrc_unhandled_error | 30 | 1 | 0.0 | 0.000 | 0.18 | 3.89 |
| L_mrc_unhandled_error | 30 | 5 | 0.0 | 0.035 | 0.0 | 2.33 |

## Artefacts
- `summary.csv`, `manifests/` (4), `raw/` (4)
- Log: `/tmp/anji_final_1.log`

**CA2 re-check:** still **100%**. Final_2 / final_3 next.
