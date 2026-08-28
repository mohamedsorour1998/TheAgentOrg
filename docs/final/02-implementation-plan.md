# The Agent Org — final phase implementation plan

**Derived from** `docs/final/01-specification.md`. That document decides *what* and *why*;
this one decides *who owns which files*, *in what order*, and *how a lane proves it is
done*. Nothing here re-opens a decision from the spec.

**Written:** 2026-08-28. Baseline `a2b1990`: 1,102 tests passing, 5 runtimes at v19, both
demo paths verified.

---

## 0 · How this plan is executed

**Fourteen lanes, run in parallel.** Each lane has one **lane agent** that owns the lane
end to end. A lane agent is an orchestrator by default: it reads its brief, works its
tasks in order, and **spawns its own subagents only when it has genuinely independent
work** — three scanner mappings, five UI screens, four adapter tests. It does not spawn
subagents to look busy; a lane with sequential tasks runs them itself.

```
                        ┌──────────────────────────────┐
                        │  integrator (you + Claude)    │
                        │  owns: merge order, the       │
                        │  daily green-demo gate        │
                        └──────────────┬───────────────┘
                                       │
        ┌──────────┬──────────┬────────┴────┬──────────┬──────────┐
     LANE A     LANE B     LANE C   …    LANE M     LANE N
   lane agent  lane agent  lane agent   lane agent lane agent
        │           │                        │
   ┌────┴────┐      │(sequential —       ┌───┴───┬───────┐
  sub  sub  sub     │ no subagents)     sub     sub     sub
```

### The five rules every lane agent obeys

1. **You own your files. You never edit another lane's.** The ownership table in §2 is
   the boundary. If your task appears to need a file you do not own, you stop and raise it
   to the integrator — you do not edit it and you do not work around it.
2. **The shared core is append-only, and only through §1's protocol.** `state.py`,
   `config.py` and `agentorg/security/` are read-mostly. Additions go through the
   integrator, batched, before lanes start.
3. **Every test change carries a RED step.** Name the mutation, apply it, watch the
   *named* test fail, paste the failure, revert. A task whose RED step was not run is not
   done. This is the repository's standing rule and it is not relaxed for parallel work.
4. **You end every session with a working tree that passes the four gates**
   (`pytest -q`, `ruff`, `actionlint`, `terraform fmt`) and with no mutation applied.
5. **You do not break the demo.** If your change makes `scripts/preflight.py` exit
   non-zero or either demo path fail, you fix it or you revert it before you hand off.

### Why lanes are drawn by FILE, not by topic

Learned the hard way on the pre-demo work: lanes drawn by topic all wanted
`scripts/run_stage.py`, and the collision surfaced at integration rather than at planning.
The ownership table below is therefore the primary artifact of this plan. Read it before
your brief.

**The two numbers that make this safe:** `state.py` is imported by **54 files** and
`config.py` by **36**. Nothing else comes close. If any lane may edit those, every lane
blocks on that lane. Hence §1.

---

## 1 · Phase 0 — the contract batch (integrator only, before any lane starts)

**Nothing parallel happens until this lands.** One commit, made by the integrator, that
adds every field and knob the fourteen lanes will need. Then the core is frozen again for
the duration.

This works because it is already proven: **four optional fields have been added to
`state.py` since it was frozen** (`poisoned`, `model_provenance`, `trigger`,
`ci_status_measured`) without breaking any of its 54 importers. Same pattern, one batch.

| Task | File | What |
|---|---|---|
| 0.1 | `state.py` | `RunState.tenant_id: str = ""` — tenancy (Lane K) |
| 0.2 | `state.py` | `RunState.cost: CostRecord \| None = None` — token/cost totals (Lane E) |
| 0.3 | `state.py` | `CostRecord` model: per-stage input/output/cached tokens, model id |
| 0.4 | `state.py` | `SecurityResult.scoring: list[ScoreRow]` — the §8 transparency artifact (Lane C) |
| 0.5 | `state.py` | `ScoreRow` model: tool, native severity, mapped severity, threshold, blocking |
| 0.6 | `state.py` | `RunState.generated_tests: GeneratedTests \| None = None` (Lane G) |
| 0.7 | `state.py` | `RunState.retrieval: RetrievalRecord \| None = None` (Lane H) |
| 0.8 | `config.py` | `QUEUE_BACKEND`, `TENANT_MODE`, `SELF_HOSTED`, `RETRIEVAL_ENABLED` — each validated at import like `STATE_BACKEND` already is |
| 0.9 | tests | One test per new field asserting it defaults falsy and an old serialised `RunState` still loads |

