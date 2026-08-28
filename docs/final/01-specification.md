# The Agent Org — final phase specification

**Status:** specification. Not an implementation plan. This document defines *what* must
be true at the final evaluation and *why*; the implementation plan that follows it
(`docs/final/02-implementation-plan.md`, to be written from this) will define *how* and
in *what order*, sliced for subagents.

**Written:** 2026-08-28, after the pre-final evaluation.
**Baseline:** commit `0b6f537`. 1,102 tests passing, 5 AgentCore runtimes at v19, both
demo paths verified end to end on 2026-08-22.

---

## 0 · What the judges asked for

Ten items, verbatim from the pre-final feedback, each mapped to the sections that satisfy
it. Nothing in this document exists without a line in this table.

| # | The judges' words | Kind | Section |
|---|---|---|---|
| 1 | evolution criteria | Evidence | §3 |
| 2 | time and cost vs Claude Code | Evidence | §4 |
| 3 | external dependency | Evidence + code | §5 |
| 4 | self hosted? | Code | §6 |
| 5 | competitive advantage? | Positioning | §7 |
| 6 | gitleaks and trivy — how we score response so we know it is go or no-go, as you claimed it is deterministic | **Code, and a real inconsistency** | §8 |
| 7 | test case automation generation and run selenium in the pipeline | Code | §9 |
| 8 | maybe we add rag or knowledge lake | Code | §10 |
| 9 | fully fledged UI in Next.js — sign up/in, link account, see agents and pipeline running in real time, status and cost | Code | §11 |
| 10 | restructure this project to be a SaaS service | **Architecture** | §12 |

**Two decisions already taken, and they shape everything below:**

- **Req 10 is a rewrite, not an addition.** A SaaS-first core — tenants, a job queue, a
  control-plane API — is built first, and the existing pipeline is ported onto it. GitHub
  becomes one integration among several rather than the substrate.
- **Req 3/4 answer is "both".** AWS Bedrock stays the default; a self-hosted path
  (own model, own runners) must be *demonstrable*, not merely documented.

---

## 1 · The one rule that protects the demo

The rewrite's honest risk is arriving at the final with a half-ported system and no
working demonstration. One rule prevents that, and every phase of the implementation plan
is subordinate to it:

> **The current demo must pass on `main` at the end of every working day.**
> `scripts/preflight.py` exits 0 and both paths run, or the day's work does not merge.

This is affordable because of how the value is distributed. Measured at baseline:

| What | Lines | Fate in the rewrite |
|---|---:|---|
| `agentorg/security/` — the scanners | 1,727 | **ports unchanged** |
| `agentorg/github_ops.py` — the GitHub seam | 1,132 | becomes one *integration adapter* |
| `agentorg/agents/` — the five agents | 1,058 | **ports unchanged** |
| `scripts/run_stage.py` — Actions glue | 909 | **replaced** by a queue worker |
| `agentorg/graph.py` — the in-process walk | 697 | becomes the orchestrator core |
| `agentorg/state.py` — the frozen contract | 300 | **ports unchanged**, still frozen |

So roughly 3,000 lines — the agents, the scanners, the contract — are the asset and move
across intact. One 909-line file is genuinely Actions-shaped and is the actual rewrite.
That asymmetry is what makes "rewrite" survivable, and the implementation plan must
preserve it: **no phase may touch `state.py`'s existing fields, the five agents' `run()`
signatures, or `agentorg/security/` except additively.**

**The corollary, stated plainly:** `graph.run_pipeline` — the in-process walk — becomes
the *primary* execution path rather than the fallback it is today. It already runs the
whole suite. That is not a new engine; it is a promotion.

---

## 2 · How the final evaluation will be judged, and what that implies

The pre-final was judged on *"does it work"*. The final adds *"is it a product"* and
*"do you know its limits"*. Three properties therefore matter more than feature count:

1. **Every claim is measured.** The standing rule from `CLAUDE.md` — numbers in prose come
   from a command whose output was pasted — extends to the spec. Every number in §3 and §4
   must be reproducible by a script in the repository.
2. **Every limitation is ours before it is theirs.** The pre-final's strongest moment was
   volunteering a known limitation. §13 is a first-class deliverable, not an appendix.
3. **The deterministic gate survives contact.** Req 6 exists because a judge doubted the
   determinism claim. §8 is the highest-priority section in this document.

---

