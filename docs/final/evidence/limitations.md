# Limitations, deliberately kept

**A limitation that is merely admitted is weaker than one that is costed.** The
costing is what shows we understand it rather than just noticing it. So each entry
below carries: what it is, how it was found, **what removing it would take**, and
**why that is not this phase's priority**.

The pre-final evaluation's strongest moment was volunteering a limitation before
being asked. This document is a first-class deliverable, not an appendix.

Nine entries. Seven are carried forward from specification §13; two were found
while writing Lane L's evidence and are new.

---

## 1 · Reported line numbers are indices into the added-lines-only view

**What.** A finding at `app/auth.py:3` does not mean line 3 of `app/auth.py`. It
means the **third added line** — the numbers index into the added-lines-only file
`agentorg/common/diff.py` materialises, not into the real file. A finding's line
number is therefore not usable for navigation.

**Cost to remove: two changes that must land together, or neither.**

Correcting the materialiser shifts `{3, 4}` to `{4, 5}` — which is the **fixture's**
pair. The two modes would then be indistinguishable, and the discriminator this
project's entire verification story rests on would be gone. The fixture would still
say `{4, 5}` and so would the real scanners, so **every provenance assertion in the
suite would keep passing while proving nothing.**

So the work is: fix the offset, *and* re-measure the fixture onto a new distinct
pair, *and* update `tests/provenance.py`'s two frozensets, *and* re-verify
`scripts/preflight.py` check 3 against a deployed runtime. Roughly a day, most of
it verification rather than code.

**Why not now.** Doing the first half without the second is **strictly worse than
doing neither** — it destroys a working discriminator and leaves a green suite that
proves nothing. This is a change that must not be attempted under time pressure,
and the final evaluation is time pressure.

**Measured consequence worth knowing:** `REAL_SCANNER_LINES` is a property of the
scanners **and of one exact diff**. A poisoned diff differing from
`fixtures/dev_result_poisoned.json` by a single missing blank line produced
`LINES: [2, 3]` — with a correct `block`, `blocking=2` and
`provenance: scanners`. That is why `preflight.py` loads the reference diff rather
than carrying a copy.

---

## 2 · All three gate Environments allow admin bypass

**What.** Measured on the live repository:

```
gate1  rules=['required_reviewers']  can_admins_bypass=True
gate2  rules=['required_reviewers']  can_admins_bypass=True
gate3  rules=['required_reviewers']  can_admins_bypass=True
```

So the honest answer to *"can a gate be skipped?"* is **yes, by a repository
admin, without a reviewer clicking.**

**Cost to remove: one API call per environment, about five minutes.**

```bash
gh api -X PUT repos/:owner/:repo/environments/gate1 \
  -F can_admins_bypass=false
```

**Why not now.** It is an **operator setting, not a code path**, and it is left as
chosen deliberately: during a live demo the ability to unblock a stuck run is worth
more than the theoretical hardening. `preflight.py` check 4 prints it on every run
and deliberately **does not fail** on it — failing would make the script refuse a
configuration the team chose.

The reason it is listed rather than quietly left: a judge may ask, and the answer
should not have to be discovered mid-demo.

---

## 3 · gitleaks severity is a constant

**What.** Every gitleaks finding is hardcoded `critical` at
`agentorg/security/gitleaks_tool.py:190`. trivy and semgrep map their own native
severities onto our four; gitleaks does not map anything.

The claim "a fixed severity threshold decides" is exactly true for trivy and
semgrep and **vacuously** true for gitleaks: every finding is critical, so the
threshold never discriminates. That is defensible for a secret scanner — a leaked
credential is not a "medium" — but it is not what the sentence sounds like, and a
judge reading the code will notice.

**Cost to remove: one to two days, and the spec's position is that it should not be
removed.**

A real mapping would read gitleaks' own signals: rule id, entropy, and
verification status. That is a genuine mapping and a genuine risk — entropy-based
severity means a *low-entropy* real credential could map below the threshold, which
converts a fail-closed constant into a fail-open heuristic.

**Why not now.** Being caught papering over it would cost more than the finding
itself, so the fix is **documentation, not code**: keep the constant, say so
loudly, and treat the constant as the policy. Lane C owns the scoring-policy table
that states this for every scanner including the fail-closed default.

---

## 4 · A vague ticket can legitimately fail at the revision cap

