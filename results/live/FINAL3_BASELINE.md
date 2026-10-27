# Anji final-3 baseline compare (live key-cells)

**Date:** 2026-09-21  
**Packs:** `results/live/final_1|final_2|final_3/` (+ prior `initial_eval_1/`, `key_cells/`)  
**Protocol:** 4 cells × n=1; destroy-after each round; TF resources = 0 after each.

## Loss rate (all cells, all three finals)

| Round | VT30 | VT90 | MRC1 | MRC5 |
|-------|-----:|-----:|-----:|-----:|
| final_1 | 0.0 | 0.0 | 0.0 | 0.0 |
| final_2 | 0.0 | 0.0 | 0.0 | 0.0 |
| final_3 | 0.0 | 0.0 | 0.0 | 0.0 |

## DLQ capture (MRC contrast)

| Round | MRC1 DLQ | MRC5 DLQ |
|-------|---------:|---------:|
| final_1 | 0.18 | 0.0 |
| final_2 | 0.17 | 0.0 |
| final_3 | 0.155 | 0.0 |

## Verdict (pos + neg)

- **Positive:** loss=0 stable across 3 finals; MRC=1 consistently shows DLQ capture vs MRC=5 (0); stacks destroyed each round.
- **Negative/mixed:** VT→recovery not claimed monotone on lite n=1; dup rates vary (0–0.12 on MRC5); thr varies by cell.
- Confirmatory H1–H3 remain **localsim-only** (350-cell). Live finals are method-scale smoke, not new Holm tests.

**CA2 floor:** still **100%** after final-3.
