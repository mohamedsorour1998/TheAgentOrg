# WHY THIS FILE EXISTS WHEN THE agentcore MODULE HAS NO EQUIVALENT.
#
# `.terraform.lock.hcl` is gitignored (.gitignore:20), so CI re-resolves provider
# versions on every `terraform init` rather than replaying a committed lock. The
# repo's answer to that is an explicit constraint per provider -- the shared
# environment pins `hashicorp/aws ~> 6.28` in providers.tf.
#
# This module is the first to need a SECOND provider: `archive`, to zip the
# handler at plan time. Left undeclared, CI would silently take whatever
# `hashicorp/archive` is newest on the day it runs, which is the one thing the
# aws pin exists to prevent. Declaring it here rather than in the environment's
# providers.tf keeps it beside the resource that needs it, and Terraform
# aggregates module constraints when selecting versions, so the pin is real.
#
# `aws` is deliberately NOT re-declared here, so this module adds no constraint
# of its own to bump. Note the EFFECTIVE constraint is not a single declaration:
# the lock file records `">= 5.93.0, ~> 6.28"`, where `~> 6.28` comes from
# environments/shared/providers.tf and the `>= 5.93.0` floor from the vendored
# terraform-aws-modules/ecr submodule the agentcore module uses. Adding a third
# constraint here would be one more thing to keep in step for no gain.
#
# Verified at 2.8.0 (the version `terraform init` selected under this
# constraint); resolution and `terraform validate` pasted in the task report.

terraform {
  required_providers {
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.8"
    }
  }
}