**What.** Clean run `32557597915` ended `status=failed`, exit 4, with security
reporting `PASS`. Four model-written review rounds: the reviewer asked for
**email-based** rate limiting, the developer kept producing **IP-based**, and the
cap expired. The scanners cleared the diff; nobody approved it.

This is a property of the ticket, not a bug. Before the model was unblocked the
path was unreachable, because `fixtures/review_result.json` always approves.

**Cost to remove: unbounded, and the removal would be a defect.**

The mechanisms that would "fix" it are all worse: raise the cap (burns tokens on a
disagreement that is not converging), have the reviewer downgrade to `approve`
after N rounds (discards a real objection), or let the developer overrule the
reviewer (deletes the review).

**Why not now.** A pipeline that always converges is one whose reviewer does not
mean anything. The operational answer is a **specific ticket**, and the wording
measured to reach `promote` is recorded in `CLAUDE.md`.

What *could* be built, cheaply, is **disagreement detection** — noticing that the
reviewer's `must_fix` has not changed across two passes and reporting "the agents
are deadlocked on X" instead of exhausting the cap silently. Half a day, and it
turns an opaque failure into a legible one. That is a real candidate for the next
phase.

---

## 5 · The reviewer's verdict is advisory

**What.** `graph.py` loops on a non-`approve` verdict; it never stops. Only
`compute_security_verdict` stops the pipeline. So if the scanners miss something,
the reviewer is the only thing that saw it, its opinion is advisory, and the change
can reach `main` past three human gates.

**This is an accepted limit, not a defended one.**

**Cost to remove: the wrong question.** Making the reviewer binding is
*technically* trivial — one branch in `graph.py`. It would also hand the shipping
decision to a model that can be persuaded, distracted or prompt-injected, which is
the exact thing this repository exists to prevent. The cost is not the code; the
cost is the entire thesis.

**Why not now, and not later either.** The two catchers are not redundant and their
authority is asymmetric on purpose:

| | reviewer | security |
|---|---|---|
| what it is | a model reading the diff | three real scanners + five lines of Python |
| catches | intent, logic, plan mismatch, taste | credentials, known CVEs, injectable patterns |
| authority | **advisory** | **binding** |
| can be talked out of it | yes — it is a prompt | **no** — no model is involved |

The demo would still block with the reviewer removed entirely. It would not block
with the scanners removed.

**What genuinely reduces this limit** is a *third*, non-model catcher: generated
tests that actually fail (specification §9). A failing test is a fact, like a
scanner finding, and can be binding without handing authority to a prompt. That is
Lane G, this phase.

---

## 6 · Self-hosted parity is partial until the approval UI lands

**What.** Three of the four layers already self-host: the scanners run in our own
container today, `LLM_BASE_URL` routes to any OpenAI-compatible gateway, and the
queue worker removes GitHub-hosted runners by construction.

**The human gates do not.** They are GitHub Environments, enforced by the platform.

**Cost to remove: the §11 approval UI — the largest single item in the plan.**

It needs authentication, per-repository authorisation, an audit record of who
approved what, and CSRF protection. `agentorg/approve_server.py` exists today as
buttons over `gates.resume` with **no authentication at all**, bound to loopback,
documented as never-expose. It is a starting point for the shape, not for the
security.

**Why not now.** It *is* now — Lanes I and J, this phase. Listed here because
requirements §6 and §9 are **coupled**, and a plan that sequenced self-hosting
before the UI would have produced a self-hosted deployment with no way to approve
anything.

**The parity statement with teeth:** self-hosted today means no human gates, which
means the deterministic block is the only thing standing between a credential and
`main`. That is stronger than most pipelines and weaker than our cloud path, and
the difference should be stated in exactly those terms.

---

## 7 · A generated test proves less when it passes than when it fails

**What.** A failing generated test is a fact. A passing one proves only that the
change does not violate a property the same system chose to check.

**Cost to remove: unremovable — it is a property of testing, not of this system.**

**Why it is listed anyway.** Because the *misuse* is removable, and the design
already forbids it: `state.GeneratedTests` carries `passed`, `failed` and `binding`
as **separate** fields rather than one verdict, with `binding` true only when a
failure was observed. A single verdict field would have made "the generated tests
passed" quotable as proof of correctness.

