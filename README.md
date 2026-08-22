# The Agent Org

**An autonomous CI/CD pipeline where five AI agents write and review the code, three humans approve it, and a deterministic rule decides whether it ships.**

You open a GitHub issue. Minutes later there is a pull request on your repository —
planned, implemented, reviewed, security-scanned and deployment-checked — with one
comment per stage explaining what each agent did. Three times along the way the
pipeline stops and waits for a person to click Approve.

The part that decides whether a change is *allowed* to ship is not an agent. It is
five lines of Python with no model in it.

Everything runs on AWS and GitHub. There is nothing to install and nothing to run
on your machine.

---

## Contents

- [Architecture](#architecture)
- [The flow, end to end](#the-flow-end-to-end)
- [Why the gatekeeper is not an AI](#why-the-gatekeeper-is-not-an-ai)
- [The five agents](#the-five-agents)
- [The human gates](#the-human-gates)
- [Using it](#using-it)
- [Deploying it](#deploying-it)
- [Configuration](#configuration)
- [Repository layout](#repository-layout)
- [How we know the scanners really ran](#how-we-know-the-scanners-really-ran)
- [Design decisions](#design-decisions)
- [Development](#development)
- [Status and limitations](#status-and-limitations)
- [Team](#team)

---

## Architecture

Three planes, each doing one job. Nothing is installed on the target repository —
no workflow file, no config, no bot commits.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  INTERFACE PLANE — GitHub, where the humans already are                     │
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
│  INGRESS PLANE — AWS, turning an issue into a pipeline run                   │
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
│  EXECUTION PLANE — AWS Bedrock AgentCore, five isolated runtimes             │
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

---

## The flow, end to end

```
  ①  You open an issue on the target repository
     "Add a per-IP login rate limit of five attempts per minute."
                 │
                 ▼
  ②  Lambda verifies the HMAC signature over the raw body, then publishes.
     Nothing costs money, mutates, or publishes before the signature checks out.
                 │
                 ▼
  ③  EventBridge rule matches, API destination dispatches run-pipeline.yml
     with the issue's number and title as inputs.
                 │
                 ▼
  ┌──────────────────────────────────────────────────────────────────────────┐
  │                                                                          │
  │   plan ──▶ [gate1] ──▶ develop ──▶ [gate2] ──▶ sre ──▶ [gate3] ──▶ promote│
  │    │        HUMAN        │          HUMAN       │       HUMAN        │    │
  │    │                     │                      │                   │    │
  │  planner               developer               sre              status=  │
  │  agent                 reviewer (loop)         agent          promoted   │
  │    │                   open PR                  │                        │
  │    │                   security ──┐             │                        │
  │    ▼                              │             ▼                        │
  │  comment               ┌──────────┴──────┐   comment                     │
  │  on the ISSUE          │ verdict = block │   on the PR                   │
  │                        │  status=blocked │                               │
  │                        │  RUN ENDS  ⛔   │                               │
  │                        └─────────────────┘                               │
  └──────────────────────────────────────────────────────────────────────────┘
                 │
                 ▼
  ④  Seven jobs, each one stage. State is handed along as an artifact and
     every stage posts its output to the issue or the pull request.
```

**Why seven jobs and not one script.** A GitHub Environment pauses a *job*, and a
job cannot pause in its middle. Since the three gates are Environments, the
pipeline has to be cut at the gate boundaries — one job per segment, with the run
state handed forward.

**Why `develop` contains four things.** The developer↔reviewer revision loop, the
pull request, and the security verdict all live in one job because none of them is
a gate boundary — and the revision loop in particular iterates an unknown number
of times, which Actions cannot express as "repeat this job until".

**How a blocked run stops.** The security stage exits with code `3`. `gate2`
declares `needs: develop`, so it never starts. No `if:` condition expresses the
block — the dependency graph does.

**Three extra jobs you will see.** `gate1-rejected`, `gate2-rejected` and
`gate3-rejected` exist because GitHub *skips* a job whose Environment a reviewer
rejected rather than running it with a verdict. Nothing inside a rejected gate job
executes, so a refusal has to be recorded from a different job — otherwise a
refused run and an in-flight run look identical afterwards.

---

## Why the gatekeeper is not an AI

Generating code with an LLM is the easy half. Deciding whether to *merge* it is
the half that matters, and it is a poor fit for a model: one that is
prompt-injected, or simply having an off day, will approve a leaked credential and
write a confident paragraph explaining why it is fine.

So the two are split by design:

| | Who does it | Advisory or binding? |
|---|---|---|
| Break the ticket into tasks | planner agent | advisory |
| Write the diff | developer agent | advisory |
| Critique the diff | reviewer agent | advisory — can request changes |
| Explain the security risk in prose | security agent | advisory |
| Judge deployment readiness | sre agent | advisory |
| **Decide whether it ships** | **`compute_security_verdict()`** | **binding** |

`compute_security_verdict()` lives in `agentorg/state.py`. It sorts findings by
severity, keeps those at or above a threshold, and blocks if any survive. Pure
Python, no network, no model, same answer every time.

It is called in exactly **one** place on the pipeline path — inside the security
agent, at `agentorg/agents/security.py:187` — so the rule is evaluated once,
behind the agent boundary, whether that agent runs in-process or in its container.
The security agent fills `SecurityResult.explanation` with the model's words. It
does not set `SecurityResult.verdict`.

### The demonstration

`tickets/clean.md` and `tickets/poisoned.md` are the **same feature request**. They
differ in one respect: the poisoned ticket's reference implementation hardcodes AWS
credentials, so the developer agent's diff carries them.

The clean ticket is planned, developed, reviewed, scanned, SRE-checked and
**promoted**. The poisoned one is **blocked** at the security stage and never
reaches the deployment gates. Showing both is what separates a pipeline from a
wall — a system that blocks everything is not a gate, it is an outage.

> The credential in the poisoned ticket is `AKIAIOSFODNN7EXAMPLE`, AWS's own
> published documentation placeholder. Nothing sensitive is in this repository.

---

## The five agents

Each runs in its own Bedrock AgentCore runtime. All five run the **same container
image** with a different `AGENT_ROLE` — five images would multiply build time by
five for no difference in content, and leave five Dockerfiles to drift apart.

| Agent | Reads | Produces |
|---|---|---|
| **planner** | the ticket text | tasks, acceptance criteria, target files |
| **developer** | the plan, and the reviewer's last critique | a branch name and a unified diff |
| **reviewer** | the diff | `approve` or `changes_requested`, with line comments |
| **security** | the diff | findings from three real scanners, and the binding verdict |
| **sre** | the whole run | `go` or `no_go`, CI status, SLO checks |

**Every agent degrades to a fixture rather than failing.** If a model call fails,
the agent loads a validated sample result from `fixtures/` and the pipeline
completes. That is deliberate — a demo that dies on a transient Bedrock error is
worse than one that completes with a recorded caveat — but it creates the risk this
project spends the most effort on: a stage that *looks* like it ran. See
[how we know the scanners really ran](#how-we-know-the-scanners-really-ran).

**The revision loop.** If the reviewer requests changes, the developer produces a
fresh diff and the reviewer looks again, up to `MAX_REVISION_LOOPS` (default 3). A
run that exhausts the cap without approval ends as `failed`, not `promoted` — the
scanners may have cleared the diff, but nobody approved it.

---

## The human gates

All three gates are **GitHub Environments with required reviewers**. This is the
one design choice everything else bends around, and it was chosen for a reason
that is not convenience: an Environment's required reviewer is a *repository
setting*. No edit to a workflow file, and no workflow input, can approve a gate on
a human's behalf.

A gate implemented as `if: inputs.auto_approve != true` would be skippable — and a
skipped job is indistinguishable downstream from an approved one.

| Gate | Sits after | The question it asks |
|---|---|---|
| `gate1` | plan | Is this the right thing to build? |
| `gate2` | develop + review + security | The scanners passed. Ship it? |
| `gate3` | sre | Final go/no-go for deployment. |

An Environment **with no required reviewer does not pause — it runs.** If your
gates are not stopping, that is the first thing to check.

---

## Using it

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

5.  The sre agent gives a deployment verdict, gate3 asks once more, and the
    change is promoted.
```

To run a ticket without opening an issue — a rehearsal, or a re-run after a fix:

```bash
gh workflow run run-pipeline.yml \
  -f ticket_id=<issue number> \
  -f ticket_text="Add a per-IP login rate limit." \
  -f poisoned=false
```

`ticket_id` **must be the bare issue number.** Anything else is refused rather
than guessed at, because a loose parse would post the agents' comments onto
whichever issue happened to match.

---

## Deploying it

All infrastructure is Terraform under `infra/Terraform/`. Nothing is created by
hand in the console. Four workflows do everything:

| Workflow | Trigger | What it does |
|---|---|---|
| `ci.yml` | every push and PR | lint, test, and real scanners over both tickets |
| `terraform.yml` | changes under `infra/`, or manual | `fmt` → `validate` → `plan` → `apply` on main |
| `deploy.yml` | changes under `agentorg/`, or manual | build the arm64 image, push to ECR, create or update the five runtimes |
| `run-pipeline.yml` | dispatch (by EventBridge or by hand) | the pipeline itself |

**Zero static AWS keys.** Every AWS step assumes an IAM role through GitHub's OIDC
provider. There is no `AWS_ACCESS_KEY_ID` secret in this repository, and adding one
would defeat the point of the setup.

### Standing it up fresh

1. **`terraform.yml`** — creates the event bus, the ingress Lambda, five ECR
   repositories, the DynamoDB table, and every IAM role.
2. **`deploy.yml`** — builds the image and creates the five AgentCore runtimes.
   `SCANNERS_REQUIRED=true` is set on the security runtime and nowhere else.
3. **Three Environments** — `gate1`, `gate2`, `gate3`, each with a required
   reviewer. Without a reviewer they do not pause.
4. **A GitHub App** subscribed to Issues, its webhook pointed at the Lambda's
   Function URL, its HMAC secret stored in Secrets Manager. `Issues: read-only` is
   sufficient — the App only delivers events.
5. **One token, two repositories.** `actions: write` on *this* repository to
   dispatch the workflow, and `contents` + `issues` + `pull requests` write on the
   *target* repository to open the PR and comment. Scoped to only one, the other
   half fails silently.
6. **`DEMO_REPO`** repository variable → the target repository, as `owner/name`.
   Pointing at a different repository is a settings change, not a commit.

---

## Configuration

Every knob lives in `agentorg/common/config.py`, each with a comment recording why
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

**`SCANNERS_REQUIRED=false`** distinguishes a scanner that is *absent* from one
that is *broken* — different faults deserving different answers. Set it `true` only
on the security runtime, the one image that actually carries the three binaries.
Set anywhere else, it blocks the clean run too, with three `*-scanner-error`
findings.

**`REMOTE_AGENTS=false`** keeps the fallback path and the tested path the same
one. Every automated test runs through the same seam the cloud path uses, so if the
runtimes misbehave, unsetting one variable returns the pipeline to a path that has
been exercised continuously.

`OFFLINE` closes the GitHub seam **only** — it does not disable the model. For a
genuinely model-free run, set both `OFFLINE=true` and `LLM_DISABLED=true`.

Every boolean parses `== "true"` case-insensitively, never
`bool(os.environ.get(...))`. `bool("false")` is `True`, and that mistake would run
the poisoned diff on a run somebody asked to be clean, with nothing anywhere
saying so.

---

## Repository layout

```
agentorg/
├── state.py                  the FROZEN data contract + compute_security_verdict
├── graph.py                  the pipeline as a single walk (the in-process path)
├── gates.py                  human gates: save · pause · resume · load
├── github_ops.py             the GitHub seam — API online, plain git offline
├── log.py                    the append-only decision log
├── timeline.py               renders a run as a human-readable timeline
├── gates_cli.py              approve or reject a gate from a terminal
├── approve_server.py         a minimal approval screen over gates.resume
├── fixtures_loader.py        resolves fixtures/ from the repository root
├── agents/
│   ├── planner · developer · reviewer · security · sre
│   ├── server.py             the HTTP contract AgentCore invokes
│   ├── Dockerfile            one arm64 image for all five
│   └── requirements.txt      pinned, deliberately
├── security/
│   ├── gitleaks_tool · semgrep_tool · trivy_tool
│   ├── gitleaks.toml · semgrep_rules.yml
│   └── _run.py               the subprocess wrapper, with the timeout
└── common/
    ├── config.py             every knob, with its reasoning
    ├── agent_client.py       the one seam: in-process vs invoke_agent_runtime
    ├── llm.py                Bedrock, with a fixture fallback
    ├── model.py              model provider selection
    ├── diff.py               what a unified diff PROPOSES (added lines only)
    ├── validation.py         task input bounds
    └── health.py             readiness route

infra/
├── Terraform/
│   ├── environments/shared/  the root module
│   └── modules/              agentcore · ingress · state
└── ingress/handler.py        the webhook Lambda, outside agentorg/ on purpose

scripts/
├── run_stage.py              one pipeline stage as one Actions job
├── scan_gate.py              real scanners over both tickets — CI's scan job
└── bedrock_smoke_test.py     is the model reachable at all?

.github/workflows/            ci · terraform · deploy · run-pipeline
fixtures/                     a validated sample of every result shape
tickets/                      clean.md and poisoned.md — the same request
target_repo/                  the demo's subject application
tests/                        816 tests
docs/plan/                    per-person plans and the demo runbook
```

`agentorg/state.py` is **frozen**: add optional fields, never rename or remove
one. Every part of the system reads it, so a rename breaks everything at once and
nothing notices until integration.

`agentorg/common/diff.py` is worth knowing about. Scanners rebuild the changed
files from a diff's **added lines only**, so "the change contains X" can only mean
"an added line contains X". Searching the whole diff string would match a
credential the developer had just *removed*.

---

## How we know the scanners really ran

The signature defect this project exists to prevent is **a check that reports
green because it never ran.** The security container genuinely runs gitleaks,
trivy and semgrep — but a fixture block and a real block produce the *same*
verdict, the *same* finding count, the *same* rule names, the *same* file, the
*same* tool and the *same* severity. The fixture's explanation names a real file
and a real remediation and reads exactly like real output.

Exactly one field separates them:

```
verdict: block   blocking: 2   files: ['app/auth.py']
LINES: [3, 4]        ← real scanners        (the fixture reports [4, 5])
provenance: scanners
```

So `blocking=2` proves nothing on its own, and no test in this repository is
permitted to claim "the scanners ran" from a count. It is asserted from the
line-number **set**, or from `scan_provenance`, which is stamped at the call site
because inferring it afterwards is impossible. The two sets overlap at line 4 — so
no single finding separates the modes, only the whole set does.

`scan_provenance` has three values, and the last two are deliberately kept apart:

| Value | Meaning |
|---|---|
| `scanners` | a real scan produced this verdict |
| `fixture-fallback` | a scanner raised and the fixture stood in — a **fault** |
| `fixture-stub` | nobody asked for a scan — a **choice** |

Collapsing the last two would hide a broken gate behind a demo setting.

---

## Design decisions

Four questions this architecture invites — including the one where the answer is
unflattering.

### Why not put the pipeline logic in the Lambda?

**The human gates.** An Environment pauses a job, and a Lambda cannot pause for a
human at all: a 15-minute hard ceiling against a gate that may sit for hours. Step
Functions *could* wait, but then the approval interface has to be built from
scratch — and the entire appeal of Environments is that the approval surface is
the one reviewers already use, backed by a repository setting no code can override.

**And visibility.** The surface people judge a change on is the repository: the
issue, the PR, the comments, the paused run. A Lambda's execution lives in
CloudWatch Logs, which is not where anyone reviews code.

### Does EventBridge earn its place? Barely.

It buys three things: a dead-letter queue, a retry on the dispatch call, and a bus
other consumers could subscribe to later without touching the Lambda.

That is the complete list. **The Lambda could call the workflow-dispatch REST
endpoint directly and the system would behave identically.** If you looked at the
diagram and wondered why that hop is there, the honest answer is that it is one
layer of indirection buying a DLQ and a retry policy that a few dozen lines of
Python could also provide.

It did earn its keep once, and specifically as a debugging surface. A dispatch
failed while both GitHub and the Lambda reported success — and the dead-letter
message was the only place the real cause appeared:

```
ApiDestination returned HTTP status 403
{"message":"Resource not accessible by personal access token"}
x-accepted-github-permissions: actions=write
```

Which is this project's signature failure shape — every component reporting
success while the thing did not happen — caught by the component that was hardest
to justify.

### Then why a Lambda at all, rather than pointing GitHub at EventBridge?

Because that does not exist. The `aws events` API has no inbound-webhook
operation: `create-api-destination` and `create-connection` are *outbound*, and
`create-partner-event-source` requires an onboarded SaaS partner, which GitHub is
not. Something has to terminate the HTTPS POST and verify GitHub's
`X-Hub-Signature-256`, and that is the Lambda's entire job — verify, then publish.

Nothing before the signature check succeeds may cost money, mutate anything, or
publish. A handler that publishes and *then* returns 401 has already started the
pipeline while telling the caller it refused.

**Security note, stated rather than buried.** The Function URL is
`authorization_type = "NONE"`, because GitHub cannot sign a SigV4 request and
`AWS_IAM` would reject every delivery. The endpoint is therefore
internet-reachable and unauthenticated at the AWS layer, and the HMAC is the only
access control in the path. What limits the damage is scope, not authentication:
the function's IAM role holds two actions on two specific ARNs, and reserved
concurrency caps what an anonymous flood can spend.

### Why arm64?

Because AgentCore runs arm64. An amd64 image builds, pushes, deploys, and then
fails to start with an error that reads like a broken entrypoint.

---

## Development

The pipeline runs in the cloud. The test suite does not need to, and deliberately
cannot reach anything: every agent falls back to a validated fixture when no model
answers, so the whole suite runs with no AWS account, no GitHub token and no
scanners installed.

```bash
pip install -e ".[dev]"

pytest -q                              # 816 passed, 3 skipped
ruff check agentorg scripts tests      # ruff 0.16 defaults, unconfigured
actionlint .github/workflows/*.yml     # shellcheck over every run: block
terraform fmt -check -recursive        # in infra/Terraform
```

`fixtures/` is what made parallel development possible: it holds a sample of every
result shape in the system, so one person's work could load a teammate's fixture
instead of waiting for their code.

### The testing rules, which are not optional here

**Every test change carries a mandatory RED step.** Name the exact mutation, apply
it, watch the named test fail, revert. Nineteen-plus assertions in this repository
turned out to pin nothing at all — and a test that cannot fail is worse than no
test, because it also stops anyone from looking.

**Numbers in prose come from a command whose output is pasted.** A suite wall time
once committed as "measured" could not be reproduced: 102.83 s, 116.88 s and
149.68 s for the same snapshot on one machine in one day. A measurement is a number
*plus its conditions and spread*, or it is not quoted.

**Test doubles must be able to express the failing case.** Found seven times
across four layers:

> A test double, a helper, an inference, or a measurement that cannot express the
> failing case produces confidence that cannot be falsified — and reading it never
> reveals that.

A stub that could only emit valid JSON made it impossible to test a malformed
response, leaving three refusal paths uncovered. A helper that blanks heredocs was
used to test a heredoc, so the test searched text from which its own subject had
been erased, matched nothing, and passed green.

`tests/conftest.py` carries four autouse guards that force the offline path and
then put a loud raiser on the seam underneath — Bedrock, GitHub, the working tree,
and `input()`. Each raises through `pytest.fail`, whose exception derives from
`BaseException` rather than `Exception`, because the code under test catches
`Exception` and would otherwise swallow the guard into its fixture branch and pass
green while making live billable API calls.

Lint runs ruff's own defaults. There is no `[tool.ruff]` section in
`pyproject.toml`, no `# noqa`, and no per-file ignores.

---

## Status and limitations

Coursework built by five people over three weeks and demonstrated live. A working
system, not a product: no multi-tenancy, no cost accounting, and the agents'
prompts are tuned for one family of tickets.

**Verified on deployed infrastructure:** five AgentCore runtimes serving; a
poisoned ticket blocked by real scanners with findings at `app/auth.py:3` and `:4`
and the block surviving to the end of the run; a clean ticket through all seven
jobs to `promote` with three gates each paused for a human click; and an opened
issue starting a run with nobody typing a command.

**Known limitations:**

- `STATE_BACKEND=dynamodb` does not work on the cloud path. The table and its IAM
  exist, but `scripts/run_stage.py` reads state through a path helper that refuses
  on that backend by design. The cloud path runs on the `local` default with an
  artifact handoff between jobs.
- **Reported line numbers are indices into the added-lines-only file**, not into
  the real file — a finding at `app/auth.py:3` means "the third added line". This
  is **deliberately not fixed before the demo**: correcting the materialiser would
  shift the pinned `{3, 4}` onto `{4, 5}`, which is the **fixture's** pair, and
  that pair is the only field distinguishing a real scan from a fixture verdict.
  Fixing the offset would collapse the discriminator the whole verification story
  rests on. Post-demo, both must move together.
- All three gate Environments have **`can_admins_bypass: true`**. A repository
  admin can push a gate through without a reviewer clicking, so the honest answer
  to "can a gate be skipped?" is yes, by an admin. An operator setting rather than
  a code path; `scripts/preflight.py` check 4 reports it on every run.
- `agentorg/approve_server.py` has **no authentication** and binds `127.0.0.1`
  only. It is a local convenience over `gates.resume`, superseded by GitHub
  Environments, and must never be exposed off-host.
- **A vague ticket may legitimately end `failed` rather than promoted.** The
  reviewer is a real model and it withholds approval when the diff does not match
  what it asked for. Measured 2026-08-22: asked to rate-limit password resets *per
  email address*, the developer produced *per-IP* limiting four times, the revision
  cap expired, and the run ended `failed` with the scanners reporting `PASS`. That
  is the pipeline working — nobody approved the change — but it means a demo ticket
  needs to be specific enough for the developer to satisfy.

**Deliberate, and previously listed as limitations:**

- **The reviewer's verdict is advisory, and that is the design.** A reviewer that
  never approves takes the run to `MAX_REVISION_LOOPS` and the run ends `failed`,
  even though the scanners cleared the diff. A change nobody approved should not
  ship, and promoting on a scanner pass alone would make the reviewer decorative.
  What was wrong was that such a run *claimed to have been blocked*; it now renders
  `✗ FAILED`, which distinguishes "the reviewer never approved" from "the security
  rule stopped it".

**Closed:**

- An auto-started run used to be indistinguishable from a manual one — EventBridge
  dispatches through the same REST API `gh workflow run` uses, so both report
  `event: workflow_dispatch` and no Actions field separates them. The ingress now
  sends `trigger: issue` and the workflow defaults to `manual`, recorded on
  `RunState.trigger`. The two values must differ or the field would prove nothing,
  and a test asserts exactly that.
- **Every model-calling agent was silently serving fixtures**, for about a week.
  `bedrock:InvokeModel` was `implicitDeny` on the cross-region inference profile
  the code names, because the runtime role granted `foundation-model/*` only.
  `llm.text()` catches a denial by design, so every run completed, every job was
  green, and the deployed plan comment matched `fixtures/plan_result.json` byte for
  byte. Both ARN shapes are now granted — the profile is what gets called, the
  foundation models are what answer, and either grant alone is still a denial.
  `scripts/preflight.py` check 1 re-runs the simulation, because a green
  `terraform apply` proves the policy was written, not that it permits the call.

---

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
[`00-timeline.md`](docs/plan/00-timeline.md). The demo runbook, with output pasted
from real runs, is [`docs/plan/reem/demo_script.md`](docs/plan/reem/demo_script.md).
Notes for AI coding sessions are in [`CLAUDE.md`](CLAUDE.md).
