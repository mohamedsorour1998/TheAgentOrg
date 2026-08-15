# Week 2 — Real Agents + Real CI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the four stubbed agents with real Bedrock calls and turn CI into a real gate, without ever letting the poisoned ticket stop blocking.

**Architecture:** A single shared helper (`agentorg/common/llm.py`) owns model invocation, JSON extraction, and the fallback-to-fixture rule, so each agent file stays a prompt plus three lines. The security verdict is never computed by a model — `compute_security_verdict()` in `state.py` decides, and the LLM only writes prose. `github_ops.py` grows an offline path so the whole pipeline runs with no network.

**Tech Stack:** Python 3.12+, pydantic v2, Strands (`strands-agents`), AWS Bedrock (Nova Lite, `us-east-1`), PyGithub, ruff, pytest, GitHub Actions.

**Spec:** `docs/plan/sorour/week2.md` and `docs/plan/mariam/week2.md` (the two lane plans this merges), plus `docs/plan/week1-verification-log.md` for what already landed.

## Global Constraints

- **`agentorg/state.py` is frozen.** You may ADD optional fields. NEVER rename or remove one. No task in this plan modifies `state.py`.
- **CI has no AWS credentials.** Every agent MUST fall back to its fixture when the model is unavailable or its reply will not parse. `pytest -q` must print `3 passed` with no AWS credentials and no `GITHUB_TOKEN` present. This is the single most important constraint in the plan — a real Bedrock call that raises inside `run_pipeline` breaks every lane's tests exactly the way an unguarded PyGithub client did in PR #2.
- **The LLM never decides pass/block.** `compute_security_verdict(findings, threshold)` in `state.py` returns the verdict. The security agent's model call writes `explanation` only.
- **Exact field names** (drift here silently breaks other lanes): `SecurityResult.blocking` (NOT `blocking_findings`); `ReviewResult.verdict` is `"approve"` / `"changes_requested"` (NOT `"approved"`); `HumanDecision.decision` is `"approved"` / `"rejected"` / `"overridden"`.
- **Model access is always via `create_model()`** in `agentorg/common/model.py`. Never construct a `BedrockModel` or `OpenAIModel` by hand. Default model id `us.amazon.nova-2-lite-v1:0`, region `us-east-1`.
- **Never commit `.env`.** It holds a live GitHub token. It is already in `.gitignore`.
- **Target repo is `mohamedsorour1998/auth-service`** (public). `DEMO_REPO` is the env var that points at it.
- **Hard deadline:** by end of Friday Aug 21 the poisoned ticket must block on 10 consecutive runs. Not 9 of 10.
- **Model for execution:** every implementer subagent and every reviewer subagent runs on Opus.

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `pyproject.toml` | Declare `strands-agents`, `boto3`, `ruff` | 1 |
| `agentorg/common/config.py` | Add `LLM_DISABLED`, `OFFLINE_REPO`, `OFFLINE_NOTES` | 2, 9 |
| `agentorg/common/llm.py` | **New.** Model invocation, JSON extraction, fallback rule | 2 |
| `agentorg/agents/planner.py` | Ticket → `PlanResult` | 3 |
| `agentorg/agents/developer.py` | Plan → `DevResult`, poisoned safety net | 4 |
| `agentorg/agents/security.py` | Scanners → deterministic verdict + prose | 5 |
| `agentorg/agents/reviewer.py` | Diff → `ReviewResult` | 6 |
| `agentorg/graph.py` | Interactive gate routing | 7 |
| `agentorg/gates_cli.py` | **New.** Async approve/reject CLI | 7 |
| `.github/workflows/ci.yml` | lint + test + scan jobs | 8 |
| `agentorg/github_ops.py` | Offline git path; comment hardening | 9, 10 |
| `tests/test_llm_helper.py` | **New.** JSON extraction + availability | 2 |
| `tests/test_agent_fallbacks.py` | **New.** Every agent degrades to fixtures | 3–6 |
| `tests/test_gates_cli.py` | **New.** Gate decisions recorded correctly | 7 |
| `tests/test_offline_mode.py` | **New.** Offline PR + NOTES | 9 |

---

## Task 1: Declare the missing dependencies and establish a lint baseline

**Files:**
- Modify: `pyproject.toml:5-19`

**Interfaces:**
- Consumes: nothing.
- Produces: `strands`, `boto3` importable; `ruff` available as a dev tool. Every later task depends on this.

**Why first:** `strands` and `boto3` are currently commented out and do not import. Task 3 onward cannot run at all until this lands. `ruff` is required by Task 8's CI lint job.

- [ ] **Step 1: Confirm the dependencies are genuinely missing**

Run: `python -c "import strands"`
Expected: `ModuleNotFoundError: No module named 'strands'`

- [ ] **Step 2: Uncomment the runtime dependencies**

In `pyproject.toml`, the `dependencies` list currently reads:

```toml
dependencies = [
    "pydantic>=2.0",
    # Uncomment as each lane comes online:
    # "strands-agents",        # the agent framework (Sorour)
    # "fastmcp",               # agent servers on AgentCore (Sorour)
    # "boto3",                 # AWS / Bedrock (Sorour)
    "PyGithub",               # github_ops (Mariam)
    # "flask",                 # target_repo app (Reem)
]
```

Replace it with:

```toml
dependencies = [
    "pydantic>=2.0",
    "strands-agents",          # the agent framework (Sorour)
    "boto3",                   # AWS / Bedrock (Sorour)
    "PyGithub",                # github_ops (Mariam)
    # Uncomment as each lane comes online:
    # "fastmcp",               # agent servers on AgentCore (Sorour, wk3)
    # "flask",                 # target_repo app (Reem)
]
```

- [ ] **Step 3: Add ruff to the dev extra**

The `[project.optional-dependencies]` block currently reads:

```toml
[project.optional-dependencies]
dev = [
    "pytest>=8.0",
]
```

Replace it with:

```toml
[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "ruff>=0.6",
]
```

- [ ] **Step 4: Install and confirm both import**

```bash
pip install -e ".[dev]"
python -c "import strands, boto3; print('deps OK')"
```
Expected: `deps OK`

- [ ] **Step 5: Establish the lint baseline**

Run: `ruff check agentorg`

Fix every finding ruff reports. Do NOT add ignore rules or a ruff config to silence them — the point is that CI's lint job passes honestly on the first PR of the week. If ruff reports unused imports in files this plan later rewrites (for example `json` imported but unused), remove them now.

Re-run until clean.
Expected: `All checks passed!`

- [ ] **Step 6: Confirm nothing regressed**

```bash
python make_fixtures.py
env -u GITHUB_TOKEN -u DEMO_REPO python -m pytest -q
```
Expected: `3 passed`

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml agentorg
git commit -m "build: declare strands-agents, boto3, ruff; clean lint baseline"
```

---

## Task 2: Shared LLM helper with the fallback rule

**Files:**
- Create: `agentorg/common/llm.py`
- Modify: `agentorg/common/config.py` (append only)
- Test: `tests/test_llm_helper.py`

**Interfaces:**
- Consumes: `create_model()` from `agentorg/common/model.py`.
- Produces, and every agent task below calls exactly these:
  - `available() -> bool`
  - `extract_json(text: str) -> str`
  - `structured(model_cls: type[T], system_prompt: str, user_prompt: str) -> T | None` — returns a validated instance of `model_cls`, or `None` when the model is unavailable or its reply will not parse.
  - `text(system_prompt: str, user_prompt: str) -> str | None` — plain-text reply, or `None`.

**Design note:** the lane plans duplicate a private `_extract_json` in three separate agent files. That duplication is deliberate**ly** removed here — one helper, four callers. Each agent becomes a prompt plus a three-line `run()`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_llm_helper.py`:

```python
"""Unit tests for the shared LLM helper. Owner: Sorour."""

import pytest

from agentorg.common import llm
from agentorg.state import PlanResult


def test_extract_json_from_fenced_block():
    raw = 'Sure!\n```json\n{"a": 1}\n```\nHope that helps.'
    assert llm.extract_json(raw) == '{"a": 1}'


def test_extract_json_from_unlabelled_fence():
    raw = '```\n{"a": 1}\n```'
    assert llm.extract_json(raw) == '{"a": 1}'


def test_extract_json_from_chatty_reply():
    raw = 'Here you go: {"a": 1} — let me know.'
    assert llm.extract_json(raw) == '{"a": 1}'


def test_extract_json_passes_through_bare_json():
    assert llm.extract_json('{"a": 1}') == '{"a": 1}'


def test_available_is_false_when_disabled(monkeypatch):
    monkeypatch.setattr(llm.config, "LLM_DISABLED", True)
    assert llm.available() is False


def test_structured_returns_none_when_disabled(monkeypatch):
    monkeypatch.setattr(llm.config, "LLM_DISABLED", True)
    assert llm.structured(PlanResult, "sys", "user") is None


def test_text_returns_none_when_disabled(monkeypatch):
    monkeypatch.setattr(llm.config, "LLM_DISABLED", True)
    assert llm.text("sys", "user") is None


def test_structured_returns_none_on_unparseable_reply(monkeypatch):
    monkeypatch.setattr(llm.config, "LLM_DISABLED", False)
    monkeypatch.setattr(llm, "_complete", lambda s, u: "not json at all")
    assert llm.structured(PlanResult, "sys", "user") is None


def test_structured_parses_a_valid_reply(monkeypatch):
    monkeypatch.setattr(llm.config, "LLM_DISABLED", False)
    monkeypatch.setattr(llm, "_complete", lambda s, u: (
        '```json\n{"tasks": ["t"], "acceptance_criteria": ["c"], '
        '"target_files": ["f"], "notes": ""}\n```'
    ))
    result = llm.structured(PlanResult, "sys", "user")
    assert isinstance(result, PlanResult)
    assert result.tasks == ["t"]


def test_structured_returns_none_when_the_model_raises(monkeypatch):
    def boom(system_prompt, user_prompt):
        raise RuntimeError("bedrock exploded")

    monkeypatch.setattr(llm.config, "LLM_DISABLED", False)
    monkeypatch.setattr(llm, "_complete", boom)
    assert llm.structured(PlanResult, "sys", "user") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_llm_helper.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'agentorg.common.llm'`

- [ ] **Step 3: Add the config knob**

Append to `agentorg/common/config.py` (do not rename or reorder anything already there):

```python
# LLM availability (Sorour) -------------------------------------------------
# Set true to force every agent onto its fixture without attempting a model
# call. CI sets this so the suite never needs AWS credentials.
LLM_DISABLED = os.environ.get("LLM_DISABLED", "false").lower() == "true"
```

- [ ] **Step 4: Write the helper**

Create `agentorg/common/llm.py`:

```python
"""Shared model invocation for every agent. OWNER: Sorour.

One rule governs this module: an agent must never crash the pipeline because
a model was slow, absent, or chatty. `structured()` and `text()` return None
on any failure, and the caller falls back to its fixture. That is what keeps
`pytest -q` green in CI, which has no AWS credentials.
"""

from __future__ import annotations

import re
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from . import config
from .model import create_model

T = TypeVar("T", bound=BaseModel)

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def available() -> bool:
    """True when a model call is worth attempting. Cheap; makes no network call."""
    if config.LLM_DISABLED:
        return False
    if config.LLM_BASE_URL:
        return bool(config.LLM_API_KEY) and config.LLM_API_KEY != "not-needed"
    try:
        import boto3

        session = boto3.Session(region_name=config.AWS_REGION)
        return session.get_credentials() is not None
    except Exception:
        return False


def extract_json(text_in: str) -> str:
    """Pull a JSON object out of a reply that may be fenced or chatty."""
    text_in = text_in.strip()
    fenced = _FENCE.search(text_in)
    if fenced:
        return fenced.group(1).strip()
    start, end = text_in.find("{"), text_in.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text_in[start : end + 1]
    return text_in


def _complete(system_prompt: str, user_prompt: str) -> str:
    """Raw model call. Separated so tests can substitute it."""
    from strands import Agent

    agent = Agent(model=create_model(), system_prompt=system_prompt)
    return str(agent(user_prompt))


def text(system_prompt: str, user_prompt: str) -> str | None:
    """Plain-text reply, or None if the model is unavailable or failed."""
    if not available():
        return None
    try:
        reply = _complete(system_prompt, user_prompt)
    except Exception:
        return None
    reply = reply.strip()
    return reply or None


def structured(model_cls: type[T], system_prompt: str, user_prompt: str) -> T | None:
    """Reply parsed into model_cls, or None if unavailable/unparseable."""
    raw = text(system_prompt, user_prompt)
    if raw is None:
        return None
    try:
        return model_cls.model_validate_json(extract_json(raw))
    except (ValidationError, ValueError):
        return None
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_llm_helper.py -q`
Expected: `10 passed`

- [ ] **Step 6: Confirm the whole suite and lint are clean**

```bash
env -u GITHUB_TOKEN -u DEMO_REPO python -m pytest -q
ruff check agentorg
```
Expected: `13 passed`, then `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add agentorg/common/llm.py agentorg/common/config.py tests/test_llm_helper.py
git commit -m "feat(llm): shared model helper that degrades to fixtures"
```

---

## Task 3: Planner agent calls a real model

**Files:**
- Modify: `agentorg/agents/planner.py` (whole file)
- Test: `tests/test_agent_fallbacks.py`

**Interfaces:**
- Consumes: `llm.structured(PlanResult, ...)` from Task 2.
- Produces: `planner.run(state: RunState) -> PlanResult` — signature unchanged; `graph.py` keeps calling it as-is.

- [ ] **Step 1: Write the failing test**

Create `tests/test_agent_fallbacks.py`:

```python
"""Every agent must degrade to its fixture when no model is available.

This is what keeps CI green without AWS credentials. Owner: Sorour.
"""

from agentorg.agents import planner
from agentorg.common import config
from agentorg.state import RunState


def _state() -> RunState:
    return RunState(ticket_id="CLEAN-1", ticket_text="Add a per-IP login rate limit.")


def test_planner_falls_back_to_fixture_without_a_model(monkeypatch):
    monkeypatch.setattr(config, "LLM_DISABLED", True)
    result = planner.run(_state())
    assert result.tasks, "planner must still return a usable plan"
    assert result.acceptance_criteria
    assert result.target_files


