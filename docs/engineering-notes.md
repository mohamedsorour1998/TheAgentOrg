# Engineering notes

Project history and measured findings. The [README](../README.md) says what the
system does; this file records what we learned building it, and why several
non-obvious decisions are the way they are.

Kept out of the README deliberately: a reader evaluating the project needs the
architecture and the usage, not the changelog. Kept at all because most of these
were expensive to discover and cheap to forget.

---

## The defect class this project is organised around

> **A check that cannot distinguish "did not run" from "passed" is worse than no
> check at all.**

Everything below is a variation on it. The pattern recurred often enough to be
worth stating as a rule:

> A test double, a helper, an inference, or a measurement that cannot express the
> failing case produces confidence that cannot be falsified — and reading it never
> reveals that.

---

## Every agent was silently serving fixtures

For about a week the deployed pipeline produced agent-shaped output that no model
had written, with every job green. Three independent IAM defects on one policy
statement, each hiding the next:

| # | Defect | Found by |
|---|---|---|
| 1 | `Resource` granted `foundation-model/*`, but `BEDROCK_MODEL` names an `inference-profile/` ARN | `simulate-principal-policy` |
| 2 | `Action` granted `InvokeModel`, but `strands.Agent` calls **`ConverseStream`** | the container log naming the operation |
| 3 | `Resource` scoped to one region, but a cross-region profile routes to three | `get-inference-profile` |

Fixing each one exposed the next, because until then every call failed at the
earlier check. A fix that turns one silent failure into a different silent failure
looks like no progress at all unless something reads the log.

**Why it went unnoticed:** `llm.text()` catches a denial by design — a demo that
dies on a transient Bedrock error is worse than one that completes on a fixture —
so the run finished, the job went green, and the deployed plan comment matched
`fixtures/plan_result.json` byte for byte.

**Why the deploy's own smoke test did not catch it:** it asserted
`grep -q '"tasks"'` on the planner's answer, and the fixture *begins* with
`"tasks"`. The check could not fail. It now keys on the fixture's `notes` literal,
whose **absence** is the discriminator.

**Two lessons.** Simulate the action the SDK actually calls, not the one you assume.
And the proof a model answered is not a green job — it is that the output stopped
matching the fixture.

## The provenance could not cross the seam it was built to describe

`RunState.model_provenance` exists to record whether a run's output came from the
model or a fixture. The first run after the IAM fixes printed `_source=none` beside
a plan comment that was unmistakably model-written.

Under `REMOTE_AGENTS=true` the model call happens **inside the container**, and
`llm.last_source()` on the runner never sees it. Same shape as `RunState.poisoned`:
a fact only the container holds has to travel. `source` now rides on the HTTP
envelope, and three cases fail toward *unknown* rather than *model* — an older
container omitting the key, an unrecognised value, and a warm container reused
across invocations.

## `promote` reported success while merging nothing

A run finished with all seven jobs green and `status=promoted`, and no pull request
was merged. `promote` deliberately held no GitHub credentials — correct when it only
wrote a status row, wrong the moment it started calling `merge_pr`, whose offline
path returns `local://<branch>`: a ref that reads like success.

The test that should have caught it **exempted `promote` by name**, with the
now-stale reason in a comment. An exemption entry outlives its reason silently. The
list is gone; if a job genuinely needs no seam, that must be proved by an assertion
about what the job does.

## The scanner layer had four fail-open paths

All four were silent — green suite, green gate, wrong verdict.

- **semgrep's severity table mapped `HIGH` and `CRITICAL` to `low`** against a block
  cutoff of `high`. A rule semgrep marked CRITICAL could not block. The
  unrecognised-value default now fails **closed**: a severity name the table does
  not know is not evidence of safety.
- **trivy's `data.get("Results") or []`** collapsed falsy wrong types to a valid
  empty list *before* the shape guard, so a malformed report produced `pass`. Its
  sibling wrapper spelled the same guard `.get(k, [])` and tripped correctly — two
  spellings of one guard, one failing open.
- **One absent scanner aborted the whole fan-out.** semgrep runs first, so its raise
  discarded gitleaks' and trivy's findings *and* their blocking faults. This was
  CI's normal path, not an edge case.
- **The security agent's poison check was a whole-diff substring scan**, the form
  `common/diff.py` exists to replace. It read a key on a *removed* line as poison —
  the shape of every revision after a reviewer asks for credentials to be taken out.

## A `--no-prefix` diff scanned nothing and reported `pass`

