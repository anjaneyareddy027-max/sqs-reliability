# SQS reliability / recovery research stack.
# Tags: project slug only — never personal name or personal ID.

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project    = "anji-thesis"
      managed_by = "terraform"
      purpose    = "research-eval"
      data       = "synthetic"
      stage      = var.stage
    }
  }
}

locals {
  prefix           = "${var.name_prefix}-${var.stage}"
  consumer_present = fileexists(var.consumer_package)
  sync_present     = fileexists(var.sync_package)
}

resource "aws_sqs_queue" "orders_dlq" {
  name                      = "${local.prefix}-orders-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "orders" {
  name                       = "${local.prefix}-orders"
  visibility_timeout_seconds = var.visibility_timeout
  receive_wait_time_seconds  = 20
  message_retention_seconds  = 345600
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.orders_dlq.arn
    maxReceiveCount     = var.max_receive_count
  })
}

resource "aws_dynamodb_table" "orders" {
  name         = "${local.prefix}-orders"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "order_id"

  attribute {
    name = "order_id"
    type = "S"
  }
}

resource "aws_dynamodb_table" "events" {
  name         = "${local.prefix}-events"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "run_id"
  range_key    = "event_id"

  attribute {
    name = "run_id"
    type = "S"
  }

  attribute {
    name = "event_id"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
}

resource "aws_ssm_parameter" "fault" {
  name = "/sqs-rr/${var.stage}/fault"
  type = "String"
  value = jsonencode({
    mode         = "none"
    rate         = 0.0
    window_start = null
    window_end   = null
    point        = "auto"
  })
}

data "aws_iam_policy_document" "assume_lambda" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "consumer" {
  name               = "${local.prefix}-queue-consumer"
  assume_role_policy = data.aws_iam_policy_document.assume_lambda.json
}

resource "aws_iam_role" "sync" {
  name               = "${local.prefix}-sync-processor"
  assume_role_policy = data.aws_iam_policy_document.assume_lambda.json
}

data "aws_iam_policy_document" "consumer" {
  statement {
    sid       = "OrdersRw"
    actions   = ["dynamodb:PutItem", "dynamodb:GetItem"]
    resources = [aws_dynamodb_table.orders.arn]
  }

  statement {
    sid       = "EventsWrite"
    actions   = ["dynamodb:PutItem"]
    resources = [aws_dynamodb_table.events.arn]
  }

  statement {
    sid       = "Sqs"
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes", "sqs:ChangeMessageVisibility"]
    resources = [aws_sqs_queue.orders.arn]
  }

  statement {
    sid       = "FaultParam"
    actions   = ["ssm:GetParameter"]
    resources = [aws_ssm_parameter.fault.arn]
  }

  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.consumer.arn}:*"]
  }
}

data "aws_iam_policy_document" "sync" {
  statement {
    sid       = "OrdersRw"
    actions   = ["dynamodb:PutItem", "dynamodb:GetItem"]
    resources = [aws_dynamodb_table.orders.arn]
  }

  statement {
    sid       = "EventsWrite"
    actions   = ["dynamodb:PutItem"]
    resources = [aws_dynamodb_table.events.arn]
  }

  statement {
    sid       = "FaultParam"
    actions   = ["ssm:GetParameter"]
    resources = [aws_ssm_parameter.fault.arn]
  }

  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.sync.arn}:*"]
  }
}

resource "aws_iam_role_policy" "consumer" {
  name   = "consumer"
  role   = aws_iam_role.consumer.id
  policy = data.aws_iam_policy_document.consumer.json
}

resource "aws_iam_role_policy" "sync" {
  name   = "sync"
  role   = aws_iam_role.sync.id
  policy = data.aws_iam_policy_document.sync.json
}

resource "aws_cloudwatch_log_group" "consumer" {
  name              = "/aws/lambda/${local.prefix}-queue-consumer"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "sync" {
  name              = "/aws/lambda/${local.prefix}-sync-processor"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "consumer" {
  count = local.consumer_present ? 1 : 0

  function_name    = "${local.prefix}-queue-consumer"
  role             = aws_iam_role.consumer.arn
  runtime          = "python3.12"
  architectures    = ["x86_64"]
  handler          = "queue_consumer.handler.lambda_handler"
  filename         = var.consumer_package
  source_code_hash = filebase64sha256(var.consumer_package)
  memory_size      = var.memory_mb
  timeout          = var.consumer_timeout

  environment {
    variables = {
      ORDERS_TABLE     = aws_dynamodb_table.orders.name
      EVENTS_TABLE     = aws_dynamodb_table.events.name
      FAULT_PARAM_NAME = aws_ssm_parameter.fault.name
      FAULT_HARD_KILL  = "1"
      IDEMPOTENCY      = "on"
      STAGE            = var.stage
      ADAPTIVE_VT      = "0"
      QUEUE_URL        = aws_sqs_queue.orders.url
    }
  }

  depends_on = [aws_cloudwatch_log_group.consumer, aws_iam_role_policy.consumer]
}

resource "aws_lambda_event_source_mapping" "orders" {
  count = local.consumer_present ? 1 : 0

  event_source_arn                   = aws_sqs_queue.orders.arn
  function_name                      = aws_lambda_function.consumer[0].arn
  batch_size                         = var.batch_size
  maximum_batching_window_in_seconds = var.batching_window_seconds
  function_response_types            = ["ReportBatchItemFailures"]

  scaling_config {
    maximum_concurrency = var.max_concurrency
  }
}

resource "aws_lambda_function" "sync" {
  count = local.sync_present ? 1 : 0

  function_name    = "${local.prefix}-sync-processor"
  role             = aws_iam_role.sync.arn
  runtime          = "python3.12"
  architectures    = ["x86_64"]
  handler          = "sync_api.app.lambda_handler"
  filename         = var.sync_package
  source_code_hash = filebase64sha256(var.sync_package)
  memory_size      = var.memory_mb
  timeout          = 10

  environment {
    variables = {
      ORDERS_TABLE     = aws_dynamodb_table.orders.name
      EVENTS_TABLE     = aws_dynamodb_table.events.name
      FAULT_PARAM_NAME = aws_ssm_parameter.fault.name
      FAULT_HARD_KILL  = "1"
      IDEMPOTENCY      = "on"
      STAGE            = var.stage
    }
  }

  depends_on = [aws_cloudwatch_log_group.sync, aws_iam_role_policy.sync]
}

resource "aws_apigatewayv2_api" "http" {
  count         = local.sync_present ? 1 : 0
  name          = "${local.prefix}-http"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "sync" {
  count                  = local.sync_present ? 1 : 0
  api_id                 = aws_apigatewayv2_api.http[0].id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.sync[0].invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "orders" {
  count     = local.sync_present ? 1 : 0
  api_id    = aws_apigatewayv2_api.http[0].id
  route_key = "POST /orders"
  target    = "integrations/${aws_apigatewayv2_integration.sync[0].id}"
}

resource "aws_apigatewayv2_stage" "default" {
  count       = local.sync_present ? 1 : 0
  api_id      = aws_apigatewayv2_api.http[0].id
  name        = "$default"
  auto_deploy = true
}

resource "aws_lambda_permission" "apigw" {
  count         = local.sync_present ? 1 : 0
  statement_id  = "AllowHttpApi"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.sync[0].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http[0].execution_arn}/*/*"
}
