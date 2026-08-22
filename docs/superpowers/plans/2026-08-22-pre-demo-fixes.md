# Pre-Demo Fix Plan — real agents, real CI, real merge

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every agent in the deployed pipeline is genuinely model-backed, the SRE
agent reads real CI, `promote` merges the pull request, and the four documented
limitations are closed — with the demo path never left broken between tasks.

**Architecture:** Nine tasks in dependency order. Task 1 fixes the IAM grant that
is currently making all four model-calling agents fall back to fixtures — it is the
highest-value change in this plan and it touches one Terraform statement. Tasks 2–3
make the fixture fallback *visible* rather than silent, so a regression cannot hide
again. Tasks 4–6 make the SRE agent real and give `promote` a merge. Tasks 7–9 close
the remaining documented limitations. Every task is independently revertible and
leaves `main` demo-ready.

**Tech Stack:** Python 3.12, pydantic v2, Terraform 1.15.8, AWS (Bedrock AgentCore,
Bedrock Nova, Lambda, EventBridge, DynamoDB, ECR), GitHub Actions, PyGithub.

**Spec:** this document. The four limitations it closes are recorded in
`README.md` → *Status and limitations* and in `CLAUDE.md`.

## Global Constraints

- **Demo is Tue Aug 25 2026.** Today is Aug 22. Anything that looks like a crash on
  a projector outranks polish. `main` must be demo-ready after every task.
- `agentorg/state.py` is **FROZEN**: ADD optional fields only, never rename or
  remove one.
- Python is `.venv-main/bin/python`. Do **not** create a venv; do not use
  `.venv-habiba` / `.venv-sorour` / `.venv-testing`.
- Baseline is **816 passed, 3 skipped** (`--collect-only` reports 819). It must stay
  green, plus new tests.
- `.venv-main/bin/python -m ruff check agentorg scripts tests` must exit 0. **No
  `[tool.ruff]` section, no `# noqa`, no per-file ignores.** `I001`, `BLE001` and
  `ISC004` are ruff 0.16 defaults and fire without being selected.
- `actionlint .github/workflows/*.yml` must exit 0. `terraform fmt -check
  -recursive` must exit 0.
- **Every test change carries a mandatory RED step:** name the exact mutation, apply
  it, watch the exact named test fail, **paste the failure**, revert. A task whose
  RED step was not run is **not done**. Never end a turn with a mutation applied.
- **Numbers in prose come from a command whose output you paste**, never from recall.
- Zero static AWS keys. Every AWS step assumes
  `arn:aws:iam::339712964409:role/github-actions-role` via OIDC.
- Never read, print, log or commit `.env`. FAKE credential literals only in tests;
  `AKIAIOSFODNN7EXAMPLE` is AWS's published example and is safe.
- Account `339712964409`, region `us-east-1`. Repo
  `mohamedsorour1998/TheAgentOrg`; target `mohamedsorour1998/auth-service`.
- Do NOT `ls` or tab-complete inside `runs/` (~10k files).
- **Broad `except Exception` clauses in this repo are load-bearing.** BLE001 is
  satisfied by an inline `logging` call carrying the traceback; narrowing the
  `except` also satisfies it *with no logging at all*, so lint blesses the more
  dangerous option. Fetch loggers inline; never bind a module-level `_log`.

---

## The finding that reorders everything

**Every model-calling agent in the deployed pipeline is falling back to its
fixture, silently.** Proved three ways:

1. The auto-triggered run's plan comment on `auth-service` issue #15 matches
   `fixtures/plan_result.json` **byte for byte** — same three tasks, same order.
2. The containers say so. `/aws/bedrock-agentcore/runtimes/theagentorg_{planner,
   developer,reviewer,security}-*-DEFAULT` each logged
   `WARNING model call failed; the caller will fall back to its fixture`.
3. IAM simulation names the cause:

```
$ aws iam simulate-principal-policy \
    --policy-source-arn arn:aws:iam::339712964409:role/theagentorg-shared-agentcore-runtime-role \
    --action-names bedrock:InvokeModel \
    --resource-arns "arn:aws:bedrock:us-east-1:339712964409:inference-profile/us.amazon.nova-2-lite-v1:0"
implicitDeny

$ ... --resource-arns "arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-lite-v1:0"
allowed
```

`config.BEDROCK_MODEL` defaults to `us.amazon.nova-2-lite-v1:0`. The `us.` prefix
makes it a **cross-region inference profile**, whose ARN is
`…:inference-profile/…`, not `…::foundation-model/…`. The runtime role grants only
`foundation-model/*`. So `InvokeModel` is denied, `llm.text()` catches it,
`structured()` returns `None`, and every agent serves its fixture.

The model itself is fine — with root credentials it answers:

```
$ .venv-main/bin/python -c "from agentorg.common import llm; print(repr(llm.text('Reply with the single word OK.', 'Say OK.')))"
'OK.'
```

So this is one missing IAM statement, and it is Task 1.

**Why it was invisible:** the fallback is deliberate and correct behaviour — a demo
that dies on a transient Bedrock error is worse than one that completes. But
nothing surfaced *which* path answered. `SecurityResult` has `scan_provenance` for
exactly this reason; no other result type has an equivalent. Task 2 fixes that.

---

## File structure

| File | Responsibility | Task |
|---|---|---|
| `infra/Terraform/modules/agentcore/main.tf` | add the inference-profile grant | 1 |
| `tests/test_agentcore_iam.py` *(new)* | pin the grant as data | 1 |
| `agentorg/state.py` | add `RunState.model_provenance` (optional) | 2 |
| `agentorg/common/llm.py` | record which path answered | 2 |
| `agentorg/agents/{planner,developer,reviewer}.py` | stamp provenance | 2 |
| `agentorg/graph.py`, `scripts/run_stage.py` | render provenance in comments | 2 |
| `tests/test_model_provenance.py` *(new)* | the discriminator's tests | 2 |
| `scripts/preflight.py` *(new)* | one command that proves the deployed path is real | 3 |
| `agentorg/github_ops.py` | `ci_status()`, `merge_pr()` | 4, 6 |
| `agentorg/agents/sre.py` | real CI + model-authored SLO checks | 5 |
| `auth-service/.github/workflows/ci.yml` *(new, target repo)* | give the target real CI | 4 |
| `scripts/run_stage.py` | merge on promote; `gates.load`; failed-run log rows | 6, 7, 8 |
| `.github/workflows/run-pipeline.yml` | `trigger` input | 9 |
| `infra/Terraform/modules/ingress/main.tf` | send `trigger=issue` | 9 |

---

## Task 1: Grant the runtime role the inference profile it actually calls

**Files:**
- Modify: `infra/Terraform/modules/agentcore/main.tf` (the `BedrockInvoke` statement)
- Create: `tests/test_agentcore_iam.py`

**Interfaces:**
- Consumes: nothing.
- Produces: no Python interface. Task 3's preflight asserts the effect.

**Why first:** until this lands, every other agent change is unobservable — the
model is denied, so a new prompt or a new agent behaves identically to the old one.

- [ ] **Step 1: Confirm the denial yourself before changing anything**

```bash
aws iam simulate-principal-policy \
  --policy-source-arn arn:aws:iam::339712964409:role/theagentorg-shared-agentcore-runtime-role \
  --action-names bedrock:InvokeModel \
  --resource-arns "arn:aws:bedrock:us-east-1:339712964409:inference-profile/us.amazon.nova-2-lite-v1:0" \
  --query 'EvaluationResults[0].EvalDecision' --output text
```

Expected: `implicitDeny`. Paste the output into the task record. If it says
`allowed`, someone has already fixed this — stop and re-read the statement.

- [ ] **Step 2: Write the failing test**

Create `tests/test_agentcore_iam.py`. It parses the HCL as text — there is no
`terraform` call, so it runs in CI with no credentials, the same reason
`tests/test_ingress_terraform.py` is written that way.

```python
"""The runtime role must be able to invoke the model the code actually asks for.

MEASURED 2026-08-22: it could not. `config.BEDROCK_MODEL` defaults to
`us.amazon.nova-2-lite-v1:0`, whose `us.` prefix makes it a cross-region INFERENCE
PROFILE -- ARN `…:inference-profile/…`, not `…::foundation-model/…`. The role
granted only foundation-model, so `bedrock:InvokeModel` was `implicitDeny`,
`llm.text()` caught it, and all four model-calling agents served fixtures while
every job reported green. The deployed plan comment matched
fixtures/plan_result.json byte for byte.

This test pins the grant so that cannot recur silently.
"""

import re
from pathlib import Path

from agentorg.common import config

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENTCORE_TF = REPO_ROOT / "infra" / "Terraform" / "modules" / "agentcore" / "main.tf"


def _code() -> str:
    assert AGENTCORE_TF.is_file(), f"{AGENTCORE_TF} is missing; this test pins nothing"
    text = AGENTCORE_TF.read_text()
    # Strip `#` comments so prose about wildcards cannot satisfy an assertion.
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def test_the_default_model_is_a_cross_region_inference_profile():
    """The premise. If this stops holding, the grant below may be over-broad.

    Asserted rather than assumed: a future default of a bare foundation-model id
    would make the inference-profile grant unnecessary, and an unnecessary IAM
    grant should be removed rather than left as decoration.
    """
    assert config.BEDROCK_MODEL.startswith(("us.", "eu.", "apac.", "global.")), (
        f"BEDROCK_MODEL={config.BEDROCK_MODEL!r} no longer looks like an inference "
        f"profile id. If it is now a bare foundation-model id, the "
        f"inference-profile statement in the agentcore module is dead weight and "
        f"should be removed rather than left granting more than the code uses."
    )


def test_the_runtime_role_may_invoke_an_inference_profile():
    """Without this the model call is implicitly denied and every agent serves a fixture."""
    code = _code()
    assert "inference-profile" in code, (
        "the agentcore runtime role does not grant bedrock:InvokeModel on any "
        "inference-profile ARN. config.BEDROCK_MODEL is an inference profile "
        f"({config.BEDROCK_MODEL!r}), so InvokeModel is implicitDeny, llm.text() "
        "catches the denial, and all four model-calling agents fall back to their "
        "fixtures -- silently, with every job green. MEASURED 2026-08-22."
    )


def test_the_inference_profile_grant_is_scoped_to_this_account_and_region():
    """A wildcard here would grant every profile in every region."""
    code = _code()
    profile_arns = re.findall(r'"(arn:aws:bedrock:[^"]*inference-profile/[^"]*)"', code)
    assert profile_arns, (
        "no inference-profile ARN literal found, so this test cannot check its "
        "scope -- either the grant is missing or it was written in a form this "
        "matcher cannot see"
    )
    for arn in profile_arns:
        assert "${data.aws_region.current.region}" in arn or "us-east-1" in arn, (
            f"inference-profile ARN {arn!r} is not scoped to a region"
        )
        assert "${var.account_id}" in arn or "339712964409" in arn, (
            f"inference-profile ARN {arn!r} is not scoped to this account; an "
            f"empty account field would grant profiles this account does not own"
        )


def test_the_foundation_model_grant_is_still_present():
    """Both are needed: a profile fans out TO foundation models.

    Invoking a cross-region profile requires InvokeModel on the profile AND on the
    underlying foundation models it routes to. Removing either one restores the
    silent-fixture failure, so this test exists to stop a future reader "tidying"
    the pair down to one.
    """
    code = _code()
    assert "foundation-model" in code, (
        "the foundation-model grant is gone. An inference profile routes TO "
        "foundation models, so both grants are required; with only the profile "
        "ARN the call is still denied."
    )
