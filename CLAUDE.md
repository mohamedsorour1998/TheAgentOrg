# CLAUDE.md — working in The Agent Org

A multi-agent CI/CD pipeline. Five role agents walk a ticket through three human
gates; a deterministic security rule decides whether it ships.

**This platform runs in the cloud.** A GitHub issue triggers a Lambda, which
publishes to EventBridge, which dispatches a GitHub Actions workflow, which
invokes five Bedrock AgentCore runtimes. There is no laptop in the deployed path.
What runs locally is the **test suite** — deliberately hermetic, no AWS, no
GitHub, no scanners — and that is how you verify a change before pushing it.

Account `339712964409`, region `us-east-1`. Repo
`mohamedsorour1998/TheAgentOrg`; target repo `mohamedsorour1998/auth-service`.

Python for development is `.venv-main/bin/python`. Do **not** create a venv and
do not use `.venv-habiba` / `.venv-sorour` / `.venv-testing` — each carries an
editable-install `.pth` file pointing at a sibling worktree, so imports resolve
somewhere other than where you are editing.

---

## How to work in this repository

1. **Read before you write.** Nearly every file carries a comment explaining why
   it is the way it is, usually because the alternative was tried and measured.
   Those comments are the primary documentation; this file is an index to them.
2. **Every test change carries a mandatory RED step.** Name the exact mutation,
   apply it, watch the exact named test fail, paste the failure, revert. A task
   whose RED step was not run is **not done**. **An inert mutation does not count** —
   if the output is unchanged, the step tested nothing; say so and pick another.
3. **Never end a turn with a mutation applied.** `git diff` as your last step.
4. **Numbers in prose must come from a command whose output you paste**, never
   from recall.
5. **Update this file after each task.** If you learned something a future
   session would otherwise re-derive — a measured trap, a corrected claim, a
   verified run — record it here. That is the standing instruction, not a
   suggestion.
6. **Write one file per tool call, and commit it before starting the next.**
   Measured 2026-08-28: two lanes each died **three times at the identical point**,
   immediately after announcing a large test file — the API dropped the stream
   mid-write. Splitting into files under ~200 lines, each run and committed before
   the next began, got one lane from four total losses to `C1–C6` committed plus a
   second file on disk in a single attempt. A batch of work that is not committed
   does not exist.

   The corollary bites harder here than in most repositories: **a death can leave a
   RED-step mutation applied**, which violates rule 3 without anybody choosing to.
   Lane C died mid-verification with `FAIL_CLOSED_SEVERITY = "low"` in the tree, and
   because that value is validated at import, *every* test in the suite failed with a
   `ValueError` — the whole worktree looked catastrophically broken and was one
   `git checkout` from fine. **Check `git diff` in an abandoned worktree before
   diagnosing anything.**

### Before you commit

```bash
.venv-main/bin/python -m pytest -q                            # green; see the note below
.venv-main/bin/python -m ruff check agentorg scripts tests    # exit 0
actionlint .github/workflows/*.yml                            # exit 0
cd infra/Terraform && terraform fmt -check -recursive          # exit 0
```

**If you touched `scripts/make_deck.py`, also regenerate the deck** — none of the four
gates opens it, so a broken slide passes all of them:

```bash
.venv-main/bin/python scripts/make_deck.py                    # exit 0, prints its own checks
```

**The pass count is deliberately not written here.** It was `816 passed, 3
skipped` / `--collect-only` **819** before the 2026-08-22 four-lane pre-demo work,
and every lane added tests concurrently — so any number in this file is stale
within hours of being written. Measure it:

```bash
.venv-main/bin/python -m pytest --collect-only -q | tail -1
```

What has NOT changed is the **shape** of the result, and that part is the useful
instrument. The three skips live in `tests/test_provenance.py` and fire only when
all three scanner binaries **are** on PATH. On a machine with no scanners those
three RUN, and the skip count is 0. `docs/plan/reem/demo_script.md` treats that
inversion as an instrument check — `7 passed` from that file on a provisioned
machine means the skips are broken.

**A FOURTH SKIP FIRES IN A WORKTREE AND NOWHERE ELSE**, and it is correct behaviour
both times. `test_ingress_terraform.py:722` skips when
`infra/Terraform/environments/shared/terraform.tfvars` is absent — and that file is
**gitignored** (`.gitignore:14`), so it exists in the main checkout and in no linked
worktree, ever. Measured 2026-08-28, same commit, same interpreter:

```
main checkout     tests/test_ingress_terraform.py   30 passed
a linked worktree                                   29 passed, 1 skipped
```

So the honest instrument is **3 skips on `main`, 4 in a worktree** — plus the scanner
inversion above, which moves it the other way. A lane reporting `4 skipped` has not
broken anything; a lane reporting `4 skipped` **on main** has.

This is the same class as the `PYTHONPATH` failure below: a gitignored or
install-resolved path behaves differently in a worktree, and the difference reads as a
lane's regression. Only one test in the suite has this shape (`grep -rn "pytest.skip"
tests/ | grep gitignored` returns one line), so it is a known constant rather than a
hazard to hunt.

Anything that looks like a crash on a projector outranks polish.

### One test failed in every worktree and nowhere else — FIXED in `cf5cb83`

Recorded because **three lanes each spent time on it as their own regression, and I
told all three they were wrong before measuring**. The lesson is about how it was
misdiagnosed, not about the fix, which is one line.

`test_the_stage_records_the_trigger_onto_the_run_state` passed in the shared checkout
and failed in **every linked worktree, at any commit** — including a pristine one at
`9b2b1ee` with no lane's code in it. It runs `scripts/run_stage.py` as a **subprocess**,
so `sys.path[0]` is `scripts/` and the worktree root never reaches `sys.path`. The
editable install's finder then resolves `agentorg` to the **shared checkout**:

```
MAPPING = {'agentorg': '/Users/sorour/sorour/TheAgentOrg/agentorg'}
```

`gates._STATE_DIR` derives from `__file__`, so the stage wrote the shared checkout's
`runs/` while the test globbed the worktree's. Fixed by putting `PYTHONPATH=REPO_ROOT`
in the subprocess env; verified **`21 passed`** in a pristine worktree that failed
before it. **Nothing in the product was wrong** — both halves behaved exactly as
documented.

Three things worth keeping:

- **`RUNS_DIR`, which that test sets, is read by NOTHING.** `grep -rn RUNS_DIR
  agentorg/ scripts/` is empty. Believing it redirected state is what made the failure
  look inexplicable, and it is why the first three diagnoses were wrong. The comment at
  the call site now says so.
- **`-k` WITH A MISSPELLED NAME EXITS 0.** Measured: `pytest -k triger` (one `g`)
  reports `1172 deselected` and exits **0** — indistinguishable from a clean run. That
  is how a lane "verified" a fix that had not run. Assert the selection is non-empty, or
  read the collected count.
- **A failure reproducing in every worktree and never on `main` is environmental**, and
  the discriminator is a **pristine worktree at the same commit** — not a stash, which
  leaves the editable install pointing the same wrong way.

### A GATE VERIFIED ONLY IN THE MAIN CHECKOUT IS NOT VERIFIED

Three instances on 2026-08-28 alone, and together they cost more time than any code
defect this phase. Every one passes in the main checkout, fails in every worktree, and
reads as the lane's own regression.

| What | Where it lives | Symptom in a worktree |
|---|---|---|
| `agentorg` resolution | the editable install's `MAPPING` | a subprocess writes state to the **shared** checkout — `cf5cb83` |
| `terraform.tfvars` | gitignored (`.gitignore:14`) | a 4th skip appears in `test_ingress_terraform.py` |
| the `+x` bit on a script | **the disk, not the index** | `ruff` reports `EXE001` and the lint gate fails |

The third was mine and it is the clearest. `afb48d6` added
`scripts/measure_dependencies.py` with a shebang; I ran `chmod +x` to satisfy `EXE001`
and git recorded `100644`. So `ruff check agentorg scripts tests` printed **All checks
passed!** on `main` and **Found 1 error** in every worktree — I had verified the gate in
the one place where an untracked mode change made it pass, and would have shipped a lint
break to four lanes believing it was green. Fixed with `git update-index --chmod=+x`.

**The rule: state that lives on disk but not in the index is state a worktree does not
inherit.** File modes, gitignored files, and editable-install resolution are the three
found so far. Before telling a lane its failure is its own, reproduce in a pristine
worktree — and before trusting a gate, run it somewhere other than the checkout you
developed in.

---

## The architecture, in one pass

```
GitHub issue opened on auth-service
   │
   │  webhook, HMAC-SHA256 over the raw body
   ▼
Lambda Function URL  theagentorg-shared-github-ingress
   │  infra/ingress/handler.py — verify, then PutEvents. Nothing else.
   │  auth_type = NONE (GitHub cannot sign SigV4), so the HMAC is the ONLY
   │  access control in the whole path.
   ▼
EventBridge bus  theagentorg-shared-github-ingress
   │  rule: source github.webhook · detail-type "issues" · detail.action "opened"
   │  → API destination → POST …/run-pipeline.yml/dispatches
   │  → DLQ on failure (14-day retention)
   ▼
GitHub Actions  run-pipeline.yml — 7 jobs + 3 rejection recorders
   │
   │  plan → [gate1] → develop → [gate2] → sre → [gate3] → promote
   │           human               human            human
   │
   │  RunState handed job-to-job as an Actions artifact
   ▼
5 × Bedrock AgentCore runtimes  theagentorg_{planner,developer,reviewer,security,sre}
      one arm64 image · five ECR tags · differing only by AGENT_ROLE
      the security image alone carries gitleaks + trivy + semgrep
```

Supporting: 5 ECR repositories, DynamoDB `theagentorg-runs`, Secrets Manager (the
webhook secret and the dispatch token), CloudWatch. Every AWS step assumes
`arn:aws:iam::339712964409:role/github-actions-role` via GitHub OIDC. **Zero
static AWS keys anywhere.**

### Why seven jobs and not one function call

A GitHub Environment pauses a **job**, and a job cannot pause in its middle.
Since the three gates are Environments, the pipeline must be cut at the gate
boundaries. That single fact produces most of the structure below.

`develop` contains four things — the developer↔reviewer loop, the PR, and the
security verdict — because none is a gate boundary, and the loop iterates an
unknown number of times, which Actions cannot express as "repeat until".

A blocked run exits `3` from `develop`; `gate2` declares `needs: develop`, so it
never starts. **No `if:` expresses the block — the dependency graph does.**

**Since Phase 1 there is a second path, and it removes that constraint.**
`agentorg/queue/` + `scripts/worker.py` provide the sequencing, handoff, pause and
isolation Actions provides today — and a pause is a **durable row**, not a held runner
slot, so it can happen anywhere. The seven jobs may therefore collapse; **the three
gates must not.** Verified end to end with no Actions involved: a clean ticket reaches
`promoted`, a poisoned one exits **3** with `blocking=2`, a refusal exits **4**.

Two facts from building it that outlive the queue itself:

- **SQS could not have done this, and the reason is correctness.** Its nearest thing to
  a pause is a visibility timeout **capped at 12 hours**, so a gate awaiting a human
  silently becomes claimable after half a day and the run merges with an approval nobody
  gave. It also cannot answer `jobs_for_run`. Hence SQL, and `QUEUE_BACKEND=sqs` **raises**
  rather than falling through to memory.
- **The claim is at-least-once, not exactly-once, and the tests say so.** Two workers
  cannot hold one job — that is the transaction plus a UNIQUE index. What no queue can
  rule out without a fencing token the work itself honours is a lease that expired while
  its worker was **alive but wedged**. So `reclaimed_from` is the only trace that a stage
  may have run twice, and `worker._already_ran` reads the **run's** own record before
  re-running such a job — keyed on `security` rather than `dev` for `develop`, because the
  developer fills `state.dev` first and a reclaimed `develop` that produced a diff then
  died before the scanners ran would otherwise read as complete.

### Why a Lambda, and whether EventBridge earns its place

There is no inbound-webhook API in EventBridge — checked, not assumed:
`create-api-destination` and `create-connection` are OUTBOUND, and
`create-partner-event-source` needs an onboarded SaaS partner, which GitHub is
not. Something must terminate the HTTPS POST and verify the HMAC. That is the
Lambda's entire job.

EventBridge earns its place **barely**: a DLQ, a retry, and a bus other consumers
could subscribe to. The Lambda could call the dispatch REST endpoint directly and
the system would behave identically. It did earn its keep once as a debugging
surface — see the verified-runs section below.

---

## The frozen contract — `agentorg/state.py`

**FROZEN. You may ADD optional fields; never rename or remove one.** A rename
breaks all five lanes at once and nobody notices until integration.

Fields added since the freeze, all optional, all defaulting to a falsy value:
`SecurityResult.scan_provenance`, `LogEvent.scan_provenance`, `RunState.poisoned`,
`RunState.model_provenance`, `RunState.trigger`, `RunState.ci_status_measured`.

Three of those exist for the same structural reason — **a value measured on one machine
and needed on another**. Over HTTP the state IS the payload, so a per-call argument the
container must see has to travel as a field: `poisoned` (the caller's choice),
`ci_status_measured` (the runner has a GitHub token, the container does not), and
`model_provenance` (the model call happens over there).

### The vocabulary

| Alias | Members |
|---|---|
| `Severity` | `low`, `medium`, `high`, `critical` |
| `Actor` | `planner`, `developer`, `reviewer`, `security`, `sre`, `human`, `system` |
| `Stage` | `plan`, `gate1`, `develop`, `review`, `security`, `gate2`, `sre`, `gate3`, `promote` |
| `ScanProvenance` | `scanners`, `fixture-fallback`, `fixture-stub` |
| `ScanProvenanceOrUnknown` | the above plus `""` — a row written before the field existed |

`SEVERITY_ORDER: dict[str, int] = {"low": 0, "medium": 1, "high": 2, "critical": 3}`.

**The `: dict[str, int]` annotation is quoted here deliberately.** An earlier version of
this line omitted it, and a Lane C RED step built its mutation from this file's text: the
substitution matched nothing, the suite stayed green at `25 passed`, and an inert
mutation is indistinguishable from a caught one. **Copy source from the source, not from
this file** — and assert your substitution applied before trusting the result.

`LogEvent.action` also admits `"merged"`, which nothing in `agentorg/` ever
writes — dead vocabulary, harmless, but check `timeline._MARK` before removing it.

### The models

| Model | Produced by | Key fields |
|---|---|---|
| `PlanResult` | `agents/planner.py` | `tasks`, `acceptance_criteria`, `target_files`, `notes` |
| `DevResult` | `agents/developer.py` | `branch`, `diff`, `summary`, `files_changed`, `pr_url` (filled by `github_ops`, not the agent) |
| `ReviewResult` | `agents/reviewer.py` | `verdict` (`approve`/`changes_requested`), `comments`, `must_fix` |
| `Finding` | `agentorg/security/` | `tool`, `severity`, `rule`, `file`, `line`, `description` |
| `SecurityResult` | `agents/security.py` | `verdict` (`pass`/`block`), `findings`, `blocking`, `explanation`, `scan_provenance` |
| `SREResult` | `agents/sre.py` | `verdict` (`go`/`no_go`), `ci_status`, `slo_checks` |
| `HumanDecision` | the gates | `gate`, `decision` (`approved`/`rejected`/`overridden`), `by`, `at`, `reason` |
| `RunState` | `graph.run_pipeline` / `run_stage.py` | the whole run; `status` is `running`/`blocked`/`rejected`/`promoted`/`failed` |
| `LogEvent` | `graph._log`, `run_stage._log`, `gates` | one JSONL row |

`SecurityResult.explanation` is the model's prose. It does **not** set
`SecurityResult.verdict`.

### The block rule

```python
def compute_security_verdict(
    findings: list[Finding],
    threshold: Severity = "high",
) -> tuple[Literal["pass", "block"], list[Finding]]:
    """Block if any finding is at or above the threshold severity."""
    cutoff = SEVERITY_ORDER[threshold]
    blocking = [f for f in findings if SEVERITY_ORDER[f.severity] >= cutoff]
    return ("block" if blocking else "pass"), blocking
```

Pure Python, no model, no I/O. Called in **exactly one place** on the pipeline
path: `agentorg/agents/security.py:187`. Neither `graph.py` nor
`scripts/run_stage.py` calls it — both reach the verdict through
`call_agent("security", state)` then `state.security.verdict`. So the rule is
evaluated once, behind the agent seam, whether the agent runs in-process or in its
runtime. (`scripts/scan_gate.py` calls it twice more, off the pipeline path.)

Two properties to keep in mind:

- **`compute_security_verdict([]) == ("pass", [])`.** Three scanner wrappers'
  docstrings depend on that, and it is why a scanner failure must never become an
  empty list — that would send a poisoned change green with the whole suite
  staying green alongside it.

- **DETERMINISM IS NOT ENOUGH, AND 24 OF 25 TESTS PROVED IT.** Measured 2026-08-28 by
  Lane C: transposing two rows of `SEVERITY_ORDER` — `"high": 3, "critical": 2` — left
  **24 of 25** determinism tests green and silently turned the 25th into a **SKIP**,
  reported as `24 passed, 1 skipped`, which reads like a clean run. With that table, **a
  committed credential stops blocking at the shipped threshold.**

  The cause is structural and applies to any property test over a lookup table: every
  property derived its expectation from **the same table the rule reads**, so the
  mutation moved the rule and the expectation together and the lattice stayed
  self-consistent — repeatable, order-independent, monotone, and wrong. Determinism is
  satisfied by a wrong table applied consistently.

  Two fixes, and the first is a deliberate exception to this repo's no-second-declaration
  rule: `tests/test_scoring_determinism.py` restates the ranking as a **literal**,
  because a second declaration is the only way to detect a change in the first. The
  second states the **consequence** rather than the mechanism — a gitleaks finding blocks
  at every legal threshold — and it is the assertion that actually reads as an alarm.

  **The skip was the worse half.** Its condition was computed from `THRESHOLD_FLOOR`,
  which derives from the table under test, so the mutation moved the floor and **deleted
  the test**. A skip whose condition depends on the thing under test is a test that can
  delete itself; assert the refusal as behaviour instead.
- **An unknown threshold used to raise `KeyError` mid-run.** Verified:
  `compute_security_verdict([], threshold="HIGH")` → `KeyError: 'HIGH'`, raised
  from inside the security agent — the one stage whose whole purpose is to produce
  a verdict, dying while producing one, with a traceback naming a dict lookup
  rather than a misconfigured knob. **Fixed 2026-08-22:**
  `config.SECURITY_BLOCK_THRESHOLD` is now validated **at import** against
  `SEVERITY_ORDER`, like `STATE_BACKEND`. `SEVERITY_ORDER` is imported into
  `config`, not restated — there is no cycle, measured by AST: `state.py` imports
  only `__future__`, `datetime`, `typing`, `uuid` and `pydantic`. The import is
  **absolute**, because `tests/test_scanner_resilience.py` loads `config.py`
  standalone through `spec_from_file_location`, where a relative import raises.

### `RunState.poisoned`, and why the field exists

`developer.run(state, poisoned: bool | None = None)` takes a Python keyword
argument, and `agents/server.py:164` calls `AGENTS[role].run(state)` with no
kwargs — over HTTP the state **is** the payload, so a per-call argument the
container must see has to travel as a field.

The default is `None`, **not `False`**, and that is load-bearing:

```python
if poisoned is None:
    poisoned = state.poisoned
