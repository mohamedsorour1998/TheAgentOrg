# The Agent Org

*by **RosettaTeam** — Sorour · Mariam · Habiba · Reem · Aya*

A multi-agent CI/CD pipeline. A ticket flows through five role agents —
**planner → developer → reviewer → security → SRE** — with three human approval
gates. Agents run on **AWS Bedrock AgentCore** with **Strands**; infrastructure
is **Terraform**; the orchestration is **GitHub Actions**.

## The claim, stated precisely

**A poisoned ticket is blocked on every run, and the block comes from
`compute_security_verdict()` — pure Python in `agentorg/state.py`, never a
model.**

The AI writes the code and explains the risk. Deterministic code decides whether
it ships. That division is the strongest thing about the design: an LLM's
explanation is prose on a pull request, and an LLM's opinion of a diff is
advisory, but the verdict is a threshold comparison over a list of findings and
it returns the same answer every time.

The security agent fills `SecurityResult.explanation` with the model's words. It
does **not** set `SecurityResult.verdict`.

### Two tickets, one feature

`tickets/clean.md` and `tickets/poisoned.md` are the **same feature request** —
*add a per-IP login rate limit*. They differ in exactly one way: the poisoned
ticket's reference implementation hardcodes AWS credentials, so the developer
agent's diff carries them.

```
tickets/poisoned.md:17:+AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"
```

(`AKIAIOSFODNN7EXAMPLE` is AWS's own published documentation placeholder —
nothing sensitive.)

Showing **both** is what proves the pipeline ships features rather than merely
refusing them. The clean ticket is planned, developed, reviewed, scanned,
SRE-checked and **promoted**; the poisoned one is **blocked** at the security
stage and never reaches the deploy gates.

The developer↔reviewer loop is real, not decorative. It is capped at
`MAX_REVISION_LOOPS = 3`, and runs recorded under `runs/` show the reviewer
requesting changes and the developer producing a fresh diff for each pass. Each
pass posts its own pair of comments, carrying that pass's own diff — so a run
that argues with itself three times renders as three attempts, not one repeated
diff. (With the shipped fixture reviewer, which approves on the first pass, both
demo runs take one pass; the loop is exercised by tests and by model-path runs.)

## Architecture

```
issue ─▶ Lambda (HMAC) ─▶ EventBridge ─▶ run-pipeline.yml ─▶ invoke_agent_runtime ×5
                                              │
   plan ─▶ [gate1] ─▶ develop+review+PR+security ─▶ [gate2] ─▶ sre ─▶ [gate3] ─▶ promote
```

**The five agents** each expose `run(state) -> Result`. Locally that is a Python
call; in the cloud it is an HTTP POST to a container. Both go through one seam,
`agentorg/common/agent_client.call_agent`, selected by `REMOTE_AGENTS`.

**The three gates are GitHub Environments**, not `if:` conditions. An
Environment's required reviewer is a repository *setting*, so no edit to the
workflow and no workflow input can approve a gate on a human's behalf. A gate
implemented as an `if:` would be skippable, and a skipped gate is
indistinguishable downstream from an approved one.

That has a structural consequence: an Environment pauses a **job**, and a job
cannot pause in its middle. So the pipeline cannot be one `run_pipeline()` call —
it is cut at the gate boundaries, seven jobs, with the `RunState` handed along as
an Actions artifact. Each job runs `scripts/run_stage.py <stage>`.

A refused gate **skips** its job rather than running it with a verdict, so three
separate `gate*-rejected` recorder jobs exist to write the refusal down. Without
them a refused run and an in-flight run are byte-identical on disk.

**The block rule is evaluated before the reviewer's verdict is treated as
terminal**, and that order is load-bearing. On the poisoned ticket a competent
reviewer objects to the hardcoded key and the developer re-inserts it on every
revision, so the revision cap would reliably exhaust and the run would end
`failed` without the scanners ever running — quietly downgrading "the poisoned
ticket blocks every time" into "it fails at review".

**Every stage posts its output** to the target repo through one function. Plan
and gate1 land on the issue (no PR exists yet); everything from the developer
onward lands on the PR. The PR is the timeline a judge reads, so a stage that ran
silently is a stage that did not run as far as anyone watching can tell.

## Quick start

```bash
pip install -e ".[dev]"

python make_fixtures.py                     # regenerate + validate all fixtures
pytest -q                                   # 795 passed, 3 skipped

python -m agentorg.graph                    # clean ticket   -> promoted
python -m agentorg.graph --poisoned         # poisoned ticket -> blocked
python -m agentorg.timeline <run_id>        # render one run as a timeline
```

Verified today on merged `main`, with the three scanner binaries on PATH:

```
$ python scripts/scan_gate.py
SCAN OK

$ SCANNERS_REQUIRED=true python -m agentorg.graph
status=promoted
security verdict=pass, blocking=0

$ SCANNERS_REQUIRED=true python -m agentorg.graph --poisoned
status=blocked
security verdict=block, blocking=2
```

Everything also runs **on fixtures** with no AWS, no GitHub and no scanners
installed, so the whole path works before any single lane's real code exists.
`SCANNERS_REQUIRED` defaults false precisely so a missing binary stays a
development affordance.

## The verification story

The pipeline's own signature defect — a check that reports green because it never
ran — is the thing this repo spends the most effort refusing.

**The one field that proves the scanners ran.** A fixture block and a real
gitleaks block produce the *same* verdict, the same `blocking=2`, the same rule
names, the same file, the same tool and the same severity. Real scanners report
`app/auth.py:3` and `:4`; the fixture reports `:4` and `:5`. **The line-number
pair is the only field distinguishing the two paths**, so `blocking=2` proves
nothing on its own. Because inferring it after the fact is impossible, it is
**recorded at the call site** as `scan_provenance`, and the timeline renders it in
words:

```
⛔ security security  blocked [block] — 2 blocking
           ↳ scan: real scanners ran
```

**Every test change carries a mandatory RED step**: name the mutation, apply it,
watch the named test fail, revert. Nineteen-plus assertions in this repo turned
out to pin nothing. The recurring lesson, found seven times across four layers:
*a test double, a helper, an inference, or a measurement that cannot express the
failing case produces confidence that cannot be falsified — and reading it never
reveals that.*

**Four autouse guards** in `tests/conftest.py` keep the suite off the live model,
off the GitHub API, out of the working tree and off the terminal. Each raises
through `pytest.fail`, whose `Failed` derives from `BaseException` — so the blind
`except Exception` in the code under test cannot swallow it.

**Numbers must be reproducible or quoted as a range.** The suite's wall time on
the same 793 tests measured 102.83s, 116.88s and 149.68s on one day; the spread
is machine load. "Measured" is a property of a number plus its conditions.

## Status

### Live in AWS (account `339712964409`, `us-east-1`)

| Resource | State |
|---|---|
| Five AgentCore runtimes `theagentorg_{planner,developer,reviewer,security,sre}` | All READY, version 9 |
| `SCANNERS_REQUIRED=true` | Set on the **security runtime only** |
| Five ECR repos | One arm64 image each, tagged with the commit SHA |
| Lambda Function URL, EventBridge bus, Secrets Manager secret, DynamoDB table `theagentorg-runs` | Created |
| `terraform apply` | `Apply complete! Resources: 0 added, 0 changed, 0 destroyed` |

Verified today by invoking the deployed `theagentorg_security` runtime with a
poisoned `RunState`: `verdict: block`, `blocking: 2`, lines `[3, 4]`,
`provenance: scanners`. The deployed container genuinely scans.

Gates: `pytest -q` → 795 passed, 3 skipped · `ruff check agentorg scripts tests`
→ exit 0 · `actionlint .github/workflows/*.yml` → exit 0 ·
`terraform fmt -check -recursive` and `validate` → clean.

DORA batch, this run: the Agent Org blocks the poisoned change **10/10** and
ships **0/10** bad changes; the no-checks baseline blocks **0/10** and ships
**10/10**. Footer: `provenance: real_scanners`.

### BLOCKED-ON-HUMAN

1. **The GitHub App must be created and installed** on the target repo, and the
   webhook secret value minted into
   `theagentorg-shared-github-webhook-secret`. Until then the Lambda returns
   **500 "webhook secret unavailable"** — deliberately not 401. From
   `infra/ingress/handler.py`:

   > 500, never 401. "We cannot read our own secret" is not "your signature is
   > wrong", and conflating them sends the next person to rotate a secret that
   > was always correct.

   The secret resource exists; it has **no version**, so `GetSecretValue`
   currently fails with `ResourceNotFoundException`.

2. **The three GitHub Environments (`gate1`, `gate2`, `gate3`) need required
   reviewers.** Without a reviewer an Environment does **not** pause — it runs.
   That is the highest-risk silent failure in the design, and no test in this
   repository can assert a repository setting.

3. **The EventBridge rule has no target yet.** The rule
   `theagentorg-shared-github-issue-opened` is ENABLED and matches
   `source: github.webhook`, `detail-type: issues`, `detail.action: opened`, but
   `list-targets-by-rule` returns `[]`: the connection and API destination are
   count-gated behind `dispatch_token_secret_name`, which is unset because an
   `API_KEY` connection needs the token's *value* at plan time. Mint a
   fine-grained token with `actions: write` on the one repo, put it in Secrets
   Manager, set the variable, apply.

   **Ordering matters here and it cost real time:** the workflow file must be on
   `origin/main` *before* the target is applied. GitHub resolves the workflow
   file on the ref, and it answers **404 both** for "file not on ref" and for an
   unauthenticated dispatch — two causes, one indistinguishable symptom.

### Not yet verified

- **No live end-to-end run has gone `issue → Lambda → EventBridge → workflow →
  five runtimes`.** Each link is tested individually; the whole chain has never
  fired.
- **No human has rejected a real Environment and watched a rejection-recorder job
  fire.** That path is verified by in-process invocation and YAML parse only.

## Who owns what

| Area | Owner |
|---|---|
| `infra/` — all Terraform | **Sorour** |
| `agentorg/{graph,gates,log,state}.py`, `agentorg/common/`, `agentorg/agents/` | **Sorour** |
| `agentorg/github_ops.py`, `.github/workflows/`, `scripts/scan_gate.py` | **Mariam** |
| `agentorg/security/` — semgrep / gitleaks / trivy wrappers | **Habiba** |
| `target_repo/`, `tickets/`, `tests/test_functional_*`, `tests/test_baseline.py` | **Reem** |
| `tests/test_block_*`, `tests/test_chaos_*`, `tests/test_dora_*`, `tests/provenance.py` | **Aya** |

## How nobody blocks anybody

1. `agentorg/state.py` is the frozen contract. You may **add** optional fields;
   never rename or remove one. A rename breaks all five lanes at once and nobody
   notices until integration.
2. `fixtures/` holds a validated sample of every result, so a lane loads a
   teammate's fixture instead of waiting for their real code.
3. Each person owns their own directory, so no two people edit the same files.

## Plans and docs

Per-person plans are in [`docs/plan/`](docs/plan/); start with
[`00-timeline.md`](docs/plan/00-timeline.md). The demo runbook, with pasted
output from real runs, is
[`docs/plan/reem/demo_script.md`](docs/plan/reem/demo_script.md).
Instructions for Claude Code sessions are in [`CLAUDE.md`](CLAUDE.md).