**Every field optional, every default falsy.** A run written before this batch must still
load — that property is what has made four previous additions safe, and Task 0.9 pins it.

**Verification:** `pytest -q` green, and a `RunState` JSON blob from `runs/` (pre-batch)
round-trips.

---

## 2 · File ownership — the primary artifact

Read your row. Anything not in your row is not yours.

| Lane | Agent owns these files | Exclusively? |
|---|---|---|
| **A** | `agentorg/queue/**` (new), `scripts/worker.py` (new) | yes |
| **B** | `agentorg/tenancy/**` (new), `agentorg/db/**` (new) | yes |
| **C** | `agentorg/security/gitleaks_tool.py`, `trivy_tool.py`, `semgrep_tool.py`, `scoring.py` (new) | yes |
| **D** | `agentorg/integrations/**` (new), and `github_ops.py` **only** to extract its interface | yes |
| **E** | `agentorg/common/llm.py`, `agentorg/cost/**` (new) | yes |
| **F** | `agentorg/selfhost/**` (new), `infra/selfhost/**` (new) | yes |
| **G** | `agentorg/agents/testgen.py` (new), `target_repo/tests/e2e/**` (new) | yes |
| **H** | `agentorg/retrieval/**` (new) | yes |
| **I** | `web/app/api/**`, `web/lib/**` (new Next.js app) | yes |
| **J** | `web/app/(routes)/**`, `web/components/**` | yes — same tree as I, disjoint subdirectories |
| **K** | `agentorg/api/**` (new control-plane API) | yes |
| **L** | `docs/final/evidence/**` (new), `scripts/measure_*.py` (new) | yes |
| **M** | `agentorg/agents/{planner,developer,reviewer,security,sre}.py` — prompts only | yes |
| **N** | `.github/workflows/**`, `infra/Terraform/**` | yes |

**Contested files, and who may touch them:**

| File | Rule |
|---|---|
| `state.py`, `config.py` | **integrator only**, Phase 0 only |
| `agentorg/security/__init__.py`, `_run.py` | **nobody** this phase — the fan-out and the absent/broken classifier are load-bearing and already correct |
| `graph.py` | **integrator**, in Phase 2, once Lane A's queue exists |
| `scripts/run_stage.py` | **integrator**, Phase 3 — deleted, not edited |
| `agentorg/approve_server.py` | **integrator**, Phase 4 — deleted once Lane I/J ship approvals |
| `scripts/make_deck.py`, `docs/pitch/**` | Lane L, for the final deck |

---

## 3 · The phases

Lanes run in parallel *within* a phase. A phase ends when every lane in it is merged and
the demo is green.

### Phase 0 — contract batch · integrator · half a day
§1. Blocks everything.

### Phase 1 — foundations · Lanes A, B, C, E, L · parallel
Queue, tenancy, scoring and cost — the four every later lane depends on — plus evidence,
which starts here rather than later because it measures a *moving* system and needs today's
baseline before the other lanes change it.

### Phase 2 — the port · Lanes D, F, K + integrator on `graph.py` · parallel
The pipeline moves onto the queue. **The riskiest phase**; §5's gate applies hardest here.

**SCOPE CORRECTION, measured 2026-08-28 at the start of the phase.** "Port `graph.py`
onto the queue" was written before Lane A existed and its premise is wrong. Measured:

- **Nothing on the deployed path imports `graph.py` for orchestration.** `grep` for
  `run_pipeline(` across `agentorg/ scripts/ infra/` returns exactly one hit — its own
  `__main__` block. Every other caller is a test, in 17 files.
- **The cloud pipeline and Lane A's queue already share one implementation**:
  `run-pipeline.yml` runs `python scripts/run_stage.py <stage>`, and
  `queue/runner.py` runs the identical command as a subprocess. There is no second
  orchestrator to port.
- **The seven comment renderers are already shared.** `run_stage.py` calls
  `graph._plan_comment`, `_gate_comment`, `_develop_comment`, `_review_comment`,
  `_security_comment` and `_sre_comment` directly. `graph.py` is the renderer library
  plus a test-only walk, not a rival pipeline.

