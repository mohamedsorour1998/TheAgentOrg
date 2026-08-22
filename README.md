# The Agent Org

*by **RosettaTeam** — Sorour · Mariam · Habiba · Reem · Aya*

A CI/CD pipeline whose reviewers are AI agents and whose gatekeeper is not.

Five role agents — **planner → developer → reviewer → security → SRE** — take a
ticket from a sentence of English to an open pull request on a real repository.
Three human approvals sit in the middle of that walk. At the end, one function
decides whether the change is allowed to ship, and that function is ordinary
Python with no model in it.

The agents run on **AWS Bedrock AgentCore**; the orchestration is **GitHub
Actions**; every AWS resource is **Terraform**. Account `339712964409`,
`us-east-1`.

---

## The claim, stated so it can be falsified

**A ticket carrying a hardcoded AWS credential is blocked, and the block comes
from `compute_security_verdict()` in `agentorg/state.py` — never from a model.**

The whole function is five lines: sort findings by severity, keep the ones at or
above a threshold, block if any survive. It is called in exactly **one** place on
the pipeline path — `agentorg/agents/security.py:187`, inside the security agent —
so it is evaluated once, behind the agent seam, whether that agent runs in this
Python process or in its container. Neither `graph.py` nor `scripts/run_stage.py`
calls it; both read `state.security.verdict` afterwards.

That division is the point of the project. An LLM writes the code, and an LLM
explains the risk in prose on the pull request. An LLM's *opinion* of a diff is
advisory. The verdict is a threshold comparison over a list, and it returns the
same answer every time. The security agent fills
`SecurityResult.explanation` with the model's words; it does not set
`SecurityResult.verdict`.

### Two tickets, one feature

`tickets/clean.md` and `tickets/poisoned.md` are the **same feature request** —
add a per-IP login rate limit to `app/auth.py`. They differ in one way: the
poisoned ticket's reference implementation hardcodes credentials, so the
developer agent's diff carries them.

```
tickets/poisoned.md:17:+AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"
```

(`AKIAIOSFODNN7EXAMPLE` is AWS's own published documentation placeholder.
Nothing sensitive is in this repository.)

Showing **both** is what separates a pipeline from a wall. The clean ticket is
planned, developed, reviewed, scanned, SRE-checked and **promoted**. The poisoned
one is **blocked** at the security stage and never reaches the deploy gates.

---

## The flow

```
 GitHub issue opened
        │
        ▼
 Lambda Function URL ── verifies HMAC-SHA256 over the raw body
        │                (the only access control on a public endpoint)
        ▼
 EventBridge bus ── rule: detail-type "issues", action "opened"
        │           └─▶ API destination ─▶ POST .../run-pipeline.yml/dispatches
        ▼
 run-pipeline.yml   ── 7 jobs + 3 rejection recorders
        │
        │   plan ──▶ [gate1] ──▶ develop ──▶ [gate2] ──▶ sre ──▶ [gate3] ──▶ promote
        │             human                   human              human
        │
        └── each agent call ──▶ invoke_agent_runtime ──▶ 1 of 5 AgentCore runtimes
                                                          (arm64, one image, five tags)
```

`develop` is one job containing four things — the developer↔reviewer revision
loop, the pull request, and the security verdict — because none of them is a gate
boundary, and because the revision loop iterates an unknown number of times and
Actions cannot express "repeat this job until". A blocked run ends there with exit
code 3, and `gate2` never starts because it `needs` it.

---

## Why the architecture looks like this

This is the question the previous version of this README failed to answer, and
part of the honest answer is that one component barely earns its place.

### Why not just put the pipeline logic in the Lambda?

Two structural reasons, and neither is a preference.

**The human gates.** All three gates are GitHub Environments with required
reviewers. An Environment pauses a **job**, and a job cannot pause in its middle —
which is precisely why the pipeline is cut into seven jobs at the gate boundaries
instead of being one `run_pipeline()` call, with the `RunState` handed along as an
Actions artifact. A Lambda cannot pause for a human at all: it has a hard
15-minute ceiling, and a gate may sit for hours waiting on someone to read a
diff. Step Functions *could* wait — but then the approval UI has to be built from
scratch, and the reason for choosing Environments is that the approval surface is
the one reviewers already use, and that a required reviewer is a repository
**setting** that no edit to a workflow file can argue with. A gate implemented as
`if: inputs.auto_approve != true` would be skippable, and a skipped gate is
indistinguishable downstream from an approved one.

