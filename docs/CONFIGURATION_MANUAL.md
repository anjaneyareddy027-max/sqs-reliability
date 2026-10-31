# Configuration Manual

**Name:** Anjaneya Reddy Gurram
**Student ID:** 24288853
**Module:** Research Project

This manual provides the technical specifications, setup instructions, and operational guidelines to reproduce the experimental results for the research project "Reliability and Recovery of Amazon SQS Messaging under Injected Consumer and Downstream Failures".

## Prerequisites
- **AWS CLI:** Version 2.x installed and configured (`aws configure`).
- **AWS SAM CLI:** Serverless Application Model CLI installed for building and deploying IaC.
- **Python:** Version 3.11+ installed.
- **Git:** Version control.

## Account Setup
- Must be executed in your **own AWS account** (authorized target).
- Prefer deploying to a consistent region (e.g., `eu-west-1`) to minimize cross-region variability.

## IAM Notes
- The SAM deployment uses AWS managed policies and least privilege custom policies where possible.
- Avoid deploying with a wildcard `*` role in production. The provided `template.yaml` defines explicit permissions for SQS queues, Lambda functions, and DynamoDB tables.

## Billing Alarm
- **Critical:** Setup AWS Billing Alarms using CloudWatch before running any experiments. 
- You can create an alarm in the AWS Billing Console that triggers an SNS email notification if estimated charges exceed a set threshold (e.g., $10.00).

## Environment Variables (`.env`)
Copy the `.env.example` file to `.env` and configure it:
```dotenv
AWS_REGION=eu-west-1          # Consistency for deployments
STAGE=exp                     # Deployment stage: dev, pilot, or exp
DRY_RUN=0                     # Set to 1 for local dry validation without AWS costs
FAULT_MODE=none               # Fault mode: none, consumer_kill, unhandled_error, datastore_reject, datastore_timeout
FAULT_RATE=1.0                # Injection probability for the fault
FAULT_WINDOW_SEC=60           # Duration of the fault injection
VISIBILITY_TIMEOUT=30         # SQS Visibility Timeout (seconds)
MAX_RECEIVE_COUNT=5           # Retry threshold for SQS to DLQ
BATCH_SIZE=10                 # Messages batched per Lambda invocation
ORDER_COUNT=1000              # Total messages to generate
LOAD_PROFILE=normal           # normal | burst
RUN_ID=test-run               # Tracking identifier
ENABLE_COST_GUARD=1           # Set to 1 to abort if projected cost exceeds limit
MAX_ESTIMATED_USD=5.00        # Maximum cost tolerance per run
```

## Deploy and Destroy
**Deploy:**
1. Execute cost guards and estimations:
   ```bash
   python scripts/assert_free_tier_guard.py
   python scripts/estimate_cost.py --config configs/pilot.yaml
   ```
2. Build and Deploy:
   ```bash
   sam build
   sam deploy --guided
   ```
   (Use guided once to generate `samconfig.toml`, then `sam deploy` subsequently).

**Destroy:**
Once experiments finish, destroy the stack to avoid idle and trailing charges:
```bash
sam delete --stack-name <stack-name> --no-prompts
```

## How to Run Campaigns
Ensure the virtual environment is sourced and requirements are installed. All runners expect the CLI commands exactly as stated in the project scripts.

- **Pilot:** Validates the system end-to-end at a very low volume.
  ```bash
  DRY_RUN=1 python -m src.control.experiment_runner --config configs/pilot.yaml
  ```
- **Baseline (No Fault):** Mirrors the baseline steady-state from Kyrychenko et al. (2025).
  ```bash
  python -m src.control.experiment_runner \
    --config configs/baseline_kyrchenko.yaml \
    --fault none --randomise-order --repeats 5 --out results/baseline/
  ```
- **Fault Campaigns:** Evaluates system behavior under different induced errors.
  ```bash
  python scripts/run_fault_campaign.sh --fault consumer_kill --vary visibility_timeout
  python scripts/run_fault_campaign.sh --fault unhandled_error --vary max_receive_count
  ```

## How to Interpret CSVs
Generated data is saved under `results/`.
- **manifests/**: JSON context holding the configuration, timestamp, random seed, and Git commit hash used for the run.
- **summary.csv**: Contains aggregated trial data.
  - `Total Produced`: Number of generated events.
  - `Process Rate`: Successes divided by produced (throughput).
  - `Message Loss Rate`: Percentage of messages not successfully written to DB and not arriving in the DLQ.
  - `Duplicate Rate`: Excess successful writes over unique messages, relative to the no-fault baseline floor.
  - `Recovery Time`: Time from fault cessation to queue depth normalization.

## Troubleshooting
- **Duplicates soaring:** Check if `VISIBILITY_TIMEOUT` is too low compared to Lambda processing execution time. Faster timeouts lead to in-flight messages becoming visible before deletion.
- **Recovery time is slow/stalled:** Check if `VISIBILITY_TIMEOUT` is too high. If a fault disrupts processing, messages stay invisible for too long before getting retried, slowing backlog drain.
- **Zero baseline throughput:** Confirm SAM permissions and DynamoDB provisioned capacity limits or if the event source mapping is disabled.

## Cost Controls
- Always have `ENABLE_COST_GUARD=1` in your scripts.
- Use `DRY_RUN=1` to test the harness logically before dispatching.
- Delete the stack (`sam delete`) between days of work.
- Validate `report_batch_item_failures` is correctly minimizing redundant batch retries (avoiding dual charging on unchanged messages).

Anjaneya Reddy Gurram