So porting `graph._walk` would move code nothing calls, while leaving the actual
duplication — `_walk`'s stage sequence versus `run_stage.py`'s six `_stage_*`
functions, 713 and 909 lines — exactly where it is. That duplication is real and it is
where CLAUDE.md's three mutations survived 793 tests, but it is a **Phase 3** job,
because §3 already deletes `run_stage.py` there "once the queue has run both demo
paths". Deleting the file is the port; doing both is doing it twice.

**What the integrator did in Phase 2 instead**, having established the above: fixed
`SecurityResult.scoring` being empty on both fixture paths, so a `fixture-fallback`
block no longer renders "no findings were scored" over three findings. Same class of
defect the phase was aimed at — a check whose output cannot distinguish "did not run"
from "found nothing" — reached from the direction that was actually broken.

### Phase 3 — product surface · Lanes G, H, I, J, M · parallel
Tests, retrieval, the UI. `run_stage.py` is deleted here, by the integrator, once the
queue has run both demo paths.

**THE PRECONDITION IS MET AND THE DELETION IS STILL WRONG. `run_stage.py` STAYS.**

Both demo paths ran on the queue, measured 2026-08-28 with `QUEUE_BACKEND=postgres`,
`OFFLINE=true LLM_DISABLED=true`, no GitHub Actions anywhere:

```
POISONED  plan → [gate1 PAUSED, released by --approve] → develop
          status=blocked        worker exit 3
CLEAN     plan → gate1 → develop → gate2 → sre → gate3 → promote
          status=promoted       security=pass       decisions=3
```

The gate genuinely paused and waited for a human — the property a GitHub Environment
provides by holding a runner slot, provided instead by a row.

**But the queue does not REPLACE `run_stage.py`, it INVOKES it**, and that is
deliberate. `agentorg/queue/runner.py:49` is `_RUN_STAGE = _ROOT / "scripts" /
"run_stage.py"`, and its module docstring gives two reasons that are still true:

- **Per-stage process isolation.** Two pieces of module state would otherwise be
  inherited by an in-process call, and both produce a *false measurement* rather than a
  crash: `llm._record`/`last_source()` (a run inheriting the previous run's provenance
  "looks like a measurement"), and the scanner fan-out memo (`tests/conftest.py`'s fifth
  guard exists because "a stale cache hit looks exactly like a scan"). A worker running
  seven stages in one process inherits both, all day, with nothing clearing them.
- **The exit code.** `run_stage.py` communicates through `sys.exit(main())`. A subprocess
  guarantees that an `os._exit`, a segfault or a `SystemExit` raised anywhere in the tree
  still arrives as a number. `3 ≠ 1` is what makes a blocked demo run distinguishable
  from a broken workflow on a projector.

Measured blast radius of deleting it: **15** invocations in `run-pipeline.yml`, **2**
references in `queue/runner.py`, **17** test files. Deleting it breaks the deployed cloud
pipeline *and* the queue that was meant to replace it.

So the plan's phrase "deleted, not edited" was written expecting the queue to reimplement
the stages in-process. Lane A chose the subprocess instead, for reasons its docstring
argues better than the plan did — and that choice makes `run_stage.py` the **shared stage
implementation both paths run**, not a rival to be removed. The right reading of the
ownership row is now *nobody edits it casually*, which it already was.

The real remaining duplication is `graph._walk`'s stage sequence versus `run_stage.py`'s
six `_stage_*` functions. That is a genuine defect — CLAUDE.md records three mutations
surviving 793 tests there — and it is **not** fixed by deleting either file, because
`_walk` is the test-only path and `run_stage.py` is the deployed one. Left as named debt
rather than closed badly a week before the final.

### Phase 4 — hardening and the deck · Lanes L, N + integrator · parallel
`approve_server.py` retired. Deck rebuilt. Limitations costed.

**Find yourself:**

| Phase | Lanes in parallel | Integrator's own work |
|---|---|---|
| 0 | — | the contract batch (§1) |
| 1 | A · B · C · E · L | merge order, daily gate |
| 2 | D · F · K | `graph.py` onto the queue |
| 3 | G · H · I · J · M | delete `run_stage.py` |
| 4 | L · N | retire `approve_server.py` |

A lane appearing in two phases (L) does different work in each: baseline first, final
numbers last.