## 3 · Evolution criteria (req 1)

**The ask, interpreted.** "Evolution criteria" is how you decide whether the *next*
version is better than the current one — a rubric, applied to itself, with numbers.

**What must exist:**

- **A scorecard, versioned in the repo**, with a row per dimension and a measured value
  per release. Dimensions must be things this system can actually move:
  *block correctness* (poisoned blocked / poisoned run), *false-block rate* (clean runs
  wrongly blocked), *time to merge*, *cost per merged change*, *human touches per change*,
  *agent-attributable rework* (revisions caused by the agent misreading the ticket),
  *escaped defects* (things that shipped and should not have).
- **A baseline row measured today**, so the final can show movement rather than a claim.
- **A promotion rule**: what must hold for a change to this pipeline to be accepted. It
  must include a *no-regression* clause on block correctness — the gate is the product,
  and a release that ships faster while blocking less is a worse release.
- **A rejection example.** At least one recorded case where the scorecard said no. A rubric
  that has never rejected anything is decoration; this project has already learned that
  lesson about tests.

**Deliberately excluded:** vanity metrics (lines of code, number of agents, model size).
They cannot be moved in a direction that matters.

---

## 4 · Time and cost vs Claude Code (req 2)

**The ask, interpreted.** The judges want to know whether this system is economically
sensible next to a developer driving Claude Code by hand. That is a fair and dangerous
question — dangerous because the honest answer is *"it depends on the failure you are
buying insurance against"*, and a hand-wave there reads as evasion.

**The comparison must be like-for-like or it is worthless.** Three scenarios, same ticket,
same target repository, measured not estimated:

| Scenario | What is measured |
|---|---|
| **A · Human + Claude Code** | wall-clock from ticket to merged PR, tokens consumed, reviewer minutes spent |
| **B · The Agent Org, cloud default** | same three, plus gate wait time excluded and reported separately |
| **C · The Agent Org, self-hosted** | same three, with model cost replaced by compute cost |

**What must exist:**

- **Token and cost instrumentation on every model call.** There is none today — verified,
  `agentorg/common/llm.py` records no usage. Every call must record input tokens, output
  tokens, cached tokens, model id, and the stage that made it, onto the run's own record.
  Without this, req 2 and the cost half of req 9 are both unanswerable.
- **A cost model that separates the three things that actually differ:** model inference,
  the compute the pipeline runs on, and *human minutes*. Human minutes are the expensive
  line and the one a judge will recognise.
- **The honest framing, on a slide:** this pipeline is not cheaper per change than a
  competent developer with Claude Code. It is cheaper per *escaped credential*, and it
  does not get tired at 6pm. The comparison table must make the cost delta visible rather
  than hidden, and the value claim must be about the class of failure it removes.
- **Prompt caching, measured.** The five agents re-send a large repository snapshot on
  every call. `usage.cache_read_input_tokens` must be non-zero and reported — an unmeasured
  cache is the single largest silent cost in the current design.

**A trap to avoid:** do not quote a single dollar figure as *the* cost. Cost varies with
ticket size, revision count and cache hit rate. Quote a range with its conditions, the way
`CLAUDE.md` already requires for timings.

---

## 5 · External dependency (req 3)

**The ask, interpreted.** What breaks this system if a third party changes, and how locked
in are we?

**What must exist:**

- **A dependency inventory** distinguishing three severities: *substitutable* (a scanner —
  swap the binary), *seam-bound* (GitHub — one adapter behind one interface), and
  *load-bearing* (the model provider). Measured at baseline: 33 references to `bedrock`,
  13 to `amazonaws`, 5 to `github.com`, 1 to `openai`.
- **A named blast radius per dependency**: what stops working, what degrades, what is
  unaffected. This project already knows how to answer this well for scanners — absent
  versus broken, with different answers. Extend that discipline outward.
- **A supply-chain answer**, because a security product will be asked: pinned versions, SBOM
  for the container image, and a documented scanner-update process. This is also req 10's
  problem (a SaaS vendor owns its supply chain) and should be built once.

---

## 6 · Self-hosted (req 4)

**The ask, interpreted.** Can this run on infrastructure the customer controls?

**The answer must be a demonstration, not a paragraph.** A self-hosted run, shown or
recorded, ending in the same block verdict as the cloud run.

**What must exist:**