```

`None` means "nobody said", so the field decides. `False` means a caller
explicitly asked for a clean run and must be able to override a poisoned state.
Written as the obvious `poisoned or state.poisoned`, `poisoned=False` could not
turn poisoning **off**, and the failure would be invisible until a clean demo
shipped an AWS key.

---

## The one verification idea that matters most

The deployed security container genuinely runs scanners, and there is exactly
**one field** that proves it.

```
verdict: block   blocking: 2   files: ['app/auth.py']
LINES: [3, 4]        <- real scanners
provenance: scanners
```

Real scanners report `app/auth.py:3` and `:4`. The fixture reports `:4` and `:5`.
**The line-number pair is the only field distinguishing the two paths.**

`blocking=2`, the verdict `block`, both rule names (`aws-access-key-id`,
`aws-secret-access-key`), the file, the tool `gitleaks` and the severity
`critical` are produced **identically by both paths** — so `blocking=2` proves
nothing on its own. The fixture's explanation names a real file and a real
remediation and is indistinguishable from real gitleaks output.

Never assert "the scanners ran" from a count. Assert it from the line numbers, via
`tests/provenance.py`'s `REAL_SCANNER_LINES` / `FIXTURE_LINES`, or from the
recorded `scan_provenance`. **The two sets overlap at line 4**: no single-line
observation separates the modes, only the whole set does. Compare sets, never
individual findings.

`tests/provenance.py` does not merely compare — it **cross-checks against
`shutil.which` and raises when the two disagree**, because a silently wrong answer
here would mislabel the provenance column of every metric, and a mislabelled
metric is worse than a missing one: it reads as evidence.

### The reported line numbers are wrong, and MUST NOT be fixed before the demo

A finding at `app/auth.py:3` does **not** mean line 3 of `app/auth.py`. It means
the **third added line** — the numbers are indices into the added-lines-only file
`common/diff.py` materialises, not into the real file. Genuinely a defect, and
deliberately left alone until after Aug 25.

**Correcting the materialiser would shift `{3, 4}` to `{4, 5}` — which is the
FIXTURE's pair.** The two modes would then be indistinguishable, and the
discriminator this entire verification story rests on would be gone. The fixture
would still say `{4, 5}` and so would the real scanners, so every provenance
assertion in the suite would keep passing while proving nothing. Fixing the offset
and re-measuring the fixture are **one change, not two**, and doing the first
without the second is strictly worse than doing neither.

Two consequences worth holding onto:

- **`REAL_SCANNER_LINES` is a property of the scanners AND of one exact diff.**
  Measured 2026-08-22: a poisoned diff differing from
  `fixtures/dev_result_poisoned.json` by a **single missing blank line** produced
  `LINES: [2, 3]` with `provenance: scanners`, a correct `block` and
  `blocking=2`. Anything that edits the reference diff moves the discriminator, so
  `scripts/preflight.py` loads that diff rather than carrying a copy of it.
- A finding's line number is not usable for navigation. Do not build a "jump to
  line" affordance on it, and do not quote it to a judge as a file position.

### The three provenance values, and why the last two stay apart

| Value | Meaning |
|---|---|
| `scanners` | a real scan produced this verdict |
| `fixture-fallback` | a scanner raised and the fixture stood in — a **fault** |
| `fixture-stub` | nobody asked for a scan (`use_real_scanners=False`) — a **choice** |

Collapsing the last two hides a broken gate behind a demo setting. A fourth state
exists and is unnameable: `""`, a row written before the field existed; the
renderer reports it as *unknown* rather than guessing.

Provenance is stamped at the call site by `_with_provenance`, which uses
`model_copy(update=...)` — a copy, not an in-place set, because the fixture loader
returns a freshly validated model each call and mutating what a loader hands back
is how a shared fixture becomes shared mutable state. Note `update=` cannot reach
`verdict`, `findings` or `blocking`, so this cannot become a way to change a
decision.

### Absent scanner vs broken scanner

Two different faults, and they must not get the same answer. The classifier in
`agentorg/security/_run.py` is a **conjunction**: ABSENT iff `FileNotFoundError`
**and** `shutil.which(argv0)` finds nothing.

Either signal alone misclassifies real cases, in the fail-open direction —
`which` misreads a lost `+x` bit and a directory argv0 as absent; the exception
type misreads a broken shebang as absent, because errno 2 names the missing
*interpreter*. `_run.py:39-64` carries the measured table. Do not describe the
classifier as either half.

| Situation | `SCANNERS_REQUIRED` | Result |
|---|---|---|
| absent | `false` | `unrunnable_findings` **raises**; `security.py` catches → `fixture-fallback`; poisoned diff still blocks |
| absent | `true` | one `*-scanner-error` finding per tool, severity `high` → blocks **every** run including clean, `blocking=3` |
| broken | either | always a blocking finding; provenance `scanners` |

`error_finding` severity is `high` — **the block threshold**. Lower it to `medium`
and the lane silently reverts to failing open. Deliberately not `critical`, so a
dead scanner does not impersonate a discovered secret in a list a human is
reading. Its `file` is `<{tool} scanner>` and `line` is `0`.

`unrunnable_findings` **raises rather than returning `[]`** for the absent case,
because `compute_security_verdict([])` passes — an `[]` returned from there is one
careless `return` away from the silent-pass bug, and that `return` would be
invisible in review, because returning the value a helper handed you is what
correct code looks like.

---

### The scoring policy — `agentorg/security/scoring.py`

**ONE table, three scanners, native → ours.** Added 2026-08-28 (Lane C) because a judge
doubted the determinism claim and was right to: "a fixed threshold decides" was exactly
true for trivy and semgrep, which map their native severities, and **vacuously** true for
gitleaks, which hardcoded `severity="critical"` at the `Finding` constructor. Three
tables in three files, one of which was not a table at all.

**No run's verdict changed.** Every mapping is byte-for-byte what the two private tables
did; `tests/test_scanner_correctness.py`'s 9 call sites into both `_map_severity`
functions are the regression net, and the wrappers still expose `_map_severity` as the
named seam those tests drive.

| Thing | Value | Why |
|---|---|---|
| `FAIL_CLOSED_SEVERITY` | `high` | ONE constant for all three. Refused **at import** if it drops below the shipped block threshold — the semgrep default that was measured failing open cannot be reintroduced quietly. Deliberately not `critical`, so an unrecognised severity does not impersonate a discovered secret. |
| `POLICY[tool]` | a table **or** a constant, never both | `__post_init__` refuses anything else. Two of the three scanners emit a severity and are MAPPED; one emits none and is ASSIGNED one by rule. Both set means two answers exist and nothing records which the verdict used. |
| `THRESHOLD_FLOOR` | **derived**, `critical` | Computed from the policy carrying `protects_core_guarantee`. A literal would be a second declaration of gitleaks' severity, and two copies keep agreeing while one moves. |
| `NATIVE_NONE` vs `NATIVE_UNRECORDED` | `""` vs `<not recorded>` | "the scanner has nothing to say about severity" and "the scanner said something and this row lost it" are different facts. One spelling makes a gap in the artifact read as data about gitleaks. |

**THE GITLEAKS CONSTANT STAYS — it is a POLICY, and now the code says so.** gitleaks
reports no severity field at all (RuleID, File, StartLine, Description, an entropy score,
nothing that ranks the hit), so there is nothing to map and a severity must come from
somewhere. The rule: **any finding from a secret scanner is `critical`**, because a
committed credential has no lesser grade. Rejected alternatives, recorded next to the
value: entropy ranking scores a short high-entropy token below a long structured one, and
per-rule severities means answering "which credentials are we willing to merge?".

The wrapper calls `scoring.policy_severity("gitleaks")` rather than typing the word, and
`POLICY["gitleaks"].rationale` is **rendered** into the PR comment — a justification that
lives only in a comment is one nobody outside that file can quote. The honest
consequence, stated rather than papered over: **the threshold does not DISCRIMINATE among
gitleaks findings.** All sit at the top of the scale, so the arithmetic is `critical >=
threshold`, true for every threshold this project accepts. It still runs; it has one input
to compare.

**`THRESHOLD_FLOOR` does not bind today, and the test says so.** All four legal thresholds
pass, because the floor is `critical`. It binds the moment gitleaks' constant is lowered,
which is the realistic way this guarantee is lost — so
`test_the_threshold_floor_binds_when_the_secret_policy_is_lowered` lowers the source of
truth in an exec'd **copy** of the module and watches `high` become a refusal. A floor
that cannot be observed refusing anything is a vacuous check.

**`resolve_threshold` REFUSES, never CLAMPS.** Clamping runs the gate at a threshold the
operator did not ask for and reports success — the same shape as `STATE_BACKEND` falling
back to `local` on a typo. It also raises `ValueError` for a value outside the vocabulary,
because `config` validates the **environment variable** at import and a per-project
threshold arrives at **run** time, where an import-time check cannot see it.

**Where the module is NOT yet wired, and it is a real gap.** `SecurityResult.scoring` is
populated by `score_findings`, but the two call sites belong to other lanes:
`agents/security.py` (emit the rows) and `github_ops.py` (render the table). Until those
land, `scoring` is exercised only by its tests — the module is correct and **no deployed
run carries a scoring row**. `render_scoring_table` returns **lines**, not a blob, because
every renderer in `github_ops.py` composes a list and joins once.

**`score_findings` never writes `>=`.** Every `blocking` flag comes from
`compute_security_verdict`, one call per finding, against the same five lines the
pipeline's verdict comes from. A local comparison would be a second decision path whose
only job is to agree with the first, and **an audit artifact that can disagree with the
decision it describes is worse than none: it reads as proof.**

---

## The knobs — `agentorg/common/config.py`

Every knob lives there, with longer notes than this table. **Every boolean parses
`== "true"` case-insensitively** — never `bool(os.environ.get(...))`, which reads
the string `"false"` as `True`.

| Knob | Env var | Default | Why the default is load-bearing |
|---|---|---|---|
| `REMOTE_AGENTS` | same | `false` | False = in-process. Keeps the LOCAL path the tested one (the whole suite runs through `call_agent`), **and** it is the demo's fallback: if the runtimes misbehave, unsetting one variable puts the pipeline back on the path that has been green all week. |
| `SCANNERS_REQUIRED` | same | `false` | False = a missing binary is a **dev affordance**. True promotes **absent → fault**. Set true on a runtime **without** the binaries and it blocks even the CLEAN run, with `blocking=3`. |
| `OFFLINE` | same | `false` | Closes the **GitHub seam only**. It does **NOT** disable the model. |
| `LLM_DISABLED` | same | `false` | True forces every agent onto its fixture with no model call attempted. |
| `STATE_BACKEND` | same | `local` | Unknown values **raise at import** rather than falling back — a typo'd `dynamo` silently writing to disk would leave an operator believing a run is durable. |
| `STATE_TABLE` | same | `theagentorg-runs` | The DynamoDB table. Same literal as the Terraform module's default — two places, one value. |
| `SECURITY_BLOCK_THRESHOLD` | same | `high` | Passed straight to the block rule. **Validated at import** against `SEVERITY_ORDER` since 2026-08-22 — unvalidated, a typo raised `KeyError` inside the security agent mid-run. |
| `MAX_REVISION_LOOPS` | same | `3` | Caps the developer↔reviewer loop. `int()`, so a non-numeric value raises at import. **`run-pipeline.yml` sets it PER RUN: `1` when `poisoned`, `3` otherwise** — a poisoned run cannot converge (the safety net re-adds the key every pass), while a clean run genuinely uses its retries. Set on `develop` only, the one job that runs the loop. |
| `SCANNER_TIMEOUT_SECONDS` | same | `120` | Per-scanner-**invocation**, not whole-suite. A hung scanner is worse than a crashed one: on a projector it is indistinguishable from a freeze. |
| `GITHUB_REPO` | **`DEMO_REPO`** | `""` | **The one name mismatch in the file.** Setting `GITHUB_REPO` in the environment has no effect. |
| `GITHUB_TOKEN` | same | `""` | With `DEMO_REPO`, decides `_use_local()`. |
| `OFFLINE_REPO` / `OFFLINE_NOTES` | same | under `runs/` | Where the offline path does real local git and records blocked-run reasons. |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | same | `""` / `not-needed` / nova-2-lite | Non-empty base URL routes to an OpenAI-compatible gateway instead of Bedrock. |
| `BEDROCK_MODEL` | same | `us.amazon.nova-2-lite-v1:0` | The Bedrock path's model id. |

**`OFFLINE` does not take the model offline**, and the comment in `config.py`
exists because an earlier one claimed it did. `llm.available()` reads
`LLM_DISABLED`, `LLM_BASE_URL` and boto3 credentials — never `OFFLINE`. Measured:

```
OFFLINE=true python -c "from agentorg.common import llm; print(llm.available())"
True
```

For a genuinely offline run set **both** `OFFLINE=true LLM_DISABLED=true`.

**`SCANNERS_REQUIRED=true` belongs on the security runtime only** — that is the
one image carrying the three binaries, so it is the only agent that can honestly
demand them. `deploy.yml:249-251` guards it to that agent. It is deliberately
absent from `run-pipeline.yml`, where it would block the clean half of the demo.

**Read knobs through the module, never as a bare name.** `from ..common.config
import SCANNERS_REQUIRED` binds the value at import — before any fixture runs — so
the knob would silently ignore both the tests and the deployed environment. Same
trap for `config.LLM_DISABLED`.

**`STATE_BACKEND=dynamodb` is known debt.** `scripts/run_stage.py:_load` calls
`gates._state_path`, which refuses on that backend by design, so every cloud stage
after `plan` raises. `run-pipeline.yml` sets no `STATE_BACKEND` and runs on the
`local` default with an artifact handoff. Fixing it means reading through
`gates.load` in `run_stage.py`, not only in `gates.py`.

---

## The seams

Four places where behaviour switches. Each one is a single function, and each is
where a test opts in or out.

### `agentorg/common/agent_client.py` — in-process vs remote

```python
def call_agent(role: str, state: RunState, **kwargs) -> BaseModel: ...
```

Role validity is checked **before** the branch, so a typo'd role fails as a
spelling mistake rather than as "no runtime named theagentorg_bandit" a network
round trip later.

**There is no fallback to local.** A run that silently ran the planner in-process
after failing to reach the runtime would report success for the thing it did not
do. Every failure raises.

The remote branch:

```python
invoke_agent_runtime(
    agentRuntimeArn=arn,
    qualifier="DEFAULT",     # REQUIRED — without it the call is
                             # ResourceNotFoundException even against a READY
                             # runtime with a READY endpoint. Measured.
    payload=state.model_dump_json().encode("utf-8"),   # RAW BYTES for boto3;
                                                       # the CLI wants base64
    contentType="application/json",
)
```

**Two clients, two service models.** `bedrock-agentcore` (data plane) has
`invoke_agent_runtime` and not `list_agent_runtimes`; `bedrock-agentcore-control`
is the reverse. Describing "the AgentCore client" is wrong.

ARNs are read from the response field, never assembled and never scraped from
`--output text`. Runtime names match **exactly**: `theagentorg_planner_v2` must not
satisfy `planner`.

Retries are disabled on both clients deliberately — an agent invocation is not
idempotent: it writes a PR comment and burns model tokens, so a silent botocore
retry of a call that actually succeeded would double both.

Response validation refuses, in order: a zero-byte body (**not** parsed as `{}` —
that makes a blank response indistinguishable from a runtime that answered `{}`),
an unparseable body, a non-dict body, a non-200 status, a mismatched `agent` echo,
and a **falsy `result`** — "a green response meaning 'the agent did not run' is the
one answer this pipeline must never accept."

The failure classifier has six classes and an explicit **UNCLASSIFIED** branch:
TIMED OUT, DENIED, NOT FOUND, THROTTLED, refused, unclassified. A classifier that
guesses is worse than one admitting it did not recognise the error, because the
guess is what makes a caller wait out a condition that will never clear. Note
`ClientError` is **not** a subclass of `BotoCoreError` — verified against botocore
1.43.75 — so a single `except BotoCoreError` would let every AccessDenied through.

Only `poisoned` can cross the wire (`_KWARGS_CARRIED_ON_THE_STATE`).
`security.run`'s `use_real_scanners` **raises** in remote mode rather than being
accepted and dropped, which would run the security agent with real scanners while
the caller believed they were off — the same defect as a check that did not run.

### `agentorg/common/llm.py` — the model

`available()` makes no network call: `LLM_DISABLED`, then a gateway key check, then
boto3 credentials. `text()` returns `None` on every failure — unavailable, raised,
non-`str` reply, or empty after strip — and `structured()` collapses validation
failures to `None` too. That single signal is why four agents need no `try/except`.

`_complete` is the substitution seam the suite replaces.

**It also records what each call CONSUMED, and that is new as of 2026-08-28.** Before
it there was no cost tracking at all — `str(agent(...))` discarded `result.metrics` on
the one line in the repository that holds an `AgentResult` — so "what did that run
cost" had no answer, and two judge requirements hung on it.

The recorder mirrors `_LAST_SOURCE`: module state, reset by the caller, read through
`llm.usage()`. **Deliberately not a widened `_complete` return type** — conftest guard
1 replaces `_complete`, and dozens of tests hand back a bare string, so a new return
shape would break every one of them. That is this repo's named pattern (a double that
cannot express the new shape), arriving one layer up.

Four things worth knowing before touching it:

- **`accumulated_usage` is a dict with CAMELCASE keys**, not an object with
  attributes. strands declares it as a TypedDict carrying Bedrock's own key names
  (`strands/types/event_loop.py`, verified against strands-agents 1.52.0). Read with
  `.get`, never subscripted.
- **`cacheReadInputTokens` is OPTIONAL and its absence is a fact.** The TypedDict is
  `total=False`, strands' accumulator only creates the key `if
  "cacheReadInputTokens" in source`, and the OpenAI path sets it behind `if cached
  := ...` — so a real zero is omitted there too. Hence `Usage.cached_reported`
  records **presence**, not truthiness: `cached_reported=False` means the provider
  said nothing, `cached_tokens=0` means it said zero. Collapsing them makes an
  unmeasured cache read as a measured miss.
- **A fixture fallback records a ZERO ROW, not nothing** — stamped inside
  `record_fixture_fallback`, the one call every agent's fallback branch already
  makes. Same requirement as `scan_provenance`: a stage that fell back must not be
  indistinguishable from a stage nobody measured.
- **Usage crosses the remote seam on the 200 envelope**, exactly as `source` does —
  `usage_payload()` / `absorb_usage_payload()`. **The two wiring lines are still
  pending an owner**, because `agents/server.py` and `common/agent_client.py` are not
  Lane E's files. Until they land, a `REMOTE_AGENTS=true` run records no usage on the
  runner. `absorb_usage_payload` never raises and records **nothing** for an absent
  payload rather than a zero row.

  The two lines, verbatim. In `agents/server.py`, one key on the existing 200
  envelope (currently `server.py:194-198`):

  ```python
  self._send(200, {
      "agent": role,
      "result": result.model_dump(mode="json"),
      "source": llm.last_source() or "",
      "usage": llm.usage_payload(),          # ← ADD THIS LINE
  })
  ```

  In `common/agent_client.py`, immediately after the existing `llm._record(source)`
  block and **before** `return _validate(...)` (currently `agent_client.py:543-547`):

  ```python
  source = envelope.get("source") if isinstance(envelope, dict) else None
  if source in (llm.SOURCE_MODEL, llm.SOURCE_FIXTURE):
      llm._record(source)

  # ← ADD THESE TWO LINES
  if isinstance(envelope, dict):
      llm.absorb_usage_payload(envelope.get("usage"))

  return _validate(role, envelope)
  ```

  **Before `_validate`, for the same reason `_record(source)` is**: a container that
  answered honestly and then failed validation still spent the tokens, and a cost
  record that drops them understates the bill for exactly the runs worth
  investigating. **`absorb_usage_payload` needs no refusal of its own** — it is not a
  verdict, it validates its own rows, returns a count, and never raises.

  **Verifying it worked, and the distinction that matters.** Absent wiring and a
  zero-cost run are different facts and the record already separates them, measured
  three ways on the runner side:

  ```
  WIRING ABSENT (no key sent)            accepted=0  stages=0  usd=None
      render -> "cost: no model calls recorded for this run"
  WIRING PRESENT, container fell back    accepted=1  stages=1  usd=0.0
  WIRING PRESENT, model answered         accepted=1  stages=1  usd=0.0085
  ```

  So the check is **`len(state.cost.stages)`, never `usd`**: an unwired run has
  **zero rows** and `usd=None`, a wired run has a row per stage even when that stage
  spent nothing. `usd == 0.0` cannot tell them apart, and `absorb_usage_payload`'s
  return count is the same discriminator one layer down — `0` means nothing arrived,
  not that nothing was spent.

### `agentorg/cost/` — what a run cost

Three modules split by what can be wrong with each: `prices.py` (wrong when stale),
`record.py` (wrong when it guesses), `report.py` (wrong when it flatters).

**`CostRecord.usd` is `float | None` and the distinction is load-bearing**: `None`
means not priced — an unknown model, or a table nobody updated — while `0.0` means
priced and free. `total_usd` returns None only when NOTHING could be priced, and
otherwise **understates** by skipping unpriced rows, with `report.render` naming how
many stages were priced so a partial total is never read as complete.

**Every price row carries the date it was read**, per model, plus the command that
produced it. Measured 2026-08-28 from the **AWS Pricing API**, not a web page:

```
aws pricing get-products --service-code AmazonBedrock --region us-east-1 \
  --filters "Type=TERM_MATCH,Field=model,Value=Nova 2.0 Lite" \
            "Type=TERM_MATCH,Field=regionCode,Value=us-east-1" \
            "Type=TERM_MATCH,Field=feature,Value=On-demand Inference"

