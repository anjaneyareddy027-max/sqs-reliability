# Demo walkthrough (artefact demo, 5-10 min)

A command-by-command runbook for showing the artefact working. Every step has a
local (free) version; the live steps need a deployed stack in your own account
(see `CONFIGURATION_MANUAL.md` section 5).

## 0. Before recording

```bash
source .venv/bin/activate
make test            # green tests on screen is a nice opener
export STACK_NAME=sqs-rr-dev AWS_REGION=eu-west-1   # live only
```

## 1. Architecture (1 min)

Open `docs/ARCHITECTURE.md` and walk through the two arms and the message
lifecycle diagram: where the visibility timeout, maxReceiveCount and batch size
act, and where the faults are injected.

## 2. Deployed resources (1 min, live)

```bash
aws cloudformation describe-stacks --stack-name $STACK_NAME --query 'Stacks[0].Outputs' --output table
```

Show in the console: the orders queue with its redrive policy, the DLQ, the
two functions, the event source mapping (batch size, ReportBatchItemFailures),
the two tables and the `/sqs-rr/dev/fault` parameter.

## 3. Produce 100 orders and show the writes (1-2 min)

Local:

```bash
ORDER_COUNT=100 FAULT_MODE=none RUN_ID=demo-happy \
  python -m src.control.experiment_runner --from-env --out results/demo
cat results/demo/manifests/demo-happy.json | python -m json.tool | head -40
```

Live:

```bash
DRY_RUN=0 ORDER_COUNT=100 FAULT_MODE=none RUN_ID=demo-happy \
  python -m src.control.experiment_runner --from-env --live --out results/demo
aws dynamodb scan --table-name sqs-rr-dev-orders --select COUNT
```

## 4. Turn on `unhandled_error`, show retries and the DLQ (2 min)

Local: the same command with a fault.

```bash
ORDER_COUNT=600 FAULT_MODE=unhandled_error FAULT_RATE=0.5 MAX_RECEIVE_COUNT=1 RUN_ID=demo-mrc1 \
  python -m src.control.experiment_runner --from-env --out results/demo
```

Live: flip the switch by hand while orders are flowing.

```bash
python -m src.control.fault_controller --param /sqs-rr/dev/fault --mode unhandled_error --rate 1
aws sqs get-queue-attributes --queue-url <OrdersQueueUrl> --attribute-names All
aws sqs get-queue-attributes --queue-url <OrdersDlqUrl> --attribute-names ApproximateNumberOfMessages
python -m src.control.fault_controller --param /sqs-rr/dev/fault --off
```

Point out `ApproximateNumberOfMessagesNotVisible` climbing: the failed batches
are in flight, waiting for the visibility timeout.

## 5. Change maxReceiveCount and compare DLQ capture (1 min)

```bash
ORDER_COUNT=600 FAULT_MODE=unhandled_error FAULT_RATE=0.5 MAX_RECEIVE_COUNT=10 RUN_ID=demo-mrc10 \
  python -m src.control.experiment_runner --from-env --out results/demo
grep -h dlq_capture_rate results/demo/manifests/demo-mrc*.json
```

(live: `aws sqs set-queue-attributes` with a new `RedrivePolicy`, or let the
runner apply it - it does that at the start of every run.)

## 6. Recovery time plot from the last campaign (1 min)

Open `results/figures/recovery_vs_max_receive_count.png` and
`results/figures/recovery_vs_visibility.png`, then
`results/figures/timeline_visible_vs_backlog.png` for why recovery is measured
on the backlog and not on the visible count.

## 7. Reproducibility (30 s)

Show one manifest (git sha, config hash, seed, fault schedule), the
pre-registered `configs/analysis_plan.yaml`, and `docs/CONFIGURATION_MANUAL.md`.

## Clean up (live)

```bash
scripts/destroy.sh $STACK_NAME
```

Anjaneya Reddy Gurram
