# CLAUDE.md — working in The Agent Org

A multi-agent CI/CD pipeline. Five role agents walk a ticket through three human
gates; a deterministic security rule decides whether it ships. Judged live demo:
**Tue Aug 25, 2026.**

Python is `.venv-main/bin/python`. Do **not** create a venv and do not use
`.venv-habiba` / `.venv-sorour` / `.venv-testing` — each carries an editable
install `.pth` file pointing at a sibling worktree, so imports resolve somewhere
other than where you are editing.

Account `339712964409`, region `us-east-1`.

---

## What the cloud path has actually done, measured 2026-08-22

Both demo halves completed end to end on the deployed system. This section exists
because "deployed" and "verified" were separate facts for most of a week, and
several claims in this file were written while only the first was true.

**The poisoned half** — run `32540401814`. `plan → gate1` (paused, approved by
click) `→ develop`, which blocked. It produced **PR #11** on
`mohamedsorour1998/auth-service` carrying three agent comments, the security one
reading verbatim:

```
### Agent Org · security
**BLOCK** — 2 blocking finding(s) of 3 total
_provenance: scanners_
- `gitleaks` **aws-access-key-id** (critical) at `app/auth.py:3`
- `gitleaks` **aws-secret-access-key** (critical) at `app/auth.py:4`
```

Lines **3 and 4** with `provenance: scanners` — the discriminator this file's next
section is about, from the deployed container rather than a fixture.

**The clean half** — run `32540911270`, **all seven jobs green**: `plan → gate1 →
develop → gate2 → sre → gate3 → promote`, three gates each paused for a click, all
three rejection recorders correctly `skipped`. **PR #12**, six stage comments, and
security read `**PASS** — 0 blocking finding(s) of 0 total`, `_provenance:
scanners_`. Real scanners cleared a clean diff, which is the half a fixture
fallback could not honestly produce.

**The gates hold.** Each gate job sat in `waiting` with
`approvers: ["mohamedsorour1998"]` and did not proceed until approved. Before
2026-08-22 `gate1` was the only Environment and had `protection_rules: []`, so it
did not pause — it ran. An Environment without a required reviewer is not a gate.

**`ticket_id` MUST BE THE BARE ISSUE NUMBER** for the pre-PR comments to land.
`github_ops._ISSUE_REF` is `\A#?([0-9]+)\Z` and anything else is refused by
design (`github_ops.py:418` — a loose parse would write on issue #1 of the target
repo). Dispatching `ticket_id=CLEAN-VERIFY` logs

```
[post_comment] ticket 'CLEAN-VERIFY' is not an issue number, so there is no
issue to comment on
```

and the plan and gate1 comments go nowhere, silently, while every job stays green.
The EventBridge template sends the real number, so the auto-triggered path is
correct; a hand dispatch with a label is not.

**Still unverified:** nothing on the cloud path. The automatic trigger was the last
open item and it is now **VERIFIED** — see below.

## The automatic trigger, verified 2026-08-22

Opening issue **#15** on `auth-service` started run `32542152671` with no
`gh workflow run` anywhere. The whole chain, each hop with its own evidence:

```
issue opened
  -> Lambda:  accepted delivery e89b0238-9dc4-11f1-87a1-90dc3e86b309 (issues)
  -> EventBridge rule -> API destination -> POST .../dispatches
  -> run 32542152671, plan job env:
       TICKET_ID: 15
       TICKET_TEXT: Rate-limit the password reset endpoint
       POISONED: false
  -> all seven jobs green, three gates each paused for a click
  -> PR #17, with `plan` and `gate1` on the ISSUE and six stage comments on the PR
  -> security: PASS, provenance: scanners
```

`TICKET_ID: 15` is the proof the inputs came from the issue rather than from a
hand dispatch: nothing in this repository knows that number.

**`event:` STILL READS `workflow_dispatch` ON AN AUTO-STARTED RUN, and that is
correct rather than a sign it did not work.** EventBridge triggers the workflow
through the REST dispatch API, so GitHub records the same event type it records
for `gh workflow run`. There is no field that distinguishes them. To tell an
auto-started run from a hand-started one, read the plan job's `TICKET_ID` — or
just note that nobody typed anything.

