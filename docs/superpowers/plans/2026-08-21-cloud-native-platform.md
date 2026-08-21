# Cloud-Native Platform Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Agent Org runs on AWS, triggered by creating a GitHub issue, with every agent executing on Bedrock AgentCore and every agent's output posted back to the target repo.

**Architecture:** A GitHub App subscribed to Issues POSTs to a Lambda Function URL, which verifies the HMAC and puts the event on EventBridge. A rule dispatches `run-pipeline.yml` in *this* repo, which runs the pipeline with `REMOTE_AGENTS=true` so each agent call becomes an `invoke_agent_runtime` against one of the five deployed runtimes. Human gates are GitHub Environments. Nothing is committed to the target repo.

**Tech Stack:** Python 3.12, Terraform 1.15.8, AWS (Bedrock AgentCore, Lambda, EventBridge, Secrets Manager, ECR), GitHub Actions.

## Global Constraints

- `agentorg/state.py` is FROZEN. You may ADD optional fields; never rename or remove one.
- Baseline is **544 passed, 3 skipped**. It must stay green. `ruff check agentorg scripts tests` must exit 0. No `[tool.ruff]` section, no `# noqa`, no per-file ignores.
- `actionlint .github/workflows/*.yml` must exit 0. It runs shellcheck over every `run:` block.
- All infrastructure lives in `infra/Terraform/`. Nothing is created by hand in the console.
- Zero static AWS keys. Every AWS step assumes `arn:aws:iam::339712964409:role/github-actions-role` via OIDC.
- Account `339712964409`, region `us-east-1`.
- Never read, print, log or commit `.env` — it holds a live GitHub token. FAKE credential literals only in tests. `AKIAIOSFODNN7EXAMPLE` is AWS's published example and is safe.
- **Nothing may be committed to the target repo** (`auth-service`). No workflow file, no config.
- Do not weaken the four autouse guards in `tests/conftest.py`.
- A judged live demo is **Tue Aug 25**. Anything that looks like a crash on a projector outranks polish.

## The rules this repo learned the hard way — enforce them

1. **Every test task carries a mandatory RED step**: name the exact mutation, make it, watch the exact named test fail, revert. Nineteen assertions in this repo have turned out to pin nothing. A test that cannot fail is worse than no test.
2. **When you change a mechanism, tests referencing the old one do not fail — they stop testing.** Three tests went silently vacuous this week because a matcher keyed on a string that no longer existed. Any test whose matcher can match nothing must assert that it matched.
3. **Numbers in prose must come from a command whose output you paste**, not from recall.
4. **A check that cannot distinguish "did not run" from "passed" is the defect this whole project exists to prevent.** Same for "denied" versus "not ready yet".

---

## Task 1: Scanners in the container image

**Files:**
- Modify: `agentorg/agents/Dockerfile` (already done — verify and test)
- Modify: `.github/workflows/deploy.yml`
- Test: `tests/test_deploy_workflow.py` (append)

**Why first:** without this the deployed security agent returns a **fixture verdict**, so "the security gate ran in the cloud" is false. Measured: the image had no scanner binaries at all.

- [ ] **Step 1: Verify the Dockerfile layer is present**

The scanner install layer was added before `WORKDIR /app`, pinned to CI's versions (`ci.yml:124-126`): gitleaks 8.21.2, trivy 0.74.0, semgrep 1.172.0. Confirm it ends with the version-check tail:

```
 && gitleaks version && trivy --version && semgrep --version
```

That tail is the guarantee: a binary that downloads but cannot execute is a BROKEN scanner, which blocks *every* run including the clean one. It must fail the build, not ship.

- [ ] **Step 2: Set `SCANNERS_REQUIRED=true` on the security runtime ONLY**

In `deploy.yml`'s runtime create/update loop, the `--environment-variables` argument currently passes only `AGENT_ROLE`. Add the knob for one agent:

```bash
env_vars="AGENT_ROLE=${agent}"
if [ "$agent" = "security" ]; then
  # Only the security runtime. Set on an agent whose image lacks the
  # binaries and even the CLEAN run blocks with blocking=3 --
  # see agentorg/common/config.py:64-100.
  env_vars="${env_vars},SCANNERS_REQUIRED=true"
fi
```

- [ ] **Step 3: Write the failing test**