Two further guards belong to Lane G and are not yet built: the agent that wrote the
change must not be the sole author of the test that clears it — either a different
agent generates it, or it is generated from the **ticket's acceptance criteria**
rather than the diff. Same separation-of-authority principle as the security
verdict, one layer out.

---

## 8 · NEW · There is no cost or token accounting anywhere

**What.** Measured 2026-08-28: `grep -rn 'input_tokens' agentorg/` returns exactly
two hits, both field declarations in `state.py`. `agentorg/common/llm.py` contains
no usage accounting of any kind. Nothing writes `StageCost`.

Two consequences, both of which a judge will ask about directly:

- **Cost per merged change cannot be measured** — the scorecard reports it as an
  unmeasured dimension with the reason, rather than estimating it.
- **Prompt caching is unmeasured**, and the five agents re-send a repository
  snapshot on every call. `cache_read_input_tokens` being non-zero is the single
  largest silent cost question in the current design.

**Cost to remove: Lane E, this phase.** The contract already exists — the Phase 0
batch added `CostRecord` and `StageCost` with `cached_tokens` separated from
`input_tokens` precisely because it is priced differently and is the number that
reveals whether caching works at all.

**Why not now — it is now, and this entry records why it was not sooner.** The
pipeline was built to prove a *gate*, and a gate's correctness does not depend on
what it cost. That was the right priority and it is now the binding constraint on
two judge requirements: the cost comparison against a developer driving Claude Code
by hand, and the cost view in the product UI.

**Note the deliberate design in the contract:** `CostRecord.usd` is `None` rather
than `0.0` when unpriced. Defaulting to zero would make a missing price table look
like a free run — this project's signature defect shape, a value that reads as a
legitimate answer when the question was never asked.

---

## 9 · NEW · The dispatch token's rotation status is contradicted in the repository

**What.** Found while auditing the rejection log. Two files disagree about whether
a known-exposed credential has been rotated:

```
.github/workflows/terraform.yml:213   "all ten were deleted and the token rotated."
CLAUDE.md:1876                        "THE TOKEN STILL NEEDS ROTATING."
```

The exposure was real and is documented: `terraform.yml` uploaded the binary
`tfplan` as an artifact, a binary plan embeds Terraform **state**, and this state
holds `aws_secretsmanager_secret_version.dispatch_token`. Unpacked from artifact
`9466368657`, three entries each matched `github_pat_[A-Za-z0-9_]{20,}` exactly
once. The upload was narrowed to `plan.txt` and ten artifacts were deleted.

**Deleting the artifacts removed the distribution channel, not the exposure.**

**Cost to remove: minutes, and it is an operator action no code change can
perform.** Mint a new fine-grained PAT scoped to both repositories —
`auth-service` for contents + issues + pull requests, `TheAgentOrg` for
`actions:write`, since narrowed to either alone the other half fails silently —
write it to Secrets Manager, and re-apply so the EventBridge connection picks it
up.

**Why this is listed as a limitation rather than fixed here.** Lane L owns
`docs/final/evidence/**` and `scripts/measure_*.py`; it owns neither
`.github/workflows/**` (Lane N) nor the AWS account. **Handed to the integrator as
an action item**, with the recommendation that the contradiction be resolved in
whichever direction is true — and that until it is, the token be treated as
compromised, because that is the safe reading of a disagreement.

The token also **lands in S3 Terraform state** unavoidably: an API_KEY connection
takes the value through configuration. That is an accepted risk with a documented
audience (anyone with read access to the backend bucket) and is **not** the same
exposure as the artifact one, whose audience was anyone who could read the
repository's workflow runs. Same secret, different blast radius.

---

## What is NOT on this list, and why

Three things a reader might expect here are absent deliberately:

**The agents' fixture fallback is not a limitation.** It is correct behaviour —
every agent degrades to a fixture rather than failing a run. What *was* a defect is
being unable to **see** it, and that is why `scan_provenance` and
`model_provenance` exist and why `preflight.py` check 3 reads line numbers rather
than counts.

**`STATE_BACKEND=dynamodb` raising is known debt, not a limitation.**
`run_stage.py:_load` calls `gates._state_path`, which refuses on that backend by
design, so every cloud stage after `plan` would raise. The cloud pipeline sets no
`STATE_BACKEND` and runs on the `local` default with an artifact handoff. Nothing
is degraded today; a future change to that knob has a known landmine.