```

- [ ] **Step 3: Run it, watch it fail**

```bash
.venv-main/bin/python -m pytest -q tests/test_agentcore_iam.py -v
```

Expected: `test_the_runtime_role_may_invoke_an_inference_profile` FAILS with the
message about implicitDeny; `test_the_inference_profile_grant_is_scoped_…` FAILS on
the "no inference-profile ARN literal found" assertion. The other two PASS. Paste
the output.

- [ ] **Step 4: Implement**

In `infra/Terraform/modules/agentcore/main.tf`, replace the `BedrockInvoke`
statement's `Resource` list. The statement currently reads:

```hcl
{
  Sid      = "BedrockInvoke"
  Effect   = "Allow"
  Action   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
  Resource = ["arn:aws:bedrock:${data.aws_region.current.region}::foundation-model/*"]
},
```

Change it to:

```hcl
{
  # BOTH ARN SHAPES ARE REQUIRED, AND THAT IS NOT BELT-AND-BRACES.
  #
  # config.BEDROCK_MODEL defaults to `us.amazon.nova-2-lite-v1:0`. The `us.`
  # prefix makes it a CROSS-REGION INFERENCE PROFILE, not a foundation model, and
  # the two live at different ARN shapes:
  #
  #   arn:aws:bedrock:us-east-1:339712964409:inference-profile/us.amazon.nova-2-lite-v1:0
  #   arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-2-lite-v1:0
  #
  # Invoking the profile needs InvokeModel on the PROFILE (the thing called) and
  # on the FOUNDATION MODELS it routes to (the things that answer). Grant only one
  # and the call is denied.
  #
  # MEASURED 2026-08-22, with only the foundation-model ARN present:
  #
  #   simulate-principal-policy … inference-profile/us.amazon.nova-2-lite-v1:0
  #   implicitDeny
  #
  # The consequence was the worst available shape. `llm.text()` catches the
  # denial, `structured()` returns None, and all four model-calling agents fall
  # back to their fixtures -- so the deployed pipeline produced fixture output
  # while every job reported green, and the plan comment on the target repo
  # matched fixtures/plan_result.json byte for byte. Nothing anywhere said the
  # model had not answered.
  #
  # Note the profile ARN carries an ACCOUNT and the foundation-model ARN does not.
  # That asymmetry is AWS's, not a typo: inference profiles are account-scoped
  # resources, foundation models are not.
  Sid    = "BedrockInvoke"
  Effect = "Allow"
  Action = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
  Resource = [
    "arn:aws:bedrock:${data.aws_region.current.region}::foundation-model/*",
    "arn:aws:bedrock:${data.aws_region.current.region}:${var.account_id}:inference-profile/*",
  ]
},
```

- [ ] **Step 5: Verify the tests pass and formatting holds**

```bash
.venv-main/bin/python -m pytest -q tests/test_agentcore_iam.py
cd infra/Terraform && terraform fmt -check -recursive; echo "fmt exit=$?"
```

Expected: `4 passed`, `fmt exit=0`.

- [ ] **Step 6: RED step — prove each assertion pins something**

Three mutations, one at a time, reverting after each. Paste every failure.

1. Delete the `inference-profile/*` line from the `Resource` list.
   Expected: `test_the_runtime_role_may_invoke_an_inference_profile` fails, and
   `test_the_inference_profile_grant_is_scoped_to_this_account_and_region` fails on
   "no inference-profile ARN literal found".
2. Restore it, but write it unscoped as `"arn:aws:bedrock:*:*:inference-profile/*"`.
   Expected: the scope test fails naming the ARN.
3. Restore it, and delete the `foundation-model/*` line.
   Expected: `test_the_foundation_model_grant_is_still_present` fails.

Confirm `git diff` is clean of mutations as your last step.

- [ ] **Step 7: Commit and apply**

```bash
git add infra/Terraform/modules/agentcore/main.tf tests/test_agentcore_iam.py
git commit -m "fix(iam): the runtime role could not invoke the model the code asks for

MEASURED: bedrock:InvokeModel on inference-profile/us.amazon.nova-2-lite-v1:0 was
implicitDeny for theagentorg-shared-agentcore-runtime-role, while the policy
granted foundation-model/* only. config.BEDROCK_MODEL is an inference profile, so
every model call was denied, llm.text() caught it, and all four model-calling
agents served fixtures -- with every job green and the deployed plan comment
matching fixtures/plan_result.json byte for byte.

Both ARN shapes are now granted: the profile is the thing called, the foundation
models are the things that answer, and either grant alone is still a denial."
git push
```

Then apply and re-verify against the live account:

```bash
gh run watch "$(gh run list --workflow=terraform.yml --limit 1 --json databaseId -q '.[0].databaseId')" --exit-status

aws iam simulate-principal-policy \
  --policy-source-arn arn:aws:iam::339712964409:role/theagentorg-shared-agentcore-runtime-role \
  --action-names bedrock:InvokeModel \
  --resource-arns "arn:aws:bedrock:us-east-1:339712964409:inference-profile/us.amazon.nova-2-lite-v1:0" \
  --query 'EvaluationResults[0].EvalDecision' --output text
```

Expected: `allowed`. **Paste it.** Simulation is the check that matters here — a
green Terraform apply only proves the policy was written, not that it permits the
call.

---

## Task 2: Make the fixture fallback visible, the way the scanners already are

**Files:**
- Modify: `agentorg/state.py` (ADD one optional field to `RunState`)
- Modify: `agentorg/common/llm.py` (record which path answered)
- Modify: `agentorg/agents/planner.py`, `developer.py`, `reviewer.py`
- Modify: `agentorg/graph.py`, `scripts/run_stage.py` (render it)
- Create: `tests/test_model_provenance.py`

**Interfaces:**
- Consumes: nothing from Task 1, but is only *observable* once Task 1 lands.
- Produces:
  - `RunState.model_provenance: str = ""` — `""` unknown, `"model"`, `"fixture"`, or
    `"mixed"`.
  - `llm.last_source() -> str | None` — `"model"` or `"fixture"` for the most recent
    `structured`/`text` call, `None` if none has happened. Reset by
    `llm.reset_source()`.

**Why:** Task 1's defect survived because nothing distinguished "the model answered"
from "a fixture stood in". `SecurityResult.scan_provenance` exists for exactly this
and it is the reason the scanner path could be verified at all; no other result type
has an equivalent. This gives the model path the same discriminator.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_model_provenance.py`:

```python
"""Which answered -- the model, or a fixture? The same question scan_provenance answers.

MEASURED 2026-08-22: all four model-calling agents were serving fixtures in the
deployed pipeline because of an IAM denial, and NOTHING said so. The plan comment
on the target repo matched fixtures/plan_result.json byte for byte, every job was
green, and the only trace was a WARNING inside a container log nobody reads during
a demo.

`SecurityResult.scan_provenance` already prevents exactly this for the scanner
path. This module pins the equivalent for the model path.
"""

import pytest

from agentorg import graph
from agentorg.common import config, llm
from agentorg.state import RunState


def _state() -> RunState:
    return RunState(ticket_id="T-1", ticket_text="Add a per-IP login rate limit.")


def test_the_field_exists_and_defaults_to_unknown():
    """An optional ADDITION to the frozen contract, defaulting falsy."""
    assert _state().model_provenance == "", (
        "RunState.model_provenance must default to the empty string -- a run "
        "written before this field existed carries no provenance, and guessing "
        "one is what this field exists to prevent"
    )


def test_a_disabled_model_records_fixture_not_silence(monkeypatch):
    """The case that was live in production for a week."""
    monkeypatch.setattr(config, "LLM_DISABLED", True)
    llm.reset_source()
    result = llm.structured(RunState, "sys", "user")
    assert result is None, "a disabled model must return None"
    assert llm.last_source() == "fixture", (
        f"llm.last_source() is {llm.last_source()!r} after a disabled-model call; "
        f"it must be 'fixture', because a caller that cannot tell the model did "
        f"not answer is the exact defect this field exists to surface"
    )


def test_a_real_reply_records_model(monkeypatch):
    """The complement. Without this the field could be hardcoded to 'fixture'."""
    monkeypatch.setattr(config, "LLM_DISABLED", False)
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "_complete", lambda s, u: '{"tasks": ["a"], '
                        '"acceptance_criteria": ["b"], "target_files": ["c"]}')
    llm.reset_source()
    from agentorg.state import PlanResult
    result = llm.structured(PlanResult, "sys", "user")
    assert result is not None, "the stubbed model reply should have parsed"
    assert llm.last_source() == "model", (
        f"llm.last_source() is {llm.last_source()!r} after a successful model "
        f"call; it must be 'model', or the discriminator cannot distinguish the "
        f"two paths and is worthless"
    )


def test_an_unparseable_reply_records_fixture_because_the_caller_falls_back(monkeypatch):
    """A model that answered garbage is a fixture run from the caller's view.

    `structured()` returns None, so the agent loads its fixture. Recording 'model'
    here would claim the run used model output when it did not.
    """
    monkeypatch.setattr(config, "LLM_DISABLED", False)
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "_complete", lambda s, u: "not json at all")
    llm.reset_source()
    from agentorg.state import PlanResult
    assert llm.structured(PlanResult, "sys", "user") is None
    assert llm.last_source() == "fixture", (
        "an unparseable reply sends the caller to its fixture, so provenance is "
        "'fixture' -- claiming 'model' would assert the run used model output"
    )


def test_a_full_offline_run_is_labelled_fixture_end_to_end():
    """The suite's own runs are fixture runs, and the state must say so.

    conftest's guards force the offline path, so this asserts the label against
    the mode every other test in this repository runs in.
    """
    state = graph.run_pipeline("T-1", "Add a per-IP login rate limit.")
    assert state.model_provenance == "fixture", (
        f"an offline pipeline run recorded model_provenance="
        f"{state.model_provenance!r}; the whole suite runs with the model "
        f"disabled, so anything but 'fixture' means the field is not being "
        f"written on the pipeline path"
    )


def test_the_comment_says_which_path_answered():
    """A judge reads the PR, not a container log.

    The provenance has to reach the surface. A field recorded in state but never
    rendered is the same silence, one layer in.
    """
    posted: list[str] = []
    import agentorg.github_ops as github_ops
    original = github_ops.post_comment
    try:
        github_ops.post_comment = (
            lambda state, body, finding=None: posted.append(body) or "local://x"
        )
        graph.run_pipeline("T-1", "Add a per-IP login rate limit.")
    finally:
        github_ops.post_comment = original

    assert posted, "no comments were posted; this test would check nothing"
    plans = [b for b in posted if "· plan" in b]
    assert plans, f"no plan comment among {len(posted)} posted comments"
    assert "fixture" in plans[0].lower(), (
        f"the plan comment does not say which path answered: {plans[0][:300]!r}. "
        f"An offline run must say 'fixture' on the surface a judge reads, or the "
        f"demo shows agent-shaped output with no way to tell a model produced it."
    )
```

- [ ] **Step 2: Run them, watch them fail**

```bash
.venv-main/bin/python -m pytest -q tests/test_model_provenance.py -v
```

Expected: all six FAIL — the first with `AttributeError` or a pydantic error on
`model_provenance`, the rest with `AttributeError: module 'agentorg.common.llm' has
no attribute 'reset_source'`. Paste the output.

- [ ] **Step 3: Add the field to the frozen contract**

In `agentorg/state.py`, beside `poisoned`, add:

```python
    # WHICH PATH ANSWERED: the model, or a fixture. "" means a run written before
    # this field existed -- reported as unknown rather than guessed, exactly as
    # SecurityResult.scan_provenance's "" is.
    #
    # ADDED 2026-08-22, and the reason is the defect it would have caught. Every
    # model-calling agent in the deployed pipeline was serving fixtures for a week
    # because bedrock:InvokeModel was implicitDeny on the inference profile the
    # code asks for. `llm.text()` catches the denial by design, so the run
    # completed, every job was green, and the plan comment on the target repo
    # matched fixtures/plan_result.json byte for byte. The fallback is correct
    # behaviour; being unable to SEE it is not.
    #
    # "mixed" is a real value, not a hedge: a run where the planner reached the
    # model and the reviewer did not is neither a model run nor a fixture run, and
    # collapsing it to either one would make a partial outage look total or
    # invisible. Same reasoning as keeping `fixture-fallback` distinct from
    # `fixture-stub`.
    model_provenance: str = ""
```

Keep it a plain `str`, not a `Literal`. A `Literal` would make an older run's
unexpected value a validation error rather than an unknown, and this field's whole
purpose is to report honestly rather than refuse.

- [ ] **Step 4: Record the source in `llm.py`**

Add near the top of `agentorg/common/llm.py`, after the imports:

```python
# WHICH PATH ANSWERED THE MOST RECENT CALL. Module-level rather than a return
# value because `text()` and `structured()` already use None to mean "no usable
# answer", and widening either signature would change four agents' call sites for
# a fact only the pipeline layer needs.
#
# Reset explicitly by the caller rather than at the top of every call: a pipeline
# stage makes one model call, but the security agent makes a call AFTER the
# scanners have run, and a per-call reset would make the last writer win rather
# than letting the graph observe the whole run.
_LAST_SOURCE: str | None = None


def reset_source() -> None:
    """Forget which path answered. Call before a run, not between agents."""
    global _LAST_SOURCE
    _LAST_SOURCE = None


def last_source() -> str | None:
    """`"model"`, `"fixture"`, or None if no call has been made since the reset."""
    return _LAST_SOURCE


def _record(source: str) -> None:
    """`fixture` never downgrades to `model`.

    A run where ANY agent fell back is not a model run. Recording the optimistic
    value would let one successful call paper over three denials -- which is the
    shape of the defect this whole field exists to surface.
    """
    global _LAST_SOURCE
    if _LAST_SOURCE == "fixture":
        return
    _LAST_SOURCE = source
```

Then in `text()`, set `_record("fixture")` on **every** path that returns `None`
(unavailable, raised, non-`str`, empty-after-strip) and `_record("model")` on the
one path that returns a usable string. In `structured()`, set `_record("fixture")`
where it returns `None` after a parse or validation failure — the model spoke, but
the caller is about to load a fixture, and the caller's experience is what the field
reports.

- [ ] **Step 5: Stamp it onto the state**

In `agentorg/graph.py`, call `llm.reset_source()` immediately after the `RunState`
is constructed in `run_pipeline`, and set `state.model_provenance = llm.last_source()
or ""` in the `finally` block, immediately before `gates.save(state)`. The `finally`
is already where the run's ending is persisted, and putting this beside it means a
run that died mid-stage still records which path it had been using.

Do the same in `scripts/run_stage.py`: `llm.reset_source()` at the top of
`_stage_plan` and each stage that calls an agent, and set the field in `_emit`
before `gates.save`. **`_emit` is the single writer on that path**, so one edit
covers every stage — and CLAUDE.md records that three mutations already survived
because `run_stage.py` inherited `graph.py`'s comment about a hazard but not its
test. Add the equivalent assertion for both paths.

- [ ] **Step 6: Render it where a judge will see it**

In `graph._plan_comment` (and the `run_stage.py` equivalent), append a line in the
same shape the security comment already uses for `scan_provenance`:

```python
    lines.append(f"_source: {state.model_provenance or 'unknown'}_")
```

- [ ] **Step 7: Run the tests, then the full suite**

```bash
.venv-main/bin/python -m pytest -q tests/test_model_provenance.py -v
.venv-main/bin/python -m pytest -q
.venv-main/bin/python -m ruff check agentorg scripts tests
```

Expected: 6 passed; then `822 passed, 3 skipped`; ruff exit 0.

- [ ] **Step 8: RED step — four mutations**

Paste every failure, revert each.

1. In `_record`, delete the `if _LAST_SOURCE == "fixture": return` guard.
   Expected: the mixed-run protection is gone. Add a test asserting a
   fixture-then-model sequence still reports `fixture`, watch it fail, keep the test.
2. In `text()`, remove `_record("fixture")` from the `not available()` path.
   Expected: `test_a_disabled_model_records_fixture_not_silence` fails with
   `last_source() is None`.
3. In `graph.run_pipeline`'s `finally`, delete the `state.model_provenance = …` line.
   Expected: `test_a_full_offline_run_is_labelled_fixture_end_to_end` fails.
4. Remove the `_source:` line from the plan comment.
   Expected: `test_the_comment_says_which_path_answered` fails naming the body.

- [ ] **Step 9: Commit**

```bash
git add agentorg/state.py agentorg/common/llm.py agentorg/agents/planner.py \
        agentorg/agents/developer.py agentorg/agents/reviewer.py \
        agentorg/graph.py scripts/run_stage.py tests/test_model_provenance.py
git commit -m "feat(provenance): say which path answered, model or fixture

Task 1's defect -- every agent serving fixtures because of an IAM denial --
survived a week because nothing distinguished a model answer from a fixture. The
scanner path has had scan_provenance for exactly this reason and it is why that
path could be verified at all; the model path had no equivalent.

RunState.model_provenance is an optional ADDITION to the frozen contract, plain
str not Literal so an older run's value is unknown rather than a validation error.
'fixture' never downgrades to 'model': a run where any agent fell back is not a
model run, and letting one success paper over three denials is the defect itself."
```

---

## Task 3: One command that proves the deployed path is real

**Files:**
- Create: `scripts/preflight.py`
- Modify: `docs/plan/reem/demo_script.md` (Beat 0 gains one step)

**Interfaces:**
- Consumes: Task 1's grant, Task 2's `RunState.model_provenance`.
- Produces: `scripts/preflight.py`, exit 0 iff the deployed pipeline is genuinely
  model-backed and genuinely scanning.

**Why:** the three verifications that matter — the model answered, the scanners ran,
the gates hold — currently live in three different places and none is a single
command. Before a judged demo you want one thing to run.

- [ ] **Step 1: Write the script**

`scripts/preflight.py`. It is a **checked-in script, not a heredoc in a workflow**,
for the ruling `ci.yml:202-206` already made: the bytes CI runs must be the bytes
anyone can run, and YAML indentation silently rewrites Python.

```python
#!/usr/bin/env python
"""Is the deployed pipeline actually real? One command, four checks, exit 0 or 1.

Run this before a demo. Every check answers a question whose WRONG answer has
already happened once in this project:

  1. Can the runtime role invoke the model the code asks for?
     -- MEASURED implicitDeny on 2026-08-22. Every agent served fixtures.
  2. Do the five runtimes exist and report READY?
     -- READY is necessary and not sufficient; check 3 is the sufficient one.
  3. Does the security runtime return REAL scanner line numbers?
     -- {3, 4}, not the fixture's {4, 5}. The only field that separates them.
  4. Do the three Environments each have a required reviewer?
     -- An Environment with no reviewer does not pause. It runs.

Exits 1 on the first failure with a message naming what to do about it. Prints
every check's evidence, so the output is the record.
"""
```

The implementation calls, in order: `simulate-principal-policy` for the inference
profile ARN; `list-agent-runtimes` filtered to `theagentorg_`; a real
`invoke_agent_runtime` against `theagentorg_security` with a poisoned `RunState`,
asserting the finding lines equal `tests.provenance.REAL_SCANNER_LINES`; and
`gh api repos/{owner}/{repo}/environments` asserting each of `gate1`/`gate2`/`gate3`
carries a `required_reviewers` protection rule.

Import the line sets from `tests.provenance` rather than restating them — a
hardcoded copy is a second declaration of the fact this repository's whole
verification story rests on.

- [ ] **Step 2: Run it against the live account**

```bash
.venv-main/bin/python scripts/preflight.py; echo "exit=$?"
```

Expected after Task 1 has applied: all four checks pass, exit 0. **Paste the
output** — this becomes the demo runbook's evidence block.

- [ ] **Step 3: Prove each check can fail**

You cannot mutate live IAM cheaply, so prove the checks by pointing them at wrong
values: run with `--runtime-prefix theagentorg_nonexistent` (check 2 must fail),
and with an env var forcing the fixture line set (check 3 must fail). Paste both
failures.

- [ ] **Step 4: Add it to the runbook and commit**

Add to `docs/plan/reem/demo_script.md` Beat 0 as the **last** pre-flight step,
after the scanner install: "run `python scripts/preflight.py` — it must exit 0. If
check 3 fails, the security container is answering from a fixture and the block
beat's claim is false."

```bash
git add scripts/preflight.py docs/plan/reem/demo_script.md
git commit -m "feat(preflight): one command that proves the deployed path is real"
```

---

## Task 4: The diff parser must not silently scan nothing

**Files:**
- Modify: `agentorg/common/diff.py`
- Modify: `.github/workflows/deploy.yml` (the smoke test's assertion)
- Create: `tests/test_diff_headers.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `added_files` accepts every legal unified-diff header spelling; raises
  `ValueError` when a non-empty diff yields no files.

**Why this is the most dangerous finding in the audit:** `_HEADER = "+++ b/"`
recognises only git's default prefix. Proved:

```
b/ prefix    files=['app/auth.py']  key_visible=True
no prefix    files=[]               key_visible=False
```

The diff is **model-written**, so `--no-prefix` or `a/`-on-both-sides output is
plausible. With zero files materialised the scanners run successfully over an empty
tree, return `[]`, `compute_security_verdict([])` returns `("pass", [])`, and
`scan_provenance` still records `scanners` — truthfully, because they did run. They
had nothing to read.

The poisoned half is protected only by accident: `developer._key_is_in_the_change`
uses the same parser, so a no-prefix poisoned diff reads as "key absent" and the
safety net substitutes the `b/` fixture. **The clean half has no safety net**, so a
clean run reports `pass` from an empty scan — indistinguishable from a real pass.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_diff_headers.py`:

```python
"""Every legal `+++` spelling must materialise the file, or the scan is empty.

MEASURED 2026-08-22 against `_HEADER = "+++ b/"`:

    b/ prefix    files=['app/auth.py']  key_visible=True
    no prefix    files=[]               key_visible=False

Zero files means the scanners run over an empty tree, return [], and
compute_security_verdict([]) returns ("pass", []) -- while scan_provenance
truthfully records `scanners`, because they did run. The diff is model-written, so
`git diff --no-prefix` output is a plausible thing to receive.
"""

import pytest

from agentorg.common.diff import added_files

_KEY = "AKIAIOSFODNN7EXAMPLE"


def _diff(minus: str, plus: str) -> str:
    return (
        f"--- {minus}\n"
        f"+++ {plus}\n"
        "@@ -1,2 +1,3 @@\n"
        " from flask import request\n"
        f'+SECRET = "{_KEY}"\n'
    )


@pytest.mark.parametrize(
    ("minus", "plus", "label"),
    [
        ("a/app/auth.py", "b/app/auth.py", "git default"),
        ("app/auth.py", "app/auth.py", "--no-prefix"),
        ("a/app/auth.py", "a/app/auth.py", "a/ on both sides"),
        ("old/app/auth.py", "new/app/auth.py", "old/ new/ prefixes"),
        ("/dev/null", "b/app/auth.py", "a new file"),
    ],
)
def test_every_legal_header_spelling_materialises_the_file(minus, plus, label):
    """The parametrisation IS the test: one spelling passing proves nothing."""
    files = added_files(_diff(minus, plus))
    assert files, (
        f"the {label} spelling (+++ {plus}) materialised NO files. The scanners "
        f"would then run over an empty tree, find nothing, and the verdict would "
        f"be `pass` with scan_provenance truthfully reading `scanners`."
    )
    assert any(_KEY in body for body in files.values()), (
        f"the {label} spelling materialised {list(files)} but the added "
        f"credential is not in the body, so a scanner would not see it"
    )


def test_the_filename_is_the_same_whatever_the_prefix():
    """A finding must read `app/auth.py`, not `b/app/auth.py` or `new/app/auth.py`.

    The demo's central claim quotes a file and a line number. A prefix leaking into
    the path changes what a judge reads on the pull request.
    """
    for minus, plus in (
        ("a/app/auth.py", "b/app/auth.py"),
        ("app/auth.py", "app/auth.py"),
        ("old/app/auth.py", "new/app/auth.py"),
    ):
        assert list(added_files(_diff(minus, plus))) == ["app/auth.py"], (
            f"+++ {plus} produced {list(added_files(_diff(minus, plus)))}, "
            f"expected exactly ['app/auth.py']"
        )


def test_a_diff_that_yields_no_files_is_refused_not_scanned_empty():
    """The guard that makes the whole class of failure loud instead of silent.

    A non-empty diff that parses to zero files is not a clean change -- it is a
    diff this parser did not understand. Returning {} sends an empty tree to the
    scanners and the run reports `pass`. Raising makes the security stage fail,
    which is a red job rather than a false green.
    """
    with pytest.raises(ValueError, match="no files"):
        added_files("this is not a diff at all, but it is not empty either\n")


def test_an_empty_diff_is_still_an_empty_dict():
    """The complement, so the guard above cannot be over-eager.

    An empty or None diff genuinely proposes nothing -- that is not a parse
    failure, and it must not raise, because `added_files(None)` is a real call.
    """
    assert added_files("") == {}
    assert added_files(None) == {}
```

- [ ] **Step 2: Run them, watch them fail**

```bash
.venv-main/bin/python -m pytest -q tests/test_diff_headers.py -v
```

Expected: the `--no-prefix`, `a/ on both sides` and `old/ new/` cases FAIL on
"materialised NO files"; `test_a_diff_that_yields_no_files_is_refused…` FAILS with
`DID NOT RAISE`. The git-default and new-file cases PASS. Paste the output.

- [ ] **Step 3: Implement**

In `agentorg/common/diff.py`, replace the `_HEADER` constant and the header handling
with a regex that strips any single leading path component used as a prefix, and add
the refusal:

```python
# EVERY LEGAL `+++` SPELLING, not just git's default.
#
# MEASURED 2026-08-22, when this was the literal `"+++ b/"`: a `git diff
# --no-prefix` header materialised ZERO files, so the scanners ran over an empty
# tree, returned [], and compute_security_verdict([]) returned ("pass", []) --
# with scan_provenance truthfully recording `scanners`, because they had run. The
# diff is MODEL-WRITTEN, so a non-default prefix is a plausible thing to receive
# and not a hypothetical.
#
# `/dev/null` is matched and skipped: it is what git writes for the minus side of
# a new file, and treating it as a path would create a file called `null`.
_HEADER = re.compile(r"^\+\+\+ (?:/dev/null|(?:[^/\t\n]+/)?(?P<path>[^\t\n]+))")
```

And at the end of `added_files`, before the return:

```python
    # A NON-EMPTY DIFF THAT PARSES TO NOTHING IS A PARSE FAILURE, NOT A CLEAN
    # CHANGE, and the difference decides whether the pipeline lies.
    #
    # Returning {} here sends an empty directory to every scanner. They succeed,
    # find nothing, and the verdict is `pass` -- while scan_provenance says
    # `scanners`, which is true and useless. Raising makes the security stage fail
    # loudly: a red job, which is recoverable, instead of a green one that cleared
    # a change nobody read.
    if not files and diff and diff.strip():
        raise ValueError(
            f"parsed no files from a {len(diff)}-character diff. Every `+++` line "
            f"was unrecognised, so there is nothing to scan -- refusing rather "
            f"than handing an empty tree to the scanners, which would report a "
            f"clean pass over a change nobody read."
        )
    return files
```

Import `re` at the top if it is not already imported, keeping imports sorted —
`I001` is a ruff default here.

- [ ] **Step 4: Fix the deploy smoke test, which cannot currently fail**

`deploy.yml:369` asserts `grep -q '"tasks"'` on the invoke output, and
`fixtures/plan_result.json` **begins with** `"tasks"`. So the check passes
identically whether the model answered or the fixture did — which is how Task 1's
denial shipped green, in the very step whose comment says it asserts on content to
avoid "the reassuring non-answer".

Replace the assertion with one the fixture cannot satisfy. The fixture's `notes`
string is a fixed literal, so its **absence** is the discriminator:

```bash
              # ASSERTS THE MODEL ANSWERED, not merely that a plan came back.
              #
              # This previously grepped for '"tasks"' -- which
              # fixtures/plan_result.json BEGINS with, so the check passed
              # identically whether Bedrock answered or the fixture stood in. That
              # is how a week of fixture-only runs shipped green, in the step whose
              # own comment claims to assert on content.
              #
              # The fixture's `notes` value is a fixed literal. A real model reply
              # will not reproduce it, so its ABSENCE is the discriminator.
              if grep -q '"tasks"' /tmp/invoke-out.json \
                 && ! grep -qF 'Redis connection details must come from the environment.' /tmp/invoke-out.json; then
                echo "::notice::planner returned a real MODEL plan on attempt ${attempt}"
                exit 0
              fi
              if grep -qF 'Redis connection details must come from the environment.' /tmp/invoke-out.json; then
                echo "::error::the planner answered with its FIXTURE, not the model."
                echo "::error::Check bedrock:InvokeModel on the inference-profile ARN"
                echo "::error::for theagentorg-shared-agentcore-runtime-role."
                exit 1
              fi
```

Read the fixture's exact `notes` value with
`.venv-main/bin/python -c "import json; print(json.load(open('fixtures/plan_result.json'))['notes'])"`
and use that literal — **do not copy the string above on trust.**

- [ ] **Step 5: Run everything**

```bash
.venv-main/bin/python -m pytest -q tests/test_diff_headers.py -v
.venv-main/bin/python -m pytest -q
.venv-main/bin/python -m ruff check agentorg scripts tests
actionlint .github/workflows/*.yml
```

Expected: 8 passed; then the full suite green; ruff and actionlint exit 0. If any
existing scanner test breaks, read it before changing it — a test that assumed
`b/`-only parsing may have been asserting the bug.

- [ ] **Step 6: RED step — three mutations**

1. Revert `_HEADER` to the literal `"+++ b/"`.
   Expected: the three non-default spellings fail again.
2. Keep the new regex but delete the `raise ValueError` guard.
   Expected: `test_a_diff_that_yields_no_files_is_refused_not_scanned_empty` fails
   with `DID NOT RAISE`.
3. Make the guard unconditional (`if not files:`).
   Expected: `test_an_empty_diff_is_still_an_empty_dict` fails — proving the
   condition is not over-eager.

Paste each failure. `git diff` clean as the last step.

- [ ] **Step 7: Commit**

```bash
git add agentorg/common/diff.py .github/workflows/deploy.yml tests/test_diff_headers.py
git commit -m "fix(diff): a non-default prefix scanned nothing and reported pass

MEASURED: `git diff --no-prefix` output materialised ZERO files, so the scanners
ran over an empty tree, returned [], and compute_security_verdict([]) returned
('pass', []) -- with scan_provenance truthfully recording `scanners`. The diff is
model-written, so a non-default prefix is plausible, and the CLEAN half has no
safety net to catch it.

A non-empty diff that parses to no files now RAISES: a red security stage beats a
green one that cleared a change nobody read.

Also fixes the deploy smoke test, which grepped for '\"tasks\"' -- the string
fixtures/plan_result.json begins with -- so it passed identically for a fixture
and a real completion. That is how the IAM denial in the previous commit shipped
green, in the step written to prevent exactly that."
```

---

## Task 5: Give `auth-service` real CI, and read it

**Files:**
- Create: `auth-service/.github/workflows/ci.yml` (**the target repo**)
- Modify: `agentorg/github_ops.py` (add `ci_status`)
- Create: `tests/test_ci_status.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `github_ops.ci_status(state: RunState) -> str` returning exactly
  `"passing"`, `"failing"` or `"unknown"` — the three members of
  `SREResult.ci_status`, read from the frozen contract rather than restated.

**Why:** `auth-service` has **no CI at all** — no workflows directory, and its head
commit reports `state: pending` with **0** statuses:

```
$ gh api repos/mohamedsorour1998/auth-service/contents/.github/workflows
{"message":"Not Found","status":"404"}
$ gh api repos/.../commits/10d8e483.../status --jq '{state, total_count: (.statuses|length)}'
{"state":"pending","total_count":0}
```

GitHub returns `pending` for "nothing has run", which is indistinguishable from
"still running" if you read `state` naively. So `total_count == 0` must map to
`unknown`, not `pending`-as-`go` — otherwise the SRE agent would report "CI
passing" about a repository that has never run a test, which is the fail-open shape
the security lane exists to prevent.

**This must work for a target repo with CI and one without.** `unknown` is a
first-class answer, not an error.

- [ ] **Step 1: Give the target repo CI**

In the `auth-service` repository, create `.github/workflows/ci.yml`:

```yaml
# The tests the Agent Org's SRE agent reads before it recommends a deploy.
#
# Deliberately minimal: this repository is the SUBJECT of a demo pipeline, not the
# pipeline. What matters is that a real check runs on every push and pull request,
# so `github_ops.ci_status` has something true to report -- before this existed the
# commit status was `pending` with zero checks, and "no CI has ever run here" is
# not the same fact as "CI is still running".
name: ci

on:
  push:
    branches: [main]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install
        run: pip install flask pytest

      # `python -m pytest`, not bare `pytest`: `python -m` prepends the working
      # directory to sys.path and the console script does not, so the bare form
      # dies with `ModuleNotFoundError: No module named 'app'`.
      - name: Test
        run: python -m pytest tests -q
```

Commit it on a branch and open a PR rather than pushing to `main` directly, so the
first thing this workflow does is prove it runs on a pull request.

- [ ] **Step 2: Verify it produces a readable status**

```bash
gh pr create --repo mohamedsorour1998/auth-service --title "ci: run the tests on every push and PR" --body "..."
# once the run finishes:
SHA=$(gh api repos/mohamedsorour1998/auth-service/git/ref/heads/main --jq .object.sha)
gh api "repos/mohamedsorour1998/auth-service/commits/$SHA/check-runs" \
  --jq '{total: .total_count, conclusions: [.check_runs[].conclusion]}'
```

Expected: `total: 1`, `conclusions: ["success"]`. **Paste it.**

- [ ] **Step 3: Write the failing tests**

Create `tests/test_ci_status.py`. Every test stubs `github_ops._repo` — the seam
`conftest` already guards — so nothing reaches the network.

```python
"""`ci_status` must distinguish "no CI exists" from "CI is running" from "CI passed".

MEASURED 2026-08-22 on the target repo before it had any workflow:

    gh api repos/.../commits/<sha>/status -> {"state": "pending", "total_count": 0}

GitHub reports `pending` when NOTHING has run. Reading `state` naively therefore
calls an unchecked commit pending, and treating pending as go would make the SRE
agent report "CI passing" about a repository that has never run a test. That is the
fail-open shape the security lane exists to prevent, one agent over.
"""

import typing

import pytest

from agentorg import github_ops
from agentorg.state import RunState, SREResult


class _FakeCheckRun:
    def __init__(self, conclusion, status="completed"):
        self.conclusion = conclusion
        self.status = status


class _FakeCommit:
    def __init__(self, runs):
        self._runs = runs

    def get_check_runs(self):
        return _FakePaginated(self._runs)


class _FakePaginated(list):
    @property
    def totalCount(self):  # noqa: N802 - PyGithub's own spelling
        return len(self)


class _FakeRepo:
    def __init__(self, runs):
        self._runs = runs

    def get_commit(self, sha):
        return _FakeCommit(self._runs)

    def get_branch(self, name):
        class _B:
            commit = type("C", (), {"sha": "deadbeef"})()
        return _B()


def _state() -> RunState:
    state = RunState(ticket_id="7", ticket_text="Add a per-IP login rate limit.")
    from agentorg.state import DevResult
    state.dev = DevResult(branch="agent-org/7-abc1234", diff="", summary="",
                          files_changed=["app/auth.py"])
    return state


def test_the_three_answers_are_exactly_the_contracts_three(monkeypatch):
    """Read from the frozen contract, not restated.

    A fourth spelling here would fail SREResult validation at a distance, inside
    the SRE agent, rather than at this boundary.
    """
    allowed = set(typing.get_args(typing.get_type_hints(SREResult)["ci_status"]))
    assert allowed == {"passing", "failing", "unknown"}, (
        f"SREResult.ci_status now admits {sorted(allowed)}; ci_status() must be "
        f"updated to match, and this test is the tripwire"
    )


def test_zero_checks_is_unknown_not_passing(monkeypatch):
    """The measured case: a repository with no CI at all.

    `unknown` and not `passing`, because a commit nothing has checked is not a
    green commit -- and not `failing`, because nothing failed. This is why the
    third value exists.
    """
    monkeypatch.setattr(github_ops, "_repo", lambda: _FakeRepo([]))
    monkeypatch.setattr(github_ops, "_use_local", lambda: False)
    assert github_ops.ci_status(_state()) == "unknown", (
        "a head commit with zero check runs must be `unknown`. GitHub reports "
        "`pending` for this, and treating pending as passing would claim CI "
        "passed on a repository that has never run a test."
    )


def test_all_successful_is_passing(monkeypatch):
    monkeypatch.setattr(github_ops, "_repo",
                        lambda: _FakeRepo([_FakeCheckRun("success")]))
    monkeypatch.setattr(github_ops, "_use_local", lambda: False)
    assert github_ops.ci_status(_state()) == "passing"


def test_any_failure_is_failing(monkeypatch):
    """One red check outweighs any number of green ones."""
    monkeypatch.setattr(github_ops, "_repo", lambda: _FakeRepo([
        _FakeCheckRun("success"), _FakeCheckRun("failure"), _FakeCheckRun("success"),
    ]))
    monkeypatch.setattr(github_ops, "_use_local", lambda: False)
    assert github_ops.ci_status(_state()) == "failing", (
        "a failing check among passing ones must make the whole status failing; "
        "a majority vote on CI results is not a thing"
    )


def test_a_still_running_check_is_unknown_not_passing(monkeypatch):
    """In progress is not green. Treating it as green merges before CI finishes."""
    monkeypatch.setattr(github_ops, "_repo", lambda: _FakeRepo([
        _FakeCheckRun(None, status="in_progress"),
    ]))
    monkeypatch.setattr(github_ops, "_use_local", lambda: False)
    assert github_ops.ci_status(_state()) == "unknown"


def test_a_cancelled_or_timed_out_check_is_not_passing(monkeypatch):
    """Neither success nor a clean failure. Anything that is not `success` and not
    a running check is treated as failing, because promoting on a cancelled check
    is promoting on no information while looking decided."""
    for conclusion in ("cancelled", "timed_out", "action_required", "stale"):
        monkeypatch.setattr(github_ops, "_repo",
                            lambda c=conclusion: _FakeRepo([_FakeCheckRun(c)]))
        monkeypatch.setattr(github_ops, "_use_local", lambda: False)
        assert github_ops.ci_status(_state()) == "failing", (
            f"conclusion {conclusion!r} was treated as passing"
        )


def test_neutral_and_skipped_do_not_fail_the_build(monkeypatch):
    """GitHub's own semantics: `neutral` and `skipped` are not failures.

    A repository with a path-filtered workflow reports `skipped` on commits the
    filter excludes, and calling that a failure would block every such change.
    """
    monkeypatch.setattr(github_ops, "_repo", lambda: _FakeRepo([
        _FakeCheckRun("success"), _FakeCheckRun("skipped"), _FakeCheckRun("neutral"),
    ]))
    monkeypatch.setattr(github_ops, "_use_local", lambda: False)
    assert github_ops.ci_status(_state()) == "passing"


def test_the_offline_path_answers_unknown_rather_than_raising(monkeypatch):
    """The whole suite and every local run take this path.

    `unknown` is the honest answer with no GitHub to ask -- and it must not raise,
    because the SRE agent calls this on every run.
    """
    monkeypatch.setattr(github_ops, "_use_local", lambda: True)
    assert github_ops.ci_status(_state()) == "unknown"


def test_a_github_failure_is_unknown_not_passing(monkeypatch):
    """The seam raising must not become a green light."""
    def _boom():
        raise RuntimeError("api down")
    monkeypatch.setattr(github_ops, "_repo", _boom)
    monkeypatch.setattr(github_ops, "_use_local", lambda: False)
    assert github_ops.ci_status(_state()) == "unknown", (
        "an unreachable GitHub must be `unknown`; returning `passing` would make "
        "an outage look like a green build"
    )


def test_no_dev_branch_is_unknown(monkeypatch):
    """Called before `open_pr`, there is no head to look up."""
    monkeypatch.setattr(github_ops, "_use_local", lambda: False)
    state = RunState(ticket_id="7", ticket_text="x")
    assert github_ops.ci_status(state) == "unknown"
```

- [ ] **Step 4: Run them, watch them fail**

```bash
.venv-main/bin/python -m pytest -q tests/test_ci_status.py -v
```

Expected: all fail with `AttributeError: module 'agentorg.github_ops' has no
attribute 'ci_status'` except the contract test, which passes. Paste the output.

- [ ] **Step 5: Implement `ci_status`**

Add to `agentorg/github_ops.py`, near `deploy_note` (both are read-only reporters):

```python
# CI conclusions that are NOT failures. GitHub's own semantics: a skipped or
# neutral check is not a red build, and a path-filtered workflow reports `skipped`
# on every commit its filter excludes -- calling that a failure would block every
# such change.
_CI_NOT_A_FAILURE = frozenset({"success", "skipped", "neutral"})


def ci_status(state: RunState) -> str:
    """`"passing"`, `"failing"` or `"unknown"` for the run's head commit.

    THE THIRD VALUE IS THE POINT. GitHub reports a commit status of `pending` when
    NOTHING has run, which is indistinguishable from "still running" if you read
    that field. MEASURED on the target repo before it had any workflow:

        {"state": "pending", "total_count": 0}

    So zero checks is `unknown`, never `passing`. A commit nothing has examined is
    not a green commit, and an SRE agent reporting "CI passing" about a repository
    that has never run a test is the fail-open shape the security lane exists to
    prevent, one agent over.

    NEVER RAISES, and always returns one of the three. Every caller is on the
    pipeline path, and a promoted run must not depend on GitHub being reachable at
    the moment the SRE stage happens to run -- but an unreachable GitHub is
    `unknown`, not `passing`, so an outage cannot read as a green build.

    Works against a target repository with CI and one without. `unknown` is a
    first-class answer, not an error.
    """
    if _use_local() or state.dev is None or not state.dev.branch:
        return "unknown"

    try:
        repo = _repo()
        head = repo.get_branch(state.dev.branch).commit.sha
        runs = repo.get_commit(head).get_check_runs()
    except Exception:
        logging.getLogger(__name__).debug("ci_status lookup failed", exc_info=True)
        return "unknown"

    if runs.totalCount == 0:
        return "unknown"

    conclusions = [r.conclusion for r in runs]
    # A check with no conclusion has not finished. In progress is not green:
    # treating it as passing would merge before CI completed.
    if any(c is None for c in conclusions):
        return "unknown"
    if all(c in _CI_NOT_A_FAILURE for c in conclusions):
        return "passing"
    return "failing"
```

- [ ] **Step 6: Run the tests and the suite**

```bash
.venv-main/bin/python -m pytest -q tests/test_ci_status.py -v
.venv-main/bin/python -m pytest -q
.venv-main/bin/python -m ruff check agentorg scripts tests
```

Expected: 11 passed, full suite green, ruff exit 0.

- [ ] **Step 7: RED step — four mutations**

1. Delete the `if runs.totalCount == 0: return "unknown"` branch.
   Expected: `test_zero_checks_is_unknown_not_passing` fails — the case that was
   live on the real repository.
2. Change `any(c is None …)` to return `"passing"`.
   Expected: `test_a_still_running_check_is_unknown_not_passing` fails.
3. Change the `except` branch to `return "passing"`.
   Expected: `test_a_github_failure_is_unknown_not_passing` fails.
4. Add `"cancelled"` to `_CI_NOT_A_FAILURE`.
   Expected: `test_a_cancelled_or_timed_out_check_is_not_passing` fails naming
   `'cancelled'`.

Paste each. Revert each.

- [ ] **Step 8: Commit**

```bash
git add agentorg/github_ops.py tests/test_ci_status.py
git commit -m "feat(sre): read real CI, and distinguish 'no CI' from 'CI passed'

The target repo had no CI at all -- zero workflows, head commit reporting
{state: pending, total_count: 0}. GitHub says `pending` when nothing has run, so
reading that field naively calls an unchecked commit pending; treating pending as
go would have the SRE agent report 'CI passing' about a repository that has never
run a test.

Zero checks is `unknown`. An unfinished check is `unknown`. An unreachable GitHub
is `unknown`. None of them is `passing`, because an outage must not read as a
green build. Works for a target repo with CI and one without."
```

---

## Task 6: Make the SRE agent real — CI decides, the model advises

**Files:**
- Modify: `agentorg/agents/sre.py`
- Modify: `fixtures/sre_result.json` (regenerate via `make_fixtures.py`)
- Create: `tests/test_sre_agent.py`

**Interfaces:**
- Consumes: `github_ops.ci_status(state) -> str` from Task 5.
- Produces: `sre.run(state) -> SREResult` where `verdict` is decided by **code**, not
  by the model.

**Why the split:** the SRE verdict now gates a merge (Task 7). This project's whole
premise is that the shipping decision is deterministic — `compute_security_verdict`
is five lines of Python for exactly that reason. The same reasoning applies one agent
over: **CI failing always wins over a model `go`.** The model contributes SLO checks
and prose, which are advisory, exactly like `SecurityResult.explanation`.

Today `sre.run` ignores its state, never imports `llm`, and always returns
`fixtures/sre_result.json` — `verdict: go`, `ci_status: passing` — regardless of
anything. Its `SYSTEM_PROMPT` is written and never read.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_sre_agent.py`:

```python
"""The SRE verdict is decided by CODE reading real CI; the model only advises.

This agent's verdict gates a merge, so the reasoning that put
compute_security_verdict in pure Python applies here too: a model that is
prompt-injected, or simply wrong, must not be able to turn a red build into a
deploy.

BEFORE THIS TASK `sre.run` ignored its state, never called a model, and always
returned fixtures/sre_result.json -- verdict `go`, ci_status `passing` -- whatever
CI actually said. "Merge when SRE says go" would have meant "always merge".
"""

import pytest

from agentorg import github_ops
from agentorg.agents import sre
from agentorg.common import llm
from agentorg.state import DevResult, RunState, SREResult


def _state() -> RunState:
    state = RunState(ticket_id="7", ticket_text="Add a per-IP login rate limit.")
    state.dev = DevResult(branch="agent-org/7-abc1234", diff="", summary="s",
                          files_changed=["app/auth.py"])
    return state


def test_failing_ci_is_no_go_whatever_the_model_says(monkeypatch):
    """THE test. A model `go` must not override a red build."""
    monkeypatch.setattr(github_ops, "ci_status", lambda state: "failing")
    monkeypatch.setattr(llm, "structured", lambda *a, **k: SREResult(
        verdict="go", ci_status="passing",
        slo_checks=[], notes="ship it, looks fine to me"))

    result = sre.run(_state())
    assert result.verdict == "no_go", (
        "CI was failing and the model said go, and the agent returned "
        f"{result.verdict!r}. The verdict must be decided by code: a model that is "
        "wrong or manipulated cannot be allowed to turn a red build into a deploy."
    )
    assert result.ci_status == "failing", (
        f"ci_status is {result.ci_status!r} but CI was failing -- the model's "
        "claim about CI was echoed instead of the measured value"
    )


def test_passing_ci_is_go(monkeypatch):
    monkeypatch.setattr(github_ops, "ci_status", lambda state: "passing")
    monkeypatch.setattr(llm, "structured", lambda *a, **k: None)
    result = sre.run(_state())
    assert result.verdict == "go"
    assert result.ci_status == "passing"


def test_unknown_ci_is_reported_as_unknown_not_laundered(monkeypatch):
    """A repo with no CI must not be described as passing.

    Whether `unknown` permits a merge is Task 7's decision; what this pins is that
    the FIELD tells the truth. Laundering unknown into passing is the fail-open
    shape this project exists to prevent.
    """
    monkeypatch.setattr(github_ops, "ci_status", lambda state: "unknown")
    monkeypatch.setattr(llm, "structured", lambda *a, **k: None)
    result = sre.run(_state())
    assert result.ci_status == "unknown", (
        f"ci_status is {result.ci_status!r} for a target with no CI; it must be "
        "'unknown', because claiming 'passing' about a repository that has never "
        "run a test is a false claim on the surface a judge reads"
    )


def test_the_measured_ci_check_is_always_in_the_slo_checks(monkeypatch):
    """The evidence must reach the PR, not just the verdict.

    A verdict with no visible basis is indistinguishable from a guess.
    """
    monkeypatch.setattr(github_ops, "ci_status", lambda state: "passing")
    monkeypatch.setattr(llm, "structured", lambda *a, **k: None)
    result = sre.run(_state())
    names = [c.name for c in result.slo_checks]
    assert any("ci" in n.lower() for n in names), (
        f"no CI check among the slo_checks {names}; the measured fact the verdict "
        f"rests on is not visible on the pull request"
    )
    ci_check = next(c for c in result.slo_checks if "ci" in c.name.lower())
    assert ci_check.passed is True
    assert "passing" in ci_check.detail


def test_a_failing_ci_check_is_recorded_as_not_passed(monkeypatch):
    monkeypatch.setattr(github_ops, "ci_status", lambda state: "failing")
    monkeypatch.setattr(llm, "structured", lambda *a, **k: None)
    result = sre.run(_state())
    ci_check = next(c for c in result.slo_checks if "ci" in c.name.lower())
    assert ci_check.passed is False


def test_the_model_contributes_its_slo_checks(monkeypatch):
    """The model's advisory half is used, not discarded.

    Otherwise the model call is decoration and the prompt is dead code again --
    which is the state this task exists to leave behind.
    """
    from agentorg.state import SLOCheck
    monkeypatch.setattr(github_ops, "ci_status", lambda state: "passing")
    monkeypatch.setattr(llm, "structured", lambda *a, **k: SREResult(
        verdict="go", ci_status="unknown",
        slo_checks=[SLOCheck(name="error budget", passed=True, detail="97% left")],
        notes="Rollback is a revert of one commit.",
        estimated_cost_note="No new infrastructure."))

    result = sre.run(_state())
    names = [c.name for c in result.slo_checks]
    assert "error budget" in names, (
        f"the model's SLO check is missing from {names}; its advisory "
        f"contribution was dropped"
    )
    assert "revert" in result.notes, "the model's notes were discarded"
    assert "infrastructure" in result.estimated_cost_note


def test_the_model_cannot_smuggle_a_verdict_through_slo_checks(monkeypatch):
    """A model-authored check that claims to have failed does not flip the verdict.

    The verdict is CI's. A model asserting `passed=False` on an invented check
    would otherwise be an indirect route to no_go -- which is the same authority
    the first test denies it directly.
    """
    from agentorg.state import SLOCheck
    monkeypatch.setattr(github_ops, "ci_status", lambda state: "passing")
    monkeypatch.setattr(llm, "structured", lambda *a, **k: SREResult(
        verdict="no_go", ci_status="failing",
        slo_checks=[SLOCheck(name="invented", passed=False, detail="I disapprove")],
        notes=""))
    assert sre.run(_state()).verdict == "go", (
        "the model returned no_go with a failed check and flipped the verdict; "
        "the verdict must come from CI alone"
    )


def test_no_model_still_produces_a_usable_result(monkeypatch):
    """The offline path -- the whole suite, and every local run.

    `structured` returning None must not crash the stage, and the fixture must not
    be able to contradict the measured CI status.
    """
    monkeypatch.setattr(github_ops, "ci_status", lambda state: "failing")
    monkeypatch.setattr(llm, "structured", lambda *a, **k: None)
    result = sre.run(_state())
    assert isinstance(result, SREResult)
    assert result.verdict == "no_go"
    assert result.ci_status == "failing", (
        "the fixture's `ci_status: passing` overwrote the measured value on the "
        "no-model path"
    )


def test_the_system_prompt_is_actually_used():
    """It was dead code before this task. This is the tripwire for it going dead again."""
    import inspect
    source = inspect.getsource(sre)
    assert "SYSTEM_PROMPT" in source
    assert source.count("SYSTEM_PROMPT") >= 2, (
        "SYSTEM_PROMPT is defined but never referenced -- the agent is not calling "
        "the model, which is the exact state this task removed"
    )
```

- [ ] **Step 2: Run them, watch them fail**

```bash
.venv-main/bin/python -m pytest -q tests/test_sre_agent.py -v
```

Expected: most fail — the stub returns the fixture, so `verdict` is `go` and
`ci_status` is `passing` regardless. `test_the_system_prompt_is_actually_used` fails
on the count. Paste the output.

- [ ] **Step 3: Implement**

Replace the body of `agentorg/agents/sre.py`:

```python
"""SRE agent — the final go/no-go, decided by CI and explained by the model.

OWNER: Sorour.

WHY THE VERDICT IS NOT THE MODEL'S. This agent's verdict gates a merge, and the
premise of this whole pipeline is that the shipping decision is deterministic --
`compute_security_verdict` is five lines of Python for that reason. The same
reasoning applies here: a model that is prompt-injected, or simply having a bad
day, must not be able to turn a red build into a deploy.

So:

    ci_status  <- github_ops.ci_status(state), a real GitHub API read
    verdict    <- code:  "no_go" if ci_status == "failing" else "go"
    slo_checks <- the measured CI check, PLUS whatever the model contributes
    notes      <- the model's prose

The model cannot reach `verdict` or `ci_status`. It cannot reach them indirectly
through `slo_checks` either -- a model-authored check claiming `passed=False` is
recorded and does not flip the verdict, because that would be the same authority by
another route.

BEFORE 2026-08-22 this module was a stub: it ignored its state, never imported
`llm`, and returned `fixtures/sre_result.json` -- `verdict: go`, `ci_status:
passing` -- whatever CI said. `SYSTEM_PROMPT` was written and never read.
"""

from .. import fixtures_loader, github_ops
from ..common import llm
from ..state import RunState, SLOCheck, SREResult

SYSTEM_PROMPT = """You are the SRE reviewing a proposed change before deployment.

Return an SREResult. Two of its fields are NOT yours to set and will be
overwritten: `verdict` and `ci_status` are measured from the repository's real CI.

Contribute:
  * slo_checks -- operational risks you can see in the diff, each with a name, a
    boolean, and a one-line detail. Do not invent a CI check; one is added for you.
  * estimated_cost_note -- any new infrastructure or spend this change implies.
  * notes -- how to roll this back, in one sentence.

Be brief and concrete. Output must match the SREResult schema."""

# The name of the check carrying the measured CI fact. A constant because
# tests/test_sre_agent.py looks for it and graph's comment renders it -- two
# readers, one definition.
CI_CHECK_NAME = "CI"


def _prompt(state: RunState) -> str:
    parts = [f"TICKET:\n{state.ticket_text}"]
    if state.dev is not None:
        parts.append(f"CHANGE SUMMARY:\n{state.dev.summary}")
        parts.append(f"FILES CHANGED:\n{', '.join(state.dev.files_changed)}")
        parts.append(f"DIFF:\n{state.dev.diff}")
    if state.security is not None:
        parts.append(
            f"SECURITY VERDICT: {state.security.verdict} "
            f"({len(state.security.blocking)} blocking)"
        )
    return "\n\n".join(parts)


def run(state: RunState) -> SREResult:
    """Real CI decides; the model advises. See the module docstring."""
    # MEASURED FIRST, so nothing below can be mistaken for it. Never raises;
    # returns "passing", "failing" or "unknown".
    ci = github_ops.ci_status(state)

    advice = llm.structured(SREResult, SYSTEM_PROMPT, _prompt(state))
    if advice is None:
        advice = fixtures_loader.sre()

    ci_check = SLOCheck(
        name=CI_CHECK_NAME,
        passed=(ci == "passing"),
        detail=f"CI reports {ci} for this change's head commit",
    )

    # The model's checks are kept, with the measured one FIRST so a reader sees
    # the fact the verdict rests on before the advice. The model's own `verdict`
    # and `ci_status` are dropped on the floor, which is the point.
    return SREResult(
        verdict="no_go" if ci == "failing" else "go",
        ci_status=ci,
        slo_checks=[ci_check, *advice.slo_checks],
        estimated_cost_note=advice.estimated_cost_note,
        notes=advice.notes,
    )
```

Note `unknown` yields `go` here — a target repository with no CI still proceeds, and
the honest `unknown` reaches the PR comment. Whether that should block a *merge* is
Task 7's decision, made there deliberately rather than smuggled in as a side effect
of this verdict.

- [ ] **Step 4: Verify, then the suite**

```bash
.venv-main/bin/python -m pytest -q tests/test_sre_agent.py -v
.venv-main/bin/python -m pytest -q
.venv-main/bin/python -m ruff check agentorg scripts tests
```

Expected: 9 passed, full suite green, ruff exit 0. If `test_agent_fallbacks.py`
breaks, read it first — it may have been asserting that `sre.run` returns the
fixture verbatim, which is now deliberately no longer true. Update it to assert the
new contract and say so in the commit.

- [ ] **Step 5: RED step — four mutations**

1. Change the verdict line to `verdict=advice.verdict`.
   Expected: `test_failing_ci_is_no_go_whatever_the_model_says` fails — the whole
   point of the task.
2. Change `ci_status=ci` to `ci_status=advice.ci_status`.
   Expected: the same test fails on the `ci_status` assertion, plus
   `test_unknown_ci_is_reported_as_unknown_not_laundered`.
3. Drop `*advice.slo_checks` from the list.
   Expected: `test_the_model_contributes_its_slo_checks` fails.
4. Drop `ci_check` from the list.
   Expected: `test_the_measured_ci_check_is_always_in_the_slo_checks` fails.

Paste each failure; revert each.

- [ ] **Step 6: Commit**

```bash
git add agentorg/agents/sre.py tests/test_sre_agent.py
git commit -m "feat(sre): a real agent — CI decides the verdict, the model advises

Before this, sre.run ignored its state, never imported llm, and always returned
fixtures/sre_result.json: verdict go, ci_status passing, whatever CI actually said.
Its SYSTEM_PROMPT was written and never read. Since this verdict now gates a merge,
'merge when SRE says go' would have meant 'always merge'.

The verdict is code, for the same reason compute_security_verdict is: a model that
is prompt-injected or simply wrong must not turn a red build into a deploy. The
model contributes slo_checks and prose, and cannot reach verdict or ci_status --
nor reach them indirectly through a check claiming to have failed."
```

---

## Task 7: `promote` merges the pull request

**Files:**
- Modify: `agentorg/github_ops.py` (add `merge_pr`)
- Modify: `scripts/run_stage.py` (`_stage_promote`), `agentorg/graph.py` (promote step)
- Create: `tests/test_merge_pr.py`

**Interfaces:**
- Consumes: Task 5's `ci_status`, Task 6's real `SREResult`.
- Produces: `github_ops.merge_pr(state: RunState) -> str` returning a ref —
  `https://…` on a real merge, `local://<branch>` offline, or
  `merge://refused/<reason>` when it declined. **Never raises.**

**Why:** nothing currently merges. `promote` writes `status="promoted"` and leaves
the PR open, and `"merged"` exists in the frozen `LogEvent.action` union and in
`timeline._MARK` (`⇄`) with **no producer anywhere**. So the pipeline's final claim
means "three humans approved and we wrote it down".

**Why `promote` and not the SRE agent:** `promote` runs only past gate3, so three
humans have already clicked and there is nothing left to decide. Merging is an
irreversible write to someone else's repository; putting it behind an agent's
opinion means a model decides.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_merge_pr.py`:

```python
"""Merging is the last write, and the one that cannot be taken back.

`promote` runs only past gate3 -- three human approvals -- so there is nothing left
to decide. But an irreversible write still needs preconditions that are checked
rather than assumed, and a refusal that is RECORDED rather than silent.
"""

import pytest

from agentorg import github_ops
from agentorg.state import (DevResult, HumanDecision, RunState, SecurityResult,
                            SREResult)


class _FakePR:
    def __init__(self, mergeable=True):
        self.html_url = "https://github.com/o/r/pull/7"
        self.mergeable = mergeable
        self.merged = False
        self.merge_calls: list[dict] = []

    def merge(self, **kwargs):
        self.merge_calls.append(kwargs)
        self.merged = True
        return type("R", (), {"merged": True, "sha": "cafe1234"})()


class _FakePaginated(list):
    @property
    def totalCount(self):  # noqa: N802 - PyGithub's spelling
        return len(self)


class _FakeRepo:
    def __init__(self, pr=None):
        self._pr = pr
        self.owner = type("O", (), {"login": "o"})()

    def get_pulls(self, state=None, head=None):
        return _FakePaginated([self._pr] if self._pr else [])


def _promotable() -> RunState:
    """A state in exactly the shape promote sees: past gate3, everything clear."""
    state = RunState(ticket_id="7", ticket_text="Add a per-IP login rate limit.")
    state.dev = DevResult(branch="agent-org/7-abc1234", diff="", summary="s",
                          files_changed=["app/auth.py"],
                          pr_url="https://github.com/o/r/pull/7")
    state.security = SecurityResult(verdict="pass", scan_provenance="scanners")
    state.sre = SREResult(verdict="go", ci_status="passing")
    state.decisions = [
        HumanDecision(gate=g, decision="approved", by="reviewer")
        for g in ("gate1", "gate2", "gate3")
    ]
    return state


def test_a_promotable_run_is_merged(monkeypatch):
    pr = _FakePR()
    monkeypatch.setattr(github_ops, "_use_local", lambda: False)
    monkeypatch.setattr(github_ops, "_repo", lambda: _FakeRepo(pr))
    ref = github_ops.merge_pr(_promotable())
    assert pr.merged, "the pull request was not merged"
    assert ref.startswith("https://"), f"ref {ref!r} does not name a delivered merge"


def test_a_blocked_run_is_never_merged(monkeypatch):
    """Defence in depth. `promote` is unreachable on a blocked run because gate2
    needs develop -- but a function that performs an irreversible write must not
    rely on a caller's control flow for the one thing it must never do."""
    pr = _FakePR()
    monkeypatch.setattr(github_ops, "_use_local", lambda: False)
    monkeypatch.setattr(github_ops, "_repo", lambda: _FakeRepo(pr))
    state = _promotable()
    state.security = SecurityResult(verdict="block", scan_provenance="scanners")

    ref = github_ops.merge_pr(state)
    assert not pr.merged, (
        "A BLOCKED RUN WAS MERGED. The security verdict was `block` and the merge "
        "proceeded anyway."
    )
    assert "refused" in ref, f"the refusal was not recorded in the ref: {ref!r}"


def test_a_run_missing_a_gate_approval_is_never_merged(monkeypatch):
    """Three approvals, checked here as well as enforced by the job graph."""
    pr = _FakePR()
    monkeypatch.setattr(github_ops, "_use_local", lambda: False)
    monkeypatch.setattr(github_ops, "_repo", lambda: _FakeRepo(pr))
    state = _promotable()
    state.decisions = state.decisions[:2]   # gate3 never approved

    ref = github_ops.merge_pr(state)
    assert not pr.merged, "a run with only two approvals was merged"
    assert "refused" in ref


def test_a_rejected_decision_blocks_the_merge_even_with_three_rows(monkeypatch):
    """A rejection among the approvals is not an approval.

    Counting rows rather than reading them would let a rejected gate satisfy the
    check -- and `gates.resume` never un-sets a rejection, so this state is
    reachable.
    """
    pr = _FakePR()
    monkeypatch.setattr(github_ops, "_use_local", lambda: False)
    monkeypatch.setattr(github_ops, "_repo", lambda: _FakeRepo(pr))
    state = _promotable()
    state.decisions[1] = HumanDecision(gate="gate2", decision="rejected", by="r")

    ref = github_ops.merge_pr(state)
    assert not pr.merged, "a run carrying a REJECTED gate decision was merged"
    assert "refused" in ref


def test_an_sre_no_go_blocks_the_merge(monkeypatch):
    pr = _FakePR()
    monkeypatch.setattr(github_ops, "_use_local", lambda: False)
    monkeypatch.setattr(github_ops, "_repo", lambda: _FakeRepo(pr))
    state = _promotable()
    state.sre = SREResult(verdict="no_go", ci_status="failing")
    assert not pr.merged
    assert "refused" in github_ops.merge_pr(state)


def test_the_offline_path_does_not_reach_github(monkeypatch):
    """Every local run and the whole suite. Must not raise, must not write."""
    monkeypatch.setattr(github_ops, "_use_local", lambda: True)
    ref = github_ops.merge_pr(_promotable())
    assert ref.startswith("local://"), f"offline merge returned {ref!r}"


def test_a_github_failure_is_recorded_and_does_not_raise(monkeypatch):
    """`promote` must finish. A failed merge is a recorded fact, not a crash.

    Same requirement as post_comment: this is called immediately before the run's
    ending is written, and an exception here would lose it.
    """
    def _boom():
        raise RuntimeError("api down")
    monkeypatch.setattr(github_ops, "_use_local", lambda: False)
    monkeypatch.setattr(github_ops, "_repo", _boom)
    ref = github_ops.merge_pr(_promotable())
    assert "refused" in ref or "merge://" in ref, f"unexpected ref {ref!r}"


def test_an_unmergeable_pr_is_refused_not_forced(monkeypatch):
    """A conflicting PR is a fact to report, not an obstacle to route around."""
    pr = _FakePR(mergeable=False)
    monkeypatch.setattr(github_ops, "_use_local", lambda: False)
    monkeypatch.setattr(github_ops, "_repo", lambda: _FakeRepo(pr))
    ref = github_ops.merge_pr(_promotable())
    assert not pr.merged, "an unmergeable pull request was merged"
    assert "refused" in ref


def test_a_missing_pr_is_refused(monkeypatch):
    monkeypatch.setattr(github_ops, "_use_local", lambda: False)
    monkeypatch.setattr(github_ops, "_repo", lambda: _FakeRepo(None))
    assert "refused" in github_ops.merge_pr(_promotable())


def test_the_promote_stage_writes_a_merged_log_row(monkeypatch, tmp_path):
    """The `merged` action has vocabulary in the frozen contract and no producer.

    Without this row the timeline's ⇄ glyph is unreachable and the run's own log
    cannot say whether the change actually landed.
    """
    from agentorg import log
    from agentorg.state import LogEvent
    rows: list[LogEvent] = []
    monkeypatch.setattr(log, "append", lambda e: rows.append(e) or e)
    monkeypatch.setattr(github_ops, "merge_pr",
                        lambda state: "https://github.com/o/r/pull/7")

    import scripts.run_stage as run_stage
    state = _promotable()
    monkeypatch.setattr(run_stage, "_load", lambda run_id: state)
    monkeypatch.setattr(run_stage, "_emit", lambda s: None)

    args = type("A", (), {"run_id": state.run_id})()
    run_stage._stage_promote(args)

    actions = [r.action for r in rows]
    assert "merged" in actions, (
        f"promote wrote {actions} and none is 'merged'. The action exists in the "
        f"frozen contract and in timeline._MARK; without a producer the ⇄ glyph is "
        f"unreachable and the log cannot say the change landed."
    )
    assert "promoted" in actions, "the promoted row is gone"
    assert actions.index("merged") < actions.index("promoted"), (
        "the merged row must precede the promoted row: the merge is what makes the "
        "promotion true, and the timeline reads its banner off the LAST action"
    )
```

- [ ] **Step 2: Run them, watch them fail**

```bash
.venv-main/bin/python -m pytest -q tests/test_merge_pr.py -v
```

Expected: every test fails with `AttributeError: … has no attribute 'merge_pr'`.
Paste the output.

- [ ] **Step 3: Implement `merge_pr`**

Add to `agentorg/github_ops.py`, after `post_comment`:

```python
# THE PRECONDITIONS FOR AN IRREVERSIBLE WRITE, checked here as well as enforced by
# the job graph. `promote` is only reachable past gate3, so in the workflow these
# are already true -- but a function that merges somebody's pull request must not
# depend on a caller's control flow for the one thing it must never do, and
# `graph.py` and `run_stage.py` are two callers with two orderings.
_MERGE_REQUIRED_GATES = ("gate1", "gate2", "gate3")


def _merge_refusal(state: RunState) -> str | None:
    """Why this run must not be merged, or None if it may be.

    Returns a SHORT reason suitable for a ref and a log summary. Every branch is a
    fact about the run, not about GitHub -- GitHub's own refusals are handled at
    the call site, because "we declined" and "GitHub declined" are different
    events and a reader needs to know which.
    """
    if state.security is None or state.security.verdict != "pass":
        verdict = None if state.security is None else state.security.verdict
        return f"security-verdict-{verdict}"
    if state.sre is None or state.sre.verdict != "go":
        verdict = None if state.sre is None else state.sre.verdict
        return f"sre-verdict-{verdict}"

    # READ the decisions, do not count them. `gates.resume` never un-sets a
    # rejection, so a run can carry three decision rows one of which is a refusal;
    # counting rows would let that satisfy the check.
    approved = {
        d.gate for d in state.decisions
        if d.decision in ("approved", "overridden")
    }
    rejected = {d.gate for d in state.decisions if d.decision == "rejected"}
    if rejected:
        return f"gate-rejected-{sorted(rejected)[0]}"
    missing = [g for g in _MERGE_REQUIRED_GATES if g not in approved]
    if missing:
        return f"gate-not-approved-{missing[0]}"
    return None


def merge_pr(state: RunState) -> str:
    """Merge the run's pull request. Returns a ref; NEVER raises.

    The same hard requirement as post_comment, for the same reason: `promote` calls
    this and then writes the run's ending, so an exception here would lose the
    ending. A merge that did not happen is a recorded fact.

    Refs:
        https://…                     merged
        local://<branch>              offline; no GitHub was reached
        merge://refused/<reason>      we declined, with the reason
        merge://failed/<type>         GitHub declined or was unreachable
    """
    refusal = _merge_refusal(state)
    if refusal is not None:
        logging.getLogger(__name__).warning(
            "refusing to merge run %s: %s", state.run_id, refusal
        )
        return f"merge://refused/{refusal}"

    branch = state.dev.branch if state.dev else ""
    if _use_local():
        # Offline the branch is already committed in OFFLINE_REPO and there is no
        # PR to merge. Reported as delivered-locally rather than refused: nothing
        # about the RUN prevented the merge.
        return f"local://{branch}"

    try:
        repo = _repo()
        pulls = repo.get_pulls(state="open", head=f"{repo.owner.login}:{branch}")
        if pulls.totalCount == 0:
            return "merge://failed/no-open-pull-request"
        pull = pulls[0]

        # `mergeable` is None while GitHub is still computing it, and False on a
        # conflict. Neither is a green light, and neither is worth forcing: a
        # conflict is a fact to report.
        if pull.mergeable is False:
            return "merge://failed/not-mergeable"

        result = pull.merge(
            commit_title=f"{state.ticket_id}: {state.dev.summary}",
            commit_message=(
                f"Merged by The Agent Org after three human approvals.\n\n"
                f"run_id: {state.run_id}\n"
                f"security: {state.security.verdict} "
                f"(provenance: {state.security.scan_provenance or 'unknown'})\n"
                f"ci: {state.sre.ci_status}\n"
            ),
            merge_method="squash",
        )
        if not getattr(result, "merged", False):
            return "merge://failed/github-declined"
        return pull.html_url
    except Exception as exc:
        logging.getLogger(__name__).debug("merge failed", exc_info=True)
        return f"merge://failed/{type(exc).__name__}"
```

- [ ] **Step 4: Call it from both promote paths**

In `scripts/run_stage.py::_stage_promote`, before the existing `promoted` row:

```python
    # MERGED BEFORE PROMOTED, and the order is load-bearing twice over. The merge
    # is what makes the promotion true, so a promoted row written first would
    # claim an outcome that had not happened yet. And `timeline._outcome` reads
    # its banner off the LAST row's action, so `promoted` must be last or the run
    # ends on ⇄ MERGED instead of ★ PROMOTED.
    ref = github_ops.merge_pr(state)
    _log(state, "system", "promote", "merged",
         summary=f"pull request merged; {ref}", artifact_ref=ref)
```

Make the identical change in `graph.py`'s promote step. **Both**, and add the
assertion to `tests/test_merge_pr.py` for the graph path too — CLAUDE.md records
three mutations that survived because `run_stage.py` inherited `graph.py`'s comment
about a hazard but not its test.

- [ ] **Step 5: Verify**

```bash
.venv-main/bin/python -m pytest -q tests/test_merge_pr.py -v
.venv-main/bin/python -m pytest -q
.venv-main/bin/python -m ruff check agentorg scripts tests
```

Expected: 11 passed, full suite green, ruff exit 0.

- [ ] **Step 6: RED step — five mutations, and the first is the one that matters**

1. In `_merge_refusal`, delete the `security.verdict != "pass"` branch.
   Expected: `test_a_blocked_run_is_never_merged` fails with **A BLOCKED RUN WAS
   MERGED**. This is the assertion the whole task rests on.
2. Replace the `rejected` check with `len(state.decisions) >= 3`.
   Expected: `test_a_rejected_decision_blocks_the_merge_even_with_three_rows` fails.
3. Delete the `sre.verdict != "go"` branch.
   Expected: `test_an_sre_no_go_blocks_the_merge` fails.
4. Change `except Exception` to `raise`.
   Expected: `test_a_github_failure_is_recorded_and_does_not_raise` fails.
5. Swap the two `_log` calls in `_stage_promote` so `promoted` precedes `merged`.
   Expected: the ordering assertion fails — and note this is exactly the
   timeline-banner defect this repo has already hit once.

Paste each failure. Revert each. `git diff` clean as your last step.

- [ ] **Step 7: Commit**

```bash
git add agentorg/github_ops.py agentorg/graph.py scripts/run_stage.py tests/test_merge_pr.py
git commit -m "feat(promote): actually merge the pull request

Nothing merged. promote wrote status='promoted' and left the PR open, so the
pipeline's final claim meant 'three humans approved and we wrote it down'. The
`merged` action has existed in the frozen contract and in timeline._MARK (⇄) with
no producer anywhere.

promote owns this, not the SRE agent: promote is only reachable past gate3, so
three humans have clicked and nothing is left to decide. Merging is irreversible,
and putting it behind an agent's opinion means a model decides.

merge_pr re-checks its own preconditions rather than trusting the job graph, and
READS the gate decisions instead of counting them -- gates.resume never un-sets a
rejection, so three rows can include a refusal. It never raises: promote writes the
run's ending immediately after, and a failed merge is a recorded fact."
```

---

## Task 8: A `failed` run must say so — the timeline's two lies

**Files:**
- Modify: `agentorg/graph.py` (the SRE `no_go` exit), `scripts/run_stage.py`
- Modify: `scripts/run_stage.py` (`_OUTCOME_ACTIONS`)
- Modify: `agentorg/timeline.py` (`_OUTCOME`)
- Create: `tests/test_failed_run_rendering.py`

**Interfaces:**
- Consumes: nothing.
- Produces: no new API. `timeline._outcome` gains a `failed` banner.

**Why — two distinct defects, both PROVED, both on the projector:**

1. **A revision-cap `failed` run renders as `⛔ BLOCKED`.** `run_stage`'s
   `_OUTCOME_ACTIONS` maps `failed → "blocked"`, so the last row's action is
   `blocked` and the banner claims the security rule stopped a change the scanners
   **cleared**:

   ```
   status=failed   banner='⛔ BLOCKED — the change was stopped'
   security verdict was 'pass' with 0 blocking -> nothing was blocked
   ```

2. **An SRE `no_go` writes no ending row at all.** `graph.py:490-492` and
   `run_stage.py:573-576` both set `status="failed"` and return without a `_log`
   call, so the run renders `… INCOMPLETE — run stopped at sre without an ending`.
   **No test covers the `no_go` path**, which is why this survived.

The frozen `LogEvent.action` union has no `failed` member, and `state.py` is frozen
for renames — but a `Literal` may gain a member, since that is an addition. Prefer
that to reusing `blocked`, which is what causes defect 1.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_failed_run_rendering.py`:

```python
"""A `failed` run must not claim it was blocked, nor claim it never finished.

TWO MEASURED DEFECTS, both visible on a projector:

  1. run_stage._OUTCOME_ACTIONS maps failed -> "blocked", so a revision-cap run
     renders '⛔ BLOCKED — the change was stopped' while its security verdict was
     `pass` with 0 blocking. It claims the deterministic rule stopped a change the
     scanners cleared -- the pipeline's central claim, inverted.

  2. The SRE no_go path writes NO ending row, so the run renders
     '… INCOMPLETE — run stopped at sre without an ending'. No test covered that
     path, which is how it survived.
"""

import pytest

from agentorg import graph, log, timeline
from agentorg.state import LogEvent


def _banner(events: list[LogEvent]) -> str:
    return timeline._banner(events)


def _row(action: str, stage: str = "sre", actor: str = "system") -> LogEvent:
    return LogEvent(run_id="r", ticket_id="T-1", actor=actor, stage=stage,
                    action=action)


def test_the_action_vocabulary_admits_failed():
    """An ADDITION to the frozen contract's Literal, not a rename."""
    import typing
    from agentorg.state import LogEvent as LE
    actions = set(typing.get_args(typing.get_type_hints(LE)["action"]))
    assert "failed" in actions, (
        "LogEvent.action has no 'failed' member, so a failed run must borrow "
        "another action -- and borrowing 'blocked' is what makes a revision-cap "
        "run claim the security rule stopped it"
    )


def test_a_failed_run_gets_its_own_banner():
    banner = _banner([_row("opened", "plan"), _row("failed")])
    assert "FAILED" in banner.upper(), f"banner is {banner!r}"
    assert "BLOCKED" not in banner.upper(), (
        f"a failed run's banner says BLOCKED: {banner!r}. That claims the "
        f"deterministic security rule stopped it, which is a different and much "
        f"stronger claim than 'nobody approved it'."
    )
    assert "INCOMPLETE" not in banner.upper(), (
        f"a failed run reads as INCOMPLETE: {banner!r}; it did finish, with a "
        f"negative outcome"
    )


def test_a_real_block_still_says_blocked():
    """The complement. Without it, the fix above could rename every ending."""
    banner = _banner([_row("opened", "plan"), _row("blocked", "security")])
    assert "BLOCKED" in banner.upper(), f"banner is {banner!r}"


def test_the_sre_no_go_path_writes_an_ending_row(monkeypatch):
    """The path no test covered.

    Asserts on the LOG, because the timeline reads the log and nothing else -- a
    status field the renderer never sees cannot produce a banner.
    """
    rows: list[LogEvent] = []
    monkeypatch.setattr(log, "append", lambda e: rows.append(e) or e)

    from agentorg.common import agent_client
    from agentorg.state import SREResult
    real = agent_client.call_agent

    def _no_go(role, state, **kwargs):
        if role == "sre":
            return SREResult(verdict="no_go", ci_status="failing")
        return real(role, state, **kwargs)

    monkeypatch.setattr(agent_client, "call_agent", _no_go)
    state = graph.run_pipeline("T-1", "Add a per-IP login rate limit.")

    assert state.status == "failed", f"status is {state.status!r}"
    assert rows, "no log rows at all; this test would check nothing"
    assert rows[-1].action == "failed", (
        f"the run's last logged action is {rows[-1].action!r}. An SRE no_go wrote "
        f"no ending row, so the timeline renders INCOMPLETE for a run that did "
        f"finish."
    )
    assert "FAILED" in _banner(rows).upper()


def test_the_revision_cap_path_writes_failed_not_blocked(monkeypatch):
    """The other half. A reviewer that never approves is not a security block."""
    rows: list[LogEvent] = []
    monkeypatch.setattr(log, "append", lambda e: rows.append(e) or e)

    from agentorg.common import agent_client, config
    from agentorg.state import ReviewResult
    monkeypatch.setattr(config, "MAX_REVISION_LOOPS", 1)
    real = agent_client.call_agent

    def _never_approves(role, state, **kwargs):
        if role == "reviewer":
            return ReviewResult(verdict="changes_requested",
                                must_fix=["do it differently"])
        return real(role, state, **kwargs)

    monkeypatch.setattr(agent_client, "call_agent", _never_approves)
    state = graph.run_pipeline("T-1", "Add a per-IP login rate limit.")

    assert state.status == "failed"
    banner = _banner(rows)
    assert "BLOCKED" not in banner.upper(), (
        f"a run the SCANNERS CLEARED renders as {banner!r}. The security verdict "
        f"was {state.security.verdict!r} with {len(state.security.blocking)} "
        f"blocking findings -- nothing was blocked."
    )


def test_both_pipeline_paths_agree_on_the_failed_ending():
    """graph.py and run_stage.py implement the same pipeline twice.

    CLAUDE.md records three mutations that survived because run_stage.py inherited
    graph.py's COMMENT about a hazard but not its TEST. This asserts the mapping
    itself rather than trusting two copies to stay aligned.
    """
    import scripts.run_stage as run_stage
    assert run_stage._OUTCOME_ACTIONS["failed"] == "failed", (
        f"run_stage maps failed -> "
        f"{run_stage._OUTCOME_ACTIONS['failed']!r}; mapping it to 'blocked' is "
        f"what makes a revision-cap run claim the security rule stopped it"
    )
    for status, action in run_stage._OUTCOME_ACTIONS.items():
        assert action in timeline._OUTCOME, (
            f"status {status!r} maps to action {action!r}, which timeline._OUTCOME "
            f"has no banner for -- the run would render INCOMPLETE"
        )
```

- [ ] **Step 2: Run them, watch them fail**

```bash
.venv-main/bin/python -m pytest -q tests/test_failed_run_rendering.py -v
```

Expected: `test_the_action_vocabulary_admits_failed` fails; the banner tests fail
with INCOMPLETE or BLOCKED; `test_the_sre_no_go_path_writes_an_ending_row` fails
because no ending row exists; `test_both_pipeline_paths_agree…` fails on
`'blocked' != 'failed'`. Paste the output.

- [ ] **Step 3: Add `failed` to the action union**

In `agentorg/state.py`:

```python
    action: Literal[
        "opened", "proposed", "reviewed", "blocked", "passed",
        "approved", "rejected", "overridden", "merged", "promoted",
        # ADDED 2026-08-22. An ADDITION to the union, not a rename -- state.py is
        # frozen against renames and removals, and a new member breaks nothing.
        #
        # It exists because `failed` had no action and both callers borrowed
        # `blocked`, so a run the SCANNERS CLEARED rendered '⛔ BLOCKED — the
        # change was stopped'. That inverts the pipeline's central claim on the one
        # surface a judge reads. A run nobody approved, and a run the deterministic
        # rule stopped, are different endings and now say so.
        "failed",
    ]
```

- [ ] **Step 4: Give it a banner and a glyph**

In `agentorg/timeline.py`, add to `_MARK`: `"failed": "✗"` — and to `_OUTCOME`:

```python
    # A run that ENDED without shipping and without a block. The revision cap
    # exhausted, or the SRE said no_go. Distinct from BLOCKED (the deterministic
    # rule stopped it) and from REJECTED (a human said no), because attributing
    # either to this run would name a cause that did not happen.
    "failed": ("FAILED", "✗", "the change did not ship"),
```

- [ ] **Step 5: Write the ending rows**

In `run_stage.py`, change `_OUTCOME_ACTIONS["failed"]` from `"blocked"` to
`"failed"`.

At the SRE `no_go` exit in **both** `agentorg/graph.py` and
`scripts/run_stage.py`, add the row that was missing:

```python
        # THE ENDING ROW. Missing before 2026-08-22: both paths set
        # status="failed" and returned, so `timeline._outcome` -- which reads the
        # LAST row's action and never sees RunState.status -- rendered
        # '… INCOMPLETE'. A run that finished with a negative outcome is not an
        # unfinished run, and no test covered this path.
        _log(state, "system", "sre", "failed", verdict=state.sre.verdict,
             summary="SRE returned no_go; not promoting")
```

And at the revision-cap exit, change the existing `action="blocked"` row to
`action="failed"` in both files. Keep its summary — it already says "scanners
passed, but the reviewer never approved", which is now consistent with the banner.

- [ ] **Step 6: Verify**

```bash
.venv-main/bin/python -m pytest -q tests/test_failed_run_rendering.py -v
.venv-main/bin/python -m pytest -q
.venv-main/bin/python -m ruff check agentorg scripts tests
```

Expected: 6 passed, full suite green. Existing timeline tests may assert the old
INCOMPLETE rendering — read each before changing it, and if one was asserting the
bug, say so in the commit.

- [ ] **Step 7: RED step — four mutations**

1. Revert `_OUTCOME_ACTIONS["failed"]` to `"blocked"`.
   Expected: `test_both_pipeline_paths_agree_on_the_failed_ending` fails, and
   `test_the_revision_cap_path_writes_failed_not_blocked` fails naming the banner.
2. Remove the `failed` entry from `timeline._OUTCOME`.
   Expected: `test_a_failed_run_gets_its_own_banner` fails on INCOMPLETE, and the
   cross-path test fails on "has no banner for".
3. Delete the new `_log` at `graph.py`'s no_go exit.
   Expected: `test_the_sre_no_go_path_writes_an_ending_row` fails.
4. Delete it from `run_stage.py` instead, leaving `graph.py`'s in place.
   Expected: this is the divergence CLAUDE.md warns about — if **no** test fails,
   add one that drives the `run_stage` no_go path directly, watch it fail, and keep
   it.

Paste each failure. Revert each.

- [ ] **Step 8: Commit**

```bash
git add agentorg/state.py agentorg/timeline.py agentorg/graph.py \
        scripts/run_stage.py tests/test_failed_run_rendering.py
git commit -m "fix(timeline): a failed run claimed it was blocked, or never finished

Two measured defects, both on the projector.

A revision-cap run rendered '⛔ BLOCKED — the change was stopped' because
_OUTCOME_ACTIONS mapped failed -> blocked, while its security verdict was `pass`
with 0 blocking. That inverts the pipeline's central claim: it says the
deterministic rule stopped a change the scanners cleared.

An SRE no_go wrote NO ending row -- both paths set status='failed' and returned --
so the run rendered '… INCOMPLETE'. timeline._outcome reads the last row's action
and never sees RunState.status. No test covered the no_go path, which is how it
survived.

`failed` is now a member of LogEvent.action (an ADDITION to the union, not a
rename) with its own banner. A run nobody approved and a run the rule stopped are
different endings."
```

---

## Task 9: Close the DynamoDB backend, the trigger's provenance, and the reviewer's cap

**Files:**
- Modify: `scripts/run_stage.py` (`_load` reads through `gates.load`)
- Modify: `.github/workflows/run-pipeline.yml` (a `trigger` input)
- Modify: `infra/Terraform/modules/ingress/main.tf` (send `trigger=issue`)
- Modify: `agentorg/common/config.py` (validate `SECURITY_BLOCK_THRESHOLD`)
- Create: `tests/test_state_backend_cloud.py`, `tests/test_trigger_provenance.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `run_stage._load` works on both backends; `RunState` records how the run
  started.

Three of the four documented limitations, plus the unvalidated threshold. Each is
small and independent; do them in order and commit separately.

### 9a — `_load` through `gates.load`

`gates.load` **already handles both backends correctly** — verified. `_load` calls
`gates._state_path`, which refuses on dynamodb by design. So this is a three-line
change, not the rewrite the docstring implies.

- [ ] **Step 1: Write the failing test** in `tests/test_state_backend_cloud.py`:

```python
"""Every cloud stage after `plan` must work on the dynamodb backend.

MEASURED before this fix:

    STATE_BACKEND=dynamodb python -c "from agentorg import gates; gates._state_path('x')"
    RuntimeError: there is no state FILE on the 'dynamodb' backend …

`run_stage._load` called `_state_path`, so every stage after plan raised. The fix
is small: `gates.load` already reads both backends correctly.
"""

import pytest

from agentorg import gates
from agentorg.common import config
from agentorg.state import RunState


def test_load_reads_through_gates_load_not_the_path_helper(monkeypatch):
    """Asserts the SEAM, so the fix cannot regress to path-building."""
    import scripts.run_stage as run_stage
    state = RunState(ticket_id="T-1", ticket_text="x")
    monkeypatch.setattr(gates, "load", lambda run_id: state)

    def _refuse(run_id):
        raise AssertionError(
            "_load called gates._state_path; it must read through gates.load, "
            "which is the only reader that works on both backends"
        )
    monkeypatch.setattr(gates, "_state_path", _refuse)

    assert run_stage._load(state.run_id) is state


def test_a_missing_run_is_still_a_named_systemexit(monkeypatch):
    """The existing behaviour must survive the change.

    A broken artifact handoff must not become a fresh run -- that would report
    success for work it invented.
    """
    import scripts.run_stage as run_stage

    def _absent(run_id):
        raise FileNotFoundError("no such run")
    monkeypatch.setattr(gates, "load", _absent)

    with pytest.raises(SystemExit, match="did not arrive"):
        run_stage._load("nonexistent-run")
```

- [ ] **Step 2: Run it, watch the first fail** with the AssertionError from
`_refuse`. Paste it.

- [ ] **Step 3: Implement.** Replace `_load`'s body:

```python
    # READ THROUGH gates.load, the only reader that works on both backends.
    # `gates._state_path` refuses on dynamodb by design, so calling it here made
    # every cloud stage after `plan` raise under STATE_BACKEND=dynamodb.
    try:
        return gates.load(run_id)
    except FileNotFoundError as exc:
        # Named loudly, and NOT softened into a fresh RunState: the likeliest cause
        # is a broken artifact handoff, and starting a new run here would report
        # success for work it invented, silently discarding everything approved.
        raise SystemExit(
            f"no saved state for run {run_id!r}: the previous stage's artifact did "
            f"not arrive. This job cannot start a new run -- that would silently "
            f"discard everything already approved. ({exc})"
        ) from exc
```

Replace the docstring's KNOWN DEBT paragraph with what is now true.

- [ ] **Step 4: RED step.** Revert to `gates._state_path` → the first test fails.
Then delete the `except FileNotFoundError` → the second fails. Paste both.

- [ ] **Step 5: Commit** with a message naming the measured `RuntimeError`.

### 9b — the trigger's provenance

- [ ] **Step 1: Add the input** to `run-pipeline.yml`:

```yaml
      trigger:
        # HOW THIS RUN STARTED. `event:` cannot answer it: EventBridge dispatches
        # through the same REST API `gh workflow run` uses, so both read
        # `workflow_dispatch` and NO field distinguishes them.
        #
        # The ingress transformer sends "issue"; a hand dispatch leaves the
        # default. So the value is trustworthy in the direction that matters -- a
        # run claiming `issue` was sent by the rule.
        description: "How this run started (the ingress sends 'issue')"
        required: false
        type: string
        default: manual
```

- [ ] **Step 2: Send it** from `infra/Terraform/modules/ingress/main.tf`'s
`input_template`, adding `"trigger": "issue"` beside `"poisoned": "false"`. Quoted,
like every other value — the dispatch API rejects real JSON booleans and treats
every input as a string.

- [ ] **Step 3: Record it.** Pass `--trigger "$TRIGGER"` to `run_stage.py plan`, add
the argparse argument, and add `RunState.trigger: str = "manual"` as an optional
field. Render it in the plan comment beside the model provenance.

- [ ] **Step 4: Test it** in `tests/test_trigger_provenance.py`: the workflow
declares the input with default `manual`; the Terraform template sends `"issue"`;
the value reaches `RunState.trigger`; and — the anti-vacuity assertion — the
Terraform template's value and the workflow's default are **different**, since
identical values would make the field prove nothing.

- [ ] **Step 5: RED step.** Delete `"trigger": "issue"` from the template → the
Terraform test fails. Change the workflow default to `issue` → the
different-values test fails. Paste both.

### 9c — validate `SECURITY_BLOCK_THRESHOLD` at import

`compute_security_verdict([], threshold="HIGH")` raises `KeyError: 'HIGH'` **mid-run
inside the security agent**. Verified. Every other malformed knob in `config.py`
fails at import; this one does not.

- [ ] **Step 1: Test** that an unknown threshold raises at import with a message
naming the legal values, following `STATE_BACKEND`'s existing pattern.

- [ ] **Step 2: Implement** in `config.py`, immediately after the assignment:

```python
# Validated at import, like STATE_BACKEND and unlike its own past self. Unvalidated,
# a typo reached compute_security_verdict and raised `KeyError: 'HIGH'` from inside
# the security agent, mid-run -- so the pipeline died at the one stage whose whole
# purpose is to produce a verdict, and the traceback named a dict lookup rather than
# a misconfigured knob.
if SECURITY_BLOCK_THRESHOLD not in SEVERITY_ORDER:
    raise ValueError(
        f"SECURITY_BLOCK_THRESHOLD={SECURITY_BLOCK_THRESHOLD!r} is not a severity; "
        f"expected one of {', '.join(SEVERITY_ORDER)}. Refused at import rather "
        f"than raising KeyError inside the security agent halfway through a run."
    )
```

This needs `SEVERITY_ORDER` from `state.py`. If importing it into `config` creates a
cycle, define the tuple of legal severities in `config` and add a test asserting the
two agree — two declarations of one fact, with a tripwire, rather than a cycle.

- [ ] **Step 3: RED step.** Delete the check → the test fails. Paste it.

### 9d — the reviewer's cap, documented rather than changed

The reviewer's verdict is advisory: a reviewer that never approves takes the run to
`MAX_REVISION_LOOPS` and ends `failed`, even though the scanners cleared the diff.

**This is correct and stays.** A change nobody approved should not ship, and the
alternative — promoting on a scanner pass alone — would make the reviewer
decorative. What was wrong is that the run then **claimed to have been blocked**,
which Task 8 fixes.

- [ ] **Step 1:** Confirm Task 8's banner change makes the outcome legible: a
capped run now reads `✗ FAILED — the change did not ship` with the summary
"scanners passed, but the reviewer never approved after N revisions".

- [ ] **Step 2:** Update `README.md`'s *Status and limitations* entry to say the
cap is deliberate and that the run now says so, rather than listing it as a defect.

- [ ] **Step 3: Commit** all of 9a–9d together if each was verified separately.

---

## Task 10: Re-verify the whole thing end to end

**Files:** none — this task produces evidence, not code.

- [ ] **Step 1: The four local gates**

```bash
.venv-main/bin/python -m pytest -q
.venv-main/bin/python -m ruff check agentorg scripts tests
actionlint .github/workflows/*.yml
cd infra/Terraform && terraform fmt -check -recursive
```

All must pass. Paste each.

- [ ] **Step 2: Preflight against the live account**

```bash
.venv-main/bin/python scripts/preflight.py; echo "exit=$?"
```

Expected exit 0, with check 1 now `allowed`. Paste the output.

- [ ] **Step 3: The poisoned half, on the deployed pipeline**

```bash
gh workflow run run-pipeline.yml --ref main \
  -f ticket_id=<a real issue number> \
  -f ticket_text="Add a per-IP login rate limit." -f poisoned=true
```

Approve gate1. Expected: `develop` exits 3; the PR carries a security comment with
`provenance: scanners` at `app/auth.py:3` and `:4`; **`_source: model`** on the plan
comment; `status=blocked` survives; both later recorders `skipped`; the PR is **not
merged**.

- [ ] **Step 4: The clean half**

Same, `poisoned=false`. Approve all three gates. Expected: seven jobs green; the
plan comment says `_source: model` and the plan text **differs from
`fixtures/plan_result.json`** — that is the proof Task 1 worked; the SRE comment
reports a real `ci_status`; and the PR is **merged**, with `⇄ MERGED` before
`★ PROMOTED` on the timeline.

- [ ] **Step 5: The automatic trigger**

Open an issue on `auth-service`. Expected: a run appears with `trigger: issue`
recorded, and it merges after three clicks.

- [ ] **Step 6: Update the docs with what was measured**

`CLAUDE.md`'s verified-runs section and `README.md`'s status section, with pasted
output. Delete the limitations that are now closed; keep the reviewer-cap entry
reworded per 9d. Add the IAM finding to CLAUDE.md's traps — it is the most valuable
thing this plan discovered.

- [ ] **Step 7: Commit**

```bash
git add CLAUDE.md README.md docs/plan/reem/demo_script.md
git commit -m "docs: the pre-demo fixes, verified end to end"
git push
```

---

## Self-Review

**Spec coverage.** The four documented limitations: dynamodb → 9a; `event:`
indistinguishable → 9b; reviewer cap → 9d + Task 8; `approve_server` auth → left
as-is deliberately, since it is kept for a future frontend and its lack of auth is
now documented in CLAUDE.md's Secrets section rather than being a silent hazard.
The user's two additional requirements: all agents real → Tasks 1 and 6; SRE merges
→ Task 7, assigned to `promote` rather than the SRE agent for the reason in that
task. Audit findings folded in: IAM denial → 1; vacuous smoke test → 4; diff prefix
→ 4; `failed` rendering → 8; unvalidated threshold → 9c.

**Not covered, deliberately, with reasons.** `graph.run_pipeline` not setting
`state.poisoned` (audit #6) — a one-line fix, but it changes a field the local path
persists and I would rather not touch the local fallback three days out; it is
recorded in CLAUDE.md instead. Scanner reports written inside the scanned tree
(audit #5) — masks a fault, does not defeat a healthy scan; a real fix moves the
report outside the tree and touches all three wrappers. The suite writing into
`runs/` (audit #8) — a conftest redirect of `log._LOG_DIR` and `gates._STATE_DIR`,
worth doing but it touches the guard layer every test depends on. The stale line
citations (audit #10) and `tests/README.md` — documentation only. Two further audits
were still running when this plan was written; fold their findings in as Tasks 11+
rather than expanding these.

**Placeholder scan.** No TBDs. Every code step carries the actual code. Two places
say "read the real value first" — the fixture's `notes` literal in Task 4 and the
`auth-service` CI status in Task 5 — and both are instructions to measure rather
than gaps, because a literal copied on trust is exactly what Task 4 is fixing.

**Type consistency.** `ci_status` returns the three members of `SREResult.ci_status`,
read from the contract in Task 5's first test rather than restated. `merge_pr`
returns `str` like `post_comment`. `RunState.model_provenance` and `RunState.trigger`
are both optional `str` additions. `llm.last_source()` returns `str | None`, matching
its use in `graph`'s `finally` where `or ""` handles the None.

**Ordering.** Task 1 is first because everything else is unobservable while the model
is denied. Task 4 precedes Task 5 because a scan over an empty tree would make a
real CI check meaningless. Task 6 precedes Task 7 because the merge depends on a
verdict that means something. Task 8 is independent and could move earlier if a
demo rehearsal exposes the banner first.




