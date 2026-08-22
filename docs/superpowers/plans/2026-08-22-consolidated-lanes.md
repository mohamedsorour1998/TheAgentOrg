# Pre-Demo Consolidated Plan — four parallel lanes

> **For agentic workers:** each lane is executed by ONE agent, working alone, in
> the shared working tree at `/Users/sorour/sorour/TheAgentOrg`. Lanes run in
> parallel. **Your lane owns its files outright — never edit a file another lane
> owns.** Steps use checkbox (`- [ ]`) syntax.

**Goal:** close every defect the three audits found, with the deployed pipeline
demo-ready after every commit.

**Architecture:** four lanes drawn by **file ownership**, not by topic, because
`scripts/run_stage.py` is wanted by three separate fixes and `agentorg/state.py` by
two. The contested contract edits were made up front (commit `b32ea5c`) so no lane
needs to touch `state.py` or `timeline.py`'s tables. Each lane commits
independently and must leave the four gates green.

**Tech Stack:** Python 3.12, pydantic v2, Terraform 1.15.8, AWS (Bedrock Nova +
AgentCore, Lambda, EventBridge, DynamoDB, ECR), GitHub Actions, PyGithub.

**Spec:** this document, superseding
`docs/superpowers/plans/2026-08-22-pre-demo-fixes.md` (which held the pre-audit
task numbering). Findings came from three independent audits plus my own probes;
every one below was reproduced before being written down.

---

## Global Constraints — every lane

- **Demo Tue Aug 25 2026.** Today is Aug 22. `main` must stay demo-ready.
  Anything that looks like a crash on a projector outranks polish.
- Python is `.venv-main/bin/python`. Do **not** create a venv; never use
  `.venv-habiba` / `.venv-sorour` / `.venv-testing`.
- **Baseline: 818 passed, 3 skipped.** Must stay green plus your new tests.
- `.venv-main/bin/python -m ruff check agentorg scripts tests` exits 0. **No
  `[tool.ruff]` section, no `# noqa`, no per-file ignores.** `I001`, `BLE001`,
  `ISC004` are ruff 0.16 defaults and fire unselected.
- `actionlint .github/workflows/*.yml` exits 0. `terraform fmt -check -recursive`
  exits 0.
- **Mandatory RED step per test:** name the mutation, apply it, watch the exact
  named test fail, **paste the failure**, revert. A task whose RED step was not run
  is **not done**. Never end your work with a mutation applied — `git diff` last.
- **Numbers in prose come from a command whose output you paste.**
- `agentorg/state.py` is FROZEN and **already carries every field this plan needs**
  (`model_provenance`, `trigger`, and `failed` in `LogEvent.action`). Do not edit it.
- **Broad `except Exception` here is load-bearing.** BLE001 is satisfied by an
  inline `logging` call carrying the traceback; *narrowing the except also satisfies
  it with no logging at all*, so lint blesses the more dangerous option. Fetch
  loggers inline; never bind a module-level `_log`.
- Read knobs **through the module** (`config.SCANNERS_REQUIRED`), never
  `from ..common.config import SCANNERS_REQUIRED` — that binds at import, before
  any fixture runs.
- Zero static AWS keys. Never read, print, log or commit `.env`. FAKE credential
  literals only; `AKIAIOSFODNN7EXAMPLE` is AWS's published example and is safe.
- Account `339712964409`, `us-east-1`. Do NOT `ls` inside `runs/` (~10k files).
- Commit with a message that states the **measurement**, not the intention.
- Rollback point: `git tag pre-demo-fixes-baseline` (commit `cfc5df4`).

## Live state that must not regress

Five runtimes `theagentorg_{planner,developer,reviewer,security,sre}` READY at
**version 10**. `SCANNERS_REQUIRED=true` on `theagentorg_security` and **none** of
the other four — verified twice. Three Environments `gate1`/`gate2`/`gate3` each
with `required_reviewers`. EventBridge rule at 1 target, connection `AUTHORIZED`,
API destination `ACTIVE`, DLQ empty.

## Already done, do not redo

- **The credential leak** (`d237b32`): the binary `tfplan` embedded state carrying a
  live `github_pat_`; ten artifacts deleted, upload narrowed to `plan.txt`, two
  tests with RED steps. **The token still needs rotating — that is the operator's.**
- **The contract additions** (`b32ea5c`): `RunState.model_provenance`,
  `RunState.trigger`, `LogEvent.action += "failed"`, plus `timeline._MARK` and
  `_OUTCOME` entries for it.

## Corrected audit claims — do not act on the originals

- `ListAgentRuntimeEndpoints` is **still `implicitDeny`**, re-simulated
  2026-08-22. One audit reported it had become `allowed`. `deploy.yml`'s comment and
  CLAUDE.md are **correct**; do not "fix" them.