- **A self-hosted model path, proven.** `agentorg/common/config.py` already carries
  `LLM_BASE_URL` and routes to an OpenAI-compatible gateway when set — this seam exists and
  is the cheapest true win in the whole spec. Point it at a locally served model and record
  the result. The five agents need no change.
- **A self-hosted execution path.** The queue worker from §12 removes the dependency on
  GitHub-hosted runners by construction; this requirement is largely *satisfied by* the
  SaaS core rather than built separately.
- **The scanners are already local.** gitleaks, trivy and semgrep run in our own container
  today. State this — it is a strength that currently goes unmentioned.
- **A parity statement with teeth:** exactly which capabilities degrade when self-hosted,
  named, not implied. If the self-hosted model is weaker at writing diffs, say so and show
  the revision-count difference.

**The honest limit to state up front:** the *human gates* currently depend on GitHub
Environments. A fully self-hosted deployment needs its own approval mechanism, which the
§11 UI provides — so this requirement and req 9 are coupled, and the plan must sequence
them accordingly.

---

## 7 · Competitive advantage (req 5)

**The ask, interpreted.** Why this and not GitHub Copilot Workspace, Devin, Cursor's agent,
or a shell script wrapping Claude Code?

**The differentiator is not "we use agents".** Everyone uses agents. It is the structure
around them, and it is already built:

1. **The shipping decision is not a model's.** A severity comparison over scanner output
   decides; the model writes prose. Tested adversarially — a reply insisting the change was
   safe left the verdict at `block`. Competitors that ask a model "is this safe?" cannot
   make this claim, and the difference is demonstrable in thirty seconds.
2. **The refusal is structural, not conditional.** A blocked run's later stages are never
   created. There is no flag to flip and no branch to talk past.
3. **Human approval is platform-enforced**, not a step in our own code that we could skip.
4. **Provenance on every verdict.** The system distinguishes "scanned" from "fell back to a
   fixture" and says which. Most pipelines cannot tell you whether a check actually ran —
   which is this project's founding observation.

**What must exist:** a competitor matrix scored on those four axes plus the obvious ones
(cost, setup time, self-hostability), with an explicit statement of where competitors are
*better* — they are: IDE integration, breadth of language support, polish. A matrix where
we win every row is not believed by anyone.

---

## 8 · Deterministic scoring — gitleaks and trivy (req 6)

**Highest priority in this document.** A judge specifically doubted the determinism claim,
and investigating it found a real inconsistency the current story papers over.

**What is true today, measured:**

| Scanner | How severity is assigned | Where |
|---|---|---|
| **trivy** | maps trivy's own severity (`UNKNOWN`/`LOW`/…/`CRITICAL`) onto our four, failing closed on an unrecognised value | `trivy_tool.py:52` |
| **gitleaks** | **every finding is hardcoded `critical`** | `gitleaks_tool.py:190` |
| semgrep | maps its own severity | `semgrep_tool.py` |

**The problem.** The claim is "a fixed severity threshold decides". For trivy and semgrep
that is exactly true. For gitleaks it is *vacuously* true: every gitleaks finding is
critical, so the threshold never discriminates. That is defensible for a secret scanner —
a leaked credential is not a "medium" — but it is not what the sentence sounds like, and a
judge reading the code will notice. **Being caught papering over this would cost more than
the finding itself.**

**What must exist:**

- **An explicit, documented scoring policy** — one table, in the repository, that states
  for every scanner how a native severity becomes one of our four, including the
  fail-closed default and *including gitleaks' constant*, with its justification.
- **The gitleaks constant made honest.** Either it is deliberate and documented as a
  policy decision ("any verified secret is critical, by rule"), or it becomes a real
  mapping over gitleaks' own signals (rule id, entropy, verification status). The spec's
  position: keep it constant, *say so loudly*, and treat the constant as the policy.
- **A scoring transparency artifact per run**: for each finding, the scanner's native
  severity, our mapped severity, the threshold, and the resulting go/no-go. The judge's
  question is literally "how do we know it is go or no-go" — this artifact is the answer,
  and it belongs on the PR comment and in the UI.
- **Fail-closed proven for every scanner, not asserted.** A test per scanner feeding an
  unrecognised severity string and asserting the mapped result is *not* below the
  threshold. One of these already caught a live defect in semgrep (`or "low"` silently
  downgraded real CRITICAL findings); the same shape must be pinned everywhere.
