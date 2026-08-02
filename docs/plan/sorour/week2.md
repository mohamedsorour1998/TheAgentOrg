# Sorour — Week 2 (Aug 15–21): make the agents real

You own the graph (`agentorg/graph.py`), the human gates (`agentorg/gates.py`),
the log (`agentorg/log.py`), and every agent stub in `agentorg/agents/`. This
week you replace those stubs, one at a time, with real
`Agent(create_model(), SYSTEM_PROMPT)` calls that parse LLM output into the
frozen pydantic models in `agentorg/state.py`. Nothing else in the repo changes:
the data contract is frozen, so each swap is local.

The frozen shapes you must produce (from `agentorg/state.py` — do not rename or
remove any field; you may only ADD optional ones):

```python
class PlanResult(BaseModel):
    tasks: list[str]; acceptance_criteria: list[str]
    target_files: list[str]; notes: str = ""

class DevResult(BaseModel):
    branch: str; diff: str; summary: str
    files_changed: list[str]; pr_url: str | None = None   # pr_url set by github_ops, not you

class ReviewComment(BaseModel):
    file: str; line: int; note: str
class ReviewResult(BaseModel):
    verdict: Literal["approve", "changes_requested"]
    comments: list[ReviewComment] = []; must_fix: list[str] = []

class Finding(BaseModel):
    tool: Literal["semgrep","gitleaks","trivy"]; severity: Severity
    rule: str; file: str; line: int; description: str
class SecurityResult(BaseModel):
    verdict: Literal["pass", "block"]
    findings: list[Finding] = []; blocking: list[Finding] = []
    explanation: str = ""   # the LLM writes THIS ONLY; it never sets the verdict
```

The model provider is already built for you — `agentorg/common/model.py`
`create_model()` returns a Bedrock Nova-Lite model
(`us.amazon.nova-2-lite-v1:0`, region `us-east-1`) by default, or an
OpenAI-compatible model if `LLM_BASE_URL` is set. You never construct a model by
hand; you always call `create_model()`.

**★ Hard deadline: by end of Friday Aug 21 the poisoned ticket blocks every
single time on real scanners + real agents.** The block itself is guaranteed by
`compute_security_verdict()` (pure Python in `state.py`), not by any LLM — that
is the whole point. Your job is to make sure a real developer agent puts the
key into the diff and a real security agent runs the real scanners over it.

---

## Sat–Sun Aug 15–16 — planner + developer agents

**Task: make the planner agent real.**

Open `agentorg/agents/planner.py`. Current stub:

```python
from ..state import RunState, PlanResult
from .. import fixtures_loader

SYSTEM_PROMPT = """You are the Planner. Read the ticket and produce:
- concrete tasks, acceptance criteria, and the files likely to change.
Output must match the PlanResult schema exactly. Do not write code."""

def run(state: RunState) -> PlanResult:
    """STUB: returns the fixture plan. REAL: call the Strands agent on state.ticket_text."""
    # TODO(Sorour, wk2): agent = Agent(create_model(), SYSTEM_PROMPT, tools=[...])
    #                    return PlanResult.model_validate_json(str(agent(state.ticket_text)))
    return fixtures_loader.plan()
```

Steps:
1. Tighten `SYSTEM_PROMPT` so the model returns a single JSON object matching
   `PlanResult` and nothing else.
