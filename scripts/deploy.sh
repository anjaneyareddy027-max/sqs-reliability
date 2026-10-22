#!/usr/bin/env bash
# Build + deploy the SAM stack into YOUR OWN AWS account (this costs a little).
#   scripts/deploy.sh            -> samconfig [default]  (stack sqs-rr-dev)
#   scripts/deploy.sh pilot      -> samconfig [pilot]    (stack sqs-rr-pilot)
#   scripts/deploy.sh exp        -> samconfig [exp]      (stack sqs-rr-exp)
# Remember scripts/destroy.sh afterwards.
set -euo pipefail
cd "$(dirname "$0")/.."

CONFIG_ENV=${1:-default}
case "$CONFIG_ENV" in
  default) STACK=sqs-rr-dev ;;
  pilot)   STACK=sqs-rr-pilot ;;
  exp)     STACK=sqs-rr-exp ;;
  *) echo "unknown config env $CONFIG_ENV"; exit 2 ;;
esac
REGION=${AWS_REGION:-eu-west-1}
PY=.venv/bin/python

command -v sam >/dev/null || { echo "sam cli not found - pip install aws-sam-cli"; exit 1; }
command -v aws >/dev/null || { echo "aws cli not found"; exit 1; }

echo "== account check"
aws sts get-caller-identity --query Account --output text

echo "== cost guard"
$PY scripts/assert_free_tier_guard.py --config configs/pilot.yaml
$PY scripts/estimate_cost.py --config configs/pilot.yaml

echo "== validate + build"
sam validate --lint
sam build

echo "== deploy ($STACK in $REGION)"
sam deploy --config-env "$CONFIG_ENV"

aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
  --query 'Stacks[0].Outputs' --output table
echo "export STACK_NAME=$STACK   # for the runner"