- Runtimes are at **v10**, not v9. CLAUDE.md says v9 — Lane D corrects it.

## Deferred deliberately, with the reason

- **Reported line numbers are indices into the added-lines-only file**, not the real
  file. Correcting the materialiser shifts the pinned `{3,4}` to `{4,5}` — which is
  the **fixture's** pair — collapsing the only discriminator between real scanners
  and the fixture. **Do not touch before the demo.** Lane D documents it.
- **`ecr-push-policy` scopes to `astrolabe-*`** and is inert because
  `AmazonEC2ContainerRegistryFullAccess` covers the push. Fixing needs both changed
  together or the deploy breaks. Post-demo.
- **`github-actions-role` can delete other projects' ECR repos and runtimes.**
  Real, worth knowing, not a demo blocker. Post-demo.
- **`can_admins_bypass: true`** on all three gates: an admin can push a gate through
  without a reviewer click. An operator decision, not a code change — Lane D records
  it so the honest answer is available if a judge asks.
- **The suite writes into the real `runs/`** (11k+ files). A conftest redirect of
  `log._LOG_DIR` and `gates._STATE_DIR` is right, but it touches the guard layer
  every test depends on. Post-demo.

---

# LANE A — Scanner correctness

**Owns, exclusively:**
```
agentorg/security/semgrep_tool.py
agentorg/security/trivy_tool.py
agentorg/security/__init__.py
agentorg/agents/security.py
tests/test_scanner_correctness.py      (create)
```
**Never touch:** `agentorg/common/diff.py` (Lane B owns it), anything else.

Four fail-open defects in the layer whose verdict is the demo's central claim.
**Every one is silent: green suite, green gate, wrong answer.**

## A1: semgrep's severity table downgrades HIGH and CRITICAL to `low`

**PROVED** — run it yourself first:

```bash
.venv-main/bin/python -c "
from agentorg.security.semgrep_tool import _map_severity
from agentorg.state import SEVERITY_ORDER
for s in ('INFO','WARNING','ERROR','LOW','MEDIUM','HIGH','CRITICAL','',None):
    m = _map_severity(s or ''); print(f'{str(s):9} -> {m:9} (order {SEVERITY_ORDER[m]})')
print('block cutoff high =', SEVERITY_ORDER['high'])"
```

Output: `HIGH -> low (order 0)`, `CRITICAL -> low (order 0)`, cutoff `2`. The table
holds only `INFO`/`WARNING`/`ERROR`; semgrep 1.x also emits `LOW`/`MEDIUM`/`HIGH`/
`CRITICAL` from new-style rule metadata, and `mapping.get(key, "low")` sends every
one to severity 0. **A semgrep rule marked CRITICAL cannot block.** No test covers
`_map_severity`; `scan_gate.py:190` asserts only `any(f.tool == "semgrep")`.

- [ ] **Step 1: Write the failing test** in `tests/test_scanner_correctness.py`:

```python
"""Four fail-open defects in the layer that decides whether a change ships.

Every one is SILENT: the suite stays green, the gate stays green, and the verdict
is wrong. That is the exact shape this project exists to prevent, in the one place
it matters most.
"""

import pytest

from agentorg.security import gitleaks_tool, semgrep_tool, trivy_tool
from agentorg.state import SEVERITY_ORDER, compute_security_verdict


BLOCK_CUTOFF = SEVERITY_ORDER["high"]


@pytest.mark.parametrize(
    ("semgrep_severity", "must_reach_cutoff"),
    [
        ("INFO", False), ("LOW", False),
        ("WARNING", False), ("MEDIUM", False),
        ("ERROR", True), ("HIGH", True), ("CRITICAL", True),
    ],
)
def test_semgrep_severities_that_should_block_do_block(semgrep_severity, must_reach_cutoff):
    """MEASURED before the fix: HIGH and CRITICAL both mapped to `low` (order 0)
    against a cutoff of 2, so a rule semgrep marked CRITICAL could not block."""
    mapped = semgrep_tool._map_severity(semgrep_severity)
    reaches = SEVERITY_ORDER[mapped] >= BLOCK_CUTOFF
    assert reaches is must_reach_cutoff, (
        f"semgrep {semgrep_severity!r} maps to {mapped!r} (order "
        f"{SEVERITY_ORDER[mapped]}) and {'does not reach' if not reaches else 'reaches'} "
        f"the block cutoff {BLOCK_CUTOFF}. Expected "
        f"{'to block' if must_reach_cutoff else 'not to block'}."
    )


def test_an_unrecognised_semgrep_severity_fails_CLOSED():
    """The default must not be the lowest severity.

    An unknown value means semgrep said something this table has not seen. Mapping
    it to `low` means a new severity name silently stops blocking; mapping it high
    means a new name blocks loudly and somebody fixes the table. Only one of those
    is safe to be wrong about.
    """
    mapped = semgrep_tool._map_severity("SOME_FUTURE_SEVERITY")
    assert SEVERITY_ORDER[mapped] >= BLOCK_CUTOFF, (
        f"an unrecognised semgrep severity maps to {mapped!r} (order "
        f"{SEVERITY_ORDER[mapped]}), below the cutoff {BLOCK_CUTOFF}. It must fail "
        f"CLOSED: a severity name this table does not know is not evidence of "
        f"safety."
    )


@pytest.mark.parametrize(
    ("trivy_severity", "must_reach_cutoff"),
    [("UNKNOWN", False), ("LOW", False), ("MEDIUM", False),
     ("HIGH", True), ("CRITICAL", True)],
)
def test_trivy_severities_map_correctly(trivy_severity, must_reach_cutoff):
    """trivy's table is currently complete; this is the tripwire, not a fix."""
    mapped = trivy_tool._map_severity(trivy_severity)
    assert (SEVERITY_ORDER[mapped] >= BLOCK_CUTOFF) is must_reach_cutoff


def test_an_unrecognised_trivy_severity_fails_CLOSED():
    mapped = trivy_tool._map_severity("SOME_FUTURE_SEVERITY")
    assert SEVERITY_ORDER[mapped] >= BLOCK_CUTOFF, (
        f"unrecognised trivy severity maps to {mapped!r}; same fail-closed "
        f"requirement as semgrep"
    )


def test_a_high_severity_finding_actually_produces_a_block():
    """End to end through the real rule, so the mapping is not tested in isolation.

    A severity table that maps correctly but whose values do not reach
    compute_security_verdict would pass every test above.
    """
    from agentorg.state import Finding
    for sev in ("HIGH", "CRITICAL", "ERROR"):
        f = Finding(tool="semgrep", severity=semgrep_tool._map_severity(sev),
                    rule="r", file="app/auth.py", line=1, description="d")
        verdict, blocking = compute_security_verdict([f], threshold="high")
        assert verdict == "block", (
            f"a semgrep {sev} finding produced verdict {verdict!r} with "
            f"{len(blocking)} blocking. It must block."
        )
```

- [ ] **Step 2: Run it, watch it fail.** `HIGH`, `CRITICAL`, both fail-closed tests
and the end-to-end test fail. Paste the output.

- [ ] **Step 3: Implement** in `agentorg/security/semgrep_tool.py`:

```python
def _map_severity(severity: str | None) -> str:
    """Map Semgrep severity onto our vocabulary, FAILING CLOSED on the unknown.

    MEASURED 2026-08-22, when this table held only INFO/WARNING/ERROR and defaulted
    to "low": semgrep 1.x also emits LOW/MEDIUM/HIGH/CRITICAL from new-style rule
    metadata, and every one of those fell through to severity 0 against a block
    cutoff of 2. A rule semgrep marked CRITICAL could not block a change.

    THE DEFAULT IS "high", NOT "low", AND THAT IS THE WHOLE POINT. An unrecognised
    value means semgrep said something this table has not seen. Defaulting low means
    a future severity name silently stops blocking; defaulting high means it blocks
    loudly and somebody fixes the table. Only one of those is safe to be wrong
    about, and this project's signature defect is the other one.
    """
    mapping = {
        "INFO": "low",
        "LOW": "low",
        "WARNING": "medium",
        "MEDIUM": "medium",
        "ERROR": "high",
        "HIGH": "high",
        "CRITICAL": "critical",
    }
    return mapping.get((severity or "").upper(), "high")
```

Make the same change to `trivy_tool._map_severity`'s default — its table is
complete, so this is latent rather than live, but the `or "low"` shape is the same
trap one file over.

- [ ] **Step 4: RED step.** Revert the default to `"low"` → the two fail-closed
tests fail. Remove `"CRITICAL"` from the map → `test_semgrep_severities…[CRITICAL]`
fails. Paste both, revert both.

## A2: trivy's `or []` bypasses its own shape guard

**PROVED:**

```bash
.venv-main/bin/python -c "
d={'Results': ''}; print('trivy  ', repr(d.get('Results') or []), '<- guard sees a list')
e={'results':''};  print('semgrep', repr(e.get('results', [])), '<- guard TRIPS')"
```

`trivy_tool.py:145` is `data.get("Results") or []`, which collapses a falsy
wrong-typed value to a valid empty list **before** the shape guard, so the fault is
never raised. `semgrep_tool.py:164` writes `.get("results", [])` and trips
correctly. Two spellings of one guard; one fails open.