**THE DLQ IS WHERE A FAILED DISPATCH GOES, AND IT EARNED ITS PLACE ONCE ALREADY.**
The first attempt failed silently as far as GitHub and the Lambda were concerned --
both reported success -- and the only record of why was the DLQ message:

```
ERROR_MESSAGE: ApiDestination returned HTTP status 403 with payload:
{"message":"Resource not accessible by personal access token", ...}
x-accepted-github-permissions: actions=write
```

The dispatch token had been narrowed to the target repo, so it lost `actions:write`
on THIS repo. Without the DLQ that is an issue that starts nothing, with a healthy
Lambda log and no error anywhere. The README says EventBridge barely earns its
place; this is the one thing it bought that mattered.

**The token needs BOTH repositories.** `auth-service` for contents + issues + pull
requests (the PR and every comment), and `TheAgentOrg` for `actions:write` (the
dispatch). Scoped to one, the other half fails: narrow it to TheAgentOrg and every
comment 403s; narrow it to auth-service and no run ever starts.

---

## The one verification idea that matters most

The deployed security container genuinely runs scanners, and there is exactly
**one field** that proves it.

```
verdict: block   blocking: 2   files: ['app/auth.py']
LINES: [3, 4]        <- real scanners
provenance: scanners
```

Real scanners report `app/auth.py:3` and `:4`. The fixture reports `:4` and
`:5`. **The line-number pair is the only field distinguishing the two paths.**

A count of `blocking=2`, the verdict `block`, the two rule names
(`aws-access-key-id`, `aws-secret-access-key`), the file, the tool `gitleaks`
and the severity `critical` are produced **identically by both paths** — so
`blocking=2` proves nothing on its own. The fixture's explanation names a real
file and a real remediation and is indistinguishable from real gitleaks output.

Never assert "the scanners ran" from a count. Assert it from the line numbers,
via `tests/provenance.py`'s `REAL_SCANNER_LINES` / `FIXTURE_LINES`, or from the
recorded `scan_provenance` field. Note the two sets **overlap at line 4**: no
single-line observation separates the modes, only the whole set does. Compare
sets, never individual findings.

---

## The frozen contract

`agentorg/state.py` is FROZEN. You may **ADD optional fields**; never rename or
remove one. A rename breaks all five lanes at once and nobody notices until
integration.

One optional field was added this week: `RunState.poisoned: bool = False`. It
exists because `developer.run(state, poisoned=...)` is a Python keyword argument
and `agents/server.py:164` calls `AGENTS[role].run(state)` with no kwargs — over
HTTP there is nowhere to put one. The state *is* the payload, so a per-call
argument the container must see has to travel as a field. The kwarg still wins
where passed: `developer.run` reads the field only when the kwarg is absent.

`compute_security_verdict(findings, threshold)` in `state.py` is the block rule.
Pure Python, no model. It is called in exactly **one** place on the pipeline
path: `agentorg/agents/security.py:187`, inside the security agent. Neither
`graph.py` nor `scripts/run_stage.py` calls it — both reach the verdict through
`call_agent("security", state)` then `state.security.verdict`. So the rule is
evaluated once, behind the agent seam, whether the agent runs in-process or in
its AgentCore runtime.

---

## The knobs, and why their defaults are load-bearing

All in `agentorg/common/config.py`, which carries longer notes than this table.
Every boolean parses `== "true"` case-insensitively — never
`bool(os.environ.get(...))`, which reads the string `"false"` as True.