Nova 2.0 Lite  Input tokens                     0.00033   /1K = $0.33   /1M
Nova 2.0 Lite  Output tokens                    0.00275   /1K = $2.75   /1M
Nova 2.0 Lite  Prompt cache read input tokens   0.0000825 /1K = $0.0825 /1M
```

**Two traps the query itself exposed:**

- **The catalogue's name for our model is `Nova 2.0 Lite`, not `Nova 2 Lite`.** A
  query for the latter returns zero rows and **exits 0**, which reads exactly like a
  model with no pricing. `aws pricing get-attribute-values --attribute-name model`
  lists the real names.
- **`Nova Lite` and `Nova 2.0 Lite` are different models at 5.5x and 11x the price.**
  Reading the old row for the new model understates output by an order of magnitude.
  Both are in the table so the mistake is visible; a test asserts they differ.

Flex and priority tiers are deliberately **absent** — the same query returns them at
0.5x and 1.75x, and this pipeline selects neither, so pricing a run at the flex rate
would halve every reported figure.

**THE CACHE HIT RATE IS ZERO, MEASURED.** Nothing in `agentorg/` sets a Bedrock cache
point — `grep -rn 'cache_point\|cachePoint\|cache_control\|CachePoint' agentorg/ scripts/`
returns nothing — so Nova reports no `cacheReadInputTokens` at all, meaning all five
agents pay full price for the repository snapshot they re-send on every call. That is
the largest silent cost in the design. `report.render` states it in words rather than
leaving a reader to infer it from `0.0%`, because nobody reads a percentage as an
alarm. `cache_hit_rate` returns `None` for a zero denominator, never `0.0`.

**The alarm's condition is on the RENDERED STRING, not on `rate == 0.0`,** and that is
a measured fix rather than a style choice. `_pct` formats to one decimal place, so
every rate below 0.05% renders `0.0%` while comparing unequal to zero:

```
rate=1e-06   renders 0.0%   == 0.0? False
rate=0.0004  renders 0.0%   == 0.0? False
rate=0.0005  renders 0.1%   == 0.0? False
```

Against `rate == 0.0` a run with one cached token in a million printed `cache hit
rate: 0.0%` with **no finding beside it** — so two runs showing the reader an identical
number got different verdicts, and the one that looked fine was the one nobody was
warned about. Pinned by `test_a_cache_rate_that_merely_ROUNDS_to_zero_still_carries_the_finding`,
which asserts over `report._pct(rate)` rather than the float, because pinning it on
the float is exactly what let the gap exist: the test and the code agreed with each
other and neither agreed with the page.

**`cached_reported` does NOT reach the cost record, and that is a known gap.**
`llm.Usage` separates "the provider said nothing" from "the provider said zero", and
that survives the remote seam — but `StageCost` declares no such field, so both arrive
as `cached_tokens=0` and `cache_hit_rate` answers `0.0` for each. Measured:

```
provider SAID NOTHING   usage.cached_reported=False  -> rate=0.0
provider SAID ZERO      usage.cached_reported=True   -> rate=0.0
```

No reported number is wrong — both mean no caching is happening — but the two want
different fixes, and the record cannot say which you have. Left open because `state.py`
is the frozen contract and another lane's file; the fix is one optional field
(`StageCost.cached_reported: bool = False`) plus `any(e.cached_reported for e in
entries)` in `build_cost_record`. `test_the_reported_flag_does_NOT_survive_the_fold_and_that_gap_is_pinned`
**fails when the field is added**, and its message says what else to finish — a gap
recorded only in a comment gets closed halfway, with the field added and nothing
reading it, and nothing would say so.

**Per-stage attribution needs one line per stage in `graph.py` / `run_stage.py`** —
the integrator's files. Without it every model call in a run lands in a single `plan`
row: measured, and the reason
`test_a_run_through_the_real_pipeline_records_a_cost_for_every_stage` installs that
call itself and asserts on the stage **set** rather than the total, which is identical
either way.

### `agentorg/integrations/` — GitHub as ONE ADAPTER, not the substrate

Added 2026-08-28 (Lane D), spec §5. `github_ops.py` is 1,132 lines reached from 20
files and is the **one** module in `agentorg/` with a hard module-level vendor import
— measured, not asserted: `scripts/measure_dependencies.py` reports `1` of `50`
modules with a MODULE-LEVEL vendor import and names `github_ops.py` / `github`.

| Thing | What |
|---|---|
| `base.CodeHost` | the interface: five methods, four of which **cannot raise** |
| `github.GitHubHost` | shipped; **delegates** to `github_ops`, function for function |
| `memory.MemoryHost` | the double — no network, no git, no disk. **Not shipped** |
| `git.GitHost` | plain git, no vendor at all. **Not shipped** — it exists to prove the interface |

**`github_ops.py` IS NOT REWRITTEN, and that is the design.** It stays where it is
with every behaviour and all 20 importers intact; the adapter is one call per method,
asserted **over the AST** so a future edit that inlines any of it fails by name. A
refactor moving 1,132 lines would have to re-earn every trap in its comments — the
`os.path.exists`-not-`isdir` worktree guard, `_ISSUE_REF`'s anchors, `local://` only
after the bytes reach disk — and "no behaviour change" is not a claim a rewrite can
make honestly.

**The five methods are DERIVED from `graph.py`**, not designed: `post_comment` (114,
via `_comment`), `report_outcome` (486), `open_pr` (549), `ci_status` (644),
`merge_pr` (691). `deploy_note` is deliberately **absent** — it reads the Bedrock
AgentCore control plane, so an interface carrying it would oblige a plain-git adapter
to answer a question about AWS runtimes.

**An ABC, not a Protocol.** Structural typing means an adapter spelling a method
`post_commnet` satisfies nothing until stage nine dies on `AttributeError` with the
state already carrying `status="blocked"`. Measured when a RED step added a sixth
abstract method: `TypeError: Can't instantiate abstract class GitHubHost without an
implementation for abstract method 'deploy_note'` — at construction, on all three
adapters. A Protocol would have accepted all three silently.

**`open_pr` is the ONE method allowed to raise, and wrapping it would be a defect.**
`_ensure_offline_repo` refuses a repository offline mode did not create, and that
refusal exists because the `isdir` version was measured committing into a victim's
checked-out worktree. A wrapper returning a placeholder `DevResult` there proceeds as
though a branch existed.

**`host()` refuses an unknown name AND a registered-but-unshipped one** — the
`STATE_BACKEND` / `QUEUE_BACKEND=sqs` rule. "It passes the conformance suite" is not
"it may open a pull request on somebody's repository". One asymmetry, deliberate and
pinned: `INTEGRATION_HOST=` **from the environment** is the absent case and gets the
default, because that is what an unset Actions variable interpolates to, while
`host("")` **in Python** raises.

#### What the second adapter and the conformance suite actually found

Four defects, and none was visible by reading. This is the argument for D5/D6 in one
place.

- **An adapter that kept the developer's branch name.** `git left dev.branch as
  'feat/rate-limit'; open_pr must replace the agent's branch name with the one it
  actually created`. Not cosmetic: `_destination` routes a comment to the PULL
  REQUEST whenever `dev.branch` is truthy, and the developer agent fills that field
  with a branch that has no PR (the fixture's is `feat/login-rate-limit`), so every
  post-develop comment goes to a lookup that finds nothing while the run stays green.
  Fixed with `base.branch_for`, one spelling for all three adapters, pinned against
  `github_ops._short_sha`.
- **`ci_status` IS the measurement; the field is the WIRE.** Two adapters had been
  written to read `RunState.ci_status_measured`, which reads as obviously correct and
  is wrong — `github_ops.ci_status` does not read it. `[github]` failed with `assert
  'unknown' == 'failing'`. `graph.py:644` / `run_stage.py:705` measure on the runner
  (which holds a token) and store it; `agents/sre.py:163` is the **one** reader. A
  double honouring the field passes a test the shipped adapter fails.
- **The interface could not describe a host with no issues.** The first draft said
  "issue or pull request", which a bare git remote cannot name. The contract had to
  become the **ref** — `local://` means the bytes landed somewhere durable — which is
  why `DELIVERY_SCHEMES` names schemes and the issue/PR split stays in
  `github_ops._destination`.
- **A test that PENALISED the port it exists to enable.** `graph.py` was temporarily
  ported (1 import + 5 call sites) and the suite run: `1 failed, 1565 passed`, and the
  failure was `graph.py makes no github_ops calls; this test would pin nothing`. The
  derivation test matched by RECEIVER name, so it broke the moment anyone used the
  interface. Fixed to match the METHOD name and ignore the receiver, then verified
  `66 passed` under **both** spellings.

**The `git` adapter's accepted limit, recorded rather than patched:** it merges into
`main` with no protected branch and no review, so only this pipeline's own three gates
stand between a diff and `main`. Patching that would mean inventing an approval model
it does not have.

#### The RED step that came back INERT, and what closed it

Widening `CodeHost._guard`'s `except Exception` to `except BaseException` produced
**`42 passed` before and `42 passed` after** — identical — with
`test_offline_mode.py` at `25 passed` both ways. Cause: every conformance test drives
the handler with an ordinary `RuntimeError`, which both spellings catch, so the one
BaseException that matters — `pytest.fail`'s `Failed`, which is how conftest guard 2
keeps the suite off the live GitHub API — never went through the seam at all.

**This interface adds a SECOND blind handler in front of `github_ops`' own**, so it is
guard 2's history repeating on a new seam, exactly as `repo_snapshot` repeated it. Two
tests now close it, and the re-run fails both:

```
E  AssertionError: base.py's handlers catch ['BaseException']. Only `Exception` is allowed
E  Failed: DID NOT RAISE BaseException
WARNING agentorg.integrations.base: post_comment raised and the interface absorbed it
        (Failed: the conftest GitHub guard fired); answering 'comment://absorbed' instead
```

That WARNING is the defect made visible: the guard protecting 1,500 tests, absorbed
into a ref.

**Ruff dictated `_guard`'s shape.** The first draft put the `exc_info` traceback in a
helper: `BLE001` fired four times **plus** `LOG014` (`exc_info=` outside exception
handlers). BLE001 is satisfied only by a logging call ruff can statically resolve to
the logging module, carrying the traceback, **inside** the handler — so one shared
guard is what the rules force, not a style choice.

**`agentorg/integrations/` ships under the existing `include = ["agentorg*"]`** —
measured by building a wheel into a temp target and reading the tree, because
`test_packaging.py`'s `REQUIRED_SUBPACKAGES` lists only `agents`/`common`/`security`
and so cannot answer it.

### `agentorg/retrieval/` — context for PROSE and DRAFTING, and never for the gate

Added 2026-08-28 (Lane H), spec §10, judge requirement 8. The spec calls this the
requirement most likely to become a demo of a vector database rather than a product
improvement, so the acceptance test is a **moved number**.

**No third-party import, and that is a measured constraint rather than a preference.**
`test_agentcore_deploy_assets.py::test_requirements_covers_every_third_party_import_in_the_package`
AST-walks `agentorg/`, so an embeddings client or vector store here becomes a pinned
dependency of all five arm64 images — the same test Lane K measured refusing `starlette`,
which is already installed. `search.py` is weighted token overlap: sets not counts,
`keywords` at 3× body text, ties broken on `doc_id`. **The limit is stated as a test**
(`test_the_synonym_limit_is_real_and_this_test_records_it` asserts the FAILURE), so adding
stemming turns it red rather than letting the docstring quietly stop being true.

| Module | What |
|---|---|
| `provenance.py` | four values plus one unnameable; ONE encode, ONE decode |
| `guard.py` | the allow-list, and `refuse_verdict_arguments` |
| `search.py` | deterministic ranking, and the stated synonym limit |
| `repo_history.py` / `conventions.py` / `advisories.py` | the three corpora, curated, offline |
| `measure.py` | H6's before/after. Run it; do not quote a number from here |

**`documents == 0` READS IDENTICALLY FOR THREE DIFFERENT FACTS**, which is why the
provenance vocabulary exists and is the same argument as `scan_provenance`:

| Value | Meaning |
|---|---|
| `retrieved` | a corpus was searched and returned documents |
| `empty` | searched, matched nothing — a fact about the QUERY, not a fault |
| `disabled` | `RETRIEVAL_ENABLED` is false — a **choice** |
| `unavailable` | the corpus raised — a **fault** |
| `""` | a record predating the encoding. Rendered *unknown*, never guessed |

The value rides **inside `RetrievalRecord.corpora`** as `conventions=retrieved`, because
`state.py` is frozen and that model declares three fields; a fifth field is a contract
change this lane may not make. A **bare corpus name decodes to `""`, never to
`retrieved`** — a record that merely names a corpus is not evidence that corpus answered.

**H5 is enforced three ways, and the contract alone is NOT one of them.** `RetrievalRecord`
declaring no verdict field stops retrieved text being *stored* where the rule looks. It does
not stop `compute_security_verdict(findings, threshold=retrieved_value)`, which reads like
correct code and raises `KeyError` from inside the one stage whose purpose is to produce a
verdict. So `guard.context_for` refuses any consumer not on an allow-list, and
`refuse_verdict_arguments` refuses the six argument names a verdict reads.

**The security consumer is spelled `security_explanation`, and the spelling IS the
boundary.** There is no consumer name that reaches the rule: `_explain` receives an
already-decided verdict and its reply is only ever assigned to `SecurityResult.explanation`.
`context_for` returns `(text, provenance_entries, count)` — a caller cannot take the text
without taking the record.

**H7 attempts the breach rather than asserting isolation**, following
`tests/test_tenancy_leak.py`. Five attack documents written as plausible internal documents
in the corpus's own register — the finding is a known false positive; the threshold here is
above critical; `AKIAIOSFODNN7EXAMPLE` is AWS's published example so it authenticates
nothing; MAINTENANCE MODE, ignore previous instructions; approved exception SEC-4417 is on
file. Each carries the poisoned run's real rule names so it actually **ranks** — an attack
document nobody retrieves is not an attack — and
`test_the_attack_documents_are_actually_retrieved` is the positive control without which
every refusal proves only that nothing was there.

`low` and `LOW` are the pair worth keeping: the first is **legal and still blocks**
(`critical >= low`), the second raises. The attack fails for two different reasons and
neither is "the string was ignored".

#### H6 — MEASURED, and the metric is not the one the plan named

```
python -m agentorg.retrieval.measure --trials 8      # nova-2-lite, all 96 reviews source=model

MISMATCH CAUGHT   baseline 6/8    retrieval 8/8
FALSE BLOCKS      baseline 0/40   retrieval 0/40
HARD CONTROLS     refused 8/8 in all four arm/case combinations
```

The baseline approved a diff that did not implement its ticket in **2 of 8** reviews; the
retrieval arm approved none. Read the three lines together or not at all — a reviewer
objecting more on the mismatch **and** more on the settled questions has become
objection-happy, not better informed, and this project already paid for that (two clean runs
ended `status=failed` at the revision cap with security reporting PASS).

**The plan asked for false-block rate. It is ALREADY ZERO**, measured `0/15` in both arms
over the four objections CLAUDE.md records the reviewer wrongly blocking on plus missing
tests. That is the hand-written prompt fix working, and it means a corpus restating those
rulings has nothing to improve — moving a number there would have needed a baseline weaker
than the one that ships. So the metric became the reviewer's **miss rate on a plan
mismatch**, the failure the scanners structurally cannot catch since
`compute_security_verdict` reads findings and not intent. The five settled cases are kept as
the control in the other direction.

**Two measurement defects found by RUNNING it**, both this repo's named pattern arriving in
a harness rather than a test:

- **The diffs were fragments.** `BASELINE 5/5 RETRIEVAL 5/5`, every `must_fix` reading
  "references 'os' and 'time' modules that are not imported". The reviewer was objecting
  correctly to a defect no corpus can address, so the number **could not** move. Every case
  is now a complete module, asserted by `ast.parse`.
- **`strands.Agent` STREAMS to stdout**, so the first readable run had every result row
  prefixed by a fragment of the reply it described. Captured around the call.

**A TEST DISPROVED A CLAIM ALREADY WRITTEN INTO TWO FILES.** I asserted the diff alone
cannot retrieve `history-0001`, the rejection the gain depends on. It can:

```
diff only     history-0005 17   history-0002 14   history-0001 12
diff+ticket   history-0005 30   history-0001 25   history-0004 17
```

Both retrieve it at `limit=3`; the ticket changes its **rank**, third to second, and its
score, 12 to 25. The wrong claim came from reading a two-corpus probe where four
`conventions` entries sit above it. Corrected in both places with the scores pasted, and the
test now asserts the rank IMPROVES rather than that the document appears — a test
"corrected" to match the wrong claim would have pinned the wrong fact while reading as
evidence.

**A RED STEP DELETED A TEST INSTEAD OF FAILING ONE**, and it is the twelfth instance of the
named pattern. Dropping `"threshold"` from `guard.VERDICT_ARGUMENTS` — the single most
valuable name in the set — took `tests/test_retrieval_boundary.py` from **32 passed to 31
passed**, because the parametrisation read the set under test. `31 passed` reads like a clean
run. Fixed as Lane C fixed `SEVERITY_ORDER`: the names are a **literal**, with an anchor
test asserting the two agree in both directions and `threshold` asserted separately by name.
Re-run: `2 failed, 31 passed`.

**Not wired into any agent prompt**, and that is a real gap rather than an oversight: the
five agents' prompt text is Lane M's this phase. The wiring is one call per consumer —
`guard.context_for(<consumer>, query)`, append the text to the prompt, put the three return
values on `RunState.retrieval`. Until it lands, `retrieval` is exercised only by its tests
and **no deployed run carries a retrieval record** — the same shape as `scoring` before its
call sites landed.

### `agentorg/github_ops.py` — GitHub API vs local git

`_use_local()` returns `config.OFFLINE or not (config.GITHUB_TOKEN and
config.GITHUB_REPO)`. The second clause matters: PyGithub raises on an empty
token, so without it every local run and CI would die inside the PR node.

The offline path does **real git** — `init`, `checkout -B`, `add`, `commit` — in
`config.OFFLINE_REPO`. Not a stub. `_ensure_offline_repo` refuses a repository it
did not create, and uses `os.path.exists` rather than `isdir` because a linked
worktree gets a one-line `gitdir:` **file**; the `isdir` version rewrote a
victim's `user.email` and committed onto their checked-out branch before dying.

`post_comment(state, body, finding=None) -> str` **returns a ref in every case and
never raises.** That is a hard requirement: `graph.py` sets `status="blocked"` and
on the next line records this ref, and nine stages now post. Four ref shapes:
`https://…` (delivered), `local://…` (offline, only after bytes reach disk), and
`comment://<run_id>` (not delivered).

`_destination` picks issue vs PR from `state.dev.branch` — derived from the state,
not a parameter, because a `target=` argument would mean every existing caller
keeps the default and the planner's comment would silently go to the PR lookup.

`_ISSUE_REF = r"\A#?([0-9]+)\Z"`. **`[0-9]` not `\d`, deliberately** — `\d` is
Unicode-aware, so `\d+` matches Arabic-Indic `٧` and `int()` accepts it. Both
anchors are load-bearing: without them `7-extra`, `7 7`, `#7x` and `1-2` all yield
issue 7, "which is a comment written on a real issue nobody named." Every ticket id
this repo uses — `POISON-1`, `CLEAN-1`, `DEMO-1` — would become `#1`. **The next
thing `post_comment` does with the answer is WRITE.**