- [ ] **Step 1: Add the test** to your file:

```python
@pytest.mark.parametrize("wrong_value", ["", 0, False, {}])
def test_a_wrong_typed_results_field_is_a_FAULT_not_an_empty_scan(wrong_value, tmp_path):
    """MEASURED: `data.get("Results") or []` collapsed every falsy wrong type to a
    valid empty list BEFORE the shape guard, so a malformed trivy report produced
    zero findings and a `pass` instead of a blocking fault. Its sibling wrapper
    spells the same guard `.get("results", [])` and trips correctly."""
    import json
    report = tmp_path / "trivy-report.json"
    report.write_text(json.dumps({"Results": wrong_value}))
    with pytest.raises(Exception) as caught:
        trivy_tool._findings_from_report(str(report), str(tmp_path))
    assert "Results" in str(caught.value) or "shape" in str(caught.value).lower(), (
        f"a Results field of {wrong_value!r} did not raise a shape error; it was "
        f"treated as an empty scan, which reports `pass` over a report nobody read"
    )
```

Read `trivy_tool.py` to find the real name of the report-parsing helper and adjust
the call — do not assume `_findings_from_report`.

- [ ] **Step 2: Run, fail, fix.** Change line 145 to:

```python
    # `.get("Results")` then an explicit None check, NOT `or []`.
    #
    # MEASURED: `or []` collapses every falsy wrong type -- "", 0, False, {} -- to a
    # valid empty list BEFORE the shape guard below, so a malformed report produced
    # zero findings and a `pass` verdict instead of a blocking fault. semgrep_tool
    # spells the same guard `.get("results", [])` and trips correctly; this was the
    # copy that drifted.
    targets = data.get("Results")
    if targets is None:
        targets = []
```

- [ ] **Step 3: RED step.** Restore `or []` → the parametrised test fails on all
four values. Paste it.

## A3: one absent scanner aborts the whole fan-out

`security/__init__.py:300` iterates `(_semgrep, _gitleaks, _trivy)` with no
per-scanner isolation. semgrep is **first**, so its raise discards gitleaks' and
trivy's findings *and* their blocking faults. `__init__.py:50-53` records that 117
of 121 fan-out calls raise here — this is CI's normal path.

- [ ] **Step 1: Add the test:**

```python
def test_one_absent_scanner_does_not_discard_the_others(monkeypatch):
    """MEASURED: semgrep runs first and its FileNotFoundError ended the fan-out,
    so gitleaks' and trivy's findings -- and their blocking faults -- were thrown
    away. `wrappers actually invoked: ['semgrep']`."""
    from agentorg import security as sec
    from agentorg.state import DevResult, Finding

    called: list[str] = []

    def _absent(dev):
        called.append("semgrep")
        raise FileNotFoundError("semgrep is not installed")

    def _finds(name):
        def _scan(dev):
            called.append(name)
            return [Finding(tool=name, severity="critical", rule=f"{name}-r",
                            file="app/auth.py", line=1, description="d")]
        return _scan

    monkeypatch.setattr(sec, "_semgrep", _absent)
    monkeypatch.setattr(sec, "_gitleaks", _finds("gitleaks"))
    monkeypatch.setattr(sec, "_trivy", _finds("trivy"))
    sec.reset_scanner_cache()

    dev = DevResult(branch="b", diff="--- a/app/auth.py\n+++ b/app/auth.py\n@@ -1 +1,2 @@\n+x\n",
                    summary="s", files_changed=["app/auth.py"])
    try:
        findings = sec.run_all_scanners(dev)
    except FileNotFoundError:
        pytest.fail(
            f"the fan-out aborted on the first absent scanner. Wrappers invoked: "
            f"{called}. gitleaks and trivy never ran, so their findings and any "
            f"blocking faults were discarded -- and this is CI's normal path, not "
            f"an edge case."
        )
    assert "gitleaks" in called and "trivy" in called, f"invoked only {called}"
    tools = {f.tool for f in findings}
    assert {"gitleaks", "trivy"} <= tools, f"findings came only from {tools}"
```

- [ ] **Step 2: Implement.** Wrap each wrapper call so an absent scanner records
its own outcome without ending the loop. **Preserve the existing semantics
exactly:** with `SCANNERS_REQUIRED=false` an absent scanner still yields no
finding for that tool; with it true it still yields a blocking
`*-scanner-error`. What changes is only that the *other* wrappers still run.