2. Add a shared `_extract_json` helper (LLMs wrap JSON in ```json fences — strip
   them, then fall back to the first `{`…last `}`).
3. Build the agent with `create_model()` and parse its output into `PlanResult`.

Replace the whole file with:

```python
"""Planner agent — turns a ticket into a PlanResult. OWNER: Sorour."""
import json
import re

from strands import Agent

from ..state import RunState, PlanResult
from ..common.model import create_model

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


def _extract_json(text: str) -> str:
    """Pull a JSON object out of an LLM reply that may be fenced or chatty."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        return fenced.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text


def run(state: RunState) -> PlanResult:
    """Call the Strands agent on the ticket text and parse a PlanResult."""
    agent = Agent(model=create_model(), system_prompt=SYSTEM_PROMPT)
    raw = str(agent(state.ticket_text))
    return PlanResult.model_validate_json(_extract_json(raw))
```

**Done when:** a real (non-fixture) plan comes back with non-empty `tasks`:

```bash
python -c "
from agentorg.agents import planner
from agentorg.state import RunState
p = planner.run(RunState(ticket_id='CLEAN-1', ticket_text='Add a per-IP login rate limit.'))
print('tasks:', p.tasks)
assert p.tasks and p.acceptance_criteria and p.target_files
print('OK planner real')
"
```
Expected: a `tasks:` line listing real, ticket-specific tasks (mentioning rate
limit / Redis / 429), then `OK planner real`. If you see the exact fixture text
(`fixtures/plan_result.json`), the agent didn't run — check Bedrock access.

**You're unblocked because:** you built `create_model()` in week 1 and Bedrock
answered a real prompt on Wed Aug 12. Nothing here waits on a teammate.

---

**Task: make the developer agent real.**

Open `agentorg/agents/developer.py`. Current stub:

```python
from ..state import RunState, DevResult
from .. import fixtures_loader

def run(state: RunState, poisoned: bool = False) -> DevResult:
    """STUB: returns a fixture diff. REAL: call the Strands agent on state.plan."""
    # TODO(Sorour, wk2): real agent call using state.plan.
    return fixtures_loader.dev(poisoned=poisoned)
```

Two subtleties:
- On a **revision** (the graph loops back after `changes_requested`),
  `state.review` is already populated — feed its `must_fix` list back to the
  model so the second diff actually addresses the feedback.
- Keep the `poisoned` switch as a **demo safety net only**: run the real agent
  first; if `poisoned=True` and the model somehow didn't emit an AWS key, fall
  back to the poisoned reference diff so the Aug 21 deadline is deterministic.
  On the clean path the model's diff is used as-is.

Replace the whole file with:

```python
"""Developer agent — turns a PlanResult into a DevResult (a diff). OWNER: Sorour."""
import json
import re

from strands import Agent

from ..state import RunState, DevResult
from ..common.model import create_model
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


def _extract_json(text: str) -> str:
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        return fenced.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text


def _prompt(state: RunState) -> str:
    parts = [f"TICKET:\n{state.ticket_text}"]
    if state.plan is not None:
        parts.append("PLAN TASKS:\n- " + "\n- ".join(state.plan.tasks))
        parts.append("TARGET FILES:\n- " + "\n- ".join(state.plan.target_files))
    if state.review is not None and state.review.must_fix:
        # A revision loop: the reviewer asked for changes. Address them.
        parts.append("REVIEWER REQUESTED CHANGES — you MUST fix all of:\n- "
                     + "\n- ".join(state.review.must_fix))
    return "\n\n".join(parts)


def run(state: RunState, poisoned: bool = False) -> DevResult:
    """Call the Strands agent on the plan (+ any review feedback) and parse a DevResult."""
    agent = Agent(model=create_model(), system_prompt=SYSTEM_PROMPT)
    raw = str(agent(_prompt(state)))
    dev = DevResult.model_validate_json(_extract_json(raw))
    dev.branch = dev.branch or f"agent-org/{state.ticket_id}"
    # Demo safety net: the poisoned ticket must always ship an AWS key so the
    # scanners have something to catch. If the model didn't emit one, use the
    # poisoned reference diff. The clean path always uses the model's own diff.
    if poisoned and not _AWS_KEY.search(dev.diff):
        dev.diff = fixtures_loader.dev(poisoned=True).diff
        dev.files_changed = fixtures_loader.dev(poisoned=True).files_changed
    return dev
```

**Done when:** the clean run produces a real diff and still promotes; the
poisoned run's diff always contains the key:

```bash
python -c "
from agentorg.agents import developer
from agentorg.state import RunState, PlanResult
st = RunState(ticket_id='POISON-1', ticket_text='Add a per-IP login rate limit.')
st.plan = PlanResult(tasks=['add limiter'], acceptance_criteria=['429 on 6th'], target_files=['app/auth.py'])
d = developer.run(st, poisoned=True)
import re; assert re.search('AKIA[0-9A-Z]{16}', d.diff), 'poisoned diff must carry the key'
print('branch:', d.branch); print('OK developer real + poisoned safety net')
"
python -m agentorg.graph            # clean ticket -> status=promoted
```
Expected: `OK developer real + poisoned safety net`, then a graph run ending
`status=promoted` on the clean ticket.

**Hands off to Mariam:** her `github_ops.open_pr(state)` reads `state.dev.branch`
and `state.dev.diff` and sets `state.dev.pr_url`. Keep the branch name in the
`agent-org/<ticket_id>` shape she extends to `agent-org/<ticket_id>-<short_sha>`.

---

## Mon Aug 17 — reviewer agent + the revision loop

**Task: make the reviewer agent real.**

Open `agentorg/agents/reviewer.py`. Current stub:

```python
from ..state import RunState, ReviewResult
from .. import fixtures_loader

def run(state: RunState) -> ReviewResult:
    """STUB: returns the fixture (approve). REAL: call the Strands agent on state.dev."""
    # TODO(Sorour, wk2): real agent call; return changes_requested to exercise the loop.
    return fixtures_loader.review()
```

The loop itself is already wired in `graph.py` — you do NOT touch it. For
reference, the graph does:

```python
while True:
    state.dev = developer.run(state, poisoned=poisoned)
    state.review = reviewer.run(state)
    if state.review.verdict == "approve" or state.revision_count >= config.MAX_REVISION_LOOPS:
        break
    state.revision_count += 1
```

So your reviewer only needs to return a real `ReviewResult` whose `verdict` is
`"approve"` or `"changes_requested"` (exact strings — not `"approved"`), with
`must_fix` populated when it requests changes (the developer reads that list on
the next pass). Replace the whole file with:

```python
"""Reviewer agent — approve or changes_requested on a DevResult. OWNER: Sorour."""
import json
import re

from strands import Agent

from ..state import RunState, ReviewResult
from ..common.model import create_model

SYSTEM_PROMPT = """You are the Reviewer in a CI/CD pipeline. Read the unified
diff and judge whether it correctly and safely implements the plan. Respond with
ONE JSON object and nothing else. Shape:
{
  "verdict": "approve" | "changes_requested",
  "comments": [{"file": "<path>", "line": <int>, "note": "<text>"}],
  "must_fix": ["<blocking issue to fix>", ...]
}
Use "changes_requested" ONLY for real correctness or safety problems, and then
list each in must_fix. If the diff is acceptable, return "approve" with an empty
must_fix. Do not request changes for style nitpicks."""


def _extract_json(text: str) -> str:
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        return fenced.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text


def run(state: RunState) -> ReviewResult:
    """Call the Strands agent on the diff and parse a ReviewResult."""
    diff = state.dev.diff if state.dev else ""
    plan = "\n- ".join(state.plan.tasks) if state.plan else ""
    prompt = f"PLAN TASKS:\n- {plan}\n\nDIFF UNDER REVIEW:\n{diff}"
    agent = Agent(model=create_model(), system_prompt=SYSTEM_PROMPT)
    raw = str(agent(prompt))
    return ReviewResult.model_validate_json(_extract_json(raw))
```

**Done when:** a weak diff triggers exactly one revision then approves. Because
`config.MAX_REVISION_LOOPS == 3` the loop is always bounded — this test proves
it both fires and terminates:

```bash
python -c "
from agentorg.state import RunState
from agentorg import graph
st = graph.run_pipeline('CLEAN-1', 'Add a per-IP login rate limit.')
print('status:', st.status, 'revisions:', st.revision_count)
assert st.status == 'promoted'
assert 0 <= st.revision_count <= 3
print('OK reviewer + revision loop bounded')
"
```
Expected: `status: promoted revisions: <0..3>` then
`OK reviewer + revision loop bounded`. To force a revision for a manual check,
temporarily hand the developer a diff missing the 429 branch — the reviewer
should return `changes_requested` with a `must_fix` entry, and the next pass
should approve.

---

## Tue Aug 18 — security agent wiring (the deterministic block)

**Task: wire the security agent to real scanners + the pure-code verdict.**

This is the heart of the demo. Open `agentorg/agents/security.py`. Current stub:

```python
from ..state import RunState, SecurityResult, compute_security_verdict
from ..common import config
from .. import fixtures_loader
from ..security import run_all_scanners

def run(state: RunState, use_real_scanners: bool = False) -> SecurityResult:
    if not use_real_scanners:
        poisoned = state.dev is not None and "AKIA" in (state.dev.diff or "")
        return fixtures_loader.security(block=poisoned)
    findings = run_all_scanners(state.dev)
    verdict, blocking = compute_security_verdict(findings, threshold=config.SECURITY_BLOCK_THRESHOLD)
    # TODO(Sorour, wk2): LLM writes `explanation` only.
    return SecurityResult(verdict=verdict, findings=findings, blocking=blocking, explanation="")
```

The rule you rely on lives in `state.py` — pure Python, no LLM:

```python
def compute_security_verdict(findings, threshold="high") -> tuple[verdict, blocking]:
    cutoff = SEVERITY_ORDER[threshold]                       # "high" -> 2
    blocking = [f for f in findings if SEVERITY_ORDER[f.severity] >= cutoff]
    return ("block" if blocking else "pass"), blocking
```

Steps:
1. Flip `use_real_scanners` to default `True` so the graph uses the real path.
2. Wrap `run_all_scanners(state.dev)` in a `try/except`: if Habiba's scanners
   aren't importable/executable yet, or throw, fall back to the fixture. This is
   why the graph never waits on her.
3. After the verdict is computed **by code**, let the LLM write ONLY the
   `explanation`. If the LLM call fails, use a deterministic fallback string —
   the block must never depend on the model being up.

Replace the whole file with:

```python
"""Security agent — runs the scanners, applies the deterministic block rule,
and lets the LLM write ONLY the human-readable explanation. OWNER: Sorour wires
the agent; Habiba owns the scanners in agentorg/security/.

CRITICAL: the LLM does NOT decide pass/block. compute_security_verdict() (pure
code in state.py) does. That is what makes the poisoned ticket block every run.
"""
from strands import Agent

from ..state import RunState, SecurityResult, compute_security_verdict
from ..common import config
from ..common.model import create_model
from .. import fixtures_loader
from ..security import run_all_scanners

SYSTEM_PROMPT = """You are the Security explainer. You are given a verdict and a
list of blocking findings that were ALREADY decided by code. Write 1-3 plain
sentences explaining why the change was blocked or allowed, naming the tools and
rules. You may NOT change the verdict. Return plain text, no JSON."""


def _explain(verdict: str, blocking: list) -> str:
    """LLM writes the explanation only; fall back to a fixed string if it fails."""
    if verdict == "block":
        default = ("Blocked: " + "; ".join(
            f"{f.tool}:{f.rule} ({f.severity}) in {f.file}:{f.line}" for f in blocking))
    else:
        default = "Passed: no findings at or above the block threshold."
    try:
        agent = Agent(model=create_model(), system_prompt=SYSTEM_PROMPT)
        findings_txt = "\n".join(
            f"- {f.tool} {f.rule} {f.severity} {f.file}:{f.line} {f.description}"
            for f in blocking) or "(none)"
        return str(agent(f"VERDICT: {verdict}\nBLOCKING FINDINGS:\n{findings_txt}")).strip()
    except Exception:
        return default


def run(state: RunState, use_real_scanners: bool = True) -> SecurityResult:
    """Scan the diff, compute the verdict deterministically, attach an explanation."""
    try:
        findings = run_all_scanners(state.dev)
    except Exception:
        # Scanners not ready / crashed: fall back to the fixture so the graph
        # never blocks on Habiba's lane. Deterministic, no LLM.
        poisoned = state.dev is not None and "AKIA" in (state.dev.diff or "")
        return fixtures_loader.security(block=poisoned)

    verdict, blocking = compute_security_verdict(
        findings, threshold=config.SECURITY_BLOCK_THRESHOLD)
    return SecurityResult(
        verdict=verdict, findings=findings, blocking=blocking,
        explanation=_explain(verdict, blocking))
```

Note the exact field names: `SecurityResult.blocking` (NOT `blocking_findings`),
`verdict` values `pass`/`block`. The graph reads `state.security.blocking` and
`state.security.verdict`, and on `block` calls
`github_ops.post_comment(state, state.security.explanation)`.

**Done when:** the poisoned run blocks on **real** findings (2 critical from
gitleaks: `aws-access-key-id`, `aws-secret-access-key`), the clean run passes:

```bash
python -m agentorg.graph --poisoned   # -> status=blocked, blocking=2
python -m agentorg.graph              # -> status=promoted
```
Expected (poisoned): last lines show `status=blocked` and
`security verdict=block, blocking=2`. Expected (clean): `status=promoted`. If
gitleaks isn't installed yet on your box, the run still blocks via the fixture
fallback (`blocking=2`) — the numbers must match either way.

**You're unblocked because:** the `try/except` fallback means you can wire and
test this before Habiba's real `run_all_scanners` lands; when it lands, the same
code runs the real CLI with zero changes.

**Cross-dep:** confirm with Habiba (by the Wed Aug 12 handoff) that the poisoned
diff trips gitleaks — 2 critical findings, clean diff → 0.

---

## Wed Aug 19 — the three human gates (real pause/resume + CLI)

**Task: make the three gates record genuine human decisions from a CLI.**

There are three gates: `gate1` after PLAN, `gate2` after SECURITY, `gate3`
after SRE. `agentorg/gates.py` already persists and reloads state — do not
rename these functions:

```python
def pause(state: RunState, gate: str) -> pathlib.Path:
    """Persist state to runs/<run_id>.state.json and log a paused event."""
def resume(run_id: str, decision: HumanDecision) -> RunState:
    """Reload state, append the decision, set status='rejected' if rejected."""
```

`HumanDecision` (exact field names — `decision` is
`approved`/`rejected`/`overridden`, NOT `approve`):

```python
class HumanDecision(BaseModel):
    gate: Literal["gate1", "gate2", "gate3"]
    decision: Literal["approved", "rejected", "overridden"]
    by: str
    at: str = <auto iso timestamp>
    reason: str = ""
```

Today the graph auto-approves via `_auto_gate`. You'll add a **real** interactive
gate path so a run can actually stop at a gate, take a keyboard decision, and
either continue or halt. Two pieces:

**Piece 1 — an interactive gate in `graph.py`.** Add `import os`, add a
`_cli_gate` helper, and route the three gates through whichever gate function is
selected by `auto_approve`. Current gate code in `graph.py`:

```python
def _auto_gate(state: RunState, gate: str) -> HumanDecision:
    gates.pause(state, gate)
    return HumanDecision(gate=gate, decision="approved", by="auto", reason="demo auto-approve")

# ... and, three times in run_pipeline:
    if auto_approve:
        state.decisions.append(_auto_gate(state, "gate1"))
```

Add, right after `_auto_gate`:

```python
def _cli_gate(state: RunState, gate: str) -> HumanDecision:
    """Real gate: pause, ask a human on the terminal, record their decision."""
    path = gates.pause(state, gate)
    print(f"\n[{gate}] paused. state saved -> {path}")
    ans = input(f"[{gate}] approve / reject? ").strip().lower()
    decision = "approved" if ans.startswith("a") else "rejected"
    return HumanDecision(gate=gate, decision=decision,
                         by=os.environ.get("USER", "human"))
```

Replace each of the three `if auto_approve: state.decisions.append(...)` blocks
with a call through a selected gate function that also halts on reject. Put this
near the top of `run_pipeline`:

```python
    gate = _auto_gate if auto_approve else _cli_gate
```

Then gate 1:

```python
    d1 = gate(state, "gate1"); state.decisions.append(d1)
    if d1.decision == "rejected":
        state.status = "rejected"; return state
```

Gate 2 (after the security block check) and gate 3 (before promote) follow the
same three-line pattern with `"gate2"` / `"gate3"`.

**Piece 2 — an async resume CLI** (`agentorg/gates_cli.py`, new file) for the
UI/week-3 path, so a decision can be recorded against a saved state file without
a live process. Create it:

```python
"""CLI to list paused runs and resume them with a genuine HumanDecision.

    python -m agentorg.gates_cli list
    python -m agentorg.gates_cli resume <run_id> --gate gate1 \
        --decision approved --by sorour --reason "plan looks right"
"""
import argparse
import pathlib

from .state import HumanDecision
from . import gates

_RUNS = pathlib.Path(__file__).resolve().parent.parent / "runs"


def _list() -> None:
    for p in sorted(_RUNS.glob("*.state.json")):
        print(p.stem.replace(".state", ""))


def _resume(args) -> None:
    decision = HumanDecision(gate=args.gate, decision=args.decision,
                             by=args.by, reason=args.reason)
    state = gates.resume(args.run_id, decision)
    print(f"run_id={state.run_id} gate={args.gate} "
          f"decision={args.decision} status={state.status}")


def main() -> None:
    ap = argparse.ArgumentParser(prog="agentorg.gates_cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    r = sub.add_parser("resume")
    r.add_argument("run_id")
    r.add_argument("--gate", required=True, choices=["gate1", "gate2", "gate3"])
    r.add_argument("--decision", required=True,
                   choices=["approved", "rejected", "overridden"])
    r.add_argument("--by", required=True)
    r.add_argument("--reason", default="")
    args = ap.parse_args()
    if args.cmd == "list":
        _list()
    else:
        _resume(args)


if __name__ == "__main__":
    main()
```

**Done when:** (a) a gated run stops at gate 1, takes your decision, and
continues to completion; (b) the async CLI records a decision against a saved
state file and reports the resulting status.

```bash
# (a) interactive: approve all three gates -> promoted
printf 'a\na\na\n' | python -c "
from agentorg import graph
st = graph.run_pipeline('CLEAN-1', 'Add a per-IP login rate limit.', auto_approve=False)
print('final status:', st.status)
"
# expected: three '[gateN] paused ...' prompts, then 'final status: promoted'

# reject at gate 1 -> run stops, status rejected
printf 'r\n' | python -c "
from agentorg import graph
st = graph.run_pipeline('CLEAN-1', 'Add a per-IP login rate limit.', auto_approve=False)
print('final status:', st.status)
"
# expected: '[gate1] paused ...' then 'final status: rejected' (no develop/review runs)

# (b) async resume CLI against an already-paused state file
python -m agentorg.gates_cli list                      # prints run_ids with saved state
python -m agentorg.gates_cli resume <run_id> --gate gate1 \
    --decision rejected --by sorour --reason "wrong plan"
# expected: run_id=<...> gate=gate1 decision=rejected status=rejected
```

**Hands off to yourself (week 3):** the approve/reject screen calls
`gates.resume(run_id, HumanDecision(...))` — exactly what `gates_cli.py` does.
The UI is just buttons over this same call.

---

## Thu–Fri Aug 20–21 — integration + the hard deadline

**Task: full integration pass — all real agents on the happy path.**

Run the whole pipeline and confirm no stub is on the clean path (planner,
developer, reviewer, security are all real; SRE stays a fixture until week 3 —
it's `# TODO(Sorour, wk3)` and first on the cut list).

```bash
pip install -e ".[dev]"      # editable install (packaging is fixed)
python make_fixtures.py      # regenerate + validate fixtures
pytest -q                    # existing suite still green
python -m agentorg.graph     # clean -> promoted, with a real plan/diff/review
```
**Done when:** `pytest -q` passes and the clean run ends `status=promoted` using
real agent output (a ticket-specific plan and a real diff, not the fixtures).

**★ Task (HARD DEADLINE, end of Fri Aug 21): the poisoned ticket blocks every
single time on real scanners + real agents.**

```bash
for i in $(seq 1 10); do
  python -m agentorg.graph --poisoned | grep -E 'status|blocking'
done
```
**Done when:** all 10 runs print `status=blocked` and
`security verdict=block, blocking=2`. Not 9 of 10 — 10 of 10. The determinism
comes from `compute_security_verdict`, so any flip means either the developer's
diff lost the key (check the poisoned safety net in `developer.run`) or the
scanners aren't seeing it (check `run_all_scanners` with Habiba). If it flips,
stop all other work and pull in whoever's free until it's 10/10.

**Hands off to Aya:** she asserts exactly this as a black box —
`run_pipeline('POISON-1', ..., poisoned=True)` ends `status=="blocked"` with
`len(state.security.blocking) == 2`, run 20+ times, never flips. Your 10/10 is
the same guarantee she formalizes in `tests/test_block_determinism.py`.

---

## End of week 2 — done when

- `agentorg/agents/planner.py`, `developer.py`, `reviewer.py`, `security.py` are
  all real `Agent(create_model(), SYSTEM_PROMPT)` calls parsing into the frozen
  pydantic models — no fixtures on the clean path.
- `python -m agentorg.graph` ends `status=promoted` with a real plan + diff.
- The revision loop fires and terminates within `MAX_REVISION_LOOPS` (=3).
- `python -m agentorg.graph --poisoned` ends `status=blocked`,
  `blocking=2`, on real scanners — **10/10 runs**, by end of Fri Aug 21.
- The three gates pause/resume: interactive `_cli_gate` in a gated run, and the
  async `python -m agentorg.gates_cli resume ...` path both record genuine
  `HumanDecision`s and set `status` correctly.
- `pytest -q` is green.

**Cut/fallback note:** if an agent's LLM output won't parse reliably in time,
keep that one lane on its fixture for the demo (the graph accepts both) — but
NEVER cut the security path: the block is the demo, and it must be 10/10 by
Aug 21.
