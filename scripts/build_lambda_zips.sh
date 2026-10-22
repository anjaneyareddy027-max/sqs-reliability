#!/usr/bin/env bash
# Build Lambda zips for terraform (handlers + common only; boto3 from runtime).
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p build
rm -f build/queue-consumer.zip build/sync-processor.zip
(
  cd src
  zip -qr ../build/queue-consumer.zip common queue_consumer -x '*/__pycache__/*' '*.pyc'
  zip -qr ../build/sync-processor.zip common sync_api -x '*/__pycache__/*' '*.pyc'
)
ls -la build/queue-consumer.zip build/sync-processor.zip
echo "ok: terraform packages ready under build/"