```python
def test_only_the_security_runtime_demands_its_scanners():
    """SCANNERS_REQUIRED on an agent without binaries blocks the CLEAN run too.

    config.py:64-100 measures it: the knob promotes ABSENT to FAULT, so a runtime
    that cannot find gitleaks returns three *-scanner-error findings and
    blocking=3. Setting it on all five would take the demo's first half down.
    """
    scripts = "\n".join(_all_run_scripts(DEPLOY))
    assert "SCANNERS_REQUIRED=true" in scripts, (
        "no runtime demands its scanners; the cloud verdict would be a fixture"
    )
    lines = [l for l in scripts.splitlines() if "SCANNERS_REQUIRED" in l and "=" in l]
    assert lines, "SCANNERS_REQUIRED appears only in prose, not in an assignment"
    guarded = [l for l in scripts.splitlines() if 'agent" = "security"' in l]
    assert guarded, (
        "SCANNERS_REQUIRED is not guarded to the security agent; all five would "
        "get it and the clean run would block with blocking=3"
    )
```

- [ ] **Step 4: Run it, watch it fail, implement, watch it pass**

```bash
pytest -q tests/test_deploy_workflow.py -k scanners
```

- [ ] **Step 5: RED step — prove the guard is pinned**

Remove the `if [ "$agent" = "security" ]` guard so all five get the knob. Expected: the new test fails on the third assertion. Revert.

- [ ] **Step 6: Deploy and verify by LINE NUMBERS, which is the only honest check**

```bash
gh workflow run deploy --ref main
# then, once green:
aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id <security-id> \
  --query 'environmentVariables' --output json
```

Expected: `SCANNERS_REQUIRED` present on security, absent on the other four.

Then invoke the security runtime with a poisoned `RunState` and read the finding lines. **Real scanners report `app/auth.py:3` and `:4`. The fixture reports `:4` and `:5`.** That pair is the only field distinguishing the two paths — reuse `tests/provenance.py`'s `REAL_SCANNER_LINES` / `FIXTURE_LINES` rather than hardcoding.

- [ ] **Step 7: Commit**

```bash
git add agentorg/agents/Dockerfile .github/workflows/deploy.yml tests/test_deploy_workflow.py
git commit -m "feat(security): put the scanners in the image so the cloud verdict is real"
```

---

## Task 2: The remote seam

**Files:**
- Create: `agentorg/common/agent_client.py`
- Modify: `agentorg/common/config.py`, `agentorg/graph.py`
- Test: `tests/test_agent_client.py`

**Interfaces produced** — Task 3 depends on this exact signature:

```python
def call_agent(role: str, state: RunState, **kwargs) -> BaseModel: ...
```

`role` is one of `planner`, `developer`, `reviewer`, `security`, `sre`.

- [ ] **Step 1: Add the config knob**

```python
# REMOTE_AGENTS routes every agent call to its deployed AgentCore runtime instead
# of calling the in-process function. Default false: the local path stays the
# tested default and the Tuesday fallback.
REMOTE_AGENTS = os.environ.get("REMOTE_AGENTS", "false").lower() == "true"
```

- [ ] **Step 2: Write the failing test first**

```python
def test_local_mode_calls_the_in_process_function(monkeypatch):
    """REMOTE_AGENTS=false must behave exactly as before this module existed."""
    monkeypatch.setattr(config, "REMOTE_AGENTS", False)
    called = []
    monkeypatch.setattr(planner, "run", lambda s: called.append(s) or PLAN_FIXTURE)
    result = agent_client.call_agent("planner", _state())
    assert called, "local mode did not call planner.run"
    assert result == PLAN_FIXTURE


def test_an_unknown_role_raises_rather_than_guessing():
    with pytest.raises(ValueError, match="bandit"):
        agent_client.call_agent("bandit", _state())
```

- [ ] **Step 3: Implement `call_agent`**

Local branch calls `AGENTS[role].run(state, **kwargs)`. Remote branch:

```python
# The payload MUST be the raw JSON bytes. boto3 takes bytes here; the CLI takes
# base64, which is a different interface for the same API -- do not copy the
# CLI's encoding into boto3 code.
resp = client.invoke_agent_runtime(
    agentRuntimeArn=arn,
    qualifier="DEFAULT",            # required; without it the call is
                                    # ResourceNotFoundException even against a
                                    # READY runtime with a READY endpoint
    payload=state.model_dump_json().encode("utf-8"),
    contentType="application/json",
)
```

Resolve the ARN by name. **`--output text` on the CLI appends a literal `None` line** — that cost two failed deploy runs. With boto3 you read the field directly, so this trap does not apply; do not shell out.

Validate the response into the right result type per role, and raise on a 500 rather than returning an empty result — a runtime answering 200 with an empty body is the reassuring non-answer this project keeps eliminating.

- [ ] **Step 4: Handle `developer`'s second argument**

`developer.run(state, poisoned=False)` takes a parameter `server.py:164` cannot pass. Add `poisoned` as an optional field on `RunState` (additions are allowed; renames are not) and have `developer.run` read it when the kwarg is absent.

- [ ] **Step 5: Replace the five call sites in `graph.py`**

