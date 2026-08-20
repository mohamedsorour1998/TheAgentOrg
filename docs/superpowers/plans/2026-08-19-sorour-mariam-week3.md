# Sorour + Mariam Week 3 — Timeline, Approvals, Deploy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Give the judges something to read (a run timeline), give a human somewhere to click (approve/reject), and get the five agents onto AgentCore — without ever putting the demo at the mercy of the cloud.

**Architecture:** Two new read-only surfaces over artifacts that already exist — `timeline.py` reads `log.read(run_id)` and nothing else; `approve_server.py` is buttons over `gates.resume`, which is already the CLI's path. Deploy is additive: containers, a `deploy_note()` that reports reality, and an OIDC workflow with no static keys.

**Tech Stack:** Python 3.12+, pydantic v2, Bedrock AgentCore + the `agentcore` CLI, Terraform (already applied), GitHub Actions OIDC, pytest, ruff 0.16.

**Spec:** `docs/plan/sorour/week3.md` and `docs/plan/mariam/week3.md`.

## Global Constraints

- `agentorg/state.py` is FROZEN. Additions only; never rename or remove.
- The suite must stay green and `ruff check agentorg scripts tests` must exit 0. No `[tool.ruff]`, no per-file ignores, no `# noqa`.
- Never commit `.env`. Never put a static AWS key anywhere — the deploy job exists precisely to avoid them.
- Do not weaken the four autouse guards in `tests/conftest.py`.
- **The offline path is never cut.** `OFFLINE=true python -m agentorg.graph` runs the whole demo with no AWS at all, and it is the documented fallback if AgentCore is unstable at the venue.
- **The security block is never cut**, and neither is the timeline — the block IS the demo, the timeline is the UX the judges score.
- Feature freeze is **Tue Aug 25**. After it: only fixes for what dry runs surface.

## Cut order if behind (from the specs, decided in advance so nobody improvises under pressure)

SRE agent first (leave it a stub reading CI) → then the approve/reject UI (the `gates_cli` fallback already works and is verified) → then AgentCore itself (the offline path carries the demo). Never the block, never the timeline, never offline.

---

## Task 1: Fix the packaging defect that will break every container build

**Files:** Modify `pyproject.toml`. Test: `tests/test_packaging.py` (create).

This is first because Tasks 5–6 cannot work without it, and it is invisible until then.

`pyproject.toml` declares `[tool.setuptools] packages = ["agentorg"]`. That ships **only** `agentorg/*.py`: a non-editable install drops `agents/`, `common/` and `security/` entirely, so `import agentorg.graph` fails. Everything to date has been editable or run from source, which is why nothing has caught it. Verified by the final whole-branch review with `pip install --no-deps --target=…`.

An AgentCore container installs the package properly. So today, every agent image would build green and fail on import at runtime.

Steps: reproduce it first (install into a temp target, confirm the subpackages are absent and the import fails), fix the declaration so subpackages ship, then add a test that installs into a temp dir and imports `agentorg.graph`, `agentorg.agents.security` and `agentorg.common.llm`. Verify the test fails against the old declaration.

---

## Task 2: `agentorg/timeline.py` — render a run as a timeline

**Files:** Create `agentorg/timeline.py`. Test: `tests/test_timeline.py`.

**Interfaces produced:** a text renderer and an HTML renderer, both taking a `run_id` and reading **only** `log.read(run_id)`. No other input — that constraint is what makes the timeline trustworthy as evidence.

This is the UX the judges score, and it is explicitly uncuttable.

Requirements beyond the spec's, from findings already on record:
- **Surface scan provenance.** A previous review established that the decision log cannot currently answer "did the scanners actually run, or did this verdict come from a fixture?" — the only tell is the explanation's wording. The timeline is where that belongs. If the log does not yet carry it, add it to the existing `LogEvent` at the security call site rather than to `state.py`.
- **Surface whether the block reason reached the PR.** `post_comment` returns an `https://…` URL on delivery and `comment://<run_id>` when it could not deliver, and that ref is already logged. Render the difference — a block nobody was told about is a different outcome from a block that was reported.
- Render a blocked run and a promoted run distinguishably at a glance. Someone should be able to look at one screen and say which happened, without reading prose.

Test against a real run's log, not a hand-built fixture: run the pipeline, then render its `run_id`. A timeline that only works on synthetic input is not evidence.

---

## Task 3: `agentorg/approve_server.py` — approve/reject over `gates.resume`