**The run has to be visible where the work is.** The surface a reviewer judges
this on is the target repository: the issue, the pull request, the agent
comments, the Actions run with its pause. A Lambda's execution lives in
CloudWatch Logs, which is not a surface anyone reviews a change on.

### Does EventBridge earn its place? Barely.

It buys three things: a dead-letter queue, so a dispatch that fails after three
retries is still readable 14 days later instead of vanishing; a retry on the
dispatch call itself; and a bus other consumers could subscribe to later without
touching the Lambda.

That is the complete list. **The Lambda could call
`POST /repos/{owner}/{repo}/actions/workflows/run-pipeline.yml/dispatches`
directly and the system would behave identically** — the same workflow would
start with the same inputs. The bus is one hop of indirection buying a DLQ and a
retry policy that ~30 lines of Python in the handler could also provide. If you
looked at the diagram and thought "why is that hop there", you were right to.

### Then why a Lambda at all, rather than pointing GitHub at EventBridge?

Because there is no such thing, and this was checked rather than assumed. The
`aws events` API has no inbound-webhook operation: `create-api-destination` and
`create-connection` are **outbound**, and `create-partner-event-source` requires
an onboarded SaaS partner, which GitHub is not. Something has to terminate the
HTTPS POST and verify GitHub's `X-Hub-Signature-256` HMAC, and that is the
Lambda's entire job — verify, then `PutEvents`. Nothing before `compare_digest`
succeeds is allowed to cost money, mutate anything, or publish; a handler that
publishes and *then* returns 401 has already started the pipeline while telling
the caller it refused. `tests/test_ingress_handler.py` asserts zero `PutEvents`
on every reject path, and proves that assertion is not vacuous by replaying a
valid delivery through the same stub.

**Security note, stated rather than buried:** the Function URL is
`authorization_type = "NONE"`, because GitHub cannot sign a SigV4 request and
`AWS_IAM` would reject every delivery. So the endpoint is internet-reachable and
unauthenticated at the AWS layer, and the HMAC is the only access control in the
entire path. What limits the damage is scope, not authentication: the function's
IAM role holds two actions on two specific ARNs, and
`reserved_concurrent_executions` caps the spend an anonymous flood can cause.

### Is the workflow slow to start?

No. Measured across five dispatches, queue latency was **0 s** — the job picks up
immediately. What takes time in a run is the work itself: five agent invocations,
three scanners, and however long a human takes at each of the three gates. The
gates are the wall clock, and they are supposed to be.

---

## The one field that proves the scanners ran

This project's signature defect — the one it spends the most effort refusing — is
a check that reports green because it never ran.

The security container really does run gitleaks, trivy and semgrep, pinned in the
image at CI's versions. But a fixture block and a real gitleaks block produce the
**same** verdict, the same `blocking=2`, the same rule names (`aws-access-key-id`,
`aws-secret-access-key`), the same file, the same tool and the same severity. The
fixture's explanation names a real file and a real remediation and reads exactly
like real output.

Exactly one field tells them apart:

```
verdict: block   blocking: 2   files: ['app/auth.py']
LINES: [3, 4]        <- real scanners   (the fixture reports [4, 5])
provenance: scanners
```

So `blocking=2` proves nothing on its own, and no test in this repository is
allowed to claim "the scanners ran" from a count. It is asserted from the
line-number **set** — via `tests/provenance.py`'s `REAL_SCANNER_LINES` and
`FIXTURE_LINES` — or from `scan_provenance`, which is stamped at the call site
because inferring it afterwards is impossible. The two sets overlap at line 4, so
no single finding separates the modes; only the whole set does.

`scan_provenance` has three values, and the last two are kept apart on purpose:
`scanners` (a real scan decided), `fixture-fallback` (a scanner raised and the
fixture stood in — a **fault**), `fixture-stub` (nobody asked for a scan — a
**choice**). Collapsing those two would hide a broken gate behind a demo setting.
`agentorg/timeline.py` renders it in words:

```
⛔ security security  blocked [block] — 2 blocking
           ↳ scan: real scanners ran
```

### The discipline behind that

**Every test change carries a mandatory RED step:** name the mutation, apply it,
watch the named test fail, revert. Nineteen-plus assertions in this repository
turned out to pin nothing. The recurring lesson, found seven times across four
layers:

> A test double, a helper, an inference, or a measurement that cannot express the
> failing case produces confidence that cannot be falsified — and reading it never
> reveals that.