**The three skipped tests are an instrument, not a gap.** They live in
`tests/test_provenance.py` and fire only when all three scanner binaries **are** on
PATH. On a machine with no scanners those three RUN and the skip count is 0. That
inversion is a deliberate check: `7 passed` from that file on a provisioned machine
means the skips are broken.

---
---

# PHASE 4 · seven more, and one struck off

**Nine entries above, measured at `9b2b1ee`.** One is now partly closed and eight are
unchanged. Seven new ones follow — every one found by measurement in this phase, and
every one costed the same way: what removing it takes, and why not now.

## Entry 8 is PARTLY CLOSED, and the remainder is a different limitation

The instrument exists. `agentorg/cost/` prices a run, `llm.usage()` records every call,
and `scripts/measure_cost.py` reports **$0.013036 – $0.016931** per clean change with
the AWS Pricing API query and its read date attached. The cost dimension is no longer
an unmeasured row on the scorecard.

**What remains is not the same limitation.** It is entry 10.

---

## 10 · `RunState.cost` is never assigned, so no run carries its own cost

**What.** The instrument works and nothing on the pipeline path calls it. Measured over
the AST, both pipelines:

```
agentorg/graph.py       state.cost stores at NONE   cost-API calls NONE
scripts/run_stage.py    state.cost stores at NONE   cost-API calls NONE
grep -rn '.cost =' agentorg/ scripts/   ->  (nothing)
```

The usage payload **does** now cross the remote seam — `server.py:203` sends
`"usage": llm.usage_payload()` and `agent_client.py:556` calls `absorb_usage_payload`.
So the tokens reach the runner and then nothing writes them onto the state.

**Three visible consequences.** The SRE's cost block renders `""` on every run
(`sre.py:182` guards on `state.cost is not None and state.cost.stages`, correctly). The
web `/api/runs/[runId]/cost` endpoint reads the field correctly and answers empty. And
`measure_cost.py` must drive `graph.run_pipeline` in process rather than reading a
deployed run.

**Cost to remove: two lines per pipeline**, in `graph.py` and `scripts/run_stage.py`.
`llm.reset_usage()` at the start of a stage and `state.cost = merge_cost_records(...)`
at the end, plus `llm.attribute_usage_to(stage)` so every call does not land in a single
`plan` row — which Lane E measured happening without it.

**Why not now.** Both files are the integrator's, and the correct shape is a decision
about per-stage attribution across a nine-stage run handed job to job as an artifact,
not a two-line patch. `usd` is also **not** the discriminator for whether it landed —
`len(state.cost.stages)` is, because an unwired run has zero rows with `usd=None` while
a wired run that fell back has a row per stage with `usd=0.0`.

---

## 11 · No Postgres has ever been connected, so the sign-in flow has never completed

**What.** `agentorg/db/engine.py` is sqlite3-only. Nothing in the 1853-test suite
connects to a Postgres. `docker compose up` has never run — re-measured 2026-08-28,
`command -v docker` finds `/opt/homebrew/bin/docker` and `docker info` reports no
daemon. So every authenticated web route refuses with `no-tenant`, and the Auth.js
sign-in flow that `next-auth` + `@auth/pg-adapter` + `pg` exist to serve has **never
completed once**.

**The Postgres RLS is real emitted DDL asserted structurally, and nothing has executed
it.** Same distinction as a green `terraform apply` against
`simulate-principal-policy`: one proves the policy was written, the other proves it
permits the call.

**Two things this does NOT mean.** The web application typechecks, lints, passes 166
tests across 10 files, and builds 18 routes — verified here. And the refusal is
**fail-closed by design**: returning tenant zero would have demoed perfectly and handed
every new signup the original deployment's runs.

**Cost to remove: a Postgres and one migration run.** `docker compose up -d postgres`,
`DATABASE_URL` pointed at it, `python -m agentorg.db.migrations`, then sign in. The
schema is already one definition emitting two dialects, so there is nothing to write.

**Why not now.** It needs a Docker daemon on the demo machine, and installing one the
day before a judged demo is the kind of change that turns a working laptop into a
broken one. The honest position is that the sqlite path is the *tested* path and the
Postgres path is *emitted but unexecuted*.

---

## 12 · The Postgres queue dialect is never executed