**The cache interaction is load-bearing.** `_is_fault_free` gates the store, and a
result assembled from a partial fan-out must **not** be cached if any wrapper
raised — otherwise a later call with real binaries gets the degraded answer. Read
`__init__.py`'s existing cache comments before changing the loop, and add an
assertion that a partial run is not stored.

- [ ] **Step 3: RED step.** Restore the un-isolated loop → the test fails naming
`['semgrep']`. Then make the partial result cacheable → your cache assertion fails.
Paste both.

## A4: `_looks_poisoned` is the copy `common/diff.py` was written to delete

`agents/security.py:79` is `"AKIA" in (state.dev.diff or "")` — the raw whole-diff
substring scan CLAUDE.md records as costing **2 blocks in 5 live runs**. Both error
directions proved: it misses a GitHub PAT entirely, and it returns True for a key
on a `-` line (a key the change *removes*), where the developer's
`_key_is_in_the_change` correctly returns False.

It decides which fixture stands in on the fallback path, so it is choosing between
`block` and `pass`.

- [ ] **Step 1: Add the test:**

```python
def test_looks_poisoned_reads_the_change_not_the_whole_diff_text():
    """MEASURED both directions. This function chooses which fixture stands in on
    the fallback path, so it is choosing between `block` and `pass`.

    CLAUDE.md records the whole-diff substring form costing 2 blocks in 5 live runs.
    The developer agent already does this correctly via added_files() and a real
    AKIA[0-9A-Z]{16} regex; the security agent is the straggler.
    """
    from agentorg.agents import security as sec_agent
    from agentorg.state import DevResult, RunState

    def _state(diff: str) -> RunState:
        s = RunState(ticket_id="T-1", ticket_text="x")
        s.dev = DevResult(branch="b", diff=diff, summary="s", files_changed=["app/auth.py"])
        return s

    removed = ('--- a/app/auth.py\n+++ b/app/auth.py\n@@ -1,2 +1,1 @@\n'
               '-AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"\n+import os\n')
    assert sec_agent._looks_poisoned(_state(removed)) is False, (
        "a key on a REMOVED line read as poisoned. That is the shape of every "
        "revision after the reviewer asks for credentials to be taken out, and "
        "CLAUDE.md records this exact confusion costing 2 blocks in 5 live runs."
    )

    added = ('--- a/app/auth.py\n+++ b/app/auth.py\n@@ -1 +1,2 @@\n'
             '+AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"\n')
    assert sec_agent._looks_poisoned(_state(added)) is True, (
        "an ADDED key did not read as poisoned -- the fix went too far and the "
        "poisoned demo would pick the pass fixture"
    )
```

- [ ] **Step 2: Implement.** Use `agentorg.common.diff.added_files` and a real
`AKIA[0-9A-Z]{16}` regex, mirroring `developer._key_is_in_the_change`. **Import the
regex or the helper rather than writing a fifth copy** — four private copies once
drifted and that is what `common/diff.py` exists to prevent.

Lane B is editing `common/diff.py` concurrently. **Its public function
`added_files(diff) -> dict[str, str]` does not change**, so importing it is safe.
One behaviour does change: after Lane B's fix, a non-empty diff that parses to no
files **raises `ValueError`**. Handle that — a diff this parser cannot read is not
evidence of cleanliness, so treat it as poisoned rather than clean, and say so in a
comment.

- [ ] **Step 3: RED step.** Restore the substring form → the removed-line assertion
fails. Then make the function always return True → the added-line assertion still
passes but the removed one fails; confirm both directions are pinned. Paste both.

## A5: Lane A wrap-up

- [ ] Full suite, ruff. Both green.
- [ ] `git diff` shows no mutation.
- [ ] Commit each of A1–A4 separately, each message stating its measurement.

---

# LANE B — Pipeline stages

**Owns, exclusively:**
```
scripts/run_stage.py
agentorg/graph.py
agentorg/common/diff.py
tests/test_promote_guard.py            (create)
tests/test_failed_run_rendering.py     (create)
tests/test_diff_headers.py             (create)
tests/test_state_backend_cloud.py      (create)
```
**Never touch:** `agentorg/state.py`, `agentorg/timeline.py` (both already done),
`agentorg/github_ops.py` (Lane C), anything under `agentorg/security/` (Lane A).

**You own the two files every other fix wanted.** Five defects.

## B1: `common/diff.py` — a non-default prefix scans nothing

**PROVED:**

```bash
.venv-main/bin/python -c "
from agentorg.common.diff import added_files
b='--- a/app/auth.py\n+++ b/app/auth.py\n@@ -1 +1,2 @@\n+K=\"AKIAIOSFODNN7EXAMPLE\"\n'
n='--- app/auth.py\n+++ app/auth.py\n@@ -1 +1,2 @@\n+K=\"AKIAIOSFODNN7EXAMPLE\"\n'
for lbl,d in (('b/ prefix',b),('no prefix',n)):
    f=added_files(d); print(f'{lbl:12} files={list(f)} key={any(\"AKIA\" in v for v in f.values())}')"
```