| Knob | Default | Why the default is load-bearing |
|---|---|---|
| `REMOTE_AGENTS` | `false` | False = in-process. Keeps the LOCAL path the tested one (the whole suite runs through `call_agent`), **and** it is the demo's fallback: if the runtimes misbehave on Tuesday, unsetting one variable puts the pipeline back on the path that has been green all week. |
| `SCANNERS_REQUIRED` | `false` | False = a missing binary is a **dev affordance**: each wrapper raises, `agents/security.py` catches it, the FIXTURE verdict stands in, and the poisoned diff still blocks. True promotes **absent → fault**. Set true on a runtime **without** the binaries and it blocks even the CLEAN run, with `blocking=3` (three `*-scanner-error` findings). |
| `OFFLINE` | `false` | Closes the **GitHub seam only**. It does **NOT** disable the model — `llm.available()` reads `LLM_DISABLED`, `LLM_BASE_URL` and boto3 credentials, never `OFFLINE`. For a genuinely offline run set **both** `OFFLINE=true LLM_DISABLED=true`. |
| `STATE_BACKEND` | `local` | Keeps the tested path and the demo path the same. Unknown values **raise at import** rather than falling back — a typo'd `dynamo` silently writing to disk would leave an operator believing a run is durable. |
| `MAX_REVISION_LOOPS` | `3` | Caps the developer↔reviewer loop. |
| `SCANNER_TIMEOUT_SECONDS` | `120` | Per-scanner-invocation, not whole-suite. A hung scanner is worse than a crashed one: on a projector it is indistinguishable from a freeze. |

`SCANNERS_REQUIRED=true` belongs on the **security runtime only** — that is the
one image carrying the three binaries, so it is the only agent that can honestly
demand them. `.github/workflows/deploy.yml:248-251` guards it to that agent.
It is deliberately absent from `run-pipeline.yml`, where it would block the clean
half of the demo.

`STATE_BACKEND=dynamodb` is **known debt**: `scripts/run_stage.py:_load` calls
`gates._state_path`, which refuses on that backend by design, so every cloud
stage after `plan` raises. `run-pipeline.yml` sets no `STATE_BACKEND` and runs on
the `local` default. Fixing it means reading through `gates.load` in
`run_stage.py`, not only in `gates.py`.

---

## Lint rules that cannot be relaxed

```bash
.venv-main/bin/python -m ruff check agentorg scripts tests   # must exit 0
actionlint .github/workflows/*.yml                            # must exit 0
```

- **No `[tool.ruff]` section** in `pyproject.toml`. No `# noqa`. No per-file
  ignores. The rule set is ruff 0.16's defaults, unconfigured.
- `I001` (unsorted imports), `BLE001` (blind `except Exception`) and `ISC004`
  (implicit string concat in a collection literal) are **ruff 0.16 defaults** —
  verified with `ruff check --isolated`. They fire without being selected.
- `target_repo/` is **deliberately NOT** in the lint command. It is the demo's
  subject repository, not our code, and it currently has 2 ruff errors on
  purpose.
- Ruff pinned `>=0.16,<0.17` in `pyproject.toml`; setuptools `>=61,<85` in both
  `[build-system]` and the `dev` extra. Bump both together, deliberately.

---

## The four autouse guards in `tests/conftest.py`

Every one forces the offline path, then puts a loud raiser on the seam
underneath. Do not weaken them.

1. **Model (Bedrock)** — `config.LLM_DISABLED = True` and `llm._complete` →
   raiser. Without it, `pytest -q` on a laptop with AWS credentials makes a live
   billable Bedrock call per agent per pipeline test.
2. **GitHub** — `config.OFFLINE = True` and `github_ops._repo` → raiser. This
   seam **writes**: measured before the guard existed, four outbound connections
   to `api.github.com` per run, performing real branch/commit/PR writes.
3. **Offline workspace** — redirects `OFFLINE_REPO` and `OFFLINE_NOTES` at
   `tmp_path`. Guard 2 makes every test do real local `git`, and both knobs
   default under `runs/` inside this repo.
4. **Terminal** — `builtins.input` → raiser. Under `pytest -s` an unpatched
   `input()` blocks the whole suite with no failing test to point at.

Plus a fifth, suite-wide: `_scanner_cache_is_per_test_suite_wide` clears the
fan-out memo on **both** sides of every test. A stale cache hit looks exactly
like a scan.