**What.** `agentorg/queue/_sql.py` carries both dialects; `QUEUE_BACKEND` defaults to
`memory` and psycopg is not installed. The one place that selects `postgres` is
`infra/selfhost/docker-compose.yml:134` — the file that has never been run.

**And `config.py`'s own comment contradicts its own code**, three lines apart:
*"The durable backend is the deployed default and is chosen deliberately, never
inherited"* — while `config.QUEUE_BACKEND` with no environment set measures `'memory'`.
This is the record disagreeing with itself, which CLAUDE.md names as worse than either
claim alone.

**Cost to remove: `pip install psycopg` plus entry 11's Postgres.** The SQL is written
and the migrations are dialect-aware.

**Why not now.** Same daemon, and the deeper reason is entry 13: the queue has no
caller in the deployed path, so executing its Postgres dialect would verify a code path
production does not take.

---

## 13 · The queue replaces GitHub Actions in principle and in nothing else

**What.** 1,931 lines of `agentorg/queue/` plus 745 of `scripts/worker.py`, verified end
to end with no Actions involved — and `grep -icE 'queue|worker'
.github/workflows/run-pipeline.yml` returns **1**, which is a comment about DynamoDB.
All seven jobs still run `scripts/run_stage.py`. The worker's only caller is the compose
file from entry 11.

This is the rejection recorded in `scorecard.md` §7, refused under R4.

**Cost to remove: a rewrite of `run-pipeline.yml` around a long-running worker, plus a
place for it to run.** The queue's gate model is genuinely *stronger* than an Actions
Environment — a pause is a durable row, `claim` will not hand a `paused` job to a
worker, and there is no path from `paused` to `ready` without a `HumanDecision`, whereas
all three of our Environments measure `can_admins_bypass=True`.

**Why not now.** Actions is what has been verified against the live account for a week,
and CLAUDE.md's rule holds: anything that looks like a crash on a projector outranks
polish. The claim is downgraded rather than the code, and the honest sentence is that
dependence on Actions is now a **choice rather than a constraint**.

---

## 14 · No browser has ever run the browser tests, and they are outside the main suite

**What.** Two separate facts, and the second is the one nobody has written down.

**No driver exists.** Measured 2026-08-28: `import selenium` →
`ModuleNotFoundError`; `chromedriver` and `geckodriver` both ABSENT from PATH;
`safaridriver` present at `/System/Cryptexes/App/usr/bin/safaridriver` but it needs
Safari ▸ Develop ▸ Allow Remote Automation, a GUI action, and CLAUDE.md records it
hanging with no `/status` response in 120 s. Four Selenium tests are written and skip.

**They are not in `pytest -q` at all.** `pyproject.toml:77` is `testpaths = ["tests"]`,
so `target_repo/tests/e2e/` is never collected by the command every gate runs —
measured, `pytest --collect-only -q | grep -ci selenium` returns **0**. Run the
documented way they behave exactly as designed:

```
cd target_repo && PYTHONPATH=. python -m pytest tests/e2e -q
  1 passed, 4 skipped in 0.04s
  - the `selenium` package is not installed
  - no chromedriver or geckodriver on PATH
  Set SELENIUM_REQUIRED=true to make this a failure instead.
```

So the skip **is** visible and `test_the_skip_is_visible_and_not_silent` does run — but
only for somebody who knows to `cd` first. A reader who runs the four documented gates
sees no mention of a browser test in either direction, which is the failure mode
`SELENIUM_REQUIRED` was built to prevent, one level up.

**Cost to remove: two commands and a GUI click.** `pip install selenium` plus a driver,
or enable Safari's remote automation. Then `SELENIUM_REQUIRED=true` promotes the skip to
a fault (`1 failed, 4 errors`, measured by Lane G) so the tests cannot silently stop
running. Adding `target_repo/tests/e2e` to `testpaths` is a third line and is the part
worth arguing about — everything below the driver is already verified against a real
socket, because `LiveServer` opens one (`app.test_client()` is an in-process WSGI shim a
browser cannot reach).

**Why not now.** Installing a browser driver on the demo machine the day before a judged
demo, to enable four tests whose subject is a login form the unit tests already cover, is
a bad trade against CLAUDE.md's rule that a projector crash outranks polish.

---

## 15 · `SecurityResult.scoring` reaches a PR comment and the web endpoint answers empty