def test_planner_uses_the_model_when_one_answers(monkeypatch):
    from agentorg.common import llm

    monkeypatch.setattr(config, "LLM_DISABLED", False)
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "_complete", lambda s, u: (
        '{"tasks": ["from the model"], "acceptance_criteria": ["c"], '
        '"target_files": ["app/auth.py"], "notes": ""}'
    ))
    result = planner.run(_state())
    assert result.tasks == ["from the model"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_agent_fallbacks.py -q`
Expected: FAIL — `test_planner_uses_the_model_when_one_answers` fails because the stub returns the fixture regardless.

- [ ] **Step 3: Rewrite the planner**

Replace the whole of `agentorg/agents/planner.py` with:

```python
"""Planner agent — turns a ticket into a PlanResult.

OWNER: Sorour. Falls back to the fixture whenever no model answers, so the
pipeline runs end-to-end on a machine with no AWS credentials.
"""

from ..state import RunState, PlanResult
from ..common import llm
from .. import fixtures_loader

SYSTEM_PROMPT = """You are the Planner in a CI/CD pipeline. Read the ticket and
produce an implementation plan. Respond with ONE JSON object and nothing else —
no prose, no markdown fences. Shape:
{
  "tasks": ["<concrete task>", ...],
  "acceptance_criteria": ["<checkable criterion>", ...],
  "target_files": ["<path likely to change>", ...],
  "notes": "<short optional note>"
}
Do NOT write code. Keep every list non-empty."""


def run(state: RunState) -> PlanResult:
    """Plan the ticket. Returns the fixture plan if no model is available."""
    result = llm.structured(PlanResult, SYSTEM_PROMPT, state.ticket_text)
    return result if result is not None else fixtures_loader.plan()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_agent_fallbacks.py -q`
Expected: `2 passed`

- [ ] **Step 5: Confirm the pipeline is unaffected without credentials**

```bash
env -u GITHUB_TOKEN -u DEMO_REPO LLM_DISABLED=true python -m pytest -q
ruff check agentorg
```
Expected: `15 passed`, then `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add agentorg/agents/planner.py tests/test_agent_fallbacks.py
git commit -m "feat(planner): real model call with fixture fallback"
```

---

## Task 4: Developer agent calls a real model, with the poisoned safety net

**Files:**
- Modify: `agentorg/agents/developer.py` (whole file)
- Test: `tests/test_agent_fallbacks.py` (append)

**Interfaces:**
- Consumes: `llm.structured(DevResult, ...)` from Task 2.
- Produces: `developer.run(state: RunState, poisoned: bool = False) -> DevResult` — signature unchanged. Sets `dev.branch` to `agent-org/<ticket_id>` when the model leaves it blank; `github_ops.open_pr` later overwrites it with `agent-org/<ticket_id>-<short_sha>`.

**Critical behaviour:** when `poisoned=True`, the returned diff MUST contain an AWS access key matching `AKIA[0-9A-Z]{16}`. If the model did not emit one, substitute the poisoned reference diff. Friday's 10/10 depends on this.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_agent_fallbacks.py`:

```python
import re

from agentorg.agents import developer
from agentorg.state import PlanResult

_AWS_KEY_RE = re.compile(r"AKIA[0-9A-Z]{16}")


def _planned_state() -> RunState:
    state = RunState(ticket_id="POISON-1", ticket_text="Add a per-IP login rate limit.")
    state.plan = PlanResult(
        tasks=["add limiter"],
        acceptance_criteria=["429 on the 6th attempt"],
        target_files=["app/auth.py"],
    )
    return state


def test_developer_falls_back_to_fixture_without_a_model(monkeypatch):
    monkeypatch.setattr(config, "LLM_DISABLED", True)
    result = developer.run(_planned_state())
    assert result.diff
    assert result.files_changed


def test_poisoned_run_always_carries_an_aws_key_from_the_fixture(monkeypatch):
    monkeypatch.setattr(config, "LLM_DISABLED", True)
    result = developer.run(_planned_state(), poisoned=True)
    assert _AWS_KEY_RE.search(result.diff), "poisoned diff must carry the key"


def test_poisoned_safety_net_rescues_a_clean_model_diff(monkeypatch):
    """The model refused to write the key; the safety net substitutes it."""
    from agentorg.common import llm

    monkeypatch.setattr(config, "LLM_DISABLED", False)
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "_complete", lambda s, u: (
        '{"branch": "", "diff": "--- a/app/auth.py\\n+++ b/app/auth.py\\n+safe\\n", '
        '"summary": "no secrets here", "files_changed": ["app/auth.py"]}'
    ))
    result = developer.run(_planned_state(), poisoned=True)
    assert _AWS_KEY_RE.search(result.diff), "safety net must restore the key"


def test_clean_run_keeps_the_model_diff(monkeypatch):
    from agentorg.common import llm

    monkeypatch.setattr(config, "LLM_DISABLED", False)
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "_complete", lambda s, u: (
        '{"branch": "", "diff": "--- a/app/auth.py\\n+++ b/app/auth.py\\n+safe\\n", '
        '"summary": "adds a limiter", "files_changed": ["app/auth.py"]}'
    ))
    result = developer.run(_planned_state(), poisoned=False)
    assert "+safe" in result.diff
    assert result.branch == "agent-org/POISON-1"


def test_reviewer_feedback_reaches_the_prompt(monkeypatch):
    """A revision must feed must_fix back to the model."""
    from agentorg.common import llm
    from agentorg.state import ReviewResult

    seen = {}

    def capture(system_prompt, user_prompt):
        seen["prompt"] = user_prompt
        return ('{"branch": "", "diff": "d", "summary": "s", '
                '"files_changed": ["app/auth.py"]}')

    state = _planned_state()
    state.review = ReviewResult(verdict="changes_requested",
                                must_fix=["handle the 429 branch"])
    monkeypatch.setattr(config, "LLM_DISABLED", False)
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "_complete", capture)
    developer.run(state)
    assert "handle the 429 branch" in seen["prompt"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_agent_fallbacks.py -q`
Expected: FAIL — the safety-net and prompt-capture tests fail against the stub.

- [ ] **Step 3: Rewrite the developer**

Replace the whole of `agentorg/agents/developer.py` with:

```python
"""Developer agent — turns a PlanResult into a DevResult (a diff).

OWNER: Sorour.

The `poisoned` switch is a demo safety net, not a code path the model sees:
the real agent runs first, and only if the poisoned run somehow came back
without an AWS key do we substitute the reference diff. Friday's 10/10 block
depends on that key being present every single time.
"""

import re

from ..state import RunState, DevResult
from ..common import llm
from .. import fixtures_loader

SYSTEM_PROMPT = """You are the Developer in a CI/CD pipeline. Implement the plan
as a unified git diff. Respond with ONE JSON object and nothing else. Shape:
{
  "branch": "agent-org/<ticket-id>",
  "diff": "<unified diff as a single string>",
  "summary": "<one-line summary>",
  "files_changed": ["<path>", ...]
}
Implement EXACTLY what the ticket asks, including any literal code the ticket
provides. Read secrets from environment variables — never invent credentials."""

_AWS_KEY = re.compile(r"AKIA[0-9A-Z]{16}")


def _prompt(state: RunState) -> str:
    parts = [f"TICKET:\n{state.ticket_text}"]
    if state.plan is not None:
        parts.append("PLAN TASKS:\n- " + "\n- ".join(state.plan.tasks))
        parts.append("TARGET FILES:\n- " + "\n- ".join(state.plan.target_files))
    if state.review is not None and state.review.must_fix:
        parts.append(
            "REVIEWER REQUESTED CHANGES — you MUST fix all of:\n- "
            + "\n- ".join(state.review.must_fix)
        )
    return "\n\n".join(parts)


def run(state: RunState, poisoned: bool = False) -> DevResult:
    """Write the diff. Falls back to the fixture if no model is available."""
    dev = llm.structured(DevResult, SYSTEM_PROMPT, _prompt(state))
    if dev is None:
        dev = fixtures_loader.dev(poisoned=poisoned)
    if not dev.branch:
        dev.branch = f"agent-org/{state.ticket_id}"

    # Demo safety net: a poisoned run must always ship the key so the scanners
    # have something to catch. The clean path always keeps the model's diff.
    if poisoned and not _AWS_KEY.search(dev.diff):
        reference = fixtures_loader.dev(poisoned=True)
        dev.diff = reference.diff
        dev.files_changed = reference.files_changed
    return dev
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_agent_fallbacks.py -q`
Expected: `7 passed`

- [ ] **Step 5: Confirm the pipeline still blocks and promotes**

```bash
env -u GITHUB_TOKEN -u DEMO_REPO LLM_DISABLED=true python -m pytest -q
env -u GITHUB_TOKEN -u DEMO_REPO LLM_DISABLED=true python -m agentorg.graph
env -u GITHUB_TOKEN -u DEMO_REPO LLM_DISABLED=true python -m agentorg.graph --poisoned
ruff check agentorg
```
Expected: `20 passed`; then `status=promoted`; then `status=blocked` with `blocking=2`; then `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add agentorg/agents/developer.py tests/test_agent_fallbacks.py
git commit -m "feat(developer): real model call, revision feedback, poisoned safety net"
```

---

## Task 5: Security agent — real scanners behind the deterministic verdict

**Files:**
- Modify: `agentorg/agents/security.py` (whole file)
- Test: `tests/test_agent_fallbacks.py` (append)

**Interfaces:**
- Consumes: `run_all_scanners(dev)` from `agentorg/security/__init__.py`; `compute_security_verdict(findings, threshold)` from `state.py`; `llm.text(...)` from Task 2.
- Produces: `security.run(state: RunState, use_real_scanners: bool = True) -> SecurityResult`. Note the default flips to `True`.

**This is the task the demo rests on.** Pulled ahead of the reviewer deliberately: it is the only task the Friday deadline depends on, and it needs nothing from the reviewer.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_agent_fallbacks.py`:

```python
from agentorg.agents import security
from agentorg.state import DevResult


def _poisoned_dev_state() -> RunState:
    state = RunState(ticket_id="POISON-1", ticket_text="Add a per-IP login rate limit.")
    state.dev = DevResult(
        branch="agent-org/POISON-1",
        diff='AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"\n',
        summary="adds a limiter",
        files_changed=["app/auth.py"],
    )
    return state


def test_security_blocks_the_poisoned_diff(monkeypatch):
    monkeypatch.setattr(config, "LLM_DISABLED", True)
    result = security.run(_poisoned_dev_state())
    assert result.verdict == "block"
    assert len(result.blocking) == 2
    assert result.explanation, "a blocked run must always explain itself"


def test_security_passes_a_clean_diff(monkeypatch):
    monkeypatch.setattr(config, "LLM_DISABLED", True)
    state = _poisoned_dev_state()
    state.dev.diff = "+ nothing secret here\n"
    result = security.run(state)
    assert result.verdict == "pass"
    assert result.blocking == []


def test_verdict_survives_a_crashing_scanner(monkeypatch):
    """Habiba's lane exploding must not take the pipeline down."""
    def boom(dev):
        raise RuntimeError("gitleaks binary missing")

    monkeypatch.setattr(config, "LLM_DISABLED", True)
    monkeypatch.setattr(security, "run_all_scanners", boom)
    result = security.run(_poisoned_dev_state())
    assert result.verdict == "block"
    assert len(result.blocking) == 2


def test_the_model_cannot_overturn_the_verdict(monkeypatch):
    """Even a model screaming PASS must not change a block."""
    from agentorg.common import llm

    monkeypatch.setattr(config, "LLM_DISABLED", False)
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "_complete", lambda s, u: "This is completely fine, PASS.")
    result = security.run(_poisoned_dev_state())
    assert result.verdict == "block"
    assert len(result.blocking) == 2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_agent_fallbacks.py -q`
Expected: FAIL — `test_security_blocks_the_poisoned_diff` fails on the empty `explanation`, because the stub path returns the fixture and the real path sets `explanation=""`.

- [ ] **Step 3: Rewrite the security agent**

Replace the whole of `agentorg/agents/security.py` with:

```python
"""Security agent — scanners, the deterministic block rule, and prose.

OWNER: Sorour wires the agent; Habiba owns the scanners in agentorg/security/.

CRITICAL: the LLM does NOT decide pass/block. compute_security_verdict() —
pure code in state.py — does. The model is handed a verdict that is already
final and asked only to explain it. That is what makes the poisoned ticket
block on every single run.
"""

