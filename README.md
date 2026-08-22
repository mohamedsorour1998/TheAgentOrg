# The Agent Org

**An autonomous CI/CD pipeline where five AI agents write and review the code,
three humans approve it, and a deterministic rule decides whether it ships.**

Open a GitHub issue. Minutes later there is a pull request on your repository —
planned, implemented, reviewed, security-scanned and deployment-checked — with one
comment per stage explaining what each agent did. Three times along the way the
pipeline stops and waits for a person to click Approve.

The part that decides whether a change is *allowed* to ship is not an agent. It is
five lines of Python with no model in it.

Everything runs on AWS and GitHub. There is nothing to install.

---

## Contents

**Understanding it**
- [How it works](#how-it-works) · [Architecture](#architecture) · [The pipeline](#the-pipeline)
- [Why the gatekeeper is not an AI](#why-the-gatekeeper-is-not-an-ai)
- [The five agents](#the-five-agents) · [The human gates](#the-human-gates)

**Using it**
- [Quick start](#quick-start) · [Configuration](#configuration) · [Deploying it](#deploying-it)

**Working on it**
- [Development](#development) · [Repository layout](#repository-layout)
- [Testing philosophy](#testing-philosophy)

**Reference**
- [Design decisions](#design-decisions) · [Limitations](#limitations) · [Team](#team)

---

# Understanding it

## How it works

```
  You open an issue on the target repository
                 │
                 ▼
  A Lambda verifies GitHub's HMAC signature, then publishes to EventBridge
                 │
                 ▼
  EventBridge dispatches a GitHub Actions workflow — seven jobs
                 │
                 ▼
  plan ──▶ [gate1] ──▶ develop ──▶ [gate2] ──▶ sre ──▶ [gate3] ──▶ promote
             human                   human              human
                 │
                 ▼
  Each agent call invokes one of five Bedrock AgentCore runtimes
```

A blocked run stops at `develop` and never reaches the deployment gates. A promoted
run merges the pull request.

## Architecture

Three planes, each doing one job. Nothing is installed on the target repository —
no workflow file, no config, no bot commits.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  INTERFACE — GitHub, where the humans already are                           │
│                                                                             │
│    target repository            this repository                             │
│    ┌──────────────────┐         ┌───────────────────────────────┐           │
│    │ issue  ← plan    │         │ Actions: run-pipeline.yml     │           │
│    │ PR     ← 6 stage │◀────────│ Environments: gate1/2/3       │           │
│    │          comments│         │   (required reviewers)        │           │
│    └──────────────────┘         └───────────────────────────────┘           │
└───────────┬─────────────────────────────────────────────────────────────────┘
            │  issue opened  (webhook, HMAC-signed)
            ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  INGRESS — AWS, turning an issue into a pipeline run                         │
│                                                                             │
│   Lambda Function URL            EventBridge bus + rule       API dest.     │
│   ┌────────────────────┐         ┌──────────────────┐      ┌──────────────┐ │
│   │ verify HMAC-SHA256 │────────▶│ detail-type      │─────▶│ POST …/      │ │
│   │ then PutEvents     │         │  "issues"        │      │  dispatches  │ │
│   │ (nothing else)     │         │ action "opened"  │      └──────┬───────┘ │
│   └────────────────────┘         └──────────────────┘             │         │
│                                           └──▶ DLQ (failed dispatches)      │
└───────────────────────────────────────────────────────────────────┼─────────┘
                                                                    │
            ┌───────────────────────────────────────────────────────┘
            ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  EXECUTION — AWS Bedrock AgentCore, five isolated runtimes                   │
│                                                                             │
│   planner    developer    reviewer    security    sre                       │
│   ┌──────┐   ┌──────┐     ┌──────┐    ┌───────┐   ┌──────┐                  │
│   │ arm64│   │ arm64│     │ arm64│    │ arm64 │   │ arm64│                  │
│   │      │   │      │     │      │    │+3 scan│   │      │                  │
│   └──────┘   └──────┘     └──────┘    └───────┘   └──────┘                  │
│        one container image · five tags · differing only by AGENT_ROLE        │
└─────────────────────────────────────────────────────────────────────────────┘

     supporting:  ECR (5 repos) · DynamoDB (run state) · Secrets Manager
                  IAM via GitHub OIDC — zero static AWS keys
```

## The pipeline

Seven jobs, cut at the gate boundaries.

| Job | What it does |
|---|---|
| `plan` | the planner agent breaks the ticket into tasks; comments on the **issue** |
| `gate1` | pauses — is this the right thing to build? |
| `develop` | developer↔reviewer loop, opens the PR, runs the security verdict |
| `gate2` | pauses — the scanners passed, ship it? |
| `sre` | reads real CI, gives a deployment verdict |
| `gate3` | pauses — final go/no-go |
| `promote` | merges the pull request |

**Why seven jobs and not one script.** A GitHub Environment pauses a *job*, and a
job cannot pause in its middle. Since the gates are Environments, the pipeline must
be cut at the gate boundaries, with the run state handed forward as an artifact.

**Why `develop` contains four things.** The revision loop, the pull request and the
security verdict are all in one job because none of them is a gate boundary — and
the loop iterates an unknown number of times, which Actions cannot express as
"repeat this job until".

**How a blocked run stops.** The security stage exits with code `3`, and `gate2`
declares `needs: develop`. No `if:` condition expresses the block — the dependency
graph does.

**Three extra jobs you will see.** `gate1-rejected`, `gate2-rejected` and
`gate3-rejected` exist because GitHub *skips* a job whose Environment a reviewer
rejected rather than running it with a verdict. Nothing inside a rejected gate
executes, so a refusal must be recorded from a different job.

## Why the gatekeeper is not an AI

Generating code with an LLM is the easy half. Deciding whether to *merge* it is the
half that matters, and it is a poor fit for a model: one that is prompt-injected, or
simply having an off day, will approve a leaked credential and write a confident
paragraph explaining why it is fine.

So the two are split by design:

| | Who does it | Advisory or binding? |
|---|---|---|
| Break the ticket into tasks | planner agent | advisory |
| Write the diff | developer agent | advisory |
| Critique the diff | reviewer agent | advisory — can request changes |
| Explain the security risk in prose | security agent | advisory |
| Judge deployment readiness | sre agent | **advisory** — the verdict itself is code, from real CI |
| **Decide whether it ships** | **`compute_security_verdict()`** | **binding** |

`compute_security_verdict()` lives in `agentorg/state.py`. It sorts findings by
severity, keeps those at or above a threshold, and blocks if any survive. Pure
Python, no network, no model, same answer every time.

It is called in exactly **one** place on the pipeline path — inside the security
agent — so the rule is evaluated once, behind the agent boundary, whether that agent
runs in-process or in its container. The security agent fills
`SecurityResult.explanation` with the model's words. It does not set
`SecurityResult.verdict`.

The same reasoning governs the SRE agent: real CI status decides `go`/`no_go` in
code, and the model contributes only SLO observations and prose. A failing build
always wins over a model's opinion.

### The demonstration

`tickets/clean.md` and `tickets/poisoned.md` are the **same feature request**. They
differ in one respect: the poisoned ticket's reference implementation hardcodes AWS
credentials, so the developer agent's diff carries them.

The clean ticket is planned, developed, reviewed, scanned, SRE-checked and
**promoted**. The poisoned one is **blocked** at the security stage. Showing both is
what separates a pipeline from a wall — a system that blocks everything is not a
gate, it is an outage.

**Each run is a complete record on the issue it came from.** The pull request body
carries `Closes #<n>`, so GitHub links it in the issue's Development sidebar, and when
the run ends the issue receives a verdict comment and closes — `completed` for a
merged change, `not_planned` for a refused one. So the two halves are distinguishable
from the issue list alone, without opening a workflow run:

| | Clean | Poisoned |
|---|---|---|
| Issue | closed, `completed` | closed, `not_planned` |
| Pull request | merged | left open, unmerged |
| Security | `pass`, `provenance: scanners` | `block`, 2 findings, `provenance: scanners` |
| Pipeline | seven jobs green | `develop` exits **3**, everything after skipped |

> The credential in the poisoned ticket is `AKIAIOSFODNN7EXAMPLE`, AWS's own
> published documentation placeholder. Nothing sensitive is in this repository.

## The five agents

Each runs in its own Bedrock AgentCore runtime. All five run the **same container
image** with a different `AGENT_ROLE` — five images would multiply build time by
five for no difference in content, and leave five Dockerfiles to drift apart.

| Agent | Reads | Produces |
|---|---|---|
| **planner** | the ticket text | tasks, acceptance criteria, target files |
| **developer** | the plan, and the reviewer's last critique | a branch name and a unified diff |
| **reviewer** | the diff, and the repo **as the diff would leave it** | `approve` or `changes_requested`, with line comments |
| **security** | the diff | findings from three real scanners, and the binding verdict |
| **sre** | the run, plus the target repo's real CI status | `go` or `no_go`, SLO checks |

**All five read the target repository.** Each run shallow-clones it and every agent
sees the same files, briefly cached so one run's agents agree with each other and a
later run still picks up a merge. Before this, an agent reasoned about the repository
from its *name*: asked to add rate limiting to a Python Flask app, the developer agent
wrote Go — `sync.RWMutex`, `NewRateLimiter` — four revisions running, because nothing
in its prompt said what the file it was patching contained. The reviewer gets the
repository with the diff already applied, so it judges the result rather than applying
a patch in its head.

**Every agent degrades to a fixture rather than failing.** If a model call fails,
the agent loads a validated sample from `fixtures/` and the pipeline completes. That
is deliberate — a demo that dies on a transient Bedrock error is worse than one that
completes with a recorded caveat — but it creates the risk this project spends the
most effort on: a stage that *looks* like it ran. Two fields exist to tell the
difference: `scan_provenance` for the scanners and `model_provenance` for the agents.

**The revision loop.** If the reviewer requests changes, the developer produces a
fresh diff and the reviewer looks again, up to `MAX_REVISION_LOOPS` (default 3). A
run that exhausts the cap without approval ends `failed`, not `promoted` — the
scanners may have cleared the diff, but nobody approved it.

## The human gates

All three gates are **GitHub Environments with required reviewers**. This is the one
design choice everything else bends around, and the reason is not convenience: an
Environment's required reviewer is a *repository setting*. No edit to a workflow
file, and no workflow input, can approve a gate on a human's behalf.

A gate implemented as `if: inputs.auto_approve != true` would be skippable — and a
skipped job is indistinguishable downstream from an approved one.

An Environment **with no required reviewer does not pause — it runs.** If your gates
are not stopping, that is the first thing to check.

---

# Using it

## Quick start

**Opening an issue is the entire interface.**

```
1.  Open an issue on the target repository describing what you want built.

2.  Within seconds a run appears in Actions and the planner's breakdown is
    posted as a comment on your issue.

3.  The run pauses at gate1. Review the plan, click Approve.

4.  Developer and reviewer agents work; a pull request opens; the security
    stage runs gitleaks, trivy and semgrep against the diff.

       blocked → the run stops. gate2 is never reached, and the PR carries
                 every finding with its file and line number.
       clean   → the run pauses at gate2 for you.

5.  The SRE agent reads real CI and gives a verdict, gate3 asks once more,
    and the pull request is merged.
```

Write the issue specifically enough that the developer agent can satisfy it. The
reviewer is a real model and it withholds approval when the diff does not match what
it asked for — see [Limitations](#limitations).

To run a ticket without opening an issue — a rehearsal, or a re-run after a fix:

```bash
gh workflow run run-pipeline.yml \
  -f ticket_id=<issue number> \
  -f ticket_text="Add a per-IP login rate limit." \
  -f poisoned=false
```

`ticket_id` **must be the bare issue number.** Anything else is refused rather than
guessed at, because a loose parse would post the agents' comments onto whichever
issue happened to match.

## Configuration

Every knob lives in `agentorg/common/config.py`, each with a comment explaining why
its default is what it is.

| Variable | Default | Effect |
|---|---|---|
| `REMOTE_AGENTS` | `false` | `true` routes each agent call to its AgentCore runtime |
| `SCANNERS_REQUIRED` | `false` | `true` promotes a missing scanner from *affordance* to *fault* |
| `SECURITY_BLOCK_THRESHOLD` | `high` | severity at or above which a finding blocks |
| `MAX_REVISION_LOOPS` | `3` | cap on the developer↔reviewer loop |
| `SCANNER_TIMEOUT_SECONDS` | `120` | per scanner invocation, not per suite |
| `OFFLINE` | `false` | `true` makes the GitHub seam use plain `git` instead of the API |
| `LLM_DISABLED` | `false` | `true` forces every agent onto its fixture |
| `STATE_BACKEND` | `local` | `local` (JSONL) or `dynamodb`. An unknown value raises at import |
| `DEMO_REPO` | — | the target repository, `owner/name` |

Two defaults are load-bearing rather than arbitrary.

**`SCANNERS_REQUIRED=false`** distinguishes a scanner that is *absent* from one that
is *broken* — different faults deserving different answers. Set it `true` only on
the security runtime, the one image carrying the three binaries. Set anywhere else
it blocks the clean run too.

**`REMOTE_AGENTS=false`** keeps the fallback path and the tested path the same one.
Every automated test runs through the same seam the cloud path uses, so if the
runtimes misbehave, unsetting one variable returns the pipeline to a path that has
been exercised continuously.

`OFFLINE` closes the GitHub seam **only** — it does not disable the model. For a
model-free run set both `OFFLINE=true` and `LLM_DISABLED=true`.

Every boolean parses `== "true"` case-insensitively, never
`bool(os.environ.get(...))`. `bool("false")` is `True`, and that mistake would run
the poisoned diff on a run somebody asked to be clean.

## Deploying it

All infrastructure is Terraform under `infra/Terraform/`. Nothing is created by hand
in the console.

| Workflow | Trigger | What it does |
|---|---|---|
| `ci.yml` | every push and PR | lint, test, and real scanners over both tickets |
| `terraform.yml` | changes under `infra/`, or manual | `fmt` → `validate` → `plan` → `apply` on main |
| `deploy.yml` | changes under `agentorg/`, or manual | build the arm64 image, push to ECR, update the five runtimes |
| `run-pipeline.yml` | dispatch (by EventBridge or by hand) | the pipeline itself |

**Zero static AWS keys.** Every AWS step assumes an IAM role through GitHub's OIDC
provider. There is no `AWS_ACCESS_KEY_ID` secret in this repository, and adding one
would defeat the point.

### Standing it up fresh

1. **`terraform.yml`** — creates the event bus, the ingress Lambda, five ECR
   repositories, the DynamoDB table and every IAM role.
2. **`deploy.yml`** — builds the image and creates the five AgentCore runtimes.
   `SCANNERS_REQUIRED=true` is set on the security runtime and nowhere else.
3. **Three Environments** — `gate1`, `gate2`, `gate3`, each with a required
   reviewer. Without a reviewer they do not pause.
4. **A GitHub App** subscribed to Issues, its webhook pointed at the Lambda's
   Function URL, its HMAC secret in Secrets Manager. `Issues: read-only` suffices —
   the App only delivers events.
5. **One token, two repositories.** `actions: write` on *this* repository to dispatch
   the workflow, and `contents` + `issues` + `pull requests` write on the *target*
   repository to open and merge the PR. Scoped to only one, the other half fails
   silently.
6. **`DEMO_REPO`** repository variable → the target repository. Pointing at a
   different repository is a settings change, not a commit.

Run `scripts/preflight.py` before relying on a deployment. It checks four things
whose wrong answers have each happened at least once: the model is actually
invokable, all five runtimes are READY at the same version, the security container
returns *real* scanner line numbers, and each gate has a required reviewer.

---

# Working on it

## Development

The pipeline runs in the cloud. The test suite does not need to, and deliberately
cannot reach anything: every agent falls back to a validated fixture when no model
answers, so the whole suite runs with no AWS account, no GitHub token and no
scanners installed.

```bash
pip install -e ".[dev]"

pytest -q                              # the full suite
ruff check agentorg scripts tests      # ruff 0.16 defaults, unconfigured
actionlint .github/workflows/*.yml     # shellcheck over every run: block
terraform fmt -check -recursive        # in infra/Terraform
```

All four must pass before a commit.

`fixtures/` is what made parallel development possible: it holds a sample of every
result shape in the system, so one person's work could load a teammate's fixture
instead of waiting for their code.

## Repository layout

```
agentorg/
├── state.py                  the FROZEN data contract + compute_security_verdict
├── graph.py                  the pipeline as a single walk (the in-process path)
├── gates.py                  human gates: save · pause · resume · load
├── github_ops.py             the GitHub seam — API online, plain git offline
├── repo_snapshot.py          the target repo, cloned once and read by all five agents
├── log.py, timeline.py       append-only decision log, and its renderer
├── gates_cli.py              approve or reject a gate from a terminal
├── approve_server.py         a minimal local approval screen over gates.resume
├── agents/                   the five agents + server.py (HTTP) + Dockerfile
├── security/                 gitleaks / trivy / semgrep wrappers + rule files
└── common/
    ├── config.py             every knob, with reasoning
    ├── agent_client.py       the one seam: in-process vs invoke_agent_runtime
    ├── llm.py                Bedrock, with a fixture fallback
    └── diff.py               what a unified diff PROPOSES (added lines only)

infra/
├── Terraform/                all infrastructure, as modules
└── ingress/handler.py        the webhook Lambda

scripts/
├── run_stage.py              one pipeline stage as one Actions job
├── scan_gate.py              real scanners over both tickets — CI's scan job
└── preflight.py              is the deployed pipeline actually real?

.github/workflows/            ci · terraform · deploy · run-pipeline
fixtures/                     a validated sample of every result shape
tickets/                      clean.md and poisoned.md — the same request
target_repo/                  the demo's subject application
tests/                        the suite
docs/                         engineering notes, per-person plans, the demo runbook
```

`agentorg/state.py` is **frozen**: add optional fields, never rename or remove one.
Every part of the system reads it, so a rename breaks everything at once and nothing
notices until integration.

`agentorg/common/diff.py` is worth knowing about. Scanners rebuild the changed files
from a diff's **added lines only**, so "the change contains X" can only mean "an
added line contains X". Searching the whole diff string would match a credential the
developer had just *removed*.

## Testing philosophy

The suite is large because of one recurring defect:

> **A check that cannot distinguish "did not run" from "passed" is worse than no
> check at all.**

Three practices follow.

**Every test change carries a mandatory RED step.** Name the exact mutation, apply
it, watch the named test fail, revert. Many assertions in this repository turned out
to pin nothing at all — and a test that cannot fail is worse than no test, because it
also stops anyone from looking.

**Numbers in prose come from a command whose output is pasted.** A measurement is a
number *plus its conditions and spread*, or it is not quoted.

**Test doubles must be able to express the failing case.** A stub that could only
emit valid JSON made it impossible to test a malformed response, leaving three
refusal paths uncovered. A helper that blanks heredocs was used to test a heredoc, so
the test searched text from which its own subject had been erased, matched nothing,
and passed green.

`tests/conftest.py` carries five autouse guards that force the offline path and then
put a loud raiser on the seam underneath — Bedrock, GitHub, the working tree,
`input()`, and the scanner cache. Each raises through `pytest.fail`, whose exception
derives from `BaseException` rather than `Exception`, because the code under test
catches `Exception` and would otherwise swallow the guard into its fixture branch and
pass green while making live billable API calls.

Lint runs ruff's own defaults. There is no `[tool.ruff]` section, no `# noqa`, and no
per-file ignores.

### How we know the scanners really ran

The security container runs gitleaks, trivy and semgrep. But a fixture block and a
real scanner block produce the **same** verdict, the same finding count, the same
rule names, the same file, the same tool and the same severity.

Exactly one field tells them apart:

```
verdict: block   blocking: 2   files: ['app/auth.py']
LINES: [3, 4]        ← real scanners       (the fixture reports [4, 5])
provenance: scanners
```

So no test here may claim "the scanners ran" from a count. It is asserted from the
line-number **set**, or from `scan_provenance`, which is stamped at the call site
because inferring it afterwards is impossible. The two sets overlap at line 4 — so no
single finding separates the modes, only the whole set does.

`scan_provenance` has three values, and the last two are deliberately distinct:

| Value | Meaning |
|---|---|
| `scanners` | a real scan produced this verdict |
| `fixture-fallback` | a scanner raised and the fixture stood in — a **fault** |
| `fixture-stub` | nobody asked for a scan — a **choice** |

Collapsing the last two would hide a broken gate behind a demo setting.
`model_provenance` answers the same question for the agents: `model`, `fixture`, or
`mixed` when a run used both.

---

# Reference

## Design decisions

Four questions this architecture invites — including the one where the answer is
unflattering.

### Why not put the pipeline logic in the Lambda?

**The human gates.** An Environment pauses a job, and a Lambda cannot pause for a
human at all: a 15-minute hard ceiling against a gate that may sit for hours. Step
Functions *could* wait, but then the approval interface has to be built from scratch
— and the entire appeal of Environments is that the approval surface is the one
reviewers already use, backed by a repository setting no code can override.

**And visibility.** The surface people judge a change on is the repository: the
issue, the PR, the comments, the paused run. A Lambda's execution lives in CloudWatch
Logs, which is not where anyone reviews code.

### Does EventBridge earn its place? Barely.

It buys three things: a dead-letter queue, a retry on the dispatch call, and a bus
other consumers could subscribe to later without touching the Lambda.

That is the complete list. **The Lambda could call the workflow-dispatch REST
endpoint directly and the system would behave identically.** If you looked at the
diagram and wondered why that hop is there, the honest answer is that it is one layer
of indirection buying a DLQ and a retry policy that a few dozen lines of Python could
also provide.

It did earn its keep once, as a debugging surface. A dispatch failed while both
GitHub and the Lambda reported success, and the dead-letter message was the only
place the real cause appeared:

```
ApiDestination returned HTTP status 403
{"message":"Resource not accessible by personal access token"}
x-accepted-github-permissions: actions=write
```

This project's signature failure shape — every component reporting success while the
thing did not happen — caught by the component that was hardest to justify.

### Then why a Lambda at all, rather than pointing GitHub at EventBridge?

Because that does not exist. The `aws events` API has no inbound-webhook operation:
`create-api-destination` and `create-connection` are *outbound*, and
`create-partner-event-source` requires an onboarded SaaS partner, which GitHub is
not. Something has to terminate the HTTPS POST and verify GitHub's
`X-Hub-Signature-256`, and that is the Lambda's entire job — verify, then publish.

Nothing before the signature check succeeds may cost money, mutate anything, or
publish. A handler that publishes and *then* returns 401 has already started the
pipeline while telling the caller it refused.

**Security note, stated rather than buried.** The Function URL is
`authorization_type = "NONE"`, because GitHub cannot sign a SigV4 request and
`AWS_IAM` would reject every delivery. The endpoint is therefore internet-reachable
and unauthenticated at the AWS layer, and the HMAC is the only access control in the
path. What limits the damage is scope, not authentication: the function's IAM role
holds two actions on two specific ARNs, and reserved concurrency caps what an
anonymous flood can spend.

### Why arm64?

Because AgentCore runs arm64. An amd64 image builds, pushes, deploys, and then fails
to start with an error that reads like a broken entrypoint.

## Limitations

Coursework built by five people over three weeks. A working system, not a product:
no multi-tenancy, no cost accounting, and the agents' prompts are tuned for one
family of tickets.

- **A vague ticket may legitimately end `failed` rather than promoted.** The reviewer
  is a real model and it withholds approval when the diff does not match what it
  asked for — asked to rate-limit *per email address*, a developer agent produced
  *per-IP* limiting four times, the revision cap expired, and the run ended `failed`
  with the scanners reporting `PASS`. That is the pipeline working; nobody approved
  the change. It does mean a demo ticket must be specific.
- **The reviewer's verdict is advisory by design.** A change nobody approved should
  not ship, and promoting on a scanner pass alone would make the reviewer
  decorative. Such a run renders `✗ FAILED`, which distinguishes "the reviewer never
  approved" from "the security rule stopped it".
- **Reported line numbers are indices into the added-lines-only file**, not the real
  file: a finding at `app/auth.py:3` means "the third added line". Not currently
  fixed, because correcting it would collapse the discriminator described above —
  the offset and the pinned line numbers must move together.
- **`STATE_BACKEND=dynamodb` is untested on the cloud path.** The table and IAM
  exist and the code reads through a backend-agnostic seam, but the cloud path runs
  on the `local` default with an artifact handoff between jobs.
- **The SRE reports `CI unknown` and still says `go`.** GitHub answers `pending` with
  zero checks when nothing has run, which reads as `unknown` rather than `passing` —
  deliberately, since a green CI line for a repository that never ran a test would be
  a fabricated fact. `unknown` proceeds and the honest value reaches the PR comment;
  only `failing` produces `no_go`. Whether `unknown` should block a *merge* is
  `merge_pr`'s decision, made there rather than smuggled into this verdict.
- **All three gate Environments have `can_admins_bypass: true`.** A repository admin
  can push a gate through without a reviewer clicking, so the honest answer to "can
  a gate be skipped?" is yes, by an admin. An operator setting rather than a code
  path; `scripts/preflight.py` reports it on every run.
- **`agentorg/approve_server.py` has no authentication** and binds `127.0.0.1` only.
  A local convenience over `gates.resume`, superseded by GitHub Environments, and it
  must never be exposed off-host.

[`docs/engineering-notes.md`](docs/engineering-notes.md) records the defects found
building this and why several non-obvious decisions are the way they are. Worth
reading before changing the security path or the IAM policies.

## Team

Built by **RosettaTeam** — Sorour, Mariam, Habiba, Reem and Aya.

| Area | Owner |
|---|---|
| `infra/`, the pipeline core, `agentorg/common/`, `agentorg/agents/` | Sorour |
| `agentorg/github_ops.py`, `.github/workflows/`, `scripts/scan_gate.py` | Mariam |
| `agentorg/security/` — the gitleaks / trivy / semgrep wrappers | Habiba |
| `target_repo/`, `tickets/`, the functional and baseline tests | Reem |
| Block-determinism, chaos and DORA suites, `tests/provenance.py` | Aya |

Per-person plans are in [`docs/plan/`](docs/plan/) — start with
[`00-timeline.md`](docs/plan/00-timeline.md). The demo runbook is
[`docs/plan/reem/demo_script.md`](docs/plan/reem/demo_script.md). Notes for AI
coding sessions are in [`CLAUDE.md`](CLAUDE.md).
