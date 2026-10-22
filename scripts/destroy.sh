#!/usr/bin/env bash
# Tear the stack down after a campaign and check that it is really gone.
#   scripts/destroy.sh                 (STACK_NAME or sqs-rr-dev)
#   scripts/destroy.sh sqs-rr-pilot
set -euo pipefail
cd "$(dirname "$0")/.."

STACK=${1:-${STACK_NAME:-sqs-rr-dev}}
REGION=${AWS_REGION:-eu-west-1}

sam delete --stack-name "$STACK" --region "$REGION" --no-prompts

if aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" >/dev/null 2>&1; then
  echo "stack $STACK still exists - check the CloudFormation console"
  exit 1
fi
mkdir -p results
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) teardown verified: $STACK ($REGION) is gone" | tee -a results/teardown_log.txt
