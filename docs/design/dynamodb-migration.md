# Moving tenancy from Postgres to DynamoDB

**Status: PLAN, not a decision to implement.** Written 2026-09-10 at the operator's
request after a three-option comparison. The timing objection is recorded in
§8 and was overruled deliberately; it is kept here so a future reader sees it was
weighed rather than missed.

## 1. Why this is being considered at all

One reason, and it is not preference. **Amplify Hosting's SSR compute cannot join a
VPC** — measured against botocore 1.43.75, where the entire Amplify service model
carries zero `vpc`/`subnet`/`securityGroup` fields while Lambda's `CreateFunction`
carries `VpcConfig`; and confirmed by AWS's own answer on re:Post: *"Recently there is
launch of Amplify Hosting Compute even that does not have VPC configuration... RDS needs
to be open to public. However we do not recommend making the DB public."*

So any Postgres the deployed app reaches directly is a Postgres on the public internet
with `0.0.0.0/0` on 5432 — Amplify's egress addresses are not fixed, so the security
group cannot be narrowed. DynamoDB removes the question: no port, no DSN, no password,
IAM authentication from the SSR compute role.

The secondary benefit is real but must not be the argument: `PAY_PER_REQUEST` on this
workload is effectively free, against $13.98/month for `db.t4g.micro` + 20 GiB.

## 2. What is actually being replaced

Measured, not estimated:

| | |
|---|---|
| SQL-bearing modules | **11 files** |
| Lines in `db/` + `tenancy/` + `queue/` | **~4,900** |
| Tests on that layer | **232** across 7 files |
| Consumers of the layer | 12 modules, incl. 4 `web/lib/reader/*.py` |

Seven tables. Six are tenant-scoped; `app_user` is deliberately not, and its
`unscoped_reason` states why: *"one person may hold memberships in several
organisations, so no single tenant owns the row. It is unreachable from tenant scope --
membership is the only route in, and that table IS scoped."*

## 3. The key design

**Single table, `theagentorg`, and the partition key IS the tenant.** That is not an
idiom choice — it is what makes `dynamodb:LeadingKeys` able to enforce isolation at all.

```
PK                    SK                    was
────────────────────────────────────────────────────────────
TENANT#<tenant_id>    ORG                   organisation
TENANT#<tenant_id>    MEMBER#<user_id>      membership
TENANT#<tenant_id>    REPO#<full_name>      repository
TENANT#<tenant_id>    RUN#<run_id>          run
TENANT#<tenant_id>    SECRET#<name>         secret
TENANT#<tenant_id>    BUDGET                budget
TENANT#<tenant_id>    JOB#<job_id>          queue_jobs

USER#<user_id>        PROFILE               app_user   (unscoped, by design)
```

**Three `unique_together` constraints become free.** `repository(tenant_id, full_name)`,
`secret(tenant_id, name)` and `membership(tenant_id, user_id)` are exactly the composite
key, so uniqueness is the primary key rather than an index that has to be declared and
can be forgotten.

**`organisation` has `tenant_column='id'`** — it is scoped by itself — which lands
naturally as `TENANT#<id> / ORG`.

### Indexes

| Index | Keys | Answers |
|---|---|---|
| `GSI1` | PK `RUN#<run_id>`, SK `TENANT#<t>` | `gates.load(run_id)` and `jobs_for_run` without knowing the tenant |
| `GSI2` | PK `STATUS#<status>`, SK `created_at` | the queue's "next READY job" |
| `GSI3` | PK `EMAIL#<email>` | `app_user` unique-email lookup |

GSI1 is the one to scrutinise: a run id is an unguessable uuid, but a GSI keyed on it is
reachable **without** a tenant in the key, so `LeadingKeys` does not constrain it. Any
accessor reading through GSI1 must compare the returned `TENANT#` against the caller's
scope and refuse on mismatch — and that comparison is application code, so it needs a
leak test of its own rather than trust.

## 4. Where isolation comes from — and the honest regression

This is the part that decides whether the migration is acceptable.

### The web path gets a STRONGER guarantee

Today the browser's tenant reaches Postgres as `SET agentorg.tenant_id`, set by
application code. Under this design the chain becomes:

```
Cognito ID token   custom:tenant  (immutable, absent from WriteAttributes)
      │
      ▼  STS AssumeRole with session tag  tenant=<t>
scoped credentials
      │
      ▼  IAM condition  dynamodb:LeadingKeys = ["TENANT#${aws:PrincipalTag/tenant}"]
DynamoDB refuses anything else
```

**No application code is in that chain.** And it lands on work already done and
verified: `custom:tenant` is `Mutable: False`, is not in `WriteAttributes`, and was
measured to be unsettable after creation — so a caller cannot choose the tag that
authorises them. That is a better story than `SET agentorg.tenant_id`, which is one
forgotten call away from unscoped.

```json
{
  "Effect": "Allow",
  "Action": ["dynamodb:GetItem", "dynamodb:Query", "dynamodb:PutItem",
             "dynamodb:UpdateItem", "dynamodb:DeleteItem"],
  "Resource": "arn:aws:dynamodb:us-east-1:339712964409:table/theagentorg",
  "Condition": {
    "ForAllValues:StringEquals": {
      "dynamodb:LeadingKeys": ["TENANT#${aws:PrincipalTag/tenant}"]
    }
  }
}
```

### The pipeline path gets a WEAKER one, and this is the cost

The worker, `run_stage.py` and the five agents do **not** run as a tenant. They process
every tenant's jobs, so they need a credential that can read across tenants — and
`LeadingKeys` cannot apply to a principal that legitimately spans partitions.

Today those callers connect as `agentorg_app`, a non-owning role, and **Postgres RLS
enforces isolation for them too** — measured: owner sees 2 tenants' rows, `agentorg_app`
sees 1. Under DynamoDB with one service credential, the only thing keeping a stage
inside its tenant is the accessor building `PK=TENANT#<t>` correctly. **That is
application code, and it is exactly what this repository argues against.**

