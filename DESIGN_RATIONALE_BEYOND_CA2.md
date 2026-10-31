# Design rationale — beyond CA2 (Anji)

**Policy:** CA2 is a floor, not a ceiling (`_analysis_extract/reports/CA2_FLOOR_NOT_CEILING.md`).

**Date:** 2026-09-20

## CA2 floor (met)

- Localsim confirmatory H1–H3 (packaging-deduped $n{=}5$, 350 design runs): `results/summary/stats_H1_H2_H3.json`
- Live AWS SQS key-cell round **4/4**, $n{=}1$, eu-west-1, destroyed after round: `results/live/key_cells/`
- Live↔localsim descriptive delta disclosed (directional; not a fidelity hypothesis)

Terraform names/tags use project slug `sqs-rr` only (no student name or student ID).

## Beyond-CA2 confirmatory plan (live $n{=}3$)

**Intent:** Repeat the **same four lite cells** in `configs/live_key_cells.yaml` with $n{=}3$ on live AWS to raise power versus the $n{=}1$ smoke. Protocol file: `configs/live_key_cells_n3.yaml` (200 orders, 10 msg/s, VT 30/90 under `consumer_kill`; MRC 1/5 under `unhandled_error`; ESM `max_concurrency: 5`).

**Gate (must all hold):**

1. Free Tier / cost guard: planned live $n{=}3$ is $\approx 3\times$ the measured lite spend (**projection from** `results/live/key_cells/` sum $\approx \$0.00144$, not a new live measurement). Guard limit remains `MAX_ESTIMATED_USD=5`.
2. Shared-account `ConcurrentExecutions` **limit = 10**. Anji ESM default is **5**. Do not apply while another campaign already holds several unreserved slots.
3. Destroy after round (`terraform destroy`; evidence files kept).

**Probe (2026-09-20, eu-west-1):** `results/beyond_ca2/concurrency_probe_2026-09-20.json`

| Quantity | Measured |
|----------|----------|
| Account `ConcurrentExecutions` | **10** |
| `UnreservedConcurrentExecutions` | **10** (nothing reserved) |
| CloudWatch `ConcurrentExecutions` max (15 min) | **4** (steady) |
| `idem-eval-fn` invocations (15 min) | **1743** (Vikas r5 still consuming slots) |
| Anji `sqs-rr*` functions present | **none** (stack still destroyed) |
| Headroom if Anji ESM=5 applied | $10-4-5=1$ spare — **unsafe** |

**Decision:** **Live $n{=}3$ not executed.** Applying the SQS consumer mapping at `maximum_concurrency=5` beside Vikas workers=4 would leave one unreserved slot and would confound both campaigns via throttling. No live $n{=}3$ metrics exist; none are invented.

**When to run live (unchanged protocol):**

```bash
# 1) re-probe: UnreservedConcurrentExecutions and 15-min ConcurrentExecutions max
# 2) require observed_max + 5 <= 9 (leave ≥1 spare)
scripts/build_lambda_zips.sh
cd terraform && terraform apply -auto-approve   # name_prefix=sqs-rr, no personal IDs
DRY_RUN=0 PYTHONUNBUFFERED=1 python -m src.control.experiment_runner \
  --config configs/live_key_cells_n3.yaml --live \
  --out results/live/key_cells_n3
cd terraform && terraform destroy -auto-approve
```

## Beyond-CA2 executed this pass (localsim only)

| Extension | Why | Evidence | Status |
|-----------|-----|----------|--------|
| Confirmatory $n{=}3$ on the same 4 lite cells | Power vs $n{=}1$ smoke without occupying Lambda slots | `results/localsim/key_cells_n3/` (12 manifests); `results/beyond_ca2/localsim_key_cells_summary.json` | **Done (localsim)** |
| ESM poller sensitivity `max_concurrency` 2 vs 5 | Proxy for sharing `ConcurrentExecutions=10`; engine already uses `spec.max_concurrency` as poller count | `results/localsim/key_cells_concurrency/` (8 manifests) | **Done (localsim, $n{=}1$)** |
| Live confirmatory $n{=}3$ | Stronger than CA2 minimum live evidence | *Not run* — concurrency gate | **Blocked** |
| Burst / VT600 live cells | Broader than lite YAML | Not run | Optional |
| Adaptive VT / DIVE | Off in committed matrix | Out of matrix | Optional |

### Localsim $n{=}3$ (same 4 cells; not live)

| Campaign | Fault | VT | MRC | $n$ | loss mean | dup mean | DLQ mean | rec. mean (s) |
|----------|-------|---:|----:|----:|----------:|---------:|---------:|--------------:|
| L_vt_consumer_kill | consumer_kill | 30 | 5 | 3 | 0.0 | 0.0017 | 0.0 | 0.0 |
| L_vt_consumer_kill | consumer_kill | 90 | 5 | 3 | 0.0 | 0.0067 | 0.0 | 0.0 |
| L_mrc_unhandled_error | unhandled_error | 30 | 1 | 3 | 0.0 | 0.0 | 0.050 | 1.67 |
| L_mrc_unhandled_error | unhandled_error | 30 | 5 | 3 | 0.0 | 0.013 | 0.0 | 1.67 |

Honest limits of this lite localsim protocol: recovery on these 200-order / 10 msg/s cells is **not** the campaign-A 1:1 VT law (that remains the 350-cell confirmatory design). Lite $n{=}3$ supports **loss floor = 0** and **MRC 1 vs 5 DLQ direction** (MRC 1 mean DLQ $0.050$, range $0$–$0.09$; MRC 5 mean $0$). Live $n{=}1$ DLQ at MRC 1 was $0.16$ — same direction, different magnitude; not a paired fidelity test.

`run_id` values for repeat 0 match the live $n{=}1$ UUIDs because IDs are derived from config hash + seed, **not** backend. Do not mix `results/live/key_cells/` and `results/localsim/key_cells_n3/` in one analysis folder.

### Concurrency sensitivity ($n{=}1$, localsim)

Loss remained $0$ at `max_concurrency` 2 and 5 on all four cells. MRC 1 DLQ was $0.055$ at conc=2 vs $0.005$ at conc=5 (single repeats; descriptive only). This does **not** replace a live round under contention.

## COMPLETE decision

Floor evidenced. Beyond-CA2 live $n{=}3$ is still the preferred next AWS step when Vikas (or any other campaign) releases slots. Until then, localsim $n{=}3$ + poller sensitivity are the recorded exceedances.
