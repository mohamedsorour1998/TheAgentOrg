# Final evaluation — 26 September 2026

**TD63 · RosettaTeam · Glass Room 1 · 3:50 PM · Creativa Giza**
Written 2026-09-22. Four days.

---

## The constraint that shapes everything

**20 minutes covers the presentation, the demo AND the judges' questions.** The
pre-final was 30 for the same three things. Nothing else in the brief changed as
much, and every decision below follows from it.

Measured, on real runs from 2026-09-22:

| | measured | source |
|---|---|---|
| poisoned run, dispatch → gate1 waiting | ~2 min | run `35679536930` |
| poisoned run, gate1 approved → blocked | **2 min 19 s** | `05:28:58` → `05:31:17` |
| clean run, end to end with three approvals | **~6 min** | run `35354213738` |

**Both runs live is ~10 minutes of a 20-minute slot.** That leaves ten for eleven
slides and the judges' questions, and the judges' questions are the part you cannot
compress. So:

```
slides        9:00      eleven slides, ~50 s each
live demo     5:00      the POISONED run only, start to block
questions     6:00      what is left, and it should be left
```

**The clean run is not performed live.** Its evidence is a merged pull request and a
`PROMOTED` row already in the list — both real, both openable, neither costing six
minutes. A 60–90 s recording covers it if a judge wants to watch it happen.

---

## Decisions taken

| Decision | Choice |
|---|---|
| Demo | **Live, with a recording as fallback on the same slide.** Poisoned live; clean shown as its merged PR plus a recording |
| Speaker | **Sorour presents alone**, takes questions in all areas. Five on the team slide, five in the room |
| The ten judge notes | **One dedicated slide** answering all ten, plus backup slides after the close |
| Deck tooling | **The technical-presentations toolkit** — a new talk under `talks/devops-hackathon-final/`, `deckkit` untouched |

---

## What the brief now requires, and where we stand

The email names **six** sections. The current deck's `_REQUIRED` enforces five, and
**two of the six are not among them** — so today the build cannot fail if they are
weak or dropped.

| Required section | Status |
|---|---|
| Overview of the idea — problem and solution | covered |
| **Business impact** | **NEW — not an enforced section today** |
| **Differentiation** | **NEW — not enforced; material exists in `docs/final/evidence/competitors.md`** |
| High-level architecture | covered, and the diagram is being rebuilt — see below |
| Progress to date | covered, **numbers stale** |
| High-level future work plan | covered |
| A live demonstration | covered |

**Stale figures in `scripts/make_deck.py`**, measured 2026-09-22:

```
TESTS_PASSING   deck says 2143   actual 2171 collected
TEST_FILES      deck says   90   actual   93
WEB_TEST_FILES  deck says   14   actual   24
```

---

## The ten pre-final notes — nine already have shipped answers

This is the strongest thing we have to say, and it needs one slide rather than ten.

| # | Note | Answer that exists today |
|---|---|---|
| 1 | Evaluation criteria | `agentorg/retrieval/measure.py`, `scripts/measure_prompts.py` — measured arms, not opinion |
| 2 | Time and cost vs Claude Code | `agentorg/cost/` — **$0.013–0.017 per change**, priced from the AWS Pricing API; median ticket→merge **5.39 min** |
| 3 | External dependency | `scripts/measure_dependencies.py` — **1 of 50** modules has a module-level vendor import |
| 4 | Self-hosted? | `infra/selfhost/docker-compose.yml` — postgres + api + web, verified end to end |
| 5 | Competitive advantage | `docs/final/evidence/competitors.md` — commissioned research; the seam distinction |
| 6 | **gitleaks/trivy scoring → go/no-go** | `agentorg/security/scoring.py` — ONE table, three scanners, threshold floor. **Built because of this note** |
| 7 | Test generation + Selenium in the pipeline | **PARTIAL — two layers, and they do not meet.** See below |
| 8 | RAG / knowledge lake | `agentorg/retrieval/` — three corpora, wired into all four agent prompts |
| 9 | **Full Next.js UI** — sign up/in, link account, live pipeline, status, cost | **Finished this week.** Cognito + GitHub App sign-in, live run view, cost screen |
| 10 | Restructure as SaaS | `agentorg/tenancy/` + `agentorg/api/`, DynamoDB single-table, `dynamodb:LeadingKeys` against an IAM session tag |

### Note 7 is the one to state carefully