**What.** Lane C's scoring policy is wired on the *pipeline* path — `security.py:291`
calls `score_findings` and `graph.py:232` renders the table into the PR comment, so a
blocked run's comment carries the audit rows. The web `/scoring` endpoint reads the same
field, imports the same module (`web/lib/reader/detail.py:46`), and answers **empty**,
because the runs it reads were recorded before the producer landed.

**This is a data-vintage limitation, not a wiring one**, which is why it is worth its own
entry: the code is complete on both sides and the gap closes itself as soon as a run is
recorded through the new path. Nothing needs editing and nothing will announce that it
happened.

**Cost to remove: one run.** Drive the pipeline once with the scoring producer in place
and the endpoint has rows.

**Why not now.** It needs entry 11's Postgres to be visible in the UI at all, so it is
downstream of a limitation with a Docker daemon in front of it.

---

## 16 · A leaked `github_pat_` may still be unrotated, and this repository cannot settle it

**What.** Entry 9 above records two files disagreeing. Re-read at `d6165c8`, they still
disagree: `terraform.yml:213` says the artifacts were deleted **and the token rotated**;
`CLAUDE.md` says the safe reading is the pessimistic one and to treat it as compromised
until an operator confirms otherwise.

**Nothing in the repository can resolve it**, and that is the structural point. A PAT's
creation date is visible only to the account that holds it, and a live token and a
rotated one behave identically from in here.

**Cost to remove: one click by a human, outside this repository.** Check the token's
creation timestamp at `github.com/settings/personal-access-tokens` against 2026-08-22.
If it predates that date, rotate and update the Secrets Manager entry the API_KEY
connection reads.

**Why not now.** Not a priority question — nobody in this repository *can* do it. The lesson is about the record rather than the token: **when a fix depends on an
action outside the repository, the repository can only record that the action is
required and how to verify it, never that it happened.** Two files claiming opposite
things about a credential is worse than either claim alone, because a reader who finds
the reassuring one stops looking.

---

## The Phase 4 shape, and what it says

| # | Limitation | Blocked on |
|---|---|---|
| 10 | `RunState.cost` never assigned | an integrator decision about per-stage attribution |
| 11 | no Postgres ever connected | **a Docker daemon** |
| 12 | Postgres queue dialect never executed | **a Docker daemon** + psycopg |
| 13 | the queue has no caller in production | a `run-pipeline.yml` rewrite, deliberately deferred |
| 14 | no browser has run the browser tests | a driver install + a GUI click |
| 15 | `/scoring` answers empty | one run, downstream of 11 |
| 16 | a token's rotation status is unknown | **a human, outside this repository** |

**Four of seven are blocked on one thing** — a Docker daemon, or a person, on a machine.
None is blocked on a design that does not work. That is the honest summary and it is a
better one than a shorter list would give: the pattern is *unexecuted*, not *broken*, and
the difference is exactly the one this project spends its whole verification story on.

---
---

# CORRECTION · §11 and §12 were overtaken while this document was being written

**`main` advanced by two commits during Phase 4** — `471fc31` and `69ab1d3` — and both
struck at entries above. A real **PostgreSQL 16.15** was connected for the first time, and
it refused two things that had passed every structural test.

**Re-verified independently rather than relayed**, because this document's own rule is
that a number comes from a command whose output is pasted. Started the local service,
created a database, applied the rendered DDL and drove the queue:

```
psql -tAc 'select version()'
  PostgreSQL 16.15 (Homebrew) on aarch64-apple-darwin25.6.0

schema.render_schema("postgres") applied to a fresh database
  POSTGRES DDL APPLIED
  tables:       ['app_user','budget','membership','organisation','repository','run','secret']
  RLS policies: 6

QUEUE_BACKEND=postgres QUEUE_DSN=postgresql:///laneL_verify
  enqueue OK   job_id='c23eea4b…' run_id='laneL-verify-1' stage='plan' poisoned=True status='ready'
  claim OK     laneL-verify-1 plan poisoned= True
```

So **all seven tables create, six RLS policies exist, and the queue's Postgres dialect
enqueues and claims with `poisoned` surviving as a real `bool`.**