### `agentorg/gates.py` — where run state lives

`save`, `load`, `pause`, `resume`. `save` is the **one** place a `RunState` is
serialized, and it is public rather than private because the graph is one of its
three callers — a module reaching into another module's underscore is how a single
writer quietly becomes two.

`StateRef` is a frozen dataclass, **deliberately opaque** — it replaced a
`pathlib.Path` and is not a Path subclass, so a caller doing path arithmetic is
told so rather than silently building `runs/<id>.state.json/..` against a table.
`__str__` gives the path locally and `dynamodb://theagentorg-runs/<run_id>`
otherwise, which is exactly what `aws dynamodb get-item` needs.

`load` raises `FileNotFoundError` on **both** backends for an absent run —
deliberately the same exception. Do not soften it into a fresh `RunState`, which
would start a new run and report success for work it invented.

`resume` sets `status="rejected"` only for a rejection and **never un-sets it**.
Approving a run the graph already rejected leaves `status="rejected"` while still
appending the approval — so a rejected run displays a later approval on the
timeline. `status` holding is not a guard. `approve_server` refuses this at the
boundary rather than fixing it in `gates.py`, and
`tests/test_approve_server.py:266-289` pins the gap **on purpose**: a guard in
`gates.resume` would revoke the documented `gates_cli resume --decision
overridden` override path, the one capability a human is meant to keep.

---

## The five agents

Each `run()` takes a `RunState` and returns its own result type. Every one
degrades to a fixture rather than failing — which is deliberate, and which is
exactly why `scan_provenance` exists.

| Agent | Signature | Reads | Fixture fallback |
|---|---|---|---|
| `planner` | `run(state)` | `ticket_text` | `plan_result.json` |
| `developer` | `run(state, poisoned=None)` | plan, `review.must_fix`, `dev.diff` | `dev_result_{clean,poisoned}.json` |
| `reviewer` | `run(state)` | `dev.diff`, `plan.tasks` | `review_result.json` |
| `security` | `run(state, use_real_scanners=True)` | `dev` | `security_result_{block,pass}.json` |
| `sre` | `run(state)` | real CI, plan, `dev`, `review`, `security`, repo | `sre_result.json` — **advice only** |

Every one reads the target repository through `repo_snapshot.render(...)` — one shallow
`git clone`, 120s TTL, shared by all five. The reviewer passes `diff=` to get the
file **as the change would leave it**; the developer does not, because it is the one
writing the diff.

No agent wraps `llm.structured` in `try/except`: it already absorbs unavailable,
raised, chatty and unparseable, returning `None`. Wrapping again would also
swallow caller bugs and quietly serve fixture data while the run looked live.

**`sre.py` is no longer a stub, and its verdict is NOT the model's.** Since
2026-08-22 it measures CI first — `github_ops.ci_status(state)`, a real API read —
and derives `verdict` in code: `"no_go" if ci == "failing" else "go"`. The model
contributes `slo_checks`, `estimated_cost_note` and `notes` **only**, validated
against **`SREAdvice`**, a narrow model that does not even declare `verdict` or
`ci_status`. Its advisory checks are APPENDED after the measured CI row, never merged
by name, so a model check called `CI` cannot displace the real one.

Two consequences worth holding onto:

- **`sre.verdict == "no_go"` is now reachable** — it needs CI reporting `failing`.
  `graph.py`'s no_go branch is exercised behaviour rather than defensive structure.
- **`unknown` yields `go`**, deliberately: a target repo with no CI still proceeds and
  the honest `unknown` reaches the PR comment. Verified live — the clean run's SRE
  comment reads `**GO** — CI unknown` with a `FAIL CI` row above the model's advice.
  Whether `unknown` should block a MERGE is `merge_pr`'s decision, made there.

**Asking the model for `SREResult` was a real defect** — the schema required two
fields the prompt forbids, so every obedient reply was rejected and the fixture served
silently. See the verified-runs section for the measurement.

