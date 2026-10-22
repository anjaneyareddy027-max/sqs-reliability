output "orders_queue_url" { value = aws_sqs_queue.orders.url }
output "orders_queue_arn" { value = aws_sqs_queue.orders.arn }
output "orders_dlq_url" { value = aws_sqs_queue.orders_dlq.url }
output "orders_table_name" { value = aws_dynamodb_table.orders.name }
output "events_table_name" { value = aws_dynamodb_table.events.name }
output "fault_param_name" { value = aws_ssm_parameter.fault.name }
output "consumer_function_name" {
  value = try(aws_lambda_function.consumer[0].function_name, null)
}
output "sync_function_name" {
  value = try(aws_lambda_function.sync[0].function_name, null)
}
output "sync_api_url" {
  value = try("${aws_apigatewayv2_api.http[0].api_endpoint}/orders", null)
}
output "consumer_mapping_id" {
  value = try(aws_lambda_event_source_mapping.orders[0].uuid, null)
}