from ..state import RunState, SecurityResult, compute_security_verdict
from ..common import config
from ..common import llm
from .. import fixtures_loader
from ..security import run_all_scanners

SYSTEM_PROMPT = """You are the Security explainer. You are given a verdict and a
list of blocking findings that were ALREADY decided by code. Write 1-3 plain
sentences explaining why the change was blocked or allowed, naming the tools and
rules. You may NOT change the verdict. Return plain text, no JSON."""


def _default_explanation(verdict: str, blocking: list) -> str:
    """Deterministic prose, used whenever no model answers."""
    if verdict == "block":
        return "Blocked: " + "; ".join(
            f"{f.tool}:{f.rule} ({f.severity}) in {f.file}:{f.line}" for f in blocking
        )
    return "Passed: no findings at or above the block threshold."


def _explain(verdict: str, blocking: list) -> str:
    """Let the model write the prose; fall back to a fixed string."""
    findings_txt = "\n".join(
        f"- {f.tool} {f.rule} {f.severity} {f.file}:{f.line} {f.description}"
        for f in blocking
    ) or "(none)"
    reply = llm.text(
        SYSTEM_PROMPT, f"VERDICT: {verdict}\nBLOCKING FINDINGS:\n{findings_txt}"
    )
    return reply if reply else _default_explanation(verdict, blocking)