Concretely: a stub that could only emit `json.dumps` made it impossible to write
a test for a malformed response body, leaving three refusal paths uncovered. A
helper that blanks heredocs was used to test a heredoc, so the test searched text
from which its subject had been erased, matched nothing, and passed. A shared
expected-comment-count constant worked as a control and simultaneously forbade
the only run shape that could catch a third bug. And a suite wall time committed
as "measured" could not be reproduced — 102.83 s, 116.88 s and 149.68 s for the
same 793-test snapshot on one machine in one day — which is why numbers here are
quoted as ranges with their conditions, or not quoted.

`tests/conftest.py` carries four autouse guards that force the offline path and
then put a loud raiser on the seam underneath: Bedrock, GitHub, the working tree,
and `input()`. Each raises through `pytest.fail`, whose `Failed` derives from
**BaseException** — because the code under test catches `Exception`, so an
ordinary raiser would be swallowed into the fixture branch and the test would
pass green while making live billable calls. That detail is what makes the guards
work; placement alone would not.

---

## Running it

Python is `.venv-main/bin/python`. Do not create a venv.

```bash
.venv-main/bin/python -m pytest -q          # 816 passed, 3 skipped
.venv-main/bin/python make_fixtures.py      # regenerate + validate all fixtures
.venv-main/bin/python scripts/scan_gate.py  # real scanners over both fixtures

.venv-main/bin/python -m agentorg.graph               # clean    -> promoted
.venv-main/bin/python -m agentorg.graph --poisoned    # poisoned -> blocked
.venv-main/bin/python -m agentorg.graph --interactive # stop at the real gates
.venv-main/bin/python -m agentorg.timeline <run_id>   # one run as a timeline
```

Everything runs with no AWS, no GitHub and no scanners installed, which is how
five people built five lanes in parallel: `fixtures/` holds a validated sample of
every result shape, so a lane loads a teammate's fixture instead of waiting for
their code.

Every knob lives in `agentorg/common/config.py` with its reasoning. Two defaults
matter more than the rest. `REMOTE_AGENTS=false` runs all five agents in-process,
which keeps the tested path and the demo's fallback path the same one — if the
runtimes misbehave, unsetting one variable returns the pipeline to the path that
has been green all week. `SCANNERS_REQUIRED=false` makes a missing binary a
development affordance rather than a fault; set true it promotes absent → fault,
which is correct **only** on the security runtime, the one image that actually
carries the three binaries. Set it anywhere else and it blocks the clean run too.

Every boolean parses `== "true"` case-insensitively, never
`bool(os.environ.get(...))` — `bool("false")` is `True`, and that mistake would
run the poisoned diff on a run somebody asked to be clean with nothing anywhere
saying so.

---

## What is deployed, and what is verified

Five AgentCore runtimes — `theagentorg_{planner,developer,reviewer,security,sre}` —
all **READY at version 9**. One arm64 image, five ECR tags, differing only by
`AGENT_ROLE`; arm64 is not a preference, since AgentCore runs arm64 and an amd64
image pushes and deploys and then fails to start. `SCANNERS_REQUIRED=true` is set
on the security runtime only. Zero static AWS keys anywhere: every AWS step
assumes `arn:aws:iam::339712964409:role/github-actions-role` through GitHub OIDC.

**Verified end to end on 2026-08-22.** Run `32540401814`, a poisoned ticket,
produced PR #11 on `mohamedsorour1998/auth-service` carrying three agent
comments. The security comment read:

```
**BLOCK** — 2 blocking finding(s) of 3 total
_provenance: scanners_
gitleaks aws-access-key-id     (critical) at app/auth.py:3
gitleaks aws-secret-access-key (critical) at app/auth.py:4
```

Lines 3 and 4 — the real scanners, not the fixture. `status=blocked` survived to
the end of the run. The gates are real: a run pauses at `gate1` with the reviewer
named and does not proceed until approved.

### The automatic trigger, verified

Opening issue **#15** on `auth-service` started run `32542152671` with nobody
typing a command. Each hop left its own evidence: the Lambda logged
`accepted delivery e89b0238-9dc4-11f1-87a1-90dc3e86b309 (issues)`, EventBridge
dispatched, and the run's plan job came up with `TICKET_ID: 15` and
`TICKET_TEXT: Rate-limit the password reset endpoint`. That number is the proof the
inputs came from the issue — nothing in this repository knows it. All seven jobs
went green, the three gates each paused for a click, and PR **#17** carries `plan`
and `gate1` on the issue plus six stage comments on the pull request.

