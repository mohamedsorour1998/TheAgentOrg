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