- **Threshold configurability, with a floor.** Per-project thresholds are on the roadmap
  (§10 of the pre-final deck). A configurable threshold must have a floor that cannot be
  set so high that secrets stop blocking — a knob that can disable the product's core
  guarantee is a defect, not a feature.

---

## 9 · Generated tests and Selenium in the pipeline (req 7)

**The ask, interpreted.** The pipeline should generate test cases for the change it wrote,
and run browser-level tests as part of the gate.

**Why this is more than a feature.** It closes the loop on the one gap the current design
concedes: the reviewer can be wrong and the scanners only see patterns. A generated test
that *fails* is evidence no model produced.

**What must exist:**

- **A test-generation stage** that produces tests for the change under review, run in CI on
  the target repository. Constraint from experience: it must read the repository (the
  snapshot seam already exists) — an agent asked to test a file it cannot see invents
  imports, which this project has already measured once.
- **Selenium/browser tests executed in the pipeline**, against a running instance of the
  target app. The target is a Flask login handler, so this is feasible; the fixture app may
  need a minimal template to have something to drive.
- **A verdict integration that is honest about authority.** A failing generated test must
  be *binding* (it is a fact, like a scanner finding), while a *missing* generated test is
  advisory. And a generated test that passes proves less than one that fails — the spec must
  not let a green generated test be quoted as proof of correctness.
- **Flake handling, decided up front.** Browser tests flake. A flaky test that blocks
  legitimate changes will be disabled by the first person it inconveniences, and then the
  gate is theatre. Required: a retry policy, a quarantine mechanism, and a rule that a
  quarantined test's absence is reported rather than silent.
- **Generated tests must not be self-approving.** The agent that wrote the change must not
  be the sole author of the test that clears it. Either a different agent generates the
  test, or the test is generated from the *ticket's acceptance criteria* rather than the
  diff. This is the same separation-of-authority principle as §8, one layer out.

---

## 10 · RAG / knowledge lake (req 8)

**The ask, interpreted.** Give the agents memory and context beyond one repository
snapshot.

**The honest framing:** this is the requirement most likely to become a demo of a vector
database rather than an improvement to the product. It earns its place only if it changes a
verdict or a diff, so the spec makes that the acceptance test.

**What must exist:**

- **A named retrieval purpose per corpus.** Candidates, in order of defensible value:
  (a) the target repository's own history — why a past change was rejected;
  (b) the project's conventions and prior review comments, so the reviewer stops
  re-litigating settled questions; (c) CVE and remediation context, so the security
  agent's prose is specific;
  (d) the organisation's own security policy, so thresholds can cite a rule.
- **A measured before/after on at least one corpus.** Revision count, false-block rate, or
  reviewer objection quality — one number, one corpus, moved.
- **Retrieval must not reach the gate.** Retrieved text is context for *prose and drafting*,
  never an input to the severity decision. Otherwise a poisoned document becomes a way to
  argue past the threshold, which is precisely the attack the deterministic gate exists to
  prevent. **This constraint is non-negotiable and belongs in the implementation plan as an
  assertion, not a comment.**
- **Provenance on retrieved context**, consistent with how scanner provenance already works:
  a run must be able to say what it retrieved and from where.

---

## 11 · The Next.js application (req 9)

**The ask, interpreted.** Sign up, sign in, link a GitHub account, watch agents and the
pipeline run in real time, see status and cost.

**What must exist, as surfaces:**

| Surface | Must show |
|---|---|
| **Sign up / sign in** | email + OAuth; sessions; password reset. Nothing exotic — this is table stakes and should consume as little time as possible |
| **Account linking** | connect a GitHub account/installation, choose which repositories are in scope, revoke |
| **Live run view** | the seven stages, current stage, per-stage output as it arrives, the three gates as *actionable* controls |
| **Approve / reject in the product** | this is what makes self-hosting possible (§6) and removes the GitHub Environments dependency |
| **Run history** | every past run, its verdict, its findings, its provenance |
| **Cost** | per run, per repository, per period — reading the instrumentation from §4 |
| **The scoring artifact** | §8's transparency table, rendered per finding |

**Design direction.** The pre-final deck's identity — near-black surfaces, one cyan accent
for structure, rose/mint for refused/shipped, mono for identifiers — is established, tested
in front of judges, and should carry into the product. It is also genuinely apt: this is an
instrument, and it should look like one. The `frontend-design` skill should be used for the
marketing surface and the empty/error states, where a default template would be most
visible.

