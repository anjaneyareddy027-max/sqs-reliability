# Demo and Presentation Outline

**Name:** Anjaneya Reddy Gurram
**Student ID:** 24288853
**Module:** Research Project

## Part 1: Presentation Outline (10 min)

1. **Problem Statement & Motivation (1.5 min):** 
   - Introduce modern asynchronous, event-driven architectures utilizing SQS to decouple workloads.
   - Highlight the gap: While steady-state configurations are thoroughly researched (Kyrychenko et al., 2025), failure state recoveries and reliability tuning are often unverified in practice.
2. **Gap / Contribution vs Kyrychenko Baseline (2 min):**
   - Summarize the Kyrychenko baseline which optimizes purely for throughput with healthy endpoints.
   - Note that my contribution tests these "optimal" throughput configurations dynamically when injected with consumer/datastore failures.
3. **Methodology & Experimental Design (2 min):**
   - Explain the controlled two-arm experiment comparing standard execution to controlled failure injection.
   - Outline the primary IVs: Visibility Timeout and `maxReceiveCount` (redrive).
   - Outline DVs measured: duplicate rates, message loss, DLQ capture, recovery time.
4. **Key Results (3 min):**
   - **Figure 1:** Duplicate Rate vs Visibility Timeout over a failure window (localsim).
   - **Figure 2:** Recovery Time vs Visibility Timeout / MRC (trade-off; recovery tracks VT).
   - **Figure 3:** Guidance-transfer / H3 scatter (steady-state-favourable long VT lengthens mean recovery; H3_recovery fails to reject after Holm on deduped n=5). **Do not claim DIVE results** — `adaptive_vt` was false in all committed runs.
5. **Implications for Practitioners (1 min):**
   - Advice on balancing static VT against recovery SLA using committed H1–H3/exploratory evidence.
   - Adaptive visibility (optional code path) is future work, not an evaluated contribution.
6. **Limitations & Q&A Tease (0.5 min):**
   - Cloud provider drifts.
   - Scape limitation restricted to Standard queues rather than FIFO.
   - Call to action for Q&A.

---

## Part 2: Demo Script Outline (5-10 min)

1. **Show Architecture Diagram (1 min):**
   - Display the flow: Producer → API Gateway/SQS → AWS Lambda (Consumer) → DynamoDB.
   - Contrast the Synced Arm vs the Queue-Decoupled Arm + DLQ.
2. **Deployed Resources in Console (1 min):**
   - Prefer showing the **local simulator / SAM template** and committed manifests. Live AWS console deploy was **not** executed for this submission; do not imply production CloudWatch evidence.
3. **Produce 100 Orders & Happy Path (1.5 min):**
   - Issue command to produce 100 synthetic orders.
   - Open DynamoDB, demonstrating successful idempotent writes.
4. **Enable `FAULT_MODE=unhandled_error` (2 min):**
   - Trigger the fault injection parameters in the CLI runner.
   - Observe the Lambda CloudWatch logs displaying exceptions and watch SQS push affected messages back onto the queue for retry.
   - Demonstrate DLQ capture via the AWS SQS console displaying items shifting from primary to DLQ.
5. **Varying Configuration (`maxReceiveCount`) (1.5 min):**
   - Change `maxReceiveCount` parameter from 5 to 1.
   - Rerun experiment and plot the immediate contrast in how fast the DLQ absorbs failing tasks.
6. **Show Recovery Plot (1 min):**
   - Output the visual Python plotting from the most recent campaign (localsim manifests).
   - State clearly that **DIVE/adaptive_vt was not run** in the committed matrix; optional handler path exists for future campaigns only.
7. **Traceability and Reproducibility (1 min):**
   - Point out the `results/manifests/` storing exact configurations and git hashes.
   - Mention the Configuration Manual detailing all deployment logic.

Anjaneya Reddy Gurram