**Why the `pytest.fail` raisers are load-bearing, not stylistic.** `Failed`
derives from **BaseException**, not Exception. `llm.text()` catches `Exception`
and `github_ops.post_comment` catches `Exception`, so an ordinary raiser would
be **swallowed into the fixture branch and the test would pass green** — exactly
the bug the guard exists to catch. Downgrade one to a plain Exception and
`post_comment` absorbs it while the live writes go out. Placement is not what
saves those paths; `pytest.fail` is.

A test that wants a real seam opts in **in its own body**, replacing all of the
layers — the policy knob *and* the seam function. Opting in with only the policy
knob reproduces the exact bug.

---

## The testing discipline — this project's real character

**Every test change carries a mandatory RED step:** name the exact mutation,
apply it, watch the exact named test fail, revert. Nineteen-plus assertions in
this repo turned out to pin nothing. A test that cannot fail is worse than no
test.

**When you change a mechanism, tests referencing the old one do not fail — they
stop testing.** Any test whose matcher can match nothing must assert that it
matched.

**Numbers in prose must come from a command whose output you paste**, not from
recall.

### The pattern found seven times across four layers

> **A test double, a helper, an inference, or a measurement that cannot express
> the failing case produces confidence that cannot be falsified — and reading it
> never reveals that.**

The instances, briefly:

- **A stub that could only emit `json.dumps`.** So no test could express a
  malformed body at all. Three refusal paths in `agent_client` were uncovered;
  a mutation that fabricated an envelope for an empty body returned a fully
  validated `PlanResult` with the file green.
- **A helper that blanked heredocs.** `_strip_comments` erases heredoc bodies —
  correct for its purpose — but `input_template` *is* a heredoc. A test written
  over the stripped text searched for `"poisoned": "false"` in text from which
  the whole template had been erased, matched nothing, and passed.
- **A `tee`-shaped stub that changed the failure's context.** The `run_id` guard
  test's first stub used `echo | tee stage.log`; under `pipefail` the pipeline's
  status came from `tee`, so the stub could not reproduce the assignment
  `run_id="$(grep ... | cut ...)"` inheriting status 1. The test passed against
  both the fix and the bug.
- **A shared expected-counts constant.** `_PROMOTED_RUN_COMMENTS` exists so the
  local and cloud paths cannot drift, and it works — deleting the cloud
  `_sre_comment` fails 1 test, the local one fails 6. But it declares
  `develop: 1, review: 1`, so it **structurally forbade the only run shape that
  could catch a third bug** (per-pass rendering of the revision loop). A real
  control and a blind spot in the same line.
- **A number committed as "measured" that the next run could not reproduce.**
  116.88s → 149.68s → 102.83s for the same suite, load-dependent. So "measured"
  is a property of a number **plus its conditions and spread** — quote a range,
  not a point.

Three more mutations survived 793 tests, all in the cloud path, every one a case
where `run_stage.py` inherited `graph.py`'s **comment** about a hazard but not
its **test**: `return EXIT_BLOCKED → EXIT_OK` (with which the poisoned run
reaches `status='promoted'`), `artifact_ref=ref → "comment://"`, and the flush
loop re-reading `state.dev`.

**A check that cannot distinguish "did not run" from "passed" is the defect this
whole project exists to prevent.** Same for "denied" versus "not ready yet".

---

## Secrets

**Never read, print, log or commit `.env`.** It holds a live GitHub token and is
gitignored (`.gitignore:13`).

FAKE credential literals only, in tests. `AKIAIOSFODNN7EXAMPLE` is AWS's own
published documentation example and is safe — it is the poison in
`tickets/poisoned.md`.

Zero static AWS keys anywhere. Every AWS step assumes
`arn:aws:iam::339712964409:role/github-actions-role` via OIDC.

---

## Where things live