**AI-generated tests and Selenium are two mechanisms that never meet, and the slide
must not imply otherwise.**

| | What happens | Where |
|---|---|---|
| AI generates tests | `testgen.run(state)` writes **pytest** from `plan.acceptance_criteria`, executes them, reports `passed`/`failed`/`binding` | the **pipeline** — `run_stage.py:705`, `graph.py:606` |
| Selenium runs | 4 **hand-written, committed** browser tests against the target app's login form | **CI** — the `browser` job, added 2026-09-22 |

Measured: `testgen.py` contains no mention of `selenium`, `browser` or `webdriver`.
The browser job runs `target_repo/tests/e2e`, which nothing generated.

**WIRING THE e2e SUITE INTO `develop` WOULD BE THEATRE.** The pipeline changes
`mohamedsorour1998/auth-service`; the Selenium tests drive a local Flask wrapper in
*this* repository. One pipeline run would then do both and the browser still would
not be exercising the agent's change — and "so the browser tested what the AI
wrote?" gets a no.

**Say it as two layers, in the Roadmap slide, before being asked.** The AI writes and
runs tests *about the change*; Selenium proves the app works *through a real
browser*. Both real, both automated, different levels of the pyramid. Closing the
gap properly means teaching `testgen` to emit a browser test for a UI acceptance
criterion and running it against the deployed app — real work, and a half-built
version is worse than the honest story.

**What DID change on 2026-09-22:** Selenium now runs in CI at all. It previously ran
once, by hand, on a laptop; every CI run reported `1 passed, 4 skipped`. The new
`browser` job sets `SELENIUM_REQUIRED=true`, so an absent browser FAILS rather than
skipping. Verified on ci run 35682698805:

```
Google Chrome 152.0.7977.82 · ChromeDriver 152.0.7977.82
5 passed in 194.63s
```

Its first run went red and found a real defect: all three submit tests called
`find_element` immediately after clicking, so the lookup could run against the page
still on screen. Two passed by winning a race. All three now wait.

**Note 6 deserves the strongest framing.** A judge doubted the determinism claim and
was right to: it was exactly true for trivy and semgrep and *vacuously* true for
gitleaks, which hardcoded `critical`. That is now one policy table with a derived
threshold floor — the note produced a real correction, and saying so is better than
claiming we were right all along.

---

## The architecture diagram — rebuilt, AWS icons, real names

**Reference: `~/sorour/AgentsforHumansHackathon/docs/architecture.png`** and the
method in `architecture-drawio-guide.md` beside it. That diagram is the standard to
match: dark canvas on the deck's own surface, official AWS icons, resource names in
mono, grouped regions with titles, labelled edges, and a detail inset.

**Two rules carried over from that guide, both load-bearing:**

- **There is no AgentCore icon in any AWS set.** Use the **Amazon Bedrock** icon and
  label the box `Amazon Bedrock AgentCore Runtime`. Do not invent one, do not use a
  robot.
- **The deterministic gate gets a HEXAGON, not a service icon.** `compute_security_verdict`
  is our own five lines of Python. A service icon would imply AWS enforces it; the
  entire claim is that *deterministic code* does. Same shape and colour for the three
  human gates, so a judge sees at a glance that they are the same kind of thing.

### What must appear, derived from Terraform and the code — not from memory

```
find infra/Terraform -name "*.tf" -exec grep -hoE '^resource "aws_[a-z0-9_]+"' {} +
grep -rhoE 'boto3\.(client|resource)\("[a-z0-9-]+"' agentorg scripts infra web
```

**AWS — 14 services**

| Service | What it is here |
|---|---|
| Lambda + Function URL | `theagentorg-shared-github-ingress` — HMAC verify, then PutEvents |
| EventBridge | bus, rule (`issues`/`opened`), connection, API destination, target |
| SQS | the dispatch DLQ — it earned its place once, and that story is worth 20 seconds |
| Secrets Manager | webhook secret, dispatch token, Cognito sign-up client secret, session key |
| Bedrock | Nova 2 Lite via a **cross-region inference profile** |
| Bedrock AgentCore | **5 runtimes**, one arm64 image, five ECR tags, at v52 |
| ECR | 5 agent repositories + worker, scanning on, lifecycle keeps 5 |
| DynamoDB | `theagentorg-runs` (audit) and `theagentorg-tenancy` (single-table) |
| Cognito | user pool, public browser client + confidential sign-up client, managed login v2 |
| Amplify | `WEB_COMPUTE` SSR, custom domain `theagentorg.rosettacloud.app` |
| IAM + STS | OIDC, 8 roles, `LeadingKeys` against a **session tag** |
| CloudWatch Logs | 14-day retention |
| ECS Fargate | the queue worker — **count-gated off**, and the diagram should say so |
| S3 | Terraform state backend |