`_HEADER = "+++ b/"` recognises only git's default. A `--no-prefix` diff
materialises **zero files**, so the scanners run over an empty tree, return `[]`,
`compute_security_verdict([])` returns `("pass", [])` — and `scan_provenance` still
records `scanners`, truthfully. **The diff is model-written**, so this is plausible.
The poisoned half survives only by accident (the developer's safety net uses the
same parser); the clean half has no safety net.

- [ ] Accept every legal `+++` spelling: `b/`, no prefix, `a/` on both sides,
`old/`/`new/`, and `/dev/null` on the minus side. Strip at most one leading path
component. The materialised filename must be `app/auth.py` in every case — a prefix
leaking into the path changes what a judge reads on the PR.
- [ ] **A non-empty diff that parses to zero files must `raise ValueError`.**
Returning `{}` hands an empty tree to the scanners and reports `pass`. An empty or
`None` diff must still return `{}` without raising — `added_files(None)` is a real
call.
- [ ] Tests: parametrise over all five spellings; assert the filename; assert the
refusal; assert the empty case does **not** raise.
- [ ] **RED steps:** restore `"+++ b/"` → three spellings fail. Delete the raise →
the refusal test fails. Make the raise unconditional → the empty-diff test fails.

**Tell Lane A when this lands** — its A4 imports `added_files` and must handle the
new `ValueError`.

## B2: `_stage_promote` writes PROMOTED over whatever it loaded

`scripts/run_stage.py`'s `_stage_promote` sets `state.status = "promoted"` with no
check. The job graph makes it unreachable on a blocked run today — that is control
flow, not a guard, and `graph.py`'s promote step is a second caller with a different
ordering.

- [ ] Add a refusal: if the loaded state is not promotable — security verdict not
`pass`, SRE not `go`, any gate decision `rejected`, or any of the three gates
missing an approval — refuse, log it, and return a non-zero exit distinct from
`EXIT_BLOCKED` and `EXIT_REJECTED`. `EXIT_ALREADY_FINAL = 5` exists; reuse it if the
state is terminal, and justify any new code against the reasoning already in that
comment block.
- [ ] **READ the gate decisions, do not count them.** `gates.resume` never un-sets
a rejection, so a run can carry three decision rows one of which is a refusal.
- [ ] Same guard in `graph.py`'s promote step. Test **both** paths.
- [ ] **RED steps:** delete the security check → a blocked-state promote succeeds
and your test must fail loudly. Replace the decision read with `len(...) >= 3` → the
rejected-decision test fails.

## B3: the two `failed` endings

`state.py` and `timeline.py` **already** have the `failed` action, glyph and banner.
Your half is writing the rows.

- [ ] `run_stage._OUTCOME_ACTIONS["failed"]` is currently `"blocked"`. Change it to
`"failed"`. **Measured consequence of leaving it:** a revision-cap run renders
`⛔ BLOCKED — the change was stopped` while its security verdict was `pass` with 0
blocking — the pipeline's central claim asserted about a change the scanners cleared.
- [ ] The SRE `no_go` exit writes **no log row** in either `graph.py` or
`run_stage.py`, so the run renders `… INCOMPLETE`. Add
`_log(state, "system", "sre", "failed", verdict=state.sre.verdict, summary=...)` to
**both**. **No test covers the no_go path** — that is why this survived.
- [ ] Change the revision-cap exit's row from `action="blocked"` to
`action="failed"` in both files. Keep its summary.
- [ ] Test the rendered **banner**, not the status field: `timeline._outcome` reads
the last row's action and never sees `RunState.status`.
- [ ] **RED steps:** revert the mapping → the banner test fails. Delete the no_go
row from `graph.py` → its test fails. Delete it from `run_stage.py` only → if
nothing fails, add a test that drives that path directly, watch it fail, keep it.

## B4: `run_stage._load` refuses on the dynamodb backend

**PROVED:** `STATE_BACKEND=dynamodb .venv-main/bin/python -c "from agentorg import
gates; gates._state_path('x')"` → `RuntimeError: there is no state FILE on the
'dynamodb' backend`.

`gates.load` **already handles both backends correctly** — this is a three-line
change, not the rewrite the docstring implies.

