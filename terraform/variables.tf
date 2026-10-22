variable "region" {
  type    = string
  default = "eu-west-1"
}

variable "name_prefix" {
  description = "Resource name prefix (project slug only; never a personal name or ID)."
  type        = string
  default     = "sqs-rr"
}

variable "stage" {
  type    = string
  default = "dev"
}

variable "visibility_timeout" {
  type    = number
  default = 30
}

variable "max_receive_count" {
  type    = number
  default = 5
}

variable "batch_size" {
  type    = number
  default = 10
}

variable "batching_window_seconds" {
  type    = number
  default = 1
}

variable "consumer_timeout" {
  type    = number
  default = 15
}

variable "max_concurrency" {
  type    = number
  default = 5
}

variable "log_retention_days" {
  type    = number
  default = 7
}

variable "consumer_package" {
  description = "Zip for the SQS consumer Lambda (built separately)."
  type        = string
  default     = "../build/queue-consumer.zip"
}

variable "sync_package" {
  description = "Zip for the sync HTTP API Lambda (built separately)."
  type        = string
  default     = "../build/sync-processor.zip"
}

variable "memory_mb" {
  type    = number
  default = 256
}