**GitHub — the half that is not AWS**

GitHub App (installation-scoped repos) · Actions, 5 workflows · **3 Environments
with required reviewers — these ARE the gates** · OIDC → IAM, **zero static AWS
keys** · webhook → HMAC-SHA256.

**The stack**

Python 3.12 · pydantic · strands-agents · Next.js 16 / React 19 / TypeScript ·
Terraform · Docker arm64 · gitleaks · Trivy · Semgrep · pytest **2171** · vitest **311**.

**Deliverables:** `docs/architecture.drawio` (editable) and `docs/architecture.png`,
both committed. The PNG goes on the slide; the `.drawio` is what the next person edits.

---

## The slide list — eleven, ~9 minutes

| # | Slide | Section it satisfies | ~s |
|---|---|---|---|
| 1 | Title | — | 20 |
| 2 | The problem — an agent writes code; who checks it | Overview | 50 |
| 3 | The solution in one line + the nine-stage strip | Overview | 50 |
| 4 | **The gate is not a model** — the five lines, and why | Overview | 60 |
| 5 | **Architecture** — the new diagram | Architecture | 80 |
| 6 | **Business impact** | **Business impact** | 60 |
| 7 | **Differentiation** — the seam distinction | **Differentiation** | 60 |
| 8 | Progress — measured, current numbers | Progress | 60 |
| 9 | **Your ten notes, answered** | Progress | 70 |
| 10 | Roadmap beyond the hackathon | Future work | 45 |
| 11 | Demo handover | — | 15 |
| — | Close | — | 10 |

**After the close, shown only if asked:** per-note detail, the cost arithmetic, the
tenancy isolation proof, the self-hosted compose stack, the competitor table, the
limitations list.

### The two new slides, in substance

**Business impact.** Not "AI is important". The measurable claim: a change costs
**$0.013–0.017** and reaches a merge in a **5.39 min median**, against a human review
cycle measured in hours; the pipeline refuses a committed credential **deterministically**,
and a credential reaching `main` is the failure whose cost is not measured in minutes.
State the survivorship honestly — 8 merges of 37 runs.

**Differentiation.** From `competitors.md`, and the honest version is stronger than
the flattering one: every major vendor's LLM review is **advisory, in their own docs**.
Three shipped products carry our signature defect — Cursor hooks fail open, Claude
Code's exit 1 does not block, Semgrep returns 0 on an internal crash. Our distinction
is a **seam**: every gate they ship guards a tool call *inside one agent's session*;
ours guards a pipeline stage *between agents*, and three human gates sit on it.

---

## The three slides that carry the argument

Asked for explicitly: why we beat a competitor like Claude Code, how a deterministic
gate sits on top of non-deterministic models, and what the scoring algorithm is.
These are the intellectual core — slides 4, 7 and one backup — and each is written
below in the form it must survive a follow-up question in.

### Slide 4 · A deterministic gate on top of non-deterministic models

**The apparent contradiction IS the design, and naming it first is what makes it
land.** Five agents are language models: they are non-deterministic, they can be
persuaded, distracted and prompt-injected. The thing that stops a change is not one
of them.

| | reviewer | security |
|---|---|---|
| what it is | a model reading the diff | three scanners + five lines of Python |
| catches | intent, logic, plan mismatch, taste | credentials, known CVEs, injectable patterns |
| authority | **advisory** — the graph loops, it does not stop | **binding** — `compute_security_verdict` |
| can be wrong | yes, both directions | deterministic: same input, same answer |
| can be talked out of it | yes, it is a prompt | **no — no model is involved** |

**The block is a DEPENDENCY EDGE, not a status check.** `develop` exits 3 and
`gate2` declares `needs: develop`, so no `if:` expresses the block — the graph does.
Nothing to misconfigure, and no status check to mark non-required by accident.

**The line to say out loud:** *the demo would still block with the reviewer removed
entirely; it would not block with the scanners removed.* The reviewer catching the
key first is a bonus, not the mechanism.