- [ ] Read through `gates.load(run_id)`, turning its `FileNotFoundError` into the
existing named `SystemExit`. **Do not soften it into a fresh `RunState`** — that
would report success for work it invented.
- [ ] Replace the docstring's KNOWN DEBT paragraph with what is now true.
- [ ] Test the seam: stub `gates.load`, make `gates._state_path` raise
`AssertionError` if called.
- [ ] **RED steps:** revert to `_state_path` → the seam test fails. Delete the
`except FileNotFoundError` → the missing-run test fails.

## B5: the cloud path never calls `gates.pause`

`graph.py` calls it twice; `run_stage.py` never. `approve_server._awaiting()` finds
runs by the `"awaiting human decision"` marker `gates.pause` writes — so **no cloud
run is visible to the approval screen**, and that is the seam a future frontend
would read.

- [ ] Call `gates.pause(state, gate)` in the cloud gate stages so the marker row is
written. Read `gates.pause` first: it also calls `save`, so ordering relative to
`_emit` matters — do not double-write.
- [ ] Test that a cloud gate stage leaves a row `approve_server._awaiting()` can
find. Import the marker constant rather than restating the string.
- [ ] **RED step:** remove the call → the test fails naming the empty result.

## B6: Lane B wrap-up

- [ ] Full suite, ruff, `git diff` clean. Commit B1–B5 separately.

---

# LANE C — Agents and the model seam

**Owns, exclusively:**
```
agentorg/agents/planner.py
agentorg/agents/developer.py
agentorg/agents/reviewer.py
agentorg/agents/sre.py
agentorg/common/llm.py
agentorg/github_ops.py
tests/test_model_provenance.py   tests/test_sre_agent.py
tests/test_ci_status.py          tests/test_merge_pr.py     (create all four)
```
**Never touch:** `agentorg/agents/security.py` (Lane A), `graph.py` or
`run_stage.py` (Lane B), `state.py`.

Read tasks 2, 5, 6 and 7 of
`docs/superpowers/plans/2026-08-22-pre-demo-fixes.md` — they contain the **full
test bodies and implementations** for this lane. Follow them, with these amendments:

- [ ] **C1 — model provenance** (that plan's Task 2). `RunState.model_provenance`
already exists; add `llm.reset_source()`, `last_source()`, `_record()` where
`"fixture"` never downgrades to `"model"`. Stamp the agents. **Do not** edit
`graph.py` or `run_stage.py` — instead expose `llm.last_source()` and tell Lane B
the name; Lane B wires the `finally` and `_emit`. Coordinate via the report.
- [ ] **C2 — `ci_status`** (Task 5, steps 3-8). `github_ops.ci_status(state) ->
"passing" | "failing" | "unknown"`. **Zero checks is `unknown`, never `passing`** —
`auth-service` reports `{"state": "pending", "total_count": 0}`, and GitHub says
`pending` when nothing has run. Must work for a target repo with CI and without.
Never raises. **Lane D creates the target repo's CI workflow** — do not.
- [ ] **C3 — the real SRE agent** (Task 6). CI decides the verdict; the model
contributes `slo_checks`, `notes`, `estimated_cost_note` and **cannot reach**
`verdict` or `ci_status`, nor reach them indirectly through a check claiming to have
failed. Today `sre.run` ignores its state, never imports `llm`, and always returns
its fixture — its `SYSTEM_PROMPT` is dead code.
- [ ] **C4 — `merge_pr`** (Task 7, steps 3 and 6 only). Create
`github_ops.merge_pr(state) -> str`, never raising, re-checking its own
preconditions and **reading** the gate decisions rather than counting them.
**Do not wire it into promote** — Lane B owns both promote sites and will call it.
The signature is fixed by this plan so both lanes can work at once.
- [ ] Each of C1–C4 gets its RED steps from that plan, every failure pasted.

---

# LANE D — Infra, CI, config and docs

**Owns, exclusively:**
```
infra/Terraform/modules/agentcore/main.tf
infra/Terraform/modules/ingress/main.tf
.github/workflows/deploy.yml
.github/workflows/run-pipeline.yml
agentorg/common/config.py
scripts/preflight.py                   (create)
tests/test_agentcore_iam.py            (create)
tests/test_trigger_provenance.py       (create)
CLAUDE.md   README.md   docs/plan/reem/demo_script.md
auth-service's .github/workflows/ci.yml (SEPARATE REPO)
```
**Never touch:** `.github/workflows/terraform.yml` (just fixed, `d237b32`),
anything under `agentorg/` except `common/config.py`.

## D1: the IAM grant — the highest-value fix in this plan

Follow Task 1 of `docs/superpowers/plans/2026-08-22-pre-demo-fixes.md` in full.
**Every model-calling agent has been serving fixtures all week** because
`bedrock:InvokeModel` is `implicitDeny` on the inference profile the code names.
Confirm with `simulate-principal-policy` before and after; the after must read
`allowed`. **Paste both.**

## D2: the deploy smoke test cannot fail

Task 4, step 4 of that plan. `grep -q '"tasks"'` — and
`fixtures/plan_result.json` **begins with** `"tasks"`. Read the fixture's real
`notes` literal with a command; do not copy a string on trust.

## D3: give `auth-service` real CI

Task 5, steps 1-2. Open it as a PR on that repo so the workflow's first act is
proving it runs. **Paste the resulting check-run JSON.** Lane C reads this via
`ci_status` — it must handle a repo without CI too, so do not assume this landed.

## D4: `trigger` provenance

Task 9b. `RunState.trigger` already exists. Add the workflow input (default
`manual`), send `"trigger": "issue"` from the ingress `input_template` — quoted,
like every other value — and pass `--trigger` through to `plan`. **The
different-values assertion is the anti-vacuity check**: identical values would make
the field prove nothing.

**`run_stage.py` is Lane B's.** Give Lane B the argparse flag name in your report
and let it wire the stage; you own the workflow and the Terraform.

## D5: validate `SECURITY_BLOCK_THRESHOLD` at import

Task 9c. `compute_security_verdict([], threshold="HIGH")` → `KeyError: 'HIGH'`
**mid-run inside the security agent**. Every other malformed knob in `config.py`
fails at import.

## D6: `preflight.py`

Task 3. Four checks: the IAM simulation, five runtimes READY, a real invoke of
`theagentorg_security` asserting `tests.provenance.REAL_SCANNER_LINES`, and the
three Environments' required reviewers. **Import the line sets from
`tests/provenance.py`** rather than restating them.

## D7: the documentation corrections

- [ ] **Runtimes are at v10**, not v9 — CLAUDE.md says v9.
- [ ] **`ListAgentRuntimeEndpoints` is still `implicitDeny`** (re-simulated). One
audit claimed otherwise. CLAUDE.md and `deploy.yml` are **correct** — do not change
them; add a line recording that it was re-verified on 2026-08-22.
- [ ] **Record the deferred line-number finding**: reported lines are indices into
the added-lines-only file, and fixing it would shift the pinned `{3,4}` onto the
fixture's `{4,5}`, collapsing the discriminator. Say plainly that it must not be
touched before the demo.
- [ ] **Record the credential leak** (`d237b32`) in CLAUDE.md's traps: a binary
`tfplan` embeds state, a raw grep of the outer file finds nothing, and the exposure
audience is different from the accepted S3-state one.
- [ ] **Record `can_admins_bypass: true`** on the three gates, so the honest answer
exists if a judge asks whether a gate can be skipped.
- [ ] Update the four documented limitations as the lanes close them.

---

## Verification — after all four lanes report

Run by the coordinator, not by a lane.

- [ ] Four gates: `pytest -q`, `ruff`, `actionlint`, `terraform fmt -check`.
- [ ] `preflight.py` exits 0 with check 1 `allowed`.
- [ ] Terraform apply — **batched**, once, covering D1 and D4 together.
- [ ] Deploy, so the runtimes carry the new code.
- [ ] **Poisoned run:** `develop` exits 3; PR shows `provenance: scanners` at
`app/auth.py:3` and `:4`; **`_source: model`**; `status=blocked` survives; recorders
skipped; **not merged**.
- [ ] **Clean run:** seven jobs green; `_source: model` **and the plan text differs
from `fixtures/plan_result.json`** — that difference is the proof D1 worked; real
`ci_status`; PR **merged**; `⇄ MERGED` before `★ PROMOTED`.
- [ ] **Automatic trigger:** open an issue; the run records `trigger: issue`.
- [ ] Docs updated with pasted output.

## Self-Review

**Coverage.** All three audits' actionable findings map to a lane: scanner
fail-opens → A; pipeline-stage and diff defects → B; agents, model seam, CI read and
merge → C; IAM, workflows, config, docs → D. The credential leak was fixed before
lane dispatch because it was live.

**No shared files.** Verified by mapping every finding to its files: the only
contested paths were `run_stage.py` (3 findings, all Lane B) and `state.py` /
`timeline.py` (2 lanes, resolved by doing them up front in `b32ea5c`).

**Three cross-lane interfaces**, each fixed by this document so neither side waits:
`added_files` keeps its signature but gains a `ValueError` (B → A); `llm.last_source()`
(C → B); `github_ops.merge_pr(state) -> str` (C → B); the `--trigger` flag name
(D → B). Each lane reports its interface in its final message.

**Deferred, with reasons stated** rather than omitted: the line-number semantics, the
`astrolabe-*` ECR policy, the cross-project IAM blast radius, `can_admins_bypass`,
and the suite writing into `runs/`.