One thing that reads like a failure and is not: **an auto-started run still shows
`event: workflow_dispatch`**, because EventBridge triggers the workflow through the
same REST dispatch API that `gh workflow run` uses. No field distinguishes them.
Read the plan job's `TICKET_ID` if you need to tell them apart.

And the DLQ earned its keep on the first attempt, which is worth recording given
the section above calls EventBridge barely justified. That dispatch failed while
both GitHub and the Lambda reported success; the only record of why was the
dead-letter message:

```
ApiDestination returned HTTP status 403
{"message":"Resource not accessible by personal access token"}
x-accepted-github-permissions: actions=write
```

The dispatch token had been narrowed to the target repo and so lost
`actions:write` on this one. Without the DLQ, that is an opened issue that starts
nothing, with a clean Lambda log and no error anywhere. **The token needs both
repositories**: `auth-service` for contents/issues/pull-requests, `TheAgentOrg` for
`actions:write`.

`STATE_BACKEND=dynamodb` is known debt: `scripts/run_stage.py:_load` reaches
`gates._state_path`, which refuses on that backend by design, so every cloud
stage after `plan` raises. `run-pipeline.yml` sets no `STATE_BACKEND` and runs on
the `local` default with the artifact handoff.

---

## Where things live

| Path | What |
|---|---|
| `agentorg/state.py` | The FROZEN contract + `compute_security_verdict` |
| `agentorg/graph.py` | The local pipeline walk; five `call_agent` sites |
| `agentorg/common/config.py` | Every knob, with the reasoning |
| `agentorg/common/agent_client.py` | The one seam: in-process vs `invoke_agent_runtime` |
| `agentorg/agents/` | The five agents + `server.py` (HTTP), `Dockerfile` |
| `agentorg/security/` | gitleaks / trivy / semgrep wrappers + their rule files |
| `agentorg/{gates,log,github_ops,timeline}.py` | Gates, decision log, GitHub seam, renderer |
| `scripts/run_stage.py` | One pipeline stage as one Actions job (the cloud path) |
| `scripts/scan_gate.py` | Real scanners over both fixtures; CI's `scan` job |
| `.github/workflows/run-pipeline.yml` | The cloud pipeline: 7 jobs + 3 rejection recorders |
| `.github/workflows/{ci,deploy,terraform}.yml` | Lint/test/scan, runtime deploy, infra apply |
| `infra/Terraform/` | All infrastructure. Nothing is created by hand in the console |
| `infra/ingress/handler.py` | The webhook Lambda (outside `agentorg/` on purpose) |
| `fixtures/` | A validated sample of every result shape |
| `tickets/` | `clean.md` and `poisoned.md` — the same feature request |
| `target_repo/` | The demo's subject repository |
| `tests/provenance.py` | Which scanner mode a test is in, and the discriminator |
| `tests/dora_batch.py` | The before/after comparison harness |

`agentorg/state.py` is **frozen**: you may add optional fields, never rename or
remove one. A rename breaks all five lanes at once and nobody notices until
integration.

A rejected Environment **skips** its job rather than running it with a verdict,
which is why three separate `gate*-rejected` recorder jobs exist. Without them a
refused run and an in-flight run are byte-identical on disk — and "denied" versus
"not ready yet" is the same failure this whole project exists to prevent, wearing
different clothes.

---

## Who owns what

| Area | Owner |
|---|---|
| `infra/`, `agentorg/{graph,gates,log,state}.py`, `agentorg/common/`, `agentorg/agents/` | **Sorour** |
| `agentorg/github_ops.py`, `.github/workflows/`, `scripts/scan_gate.py` | **Mariam** |
| `agentorg/security/` — gitleaks / trivy / semgrep wrappers | **Habiba** |
| `target_repo/`, `tickets/`, `tests/test_functional_*`, `tests/test_baseline.py` | **Reem** |
| `tests/test_block_*`, `tests/test_chaos_*`, `tests/test_dora_*`, `tests/provenance.py` | **Aya** |

Per-person plans are in [`docs/plan/`](docs/plan/); start with
[`00-timeline.md`](docs/plan/00-timeline.md). The demo runbook, with pasted output
from real runs, is [`docs/plan/reem/demo_script.md`](docs/plan/reem/demo_script.md).
Working notes for Claude Code sessions are in [`CLAUDE.md`](CLAUDE.md).
