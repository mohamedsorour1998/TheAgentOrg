variable "name" {
  description = "Resource name prefix (e.g. theagentorg-shared)"
  type        = string
}

variable "handler_source_dir" {
  description = "Directory holding the Lambda's handler.py (zipped at plan time)"
  type        = string
}

variable "python_runtime" {
  description = "Lambda Python runtime. The runtime provides boto3, which is why the handler does not vendor it"
  type        = string
  default     = "python3.12"
}

variable "reserved_concurrency" {
  description = <<-EOT
    Reserved concurrent executions for the ingress function.

    REQUIRED, not tuning. The Function URL is public and unauthenticated at the
    AWS layer (see main.tf), so anyone can drive invocations. Without a reserve
    a flood scales the function out to the account limit and bills for every
    one. This caps the blast radius: past this many concurrent requests the URL
    answers 429 and costs nothing more. Small on purpose -- real traffic is a
    handful of issue events, never a burst.
  EOT
  type        = number
  default     = 2

  validation {
    # 0 disables the function entirely and -1 removes the cap. Both are
    # legal Lambda values and both defeat the point of this variable, so
    # neither can be set here by accident.
    condition     = var.reserved_concurrency >= 1
    error_message = "reserved_concurrency must be >= 1; 0 disables the function and -1 removes the spend cap this variable exists to impose."
  }
}

variable "log_retention_days" {
  description = "CloudWatch retention for the function's log group. Never 0 (never-expire), which is how a public endpoint's logs become an unbounded bill"
  type        = number
  default     = 14

  validation {
    condition     = var.log_retention_days > 0
    error_message = "log_retention_days must be > 0; 0 means never expire, and this function's logs are driven by public traffic."
  }
}

variable "event_source" {
  description = "EventBridge `Source` on every published event. The rule matches on it, so changing it here without changing the rule matches nothing"
  type        = string
  default     = "github.webhook"
}

variable "tags" {
  description = "Tags applied to every resource"
  type        = map(string)
  default     = {}
}