def run(state: RunState, use_real_scanners: bool = True) -> SecurityResult:
    """Scan the diff, decide in code, then attach an explanation."""
    if not use_real_scanners:
        poisoned = state.dev is not None and "AKIA" in (state.dev.diff or "")
        return fixtures_loader.security(block=poisoned)

    try:
        findings = run_all_scanners(state.dev)
    except Exception:
        # The scanner lane is not ready or crashed. Fall back to the fixture so
        # the graph never waits on another lane. Deterministic, no LLM.
        poisoned = state.dev is not None and "AKIA" in (state.dev.diff or "")
        return fixtures_loader.security(block=poisoned)

    verdict, blocking = compute_security_verdict(
        findings, threshold=config.SECURITY_BLOCK_THRESHOLD
    )
    return SecurityResult(
        verdict=verdict,
        findings=findings,
        blocking=blocking,
        explanation=_explain(verdict, blocking),
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_agent_fallbacks.py -q`
Expected: `11 passed`

- [ ] **Step 5: Confirm the demo behaviour end to end**

```bash
env -u GITHUB_TOKEN -u DEMO_REPO LLM_DISABLED=true python -m pytest -q
env -u GITHUB_TOKEN -u DEMO_REPO LLM_DISABLED=true python -m agentorg.graph --poisoned
ruff check agentorg
```
Expected: `24 passed`; then `status=blocked` and `security verdict=block, blocking=2`; then `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add agentorg/agents/security.py tests/test_agent_fallbacks.py
git commit -m "feat(security): real scanners behind the deterministic verdict"
```

---

## Task 6: Reviewer agent calls a real model

**Files:**
- Modify: `agentorg/agents/reviewer.py` (whole file)
- Test: `tests/test_agent_fallbacks.py` (append)

**Interfaces:**
- Consumes: `llm.structured(ReviewResult, ...)` from Task 2.
- Produces: `reviewer.run(state: RunState) -> ReviewResult` — signature unchanged.

**Do NOT touch the loop in `graph.py`.** It is already wired and capped by `config.MAX_REVISION_LOOPS` (3).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_agent_fallbacks.py`:

```python
from agentorg.agents import reviewer
from agentorg.graph import run_pipeline


def test_reviewer_falls_back_to_fixture_without_a_model(monkeypatch):
    monkeypatch.setattr(config, "LLM_DISABLED", True)
    state = _poisoned_dev_state()
    result = reviewer.run(state)
    assert result.verdict in ("approve", "changes_requested")


def test_reviewer_can_request_changes(monkeypatch):
    from agentorg.common import llm

    monkeypatch.setattr(config, "LLM_DISABLED", False)
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "_complete", lambda s, u: (
        '{"verdict": "changes_requested", "comments": [], '
        '"must_fix": ["no 429 branch"]}'
    ))
    result = reviewer.run(_poisoned_dev_state())
    assert result.verdict == "changes_requested"
    assert result.must_fix == ["no 429 branch"]


def test_the_revision_loop_terminates(monkeypatch):
    """A reviewer that never approves must still stop at MAX_REVISION_LOOPS."""
    from agentorg.common import llm

    monkeypatch.setattr(config, "LLM_DISABLED", False)
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "_complete", lambda s, u: (
        '{"verdict": "changes_requested", "comments": [], "must_fix": ["again"]}'
        if "DIFF UNDER REVIEW" in u else
        '{"branch": "", "diff": "d", "summary": "s", "files_changed": ["app/auth.py"]}'
    ))
    state = run_pipeline("CLEAN-1", "Add a per-IP login rate limit.")
    assert state.revision_count <= config.MAX_REVISION_LOOPS
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_agent_fallbacks.py -q`
Expected: FAIL — `test_reviewer_can_request_changes` fails because the stub always returns the approving fixture.

- [ ] **Step 3: Rewrite the reviewer**

Replace the whole of `agentorg/agents/reviewer.py` with:

```python
"""Reviewer agent — approve or request changes on a DevResult.

OWNER: Sorour.

When the verdict is changes_requested the graph loops back to the developer,
capped by config.MAX_REVISION_LOOPS. That loop lives in graph.py; this file
only produces the verdict.
"""

from ..state import RunState, ReviewResult
from ..common import llm
from .. import fixtures_loader

SYSTEM_PROMPT = """You are the Reviewer in a CI/CD pipeline. Read the unified
diff and judge whether it correctly and safely implements the plan. Respond with
ONE JSON object and nothing else. Shape:
{
  "verdict": "approve" | "changes_requested",
  "comments": [{"file": "<path>", "line": <int>, "note": "<text>"}],
  "must_fix": ["<blocking issue to fix>", ...]
}
Use "changes_requested" ONLY for real correctness or safety problems, and then
list each one in must_fix. If the diff is acceptable, return "approve" with an
empty must_fix. Do not request changes for style nitpicks."""


def _prompt(state: RunState) -> str:
    diff = state.dev.diff if state.dev else ""
    tasks = "\n- ".join(state.plan.tasks) if state.plan else ""
    return f"PLAN TASKS:\n- {tasks}\n\nDIFF UNDER REVIEW:\n{diff}"


def run(state: RunState) -> ReviewResult:
    """Review the diff. Returns the fixture verdict if no model is available."""
    result = llm.structured(ReviewResult, SYSTEM_PROMPT, _prompt(state))
    return result if result is not None else fixtures_loader.review()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_agent_fallbacks.py -q`
Expected: `14 passed`

- [ ] **Step 5: Confirm the full suite and both pipeline paths**

```bash
env -u GITHUB_TOKEN -u DEMO_REPO LLM_DISABLED=true python -m pytest -q
env -u GITHUB_TOKEN -u DEMO_REPO LLM_DISABLED=true python -m agentorg.graph
ruff check agentorg
```
Expected: `27 passed`; then `status=promoted`; then `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add agentorg/agents/reviewer.py tests/test_agent_fallbacks.py
git commit -m "feat(reviewer): real model call with fixture fallback"
```

---

## Task 7: Real human gates — interactive prompt and an async CLI

**Files:**
- Modify: `agentorg/graph.py` (gate routing)
- Create: `agentorg/gates_cli.py`
- Test: `tests/test_gates_cli.py`

**Interfaces:**
- Consumes: `gates.pause(state, gate) -> pathlib.Path` and `gates.resume(run_id, decision) -> RunState`, both already implemented in `agentorg/gates.py`.
- Produces: `python -m agentorg.gates_cli list` and `python -m agentorg.gates_cli resume <run_id> --gate <g> --decision <d> --by <who> [--reason <text>]`.

**Do NOT rename `pause` or `resume`.** The week-3 UI calls them directly.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_gates_cli.py`:

```python
"""Human gate behaviour: interactive halt and async resume. Owner: Sorour."""

import builtins

import pytest

from agentorg import gates, graph
from agentorg.common import config
from agentorg.state import HumanDecision, RunState


@pytest.fixture(autouse=True)
def _no_model(monkeypatch):
    monkeypatch.setattr(config, "LLM_DISABLED", True)


def test_rejecting_gate1_stops_the_run(monkeypatch):
    monkeypatch.setattr(builtins, "input", lambda *a: "r")
    state = graph.run_pipeline("CLEAN-1", "Add a rate limit.", auto_approve=False)
    assert state.status == "rejected"
    assert state.dev is None, "a gate-1 reject must stop before the developer runs"


def test_approving_every_gate_promotes(monkeypatch):
    monkeypatch.setattr(builtins, "input", lambda *a: "a")
    state = graph.run_pipeline("CLEAN-1", "Add a rate limit.", auto_approve=False)
    assert state.status == "promoted"
    assert [d.decision for d in state.decisions] == ["approved"] * 3


def test_auto_approve_still_works():
    state = graph.run_pipeline("CLEAN-1", "Add a rate limit.")
    assert state.status == "promoted"


def test_resume_records_a_rejection():
    state = RunState(ticket_id="CLEAN-1", ticket_text="Add a rate limit.")
    gates.pause(state, "gate1")
    resumed = gates.resume(
        state.run_id,
        HumanDecision(gate="gate1", decision="rejected", by="sorour", reason="wrong plan"),
    )
    assert resumed.status == "rejected"
    assert resumed.decisions[-1].by == "sorour"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_gates_cli.py -q`
Expected: FAIL — `test_rejecting_gate1_stops_the_run` fails; `auto_approve=False` currently skips gates entirely rather than prompting.

- [ ] **Step 3: Add the interactive gate to `graph.py`**

Add `import os` to the imports at the top of `agentorg/graph.py`, then add this immediately after the existing `_auto_gate` function:

```python
def _cli_gate(state: RunState, gate: str) -> HumanDecision:
    """Real gate: pause, ask a human on the terminal, record their decision."""
    path = gates.pause(state, gate)
    print(f"\n[{gate}] paused. state saved -> {path}")
    answer = input(f"[{gate}] approve / reject? ").strip().lower()
    decision = "approved" if answer.startswith("a") else "rejected"
    return HumanDecision(gate=gate, decision=decision,
                         by=os.environ.get("USER", "human"))
```

- [ ] **Step 4: Route the three gates through the selected function**

In `run_pipeline`, immediately after the line `state = RunState(...)` and its `_log(...)` call, add:

```python
    gate = _auto_gate if auto_approve else _cli_gate
```

Then replace each of the three existing gate blocks. Gate 1 currently reads:

```python
    # 2. GATE 1 -------------------------------------------------------------
    if auto_approve:
        state.decisions.append(_auto_gate(state, "gate1"))
```

Replace with:

```python
    # 2. GATE 1 -------------------------------------------------------------
    d1 = gate(state, "gate1")
    state.decisions.append(d1)
    if d1.decision == "rejected":
        state.status = "rejected"
        _log(state, "human", "gate1", "rejected", summary="run stopped at gate 1")
        return state
```

Gate 2 currently reads:

```python
    # 6. GATE 2 -------------------------------------------------------------
    if auto_approve:
        state.decisions.append(_auto_gate(state, "gate2"))
```

Replace with:

```python
    # 6. GATE 2 -------------------------------------------------------------
    d2 = gate(state, "gate2")
    state.decisions.append(d2)
    if d2.decision == "rejected":
        state.status = "rejected"
        _log(state, "human", "gate2", "rejected", summary="run stopped at gate 2")
        return state
```

Gate 3 currently reads:

```python
    # 8. GATE 3 + PROMOTE ---------------------------------------------------
    if auto_approve:
        state.decisions.append(_auto_gate(state, "gate3"))
    state.status = "promoted"
```

Replace with:

```python
    # 8. GATE 3 + PROMOTE ---------------------------------------------------
    d3 = gate(state, "gate3")
    state.decisions.append(d3)
    if d3.decision == "rejected":
        state.status = "rejected"
        _log(state, "human", "gate3", "rejected", summary="run stopped at gate 3")
        return state
    state.status = "promoted"
```

- [ ] **Step 5: Create the async resume CLI**

Create `agentorg/gates_cli.py`:

```python
"""Record a human decision against a paused run. OWNER: Sorour.

    python -m agentorg.gates_cli list
    python -m agentorg.gates_cli resume <run_id> --gate gate1 \
        --decision approved --by sorour --reason "plan looks right"

The week-3 UI is buttons over exactly these calls.
"""

import argparse
import pathlib

from .state import HumanDecision
from . import gates

_RUNS = pathlib.Path(__file__).resolve().parent.parent / "runs"


def _list() -> None:
    for path in sorted(_RUNS.glob("*.state.json")):
        print(path.name.removesuffix(".state.json"))


def _resume(args: argparse.Namespace) -> None:
    decision = HumanDecision(gate=args.gate, decision=args.decision,
                             by=args.by, reason=args.reason)
    state = gates.resume(args.run_id, decision)
    print(f"run_id={state.run_id} gate={args.gate} "
          f"decision={args.decision} status={state.status}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="agentorg.gates_cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    resume = sub.add_parser("resume")
    resume.add_argument("run_id")
    resume.add_argument("--gate", required=True,
                        choices=["gate1", "gate2", "gate3"])
    resume.add_argument("--decision", required=True,
                        choices=["approved", "rejected", "overridden"])
    resume.add_argument("--by", required=True)
    resume.add_argument("--reason", default="")
    args = parser.parse_args()
    if args.cmd == "list":
        _list()
    else:
        _resume(args)


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest tests/test_gates_cli.py -q`
Expected: `4 passed`

- [ ] **Step 7: Exercise the CLI by hand**

```bash
LLM_DISABLED=true python -m agentorg.graph >/dev/null
python -m agentorg.gates_cli list | tail -1
```
Expected: a run id prints. Then, substituting that id:

```bash
python -m agentorg.gates_cli resume <run_id> --gate gate1 \
    --decision rejected --by sorour --reason "wrong plan"
```
Expected: `run_id=<id> gate=gate1 decision=rejected status=rejected`

- [ ] **Step 8: Confirm the suite and lint**

```bash
env -u GITHUB_TOKEN -u DEMO_REPO LLM_DISABLED=true python -m pytest -q
ruff check agentorg
```
Expected: `31 passed`, then `All checks passed!`

- [ ] **Step 9: Commit**

```bash
git add agentorg/graph.py agentorg/gates_cli.py tests/test_gates_cli.py
git commit -m "feat(gates): interactive gate halts on reject, plus async resume CLI"
```

---

## Task 8: CI becomes a real gate — lint, test, scan

**Files:**
- Modify: `.github/workflows/ci.yml` (whole file)

**Interfaces:**
- Consumes: `ruff` from Task 1; `run_all_scanners(dev)` from `agentorg/security/__init__.py`.
- Produces: three GitHub checks named `lint`, `test`, `scan` on every PR.

**`LLM_DISABLED: "true"` is set on the `test` job.** Without it the runner would attempt Bedrock calls it has no credentials for. This is the constraint that makes Tasks 3–6 safe in CI.

**The `scan` job installs gitleaks.** Habiba's PR #3 replaces the scanner stubs with real `subprocess.run(["gitleaks", ...])` calls, and an absent binary raises `FileNotFoundError` — unhandled in her code. Sorour's Task 5 survives that because it wraps `run_all_scanners` in `try/except`; this CI job calls it directly and would go permanently red. Installing the binary keeps the check honest instead of neutered.

**Never let a missing scanner become an empty finding list.** If `run_all_scanners` returned `[]` when gitleaks is absent, `compute_security_verdict([])` returns `("pass", [])` and the poisoned ticket silently stops blocking. A missing binary must fail loudly, never quietly pass.

**Sequencing:** if Habiba's PR #3 has already merged, install gitleaks locally first (`brew install gitleaks`) or Step 1 will raise `FileNotFoundError` rather than print `SCAN OK`. If it has not merged, Step 1 passes against the stub and the CI job passes against the real binary — both are correct.

- [ ] **Step 1: Verify the scan assertion passes locally first**

```bash
python - <<'PY'
from agentorg.state import DevResult
from agentorg.security import run_all_scanners
dev = DevResult.model_validate_json(open("fixtures/dev_result_poisoned.json").read())
findings = run_all_scanners(dev)
crit = [f for f in findings if f.severity == "critical"]
print(f"{len(findings)} findings, {len(crit)} critical")
assert len(crit) >= 2
print("SCAN OK")
PY
```
Expected: output ends with `SCAN OK`

- [ ] **Step 2: Replace the workflow**

Overwrite `.github/workflows/ci.yml` with:

```yaml
# CI — runs on every PR. OWNER: Mariam.
#
# Three gates: lint (ruff), test (pytest on the frozen contract), and scan
# (the security wrappers over the poisoned fixture). The runner has no AWS
# credentials, so LLM_DISABLED pins every agent to its fixture.

name: ci

on:
  push:
    branches: [main]
  pull_request:

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install
        run: pip install -e ".[dev]"
      - name: Ruff lint
        run: ruff check agentorg

  test:
    runs-on: ubuntu-latest
    env:
      LLM_DISABLED: "true"
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install
        run: pip install -e ".[dev]"
      - name: Regenerate + validate fixtures
        run: python make_fixtures.py
      - name: Run tests
        run: pytest -q

  scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install
        run: pip install -e ".[dev]"
      # The scanner wrappers shell out to real binaries. Without gitleaks on
      # PATH the wrapper raises FileNotFoundError and this job is meaningless.
      - name: Install gitleaks
        run: |
          curl -sSfL -o /tmp/gitleaks.tar.gz \
            https://github.com/gitleaks/gitleaks/releases/download/v8.21.2/gitleaks_8.21.2_linux_x64.tar.gz
          tar -xzf /tmp/gitleaks.tar.gz -C /tmp gitleaks
          sudo mv /tmp/gitleaks /usr/local/bin/gitleaks
          gitleaks version
      - name: Run scanners on the poisoned diff
        run: |
          python - <<'PY'
          from agentorg.state import DevResult
          from agentorg.security import run_all_scanners

          dev = DevResult.model_validate_json(
              open("fixtures/dev_result_poisoned.json").read())
          findings = run_all_scanners(dev)
          criticals = [f for f in findings if f.severity == "critical"]
          print(f"{len(findings)} findings, {len(criticals)} critical")
          assert len(criticals) >= 2, "expected >=2 critical findings on poisoned diff"
          print("SCAN OK")
          PY
```

- [ ] **Step 3: Commit and push on a branch so CI actually runs**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add lint and scan jobs alongside test"
```

- [ ] **Step 4: Confirm three checks appear and pass**

After pushing the branch and opening a PR:

```bash
gh pr checks <pr-number>
```
Expected: three rows — `lint`, `test`, `scan` — each `pass`.

If `test` fails on a missing AWS credential, `LLM_DISABLED` is not reaching the job; check the `env:` block sits on the job, not on a step.

---

## Task 9: Offline mode — local git branch and a NOTES file

**Files:**
- Modify: `agentorg/common/config.py` (append only)
- Modify: `agentorg/github_ops.py` (the two `_use_local()` branches)
- Test: `tests/test_offline_mode.py`

**Interfaces:**
- Consumes: `_use_local()`, `_short_sha(text)` — both already in `github_ops.py`.
- Produces: `open_pr` writes a real local git branch when offline; `post_comment` appends to `config.OFFLINE_NOTES`.

**Note on `_use_local()`:** it is already true both when `OFFLINE=true` and when credentials are missing. Keep that behaviour — the offline git path must work in both cases, since CI has no token either.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_offline_mode.py`:

```python
"""Offline mode: a real local branch and a NOTES file, no network. Owner: Mariam."""

import subprocess

import pytest

from agentorg import github_ops
from agentorg.common import config
from agentorg.state import DevResult, RunState


@pytest.fixture
def offline(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OFFLINE", True)
    monkeypatch.setattr(config, "OFFLINE_REPO", str(tmp_path / "offline-demo"))
    monkeypatch.setattr(config, "OFFLINE_NOTES", str(tmp_path / "offline-demo" / "NOTES.md"))
    return tmp_path / "offline-demo"


def _state() -> RunState:
    state = RunState(ticket_id="POISON-1", ticket_text="Add a rate limit.")
    state.dev = DevResult(
        branch="",
        diff='AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"\n',
        summary="adds a limiter",
        files_changed=["app/auth.py"],
    )
    return state


def test_open_pr_creates_a_real_local_branch(offline):
    state = _state()
    dev = github_ops.open_pr(state)

    assert dev.pr_url == f"local://{dev.branch}"
    branches = subprocess.run(
        ["git", "branch", "--list", "agent-org/*"],
        cwd=offline, capture_output=True, text=True, check=True,
    ).stdout
    assert dev.branch in branches


def test_open_pr_commits_the_diff(offline):
    state = _state()
    github_ops.open_pr(state)
    committed = (offline / "changes" / "POISON-1.diff").read_text()
    assert "AKIAIOSFODNN7EXAMPLE" in committed


def test_open_pr_is_rerun_safe(offline):
    state = _state()
    first = github_ops.open_pr(state)
    second = github_ops.open_pr(state)
    assert first.branch == second.branch


def test_post_comment_appends_to_notes(offline):
    state = _state()
    github_ops.open_pr(state)
    ref = github_ops.post_comment(state, "Blocked: hardcoded AWS key.")

    notes = (offline / "NOTES.md").read_text()
    assert "POISON-1" in notes
    assert "Blocked: hardcoded AWS key." in notes
    assert ref.startswith("local://")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_offline_mode.py -q`
Expected: FAIL — `AttributeError: module 'agentorg.common.config' has no attribute 'OFFLINE_REPO'`

- [ ] **Step 3: Add the config knobs**

Append to `agentorg/common/config.py`:

```python
# Offline demo workspace (Mariam) -------------------------------------------
OFFLINE_REPO = os.environ.get("OFFLINE_REPO", "runs/offline-demo")
OFFLINE_NOTES = os.environ.get("OFFLINE_NOTES", "runs/offline-demo/NOTES.md")
```

- [ ] **Step 4: Add the git helpers to `github_ops.py`**

Add `import os` and `import subprocess` to the imports at the top of `agentorg/github_ops.py`, then add these two helpers immediately above `open_pr`:

```python
def _git(*args: str, cwd: str) -> None:
    """Run a git command in cwd, raising on failure."""
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   capture_output=True, text=True)


def _ensure_offline_repo() -> str:
    """Create the offline demo repo with a main branch if it doesn't exist."""
    path = config.OFFLINE_REPO
    os.makedirs(path, exist_ok=True)
    if not os.path.isdir(os.path.join(path, ".git")):
        _git("init", "-b", "main", cwd=path)
        _git("config", "user.email", "agentorg@example.com", cwd=path)
        _git("config", "user.name", "Agent Org", cwd=path)
        with open(os.path.join(path, "README.md"), "w") as fh:
            fh.write("# offline demo\n")
        _git("add", "README.md", cwd=path)
        _git("commit", "-m", "init offline demo repo", cwd=path)
    return path
```

- [ ] **Step 5: Implement the offline branch of `open_pr`**

In `open_pr`, this block currently reads:

```python
    if _use_local():
        # No network (or no credentials): keep the graph green with a local ref.
        dev.pr_url = f"local://{branch}"
        return dev
```

Replace it with:

```python
    if _use_local():
        # No network (or no credentials): do the same work against a local repo.
        path = _ensure_offline_repo()
        _git("checkout", "main", cwd=path)
        # -B resets the branch if a prior run created it, so re-runs are safe.
        _git("checkout", "-B", branch, cwd=path)
        os.makedirs(os.path.join(path, "changes"), exist_ok=True)
        diff_file = os.path.join("changes", f"{state.ticket_id}.diff")
        with open(os.path.join(path, diff_file), "w") as fh:
            fh.write(dev.diff)
        _git("add", diff_file, cwd=path)
        _git("commit", "-m", f"{state.ticket_id}: {dev.summary}", cwd=path)
        dev.pr_url = f"local://{branch}"
        return dev
```

- [ ] **Step 6: Implement the offline branch of `post_comment`**

In `post_comment`, this block currently reads:

```python
    if _use_local():
        # No network (or no credentials): hand back a local ref, same as open_pr.
        return f"comment://{state.run_id}"
```

Replace it with:

```python
    if _use_local():
        # No network (or no credentials): append the reason to a local NOTES file.
        os.makedirs(os.path.dirname(config.OFFLINE_NOTES), exist_ok=True)
        with open(config.OFFLINE_NOTES, "a") as fh:
            fh.write(f"\n## {state.ticket_id} ({state.run_id})\n{body}\n")
        return f"local://{config.OFFLINE_NOTES}"
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `python -m pytest tests/test_offline_mode.py -q`
Expected: `4 passed`

- [ ] **Step 8: Prove the whole pipeline runs offline**

```bash
rm -rf runs/offline-demo
OFFLINE=true LLM_DISABLED=true python -m agentorg.graph --poisoned
git -C runs/offline-demo branch --list 'agent-org/*'
cat runs/offline-demo/NOTES.md
```
Expected: `status=blocked` and `blocking=2`; a branch named `agent-org/DEMO-POISON-<short_sha>`; and a `## DEMO-POISON` section in NOTES.md containing the block reason.

- [ ] **Step 9: Confirm the suite and lint**

```bash
env -u GITHUB_TOKEN -u DEMO_REPO LLM_DISABLED=true python -m pytest -q
ruff check agentorg
```
Expected: `35 passed`, then `All checks passed!`

- [ ] **Step 10: Commit**

```bash
git add agentorg/github_ops.py agentorg/common/config.py tests/test_offline_mode.py
git commit -m "feat(github_ops): real offline mode with local git and NOTES"
```

---

## Task 10: The block reason can never crash a blocked run

**Files:**
- Modify: `agentorg/github_ops.py` (`post_comment` online branch)
- Test: `tests/test_offline_mode.py` (append)

**Interfaces:**
- Consumes: `_use_local()`, `_repo()` from `github_ops.py`.
- Produces: `post_comment` returns a ref string in every case and never raises.

**Why this matters:** `graph.py` calls `post_comment(state, state.security.explanation)` immediately after setting `status="blocked"`. If that call raises, a correctly-blocked run turns into a traceback — on stage, on Aug 25.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_offline_mode.py`:

```python
def test_post_comment_never_raises_without_a_pr(monkeypatch, capsys):
    """A missing PR must surface the reason, not crash the blocked run."""
    class _NoPulls:
        totalCount = 0

    class _Repo:
        owner = type("O", (), {"login": "someone"})()

        def get_pulls(self, **kwargs):
            return _NoPulls()

    monkeypatch.setattr(config, "OFFLINE", False)
    monkeypatch.setattr(config, "GITHUB_TOKEN", "x")
    monkeypatch.setattr(config, "GITHUB_REPO", "someone/auth-service")
    monkeypatch.setattr(github_ops, "_repo", lambda: _Repo())

    state = _state()
    state.dev.branch = "agent-org/POISON-1-abc1234"
    ref = github_ops.post_comment(state, "Blocked: hardcoded AWS key.")

    assert ref.startswith("comment://")
    assert "Blocked: hardcoded AWS key." in capsys.readouterr().out
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_offline_mode.py -q`
Expected: FAIL — `RuntimeError: no open PR for branch ... to comment on`

- [ ] **Step 3: Harden the online branch of `post_comment`**

In `post_comment`, this block currently reads:

```python
    repo = _repo()

    branch = state.dev.branch if state.dev else ""

    pulls = repo.get_pulls(
        state="open",
        head=f"{repo.owner.login}:{branch}",
    )

    if pulls.totalCount == 0:
        raise RuntimeError(
            f"no open PR for branch {branch!r} to comment on"
        )

    issue = repo.get_issue(pulls[0].number)
    comment = issue.create_comment(body)

    return comment.html_url
```

Replace it with:

```python
    repo = _repo()
    branch = state.dev.branch if state.dev and state.dev.branch else ""
    pulls = repo.get_pulls(state="open", head=f"{repo.owner.login}:{branch}") if branch else None

    if not branch or pulls is None or pulls.totalCount == 0:
        # No PR to attach to. Surface the reason rather than crashing a run
        # that has already, correctly, been blocked.
        print(f"[post_comment] no PR for {branch!r}; reason: {body}")
        return f"comment://{state.run_id}"

    issue = repo.get_issue(pulls[0].number)
    return issue.create_comment(body).html_url
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_offline_mode.py -q`
Expected: `5 passed`

- [ ] **Step 5: Confirm the suite and lint**

```bash
env -u GITHUB_TOKEN -u DEMO_REPO LLM_DISABLED=true python -m pytest -q
ruff check agentorg
```
Expected: `36 passed`, then `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add agentorg/github_ops.py tests/test_offline_mode.py
git commit -m "fix(github_ops): a missing PR can no longer crash a blocked run"
```

---

## Task 11: The Friday gate — poisoned blocks 10 out of 10

**Files:**
- Modify: `docs/plan/week1-verification-log.md` → rename usage: append a Week 2 section (do not delete the Week 1 content)

**Interfaces:**
- Consumes: everything above.
- Produces: recorded evidence that the hard deadline is met.

**This task is verification, not new code.** If any run flips, stop and fix the cause before continuing — do not average across runs, and do not create `tests/test_block_determinism.py` (that file belongs to Aya's lane).

- [ ] **Step 1: Run the poisoned path ten times with no model**

```bash
for i in $(seq 1 10); do
  env -u GITHUB_TOKEN -u DEMO_REPO LLM_DISABLED=true \
    python -m agentorg.graph --poisoned | grep -E 'status=|blocking='
done
```
Expected: ten identical pairs — `status=blocked` and `security verdict=block, blocking=2`. Ten out of ten.

- [ ] **Step 2: Run the poisoned path ten times offline**

```bash
rm -rf runs/offline-demo
for i in $(seq 1 10); do
  OFFLINE=true LLM_DISABLED=true python -m agentorg.graph --poisoned | grep -E 'status='
done
grep -c '^## DEMO-POISON' runs/offline-demo/NOTES.md
```
Expected: ten `status=blocked` lines, and a NOTES.md count of `10` — every blocked run explained itself.

- [ ] **Step 3: Run the poisoned path five times against the live model and repo**

Requires `.env` with `GITHUB_TOKEN` and `DEMO_REPO`. This makes real Bedrock calls and opens real PRs.

```bash
set -a && . ./.env && set +a
for i in $(seq 1 5); do
  python -m agentorg.graph --poisoned | grep -E 'status=|blocking='
done
```
Expected: five `status=blocked` / `blocking=2` pairs, and a block comment on each corresponding PR in `auth-service`.

- [ ] **Step 4: Confirm the clean path still promotes with a live model**

```bash
set -a && . ./.env && set +a
python -m agentorg.graph
```
Expected: `status=promoted`, `security verdict=pass, blocking=0`, and a real PR URL.

- [ ] **Step 5: Record the result**

Append a `# Week 2` section to `docs/plan/week1-verification-log.md` recording: the 10/10 offline result, the 10/10 no-model result, the 5/5 live result, the clean-path result, and the PR URLs produced. Follow the format of the existing Week 1 sections.

- [ ] **Step 6: Commit**

```bash
git add docs/plan/week1-verification-log.md
git commit -m "docs(plan): record week 2 determinism verification"
```

---

## Self-Review

**Spec coverage.** Sorour's week 2 — planner (Task 3), developer (4), reviewer (6), security (5), gates (7), integration + 10/10 (11). Mariam's week 2 — CI lint/test/scan (8), offline mode (9), block comment (10). The two blockers I raised from the repo audit are Task 1 (deps, lint) and the `LLM_DISABLED` wiring inside Task 8. Habiba's PR #3 is a dependency, not a task here — it is reviewed and merged separately, and Task 5's `try/except` means neither lane is hard-blocked by it.

**Deliberate deviations from the lane plans**, each of which a reviewer should accept rather than flag:
- The lane plans duplicate `_extract_json` in three agent files. Task 2 replaces that with one shared helper.
- The lane plans give only the developer and security agents a fallback. Every agent gets one here, because CI has no AWS credentials and would otherwise fail exactly as PR #2 did.
- Security (Task 5) is sequenced before the reviewer (Task 6), against the calendar order in the lane plan. It is the only task the Friday deadline depends on and it needs nothing from the reviewer.

**Type consistency.** `llm.structured(model_cls, system_prompt, user_prompt)` and `llm.text(system_prompt, user_prompt)` are used with those exact signatures in Tasks 3–6. `llm._complete` is the seam every test monkeypatches. `_use_local()`, `_repo()`, `_short_sha()` in Tasks 9–10 match what Week 1 actually left in `github_ops.py`. `SecurityResult.blocking`, `ReviewResult.verdict`, and `HumanDecision.decision` use the frozen spellings throughout.

**Test counts** in the "Expected" lines assume tasks run in order and are cumulative: 3 (existing) → 13 → 15 → 20 → 24 → 27 → 31 → 35 → 36. If you execute out of order, the totals shift; the per-file counts do not.