`graph.py:166,178,181,201,261` become `call_agent("planner", state)` etc. Nothing else in `graph.py` changes.

- [ ] **Step 6: RED steps**

- Force `REMOTE_AGENTS=True` with a stubbed client and assert `qualifier="DEFAULT"` is passed. Remove the qualifier → the test must fail.
- Make the stub return `{"result": {}}` → the "raise on empty" test must fail if you delete that check.

- [ ] **Step 7: Full suite, then commit**

`pytest -q` must still report **544 passed + your new tests**, because `REMOTE_AGENTS` defaults false.

---

## Task 3: `run-pipeline.yml` with Environment gates

**Files:**
- Create: `.github/workflows/run-pipeline.yml`
- Test: `tests/test_run_pipeline_workflow.py`

**Consumes:** Task 2's `call_agent`, and `REMOTE_AGENTS=true`.

**The structural consequence:** a job pauses at an Environment, so the pipeline cannot be one `run_pipeline()` call in one job. Split at the gate boundaries:

```
plan → [gate1] → develop+review → [gate2] → sre → [gate3] → promote
```

`RunState` is handed between jobs. `gates.save`/`gates.resume` (`gates.py:50,78`) already exist for exactly this. For Tuesday, pass it as an Actions artifact; Task 4 replaces that with DynamoDB.

- [ ] **Step 1: Create the three Environments on the repo**

`gate1`, `gate2`, `gate3`, each with a required reviewer. This is a repo setting, not a file — record in the task report that it was done, since no test can assert it.

- [ ] **Step 2: The workflow**

`on: workflow_dispatch` with inputs `ticket_id`, `ticket_text`, `poisoned` (boolean), `auto_approve` (boolean, default false). Every AWS job needs `id-token: write` and assumes the OIDC role. `REMOTE_AGENTS=true`, `DEMO_REPO` from a repo variable, `GITHUB_TOKEN` from secrets so `_use_local()` (`github_ops.py:56`) takes the **online** branch and a real PR appears.

Each gate job declares `environment: gateN` and does nothing but exist — its purpose is to pause.

- [ ] **Step 3: Tests that pin the blast radius**

Model them on `tests/test_deploy_workflow.py`, which already parses workflows as YAML rather than grepping prose. Assert: no static AWS key anywhere; every gate job declares an `environment`; the three gates are `gate1`/`gate2`/`gate3`; `REMOTE_AGENTS=true` is set; the state artifact is uploaded by one job and downloaded by the next.

- [ ] **Step 4: RED step**

Delete `environment: gate2` from its job. The "every gate job declares an environment" test must fail. Revert.

- [ ] **Step 5: Run it end to end**

`gh workflow run run-pipeline.yml -f ticket_id=DEMO-1 -f ticket_text="Add a per-IP login rate limit." -f poisoned=true`

Expected: pauses at gate1 showing "Review pending"; after three clicks, a PR exists on the target repo and the poisoned run shows `blocked`, `blocking=2`, at lines **3 and 4**.

---

## Task 4: Per-agent output posted to the target repo

**Files:**
- Modify: `agentorg/github_ops.py`, `agentorg/graph.py`
- Test: `tests/test_agent_comments.py`

**Consumes:** `post_comment(state, body, finding=None)` (`github_ops.py:246`) — already exists, already never raises, already used for the security explanation at `graph.py:217`. Extend; do not write a second one.

- [ ] **Step 1: Failing test — one comment per agent**

```python
def test_every_agent_stage_posts_its_output(monkeypatch):
    """The PR is the timeline. A stage that runs silently is invisible to a judge."""
    posted = []
    monkeypatch.setattr(github_ops, "post_comment",
                        lambda state, body, finding=None: posted.append(body) or "local://x")
    run_pipeline("T-1", "Add a per-IP login rate limit.")
    stages = {"plan", "develop", "review", "security", "sre"}
    for stage in stages:
        assert any(stage in b.lower() for b in posted), f"{stage} posted nothing"
```

- [ ] **Step 2: Implement**

Post after each stage completes. Plan and gate1 comment on the **issue** (the PR does not exist until `open_pr` at `graph.py:196`); everything from develop onward on the **PR**. The reviewer loop appends rather than replaces — three revisions is part of the story.

- [ ] **Step 3: RED step**

Delete the planner's post. The test must fail naming `plan`. Revert.

---

## Task 5: The GitHub App → Lambda → EventBridge ingress

**Files:**
- Create: `infra/Terraform/modules/ingress/{main.tf,variables.tf,outputs.tf}`
- Create: `infra/ingress/handler.py`
- Modify: `infra/Terraform/environments/shared/main.tf`
- Test: `tests/test_ingress_handler.py`