| Entry | Was | Now |
|---|---|---|
| **§11** | *"no Postgres has ever been connected"* | **WRONG.** One has. The DDL executes and the RLS policies are real objects, not asserted strings |
| **§12** | *"the Postgres queue dialect is never executed"* | **WRONG.** It executes, after `69ab1d3` fixed a `DatatypeMismatch` on the first INSERT |
| **§13** | the queue has no caller in production | **UNCHANGED.** Executing a dialect is not deploying a path |

### What is STILL true, and it is narrower than the two entries claimed

- **The web sign-in flow has still never completed.** A schema existing is not a session
  being issued, and `69ab1d3` records a *new* obstacle: the tenant lookup is **circular
  under RLS** — `membershipsFor` reads `membership`, which carries an RLS policy needing a
  bound tenant, and the bound tenant is what that query exists to discover. Measured
  there: `no tenant bound -> []`, `tenant-zero bound -> [('tenant-zero',)]`. So §11's
  *conclusion* survives its *premise* being wrong, for a better reason.
- **`docker compose up` has still never run.** Re-measured after the rebase: `docker info`
  reports no daemon. The Postgres above is a Homebrew service, not the compose stack, so
  the container topology is still unexecuted.
- **`config.QUEUE_BACKEND` still defaults to `memory`** while its own comment three lines
  above says the durable backend is the deployed default. §12's contradiction stands.
- **The RLS is only as good as the ROLE.** `69ab1d3` measured the sharpest thing here: as
  the superuser owning the tables the policies isolate **nothing** — `attacker sees
  [('run-t-attacker',…),('run-t-victim',…)]` — and refuse correctly only as a plain LOGIN
  role. So "Postgres enforces reads" is true of a correctly-provisioned role and false of
  the connection a developer most easily makes.

### The lesson, which is the reason this correction is appended rather than edited in

**Two entries in a limitations document went stale inside four hours, and the document was
the last thing to know.** Both were measured true at `d6165c8` and false at `69ab1d3`. The
originals are left in place above rather than rewritten, following this repository's own
practice — `0d229dc` retains a wrong 2-of-5 measurement marked `[SUPERSEDED]` *"because
the 2/5 is the evidence that the defect was real"*.

Here the superseded text is evidence of something more useful than a defect: **it names
exactly what nobody had run, and somebody then ran it and found two real bugs.** A
limitation written down is a limitation somebody can close, and these two were closed by a
different lane within hours of being recorded. That is the argument for the whole document.

---

## 17 · `migrations.migrate` accepts a Postgres dialect it cannot execute

**What.** Found while re-verifying §11 rather than by reading. `migrate()` takes
`dialect: str = schema.SQLITE` and is happy to be passed `"postgres"` — and then calls
`connection.executescript` at three sites, which only `sqlite3` provides:

```
migrations.migrate(psycopg_connection, dialect="postgres")
  AttributeError: 'Connection' object has no attribute 'executescript'

sqlite3 Connection has executescript: True
psycopg Connection has executescript: False
```

Its own annotation is `connection: sqlite3.Connection` (`migrations.py:111`), so the
signature is internally consistent — the `dialect` parameter offers a backend the
`connection` parameter cannot be.

**The consequence is narrower than "Postgres does not work", and worse than it sounds.**
Every Postgres verification so far — `471fc31`, `69ab1d3`, and this lane's — applied the
DDL through `schema.render_schema("postgres")` directly. That works. But it bypasses the
migration runner entirely, so **the forward-only ledger, its checksum guard and its
idempotency have never executed on Postgres.** `migrations.py`'s own docstring explains
why that ledger matters: *"A migration edited after it was applied is the failure that
produces two databases with the same version number and different shapes"*, and the
checksum is the only thing that can see it.

So a Postgres deployment today gets the schema and **no version history** — the exact
condition the ledger exists to prevent.

**Cost to remove: one dialect-aware statement splitter.** `executescript` is
sqlite3-specific sugar for "run this multi-statement string"; psycopg's `execute` will
take one too, so the fix is roughly `if dialect == POSTGRES: connection.execute(sql) else:
connection.executescript(sql)` at three sites, plus widening the annotation. The DDL
itself already renders correctly per dialect — that is what `471fc31` fixed.

**Why not now.** `agentorg/db/` is not this lane's file, and the change wants a test that
runs the ledger twice against a real Postgres to prove idempotency — which is the whole
point of it and cannot be asserted structurally. Reported with the reproduction above.
