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

# ── the rule's target: dispatching run-pipeline.yml ───────────────────────────

variable "dispatch_repo" {
  description = <<-EOT
    The repository OWNING run-pipeline.yml, as "owner/name". This is THIS
    repository, not the target repo the pipeline writes to -- the workflow lives
    here and only here, because nothing may be committed to the target repo.
  EOT
  type        = string
  default     = "mohamedsorour1998/TheAgentOrg"
}

variable "dispatch_workflow_file" {
  description = "Workflow filename to dispatch. GitHub accepts the file name in place of a numeric id."
  type        = string
  default     = "run-pipeline.yml"
}

variable "dispatch_ref" {
  description = "Git ref the dispatched run checks out. The workflow must exist on this ref or the API answers 404."
  type        = string
  default     = "main"
}

variable "dispatch_token_secret_name" {
  description = <<-EOT
    Secrets Manager secret NAME holding the GitHub token EventBridge dispatches
    with. Empty (the default) means the target wiring is NOT created.

    THE DEFAULT IS EMPTY ON PURPOSE, and it is the reason this whole feature is
    opt-in rather than always-on. An `aws_cloudwatch_event_connection` with
    API_KEY auth requires the token's VALUE as a configuration value, so
    Terraform must be able to READ it at plan time. Before a human has written
    that secret's value there is nothing to read, and an ungated
    `data.aws_secretsmanager_secret_version` FAILS THE PLAN -- which would turn
    terraform.yml, currently green end to end, red on every run until somebody
    minted a token.

    So: set this only after `aws secretsmanager put-secret-value` has written the
    token. Until then every other resource in this module applies exactly as it
    did before, and the rule simply has no target -- which outputs.tf says out
    loud rather than leaving to be discovered.

    THE TOKEN LANDS IN TERRAFORM STATE. That is not avoidable with an API_KEY
    connection: the provider takes the value through config. State is in S3 and
    readable by anyone with state access, so scope the token to
    `actions: write` on this ONE repository and nothing else, and rotate it after
    the demo. Never put the literal in a .tf file, and never read it from .env.
  EOT
  type        = string
  default     = ""
}

variable "dispatch_token_secret_json_key" {
  description = <<-EOT
    Key to read from the token secret when it holds JSON. Empty means the secret
    is a bare string.

    Both shapes exist for the same reason handler.py accepts both for the webhook
    secret: `put-secret-value --secret-string ghp_xxx` gives a bare string, while
    the console's key/value editor gives `{"token": "ghp_xxx"}`. Getting this
    wrong sends the JSON envelope as the bearer token and every dispatch 401s.
  EOT
  type        = string
  default     = ""
}