**The mitigation, and it should be treated as mandatory rather than optional:** the
worker assumes a per-tenant role for the duration of a job. The job row names its
tenant, so the worker can `AssumeRole` with `tenant` as a session tag before running the
stage and drop the credential after. That restores enforcement-outside-the-code for the
pipeline too, at the cost of one STS call per job (~50ms, cacheable for the job's life).

**If that mitigation is cut for time, the migration should not proceed.** Without it the
project trades a guarantee it has evidence for against one it does not, which is the
opposite of the direction every other decision here has gone.

## 5. Operations that get better, and one that gets harder

**Better — the budget check.** Today it is read-modify-write behind `_require`. It
becomes one atomic conditional update, which cannot race:

```
UpdateItem  SET spent_cents = spent_cents + :amt
ConditionExpression:  unlimited = :true OR spent_cents + :amt <= ceiling_cents
```

A tenant with **no budget row** must still be REFUSED, not admitted — the condition
fails on a missing item, which is the correct direction and matches the existing rule
that absent must not read as unlimited.

**Better — the queue claim.** `UpdateItem` with
`ConditionExpression: attribute_not_exists(claimed_by)` is an atomic claim. A pause stays
a durable item with no visibility timeout, so the 12-hour cap that disqualified SQS does
not exist here. This is the one part of the system DynamoDB genuinely suits better than
SQL.

**Harder — anything that was a transaction across tables.** `TransactWriteItems` caps at
**100 items** and cannot span tables in the way a SQL transaction spans rows. Every
current multi-statement write needs auditing; most are single-partition and become one
`TransactWriteItems` within `TENANT#<t>`, which is fine.

**Gone — the migration ledger.** `migrations.migrate` and its checksum guard become
moot; DynamoDB is schemaless. Lane R's work closing item 2 is superseded. Backfills stop
being DDL and become code, which is *less* safe, not more — a backfill script has no
ledger saying it ran. A replacement marker item (`TENANT#_meta / BACKFILL#<name>`) should
be part of the plan, not an afterthought.

## 6. Keeping the test suite honest

**The hermetic suite must not gain an AWS dependency.** The pattern already exists:
`agentorg/queue/_memory.py` is the in-process backend that keeps 232 tests offline while
`_sql.py` is the durable one. DynamoDB joins as a **third backend behind the same
interface**, not as a replacement for the memory one.

- `boto3` is already a dependency of the five arm64 images (`agent_client` uses it), so
  importing it in `tenancy/` ships nothing new — verify with
  `test_requirements_covers_every_third_party_import_in_the_package` rather than assuming.
- Integration tests run against **DynamoDB Local** in podman, the way Postgres does now.
  `moto` is the alternative and is a test-only dependency; either is acceptable, neither
  may be imported from `agentorg/`.

**`tests/test_tenancy_leak.py` is the acceptance criterion for this whole migration.** It
drives *every registered accessor* and attempts real breaches. If the accessor interface
is unchanged, most of those 52 tests are unchanged — they assert behaviour, not SQL. Two
things must be added rather than ported:

1. a breach attempt through **GSI1**, which is the one index reachable without a tenant
   in the key;
2. a breach attempt with **the wrong session tag**, asserting IAM refuses it — the
   DynamoDB analogue of the measured `agentorg_app` result, and the only test that can
   show `LeadingKeys` is doing anything. Without it the IAM condition is a check that
   cannot be observed failing.

## 7. Sequence

Ordered so that nothing is deleted before its replacement is proven.

| # | Step | Done when | State |
|---|---|---|---|
| 1 | Table + 3 GSIs, `PAY_PER_REQUEST` | table ACTIVE | **DONE** — `modules/tenancy`, applied by CI. Table ACTIVE, 3 GSIs ACTIVE all KEYS_ONLY, PITR enabled |
| 2 | `agentorg/db/_dynamo.py` — key construction, pure functions | unit tests; no AWS call | **DONE** — 18 tests, RED both directions on the cross-language prefix check |
| 3 | `tenancy/accessors.py` gains a DynamoDB backend | the 52 leak tests pass against it | **PARTIAL** — `_dynamo_store.py` + 9 tests done (item ops, GSI1 breach, pagination). The ~20 accessors are NOT ported |
| 4 | The two NEW leak tests (§6) | both fail before the IAM policy exists, pass after | **DONE** — GSI1 breach is hermetic; the tag breach is `preflight.py` check 8, measured below |
| 5 | Queue backend | `test_queue_*.py` 92 tests pass | **DONE** — `_dynamo.py`, 7 tests |
| 6 | STS session-tag path for the web app | wrong-tag breach refused, measured | **DONE 2026-09-15** — `db/tenant_credentials.py` + the readers + the Amplify compute role. Check 8 measured against the deployed policy; see below |
| 7 | **Per-tenant AssumeRole in the worker** (§4) | a stage cannot read another tenant, measured | OPEN — and `runtime_enabled` is false, so no worker is deployed to fix. The pipeline runs on GitHub Actions |
| 8 | `web/lib/reader/*.py` repointed | `/runs` returns real rows on the deployed app | **DONE** — all four readers on boto3, and since step 6 on a SCOPED credential rather than the ambient one |
| 9 | Backfill marker + one-time copy from Postgres | row counts agree both ways | **MOOT** — see below |
| 10 | Retire the Postgres path | only after 1–9; `_memory.py` stays forever | OPEN |

### THE OPERATOR'S DECISION, 2026-09-15: **DynamoDB only**

Asked directly, and it settles the one question this document left open. There is no
Postgres anywhere — not in the deployed path, not in the self-hosted stack, not as a
switchable alternative. `_memory.py` still stays forever, because it is what keeps the
suite hermetic; it is not a second *durable* backend.

**That makes step 9 MOOT rather than done, and the distinction matters.** A backfill
copies production rows. There are none: the deployed app has never had a working
Postgres — CLAUDE.md records `/api/session` answering `tenant_id: null` because the
tenant lookup was circular under RLS, which is one of the two reasons this migration
was proposed. The only Postgres data that ever existed was in a local podman volume
and on a Homebrew service, both of them development fixtures. **Do not write a
backfill script for rows nobody has**; if a future deployment needs one, this row is
where to say so.

### STEP 5 AND 8 WERE DONE BEFORE THIS TABLE SAID SO

Recorded because the table above was stale for five days and read as authoritative.
Commits `2a62254` (step 5) and `da7c1dd` (step 8) landed while the row still said
nothing, and `ecf18ab` ported the twenty accessors the step-3 row calls NOT PORTED.
Verified by running them rather than by reading the commits:

```
tests/test_dynamo_store.py    9 passed
tests/test_queue_dynamo.py    7 passed
tests/test_tenancy_leak.py   52 passed
web/lib/reader/{_client,runs,detail,repositories}.py   all import boto3
```

**A plan document is not a record of what happened.** This one is edited by hand and
the code is not, so when they disagree the code is right — the same rule this
repository applies to a comment and a test. Re-measure before quoting a row.

### A DEFECT THIS MIGRATION INTRODUCED, FOUND 2026-09-15

Moving the self-hosted stack onto DynamoDB means mounting an AWS credential into it,
and that **removed a guard along with the thing it guarded**. `llm.available()` falls
through to boto3 whenever `LLM_DISABLED` and `LLM_BASE_URL` are both empty; the
`api` service set neither, and had never needed to, because
`test_no_aws_credential_or_region_reaches_any_service` made the branch unreachable.
Measured on the parsed compose file:

```
service  LLM_DISABLED  LLM_BASE_URL           -> reaches
worker   (unset)       http://model:11434/v1     gateway
api      (unset)       (unset)                   YES - BEDROCK
web      true          (unset)                   no
```

A stack whose entire claim is that it is self-hosted was one typo from a live billable
model call, with everything green. Fixed in `ae73d75`, and the replacement test asserts
the property the old one was accidentally providing: **every credentialed service must
pin its model path.** The general form is worth carrying into steps 6 and 7 — *when a
capability is added, check which existing test was silently providing a guarantee it no
longer provides.*

### What check 8 measured, 2026-09-10

```
tag=t1  -> TENANT#t1     allowed        <- the POSITIVE CONTROL
tag=t1  -> TENANT#t2     implicitDeny   <- THE BREACH, refused
no tag  -> TENANT#t1     implicitDeny   <- fails closed
tag=t1  -> Query gsi1    explicitDeny   <- the index gap, shut deliberately
```

RED, proving the check is not passing by construction:

```
condition REMOVED  -> tag=t1 reaching TENANT#t2 = allowed
index Deny REMOVED -> Query gsi1 = implicitDeny
```

**So §4's claim that the web path gets a STRONGER guarantee is now measured
rather than argued.** The pipeline half is still the open regression, and step 7
is still mandatory.

### THAT CHECK PASSED AGAINST A ROLE THAT GRANTED NOTHING — 2026-09-15

**Read the section above again knowing this.** Every row of it was correct, and
for five days it described a document no principal in the account was subject to.

Three defects in one chain, each hiding the next, exactly as the three Bedrock
IAM defects did in August:

1. **CI could not attach the policy.** The apply for `e2e4423` failed with
   `AccessDenied` on `iam:AttachRolePolicy` for all three roles. The CI role
   *does* name that action — scoped to
   `Resource: policy/theagentorg-shared-*`. It is authorised against the **role**
   being written to, not the policy being attached (the policy travels as the
   `iam:PolicyARN` condition key), so the statement can never match:

   ```
   iam:AttachRolePolicy  role/theagentorg-shared-tenancy-scoped     implicitDeny
   iam:AttachRolePolicy  policy/theagentorg-shared-tenancy-scoped   implicitDeny
   iam:PutRolePolicy     role/theagentorg-shared-tenancy-scoped     allowed
   ```

2. **So the role existed and carried nothing.** `get-role` answered, `list-role-policies`
   and `list-attached-role-policies` were both empty. The role could be assumed
   and granted zero.

3. **And check 8 reported PASSED.** It read the policy through
   `iam get-policy` + `get-policy-version`, which answer perfectly for a managed
   policy attached to nobody. Four correct rows about an unenforced document.

The check's own docstring named the gap it stopped one step short of: it reads
from the account rather than from Terraform because *"a policy that exists in a
`.tf` file and was never applied is precisely the failure this check exists to
catch"* — and **"applied, and attached to nothing" is the next step along that
same line**, with the same symptom. A check present, enumerable, and enforcing
nothing.

Both halves are fixed at the mechanism. The policies are now **inline**
(`aws_iam_role_policy`), which is what CI can write and which cannot reach the
detached state at all; and `preflight_tenancy` reads them with
`iam get-role-policy`, so the document it evaluates **is** the role's or the call
raises. Measured before and after with nothing in AWS changing between the runs:

```
before   ... four correct rows ...   -> check 8 PASSED
after    -> FAILED: role theagentorg-shared-tenancy-scoped does not carry an
            inline policy named theagentorg-shared-tenancy-scoped
after the apply landed               -> check 8 PASSED, and now it means it
```

**Widening CI's IAM grant was the alternative and was refused**, on the
precedent already set for `iam:CreateServiceLinkedRole`: performed by hand once
rather than granting CI standing power over IAM.

### AND THE SSR RUNTIME HAD NO CREDENTIAL AT ALL — 2026-09-15

Step 6's other half, and it would have made the whole chain unreachable even
with the policy correctly attached. Measured on the live app `d15q7tk62qnlxt`:

```
computeRoleArn      null    (app)
computeRoleArn      null    (branch main)
iamServiceRoleArn   AmplifySSRLoggingRole-6b5aca0f...   -> 4 CloudWatch actions
```

**The two role fields are not interchangeable**, and having one is why nothing
looked unconfigured: `iamServiceRoleArn` is what Amplify assumes on the app's
behalf for logging, `computeRoleArn` is what the SSR Lambda **runs as**. So step
8's readers were repointed at DynamoDB onto a runtime that could write logs and
reach nothing else.

`infra/amplify/spec.py` + `provision.py` now set it, reading the role back from
IAM rather than assembling its ARN — an assembled ARN also succeeds for a role
nobody created. Applied and verified:

```
computeRoleArn  arn:aws:iam::339712964409:role/theagentorg-shared-amplify-compute
environment     TENANT_SCOPED_ROLE_ARN=arn:aws:iam::...:role/theagentorg-shared-tenancy-scoped
carried_keys    ['AMPLIFY_DIFF_DEPLOY', '_LIVE_UPDATES']
```

**THE SELF-HOSTED STACK IS OUT OF SCOPE FOR THIS GUARANTEE, AND ALWAYS WAS.**
`TENANT_SCOPED_ROLE_ARN` is blank in `infra/selfhost/docker-compose.yml` because
the role's trust policy names one principal — the Amplify compute role — while a
laptop session is `arn:aws:iam::339712964409:root`, which can read every
partition regardless of any policy. That is the Postgres superuser finding again:
as the table OWNER every RLS policy was decoration while `pg_policies` listed all
six. Granting a development machine the ability to act as any tenant is an
operator's decision, not a compose-file one.

### One design assumption that did not survive contact

`tenant_assumer_arns = []` was written as a fail-closed default: a role nothing
can assume. **IAM refuses to create it at all** — `MalformedPolicyDocument: The
passed in policy has a statement with no principals!` — so the apply died two
minutes in, after the table had already been attempted. The role is count-gated
now, following `modules/ingress`. An output documenting the unreachable state was
corrected too: a stale comment claiming a safe state is worse than none, because
a reader stops looking.

Steps 1–5 are reversible; the repo keeps working on Postgres throughout. **Step 10 is the
only irreversible one** and should lag the rest by a comfortable margin.

## 8. The objection, recorded

This is the largest rewrite available in this repository, it replaces the isolation
layer, and it is being done close to a Final Evaluation. The thing it puts at risk —
tenant isolation — is the thing the project has the *strongest* evidence for: a leak
suite that attempts real breaches, and a measured owner-vs-`agentorg_app` result.

The alternative that solves the stated problem with none of that risk is **API Gateway +
Lambda in the VPC** (~$14/month), which keeps RLS and all 232 tests untouched and is
AWS's own documented answer to the Amplify-VPC limitation.

Recorded because the repository's rule is that a lane which names what it could not run
is worth more than one reporting green. If this plan is executed and step 7 is skipped,
that trade has been made silently, and this paragraph is the thing that says so.
