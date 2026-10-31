# Viva Q&A Seeds

**Name:** Anjaneya Reddy Gurram
**Student ID:** 24288853
**Module:** Research Project

This document provides sample answers and defenses for the examiner seeds proposed in the project master prompt.

### 1. Why Standard not FIFO?
Standard SQS yields higher, nearly unlimited throughput, and its semantics ("at-least-once" delivery, best-effort ordering) explicitly introduce duplicates and out-of-order execution. Studying reliability under standard Standard rules highlights the necessity for idempotent system designs, which is a core architectural requirement for modern resilient microservices. FIFO suppresses transport duplicates by design but imposes harsh throughput limits and limits the exploration of concurrent processing recovery logic.

### 2. How do you define message loss on an at-least-once queue?
In an at-least-once queue, message loss theoretically shouldn't occur unless a message is deleted prematurely or expires from retention. I operationally define message loss as: `Produced Count − (Unique successfully processed records in DynamoDB + Unique records captured in DLQ)`. If a message never lands safely in the database and is absent from the DLQ after full stack drainage, it was lost in the topology (e.g., consumer failed after deletion call but before DB commit).

### 3. Why is reliability not implied by throughput?
The optimal configurations proposed by baseline studies like Kyrychenko et al. (2025) are centered entirely around a healthy system processing messages as fast as possible. throughput does not account for handling partial execution, retry storms, or datastore backpressure. High throughput configurations (e.g., massive batches, short visibility timeouts) can actually destroy reliability during an outage by initiating rapid infinite loops of duplicates and causing resource contention, rather than pacing the system.

### 4. How did you control for provider performance drift?
Serverless architectures are notorious for temporal performance drift based on AWS's backend loads (Eismann et al., 2022). To mitigate this, runs were grouped densely on the same days, order randomized (e.g., alternating between low/high visibility configurations), and experiments were repeated multiple times to generate effect sizes and confidence intervals rather than absolute comparisons. 

### 5. Ethics: why is own-account load acceptable?
The target environments are entirely localized to my personal AWS account. Generating load here is acceptable because: 
1. The synthetic data utilized contains no PII or real individuals' information.
2. The request frequency and concurrency were kept deliberately low enough (under 1000 orders/pilot scale) so as to comply with the AWS Acceptable Use Policy, guaranteeing no cross-tenant "noisy-neighbor" issues.

### 6. Would results transfer to Kafka/RabbitMQ?
Conceptually, the trade-offs exist everywhere, but technically, the implementation metrics wouldn't directly translate. Kafka uses offset tracking and consumer groups rather than an explicit "Visibility Timeout". RabbitMQ handles explicit acks/nacks effectively like SQS, but is not a managed stateless service. Therefore, while the architecture principles hold, the parameter names, DLQ routing physics, and scaling equations are distinctly AWS SQS specific.

### 7. What is the practical recommendation for visibility timeout under failure?
Statically tying visibility timeout closely to normal execution time minimizes duplicate exposure when failures resolve quickly, but risks long in-flight delay if processing stalls. Committed localsim evidence shows recovery time tracks VT nearly 1:1 under consumer kill (e.g., VT 600 → mean recovery 600 s). H3_recovery fails to reject after Holm (p_adj=0.084) on packaging-deduped n=5/cell; the large rank-biserial effect (r=0.667) is descriptive. Practical guidance: choose VT from the recovery SLA you can tolerate, not from steady-state throughput optima alone. **DIVE / adaptive_vt was not enabled in the experiment matrix** (`adaptive_vt: false` in manifests); do not present adaptive visibility as an evaluated result. Code for optional `ChangeMessageVisibility` exists but is future work pending a dedicated campaign.

### 8. How is recovery time operationalised?
Recovery time is defined as the elapsed time from the exact moment the active fault injection is lifted (fault window expires) to the point where the `ApproximateNumberOfMessagesVisible` on the queue returns to its pre-fault steady-state baseline band, and backlog clearing processing normalizes to steady-state throughput.

### 9. Why SAM not Terraform?
AWS SAM (Serverless Application Model) is heavily optimized specifically for AWS serverless assets (Lambda, SQS, API Gateway, DynamoDB). It directly abstracts complex Event Source Mapping permissions and IAM bindings into simple, unified YAML templates compared to the verbose, lower-level provider syntax required by Terraform. SAM allows for faster execution and local debugging (`sam local invoke`) utilizing the same template file.

### 10. What would you change with a larger budget?
With a larger, unlimited budget, I would:
1. Align item volumes directly with the Kyrychenko baseline (500,000 baseline items per run instead of a smaller representative subset).
2. Execute a full factorial ANOVA covering all IV configurations aggressively instead of utilizing orthogonal matrices/key cell subsets to guard costs.
3. Test a multi-region Active-Active pipeline to see if architectural failover changes the necessary queue buffering configurations.

Anjaneya Reddy Gurram