**Two engineering constraints that will otherwise be discovered late:**

- **Real time means a real transport.** Polling a REST endpoint every two seconds will be
  the first thing a judge notices. Server-sent events or websockets, decided in the plan,
  with the queue from §12 as the event source.
- **The approval UI is a security surface.** `agentorg/approve_server.py` exists today with
  *no authentication*, bound to loopback, documented as never-expose. The Next.js approval
  path is the opposite: internet-reachable and authorising a merge. It needs authentication,
  authorisation per repository, an audit record of who approved what, and CSRF protection.
  The existing file should be retired rather than extended — its own docstring says why.

---

## 12 · SaaS restructure (req 10)

**The decision:** build the SaaS core first, port the pipeline onto it.

**What must exist:**

- **Tenancy.** An organisation owns repositories, runs, secrets and a budget. Every query
  is tenant-scoped. Cross-tenant leakage is the one defect that would end this as a
  product, so tenant isolation needs a test that *attempts* the leak rather than asserting
  its absence.
- **A control plane / data plane split**, which this project already understands from
  AgentCore: configuration and orchestration on one side, run execution on the other.
- **A job queue replacing Actions jobs.** This is the actual rewrite. What Actions provides
  today and must be replaced deliberately: sequencing, artifact handoff between stages,
  pausing for approval, and per-job isolation. The pause is the interesting one — the
  current design is shaped entirely by "a job cannot pause in its middle", and a queue with
  durable state removes that constraint. **The seven-job structure may collapse; the three
  gates must not.**
- **Per-tenant secrets.** A customer's GitHub token and model credentials, encrypted,
  never in a log, never in a Terraform state file — this project has already been burned by
  a token in a plan artifact and must not repeat it at multi-tenant scale.
- **Budgets and quotas**, per tenant, enforced before a run starts rather than discovered
  on an invoice. §4's instrumentation is the input.
- **Migration path.** The existing single-tenant deployment becomes tenant zero. Nothing is
  thrown away.

**What must NOT change:** `state.py`'s existing fields, the five agents' signatures,
`agentorg/security/`. They are the asset. The plan's phases must treat them as read-only
except additively.

---

## 13 · Limitations, deliberately kept (a first-class deliverable)

The pre-final's strongest moment was volunteering a limitation before being asked. Carry
seven forward and add the new ones honestly:

1. **Reported line numbers are indices into the added-lines-only view**, not the real file.
   Correcting the offset would collapse the fixture-vs-real discriminator, so the fix is
   *both* changes at once or neither.
2. **Gate Environments allow admin bypass** (`can_admins_bypass: true` on all three). An
   operator setting, reported by preflight on every run.
3. **gitleaks severity is a constant** — see §8. Now documented as policy rather than
   discovered by a judge.
4. **A vague ticket can legitimately fail** at the revision cap. A property of the ticket,
   not a bug.
5. **The reviewer's verdict is advisory.** If the scanners miss something, only an advisory
   opinion saw it.
6. **Self-hosted parity is partial** until §11's approval UI lands (§6).
7. **Generated tests prove less when they pass than when they fail** (§9).

**A limitation is only credible if it is costed.** Each one needs a sentence on what it
would take to remove and why that is not this phase's priority.

---

## 14 · What "done" means for this specification

This document is complete when every judge requirement in §0 maps to a section that states
what must exist and how it will be verified. It is *correct* when the implementation plan
derived from it can be executed by subagents without needing to re-decide anything in here.

**The next artifact** is `docs/final/02-implementation-plan.md`: phases, tasks sized for a
single subagent, explicit file ownership per lane (drawn by *file*, not by topic — the
lesson from the pre-demo four-lane work, where `run_stage.py` was wanted by three fixes at
once), a mandatory RED step per test change, and the daily green-demo rule from §1 as a
hard gate on every phase.

**Three things that plan must not do**, each learned here:

- **Do not draw lanes by topic.** Draw them by file ownership. Two lanes editing one file
  is a merge conflict discovered at integration.
- **Do not let a phase end with the demo broken.** §1 is the rule; the plan enforces it.
- **Do not write a test that reads a workflow's text and calls it verification.** This
  project has one recorded case of a test that *required* a bug — asserting a broken
  expression was present, so it passed on the defect and would have failed on the fix.
  Where behaviour depends on a platform's evaluation, the test must evaluate.