**Prepared answer — "what if the scanners miss something?"** Then the reviewer is
the only thing that saw it, its verdict is advisory, and the change can reach `main`
past three human gates. That is an accepted limit, not a defended one, and the gates
are why it is acceptable.

### Slide 7 · Differentiation — the seam, not "we are the only deterministic one"

**Do not claim we are the only deterministic gate. It is false and a judge will find
the counter-example in a minute.** `docs/final/evidence/competitors.md` §6 records
five of my own claims being disproved by the research I commissioned, four of them
wrong in the flattering direction. The narrower claim is the one that survives.

**Open with their own words** — every major vendor's LLM review is advisory, and each
says so in its own documentation:

| Product | Their words |
|---|---|
| Copilot code review | *"will not block merging changes"* |
| **Anthropic managed Code Review** | *"the check run **always** completes with a neutral conclusion so it never blocks merging"* |
| OpenAI Codex | review rules *"don't replace tests, branch protections, or required approvals"* |
| Cursor Bugbot | findings *"default to `neutral`"* — requiring the status does not block |

And **Snyk's own platform page argues this thesis verbatim: "The generator cannot be
the validator."**

**Then concede what is real**, because the category is not empty: Claude Code's
permission deny rules are *"enforced by Claude Code, not by the model"* and hold even
under `bypassPermissions`. Factory's Droid Shield hard-blocks `git commit`. OpenHands
ships deterministic analyzers. Jules has two clean human gates.

**Then the distinction that survives:**

> Every gate they ship guards a **tool call inside one agent's session**. Ours guards
> a **pipeline stage between agents**, with a named human reviewer, and the block is
> a dependency edge rather than a status check.
>
> No product ships multi-agent generation, a deterministic non-LLM block on stage
> output, and human approval gates between stages **as one pipeline**.

**The closing beat, and it is the strongest thing on the slide:** three shipped
products carry our own signature defect. Cursor hooks are *"fail-open by default"* on
any exit code but 2. **Claude Code's exit 1 does not block, and "a mistyped path
silently disables the gate."** Semgrep returns **exit 0 on an internal crash**. That
is a check that did not run reading as a check that passed — in products people pay
for — and it is the direct argument for `SCANNERS_REQUIRED` and for
`unrunnable_findings` raising rather than returning `[]`.

### Backup slide · The scoring algorithm, exactly

Shown when a judge asks note 6 — and the note that produced it was right.

**The rule is five lines and there is no model in it:**

```
SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}

cutoff   = SEVERITY_ORDER[threshold]
blocking = [f for f in findings if SEVERITY_ORDER[f.severity] >= cutoff]
verdict  = "block" if blocking else "pass"
```

**One table, three scanners, native → ours** (`agentorg/security/scoring.py`):

| Scanner | How its severity is decided |
|---|---|
| **semgrep** | **MAPPED.** Emits both `INFO/WARNING/ERROR` and `LOW/MEDIUM/HIGH/CRITICAL` — seven keys, four severities. `ERROR` is its top level and means `high` |
| **trivy** | **MAPPED.** Its `UNKNOWN` is a real answer about a real CVE whose sources carry no severity, so it is a mapped key and not a fall-through |
| **gitleaks** | **ASSIGNED BY POLICY — `critical`.** It reports no severity field at all, so there is nothing to map. Any finding from a secret scanner is critical by rule: a committed credential has no lesser grade |

`ScannerScoring.__post_init__` refuses anything that sets **both** a table and a
constant — two answers would exist and nothing would record which the verdict used.

**Three properties worth stating, because they are what make it a gate:**

- **`FAIL_CLOSED_SEVERITY = high`** — an unrecognised severity lands at the block
  threshold. Refused **at import** if it ever drops below it.
- **`THRESHOLD_FLOOR` is DERIVED, not written** — computed from the policy that
  carries `protects_core_guarantee`, so it equals `critical` today. A literal would
  be a second declaration of gitleaks' severity, and two copies agree until one moves.
- **`resolve_threshold` REFUSES, it never CLAMPS.** Clamping would run the gate at a
  threshold the operator did not ask for and report success.

**The honest consequence, stated rather than papered over:** the threshold does not
*discriminate* among gitleaks findings — they all sit at the top of the scale, so the
arithmetic is `critical >= threshold`, true at every threshold this project accepts.
It still runs; it has one input to compare.

