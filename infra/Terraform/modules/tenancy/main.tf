################################################################################
# The tenancy table: one table, one partition PER TENANT.
#
# `docs/design/dynamodb-migration.md` is the long form. This module builds step 1
# of it and nothing else -- the table and the two IAM policies. No application
# code reads it yet, and the Postgres path stays authoritative until step 10.
#
# THE PARTITION KEY IS THE TENANT, AND THAT IS NOT AN IDIOM CHOICE. It is the
# only shape `dynamodb:LeadingKeys` can constrain: that condition compares
# against the partition key of the request, so a table keyed on anything else
# cannot be defended by IAM at all and would push isolation back into application
# code -- which is what `agentorg/tenancy/` exists to avoid.
#
#   PK                    SK                    replaces
#   ─────────────────────────────────────────────────────────────
#   TENANT#<tenant_id>    ORG                   organisation
#   TENANT#<tenant_id>    MEMBER#<user_id>      membership
#   TENANT#<tenant_id>    REPO#<full_name>      repository
#   TENANT#<tenant_id>    RUN#<run_id>          run
#   TENANT#<tenant_id>    SECRET#<name>         secret
#   TENANT#<tenant_id>    BUDGET                budget
#   TENANT#<tenant_id>    JOB#<job_id>          queue_jobs
#   USER#<user_id>        PROFILE               app_user  -- see below
#
# THREE `unique_together` CONSTRAINTS BECOME THE PRIMARY KEY and stop being
# separately declarable: repository(tenant_id, full_name), secret(tenant_id,
# name) and membership(tenant_id, user_id) are exactly (PK, SK). A uniqueness
# rule that cannot be forgotten is worth more than one enforced by an index
# somebody has to remember to write.
#
# `app_user` IS DELIBERATELY OUTSIDE TENANT SCOPE and keeps that property here.
# `schema.py` states the reason in the table's own `unscoped_reason`: one person
# may hold memberships in several organisations, so no single tenant owns the
# row. It is unreachable from tenant scope because `membership` is the only route
# in and that table IS scoped. `LeadingKeys` therefore does not defend
# `USER#<id>` -- nothing about that changed; it was never defended by RLS either.
################################################################################

resource "aws_dynamodb_table" "tenancy" {
  name = var.table_name

  # PAY_PER_REQUEST for `modules/state`'s reason: a handful of writes per run and
  # nothing between demos, so a provisioned floor bills for capacity nobody uses.
  # It is also the entire cost argument for this migration -- a provisioned table
  # would reintroduce the standing charge that RDS was rejected for.
  billing_mode = "PAY_PER_REQUEST"

  hash_key  = "pk"
  range_key = "sk"

  # GENERIC KEY NAMES, deliberately. In a single-table design the partition holds
  # seven different row types, so a key named `tenant_id` would be a lie on the
  # `USER#<id>` partition and a key named `run_id` a lie on six of the seven.
  # The TYPE lives in the sort-key prefix, where a reader can see it.
  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }

  attribute {
    name = "gsi1pk"
    type = "S"
  }

  attribute {
    name = "gsi2pk"
    type = "S"
  }

  attribute {
    name = "gsi2sk"
    type = "S"
  }

  attribute {
    name = "gsi3pk"
    type = "S"
  }

  ##############################################################################
  # GSI1 -- find a run without knowing its tenant. `gates.load(run_id)` and
  # `jobs_for_run` both have a run id and no tenant, which is exactly the lookup
  # the base table cannot answer.
  #
  # **KEYS_ONLY, AND THAT IS A SECURITY DECISION RATHER THAN A COST ONE.** This
  # is the one index reachable WITHOUT a tenant in the key, so it is the one
  # place a cross-tenant read could originate. With `ALL` a single Query returns
  # another tenant's whole item; with KEYS_ONLY it returns only `pk`/`sk`, so the
  # caller learns that a run exists and must then `GetItem` on the BASE table --
  # where `LeadingKeys` applies and refuses. The index cannot leak contents even
  # if an accessor forgets to compare the tenant.
  #
  # That extra round trip is the price, and it is the right price: the leak this
  # index could produce is the one failure this whole subsystem exists to
  # prevent. `docs/design/dynamodb-migration.md` §6 requires a leak test aimed
  # here specifically.
  #
  # SPARSE BY CONSTRUCTION: only items carrying `gsi1pk` appear, which is runs
  # and jobs. Nothing else pays for it.
  ##############################################################################
  # `key_schema` RATHER THAN `hash_key`, and only on the indexes. The provider
  # deprecates the GSI-level `hash_key`/`range_key` arguments in favour of this
  # block; the TABLE-level ones are not deprecated, which is why
  # `modules/state` -- same provider version, no GSIs -- validates clean while an
  # earlier draft of this file produced exactly four warnings, one per GSI key.
  global_secondary_index {
    name            = "gsi1"
    projection_type = "KEYS_ONLY"

    key_schema {
      attribute_name = "gsi1pk"
      key_type       = "HASH"
    }
  }

  ##############################################################################
  # GSI2 -- the queue's "next READY job", partitioned by status and sorted by
  # creation time. This is the query `queue.claim` issues, and the reason a
  # status-keyed index exists rather than a scan with a filter: a filter reads
  # (and bills for) every item before discarding it.
  #
  # KEYS_ONLY for GSI1's reason plus one of its own: the claim is a CONDITIONAL
  # UPDATE on the base item, so the worker must go back to the base table
  # regardless. Projecting the payload here would copy every job's ticket text
  # into a second place for no read that needs it.
  ##############################################################################
  global_secondary_index {
    name            = "gsi2"
    projection_type = "KEYS_ONLY"

    # ORDER IS THE MEANING HERE, not the attribute names: the provider builds the
    # key schema from this list, so HASH must precede RANGE. Swapping them
    # produces a table DynamoDB will accept and a queue whose "next READY job"
    # query returns nothing.
    key_schema {
      attribute_name = "gsi2pk"
      key_type       = "HASH"
    }

    key_schema {
      attribute_name = "gsi2sk"
      key_type       = "RANGE"
    }
  }

  ##############################################################################
  # GSI3 -- `app_user` by email, which `schema.py` declares UNIQUE. DynamoDB has
  # no unique constraint on a non-key attribute, so uniqueness has to be enforced
  # on WRITE with a conditional put against this index's key space; the index
  # alone does not provide it. That is a real weakening versus the Postgres
  # UNIQUE and it belongs in the accessor, with a test.
  ##############################################################################
  global_secondary_index {
    name            = "gsi3"
    projection_type = "KEYS_ONLY"

    key_schema {
      attribute_name = "gsi3pk"
      key_type       = "HASH"
    }
  }

  point_in_time_recovery {
    enabled = var.point_in_time_recovery
  }

  # Explicit, like `modules/state`. This table holds encrypted tenant secrets
  # (`secret` rows carry nonce/ciphertext/mac) and every run's ticket text.
  server_side_encryption {
    enabled = true
  }

  tags = var.tags
}