| Path | What |
|---|---|
| `agentorg/state.py` | The FROZEN contract + `compute_security_verdict` |
| `agentorg/graph.py` | The local pipeline walk; five `call_agent` sites |
| `agentorg/common/config.py` | Every knob, with the reasoning |
| `agentorg/common/agent_client.py` | The one seam: in-process vs `invoke_agent_runtime` |
| `agentorg/agents/` | The five agents + `server.py` (HTTP), `Dockerfile`, `requirements.txt` |
| `agentorg/security/` | semgrep / gitleaks / trivy wrappers + their rule files |
| `agentorg/{gates,log,github_ops,timeline}.py` | Human gates, decision log, GitHub seam, timeline renderer |
| `scripts/run_stage.py` | One pipeline stage as one Actions job (the cloud path) |
| `scripts/scan_gate.py` | Real scanners over both fixtures; CI's `scan` job |
| `.github/workflows/run-pipeline.yml` | The cloud pipeline: 7 jobs + 3 rejection recorders |
| `.github/workflows/{ci,deploy,terraform}.yml` | Lint/test/scan, runtime deploy, infra apply |
| `infra/Terraform/` | All infrastructure. Nothing is created by hand in the console |
| `infra/ingress/handler.py` | The webhook Lambda (outside `agentorg/` on purpose) |
| `fixtures/` | A validated sample of every result shape |
| `tickets/` | `clean.md` and `poisoned.md` — the same feature request |
| `tests/provenance.py` | Which scanner mode a test is in, and the line-number discriminator |
| `docs/plan/reem/demo_script.md` | The six demo beats, with pasted verified output |
| `runs/` | Run logs + paused state. Gitignored, ~10k files — **never `ls` or tab-complete in here** |

---

## Traps already paid for

- **`aws --output text` appends a literal `None` line.** Cost two failed deploy
  runs. Read fields from the boto3 response; do not scrape CLI text.
- **`invoke_agent_runtime` needs `qualifier="DEFAULT"`.** Without it the call is
  `ResourceNotFoundException` even against a READY runtime with a READY
  endpoint. Not optional-with-a-sensible-default — measured.
- **The CLI wants a base64 payload; boto3 wants raw bytes.** Two interfaces to
  one API. Copying the CLI's encoding into boto3 code sends a base64 string as
  the body and the container fails to parse it.
- **A runtime reports READY before its endpoint serves the new version.** Retry
  the invoke rather than polling a status field.
- **`ListAgentRuntimeEndpoints` is not grantable to the CI role.** Measured with
  `simulate-principal-policy`: `implicitDeny` against both the runtime and
  runtime-endpoint ARNs, while the role's policy grants
  `bedrock-agentcore:*` on `"*"`.
- **`fixtures/` must be explicitly `COPY`ed into any image.** `fixtures_loader`
  resolves it from the **repo root** (`Path(__file__).parent.parent /
  "fixtures"`), so `pip install .` never ships it. Without it a runtime answers
  `/ping` 200 and every `/invocations` dies with `FileNotFoundError:
  /app/fixtures/plan_result.json`.
- **The image must be arm64.** AgentCore runs arm64; an amd64 image pushes,
  deploys, then fails to start with an exec format error that reads like a
  broken entrypoint.
- **Base image from ECR Public, not Docker Hub.** CodeBuild pulls anonymously
  and Docker Hub answers 429 — late in the build, for a reason unrelated to this
  repo.
- **The workflow file must be on `origin/main` BEFORE the EventBridge target is
  applied.** GitHub resolves the workflow file on the ref, and it answers **404
  both** for "file not on ref" and for an unauthenticated dispatch — two causes,
  one indistinguishable symptom.
- **`workflow_dispatch` inputs arrive as STRINGS**, booleans included, and the
  REST dispatch API rejects real JSON booleans inside `inputs`. `run_stage.flag`
  parses text and **raises** on anything unrecognised: `poisoned=yes` must be a
  loud error, not a quiet clean run.
- **A rejected GitHub Environment SKIPS its job**, it does not run it with a
  verdict. So a branch inside the gate job could never record a refusal — hence
  the three separate `gate*-rejected` recorder jobs whose `if:` fires when the
  gate job did not succeed.

---

## Before you commit

```bash
.venv-main/bin/python -m pytest -q                            # 816 passed, 3 skipped
.venv-main/bin/python -m ruff check agentorg scripts tests    # exit 0
actionlint .github/workflows/*.yml                            # exit 0
cd infra/Terraform && terraform fmt -check -recursive         # exit 0
```

Anything that looks like a crash on a projector outranks polish.