**And the answer to "how do I know the scanners really ran?"** — the line numbers.
Real scanners report `app/auth.py:3` and `:4`; the fixture reports `:4` and `:5`.
That pair is the only field that separates the two, which is why `preflight.py`
check 3 asserts it and why the block slide shows it.

---

## Work plan — four days

**Day 1 (22nd) — the diagram and the numbers**
1. Rebuild the architecture diagram in draw.io against the service list above. Export
   `docs/architecture.png`, commit the `.drawio`.
2. Re-measure every figure. One constants block, each annotated with its command.

**Day 2 (23rd) — the deck**
3. `cp -r talks/rosettacloud-genai-hackathon talks/devops-hackathon-final` in the
   technical-presentations repo. Leave `deckkit` alone.
4. Palette via `deck.palette()` — TheAgentOrg's surface `#0b0f17`, accent `#22d3ee`.
   **Never by reassigning imported names**; the engine refuses it and the guide says why.
5. Write the eleven slides. `_REQUIRED` = the **six** sections the email names.
6. Backup slides after the close.

**Day 3 (24th) — the demo and the recordings**
7. Record the clean run and the poisoned run from the browser. **This is a documented
   deviation:** `deckkit/record.py` renders terminal output and cannot record a web UI,
   so these are screen captures. Say "this is a recording" on the slide and aloud.
8. Embed with `video()`; **poster frame = the LAST frame**, so a clip that never plays
   still reads as a result.
9. Rehearse against a clock. Verify the sum programmatically, not by eye.

**Day 4 (25th) — freeze**
10. `verify()` green: motion, element order, wrapped-height collisions, shape bounds,
    banned language, capitalisation, the six required sections.
11. **Break one check and confirm it fails**, then restore. A check that never fires is
    indistinguishable from one that cannot.
12. Full gate sweep + `preflight.py`. Confirm check 3 still reads `LINES: [3, 4]`.
13. Dry run on the actual laptop, on the actual HDMI adapter.

---

## Demo runbook — the live five minutes

1. **Before the session**, with the app already open and signed in: confirm the runs
   list loads and one `PROMOTED` run with a merged PR is visible. That is the clean
   half, already done.
2. Open **Start a run**, choose the repository, tick **Demonstrate a blocked run**, Start.
3. The form collapses; the pending row appears with the issue number. ~1 min to `plan`.
4. **gate1 — approve.** Say what a gate is while it moves.
5. `develop` runs ~2 min 19 s. This is the window to explain that the reviewer is a
   model and advisory, and the scanners are not.
6. **The block.** `BLOCKED`, `blocking: 2`, `provenance: scanners`,
   `app/auth.py:3` and `:4`. Everything after it did not run.
7. **The line numbers are the proof.** Real scanners report `{3,4}`; the fixture reports
   `{4,5}`. It is the only field that separates them.

**Contingency**

| If | Then |
|---|---|
| the run stalls | play the recording on the same slide — it is already there |
| the venue network fails | the recordings are in the deck; nothing is fetched |
| a judge asks for the clean run | open the merged PR, then play the 90 s recording |
| a judge asks "can a gate be skipped?" | yes, by a repository admin. `preflight.py` check 4 prints it. Say it before being asked |

---

## Logistics, from the email

- **Arrive 1:30 PM.** Slot is 3:50 PM, Glass Room 1. One hour before is the minimum.
- **All five register individually** for DevOps Day or they do not get in the venue.
- **HDMI** — bring the adapter and test it.
- **Nothing installed or downloaded on the day.** Deck, recordings and the app all
  resident on the laptop.
- **Smart casual / semi-formal.**
- **English preferred**, and not scored. Egyptian judges will help.

---

## Open questions

1. ~~Is Selenium run inside the pipeline?~~ **ANSWERED 2026-09-22.** It was not — no
   workflow mentioned it. It now runs in CI and fails when no browser is present. The
   remaining gap is that Selenium does not verify the AI-GENERATED tests; see note 7
   above for why joining them would be theatre.
2. **Stale run rows.** #60 has no Actions run and #61 has a duplicate. Decide whether to
   leave them (honest) or start a clean tenant for the demo (tidier, and the isolation
   story then has nothing to show).
3. ~~Team slide photographs~~ **ANSWERED.** Reuse the pre-final deck's:
   `docs/pitch/photos/square/` holds five at **640x640** — aya, habiba, mariam, reem,
   sorour. Copy into the new talk directory; no re-cropping needed.