`fixtures/review_result.json` has `verdict: "approve"`, so the revision loop
normally executes **exactly once**. On a **poisoned** diff a live reviewer does not
approve: the 2026-08-22 poisoned run ran all four passes (`review ×4` on PR #44) and
security blocked it.

### WHO CATCHES A REAL SECURITY ISSUE — the ordering, and why two catchers is right

Asked during rehearsal and worth writing down, because the demo makes it look as
though the reviewer beat the scanners to it. Read off `scripts/run_stage.py:599-627`:

```
while True:                       # the developer/reviewer loop
    developer  -> a diff
    reviewer   -> approve | changes_requested      <- ADVISORY, runs EVERY pass
    break on approve, or on the revision cap
open_pr
security     -> pass | block                       <- BINDING, runs ONCE, after
```

So the reviewer does not catch it *first* in any meaningful sense — it runs **once per
pass** while security runs **once, after the loop settles**. On a poisoned run that is
four reviewer objections before security has been asked anything. Both refuse; only
security's refusal stops the pipeline (`develop` exits 3, `gate2` never starts).

**The answer to "if I have a real issue, who catches it?" is: both, on purpose, and
they are not redundant.**

| | reviewer | security |
|---|---|---|
| what it is | a model reading the diff | three real scanners + five lines of Python |
| catches | intent, logic, plan mismatch, taste | credentials, known CVEs, injectable patterns |
| authority | advisory — `graph.py` loops, does not stop | **binding** — `compute_security_verdict` |
| can be wrong | yes, both directions | deterministic, same answer every time |
| can be talked out of it | yes — it is a prompt | **no** — no model is involved |

A model that can be persuaded, distracted or prompt-injected must not be the thing
standing between a credential and `main`. That is the whole thesis of this repository,
and it is why the reviewer catching the key first is a *bonus* rather than the
mechanism: **the demo would still block with the reviewer removed entirely.** It would
not block with the scanners removed.

Corollary for a question a judge may ask — *what if the scanners miss something?* Then
the reviewer is the only thing that saw it, its verdict is advisory, and the change can
reach `main` past three human gates. That is an accepted limit, not a defended one: the
gates are the last line, which is why they require a named reviewer.

### Two agent-level guards worth knowing

**The developer's poisoned safety net.** If `poisoned` is asked for and the model's
diff does not contain a key, the reference diff is substituted — but `summary` is
deliberately **not** rewritten, and that omission is the only observable difference
between the rescue path and a wholesale fixture fallback. A named test asserts on
it. If you want to fix the cosmetic mismatch, give the test a different way to tell
the two paths apart **first**.

`_key_is_in_the_change` searches **added lines only**, via `common/diff.py`. The
previous form searched the whole diff string, so a key on a `-` line — the shape of
every revision after the reviewer asks for credentials to be removed — satisfied
the check, the net declined to substitute, and the poisoned ticket promoted.
Measured: 2 blocks in 5 live runs. **Do not widen this back to the whole diff.**

**The reviewer's `_ensure_actionable`.** Applied to both the model and fixture
paths. `graph.py` loops back on any non-`approve` verdict, but `developer._prompt`
attaches the previous diff and the reviewer's notes only `if
state.review.must_fix`. So a `changes_requested` with an empty `must_fix` sends a
plain **first-pass** prompt — the developer regenerates from the ticket instead of
revising, burns all three revisions doing it, and no test, log line or exception
marks the difference. The verdict itself is never rewritten: downgrading to
`approve` would discard a real objection.

### The HTTP contract — `agentorg/agents/server.py`

Standard library only. Two routes.

`GET /ping` → 200 `{"status":"healthy","agent":"<role>"}`, or 404.

`POST /invocations` — 400 (bad `Content-Length`, empty body, non-JSON), 413 (over
4 MiB, checked **before** the read so a hostile length cannot make the container
allocate), 422 (not a valid `RunState`, with the validation detail), 500 (any
exception from the agent, with type and message), 200 on success.

Accepts the state bare or wrapped as `{"state": ...}`. Returns
`{"agent": role, "result": ...}` — the role is echoed because during a
five-runtime deploy the most likely failure is invoking the wrong one, and
`agent_client` checks it.

`model_dump(mode="json")` is required; `model_dump()` alone returns objects
`json.dumps` cannot encode.

**Failures are not swallowed here.** The agents already absorb every model-side
failure, so an exception reaching this layer means something they deliberately did
not handle — turning it into a 200 with an empty result would recreate this
project's signature defect.

`AGENT_ROLE` **raises** when unset or unknown, at startup and per request. No
default: a default would mean a misconfigured runtime silently serving the wrong
agent's results with every response looking successful.

### The container image

One arm64 image, five ECR tags, differing only by `AGENT_ROLE`.

- **arm64 is not optional.** AgentCore runs arm64; an amd64 image pushes, deploys,
  then fails to start with an exec format error that reads like a broken entrypoint.
- **Base from ECR Public**, not Docker Hub — CodeBuild pulls anonymously and Docker
  Hub answers `429 Too Many Requests`, late in the build, for a reason unrelated to
  this repo.
- **`COPY fixtures ./fixtures` is required.** `fixtures_loader` resolves from the
  **repo root**, so `pip install .` never ships it. Measured on the first runtime
  that served traffic: `/ping` answered 200 and every `/invocations` died with
  `FileNotFoundError: '/app/fixtures/plan_result.json'`.
- **Two build-time checks, both mandatory.** The scanner version tail
  (`gitleaks version && trivy --version && semgrep --version`) catches a binary
  that downloads but cannot execute; the import smoke test runs from `/` so a
  missing subpackage cannot be masked by the working directory.
- semgrep lives in `/opt`, not `/tmp` — `/tmp` is not guaranteed to survive between
  build and run, and a symlink into a wiped directory presents as a **broken**
  scanner, which blocks every run.
- `git` is installed deliberately: `github_ops.open_pr` shells out to real `git` on
  the offline path.
- `PyGithub` and `boto3` are in `requirements.txt` because the spec's three lines
  build an image that dies at import — `github_ops.py:35` is module-level and
  unconditional, and `graph.py` imports it.

---

## The cloud pipeline — `run-pipeline.yml` + `scripts/run_stage.py`

`workflow_dispatch` **only**. A `push:` trigger would invoke five runtimes and open
a pull request on somebody else's repository on every commit to this one. The
EventBridge target dispatches this same event through the REST API.

Four inputs, all strings on the wire: `ticket_id` (required), `ticket_text`
(required), `poisoned` (default false), `auto_approve` (default false) — plus a
fifth, `trigger` (type string, default `manual`), added 2026-08-22.

**`trigger` exists because no Actions field can answer "how did this run start?"**
EventBridge dispatches through the same REST API `gh workflow run` uses, so an
auto-started run and a typed one both report `event: workflow_dispatch` —
measured on run `32542152671`, started by opening issue #15. The ingress
`input_template` sends `"trigger": "issue"`; a hand dispatch leaves the default.
**The two values must DIFFER**, and `tests/test_trigger_provenance.py` asserts
that: identical values would make a run recording the value indistinguishable
from a run whose trigger was never set, so the field would be present, populated
and worthless. The evidence is asymmetric and the tests say so — a run recording
`issue` was sent by the rule, because nothing else sends that string, but
`manual` may be a hand dispatch **or** a caller that forgot.

### The jobs

| Job | needs | AWS creds | Environment | artifact in → out |
|---|---|---|---|---|
| `plan` | — | yes | — | — → `run-state-<id>` |
| `gate1` | plan | no | `gate1` | `run-state-<id>` → `-gate1` |
| `develop` | plan, gate1 | yes | — | `-gate1` → `-develop` (`if: always()`) |
| `gate2` | plan, develop | no | `gate2` | `-develop` → `-gate2` |
| `sre` | plan, gate2 | yes | — | `-gate2` → `-sre` (`if: always()`) |
| `gate3` | plan, sre | no | `gate3` | `-sre` → `-gate3` |
| `promote` | plan, gate3 | no | — | `-gate3` → `-final` |

`promote` is the only pipeline job with **neither** AWS credentials nor the GitHub
seam. The gates hold the GitHub seam but no AWS: recording an approval reaches no
runtime — and without `DEMO_REPO`/`GITHUB_TOKEN` the gate comment degrades to
`local://`, so the approval a judge is looking for never appears while the job
still reports green.

Every upload sets `if-no-files-found: error`. The default `warn` publishes an
**empty artifact as a successful step**, and the next job's download then succeeds
against nothing.

`if: always()` on `develop`'s upload is the one place it is needed: a blocked run
exits non-zero on purpose, and its state — carrying the verdict, the findings and
the PR url — is the most important artifact the workflow produces.

### The three rejection recorders

A rejected Environment makes GitHub **skip** its job, not run it with a verdict.
So nothing inside a gate job executes on a refusal, and a branch in there could
never record one. Hence three separate recorder jobs.

```yaml
gate1-rejected:  if: always() && needs.plan.result    == 'success'
                    && needs.gate1.result != 'success' && needs.gate1.result != 'cancelled'
gate2-rejected:  if: always() && needs.develop.result == 'success'
                    && needs.gate2.result != 'success' && needs.gate2.result != 'cancelled'
gate3-rejected:  if: always() && needs.sre.result     == 'success'
                    && needs.gate3.result != 'success' && needs.gate3.result != 'cancelled'
```

**`!= 'cancelled'` IS A THIRD CAUSE, and it was missing until 2026-08-22.** GitHub
gives a non-success job three results and the recorders treated two of them as one:

| result | meaning | who records it |
|---|---|---|
| `success` | approved | the gate job itself |
| `skipped` | **a reviewer refused** | the recorder — its whole reason to exist |
| `cancelled` | **nobody decided** | nothing; the run's own status is the ending |

MEASURED, run `32575709109`: the poisoned run was cancelled at gate1 — two runs
contending for one concurrency slot — and the recorder posted **`REJECTED by
mohamedsorour1998`** to issue #37, naming a human who never saw the gate, then exited
4. **The upstream-stage clause does not catch this**, because a cancelled run's
upstream stage usually DID succeed. Fabricating a decision against a person's name is
the inverse of the defect this job exists to prevent.

The recorded reason also said *"was refused, **or** its job did not complete"* —
honest hedging when the recorder genuinely could not tell, and unreadable as a result:
a person is told a decision was recorded against their name and then told it might not
have been a decision. With `cancelled` excluded there is one cause, so it names that
one and says the change was not merged. Pinned by
`test_the_recorded_refusal_reason_names_one_cause_not_two`, which fails on the word
`" or "` — the hedge itself is the thing being forbidden.

**The upstream-stage clause is a discriminator, not redundancy.** A gate the run
never reached is *also* skipped, so `needs.<gate>.result` alone reads identically
for "a human refused this" and "the run stopped earlier". If the preceding stage
SUCCEEDED, the only remaining reason the gate did not run is the human. It also
keeps each recorder's download honest — the artifact it reads is that stage's.

**MEASURED, run `32509257195`:** a poisoned run blocked at `develop` recorded
`status=blocked` correctly, then `gate2-rejected` fired on gate2's `skipped` and
overwrote it with `status=rejected` attributed to a human who never saw the gate.
The block — the one thing that demo beat exists to show — was erased by the job
written to preserve refusals. `gate3-rejected` failed louder: with `sre` skipped
there was no `-sre` artifact, so it died in `download-artifact` having recorded
nothing.

`_stage_gate_rejected` also refuses in code when the state it loads is already
terminal, returning `EXIT_ALREADY_FINAL`. Defence in depth: a future workflow edit
cannot re-erase a block.

### Exit codes

| Code | Meaning |
|---|---|
| `0` | stage completed, run advances |
| `1` | uncaught exception — a crash |
| `3` | the deterministic block rule blocked the run — **the pipeline working** |
| `4` | a human refused a gate, or the revision cap exhausted, or SRE `no_go` |
| `5` | a recorder was asked to overwrite a run that had already ended |

`3` is deliberately not `1`: sharing it would make the poisoned demo run
indistinguishable from a broken workflow on the projector.

### The string-typed booleans

`workflow_dispatch` inputs arrive as **strings**, booleans included, and the REST
dispatch API rejects real JSON booleans inside `inputs`. So `run_stage.flag`
parses text, accepts `"true"` / `"false"` / `""` (empty means absent), lower-cases
but does **not** strip — whitespace means something upstream is mangling the input
— and **raises** on anything else. `poisoned=yes` must be a loud error, not a quiet
clean run.

### The run_id guard

```bash
run_id="$(grep -m1 '^run_id=' stage.log | cut -d= -f2 || true)"
if [ -z "$run_id" ]; then
  echo "::error::the plan stage printed no run_id; nothing downstream can find its state"
  exit 1
fi
```

The `|| true` is what makes the guard **reachable**. Under `set -euo pipefail`, a
log with no `run_id=` line makes grep exit 1 and kill the step immediately — so
without it the guard fired only for a literal empty `run_id=` line, never for the
missing-line case its own message describes.

### `ticket_id` must be the bare issue number

Measured on the clean verification runs: dispatching `ticket_id=CLEAN-VERIFY` logs

```
[post_comment] ticket 'CLEAN-VERIFY' is not an issue number, so there is no
issue to comment on
```

and the plan and gate1 comments go **nowhere, silently, while every job stays
green**. The EventBridge template sends the real number, so the auto-triggered
path is correct; a hand dispatch with a label is not.

---

## The ingress Lambda — `infra/ingress/handler.py`

The flow, in order. **The HMAC check is step 5, and everything that costs money or
mutates anything is after it.**

```
1. method != POST                      → 405   (not 401: a crawler probe must not
                                                read as a signature failure)
2. x-hub-signature-256 missing/malformed → 401
3. raw body unreadable                 → 401
4. secret unreadable                   → 500   (deliberately not 401 — "your
                                                signature failed" would be a lie)
5. compare_digest fails                → 401
   ── VERIFIED. ONLY NOW DOES ANYTHING HAPPEN. ──
6. body is not JSON                    → 400   (the App is on x-www-form-urlencoded)
7. put_events raises                   → 500
8. FailedEntryCount truthy             → 500   (HTTP 200 with a dropped entry)
9.                                     → 202
```

Steps 1–3 precede the secret fetch entirely: an anonymous caller must not be able
to drive `GetSecretValue` calls against a public endpoint.
`tests/test_ingress_handler.py` asserts **zero `PutEvents` on every reject path**,
and proves that assertion is not vacuous by replaying a valid delivery through the
same stub.

### The four traps, all handled

- **RAW BODY.** Nothing between reading it and `hmac.new` touches the bytes. A
  `json.dumps(json.loads(body))` round-trip renormalises whitespace and key order
  and 401s every delivery.
- **`isBase64Encoded`** is honoured, decoded before the HMAC, with
  `validate=True`. No opportunistic decode when the flag is false — a JSON body can
  be valid base64 by accident.
- **Header case.** `_header` lower-cases incoming keys itself, so behaviour does
  not depend on the integration in front (Function URL lower-cases; API Gateway
  REST and ALB do not).
- **`compare_digest`, never `==`.** `==` returns early at the first differing byte,
  leaking position through timing. Both sides are encoded to bytes, because
  `compare_digest` raises `TypeError` on a str with non-ASCII — `sha256=é` would
  502 instead of 401.

`DetailType` is the `x-github-event` header **verbatim**. That is the coupling to
the rule's `detail-type: ["issues"]` — invent a value here and the rule matches
nothing, the bus still accepts, and nothing turns red.

Secret accepts two shapes: a bare string, or JSON read at key `webhook_secret`. A
JSON secret with a misspelled key **raises** rather than falling through to the
whole document as the HMAC key. `if not secret.strip()` catches whitespace-only,
which is a guessable key with the same universal-forgery hazard as empty.

---

## The infrastructure — `infra/Terraform/`

Three modules under one root at `environments/shared/`. S3 backend
`theagentorg-shared-terraform-backend`. Applied by `terraform.yml`, never from a
laptop — a local apply leaves the account agreeing with one person's working
directory rather than with main.

### `modules/agentcore`

Five ECR repositories `theagentorg-shared-<agent>-agent` (image scanning on,
lifecycle keeps 5), plus `theagentorg-shared-agentcore-runtime-role` with four
statements: logs, `bedrock:InvokeModel*` on foundation models,
`bedrock-agentcore:InvokeAgentRuntime`, and ECR pull.

**The AgentCore runtimes themselves are not Terraform** — `deploy.yml` creates them
through `bedrock-agentcore-control`. This module lays down the registries and the
role.

### `modules/ingress`

Always created: the Lambda (python3.12, **arm64**, 10s, 256 MB, **reserved
concurrency 2** as a spend cap), its Function URL (`authorization_type = "NONE"`),
the log group (14-day retention), the webhook secret **container only — Terraform
never writes the value**, the event bus, the rule, and an IAM policy with exactly
three statements:

| Sid | Actions | Resource |
|---|---|---|
| `OwnLogGroupOnly` | `logs:CreateLogStream`, `logs:PutLogEvents` | that one log group `:*` |
| `ReadTheWebhookSecretAndNothingElse` | `secretsmanager:GetSecretValue` | that one secret ARN |
| `PublishToTheIngressBusAndNothingElse` | `events:PutEvents` | that one bus ARN |

No wildcard resource anywhere, including logs. `logs:CreateLogGroup` is **absent**
— the group is Terraform-managed — and `AWSLambdaBasicExecutionRole` is
deliberately not attached because it grants logs on `*`.

**The Function URL is internet-reachable and unauthenticated at the AWS layer.**
GitHub cannot sign SigV4, so `AWS_IAM` would reject every delivery; NONE is the
only option that works. The provider auto-adds the public
`lambda:InvokeFunctionUrl` permission, which is why no `aws_lambda_permission`
resource appears — adding one would be redundant, not protective — and those
policies are **not removed on destroy**. Three defences, in order: the HMAC,
reserved concurrency, and IAM narrowed to two actions on two ARNs.

The rule's pattern:

```json
{"source": ["github.webhook"], "detail-type": ["issues"], "detail": {"action": ["opened"]}}
```

`action: ["opened"]` filters **at the bus**, not in the handler, so every Issues
delivery is recorded but only an opened issue starts a run. Filtering in the
handler would make "we never saw it" and "we saw it and ignored it"
indistinguishable.

**Count-gated on `dispatch_token_secret_name`:** the connection (API_KEY), the API
destination (rate limit 1/s), the target, its role and policy, and the DLQ. The
gate exists because an API_KEY connection needs the token's **value at PLAN
time** — an ungated read of a secret nobody has written yet fails the *plan*, which
would turn `terraform.yml` red on every run until somebody minted a token.

The input transformer sends, with **every value quoted** because the dispatch API
rejects real JSON booleans:

```
ref          → main
ticket_id    → the issue number
ticket_text  → the issue title
poisoned     → the literal string "false"
auto_approve → the literal string "false"
```

`poisoned` is **hardcoded, not read off the payload**: a label is attached *after*
an issue is opened, so `$.detail.issue.labels` is reliably empty on the matched
event — reading it would produce a clean run while appearing to honour the label.
The issue **body is deliberately unused**: unbounded, may hold anything, and goes
straight into an agent prompt.

The auth scheme belongs in the **value**, `"Bearer <token>"`. EventBridge sends
`<key>: <value>` verbatim, so `key = "Bearer"` sends `Bearer: <token>`, which
GitHub ignores — and an ignored auth header on this endpoint answers **404, not
401**, which reads as "the workflow does not exist" and sends the next person
looking for a missing file.

### `modules/state`

DynamoDB `theagentorg-runs`, PK `run_id`, SK `ts_event_id`, PAY_PER_REQUEST, PITR
and SSE on, no GSI. Only key attributes are declared, so a new optional pydantic
field is not a second declaration.

The sort key includes `event_id` because **`PutItem` replaces at a (pk, sk)
pair**: two events in the same clock tick share a timestamp, so a timestamp-only
sort key would silently overwrite one and the log would come back a row short with
nothing raised. The local JSONL append cannot lose a line this way — exactly why
this is the one place the two backends genuinely differ.

IAM grants exactly `PutItem`, `Query`, `GetItem`, `UpdateItem` on that one table.
**`Scan`, `DeleteItem` and `BatchWriteItem` are deliberately absent** — the table is
an audit trail.

---

## The test suite

**67 test files** as of 2026-08-28, after Phase 1, Phase 2 and Lane H
(`ls tests/test_*.py | wc -l`), plus
five non-test modules in `tests/`: `conftest.py`, `provenance.py`, `dora_runner.py`,
`dora_batch.py`, `dora_table.py`. **This number went 41 → 46 → 51 → 55 in a single day**
as five lanes committed, and three of those figures were written into this file while
true. Measure it; do not read it. The per-file counts below were measured with
`--collect-only`; the table lists the largest and the ones whose subject matters,
not every file.

| File | Covers | Tests |
|---|---|---:|
| `test_deploy_workflow.py` | `deploy.yml` + `terraform.yml` blast radius — the two files that can spend money | 106 |
| `test_approve_server.py` | the approval screen, mostly what it **refuses** | 95 |
| `test_scanner_resilience.py` | the ABSENT/FAULT matrix, inside-out | 82 |
| `test_scoring_failclosed.py` | an unrecognised severity must still BLOCK, per scanner + the threshold floor | 30 |
| `test_scoring_determinism.py` | the determinism claim, exhaustive — and the ranking anchor | 27 |
| `test_scoring_table.py` | one table for three scanners; the gitleaks constant as policy, over the AST | 18 |
| `test_run_pipeline_workflow.py` | the cloud pipeline's blast radius and ungameable gates | 68 |
| `test_ingress_handler.py` | the webhook Lambda — HMAC first, EventBridge second | 49 |
| `test_agentcore_deploy_assets.py` | requirements / Dockerfile, unexerciseable locally | 49 |
| `test_agent_client.py` | the remote seam | 43 |
| `test_tenancy_leak.py` | **THE LEAK SUITE** — attempts a cross-tenant breach on every registered accessor | 52 |
| `test_api_auth.py` | K5 — what the machine-key layer refuses; the empty store, and two orderings over the AST | 31 |
| `test_api_ingress.py` | K4's three providers + K6's schema, incl. the dispatcher-vs-document loop | 30 |
| `test_api_cancel.py` | K7 — cancellation at all three positions, and K3's threshold floor | 25 |
| `test_api_submission.py` | K7 — idempotency under retry, and the gate refusal per module over the AST | 20 |
| `test_tenancy.py` | the tenancy schema, the engine, the migrations | 33 |
| `test_tenancy_secrets.py` | per-tenant secret crypto; greps the module's own logs for the plaintext | 29 |
| `test_tenancy_budgets.py` | budgets fail closed; tenant zero loses nothing | 22 |
| `test_state_backend.py` | the dynamodb backend, hostile `run_id` refusal | 33 |
| `test_timeline.py` | the run timeline, HTML escaping, delivery refs | 32 |
| `test_packaging.py` | what a NON-editable install actually ships | 31 |
| `test_ingress_terraform.py` | the ingress module's security properties as data | 30 |
| `test_offline_mode.py` | a real local branch and NOTES file, no network | 25 |
| `test_agent_fallbacks.py` | every agent degrades to its fixture | 25 |
| `test_gates_cli.py` | interactive halt, async resume, resumability | 22 |
| `test_trigger_provenance.py` | `trigger` provenance + `SECURITY_BLOCK_THRESHOLD` at import | 20 |
| `test_llm_helper.py` | JSON extraction, disabled path, KeyboardInterrupt | 20 |
| `test_deploy_note.py` | reports the real deploy or admits it cannot | 19 |
| `test_agent_comments.py` | one labelled comment per stage; issue-vs-PR routing | 19 |
| `test_repo_snapshot.py` | the shared repo view: clone, TTL, the after-diff view | 23 |
| `test_sre_agent.py` | CI decides, the model advises — and the schema it is asked for | 20 |
| `test_issue_lifecycle.py` | the issue links its PR, learns the ending, and closes | 13 |
| `test_dora_batch.py` | the headline claim under test | 14 |
| `test_agentcore_iam.py` | the inference-profile grant + the deploy smoke test's discriminator | 11 |
| `test_dora_harness.py` | the harness's raw numbers | 10 |
| `test_ingress_dispatch_target.py` | connection, API destination, input transformer | 9 |
| `test_functional_contract.py` | every result matches the frozen schema | 9 |
| `test_provenance.py` | the discriminator itself — **source of all 3 skips** | 7 |
| `test_retrieval_boundary.py` | **H7** — ATTEMPTS the breach: five hostile documents through the real block rule | 33 |
| `test_retrieval_provenance.py` | H1's four values, the fault-vs-choice split, and the synonym limit as a test | 28 |
| `test_retrieval_measure.py` | H6's harness — the cases carry their trait; the arms differ in one thing | 18 |
| `test_integration_conformance.py` | **THE CONFORMANCE SUITE** — three adapters, one set of test bodies, none naming its adapter | 42 |
| `test_integration_interface.py` | what `CodeHost` and `host()` REFUSE, plus the delegation claim over the AST | 24 |
| `test_block_shape_stability.py` | field/type fingerprint stable over 10 runs | 6 |
| `test_chaos_scanner.py` | broken scanners from OUTSIDE the pipeline | 5 |
| `test_pipeline_smoke.py` | stubbed pipeline end to end | 3 |
| `test_block_determinism.py` | poisoned → blocked, 20 consecutive runs | 3 |
| `test_baseline.py` | the no-checks "before" | 3 |
| `test_chaos_gate_and_loop.py` | a gate that never returns must fail safe | 2 |

Counts above are per-file and were measured with `--collect-only`; the **total is
deliberately not restated here**, because four lanes were adding tests
concurrently and a total goes stale the moment any of them commits. Run
`pytest --collect-only -q | tail -1` rather than trusting a number in prose.

Support modules: `conftest.py` (the guards), `provenance.py` (the discriminator),
`dora_runner.py`, `dora_batch.py`, `dora_table.py`. **`tests/README.md` is stale** —
it assigns `test_functional_flow.py`, which does not exist.

### The six autouse guards in `tests/conftest.py`

Every one forces the offline path, then puts a loud raiser on the seam underneath.
**Do not weaken them.**

1. **Model (Bedrock)** — `config.LLM_DISABLED = True` and `llm._complete` → raiser.
   Without it, `pytest -q` on a machine with AWS credentials makes a live billable
   Bedrock call per agent per pipeline test. CI never caught it because CI has no
   credentials.
2. **GitHub** — `config.OFFLINE = True` and `github_ops._repo` → raiser. This seam
   **writes**: measured before the guard existed, four outbound connections to
   `api.github.com` per run, performing real branch/commit/PR writes.
3. **Offline workspace** — redirects `OFFLINE_REPO` and `OFFLINE_NOTES` at
   `tmp_path`. Guard 2 makes every test do real local `git`, and both knobs default
   under `runs/` inside this repo. Measured with the fixture removed: **224 git
   child processes, 125 with cwd inside the repo, from 22 tests that never mention
   git**, leaving a real nested repository with six branches behind.
4. **Terminal** — `builtins.input` → raiser. Under `pytest -s` an unpatched
   `input()` blocks the whole suite with no failing test to point at.
5. **Scanner cache** — clears the fan-out memo on **both** sides of every test. A
   stale cache hit looks exactly like a scan. This one exists because a previous
   file-scoped version *predicted its own gap* in a docstring and named the
   condition that would end it; a second lane then did exactly that, and three
   tests failed in the full suite while passing alone.
6. **The repository clone** — `repo_snapshot.snapshot` → `dict`, cleared on both
   sides. **GUARD 2's HISTORY REPEATING ON A NEW SEAM.** `repo_snapshot` shallow-clones
   the target repo so every agent can see it, and three tests set a non-empty
   `GITHUB_REPO` and then drove `run_pipeline` — so `pytest -q` made real outbound
   clones to github.com. Stubbed at `snapshot`, not at `subprocess`: patching
   subprocess would leave `_read_tree` walking a directory that does not exist and
   would test our git invocation rather than what the agents do with the result, which
   is where every measured defect was. A test marked `real_snapshot` opts out (those
   stub `subprocess.run` in their own bodies, so they never reach the network either).

**Why `pytest.fail` and not a plain exception.** `Failed` derives from
**BaseException**, not Exception. `llm.text()` catches `Exception` and
`github_ops.post_comment` catches `Exception`, so an ordinary raiser would be
**swallowed into the fixture branch and the test would pass green** — exactly the
bug the guard exists to catch. Downgrade one and `post_comment` absorbs it while
the live writes go out. **Placement is not what saves those paths; `pytest.fail`
is.** Both properties are pinned by real tests, not by prose:
`test_keyboard_interrupt_is_not_swallowed` and
`test_the_blind_except_does_not_swallow_the_conftest_github_guard`.

A test that wants a real seam opts in **in its own body**, replacing all of the
layers — the policy knob *and* the seam function. Opting in with only the policy
knob reproduces the exact bug. And an opt-in raiser must be **finite**: `lambda *a:
"a"` answers every prompt forever, so a gate that starts asking twice is answered
silently rather than caught.

### The testing discipline — this project's real character

**Every test change carries a mandatory RED step.** Name the exact mutation, apply
it, watch the exact named test fail, paste the failure, revert. Nineteen-plus
assertions in this repo turned out to pin nothing. A test that cannot fail is worse
than no test, because it reads as coverage and stops anyone from looking.

**When you change a mechanism, tests referencing the old one do not fail — they
stop testing.** Any test whose matcher can match nothing must assert that it
matched. The operational form is everywhere in the suite: `assert server.AGENTS,
"server.AGENTS is empty; this test would pin nothing"`.

**A number that matches is not evidence the right code is under test.** Recorded
verbatim from a real incident: `47 passed` both before and after a stub fix, and
only the mutation produced `1 failed, 46 passed`.

**Numbers in prose must come from a command whose output you paste.**

### The pattern found TWELVE times across four layers

> **A test double, a helper, an inference, or a measurement that cannot express the
> failing case produces confidence that cannot be falsified — and reading it never
> reveals that.**

The instances, briefly:

- **A stub that could only emit `json.dumps`.** So no test could express a
  malformed body at all. Three refusal paths in `agent_client` were uncovered; a
  mutation that fabricated an envelope for an empty body returned a fully validated
  `PlanResult` with the file green.
- **A helper that blanked heredocs.** `_strip_comments` erases heredoc bodies —
  correct for its purpose — but `input_template` *is* a heredoc. A test written over
  the stripped text searched for `"poisoned": "false"` in text from which the whole
  template had been erased, matched nothing, and passed.
- **A `tee`-shaped stub that changed the failure's context.** Under `pipefail` the
  pipeline's status came from `tee`, so the stub could not reproduce the assignment
  inheriting status 1. The test passed against both the fix and the bug.
- **A shared expected-counts constant.** `_PROMOTED_RUN_COMMENTS` keeps the local
  and cloud paths from drifting, and it works — but it declares `develop: 1,
  review: 1`, so it **structurally forbade the only run shape that could catch a
  third bug** (per-pass rendering of the revision loop). A real control and a blind
  spot in the same line.
- **A number committed as "measured" that the next run could not reproduce.**
  116.88s → 149.68s → 102.83s for the same 793-test snapshot, load-dependent. So
  "measured" is a property of a number **plus its conditions and spread** — quote a
  range, not a point.
- **My own inference** that `{3,4}` proved the *built image* scans. It proves
  real-from-fixture, **not where it ran**.
- **A guard whose own record destroyed the evidence it protected** — see the
  timeline-banner note below.
- **A test satisfied by the comment explaining the thing it was checking.** Found
  TWICE in one lane on 2026-08-22, and it is the most repeatable form of this
  pattern in a codebase whose files are 40% commentary:
  - `deploy.yml`'s new smoke check assigns `fixture_note='<the fixture's notes>'`,
    and the surrounding comment quotes that same literal as the measured output of
    the command that read it. A test asserting the literal appeared anywhere in the
    step's `run` body passed while the assignment was changed to a different
    sentence — which destroys the discriminator entirely.
  - `config.py`'s threshold validation carries a comment saying "SEVERITY_ORDER is
    imported, not restated". `assert "SEVERITY_ORDER" in source` was satisfied by
    that sentence, so replacing the import with a hardcoded severity tuple left all
    19 tests green.

  Both were caught **only by running the mutation**, and both fixes have the same
  shape: assert over a **comment-stripped** form or over the **AST**, and add a
  guard that the stripping still works. Reading either test would never have
  revealed the gap — which is the whole pattern.

- **A test that REQUIRED the bug.** Three tests read `run-pipeline.yml`'s text to check
  the per-run revision cap, and one asserted that `== 'true'` was PRESENT — so it passed
  on an expression that never fired and would have failed on the fix. This is the first
  instance where a test did not merely miss a defect but actively defended it, and the
  only witness was a deployed run printing `POISONED: true` beside
  `MAX_REVISION_LOOPS: 3`. A workflow expression cannot be tested by reading the
  workflow; the runner's type coercion IS the behaviour. Replaced with four tests that
  evaluate the expression across both dispatch shapes.

- **Sixteen SRE tests that never ran pydantic.** Every one stubs `llm.structured` to
  return a ready-made `SREResult`, which is the correct isolation for "what does the
  agent do with advice" — and it meant no test validated a real reply against the
  schema the agent asks for. The agent asked for `SREResult` while its prompt told the
  model not to fill two of that model's required fields, so **every obedient reply was
  rejected and the fixture was served, on every call**, with all 16 green. Caught by
  reading `_source=fixture` on the deployed run, not by the suite. The fix's tests run
  the real validation over the real prompt and assert on the CLASS the agent passes,
  not on source text — a comment naming `SREAdvice` would satisfy a grep while the
  call still passed `SREResult`.

- **AN INERT MUTATION READS EXACTLY LIKE A PASSING ONE** — the tenth instance, and the
  first found in the RED step *itself* rather than in a test. Writing
  `scripts/measure_dependencies.py`, the mutation chosen to prove its self-check works
  was removing a `break` from an inner `ast.walk` loop. Output: **identical**, `1` hard /
  `3` deferred, exit 0. That guard is unreachable on this codebase — no module nests a
  `def` deeply enough under a non-`def` top-level statement for it to matter. Had it been
  accepted, the self-check would have been recorded as verified without ever firing.

  The mutation that moves the answer is dropping the **outer** barrier: `4` hard / `0`
  deferred, `REFUSING`, exit 1. Both facts are now in that module's docstring, because
  the discipline says *paste the failure* — and pasting **no** failure looks the same as
  not having run the step.

  **A RED step must be shown to change the output, not merely to have been applied.** If
  a mutation leaves the result byte-identical, it did not test what you think; pick
  another one and say so.

- **A PROPERTY TEST THAT READS THE TABLE UNDER TEST** — the eleventh instance, and the
  first where the blind spot was the *test suite's own expectation source*. Twenty-five
  determinism tests, all deriving their expectation from `SEVERITY_ORDER`, could not see
  `SEVERITY_ORDER` change: transposing `high` and `critical` left **24 green and turned
  the 25th into a skip**, while a committed credential silently stopped blocking. Full
  write-up under the block rule above.

  The general form: **a property is only as strong as the independence of the oracle it
  is checked against.** "Same inputs, same output" is satisfied by any consistent wrong
  answer, so pin the VALUE somewhere the mutation cannot follow, or assert the
  CONSEQUENCE rather than the mechanism. And an **inert mutation was found in this same
  lane**, from copying `SEVERITY_ORDER`'s text out of CLAUDE.md, which omitted its type
  annotation — the substitution matched nothing and the suite stayed green.

- **A RED STEP THAT DELETED A TEST RATHER THAN FAILING ONE** — the twelfth instance, and
  the same root cause as the eleventh one layer down: a **parametrisation** whose case list
  comes from the thing under test. Lane H's H7 suite parametrised its attack over
  `guard.VERDICT_ARGUMENTS`; dropping `"threshold"` from that set — the one argument
  `compute_security_verdict` actually accepts — took the file from **32 passed to 31
  passed**. Nothing failed. `31 passed` reads like a clean run.

  The fix is Lane C's: restate the names as a **literal**, add an anchor asserting the two
  agree in BOTH directions (missing from the guard is a hole; present in the guard but never
  attempted is a stale literal), and assert the load-bearing name separately. Re-run: `2
  failed, 31 passed`. **`pytest -k` is not the only way a selection silently empties — a
  parametrisation derived from the code under test does it too, and the count still looks
  healthy.**

Three more mutations survived 793 tests, all in the cloud path, every one a case
where `run_stage.py` inherited `graph.py`'s **comment** about a hazard but not its
**test**: `return EXIT_BLOCKED → EXIT_OK` (with which the poisoned run reaches
`status='promoted'`), `artifact_ref=ref → "comment://"`, and the flush loop
re-reading `state.dev`.

**One tenancy mutation survived, and it is a REDUNDANT layer rather than an untested
one** — recorded because the distinction is the whole point of running the mutation.
Removing the second `AND "tenant_id" = ?` from `add_spend`'s UPDATE left all 52 leak
tests green. Probed directly rather than assumed: `_require` runs first and its own
predicate is satisfiable only when the named tenant equals the scope, so by the time
the UPDATE runs the two placeholders are necessarily the same string —
`add_spend(scope_t1, "t2", 500)` raises `CrossTenantAccess` with `t2`'s
`spent_cents` still 0. So that clause is a third layer behind `_require` and the
trigger, not a gap. **A surviving mutation means "find out why" and not "add a
test".**

**A check that cannot distinguish "did not run" from "passed" is the defect this
whole project exists to prevent.** Same for "denied" versus "not ready yet".

---

## Verified runs — what the cloud path has actually done

### The demo pair — verified at runtime version 18, all five agents on the model

The current pair. Both scenarios re-run after the SRE schema fix below, which is the
first run where **every stage of both halves reported `_source=model`**.

| | Clean | Poisoned |
|---|---|---|
| Issue | #41 — **CLOSED / COMPLETED** | #43 — **CLOSED / NOT_PLANNED** |
| Issue comments | `plan`, `gate1`, `outcome` | `plan`, `gate1`, `outcome` |
| PR | **#42 — MERGED** | **#44 — open, blocked** |
| PR body | `Closes #41` | `Closes #43` |
| PR comments | develop · review · security · gate2 · sre · gate3 | develop · review ×2 · security |
| Security | `PASS`, `provenance: scanners` | `BLOCK`, `provenance: scanners`, `app/auth.py:3` and `:4` |
| Provenance | `_source=model` at plan, develop, sre, promote | `_source=model` at plan, develop |
| Jobs | all seven green | `develop` **exit 3**, everything after skipped |
| Recorders | all three skipped | all three skipped |

The clean run was **auto-triggered by opening the issue** — run `32580985840`,
`TRIGGER: issue`, no command typed. The poisoned run was hand-dispatched
(`32581285927`) because `poisoned` is hardcoded `"false"` in the ingress transformer.

**`CI unknown` on the clean half was a real defect, now fixed** — the SRE was asking
GitHub from inside a container with no token. See `RunState.ci_status_measured`. The
change still merged either way, because `unknown` yields `go`.

**The poisoned run now shows TWO review rounds, not four.** `MAX_REVISION_LOOPS` is `1`
when `poisoned` — the run above was measured at the old value of 3. A poisoned run
cannot converge, so the extra rounds added no evidence and read on a projector as a
developer agent that could not follow instructions. It was in fact complying every
pass: four DIFFERENT model summaries, with the safety net re-substituting the key each
time. The block, the two findings and the line numbers are unchanged — the security
verdict is computed once, after the loop, over a diff that always carries the key.

**Issue #37 is a pre-fix artifact and is not evidence of anything.** Kept, closed by
hand, with a comment explaining each symptom, because all four of that morning's
defects are visible on one issue: two plan comments (the dispatch/auto-trigger race),
a `REJECTED` followed by an `APPROVED` (the recorder firing on `cancelled`), no
outcome comment, and no linked PR — #38's body predates `Closes #<n>` by an hour. Do
not read #37 as a demo run. The pair is #41/#43.

**The issue is now a complete record on its own.** `Closes #<n>` in the PR body
populates GitHub's Development sidebar — verified through the GraphQL timeline, which
reports a `CrossReferencedEvent` for the PR and a `ClosedEvent` with the reason. An
issue previously learned only how a run BEGAN and stayed open forever.

**Dispatching the poisoned run races the auto-trigger.** Creating an issue fires a
CLEAN run within seconds, and `poisoned` is hardcoded `"false"` in the ingress
transformer by design — a label attaches after the issue opens, so the payload's
labels are reliably empty. So the poisoned run must be hand-dispatched, and if the
auto-run wins the concurrency slot first it posts its own plan to the same issue.
**That is what produced three plan comments on one issue during rehearsal** — three
separate runs against ticket 21, twelve minutes apart, each correctly posting once.
Not a loop. On 2026-08-22 the poisoned dispatch and the auto-run both started on issue
#43 five seconds apart; `gh run cancel` on the auto one left the issue with a single
clean record. **Cancel the auto-run rather than racing it** — that is the reliable
move, and it takes one command.

### A TEST THAT REQUIRED THE BUG — the worst instance of the pattern so far

`MAX_REVISION_LOOPS` is set per run in `run-pipeline.yml`: `1` when poisoned, `3`
otherwise. The first version of that expression **never applied**, and the tests were
worse than absent.

`poisoned` is declared `type: boolean`, so in an **expression** context
`inputs.poisoned` is a real boolean on a UI or `gh workflow run` dispatch — and
`== 'true'` against a boolean is always **false**. MEASURED on run `32585947588`, the
final verification run of the poisoned path:

```
POISONED: true
MAX_REVISION_LOOPS: 3        <- the CLEAN branch, on a poisoned run
```

Every run silently took the clean cap and the demo ran four review rounds exactly as
before. **Three tests read the workflow TEXT and passed throughout — and one of them
asserted that `== 'true'` was PRESENT**, so it passed on the broken expression and would
have failed on the fix. It actively defended the defect. The previous commit's own comment
asserted the opposite of what the runner does, and the tests agreed with the comment
rather than with GitHub.

The condition is now a truthiness test that **also excludes the string `"false"`**, because
both input shapes are real: a REST dispatch (EventBridge, and every `gh api` call) sends
these inputs as JSON strings, and a non-empty `"false"` string is **truthy** to GitHub. A
bare `inputs.poisoned` would send clean API-triggered runs down the poisoned branch.

```yaml
MAX_REVISION_LOOPS: ${{ (inputs.poisoned && inputs.poisoned != 'false') && '1' || '3' }}
```

Four tests now **evaluate** the expression the way the runner does, across both dispatch
shapes, with a deliberately narrow evaluator that **raises on any operator it was not
written for** — an evaluator that silently mishandles an operator is the same false
confidence one level up.

**Two lessons.** A workflow expression cannot be tested by reading the workflow: the
runner's type coercion is the whole behaviour. And when a comment and a test agree with
each other but nobody checked either against the platform, the agreement is worthless —
only the deployed run could tell.

### THE SRE'S ADVICE WAS REJECTED BY THE SCHEMA IT WAS ASKED FOR

Found by reading `_source=fixture` on a `develop` and `sre` job whose measured output
was plainly real. `sre.run` validated the model's reply against **`SREResult`**, whose
`verdict` and `ci_status` are required Literals with no default — while
`SYSTEM_PROMPT` tells the model, correctly, that those two fields are **not its to
set**. A model that OBEYED the prompt therefore produced a reply pydantic rejected.
MEASURED against the deployed runtime, 3 of 3 calls:

```
verdict=go ci=unknown source=fixture
REJECTED: 2 validation errors for SREResult
verdict     Field required
ci_status   Field required
```

The advice was good — it named the new Redis dependency and the missing test for the
rate-limiting logic — and every word was discarded. `SREAdvice` now holds exactly the
three fields the prompt asks for, so **the model literally cannot express the two it
must not set**, which is stronger than dropping them afterwards. Not fixed by
defaulting `SREResult`'s fields: that model is the frozen contract every stage writes,
and a default there reads as a decision somewhere else.

**The 16 existing SRE tests all passed throughout**, because every one stubs
`llm.structured` to return a ready-made object — the right isolation for "what does
the agent do with advice", and exactly why no test ran pydantic over the real
prompt's contract. This is the seventh instance of the pattern below: a test double
that cannot express the failing case.

**Two probes that looked like bugs and were not.** A local probe of the reviewer
returned `source=fixture` with `prompt chars: 1977`; the same input returns `model`
with `18338` once `DEMO_REPO` is set. `config.GITHUB_REPO` reads env var **`DEMO_REPO`**
— the one name mismatch in `config.py` — so a probe exporting `GITHUB_REPO` gets an
empty snapshot and the agent reasons blind. And **the AgentCore runtime log groups
record only `GET /ping`**, never `/invocations`, so they cannot tell you whether an
agent was invoked. Read the agent's OUTPUT against its fixture instead: the reviewer's
fixture is always `app/auth.py:12` "Counter expiry looks right."

### THE DEVELOPER WAS WRITING GO FOR A FLASK APP

The clean run failed twice at the revision cap before this was found, with the
scanners reporting `PASS` each time. Reading the reviewer's objections showed why:
`sync.RWMutex`, `NewRateLimiter`, "standardize Redis key formatting in GetKey".

Neither prompt said what the target was. `developer._prompt` names target FILES but
never their contents, and `target_repo/` is excluded from the image
(`.dockerignore:48`), so the agent could not look. It guessed, and every revision
inherited the guess.

The reviewer's half cost as much. Its prompt already said "ONLY for real correctness
or safety problems… not style nitpicks", and it still blocked on a different storage
choice, a missing `Retry-After` header, absent cleanup timers, and configurability
nobody asked for. "Real correctness problem" is not an operational standard. Both
prompts now name the stack, and the reviewer's names what blocks and what belongs in
`comments` instead.

**An agent asked to edit a file it cannot read, with nothing saying what language
that file is in, is being set up to fail.** This was not prompt-tuning for a nicer
demo.

### 2026-08-22, after the model was unblocked — READ THIS FIRST

**Until this date every agent in the deployed pipeline had been answering from its
fixture.** Three independent IAM defects on one statement, each hiding the next:

| # | Defect | How it was found |
|---|---|---|
| 1 | `Resource` named `foundation-model/*` only, but `BEDROCK_MODEL` is an `inference-profile/` ARN | `simulate-principal-policy` |
| 2 | `Action` named `InvokeModel`, but `strands.Agent` calls **`ConverseStream`** | the container log named the operation |
| 3 | `Resource` scoped to `us-east-1`, but the profile routes to **us-east-2 and us-west-2 as well** | `get-inference-profile` |

Fixing each one exposed the next, because until then every call failed at the
earlier check. **The lesson: simulate the action the SDK actually calls, and read
the container log rather than trusting the simulation of an action you assumed.**

The proof it is fixed is not a green job — it is that the planner's output stopped
matching the fixture. Asked for a health-check endpoint it returned six tasks about
`/healthz`, build SHA and uptime; the fixture is a Redis rate-limiter, always.

**Poisoned run `32556734837`** — `plan → gate1` (clicked) `→ develop`, blocked:

```
_source=model                       ← the agents genuinely called Bedrock
status=blocked
blocked: 2 blocking findings
  gitleaks critical app/auth.py:3 aws-access-key-id
  gitleaks critical app/auth.py:4 aws-secret-access-key
exit code 3
```

All three rejection recorders `skipped`. The security comment's explanation is now
model-written prose naming both rules, both lines and the consequence.

**Clean run `32558580388`** — **all seven jobs green**, three gates each paused for a
click, `status=promoted`, and **PR #24 merged on auth-service**. Security read
`PASS — 0 blocking finding(s) of 0 total`, `provenance: scanners`.

**Automatic trigger** — opening issue #25 started run `32558837968` with nobody
typing a command:

```
Lambda:  accepted delivery 5c2c005c-9df8-11f1-9e48-0b1f5909ad6f (issues)
plan job:  TICKET_ID: 25
           TICKET_TEXT: Add structured request logging to the auth service
           TRIGGER: issue        ← the field no Actions data could provide
           _source=model
```

### Two defects the demo run itself exposed

**The provenance could not cross the remote seam.** The first post-fix run printed
`_source=none` beside a plan comment that was unmistakably model-written. Under
`REMOTE_AGENTS=true` the model call happens in the container and
`llm.last_source()` on the runner never sees it — so the field was blank on exactly
the path it exists to describe. `source` now travels on the 200 envelope, the way
`RunState.poisoned` travels on the state.

**`promote` merged nothing while reporting success.** Run `32558114927`: seven jobs
green, `status=promoted`, and no PR merged. `promote` held no `DEMO_REPO` or
`GITHUB_TOKEN` — correct when it only wrote a status, wrong once it called
`merge_pr`, whose offline path returns `local://<branch>`: a ref that reads like a
success. The test that should have caught it **exempted `promote` by name**, with
the stale reason in a comment. That exemption list is gone.

### A real reviewer now withholds approval, and that is not a bug

Clean run `32557597915` ended `status=failed`, exit 4, with `PASS` from security.
Four model-written review rounds: the reviewer asked for **email-based** rate
limiting, the developer kept producing **IP-based**, and the cap expired. The
scanners cleared the diff; nobody approved it. Before the model was unblocked this
path was unreachable, because `fixtures/review_result.json` always approves.

**Consequence for the demo:** the clean beat's ticket text must be specific enough
for the developer to satisfy. `"Add a per-IP rate limit of five login attempts per
minute to app/auth.py, returning HTTP 429 past the threshold. Read the limit and the
Redis URL from environment variables."` reaches `promote`. A vaguer ticket may
legitimately end `failed`.


Recorded because "deployed" and "verified" were separate facts for most of a week,
and several claims in this file were once written while only the first was true.

### 2026-08-22 — the poisoned half

Run `32540401814`. `plan → gate1` (paused, approved by click) `→ develop`, which
blocked. Produced **PR #11** on `auth-service` carrying three agent comments, the
security one reading verbatim:

```
### Agent Org · security
**BLOCK** — 2 blocking finding(s) of 3 total
_provenance: scanners_
- `gitleaks` **aws-access-key-id** (critical) at `app/auth.py:3`
- `gitleaks` **aws-secret-access-key** (critical) at `app/auth.py:4`
```

Lines **3 and 4** with `provenance: scanners` — from the deployed container, not a
fixture. `status=blocked` survived to the end of the run, and both rejection
recorders correctly **skipped**.

### 2026-08-22 — the clean half

Run `32540911270`, **all seven jobs green**: `plan → gate1 → develop → gate2 → sre
→ gate3 → promote`, three gates each paused for a click, all three recorders
`skipped`. **PR #12**, six stage comments, security `PASS — 0 blocking finding(s)
of 0 total`, `provenance: scanners`. Real scanners cleared a clean diff, which is
the half a fixture fallback could not honestly produce.

### 2026-08-22 — the automatic trigger

Opening issue **#15** started run `32542152671` with nobody typing a command:

```
Lambda:  accepted delivery e89b0238-9dc4-11f1-87a1-90dc3e86b309 (issues)
plan job env:  TICKET_ID: 15
               TICKET_TEXT: Rate-limit the password reset endpoint
all seven jobs green · PR #17 · security PASS · provenance: scanners
```

`TICKET_ID: 15` is the proof the inputs came from the issue — nothing in this
repository knows that number.

**An auto-started run still reads `event: workflow_dispatch`**, and that is
correct rather than a sign it did not work: EventBridge triggers the workflow
through the same REST dispatch API `gh workflow run` uses. No field distinguishes
them. To tell them apart, read the plan job's `TICKET_ID`.

**The gates hold.** Each gate job sat in `waiting` with a named approver. Before
2026-08-22 `gate1` was the only Environment and had `protection_rules: []`, so it
did not pause — it ran. **An Environment without a required reviewer is not a
gate.**

### The DLQ earned its keep, once

The first dispatch attempt failed while **both GitHub and the Lambda reported
success** — the Lambda logged `accepted delivery`, status success, and no run
appeared. The only record of why was the dead-letter message:

```
ApiDestination returned HTTP status 403
{"message":"Resource not accessible by personal access token"}
x-accepted-github-permissions: actions=write
```

The dispatch token had been narrowed to the target repo and so lost
`actions:write` on this one. That is this project's signature failure shape — every
component reporting success while the thing did not happen — caught by the one
component hardest to justify.

**The token needs BOTH repositories**: `auth-service` for contents + issues + pull
requests, `TheAgentOrg` for `actions:write`. Narrowed to either alone, the other
half fails silently.

**A FIRST DIAGNOSIS OF THIS BLAMED THE TOKEN AND WAS WRONG.** Recorded because the
wrong answer was plausible, was written into three files, and the right one is one
`curl` away. Measured on the clean run: the SRE reported `CI unknown` while both check
runs on that exact commit were `completed/success`, finished **49 seconds before** the
stage asked. The token was blamed for lacking a `Checks: read` scope. It answers:

```
GET /repos/.../commits/<sha>/check-runs   -> HTTP 200  total_count: 2
      test                        completed success
      GitGuardian Security Checks completed success
GET /repos/.../branches/<branch>          -> HTTP 200
```

**The real cause is structural.** `sre.run` calls `github_ops.ci_status(state)`, and
under `REMOTE_AGENTS=true` that body executes INSIDE the AgentCore container. The five
runtimes carry exactly `AGENT_ROLE` and `DEMO_REPO` — no token, deliberately — so
`_use_local()` is True in there and `ci_status` returns `unknown` **on its first line,
with no API call and no exception**. Nothing to log, nothing to catch, and a WARNING on
the failure path would never have fired.

Fixed the way `RunState.poisoned` was: `scripts/run_stage.py:_stage_sre` and
`graph._walk` measure on the runner, which does hold `DEMO_GITHUB_TOKEN`, and carry the
answer on `RunState.ci_status_measured`. `sre.run` reads the field and falls back to
measuring when it is blank, so the in-process path is unchanged.

Three things worth keeping:

- **`""` means "nobody measured", NOT `unknown`.** Reading a blank as `unknown` would
  silently downgrade the one path that CAN measure. And a measured `unknown` must be
  carried through rather than re-measured — that is the falsy-value trap here, and it
  would produce the same answer for the wrong reason with every test still green.
- **Order is the requirement, not the call.** Measuring after `call_agent` leaves the
  sent state blank and reads exactly like correct code. Pinned over the **AST** by
  `test_the_sre_stage_measures_ci_before_invoking_the_agent`, because a substring check
  would be satisfied by the comment explaining it.
- **A container with no credential cannot answer a question that needs one**, and it
  fails by returning the fail-safe value rather than by erroring. Any future agent-side
  code reaching for a token has this shape.

### Live configuration

Five runtimes `theagentorg_{planner,developer,reviewer,security,sre}`, all READY at
**version 30** — re-read 2026-08-28 with `preflight.py` check 2. The number climbs on
every deploy; what matters is that all five carry the SAME one. All five carry the
**same** version: a split would mean a partial deploy, where some agents run new code
and some old and no stage's output says
which, so `scripts/preflight.py` check 2 fails on a version mismatch as well as on
a non-READY status.

**All four gates measured green on `main` at `5215ca5`, 2026-08-28** — the Phase 1
integrator baseline, before any lane merges:

```
pytest -q                              1131 passed, 3 skipped in 180.69s
ruff check agentorg scripts tests      All checks passed!
actionlint .github/workflows/*.yml     exit 0
terraform fmt -check -recursive        exit 0
preflight.py                           preflight OK   (all 4 checks PASS)
```

**And again at `fb461bd`, with Phase 1 AND Phase 2 merged — nine lanes:**

```
pytest -q                              1714 passed, 3 skipped in 143.58s
ruff / actionlint / terraform fmt      exit 0
preflight.py                           preflight OK   (all 4 checks PASS)
runtimes                               all five READY at v30
```

**Lane H, in a worktree at `6c8bb4f` plus its own four commits:**

```
pytest -q                              1792 passed, 4 skipped in 204.14s
ruff check agentorg scripts tests      All checks passed!
```

`1714 → 1792` is **78 tests** from one lane, and the `4 skipped` is the documented
worktree constant (three scanner skips plus the gitignored `terraform.tfvars`), not a
regression — see the note at the top of this file.

`1131 → 1714` is **583 tests** across nine lanes. Preflight check 3 still reads
`LINES: [3, 4]` with `provenance: scanners`, so the discriminator survived Lane C
rewriting all three scanner wrappers AND Lane D moving every GitHub call behind an
interface. **64 test files.**

`1131 → 1502` is **371 tests** added by five lanes: queue (A), tenancy (B), scoring (C),
cost (E) and evidence (L). The suite is 2.5 minutes and still needs no infrastructure —
that is what keeps it usable as a gate, and it is why Lane A's in-process queue backend
and Lane B's sqlite dialect are the tested paths rather than a convenience.

Preflight check 3 read `LINES: [3, 4]` with `provenance: scanners` from the deployed
security runtime — **both times**, so the discriminator survived Lane C rewriting all
three scanner wrappers behind a shared severity table. That is the one number to re-read
after any lane touches `agentorg/security/`, and it is the check no green suite can
replace.

Environments `gate1`/`gate2`/`gate3`, each with `required_reviewers` — **and each
with `can_admins_bypass: true`**, measured:

```
gate1  rules=['required_reviewers']  can_admins_bypass=True
gate2  rules=['required_reviewers']  can_admins_bypass=True
gate3  rules=['required_reviewers']  can_admins_bypass=True
```

So the honest answer to "can a gate be skipped?" is **yes, by a repository
admin**, without a reviewer clicking. That is an operator setting, not a code
path, and it is left as-is deliberately — but a judge may ask, and the answer
should not have to be discovered mid-demo. `preflight.py` check 4 prints it on
every run and does **not** fail on it: failing would make the script refuse a
configuration the team chose.

Repository variable `DEMO_REPO`; secret `DEMO_GITHUB_TOKEN`. EventBridge rule at
**1 target**, connection `AUTHORIZED`, API destination `ACTIVE`, DLQ empty.

### The pre-demo check — `scripts/preflight.py`

One command, four checks, exit 0 or 1. Each answers a question whose wrong answer
has already happened here **while reporting green**:

| # | Question | Why it is not redundant |
|---|---|---|
| 1 | Can the runtime role invoke the model the code names? | `simulate-principal-policy`, not a green apply. An apply proves the policy was **written**; only this proves it **permits the call**. |
| 2 | Five runtimes, READY, one version? | Necessary, **not sufficient** — a runtime reports READY before its endpoint serves the new version. |
| 3 | Does the security runtime return **real** scanner lines? | The only sufficient check, and the only field that separates a real scan from the fixture. |
| 4 | Do all three Environments require a reviewer? | An Environment with no reviewer **does not pause — it runs**. |

The line sets are **imported** from `tests/provenance.py`, never restated: a copy
would be a second declaration of the fact this repository's whole verification
story rests on, and both copies would keep passing as they drifted.

**Check 3 caught a defect in itself on its first live run**, and the lesson
generalises. With a hand-written poisoned diff differing from the reference one by
a **single missing blank line**, it reported `LINES: [2, 3]` against an expected
`[3, 4]` — while `provenance` read `scanners`, the verdict was a correct `block`
and `blocking` was 2. Reported lines are indices into the **added-lines-only**
file, so deleting one blank line shifts every finding below it. Therefore
`REAL_SCANNER_LINES` is a property of *the scanners* **and** *that exact diff*, and
the script loads the reference diff from `fixtures/dev_result_poisoned.json` rather
than carrying a second copy.

---

## The presentation — `scripts/make_deck.py`

The pitch deck is **generated**, not hand-built: `docs/pitch/TheAgentOrg-prefinal.pptx`,
16 slides, real transitions and click-advanced animations. One command, and it
self-checks:

```bash
.venv-main/bin/python scripts/make_deck.py
```

```
docs/pitch/TheAgentOrg-prefinal.pptx  (379 KB)
  slides:      16
  animated:    14
  layout:      clean
  sections:    all four covered
  transitions: all
  OK — motion, content rules and layout verified in the saved file
  file(1):     Microsoft OOXML
```

**Why generated.** Somebody has to be able to fix a typo at 11pm the night before and
regenerate. And a hand-built `.pptx` lets a stale figure survive on a slide indefinitely
— every number is a constant at the top of the script, each annotated with the command
that produced it, which makes rule 4 of this file enforceable rather than aspirational.

`python-pptx>=1.0,<2` is in the **`dev` extra only**. Nothing under `agentorg/` imports
it; a presentation tool has no business in a container where every extra import is
another thing that can fail at runtime on arm64.

### Motion is not a python-pptx feature

Measured before relying on it: `dir(slide)` exposes nothing matching `trans` or `anim`.
The library models shapes and text, **not the timing tree**. Both live in the slide's raw
XML, which it does expose, so `_transition` and `_animate` build that XML directly.

- `<p:transition>` must be appended to `<p:sld>` as its **LAST child** — the schema
  requires that order, and an element out of place makes PowerPoint declare the file
  corrupt and offer to repair it, which on a projector is indistinguishable from a broken
  deck.
- An entrance animation is one `<p:timing>` tree per slide. Each shape needs `<p:set>` to
  make it visible **plus** `<p:animEffect filter="fade">` — without the `<p:set>` the
  shape is on screen from the start and the fade animates something already visible.
- Circular photographs are `auto_shape_type = MSO_SHAPE.OVAL` on the picture, which
  writes `<a:prstGeom prst="ellipse">`. Verified in the saved XML: 5 pictures, 5
  ellipses.

### `verify()` reads the saved archive back, and that is the point

**A deck that silently lost its motion is byte-different but visually identical until it
is presented**, and python-pptx will never report it — it never knew about those
elements. So the file is re-opened and four things are counted. Each has caught a real
defect:

| check | what it caught |
|---|---|
| transitions + `animEffect` per slide | the library cannot tell you they are missing |
| **wrapped-height** collisions | a width-only check reported clean while **six** boxes overlapped |
| banned phrases | cut copy and source code are easy to reintroduce while editing prose |
| required sections present | a deck that reads well and omits a required topic fails the brief |

**On the layout check:** the question is wrapped **height**, not line width. With
`word_wrap` on, a long line does not overflow sideways — it wraps, and the box grows
**downward** into whatever sits below it. Compare boxes only when they overlap
**horizontally**: two columns side by side share a vertical band by design, and flagging
those made the audit cry wolf on every stat slide. Give single-line boxes an explicit
`height`, or the 1in default hangs past the slide edge and every slide reports a
false positive.

### Design in the browser first

`docs/pitch/preview/index.html` renders the same palette, type scale and 16:9 geometry as
HTML:

```bash
.venv-main/bin/python -m http.server 8412 --directory docs/pitch
```

A colour decision then takes one reload instead of regenerate → open PowerPoint → squint.
**That loop is why this deck came out well**, and it is the step most likely to be
skipped. The preview's CSS custom properties and the generator's palette constants are
the **two places a colour lives** and must change together — otherwise what is on screen
stops being what was signed off.

### The palette, and the three content rules

Dark: near-black with a hint of blue (**not flat grey**, which reads as dead), off-white
text (**never pure white**, which glares), cyan for every structural mark, rose and mint
only where a slide states a failure or a success. Dark because the delivery medium is a
Teams screen share where the viewer's own brightness is unknown.

Three rules the first version of the deck broke, all now enforced by `verify()`:

- **No source code on a slide.** Nobody reads five lines of Python off a screen share in
  sixty seconds, and asking them to spends their attention on parsing instead of on the
  argument.
- **No per-person slides.** A slide headed with one engineer's name invites "so what did
  the other four do", and makes a five-person team read as five people who worked
  separately. One *combined* team slide with photographs is different, and good.
- **Photographs are pre-cropped square** in `docs/pitch/photos/square/`. CSS crops with
  `object-fit: cover`; **PowerPoint has no equivalent and stretches instead**, and a
  stretched face is the one defect an audience notices instantly. Portraits are biased to
  the upper third — a centre crop on a tall photograph cuts the top of the head off.

The recipe is also written up in `~/sorour/BringingtheModelHome/CLAUDE.md`, with the
generator copied to `reference/` there, so a future deck for a different event starts
from this one rather than from scratch.

---

## Lint rules that cannot be relaxed

```bash
.venv-main/bin/python -m ruff check agentorg scripts tests   # must exit 0
actionlint .github/workflows/*.yml                            # must exit 0
```

- **No `[tool.ruff]` section** in `pyproject.toml`. No `# noqa`. No per-file
  ignores. The rule set is ruff 0.16's defaults, unconfigured.
- `I001` (unsorted imports), `BLE001` (blind `except Exception`) and `ISC004` are
  **ruff 0.16 defaults** — verified with `ruff check --isolated`. They fire without
  being selected.
- `target_repo/` is **deliberately NOT** in the lint command. It is the demo's
  subject repository, not our code, and it currently has 2 ruff errors on purpose.
- Ruff pinned `>=0.16,<0.17`; setuptools `>=61,<85` in both `[build-system]` and
  the `dev` extra. Bump both together, deliberately.

**The broad `except Exception` clauses are load-bearing, and ruff blesses the
dangerous alternative.** BLE001 is satisfied when the handler contains a logging
call ruff can *statically resolve* to the logging module **and** that call carries
the traceback. Measured across 12 ruff variants: the level is irrelevant, ruff
cannot resolve a module-level alias — so `_log.exception(...)` turns
`ruff check agentorg` red — and **narrowing the `except` satisfies the rule with no
logging at all**. So lint will bless a narrow clause that silently drops the
failure, which is the worse option. Fetch loggers **inline** at each call site;
do not "clean up" `logging.getLogger(__name__)` into a module-level `_log`.

---

## Traps already paid for

**AWS / AgentCore**

- **The model id is an INFERENCE PROFILE, and it needs its own grant.** The most
  valuable thing found in this project. `config.BEDROCK_MODEL` is
  `us.amazon.nova-2-lite-v1:0`, and the `us.` prefix makes it a **cross-region
  inference profile**, not a foundation model. The two live at different ARN
  shapes, and the runtime role granted only the second:

  ```
  arn:aws:bedrock:us-east-1:339712964409:inference-profile/us.amazon.nova-2-lite-v1:0  implicitDeny
  arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-2-lite-v1:0                  allowed
  ```

  So `bedrock:InvokeModel` was denied, `llm.text()` caught it **by design**,
  `structured()` returned `None`, and **every model-calling agent served its
  fixture for about a week** — with every job green and the deployed plan comment
  matching `fixtures/plan_result.json` byte for byte. Nothing anywhere said the
  model had not answered.

  **Both ARN shapes are required**: the profile is the thing **called**, the
  foundation models are the things that **answer**, and either grant alone is still
  a denial. Note the asymmetry — the profile ARN carries an account and the
  foundation-model ARN does not; that is AWS's, not a typo. Inference profiles are
  account-scoped, foundation models are not.

  The fallback is correct behaviour. Being unable to **see** it was the defect, and
  the check positioned to catch it — `deploy.yml`'s smoke step — grepped for
  `"tasks"`, which `fixtures/plan_result.json` contains on **line 2**. So it passed
  identically for a fixture and a real completion, in the step whose own comment
  claimed to assert on content to avoid "the reassuring non-answer".
- **`ListAgentRuntimeEndpoints` is not grantable to the CI role.** Measured with
  `simulate-principal-policy`: `implicitDeny` against both the runtime and
  runtime-endpoint ARNs, while the role's policy grants `bedrock-agentcore:*` on
  `"*"`. **RE-VERIFIED 2026-08-22** against the live account, because one audit
  reported it had become `allowed`:

  ```
  runtime/theagentorg_security-Wa42fz7FCC                          implicitDeny
  runtime/theagentorg_security-Wa42fz7FCC/runtime-endpoint/DEFAULT  implicitDeny
  (no --resource-arns at all)                                       allowed
  ```

  The audit was reading the **resource-less** form. That answers `allowed` and
  means nothing: an action simulated without a resource does not exercise the
  resource clause any statement is scoped by. `deploy.yml`'s comment and this file
  are **correct** — do not "fix" them. This is the same "denied versus not ready
  yet" confusion the retry loop exists to avoid.
- **`aws --output text` appends a literal `None` line.** Cost two failed deploy
  runs. Read fields from the boto3 response; do not scrape CLI text.
- **`invoke_agent_runtime` needs `qualifier="DEFAULT"`.** Without it the call is
  `ResourceNotFoundException` even against a READY runtime with a READY endpoint.
- **The CLI wants a base64 payload; boto3 wants raw bytes.** Two interfaces to one
  API.
- **A runtime reports READY before its endpoint serves the new version.** Retry the
  invoke rather than polling a status field.
- **`iam:CreateServiceLinkedRole` is `implicitDeny` for the CI role**, and
  EventBridge needs an SLR to create an API-destination connection. Created by hand
  once, rather than granting CI standing power to mint roles for any AWS service.
- **EventBridge names its connection secret `events!connection/…`**, which the
  existing `theagentorg-shared-*` grant cannot match. One statement was added
  scoped to that prefix, deliberately **without** `GetSecretValue`.
- **The image must be arm64**, and its base must come from ECR Public — Docker Hub
  answers 429 to anonymous CodeBuild pulls, late in the build.
- **`fixtures/` must be explicitly `COPY`ed** into any image.

**GitHub**

- **The workflow file must be on `origin/main` BEFORE the EventBridge target is
  applied.** GitHub resolves the workflow on the ref, and answers **404 both** for
  "file not on ref" and for an unauthenticated dispatch — two causes, one symptom.
- **`workflow_dispatch` inputs arrive as STRINGS**, booleans included, and the REST
  dispatch API rejects real JSON booleans inside `inputs`.
- **A rejected GitHub Environment SKIPS its job**, it does not run it with a
  verdict. Hence the three `gate*-rejected` recorder jobs.
- **An Environment with no required reviewer does not pause — it runs.**
- **`python -m pytest` and bare `pytest` are not interchangeable** in
  `target_repo/`: `python -m` prepends cwd to `sys.path`, the console script does
  not, and the bare form dies with `ModuleNotFoundError: No module named 'app'`. Do
  not harmonise those two CI lines. **RE-MEASURED 2026-08-22** in a clean venv on
  the deployed `auth-service` checkout, against the same tests: `python -m pytest
  tests -q` → `1 passed`; `pytest tests -q` → `ModuleNotFoundError: No module named
  'app'`. That repo's new `ci.yml` uses the `python -m` form for this reason.

**Terraform**

- **A binary `tfplan` embeds Terraform STATE, so uploading it publishes every
  secret in that state.** Fixed in `d237b32`; recorded here because the shape is
  not obvious and the naive check misses it.

  `terraform.yml` uploaded the binary plan as an artifact. A binary plan carries a
  full copy of state, and this state holds
  `aws_secretsmanager_secret_version.dispatch_token` — the ingress module reads that
  secret **at plan time**, because an API_KEY connection needs the token's value as
  a configuration value. So every `tfplan-*` artifact carried a live `github_pat_`
  with `actions:write` here and contents/issues/pull-requests on the target repo.

  **A raw grep of the outer file finds nothing**, and that is the trap: `tfplan` is
  **itself a zip**. Unpacked from artifact `9466368657`, three entries each matched
  `github_pat_[A-Za-z0-9_]{20,}` exactly once — `tfplan` (43990 bytes), `tfstate`
  (106725) and `tfstate-prev` (107238). Ten artifacts were deleted and the upload
  narrowed to `plan.txt`.

  **This is not the same exposure as the accepted S3-state one.** That risk is
  documented under Secrets and its audience is "anyone with read access to the
  backend bucket". An Actions artifact's audience is anyone who can read the
  repository's workflow runs, plus anyone the artifact URL is shared with, for the
  retention period — a different and much wider set. Same secret, different blast
  radius; do not let the accepted risk excuse this one.

  **WHETHER THE TOKEN WAS ROTATED IS UNRESOLVED IN THIS REPOSITORY, AND THE TWO
  RECORDS DISAGREE.** `terraform.yml:213` says "all ten were deleted **and the token
  rotated**"; this file said it still needed rotating. Both were written the same day.
  Nothing in the repository can settle it — a PAT's creation date is visible only to
  the account that holds it, and a live token and a rotated one behave identically
  from in here.

  So the safe reading is the pessimistic one: **treat that `github_pat_` as
  compromised until an operator confirms otherwise.** Deleting the artifacts removed
  the distribution channel, not the exposure. The confirmation is a human action —
  check the token's creation timestamp at
  `github.com/settings/personal-access-tokens` against 2026-08-22 — and it is one
  click, which is why the disagreement should not have survived.

  **The lesson is about the record, not the token.** Two files claiming opposite
  things about a credential is worse than either claim alone: a reader who finds the
  reassuring one stops looking. When a fix depends on an action outside the
  repository, the repository can only record *that the action is required* and how to
  verify it — never that it happened.
- **`*.tfvars` is gitignored** (`.gitignore:14`), so a value set there exists only
  on the machine that wrote it while CI applies from a fresh checkout — it looks
  configured locally and the rule stays at zero targets. Use `TF_VAR_<name>` in the
  workflow's `env:` instead.
- **A rule with no target fires into nothing while looking perfectly healthy** in
  the console. Hence the `dispatch_target_enabled` output.
- **`recovery_window_in_days = 0`** on the webhook secret, so a destroy/apply cycle
  can reuse the name.
- **`terraform_wrapper: false`** everywhere — the wrapper rewrites stdout and breaks
  reading a step's real output later in the job.
- The apply **re-plans** rather than consuming the plan artifact. Explicit tradeoff:
  a re-plan can differ from what was reviewed, but applying a stale plan against
  moved state has the opposite failure.

**This repo's own history**

- **A guard whose record destroys the evidence it protects is worse than no guard.**
  `timeline._outcome` reads its banner off the **last** log row's action, not off
  `RunState.status`. A recorder's explanatory row became the last row and downgraded
  `⛔ BLOCKED` to `… INCOMPLETE` while the state file still said `blocked` — so every
  state-reading assertion stayed green. Fixed by re-appending the run's real ending,
  and pinned by a test asserting on the **rendered banner**.
- **Six cross-referenced line numbers in docstrings are stale**, and
  `github_ops.py:30` points at a `_target` function that does not exist (it is
  `_destination`). Verify a line number before quoting it into anything.
- **A `failed` run renders as `INCOMPLETE`** on the timeline, because no ending
  action in `_OUTCOME` corresponds to it. The SRE `no_go` path writes no log row at
  all after `sre/reviewed`.

---

## Secrets

**Never read, print, log or commit `.env`.** It holds a live GitHub token and is
gitignored (`.gitignore:13`).

FAKE credential literals only, in tests. `AKIAIOSFODNN7EXAMPLE` is AWS's own
published documentation example and is safe — it is the poison in
`tickets/poisoned.md`, at line 17, with its paired example secret at line 18. Note
the key also appears in that file's prose at line 6, which is why the demo greps
`AWS_ACCESS_KEY_ID` rather than the key itself: the key matches two lines and
invites "which one is real?" mid-demo.

Zero static AWS keys anywhere. Every AWS step assumes
`arn:aws:iam::339712964409:role/github-actions-role` via OIDC.

The dispatch token **lands in S3 Terraform state** — unavoidable with an API_KEY
connection, since the provider takes the value through configuration. Scope it to
these two repositories and **rotate it after the demo**.

### Tenancy (Lane B) — where isolation is enforced, and where it is not

`agentorg/tenancy/ADR-001-database.md` is the long form. The four facts a future
session would otherwise re-derive:

- **Writes are refused by the DATABASE on both dialects; reads only on Postgres.**
  SQLite has no mechanism that constrains a `SELECT` against a base table — no RLS,
  and a trigger cannot fire on a read. So on the tested path a read is only as
  scoped as its accessor's `WHERE tenant_id = ?`. Measured: removing that one
  predicate from `accessors._require` fails **13 named tests**. The Postgres RLS is
  real emitted DDL asserted structurally, but **nothing in the suite connects to
  Postgres** — same distinction as a green `terraform apply` versus
  `simulate-principal-policy`.
- **`IS NOT`, never `!=`, in every SQLite trigger.** SQL's three-valued logic makes
  `'t2' != NULL` evaluate to NULL, which does not fire a `WHEN` — so a `!=` guard is
  absent **exactly when no tenant is bound**, the case it most needs to catch. Both
  spellings refuse an ordinary mismatch, so the bug survives any hand test. Mutating
  one operator fails `test_the_sqlite_guards_use_IS_NOT_and_never_bare_inequality`
  **and** the executed `test_with_no_tenant_bound_every_scoped_write_is_refused`.
- **A trigger's `RAISE(ABORT, ...)` surfaces as `sqlite3.IntegrityError`, not
  `OperationalError`** — measured. So a `try/except OperationalError` around a
  scoped write would look like handling a refusal and catch nothing; catching
  `IntegrityError` and returning quietly would turn a refused breach into a silent
  no-op read as success. `accessors._write` deliberately catches neither.
- **`dict(sqlite3.Row)` silently collapses duplicate column names**, keeping the
  first of each pair, nothing raised. `membership JOIN app_user` shares `id` and
  `created_at`, so an unaliased `SELECT *` returns 7 keys as a 5-key dict with the
  user's `id` simply gone — and the result still looks like a member. That is why
  `list_members` names its columns.

Two directions that pass a casual reading either way round, both pinned: a tenant
with **no budget row is REFUSED**, not admitted (absent must not read as unlimited,
the same trap as a blank `ci_status_measured` read as `unknown`); and
`RunState.tenant_id == ""` is **TRANSLATED** to tenant zero, never rewritten —
`state.py` is frozen and every run on disk carries the blank.

**The secret cipher is stdlib and is NOT AES-GCM.** `cryptography` is absent from
the declared dependency closure — PyJWT requires it only under an extra and CI
installs `.[dev]` — so an import would work locally and fail in CI. It is
encrypt-then-MAC over an HMAC-SHA256 keystream with per-record random nonce and
scrypt subkeys: the right shape, without a reviewed constant-time primitive. Every
row records **which cipher wrote it**, the way `scan_provenance` records scanner
mode, so a KMS migration is visible in the data. Binding it to KMS is a stated
further step, not a done one.

`agentorg/approve_server.py` has **no authentication** and binds `127.0.0.1` only.
It resumes a paused pipeline past a human gate, including the security gate. Three
things stand in for the auth that does not exist, and none is a substitute:
loopback binding, POST-only mutations, and a cross-site `Origin` refusal — that
last because loopback binding alone does **not** stop a page in the local browser
from posting here, which is the hole it is most often assumed to close. `by` is
recorded as `"ui-reviewer"` for every decision because with no authentication the
server genuinely does not know who clicked. **Never expose it off-host.** It is
kept for a future frontend, since it is buttons over `gates.resume`.

### The control-plane API (Lane K) — and the one thing it cannot do

`agentorg/api/` is stdlib-only HTTP over Phase 1's substrate: `POST /v1/runs` is
`queue.enqueue`, `GET /v1/runs/<id>` is `queue.jobs_for_run`, cancel is
`queue.complete`. Verified end to end with no GitHub involvement — submitted,
watched and cancelled over a real socket, no token and no network.

**IT CANNOT APPROVE OR RESUME A GATE, and the refusal is structural rather than a
policy note.** No route maps to `gates.resume` or `queue.resume`, no scope grants
it, and `tests/test_api_submission.py` asserts that per-module over the **AST** —
because these files are half commentary and several discuss `gates.resume` at
length, so a grep for `resume` matches the prose arguing for its absence while a
real call sits beside it. The argument: a machine credential that could approve a
gate defeats the gate for exactly the callers gates exist to exclude, and
`HumanDecision.by` would name a service account while reading as a human decision.
The absent scope is deliberate too — a `gates:approve` nobody holds reads as a
capability that exists, and the next person grants it and hunts for the broken route.

Four things measured rather than assumed:

- **An empty key store is a REFUSAL.** Every authenticated route 401s until a key
  is provisioned, asserted from the transport as well as the function. Written the
  other way the failure is invisible from inside a deployment: every request
  succeeds and the first signal is somebody else's run in your tenant. Same
  direction as `budgets.check` with no budget row.
- **scrypt `n=2**14` costs 27.7 ms here** (`n=2**13` is 11.6 ms), and an unknown
  key id costs the SAME work as a wrong secret — 23.1 vs 22.8 ms, because the hash
  runs against a throwaway salt either way. An early return on a missing id reads as
  correct code and answers "does this key exist?" through timing.
- **`secrets.token_urlsafe` emits base64url, whose alphabet CONTAINS `_`.** A key
  formatted `agtk_<id>_<secret>` therefore broke `split("_")` and refused every
  legitimately issued key. **A test asserting only that malformed keys are refused
  passes against this**, because refusing everything satisfies it. Found by issuing
  a key and resolving it in the same breath; fixed with `maxsplit=2`.
- **The queue's UNIQUE index cannot carry HTTP idempotency**, and this is the
  measurement worth keeping: `adopt_run_id` renames the placeholder row in place, so
  `jobs_for_run(placeholder)` drops to 0 and re-enqueuing that triple is **accepted**.
  The index stops a retry arriving before `plan` runs and admits the identical retry
  arriving after — a window narrow enough to make the defect intermittent. Hence
  `api/idempotency.py`, keyed `(tenant_id, key)`; the tenant half is not optional
  because `Idempotency-Key` is client-chosen and two customers will pick the same one.

**What cancellation guarantees, precisely**, because "honoured mid-run" has an edge
and all three positions were verified: a READY next stage stops (`queue.claim`
returns None), a run PAUSED at a gate stops and leaves `queue.awaiting()`, and a
stage **already executing is not killed** — the subprocess runs to completion and
the worker's later `complete` is REFUSED (`already ended as 'rejected' with exit 4`).
So the promise is *no further stage runs*, not *the current stage stops*. Killing it
would be the stronger promise and is refused for `queue.fail`'s reason: a
half-killed stage may have opened a PR and posted three comments. A second cancel
is **409**, never 200 — a cancel reporting success for a run it did not cancel is
the signature defect.

**K3's threshold goes through `scoring.resolve_threshold` and the floor is not
re-implemented.** The RED step that proves it is worth copying: replacing the call
with a local list check that rejects `HIGH` and `catastrophic` identically leaves
every vocabulary test green, and **only** the test that lowers `THRESHOLD_FLOOR` to
`low` catches it. The floor is `critical` today so it refuses none of the four legal
thresholds — a test that tried only legal values could not tell whether this API
consults it at all. `security` is absent from `OPTIONAL_CHECKS`, so no configuration
disables the one binding check.

Three known gaps, named rather than implied: the key store, the idempotency store
and the config store are **in-process**, so none survives a restart (the durable
homes are `schema.SECRET` and `schema.REPOSITORY`, Lane B's files); `RunStatus.cost_usd`
is always `None` because reaching `RunState.cost` needs `gates.load` and the AST test
forbids importing `gates` at all — closing it needs a cost column on the job row or a
read helper that is not `gates`; and one ingress secret per provider means one webhook
endpoint serves every tenant, since a webhook arrives with no credential and "try
every tenant's secret" is an oracle for which tenants exist.

**No web framework.** `starlette 1.6.0` and `uvicorn 0.52.4` ARE installed and were
still refused: `test_requirements_covers_every_third_party_import_in_the_package`
AST-walks `agentorg/` and would make any import here a dependency of all five arm64
agent images — and `starlette` already sits in that test's `_NOT_RUNTIME` list
described as "dead code today", so importing it from a live module would make that
exclusion a lie. `tests/test_api_ingress.py` asserts the absence locally, so the
failure names this package instead of surfacing as a packaging test going red.

### Generated tests (Lane G) — a SIXTH agent, and why it must not read the diff

`agentorg/agents/testgen.py`, spec §9 / judge requirement 7. It generates pytest files
from `state.plan.acceptance_criteria`, executes them, and reports what happened.

**SEPARATION OF AUTHORITY IS THE POINT, one layer out from the block rule.** A model
that can be persuaded must not stand between a credential and `main` — hence
`compute_security_verdict`. Same argument for tests: **if the agent that wrote the
change also writes the test that clears it, the test is a restatement of the change's
own assumptions.** It passes either way.

Enforced structurally, not by prompt. `_prompt` never touches `state.dev`, and
`repo_snapshot.render(targets)` is called with **no `diff=`** — that keyword is the
before/after switch, and the reviewer passing it is what makes this agent's omission a
deliberate distinction rather than an accident.
`tests/test_testgen_authority.py` asserts both over the **AST**, plus an executed twin
that puts a sentinel in `dev.diff` and checks it never reaches the prompt. Over the AST
because that module's own docstring says "is NOT given `state.dev`" in those words, so
a substring check is satisfied by the sentence explaining the guarantee — the failure
this file records twice already. RED: `render(targets, diff=state.dev.diff)` fails **4
of 8**.

**`test_the_snapshot_is_the_BEFORE_view...` asserts the REVIEWER still passes `diff=`.**
Without that half, "testgen omits it" stays true if the after-view is deleted, if the
parameter disappears, or if nobody uses it — a vacuous check that reads as a real one.

**The model is asked for `TestPlan`, which cannot express `passed`, `failed` or
`binding`.** That is the `SREAdvice` lesson applied before it could bite: those three
are MEASURED by running the tests, and a model reporting its own pass count is
fabricated evidence rather than a schema mismatch.

| Field | Rule |
|---|---|
| `binding` | **`failed > 0`, in one function.** Not `not passed` |
| `source` | `acceptance_criteria` or `fixture`. **Never `diff`** — the contract admits it, so the refusal is asserted |
| `notes` | carries the G7 caveat, the quarantine report, and `NOT EXECUTED` when nothing ran |

**`binding = not passed` is the dangerous mis-spelling**, because it reads as "be
strict" and would defend itself in review. It turns the GENERATOR's failure to produce
anything into a block on somebody else's change; the feature then earns a reputation for
false alarms and gets switched off — which is G6's social failure mode arriving through
G5. RED, `binding=True` on the missing-test path: 1 failed, 6 passed.

**G7 lives in the rendered notes, not only in the field.** Nobody reads `passed=3` as "a
model wrote three assertions from a ticket", so `GREEN_PROVES` says it, and a green and a
red run must not share a message — one string covering both is honest and useless. Same
argument as `report.render` naming the zero cache hit rate in words.

**The flake policy is ONE retry, and a self-disagreement is a third outcome.** Failed
then passed is neither a pass (the run disagreed with itself) nor a failure (it did not
reproduce): it is quarantined, `binding` stays False, and the quarantine is NAMED.
`QUARANTINE_FLAKY` (a fault) and `QUARANTINE_CHOSEN` (a human's decision) deliberately do
not share a spelling — `scan_provenance`'s rule, because collapsing them hides a broken
generated test behind last month's decision. The empty case writes "no tests are
quarantined" rather than nothing: an absent line and a line stating nothing was excluded
are different facts and only one is checkable. RED, a flake returning no quarantined
paths, produced the artifact that makes the case — `2 passed, 0 failed … no tests are
quarantined` for a run that failed and was retried.

**NO BROWSER RUNS ON THIS MACHINE, and G4 is unverified against a real one.** Measured
2026-08-28: selenium not installed, `chromedriver`/`geckodriver` not on PATH, no
Chrome.app or Firefox.app, and `safaridriver -p 4444` **hung with no `/status` response
in 120 s** (it needs Safari ▸ Develop ▸ Allow Remote Automation, a GUI action). Four
Selenium tests are written and skip here. `SELENIUM_REQUIRED=true` promotes the skip to a
**fault** — `1 failed, 4 errors` — the same shape as `SCANNERS_REQUIRED`, and
`=false` correctly stays a skip. `test_the_skip_is_visible_and_not_silent` runs
unconditionally and prints what is missing, because a suite collecting zero browser tests
reads identically to one where they all passed.

**`target_repo/tests/e2e/app_web.py` WRAPS `create_app()` and does not edit
`app/auth.py`.** That file is what every clean and poisoned diff is written against, and
`REAL_SCANNER_LINES` is a property of the scanners **and** of that exact reference diff —
one missing blank line moved the findings from `{3,4}` to `{2,3}`. The new route is
`/web/login`, not `/login`: overriding the endpoint the unit tests and every generated API
test drive would mean a browser test that passes because it rewrote the thing under test.
`LiveServer` opens a real socket, because `app.test_client()` is an in-process WSGI shim a
browser cannot reach.

**selenium is NOT a dependency of the five arm64 containers**, and that is a constraint
rather than a preference: `test_requirements_covers_every_third_party_import_in_the_package`
AST-walks `agentorg/`, so an import there would ship it to all five images. The e2e tests
live outside `agentorg/` and import selenium **inside a fixture**, so collection never
needs it. Measured: `0` occurrences in `agents/requirements.txt`, that file's 49 tests
still green.

**THE DONE-WHEN, measured.** `tests/test_testgen_catches_a_break.py` runs a real
`python -m pytest` subprocess against `return username in _USERS` — a login handler
accepting ANY password for a known user:

```
BROKEN   passed=0 failed=1 binding=True
WORKING  passed=1 failed=0 binding=False
>       assert authenticate("alice", "not-the-password") is False
E       AssertionError: assert True is False
```

**The security verdict on that same diff is `pass`, correctly.** No credential, no CVE,
no injectable pattern — the three scanners are structurally blind to it, and one test
asserts both verdicts side by side. That contrast is the argument for this lane.

**The control is what makes it evidence.** A generated test that failed against every app
— a syntax error, a bad import, a wrong module path — satisfies the done-when assertion
perfectly while catching nothing. RED, `_counts` always reporting one failure, is caught
by the control and by nothing else.

**Two wiring lines are NOT done, and no pipeline stage calls this agent.** `graph.py`,
`scripts/run_stage.py`, `agents/server.py` and `common/agent_client.py` are the
integrator's files, so `testgen` is exercised only by its own 28 tests and
`RunState.generated_tests` is `None` on every run. Same honest state Lane C's `scoring`
and Lane E's usage payload were left in.

**One more inert-mutation instance, in a RED step's shell quoting.** A `python3 - <<'EOF'`
heredoc containing `''')` died with `SyntaxError: unmatched ')'`, the mutation never
applied, and pytest printed `5 passed` — indistinguishable from a caught mutation, with
the error scrolled above it. Redone through a file whose script **asserts its own
substitution applied and re-reads the file to confirm**. `assert s.count(old) == 1` before
writing is the cheap form of that check.

---

## Where things live

| Path | What |
|---|---|
| `agentorg/state.py` | The FROZEN contract + `compute_security_verdict` |
| `agentorg/graph.py` | The in-process pipeline walk; five `call_agent` sites |
| `agentorg/gates.py` | Human gates: save / pause / resume / load, and `StateRef` |
| `agentorg/log.py` | The append-only decision log; `runs/<run_id>.jsonl` |
| `agentorg/timeline.py` | The renderer — text and HTML |
| `agentorg/github_ops.py` | The GitHub seam, `deploy_note()`, `merge_pr()`, `report_outcome()` |
| `agentorg/integrations/` | GitHub as ONE ADAPTER behind one interface. `base.CodeHost` is derived from `graph.py`'s five calls; `GitHubHost` **delegates** to `github_ops`; `MemoryHost` is the suite's double and `GitHost` is the not-shipped proof the interface is real |
| `agentorg/repo_snapshot.py` | The shared repo view every agent reads: clone, TTL, after-diff |
| `agentorg/gates_cli.py` | `list` and `resume` — the only route to `--decision overridden` |
| `agentorg/approve_server.py` | A local approval screen; no auth, loopback only |
| `agentorg/fixtures_loader.py` | Resolves `fixtures/` from the **repo root** |
| `agentorg/db/` | The tenancy schema as DATA (one definition, two dialects), the connection + tenant binding, forward-only migrations |
| `agentorg/tenancy/` | Scoped accessors, per-tenant secret crypto, budgets, tenant zero, and `ADR-001-database.md` |
| `agentorg/queue/` | The job queue that replaces Actions' sequencing. `_memory.py` keeps the suite hermetic; `_sql.py` is durable and holds the ADR. **A pause is a durable ROW** |
| `agentorg/retrieval/` | Three curated corpora, deterministic ranking, and **the boundary as a refusal**: no consumer name reaches `compute_security_verdict`. Stdlib only — an import here ships to five arm64 images |
| `agentorg/api/` | The control plane: submit, watch, cancel, configure, verified webhook ingress, and a generated OpenAPI schema. Stdlib HTTP. **No route can approve or resume a gate** |
| `scripts/worker.py` | claim → run one stage → record → re-enqueue or pause. Takes no run parameters: they live on the row |
| `agentorg/common/config.py` | Every knob, with the reasoning |
| `agentorg/common/agent_client.py` | The one seam: in-process vs `invoke_agent_runtime` |
| `agentorg/common/llm.py` | Bedrock, with the fixture fallback — and the token/usage recorder |
| `agentorg/cost/` | The price table, a run's `CostRecord`, and the cache finding |
| `agentorg/common/diff.py` | What a diff PROPOSES — added lines only |
| `agentorg/agents/` | The five agents + `server.py` (HTTP) + `Dockerfile` |
| `agentorg/agents/testgen.py` | A SIXTH agent: generates pytest from `plan.acceptance_criteria`, **never from the diff**. Runs them, and reports a red result as binding and a green one as not evidence. **Not wired into any stage yet** |
| `target_repo/tests/e2e/` | The browser surface + Selenium. Wraps `create_app()` rather than editing `app/auth.py`. **Skips here: no browser on this machine** |
| `agentorg/security/` | semgrep / gitleaks / trivy wrappers, `_run.py`, rule files |
| `agentorg/security/scoring.py` | ONE severity table for all three scanners, the gitleaks policy, the threshold floor, and the `ScoreRow` audit trail |
| `scripts/run_stage.py` | One pipeline stage as one Actions job (the cloud path) |
| `scripts/preflight.py` | Four checks proving the DEPLOYED path is real; exit 0 or 1 |
| `scripts/measure_dependencies.py` | Vendor coupling over the **AST** — 4 of 31 modules, **1** module-level. Replaced four grep counts that reproduced under no scope |
| `scripts/scan_gate.py` | Real scanners over both fixtures; CI's `scan` job |
| `.github/workflows/run-pipeline.yml` | The cloud pipeline: 7 jobs + 3 recorders |
| `.github/workflows/{ci,deploy,terraform}.yml` | Lint/test/scan, runtime deploy, infra apply |
| `infra/Terraform/` | All infrastructure. Nothing created by hand in the console |
| `infra/ingress/handler.py` | The webhook Lambda (outside `agentorg/` on purpose) |
| `fixtures/` | Seven files — a validated sample of every result shape |
| `tickets/` | `clean.md` and `poisoned.md` — the same feature request |
| `target_repo/` | The demo's subject app: a Flask login handler. The **deployed** copy is `mohamedsorour1998/auth-service`, which had **no CI at all** until 2026-08-22 — head commit `{"state":"pending","total_count":0}`. A `ci.yml` running `python -m pytest tests -q` on every push and PR is open as **PR #18** there. GitHub reports `pending` when NOTHING has run, so zero checks must read as `unknown`, never `passing` |
| `tests/conftest.py` | The six autouse guards |
| `tests/provenance.py` | Which scanner mode a test is in, and the discriminator |
| `tests/test_issue_lifecycle.py` | The issue links its PR, learns the ending, closes |
| `scripts/make_deck.py` | Generates the pitch deck; self-checks motion, layout and content |
| `scripts/demo_{clean,poisoned}.sh` | The demo's two paths as one command each |
| `docs/pitch/` | The deck, the speaking script, and the browser design mirror |
| `docs/demo-runbook.md` | The live demo, step by step — the operator handout |
| `docs/handout-*.md` | One per engineer: their lane, their numbers, their questions |
| `docs/plan/` | Per-person plans; `reem/demo_script.md` is the OFFLINE runbook |
| `runs/` | Run logs + paused state. Gitignored, ~10k files — **never `ls` in here** |

`make_fixtures.py` regenerates all seven fixtures and re-validates each from disk.
Its block fixture's verdict is **computed** by `compute_security_verdict`, not
typed. It writes to a **relative** `fixtures/` path, so run it from the repo root.

`tickets/poisoned.md` has an unclosed code fence and no trailing newline. Harmless,
but do not "fix" it without checking: the demo script hardcodes the expected grep
output as line `17:`, which a reflow would shift.