The diff parser recognised only git's default `+++ b/` prefix. Any other legal
spelling materialised **zero files**, so the scanners ran over an empty tree,
returned `[]`, and `compute_security_verdict([])` returned `("pass", [])` — while
`scan_provenance` truthfully recorded `scanners`. They had run; they had nothing to
read.

The diff is model-written, so a non-default prefix was never hypothetical. A
non-empty diff that now parses to no recognised header **raises** rather than
returning an empty dict.

## A live token was downloadable from a build artifact

`terraform.yml` uploaded the binary `tfplan`. A binary plan embeds a full copy of
Terraform state, and this state holds the dispatch token's value — the ingress
module must read that secret at plan time, because an API_KEY connection takes the
value through configuration.

The outer file greps clean because `tfplan` is *itself* a zip; the token sits in the
`tfstate` entry inside it. Ten unexpired artifacts carried it.

This is a **different** exposure from the S3-state one the module's comments accept:
that needs AWS credentials, this needed only a GitHub account against a public
repository. Only `plan.txt` is uploaded now — Terraform prints
`value = (sensitive value)` there — and the apply re-plans, so nothing ever
consumed the binary.

## A `failed` run claimed to have been blocked

Two rendering defects, both visible on a projector:

- A run whose revision cap expired rendered `⛔ BLOCKED — the change was stopped`
  while its security verdict was `pass` with zero blocking findings. It asserted the
  deterministic rule had stopped a change the scanners had cleared — the pipeline's
  central claim, inverted.
- An SRE `no_go` wrote no ending log row at all, so a finished run rendered
  `… INCOMPLETE`. `timeline._outcome` reads the last row's action and never sees
  `RunState.status`. No test covered that path.

`failed` is now a member of the action vocabulary with its own banner. A run nobody
approved and a run the rule stopped are different endings.

## A rejection recorder erased the block it existed to preserve

A rejected GitHub Environment makes GitHub **skip** its job rather than running it
with a verdict — so a refusal has to be recorded by a different job. But a gate the
run never *reached* is also skipped, and `needs.<gate>.result` reads identically for
both.

A poisoned run blocked at `develop` recorded `status=blocked` correctly, and then the
gate2 recorder fired on gate2's `skipped`, overwrote it with `status=rejected`, and
attributed it to a human who never saw the gate. Each recorder now also requires the
*preceding stage* to have succeeded: if it did, the only remaining reason the gate
did not run is the human.

## Numbers, and why they are quoted as ranges

A suite wall time committed as "measured" could not be reproduced: 102.83 s,
116.88 s and 149.68 s for the same snapshot on one machine in one day, load being
the variable. A measurement is a number *plus its conditions and spread*, or it is
not quoted.

Two counts in this repository are ratios rather than integers for the same reason —
`runs/` is gitignored scratch that every test run grows, so absolute file counts
drift while the proportion holds.

## Deliberately not fixed

- **Reported line numbers are indices into the added-lines-only file**, not the real
  file: a finding at `app/auth.py:3` means "the third added line". Correcting the
  materialiser would shift the pinned `{3, 4}` onto `{4, 5}` — the **fixture's**
  pair — collapsing the only field that distinguishes a real scan from a fixture
  verdict. The offset and the fixture must move together, in one change.
- **`ecr-push-policy` is scoped to the wrong repository prefix** and is inert,
  because a managed policy covers the push. Tightening it means changing both at
  once or the deploy breaks.
- **The CI role can delete other projects' ECR repositories and runtimes**, via two
  AWS managed policies. Real, worth knowing, not on the demo path.
- **The test suite writes into the real `runs/`.** A conftest redirect is the fix,
  but it touches the guard layer every test depends on.

## Conventions worth knowing before changing code

- **Broad `except Exception` clauses here are load-bearing.** Ruff's `BLE001` is
  satisfied by an inline logging call carrying the traceback — *and also* by
  narrowing the except with no logging at all. So lint blesses the more dangerous
  option. Fetch loggers inline; never bind a module-level alias.
- **Read config through the module** (`config.SCANNERS_REQUIRED`), never
  `from ..common.config import SCANNERS_REQUIRED` — that binds the value at import,
  before any fixture runs.
- **`pytest.fail` in the test guards is not stylistic.** Its exception derives from
  `BaseException`, and the code under test catches `Exception`; an ordinary raiser
  would be swallowed into the fixture branch and the test would pass green while
  making live billable calls.
- **`agentorg/state.py` is frozen**: add optional fields, never rename or remove one.