**Why a Lambda and not a native EventBridge webhook:** checked, not assumed. The full `aws events` command list on CLI 2.36.24 has no webhook API — `create-api-destination` and `create-connection` are *outbound*, and `create-partner-event-source` needs an onboarded SaaS partner. AWS's partner list does not include GitHub.

- [ ] **Step 1: The handler, with the three traps handled**

```python
def handler(event, context):
    body = event["body"]          # RAW string. Any JSON round-trip changes the
                                  # HMAC and every request 401s.
    sig = event["headers"].get("x-hub-signature-256", "")
    expected = "sha256=" + hmac.new(_secret().encode(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):   # compare_digest, not ==
        return {"statusCode": 401}
    ...
```

- [ ] **Step 2: Tests for the handler**

A valid signature → 202 and one `PutEvents`. A wrong signature → 401 and **zero** `PutEvents`. A missing header → 401. Use a FAKE secret literal.

- [ ] **Step 3: RED step**

Replace `hmac.compare_digest(sig, expected)` with `True`. The wrong-signature test must fail. Revert.

- [ ] **Step 4: Terraform**

Lambda + Function URL (`auth-type NONE` — the HMAC is the only access control, so it runs first), Secrets Manager secret for the webhook secret, EventBridge bus and a rule matching issue-opened, and **reserved concurrency** so a flood cannot drive spend. IAM: `secretsmanager:GetSecretValue` on that one secret ARN and `events:PutEvents` on that one bus. Nothing wider.

- [ ] **Step 5: `terraform fmt -check -recursive` and `validate`, then plan**

Apply through `terraform.yml`, not locally — that workflow is already green end to end.

- [ ] **Step 6: Install the GitHub App and link the repo**

Subscribe to Issues, point the webhook at the Function URL, install on the target repo. Then `DEMO_REPO` is the only thing to change to link a different repo.

- [ ] **Step 7: End-to-end**

Create an issue on the target repo. Expected: a `run-pipeline` run appears within seconds, pauses at gate1, and a PR follows. Add the `poisoned` label to a second issue and confirm it blocks.

---

## Task 6: Run state in DynamoDB

**Files:**
- Create: `infra/Terraform/modules/state/`
- Modify: `agentorg/log.py`, `agentorg/gates.py`, `agentorg/common/config.py`
- Test: `tests/test_state_backend.py`

**Not on the demo's critical path** — with GitHub as the surface, the timeline a judge reads is the PR. Do it after 1-5 are green.

`agentorg/log.py` is pre-designed for this: lines 29 and 40 carry `# <-- swap this line for a DynamoDB PutItem/Query` markers and the docstring promises `append()`/`read()` signatures stay identical.

- [ ] **Step 1:** Table `theagentorg-runs`, PK `run_id`, SK `ts#event_id`. Grant `dynamodb:PutItem/Query/GetItem/UpdateItem` on that table to the runtime role and the CI role.
- [ ] **Step 2:** `config.STATE_BACKEND` (`local` | `dynamodb`), default `local`.
- [ ] **Step 3:** Swap the two marked lines behind that branch. `gates.save`/`resume` too.
- [ ] **Step 4:** `gates.pause()` returns a `pathlib.Path` (`gates.py:55`) that `graph.py:109` prints — that return type must become opaque.
- [ ] **Step 5:** `approve_server._awaiting()` discovers runs by `glob` (`approve_server.py:176`), which is **also** its path-traversal defence (`:61-65`): a `run_id` is trusted only because it came from the glob. Replacing the glob removes that guarantee — add explicit `run_id` validation.
- [ ] **Step 6: RED step** — point `STATE_BACKEND=dynamodb` at a stub and assert the event count round-trips. Delete the write → the count test must fail.

---

## Also before Tuesday

- Close the five stale PRs on the target repo (#5–#9) so judges see a clean slate.
- Delete two orphans from the abandoned CLI attempt, neither Terraform-managed: AgentCore Memory `theagentorg_planner_mem-FM9Dgv31gr` and CodeBuild project `bedrock-agentcore-theagentorg_planner-builder`.
- Re-time `docs/plan/reem/demo_script.md` around three gate pauses.

## Self-Review

**Spec coverage:** cloud compute → Tasks 1-2; issue-triggered → Task 5; agent output in the repo → Task 4; human gates → Task 3; cloud state → Task 6.

**Type consistency:** `call_agent(role, state, **kwargs)` is defined in Task 2 and consumed in Task 3. `post_comment(state, body, finding=None)` is the existing signature and is not changed.

**Known interaction:** Task 1's `SCANNERS_REQUIRED=true` on the security runtime is what makes Task 3's line-number verification meaningful. Doing 3 before 1 would verify a fixture.
