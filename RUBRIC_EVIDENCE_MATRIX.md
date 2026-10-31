# Rubric evidence matrix — Anji (SQS reliability)

**Driver:** `/RUBRIC_70_TO_100_STRATEGY.md`  
**Artefact:** `anji-thesis/sqs-reliability-recovery/`  
**CA2:** 100% | **INITIAL_EVAL_PASS:** yes | **Final-3:** done  
**Rubric70:** **yes** (reference fold)

| Rubric requirement | Where evidence exists | Concrete evidence | Gap to 80+/90+ |
|--------------------|----------------------|-------------------|----------------|
| Objectives fully achieved | STATUS; FINAL3_BASELINE | loss=0; MRC=1 DLQ contrast | Keep Obj map in LaTeX |
| Critical literature review | alignment / report | vs Kyrychenko steady-state | Maintain critique |
| Alternatives considered | configs; DESIGN_RATIONALE | localsim confirmatory vs live lite | Documented |
| Methodology justified | ANALYSIS; live_key_cells | VT / MRC factors; destroy-after | — |
| Rigorous implementation | terraform; final_1–3 | 4/4 cells ×3 destroyed | — |
| Rigorous evaluation | STATUS Rubric70; FINAL3 | pos+neg; VT non-monotone | Live n>1 soft |
| Synthesis of data | key_cells + finals | MRC=1 trades success for DLQ | — |
| Relevant theory | STATUS | Fault reliability ≠ no-fault thr guidance | — |
| Insightful conclusions | STATUS | Lite not campaign-A VT law | — |
| Academic / practitioner implications | STATUS | Obj3 reliability under failure | — |
| Validity / generalisability / limitations | STATUS | n=1 smoke; H1–H3 localsim-only | Soft live n=3 |
| Reproducibility | config manual; destroy | — | — |
| Viva evidence | STATUS Rubric70 notes | Defend neg VT | — |

**Scoreboard:** yes · **Updated:** 2026-09-22
