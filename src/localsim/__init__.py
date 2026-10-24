"""Local dry-run simulator (DRY_RUN=1).

Docker/LocalStack are not needed. The simulator runs on a virtual clock and
models the parts of SQS + Lambda + DynamoDB that matter for this study:
visibility timeout, receive count, redrive to a DLQ, at-least-once duplicates,
event source mapping pollers with batch size / batching window, partial batch
responses, Lambda timeouts and conditional puts.

The real handler code (``queue_consumer.handler`` / ``sync_api.app``) is what
gets called, only the AWS services around it are fake.

Timing numbers (cold start, per record cost, write latency ...) are modelling
assumptions that live in the ``sim:`` block of each config. They are NOT
measurements of AWS and anything produced in this mode is labelled
``backend: localsim`` in its manifest.
"""
