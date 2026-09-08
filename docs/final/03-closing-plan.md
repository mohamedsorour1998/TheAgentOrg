# Closing plan — the final phase

**Written 2026-08-29, after the pre-final was passed and the team qualified for the Final
Evaluation.** Five lanes, running in parallel, closing every item CLAUDE.md's "WHAT IS
STILL OPEN" section names — plus the migration the operator asked for: **AWS Cognito for
authentication and AWS Amplify for hosting**, matching a deployment they already run.

This plan is a record of decisions, not a wish list. Every lane below is executing as this
is written; the numbered items trace to CLAUDE.md's open list, and each one is closed,
superseded, or **proved uncloseable here** — the third being a real outcome and not a
failure to try.

---

## 0 · The reference that made this cheap

`~/sorour/AgentsforHumansHackathon/` is the operator's own working Amplify + Cognito
deployment, in the same AWS account and region. It is read rather than reinvented:

| File | What it already solved |
|---|---|
| `amplify.yml` | the monorepo spec form, the `.env.production` writes, `nvm use 22`, the reserved `AWS` prefix |
| `infra/provision_cognito.py` | 1,063 lines, idempotent, with branding and a custom domain |
| `infra/provision_amplify.py` | 738 lines, find-or-create |
| `web/lib/cognito.ts` | `verifySession` → identity or **`null`**, with no middle value |

**Its `amplify.yml` comment block is the most valuable file in the reference**, because
every paragraph is a failure somebody already paid for. Two examples that would each have
cost a build cycle: the `applications:`/`appRoot:` form is *mandatory* when
`AMPLIFY_MONOREPO_APP_ROOT` is set and removing the variable instead fails at clone time,
so **the two are a pair**; and Amplify's build image ships Node 18, which Next 16 and
`jose` 6 do not support.

---

## 1 · The insight that reorders the work

CLAUDE.md's open item 1 is that **the tenant lookup is circular under RLS**.
`membershipsFor` reads the RLS-scoped `membership` table to discover the tenant that RLS
needs bound. Measured, one connection, same query, same role:

```
no tenant bound      -> []
tenant-zero bound    -> [('tenant-zero',)]
```

So `/api/session` answers `signed_in: true` with `tenant_id: null` and every authenticated
route 401s. Fail-closed, and correct as a default — the alternative would work in a demo
and hand every new signup the original deployment's runs.

**A verified Cognito claim dissolves it.** If the ID token carries `custom:tenant`, the
tenant arrives already authenticated, signature-verified against Cognito's published JWKS,
and *no RLS-scoped read is needed to discover it*. The database lookup that could not scope
itself is replaced by a claim that never needed to.

That is why the Cognito migration is not merely the operator's preference: **it closes the
hardest open item as a side effect of a change they wanted anyway.** The plan is sequenced
around it.

**What it trusts, stated precisely.** A tenant claim is only as good as who sets it.
Cognito's admin API sets it at user creation — server-side — and the token is
signature-verified, so it is trustworthy in a way a client-supplied `tenant_id` never is.
Lane K and Lane I both refused a request-supplied tenant and an AST test forbids one. That
refusal does not weaken: the tenant comes from the verified token, never from a body, a
query or a header.

---

## 2 · The five lanes

Ownership is by **file**, as it was for the previous fourteen — that is what let them run
without collisions, and it is also what produced the "correct answer nobody asks for"
pattern, so every brief demands its lane answer *what calls this?*

| Lane | Owns | Closes |
|---|---|---|
| **P** | `web/lib/{cognito,authorize,session,auth,tenant}.ts`, `web/app/api/auth/**`, `web/app/api/session/route.ts` | items 1, 6 — Cognito replaces Auth.js |
| **Q** | `amplify.yml`, `infra/amplify/**`, `infra/cognito/**` | the hosting migration |
| **R** | `agentorg/db/**`, `agentorg/tenancy/**` | items 1 (database half), 2 |
| **S** | `agentorg/cost/**`, `web/lib/reader/**`, `web/app/api/runs/**` | items 3, 4 |
| **T** | `pyproject.toml`, `target_repo/tests/e2e/**`, `docs/final/evidence/**` | items 5, 9, 10 |
| **integrator** | `graph.py`, `run_stage.py`, `CLAUDE.md`, every merge | the wiring lines lanes cannot apply |

**Items 7 and 8 are not lane work.** Item 7 (a repository admin can bypass all three gates)
is an operator setting, reported by `preflight.py` check 4 and deliberately not failed on —
the honest answer to "can a gate be skipped?" is *yes, by an admin*, and it is recorded.
Item 8 (a possibly-unrotated `github_pat_`) is one click nobody in this repository can
make; two files disagreed about it and both now state the pessimistic reading.

---

## 3 · What must not regress

The previous phases' guarantees are the acceptance criteria for this one. Verified live
before the work started, and each must still hold after:

```
POST /api/approvals unauthenticated          -> 401 "Nothing was recorded."
  + Origin: https://evil.example             -> 403, BEFORE authentication
  + a body carrying its own "by"             -> 401
  + decision: "overridden"                   -> 422 (renderable, refused)

preflight.py  all SEVEN checks PASS, runtimes v39
LINES: [3, 4]  provenance: scanners
```

**That last line is the one no green suite can replace.** It has survived Lane C rewriting
all three scanner wrappers, Lane D moving every GitHub call behind an interface, Lane H
adding retrieval, and Lane M rewriting all six prompts. It is re-read after every phase.

And `by` must still come from the session rather than the body — a test drives a hostile
body carrying its own `by` and asserts the record reads the real person. **The identity's
source changes from an Auth.js session to a Cognito claim; the refusal does not.**

---

## 4 · What is deliberately NOT built

**No RDS.** The operator decided the database is local, after the full stack was verified
running on Postgres 17.11 under podman. `infra/Terraform/modules/platform/main.tf` records
why an `aws_db_instance` was not built and what that costs: nothing in AWS holds run
history, so the web app and the queue are reachable only where the containers run. The
GitHub Actions pipeline is unaffected — it never used that database.

**The pipeline stays on GitHub Actions.** `run-pipeline.yml` carries the whole demo and is
the fallback. Lane A's queue is the second path, not the replacement, and `run_stage.py` is
the shared stage implementation both run — `queue/runner.py:49` invokes it as a subprocess
deliberately, for per-stage process isolation.

**Nothing is applied while the operator sleeps.** The lanes write and verify; they do not
create a Cognito pool, an Amplify app, or any billable resource. The authorisation covers
the work, not spending money unattended.

---

## 5 · The gate every lane passes

Unchanged from §5 of the implementation plan, plus the web gates the Python ones cannot
see:

```bash
.venv-main/bin/python -m pytest -q                          # green
.venv-main/bin/python -m ruff check agentorg scripts tests  # exit 0
actionlint .github/workflows/*.yml                          # exit 0
cd infra/Terraform && terraform fmt -check -recursive        # exit 0
.venv-main/bin/python scripts/preflight.py                  # all seven checks

cd web && npx tsc --noEmit && npm run lint && npx vitest run && npx next build
```

**`npm run build` is not redundant with the other three.** `tsc`, `eslint` and `vitest` all
read the working tree and none of them compiles the app; two defects passed all three and
were caught only by the build, and a third — `/` answering 404 — only by serving it.

Baseline at the start of this phase: **1966 passed, 3 skipped**; web **10 files, 166
tests**. Lane T's `testpaths` change moves the first number, which is why its brief demands
the before and after.