**Files:** Create `agentorg/approve_server.py`. Test: `tests/test_approve_server.py`.

**Consumes:** `gates.resume(run_id, HumanDecision) -> RunState` and `gates.save`, both already built and pinned. `HumanDecision.decision` is `"approved"`/`"rejected"`/`"overridden"` — exact strings.

Buttons over the same call the CLI already makes. Keep it small: this is the cut-safe item, and `python -m agentorg.gates_cli` is the verified fallback if it goes wrong.

Two properties to pin, both learned the hard way in this repo:
- A decision made through the server must persist to disk and survive a second decision on the same run. `resume` previously returned an updated state without writing it, so two sequential decisions lost the first.
- A rejected run must stay rejected. It must not be possible to approve a run the graph already rejected and have the file agree.

**`runs/` currently holds thousands of state files**, and `gates_cli list` prints one line per file. Whatever listing the server offers must not be that. Filter to runs actually awaiting a decision — the data to do so exists now.

---

## Task 4: `deploy_note()` reports the real runtimes

**Files:** Modify `agentorg/github_ops.py`. Test: `tests/test_deploy_note.py`.

`deploy_note()` is still the placeholder string "deploy not wired yet — pair with Sorour on infra/agentcore/". Make it report the five deployed `theagentorg_*` runtimes.

It must degrade like everything else in that module: no AWS credentials, no network, or no deployment yet must produce an honest message, never an exception and never a fabricated success. `github_ops` already has the pattern — a bounded one-line WARNING, the reason surfaced, a truthful return value.

Pin the no-credentials path, because that is what CI and every teammate's laptop will hit.

---

## Task 5: AgentCore deploy for the five agents

**Files:** `infra/agentcore/` (per the spec), plus whatever container definition the agents need.

**Consumes:** Task 1's packaging fix — without it the images build and fail on import.

The ARNs are already recorded in `docs/plan/week1-verification-log.md`: `agentcore_runtime_role_arn`, the five `ecr_repository_urls`, and `github_actions_role_arns`. Do not re-derive them from Terraform state; read them there and say so.

**This task needs real AWS and the `agentcore` CLI.** If either is unavailable, STOP and report that plainly — do not simulate a deploy, and do not report `agentcore status` output you did not obtain. A fabricated READY is worse than an honest blocker, because the fallback plan depends on knowing the truth.

Done when `agentcore status` shows READY for each runtime and `agentcore invoke '{"task":"say hi"}'` returns a real completion for at least one.

---

## Task 6: `.github/workflows/deploy.yml` — OIDC, zero static keys

**Files:** Create `.github/workflows/deploy.yml`.

**Consumes:** Task 5's images; `github-actions-role` (already exists in the account, trusts `repo:mohamedsorour1998/TheAgentOrg:*`, and is looked up by Terraform via a data source rather than managed).

Deploys on a push to `agentorg/agents/**`, assuming the role via OIDC. **No static AWS keys anywhere** — that is the point of the task, not a nicety.

Validate with `actionlint` and `shellcheck` before committing; both are already used by the CI workflow. Note you cannot prove a workflow passes without pushing — say what you validated versus what you ran.

---

## Task 7: Dry runs, online and offline

**Files:** Append to `docs/plan/week1-verification-log.md`.

Two clean dry runs and two poisoned, one pair online and one pair offline, behaving identically. Poisoned blocks 10/10 in **both** modes.

**State scanner provenance for every measurement.** The binaries are not on the default PATH; without them the verdict comes from a fixture and `compute_security_verdict` is never called. "10/10 blocked" means two different things in the two modes and the distinction has bitten this project repeatedly. Run the dry runs with the binaries installed, which is also how the demo machine should be configured.

Record what did not hold, not only what did.

---

## Self-Review

**Spec coverage:** Sorour's timeline → Task 2; his approve/reject → Task 3; his and Mariam's AgentCore deploy → Tasks 1, 5; Mariam's `deploy_note()` → Task 4; her OIDC job → Task 6; both their dry runs → Task 7. The Aug 25 freeze is process, not code. The SRE agent is deliberately left a stub: it is first on the specs' own cut list and nothing downstream reads it.

**Ordering:** Task 1 is first because Tasks 5–6 silently depend on it. Task 2 outranks Task 3 because the timeline cannot be cut and the approval UI can.

**Known risk:** Tasks 5 and 6 depend on real AWS and cannot be verified from a laptop. If they slip, the demo is unaffected — the offline path is the rehearsed fallback and is already verified at 10/10.