---

## 4 · The fourteen lanes

Each lane below states its **owner files**, its **tasks**, its **done test**, and whether
it should **fan out to subagents**. A lane agent reads only its own section plus §0–§2.

---

### LANE A · the job queue
**Spec:** §12. **Owns:** `agentorg/queue/**`, `scripts/worker.py`. **Fan out:** no —
sequential by nature. **Phase 1.**

This lane replaces what GitHub Actions provides today: sequencing, artifact handoff,
pausing for approval, per-job isolation. **The pause is the interesting one.** The current
seven-job shape exists entirely because "a GitHub Environment pauses a *job*, and a job
cannot pause in its middle". A queue with durable state removes that constraint — so the
seven jobs may collapse, but **the three gates must not**.

| # | Task |
|---|---|
| A1 | Queue interface: `enqueue`, `claim`, `complete`, `fail`, `pause`, `resume`. One module, no backend yet |
| A2 | An in-process backend, so tests need no infrastructure — this is what keeps the suite hermetic |
| A3 | A durable backend (SQS or Postgres — decide in A2's ADR, record why) |
| A4 | Durable pause/resume: a paused run survives a worker restart. **The property Actions cannot give us** |
| A5 | `scripts/worker.py`: claim → run one stage → record → re-enqueue or pause |
| A6 | Idempotency: claiming the same job twice must not run an agent twice. An agent invocation writes a PR comment and burns tokens — a silent double-run is a real cost, exactly as `agent_client`'s disabled retries already recognise |
| A7 | Exit-code parity with today: block=3, refusal=4, already-final=5. The demo's meaning depends on 3 ≠ 1 |
| A8 | Crash recovery: a worker killed mid-stage leaves the run claimable, not lost |
| A9 | Tests: pause survives restart; double-claim runs once; a killed worker's run recovers |

**Done when:** a full pipeline runs end to end on the queue with no GitHub Actions
involved, produces the same `RunState`, and a poisoned run still exits 3.

---

### LANE B · tenancy and persistence
**Spec:** §12. **Owns:** `agentorg/tenancy/**`, `agentorg/db/**`. **Fan out:** yes — schema,
scoping and the isolation suite are independent. **Phase 1.**

| # | Task |
|---|---|
| B1 | Schema: organisation, user, membership, repository, run, secret, budget |
| B2 | Migrations, forward-only, runnable against an empty database |
| B3 | Tenant-scoped accessors — **every** read and write takes a tenant, no default |
| B4 | Per-tenant secret storage, encrypted at rest, never logged. This project has already leaked a token into a Terraform plan artifact; the postmortem is in `CLAUDE.md` and must not repeat at multi-tenant scale |
| B5 | Budgets: a tenant's ceiling, checked **before** a run starts |
| B6 | Tenant zero: today's single-tenant deployment migrates in, loses nothing |
| B7 | **The leak test.** A suite that *attempts* cross-tenant access on every accessor and asserts refusal. Not "assert isolation" — attempt the breach. This is the one defect that would end the product |
| B8 | Row-level enforcement at the database, not only in application code |

**Done when:** the leak suite attempts every cross-tenant path and every one is refused,
and tenant zero's existing runs are readable.

---

### LANE C · deterministic scoring
**Spec:** §8 — **highest priority in the specification.** **Owns:** the three scanner
wrappers and a new `scoring.py`. **Fan out:** yes — one subagent per scanner. **Phase 1.**

A judge doubted the determinism claim. Investigating found a real inconsistency: trivy and
semgrep map their native severities, but **gitleaks hardcodes every finding to
`critical`** (`gitleaks_tool.py:190`). The claim "a fixed threshold decides" is exactly
true for two scanners and *vacuously* true for the third.

| # | Task |
|---|---|
| C1 | `scoring.py`: one table, every scanner, native → ours, with the fail-closed default explicit |
| C2 | gitleaks: keep the constant, **document it as policy** — "any finding from a secret scanner is critical, by rule" — and make the code say so where today it merely does so |
| C3 | trivy: unchanged behaviour, moved behind the shared table |
| C4 | semgrep: unchanged behaviour, moved behind the shared table |
| C5 | Emit `ScoreRow` per finding onto `SecurityResult.scoring` (field from Phase 0) |
| C6 | Render the scoring table into the PR comment — the literal answer to "how do we know it is go or no-go" |
| C7 | **Fail-closed test per scanner**: feed an unrecognised severity, assert the mapped result is *not* below the threshold. Semgrep's own docstring records why: its table defaulted to `"low"`, so rules it marked CRITICAL could not block a change, and **no test read the function** |
| C8 | Threshold floor: a configured threshold may not be set so high that secrets stop blocking. A knob that can disable the core guarantee is a defect |
| C9 | Property test: for any finding set, `compute_security_verdict` is a pure function of (severities, threshold) — the determinism claim, mechanised |

**Done when:** every scanner's mapping is in one table, each has a fail-closed test with a
RED step, and a run emits a scoring row per finding.

**Do not touch** `security/__init__.py` or `_run.py`. The fan-out and the absent/broken
classifier are correct and load-bearing.

---

### LANE D · integration adapters
**Spec:** §5, §12. **Owns:** `agentorg/integrations/**`; `github_ops.py` **only** to extract
its interface. **Fan out:** yes — interface, GitHub adapter, and the fake are separable.
**Phase 2.**

`github_ops.py` is 1,132 lines and imported by 20 files. It becomes *one adapter behind one
interface*, so GitHub stops being the substrate.

| # | Task |
|---|---|
| D1 | Define the interface from what `graph.py` actually calls — derive it, do not design it fresh |
| D2 | Move the existing implementation behind it, **no behaviour change**, suite still green |
| D3 | Preserve the two hard contracts: `post_comment` returns a ref in every case and never raises; `merge_pr` likewise |
| D4 | An in-memory adapter for tests, replacing per-test stubs |
| D5 | Adapter conformance suite — every adapter passes the same tests |
| D6 | A second adapter sketch (GitLab or plain git) to prove the interface is real. **Not** shipped — a one-adapter interface is an unproven claim |

**Done when:** `graph.py` imports the interface rather than `github_ops`, the suite is
green, and the conformance suite passes for two adapters.

---

### LANE E · cost and token instrumentation
**Spec:** §4, and req 9's cost view. **Owns:** `llm.py`, `agentorg/cost/**`. **Fan out:**
no. **Phase 1 — blocks Lanes I/J and L.**

**There is no cost tracking today** — verified, `llm.py` records no usage. Requirement 2
and the cost half of requirement 9 are both unanswerable until this exists.

| # | Task |
|---|---|
| E1 | Record `usage` on every model call: input, output, cached tokens, model id, stage |
| E2 | Carry it on `RunState.cost` (Phase 0 field) |
| E3 | Cross the remote seam — the model call happens in the container, so usage must travel back on the response envelope, exactly as `source` already does |
| E4 | A price table, per model, versioned, with the date it was read |
| E5 | Cost per run, per repository, per period |
| E6 | **Report the cache.** The five agents re-send a repository snapshot on every call; `cache_read_input_tokens` must be non-zero and visible. An unmeasured cache is the largest silent cost in the current design |
| E7 | Tests: usage recorded per stage; totals sum; a fixture fallback records zero rather than nothing |

**Done when:** a completed run reports total tokens, cost, and cache hit rate, and the
numbers reconcile against the provider's own usage figures.

---

### LANE F · self-hosted path
**Spec:** §6. **Owns:** `agentorg/selfhost/**`, `infra/selfhost/**`. **Fan out:** yes —
model, runner and docs are independent. **Phase 2.**

The cheapest true win in the spec: `config.LLM_BASE_URL` already routes to an
OpenAI-compatible gateway, and the scanners already run locally in our own container.

| # | Task |
|---|---|
| F1 | Serve a local model (vLLM or Ollama), point `LLM_BASE_URL` at it, run the pipeline |
| F2 | Record the parity difference **with numbers** — revision counts, verdicts, wall clock — not adjectives |
| F3 | Self-hosted execution: Lane A's worker on your own compute. Largely *satisfied by* Lane A rather than built here |
| F4 | Compose for the whole stack — UI, API, worker, Postgres. **The self-hosted demo vehicle, not a dev convenience**: `docker compose up` then a poisoned ticket blocks, with no AWS call. Phase 0 already made `postgres` a valid `QUEUE_BACKEND`, so the queue and the app share one database. Helm only if the calendar allows |
| F5 | A one-command self-hosted demo, recorded, ending in the same block verdict |
| F6 | Name the degradations explicitly. If the local model writes worse diffs, show the revision-count delta |

**Done when:** a poisoned ticket blocks on a fully self-hosted stack with no AWS call, and
the parity table has measured numbers in it.

---

### LANE G · generated tests and Selenium
**Spec:** §9. **Owns:** `agentorg/agents/testgen.py`, `target_repo/tests/e2e/**`. **Fan
out:** yes. **Phase 3.**

| # | Task |
|---|---|
| G1 | A test-generation stage that reads the repository through the existing snapshot seam. An agent asked to test a file it cannot see invents imports — this project has measured that once already |
| G2 | Generate from the **ticket's acceptance criteria**, not from the diff. **The agent that wrote the change must not be the sole author of the test that clears it** — the same separation-of-authority principle as Lane C, one layer out |
| G3 | Minimal templates in the target app so there is a browser surface to drive |
| G4 | Selenium tests running in the pipeline against a live instance |
| G5 | Verdict integration: a **failing** generated test is binding (it is a fact); a **missing** one is advisory |
| G6 | Flake policy: retries, quarantine, and a rule that a quarantined test's absence is *reported* rather than silent. A flaky blocker gets disabled by the first person it inconveniences, and then the gate is theatre |
| G7 | Do not let a green generated test be quoted as proof of correctness — it proves less than a red one |

**Done when:** a run generates tests, executes them in the pipeline, and a deliberately
broken change is caught by a generated test rather than by a scanner.

---

### LANE H · retrieval
**Spec:** §10. **Owns:** `agentorg/retrieval/**`. **Fan out:** yes — one subagent per
corpus. **Phase 3.**

Most likely of all fourteen lanes to become a demo of a vector database rather than a
product improvement. The acceptance test is therefore a *moved number*.

| # | Task |
|---|---|
| H1 | Retrieval interface with provenance — a run must be able to say what it retrieved and from where, consistent with how scanner provenance already works |
| H2 | Corpus 1: the target repository's history — why a past change was rejected |
| H3 | Corpus 2: project conventions and prior review comments, so the reviewer stops re-litigating settled questions |
| H4 | Corpus 3: CVE and remediation context, so the security agent's prose is specific |
| H5 | **The hard boundary: retrieval may never reach the gate.** An assertion in code, not a comment. Otherwise a poisoned document becomes a way to argue past the threshold — the exact attack the deterministic gate exists to prevent |
| H6 | Measure one corpus: revision count, false-block rate, or objection quality. One number, moved |
| H7 | A test that *attempts* to influence the verdict through retrieved text and asserts it cannot |

**Done when:** one corpus has a measured before/after, and H7 proves retrieval cannot
reach the verdict.

---

### LANE I · web API and data layer
**Spec:** §11. **Owns:** `web/app/api/**`, `web/lib/**`. **Fan out:** yes. **Phase 3.**
**Publishes the contract Lane J consumes — write it first, in one commit, and hand it over.**

| # | Task |
|---|---|
| I1 | **Auth.js (NextAuth)**, GitHub OAuth as the primary provider, sessions in Postgres. NOT Cognito — spec §11 records why: it would put an AWS service in the one auth path requirement 4 needs to run without AWS, and the GitHub grant is required anyway, so this collapses sign-in and account-linking into one flow |
| I2 | GitHub account/installation linking, repository scoping, revocation |
| I3 | Run list and detail endpoints, tenant-scoped through Lane B |
| I4 | **A real-time transport** — SSE or websockets, sourced from Lane A's queue events. Polling every two seconds is the first thing a judge notices |
| I5 | Approval endpoints. **This is a security surface**: authentication, per-repository authorisation, an audit record of who approved what, CSRF protection |
| I6 | Cost endpoints reading Lane E |
| I7 | The scoring artifact endpoint reading Lane C |
| I8 | Tests: authorisation per endpoint; an unauthorised approval attempt is refused and recorded |

**Done when:** every screen Lane J needs has an endpoint, approvals are authorised and
audited, and run events stream live.

---

### LANE J · web UI
**Spec:** §11. **Owns:** `web/app/(routes)/**`, `web/components/**`. **Fan out:** yes — one
subagent per screen. **Phase 3.** **Blocked on I's contract commit.**

**Use the `frontend-design` skill** for the marketing surface and the empty/error states,
where a default template shows most. The product surface inherits the deck's identity:
near-black, one cyan accent for structure, rose/mint for refused/shipped, mono for
identifiers. That palette was tested in front of judges and is genuinely apt — this is an
instrument and should look like one.

| # | Task |
|---|---|
| J1 | Sign up / sign in / reset |
| J2 | Account linking and repository selection |
| J3 | **The live run view** — seven stages, current stage, output as it arrives. The screen the demo lives on |
| J4 | Gate controls: approve and reject, in the product |
| J5 | Run history with verdict, findings, provenance |
| J6 | Cost views: per run, per repository, per period |
| J7 | The scoring table per finding — Lane C's artifact, rendered |
| J8 | Empty, loading and error states. An error says what happened and how to fix it, in the interface's voice |
| J9 | Quality floor: responsive to mobile, visible keyboard focus, reduced motion respected |

**Done when:** a judge can sign up, link a repository, watch a run, approve a gate, and see
what it cost — without a terminal.

---

### LANE K · control-plane API
**Spec:** §12. **Owns:** `agentorg/api/**`. **Fan out:** yes. **Phase 2.**

The control plane / data plane split this project already understands from AgentCore:
configuration and orchestration on one side, execution on the other.

| # | Task |
|---|---|
| K1 | Run submission: accept a ticket, enqueue, return an id |
| K2 | Run status and cancellation |
| K3 | Repository configuration: thresholds (with Lane C's floor), which checks are on |
| K4 | Webhook ingress, generalised — the existing HMAC Lambda becomes one entry point among several |
| K5 | Machine-to-machine auth for CI callers |
| K6 | OpenAPI schema, generated from the code rather than written beside it |
| K7 | Tests: submission is idempotent under retry; cancellation is honoured mid-run |

**Done when:** a run can be submitted, watched and cancelled through the API with no
GitHub involvement.

---

### LANE L · evidence and the deck
**Spec:** §3, §4, §5, §7, §13. **Owns:** `docs/final/evidence/**`, `scripts/measure_*.py`,
and the deck in Phase 4. **Fan out:** yes — the four evidence artifacts are independent.
**Phase 1 (baseline) and Phase 4 (final numbers).**

**Starts in Phase 1 on purpose:** it measures a moving system, so it needs today's baseline
before the other lanes change it.

| # | Task |
|---|---|
| L1 | The evolution scorecard (§3): dimensions, a baseline row measured today, a promotion rule with a no-regression clause on block correctness |
| L2 | **A recorded rejection.** At least one case where the scorecard said no. A rubric that has never rejected anything is decoration — this project already learned that about tests |
| L3 | The cost comparison (§4): three scenarios — human + Claude Code, cloud, self-hosted — measured, not estimated, quoted as a range with its conditions |
| L4 | The dependency inventory (§5): substitutable / seam-bound / load-bearing, with a named blast radius each |
| L5 | SBOM for the container image, pinned versions, a scanner-update process |
| L6 | The competitor matrix (§7), **including where competitors are better** — IDE integration, language breadth, polish. A matrix we win every row of is believed by nobody |
| L7 | The limitations document (§13), each one **costed**: what removing it would take, and why not this phase |
| L8 | The final deck, via `scripts/make_deck.py`, reusing the established generator and its self-checks |
| L9 | Re-measure every number in the deck the day before. The test count moves whenever anyone adds a test |

**Done when:** every claim in the deck traces to a script in the repository that produces
it.

---

### LANE M · agent prompts
**Spec:** §9, §10 (consumers of new context). **Owns:** the five agents' prompt text only.
**Fan out:** no — prompt changes need one consistent voice. **Phase 3.**

Prompts are a *file* lane rather than a topic lane precisely so no other lane edits them.

| # | Task |
|---|---|
| M1 | Give the developer and reviewer the generated-test context from Lane G |
| M2 | Give the security agent the retrieval context from Lane H, for **prose only** |
| M3 | Give the SRE agent the cost context from Lane E |
| M4 | Re-verify the stack naming still holds. An agent asked to edit a file with nothing saying what language it is in was measured writing Go for a Flask app |
| M5 | Prompt-change discipline: one change at a time, with the revision-count effect measured. A prompt edit is a behaviour change with no compiler |

**Done when:** each prompt change has a measured effect on revision count or verdict
quality, and no agent's `run()` signature changed.

---

### LANE N · CI, workflows and infrastructure
**Spec:** §12, §6. **Owns:** `.github/workflows/**`, `infra/Terraform/**`. **Fan out:** yes.
**Phase 4.**

| # | Task |
|---|---|
| N1 | Deploy the queue worker, the API and the web app |
| N2 | Terraform for the new components, following the existing module pattern |
| N3 | Keep `run-pipeline.yml` alive until Phase 3 proves the queue — **it is the demo's fallback** |
| N4 | Extend `preflight.py` to cover the new components. Every check must answer a question whose wrong answer has already happened here, and it must fail on a version mismatch, not only on a bad status |
| N5 | Deploy-blast-radius tests for anything that can spend money, matching the 108 that already guard `deploy.yml` and `terraform.yml` |
| N6 | **A workflow expression is tested by evaluating it, never by reading it.** This repository has one recorded case of a test that *required* a bug: it asserted a broken `== 'true'` comparison was present, so it passed on the defect and would have failed on the fix. Only the deployed run could tell |

**Done when:** the new stack deploys from a workflow with no static keys, and preflight
covers it.

---

## 5 · The gate every phase passes

**At the end of every working day, on `main`:**

```bash
.venv-main/bin/python -m pytest -q                          # green
.venv-main/bin/python -m ruff check agentorg scripts tests  # exit 0
actionlint .github/workflows/*.yml                          # exit 0
cd infra/Terraform && terraform fmt -check -recursive        # exit 0
.venv-main/bin/python scripts/preflight.py                  # exit 0
```

**And at the end of every phase**, both demo paths run end to end: a clean ticket merges,
a poisoned ticket blocks with `provenance: scanners` and exits 3.

If a lane cannot meet this, it reverts. A phase that ends with the demo broken has moved
the project backwards regardless of how much code it added.

---

## 6 · How the integrator merges fourteen lanes

1. **Merge in phase order, one lane at a time**, running the four gates between each.
2. **A lane that touches a file it does not own is rejected**, not fixed up. The ownership
   table is the contract; repairing a violation teaches the wrong lesson and hides the
   collision.
3. **A lane whose tests pass but whose RED steps were not run is not merged.** Ask for the
   pasted failure. Nineteen-plus assertions in this repository turned out to pin nothing.
4. **After each merge, re-run preflight.** Not the suite alone — the suite is hermetic by
   design and cannot see a deployment.
5. **Batch the Terraform applies.** They are slow and they cost money.

---

## 7 · The risks, named

| Risk | Why it is real | Mitigation |
|---|---|---|
| **The demo breaks mid-rewrite** | Actions is being replaced under a working system | §5's daily gate; `run-pipeline.yml` stays alive until Phase 3 proves the queue |
| **Two lanes edit one file** | It has already happened here | §2's table; violations rejected rather than merged |
| **The queue's pause is subtly wrong** | It is the property Actions gave us for free, and the gates depend on it | A4 and A9 test restart survival explicitly |
| **Cross-tenant leakage** | The one defect that ends the product | B7 *attempts* the breach rather than asserting its absence |
| **Retrieval reaches the verdict** | It would reopen the exact attack the gate prevents | H5 asserts it in code; H7 attempts it |
| **The UI is a polling demo** | Judges notice immediately | I4 is a real transport, sourced from queue events |
| **A prompt change regresses quality silently** | A prompt edit has no compiler | M5: one at a time, measured |
| **Cost numbers are estimates** | Req 2 collapses without instrumentation | E is Phase 1 and blocks L |
| **Fourteen lanes is too many for the calendar** | It might be | Phases 1 and 2 are load-bearing; Phase 3's G and H are the first things to cut, and the spec's §13 already treats an honest omission as acceptable |

---

## 8 · What to cut, if the calendar says so

In this order, and say so on the slide rather than leaving a gap:

1. **Lane H (retrieval)** — the judges said "maybe" for a reason.
2. **Lane G's Selenium half** — keep test generation, drop the browser layer.
3. **Lane D's second adapter** — keep the interface, drop the proof.
4. **Lane F's Helm chart** — keep the self-hosted model demo, drop the packaging.

**Never cut:** Lane C (the determinism answer), Lane E (cost instrumentation), Lane B's
leak test, or §5's daily gate.
