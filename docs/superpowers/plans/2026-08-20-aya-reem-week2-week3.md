# Aya + Reem, Weeks 2–3 — Chaos, Provenance, Baseline, and the DORA Table

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Prove the demo's central claim under fault, under repetition, and in
both scanner-provenance modes — and produce the before/after DORA numbers the
judges ask for. Nothing here builds pipeline features; everything here is a
witness on features that already shipped.

**Architecture:** Two testing lanes that share `tests/` by filename. Aya adds
`tests/test_chaos_*.py` and `tests/test_dora_*.py` plus two non-test modules
(`tests/dora_runner.py`, `tests/dora_batch.py`). Reem adds
`tests/test_baseline.py`, `tests/test_functional_flow.py`, one CI step, and
`docs/plan/reem/demo_script.md`. Both drive the frozen seam
`graph.run_pipeline(ticket_id, ticket_text, *, poisoned=False, auto_approve=True) -> RunState`
and read `agentorg.log.read(run_id)`. No task edits `agentorg/state.py`, the
graph, or any agent.

**Tech Stack:** Python 3.12+ (measured on CPython 3.14.6), pydantic v2, pytest 8,
ruff 0.16.3, gitleaks 8.21.2 / semgrep 1.172.0 / trivy 0.74.0.

**Specs:** `docs/plan/aya/week2.md`, `docs/plan/aya/week3.md`,
`docs/plan/reem/week2.md`, `docs/plan/reem/week3.md`. Week-1 context:
`docs/plan/aya/week1.md`, `docs/plan/reem/week1.md`.

**Demo:** judged live demo **Tue Aug 25**. Today is **Aug 20**. Reem's spec
freezes the script Aug 25; Aya's spec freezes metrics Aug 25. Five days.

---

## What is already done — do NOT redo

Traceable to named spec tasks. Verified against the worktree at `0e59c8a`, not
against plan prose.

### Aya

| Spec task | Spec location | Status | Evidence |
|---|---|---|---|
| `test_block_determinism.py`, 20× poisoned | wk1 Sun–Mon | **DONE** | `tests/test_block_determinism.py`, 3 tests, green |
| `test_block_shape_stability.py`, 5 agents × 10 | wk1 Tue–Wed | **DONE** | `tests/test_block_shape_stability.py`, 6 tests, green |
| Fault 2 — reviewer never approves, loop terminates at cap | wk2 Sat–Sun | **DONE by others** | `test_agent_fallbacks.py:706` and `test_gates_cli.py:234` both assert `revision_count == MAX_REVISION_LOOPS`; the second also pins the log shape |
| Fault 1 — hung gate never promotes | wk2 Sat–Sun | **PARTIAL** | Nothing asserts a *raising* gate seam. `test_gates_cli.py` covers human rejection at all three gates. Task 3 covers the raise. |
| Chaos: killed scanner | wk2 Mon–Tue | **MOSTLY DONE by Habiba** | `tests/test_scanner_resilience.py` (1941 lines) covers missing binaries, timeouts, non-zero exits, missing/malformed/wrong-typed reports, per-tool faults, `SCANNERS_REQUIRED`. Only the black-box pipeline view is open — Task 4. |
| DORA runner + harness | wk2 Wed–Thu | **OPEN** | No `tests/dora_runner.py`. Task 6. |
| Fri Aug 21 re-verify on real code | wk2 Fri | **OPEN (process)** | Task 5 makes it a command, not a ritual. |
| 10-vs-10 DORA batch | wk3 Sat–Sun | **OPEN** | Task 7. |
| DORA comparison table | wk3 Mon | **OPEN** | Task 8. |
| English backup video | wk3 Tue | **OPEN (process)** | Task 10. |
| Re-verify after late fixes | wk3 Wed–Thu | **OPEN (process)** | Task 11. |

### Reem

| Spec task | Spec location | Status | Evidence |
|---|---|---|---|
| Real Flask app: `create_app()`, `/login`, `authenticate()` | wk1 Sun–Mon | **DONE** | `target_repo/app/auth.py`; `cd target_repo && python -m pytest tests -q` prints `5 passed` (measured) |
| `tickets/clean.md` with testable criteria | wk1 Tue | **DONE** | `tickets/clean.md`, 5 acceptance criteria |
| `tickets/poisoned.md` carrying `AKIAIOSFODNN7EXAMPLE` | wk1 Wed | **DONE** | `tickets/poisoned.md`; `grep -c` returns **2**, not the 1 her spec's done-when predicts (prose mentions the key, then the diff plants it) |
| `tests/test_functional_contract.py` | wk1 Thu–Fri | **DONE, and stronger than spec** | 9 tests (spec said 8). Rewritten at merge: her original was two 0-byte placeholders, one named with a literal `*`. The merged version drops the free-pass `isinstance` assertions for cross-field agreement checks. |
| `tests/test_baseline.py` + `run_baseline` | wk2 Sat–Mon | **OPEN** | File does not exist; the 0-byte placeholder was deleted in `7293de6`. Task 1. |
| `test_functional_flow.py`: clean → promoted | wk2 Tue–Wed | **DONE by others** | `test_pipeline_smoke.py:10`, `test_block_determinism.py:33` |
| `test_functional_flow.py`: loop fires once then approves | wk2 Tue–Wed | **DONE by others** | `test_agent_fallbacks.py:824` asserts `revision_count == 1` and `calls["develop"] == 2` |
| `test_functional_flow.py`: loop terminates at cap | wk2 Tue–Wed | **DONE by others** | Same two sites as Aya's Fault 2 above |
| CI runs top-level `tests/` | wk2 Thu–Fri | **DONE** | `testpaths = ["tests"]`; CI `test` job runs `pytest -q` |
| CI runs `target_repo/tests` | wk2 Thu–Fri | **OPEN** | `grep target_repo .github/workflows/ci.yml` → no match (measured). Task 2. |
| Aug 21 deadline check | wk2 Thu–Fri | **OPEN (process)** | Folded into Task 5 |
| `docs/plan/reem/demo_script.md` | wk3 Sat–Sun | **OPEN** | Task 9 |
| Rehearsals + freeze + sign-off | wk3 Mon–Thu | **OPEN (process)** | Task 12 |

**The headline of this table:** Reem's entire `test_functional_flow.py` and Aya's
Fault 2 are already pinned by teammates' tests, in stronger form than either spec
asks for. Writing them again would add three duplicate `run_pipeline` calls to the
slowest lane in the suite and pin nothing new. Task 3 replaces them with the one
uncovered case and says so per spec item.

---

## Spec-vs-repo divergences — every one, one line each

Each of these is a place where a spec sentence is now false. No task may be
implemented from the spec text alone where a divergence is listed.

1. **[Aya wk2] The hung-gate test's second leg asserts nothing.** Its own comment says "the graph auto-approves regardless of decision value", and its assertion is `state.status in (...)` listing *all five* statuses — true for every possible run. **Measured false:** a gate returning `decision="rejected"` yields `status='rejected'`, `decisions=[('gate1','rejected')]`, and `dev is None`. `graph._decide` has honoured gate decisions since it was written.
2. **[Aya wk2] `test_reviewer_loop_is_bounded_in_the_log` FAILS as written.** It asserts `len(changes) <= MAX_REVISION_LOOPS`. **Measured: 5 events, cap is 3.** The graph logs three mid-loop revisions, one cap-exit line, and one terminal `action="blocked"` line — all with `verdict="changes_requested"`. The correct count is `MAX_REVISION_LOOPS + 2`, already pinned at `test_gates_cli.py:299`.
3. **[Aya wk2] `test_reviewer_that_never_approves_terminates_at_the_cap`'s comment is wrong about the outcome.** It says "the clean ticket still passes security, so it may end promoted". **Measured: `status='failed'`.** `graph.py` step 5b makes an unapproved change terminal — added after her spec was written.
4. **[Aya wk2] `test_scanner_that_crashes_does_not_promote` cannot fail.** It patches `graph.security.run` to raise `RuntimeError`, then asserts `pytest.raises(RuntimeError)`. It proves that a function which raises, raises. It says nothing about the pipeline. Task 4 replaces it with a fault injected *below* the seam that decides.
5. **[Aya wk2] The blind-scanner test asserts a fail-open as a passing expectation.** `assert state.status == "promoted"` on a poisoned ticket. Measured true — but it is a landmine: it goes RED the day someone fixes the fail-open. Task 4 keeps the coverage and inverts the framing.
6. **[Aya wk2/wk3] `from tests.dora_runner import ...` is a live risk.** `tests/` has no `__init__.py`; `pyproject.toml` sets `pythonpath = ["."]`, which makes `import tests.x` work under pytest and under `python -m` from the repo root, but not from any other cwd. Task 6 states the constraint rather than discovering it on stage.
7. **[Aya wk3] `run_baseline_path` calls `run_baseline(ticket_text)` with no `poisoned` argument.** **Measured:** that produces the *clean* diff, and the function then reports `bad_change_shipped=True` for a diff containing no secret. The baseline column of the headline table would be a fabrication. Task 6 fixes the call.
8. **[Aya wk3] `_step_count` for the baseline returns 0.** Reem's spec'd `run_baseline` never calls `log.append`, so `log.read(state.run_id)` is empty. **Measured: 0 events.** The DORA table's "Avg pipeline steps" baseline cell would read `0`, which reads as "no data" rather than "no checks". Task 6 decides this explicitly.
9. **[Aya wk3] "10/10 blocked" means two different things and the spec never says which.** Task 5 is the whole fix; see Global Constraints.
10. **[Reem wk1 done-when, cosmetic] `grep -c AKIAIOSFODNN7EXAMPLE tickets/poisoned.md` returns 2, not 1.** No action; the done-when text is stale, the ticket is correct.
11. **[Reem wk2] Her `run_baseline` signature is `(ticket_text, *, poisoned=False)`; Aya's consumer expects that exact shape.** They agree — this is the one cross-lane contract in the plan, and Task 1 must land before Task 6.
12. **[Reem wk2] The revision-loop half of `test_functional_flow.py` is fully covered.** See the table. Task 3 states which of her spec items are satisfied by whose existing test.
13. **[Reem wk3] The demo script's Beat 4 command does not exist yet.** `pytest -q tests/test_baseline.py::test_baseline_ships_the_poisoned_change` requires Task 1.
14. **[Reem wk3] Beat 2's expected output is unverified in one mode.** With `SCANNERS_REQUIRED=true` and no binaries installed, the clean run does **not** print `status=promoted`. See Task 5.
15. **[both] The diff-hash cache has NOT landed in this worktree.** Task 4 of Habiba's plan is unimplemented: `grep -rn "cache\|lru_cache\|sha256\|hashlib" agentorg/` returns nothing, and `run_all_scanners` is a bare `for scan in (...)` loop. Every cost number below is therefore uncached and is the pessimistic case. Do not plan around a cache that does not exist.

---

## Global Constraints

Copied verbatim from the sources named. Do not paraphrase these into a task.

**The frozen contract.** From `agentorg/state.py`:

> Rule after week 1: you may ADD optional fields. Never rename or remove one.
> A rename breaks all five lanes at once and nobody notices until integration.

No task in this plan adds a field. If one seems to need one, stop and report.

**Suite counts, measured at `0e59c8a` on this worktree:**

- Without scanner binaries: **177 passed, 1 skipped** in 19.77s.
- With scanner binaries: **178 passed** (the skip is trivy's capability test).
- Aya's two existing files alone: **9 passed in 2.31s**, of which
  `test_poisoned_always_blocks_20x` is **1.42s** — the single slowest test in the
  suite.
- `run_pipeline` costs **~60–74 ms** per call in fixture-fallback mode (measured
  over 10 runs, two ways).

**Lint.** `ruff check agentorg scripts tests` must exit 0. Measured: it does, on
ruff **0.16.3**. There is no `[tool.ruff]` section in `pyproject.toml` and none
may be added. `I001` (import sorting), `BLE001` (blind except) and `ISC004` are
ruff 0.16 defaults and cannot be relaxed. No `# noqa`, no per-file ignores. Both
of Aya's week-1 files needed an `I001` fix at merge because they predated the
lint job covering `tests/` — write imports sorted the first time.

Note: `target_repo/` is **not** in the lint command and currently has 2 `I001`
errors. That is out of scope. Do not widen the lint command; it would turn CI red
on a file no task here touches.

**Demo date.** A judged live demo is **Tue Aug 25**. Reem's script freezes that
day; Aya's metrics freeze that day. Anything that would look like a crash on a
projector outranks ordinary polish.

**The scanner-provenance rule — the single most important constraint in this plan.**

The three binaries (`semgrep`, `gitleaks`, `trivy`) are NOT on the default PATH.
Measured on this machine: all three absent.

- **Without them:** every wrapper raises `FileNotFoundError`,
  `agents/security.py` catches it (a deliberate blind `except Exception`) and
  returns `fixtures_loader.security(block=_looks_poisoned(state))`.
  **`compute_security_verdict` is never called.** The verdict comes from
  `fixtures/security_result_block.json`.
- **With them:** `run_all_scanners` fans out for real and
  `compute_security_verdict(findings, threshold=config.SECURITY_BLOCK_THRESHOLD)`
  decides.

Both modes produce `status="blocked"`, `verdict="block"`, `len(blocking) == 2`,
rules `{aws-access-key-id, aws-secret-access-key}`, severity `critical`. So
**"10/10 blocked" proves the block rule in one mode and proves a JSON file can be
read in the other.** An assertion that passes only because a fixture was returned
proves nothing about the block rule.

**Every chaos, determinism, or metrics task in this plan MUST state and control
which mode it runs in.** The mechanism is Task 5's `provenance` fixture. Do not
invent a second one.

**The one cheap discriminator, measured.** The fixture and the real scanners
disagree on line numbers and on nothing else:

| | access-key line | secret-key line | total findings |
|---|---|---|---|
| Fixture (`security_result_block.json`) | **4** | **5** | 3 |
| Real gitleaks 8.21.2 (`scripts/scan_gate.py` `EXPECTED_BLOCKING`) | **3** | **4** | 2 |

A test that wants to know which path answered can read
`{f.line for f in state.security.blocking}`. Task 5 wraps this so no test
open-codes it.

**Vocabulary — use Habiba's, do not invent a parallel one.** From
`agentorg/security/_run.py`'s module docstring, these terms are already defined
and already tested: **ABSENT** (binary not installed — a development and CI
affordance that keeps the fixture-fallback path), **FAULT** (present but broken —
must block), `classify_failure`, `run_scanner`, `unrunnable_findings`,
`error_finding`, `safe_run`, `ReportShapeError`, and the knob
`config.SCANNERS_REQUIRED` (default `false`). "Failing OPEN" is that file's name
for the gate passing bad code because the gate did not run. Reuse these words.

**Do not duplicate `tests/test_scanner_resilience.py`.** 1941 lines already cover
missing binaries, timeouts, non-zero exits, missing reports, malformed JSON,
wrong-shaped reports, wrong-*typed* inner report fields, a lost `+x` bit,
malformed argv, the `SCANNERS_REQUIRED` promotion, and the accepted
half-provisioned limit. It covers all of this **from the inside** — calling
`gitleaks_tool.scan`, `run_all_scanners`, or `security_agent.run` directly. The
gap this plan fills is the **black-box** view: what `run_pipeline` does end to end
under those same faults. Say which side of that line every assertion sits on.

**Nine batches of assertions in this repository have turned out to pin nothing.**
They passed against deliberately broken code. Therefore: **every test task below
carries a mandatory RED step naming the exact mutation to make and the exact test
that must fail.** A task whose RED step was not run is not done. A test that
cannot fail is worse than no test, because it reads as coverage.

**Instruments lie.** Two recorded cases in this repo: a recorder patching a seam
that a fixture had already replaced reported a reassuring zero while measuring
nothing; a same-size edit inside one mtime second left CPython serving stale
bytecode. Any task that *measures* must first prove its instrument can report the
failing case. Where a task counts something, it must also count it once under a
condition where the count is known to be different.

**Numbers in prose drift.** Two unmeasured counts reached "measured" prose in the
last task alone. Every count written into a docstring, a comment, or a Markdown
file by this plan must be produced by a command whose output is pasted, not
recalled.

**Hermeticity.** `tests/conftest.py` has four autouse fixtures closing three
seams: the model (`llm._complete` → `pytest.fail`), GitHub
(`github_ops._repo` → `pytest.fail`), the offline git workspace (redirected to
`tmp_path`), and the terminal (`builtins.input` → `pytest.fail`). Do not weaken
any of them. The `pytest.fail` raisers are load-bearing: `Failed` derives from
`BaseException`, so the blind `except Exception` handlers cannot swallow them.

**One measured trap for any test that manipulates PATH.** `github_ops.open_pr`
shells out to real `git` in the offline path. Replacing `os.environ["PATH"]`
wholesale — which is what `test_scanner_resilience.py`'s `_fake_scanner` helper
does, correctly, for its own inside-out tests — makes `git` unresolvable and
`run_pipeline` dies with `FileNotFoundError: [Errno 2] No such file or directory:
'git'` at `github_ops.py:114`. **Measured.** Any black-box provenance test must
*prepend* the fake-binary directory and keep `git`'s directory on PATH. Task 5's
fixture does this once so no other task repeats the mistake.

---

## Task order and why

Blocking relationships first:

1. **Task 1 [Reem] blocks Task 6, 7, 8 [Aya] and Task 9 [Reem].** `run_baseline`
   is the baseline column of the DORA table and Beat 4 of the demo script. It is
   40 lines and has no dependencies of its own. It goes first.
2. **Task 5 [shared] blocks Tasks 4, 6, 7, 11.** Nothing else in either lane can
   honestly claim a provenance mode until the fixture that controls provenance
   exists.
3. **Task 2 [Reem] blocks nothing** but is a two-line CI change closing a
   measured gap; doing it early keeps it from being cut.

Tasks 3 and 4 are independent of each other and of 6–8. Tasks 9–12 are Aug 24–27
process work that depends on everything above.

No two tasks write the same file. File ownership: Task 1 → `test_baseline.py`;
Task 2 → `ci.yml`; Task 3 → `test_chaos_gate_and_loop.py`; Task 4 →
`test_chaos_scanner.py`; Task 5 → `tests/provenance.py` + `test_provenance.py`;
Task 6 → `dora_runner.py` + `test_dora_harness.py`; Task 7 → `dora_batch.py` +
`test_dora_batch.py`; Task 8 → `dora_table.py`; Task 9 → `demo_script.md`.

---

## Task 1 [Reem]: the no-checks baseline — `run_baseline`

**Satisfies:** Reem wk2 "Sat–Mon Aug 15–17 — the no-checks baseline" (her whole
done-when for that day). Unblocks Aya wk2 Wed–Thu, Aya wk3 Sat–Sun and Mon, and
Reem wk3 Beat 4.

**Files:**
- Create: `tests/test_baseline.py`

**Why first:** it is the "before" half of the demo's headline comparison, three
other tasks import it, and it depends on nothing.

**Interfaces produced** — Task 6 imports this exact signature; do not change it:

```python
def run_baseline(ticket_text: str, *, poisoned: bool = False) -> RunState: ...
```

`POISON_KEY = "AKIAIOSFODNN7EXAMPLE"` is also exported (her spec names it).

**Steps:**

1. Create `tests/test_baseline.py` with this content. It is her spec's file with
   two additions justified below.

```python
"""The no-checks baseline: plan -> develop -> merge, with NO review, NO security,
NO gates. Owner: Reem.

This is the deliberate "before" picture. Without the Agent Org's checks the
poisoned change ships untouched -- that failure is the point. Aya's DORA harness
(tests/dora_runner.py) consumes run_baseline() as the baseline path.

WHY THIS FILE IS BOTH A MODULE AND A TEST FILE. `run_baseline` is imported by
tests/dora_runner.py, so it has to live somewhere importable; pyproject's
`pythonpath = ["."]` makes `from tests.test_baseline import run_baseline` work
from the repository root. Keeping it here rather than in a separate module is
what the spec asks for and keeps the "before" path in the same file as the two
tests that describe it.

WHAT THIS FILE DELIBERATELY DOES NOT DO: it never calls reviewer.run,
security.run, sre.run, gates.pause or github_ops.open_pr. That absence IS the
baseline. A future edit that "fixes" the baseline by adding a check destroys the
demo's contrast -- see test_the_baseline_runs_no_checks_at_all, which fails if
any of the five result fields is ever populated.

Run: pytest -q tests/test_baseline.py
"""

from agentorg.agents import developer, planner
from agentorg.state import RunState

# AWS's own public documentation placeholder -- nothing sensitive. Named here
# rather than inlined so the poisoned assertion below says what it is looking
# for.
POISON_KEY = "AKIAIOSFODNN7EXAMPLE"


def run_baseline(ticket_text: str, *, poisoned: bool = False) -> RunState:
    """Plan -> develop -> merge. No review, no security, no gates.

    Mirrors the shape of run_pipeline but strips every check, so a poisoned diff
    is never inspected and the change is marked promoted regardless.

    KEYWORD-ONLY `poisoned`, matching developer.run's own signature and
    run_pipeline's. Aya's DORA runner passes it by keyword; a positional second
    parameter here would let a caller silently pass a ticket id into it.
    """
    state = RunState(ticket_id="BASELINE", ticket_text=ticket_text)
    state.plan = planner.run(state)
    state.dev = developer.run(state, poisoned=poisoned)
    # "Merge" with no review/security/gates: just declare it shipped.
    state.status = "promoted"
    return state


def test_baseline_promotes_a_clean_change():
    state = run_baseline("Add a per-IP login rate limit.", poisoned=False)
    assert state.status == "promoted"
    assert state.plan is not None
    assert state.dev is not None


def test_the_baseline_runs_no_checks_at_all():
    """The absence of checks is the baseline's whole content, so assert it.

    Split out from the promotion test because these two assertions fail for
    different reasons: the one above breaks if the baseline stops shipping, this
    one breaks if the baseline starts checking. A single test conflating them
    would report "the baseline is wrong" without saying which way.
    """
    state = run_baseline("Add a per-IP login rate limit.", poisoned=True)
    assert state.review is None, "the baseline must not review"
    assert state.security is None, "the baseline must not scan"
    assert state.sre is None, "the baseline must not assess"
    assert state.decisions == [], "the baseline must not ask a human"
    assert state.revision_count == 0, "the baseline has no revision loop"


def test_baseline_ships_the_poisoned_change():
    """The whole point: with no security stage, the hardcoded key sails through.

    THIS TEST ASSERTS AN UNSAFE OUTCOME ON PURPOSE. It is the "before" picture
    the DORA table contrasts against, not a bug report. If it ever goes red
    because the baseline stopped shipping the poison, the contrast is gone and
    the demo's headline comparison has no left-hand column -- fix the baseline,
    do not relax the assertion.
    """
    state = run_baseline("Add a per-IP login rate limit.", poisoned=True)
    assert state.status == "promoted"          # it SHIPPED
    assert state.security is None              # nothing scanned it
    assert POISON_KEY in state.dev.diff        # the secret is right there

    # The negative control. Without it, a developer stub that planted the key in
    # BOTH diffs would satisfy the line above while destroying the contrast the
    # DORA table is built on.
    clean = run_baseline("Add a per-IP login rate limit.", poisoned=False)
    assert POISON_KEY not in clean.dev.diff, (
        "the clean baseline diff must not carry the key, or 'poisoned' means "
        "nothing and the baseline column of the DORA table is a coincidence"
    )
```

2. Run `pytest -q tests/test_baseline.py`. Expect `3 passed`. Her spec's
   done-when says `2 passed`; this file has three because the no-checks assertions
   were split out for the reason its docstring gives. Report the real number.
3. Run `ruff check agentorg scripts tests` and confirm exit 0. The import block is
   already sorted (`agentorg.agents` before `agentorg.state`).
4. Run the full suite: `pytest -q`. Expect **180 passed, 1 skipped** (177 + 3).
   Paste the actual line.

**Verify it can fail — mandatory RED step.** Three mutations, each must turn a
different test red:

- In `run_baseline`, change `state.status = "promoted"` to `state.status = "running"`.
  → `test_baseline_promotes_a_clean_change` and
  `test_baseline_ships_the_poisoned_change` go red. Revert.
- In `run_baseline`, add `from agentorg.agents import security` and
  `state.security = security.run(state)` before the status line.
  → `test_the_baseline_runs_no_checks_at_all` and
  `test_baseline_ships_the_poisoned_change` go red. Revert. **This is the important
  one:** it proves the file detects a baseline that started checking.
- In `run_baseline`, change `developer.run(state, poisoned=poisoned)` to
  `developer.run(state, poisoned=False)`. → `test_baseline_ships_the_poisoned_change`
  goes red on the `POISON_KEY in state.dev.diff` line. Revert. This is exactly the
  bug in Aya's spec'd `run_baseline_path` (divergence 7), so proving the test catches
  it here is what protects her consumer.

**Cost:** measured **0.06 ms per `run_baseline` call** — four calls in this file.
Negligible; it never touches the graph, the scanners, git, or the log.

**Do not:** add a `run_id`-bearing log to the baseline in this task. Task 6 owns
that decision and states the options.

---

## Task 2 [Reem]: run the target-app tests in CI

**Satisfies:** Reem wk2 "Thu–Fri Aug 20–21 — CI hookup", the one half of it that
is genuinely open.

**Files:**
- Modify: `.github/workflows/ci.yml` (append one step to the `test` job)

**Measured gap:** `grep -n target_repo .github/workflows/ci.yml` returns no
match. `cd target_repo && python -m pytest tests -q` prints `5 passed` locally, so
five real tests on the file the entire demo diffs against run in nobody's CI. Her
spec already names the exact step and says to ask Mariam for it; Mariam owns the
file, so this is a one-step PR against her lane, not a rewrite.

**Interfaces:** none. This is a workflow step.

**Steps:**

1. In `.github/workflows/ci.yml`, in the **`test`** job, after the existing
   `- name: Run tests` step, append:

```yaml
      # target_repo/ is a separate tiny project: `app` is importable only from
      # inside it, so the root `pytest -q` (testpaths = ["tests"]) never
      # collects these five tests. They cover app/auth.py -- the file every
      # poisoned and clean diff in this repo is written against -- so a change
      # that breaks the login handler would otherwise reach the demo with CI
      # green. Owner of the tests: Reem; owner of this workflow: Mariam.
      - name: Target app tests
        run: cd target_repo && python -m pytest tests -q
```

2. Confirm the step is inside the `test` job and not `lint` or `scan` — `scan`
   installs three binaries and a 108 MB database, and this step needs none of them.
3. Verify locally exactly as CI will: `cd target_repo && python -m pytest tests -q`.
   Expect `5 passed`.
4. `ruff check agentorg scripts tests` → exit 0 (unchanged; YAML is not linted, and
   `target_repo/` is deliberately outside the lint command).

**Verify it can fail — mandatory RED step.** In `target_repo/app/auth.py` change
`return _USERS.get(username) == password` to `return True`. Run
`cd target_repo && python -m pytest tests -q` and confirm it reports failures
(`test_authenticate_rejects_bad_password` and
`test_login_rejects_invalid_credentials`). Revert. Without this, the new step could
point at a directory whose tests always pass — precisely the "instruments lie"
failure: a green step that measures nothing.

**Decision you cannot settle from the spec: does this step need `flask` installed?**
- Option A: rely on the root `pip install -e ".[dev]"`, which pulls `flask` because
  `pyproject.toml`'s `[project].dependencies` lists it (verified: the `flask` line is
  uncommented and present).
- Option B: add a separate `pip install flask` step.
- **Recommend A.** `flask` is already a hard dependency of the root project and the
  `test` job runs `pip install -e ".[dev]"` before this step. Option B installs it
  twice and creates a second place where the version can drift.

**Note for the implementer:** this file is Mariam's. Land it as its own small commit
whose message says why, so she reviews one step rather than finding it inside a
test-heavy diff.

---

## Task 3 [Aya + Reem]: chaos — the hung gate, and what is already covered

**Satisfies:**
- **Aya wk2 "Sat–Sun — chaos test: hung gate + reviewer loop"**, Fault 1 in full;
  Fault 2 by pointing at the existing coverage rather than duplicating it.
- **Reem wk2 "Tue–Wed — happy path + revision loop"**, all three of her tests, by
  the same argument. Her file `tests/test_functional_flow.py` is **not created**.

**Files:**
- Create: `tests/test_chaos_gate_and_loop.py`

**This task deliberately writes fewer tests than the two specs ask for.** Read the
justification before implementing; it is the point of the task.

**What is already pinned, measured, with sites:**

| Spec item (Aya / Reem) | Already pinned at | What it asserts |
|---|---|---|
| Loop terminates at the cap | `tests/test_agent_fallbacks.py:731` | `revision_count == MAX_REVISION_LOOPS`, **plus** that the loop was a real revision loop (`YOUR PREVIOUS DIFF` reached the developer) — which equality alone does not prove |
| Loop terminates at the cap | `tests/test_gates_cli.py:247` | Same equality, **plus** `status == "failed"`, `sre is None`, and `decisions == [("gate1","approved")]` |
| Loop bounded in the log | `tests/test_gates_cli.py:299` | `len(capped_lines) == MAX_REVISION_LOOPS + 2`, and the cap-exit line's summary differs from the approve-exit line's |
| Loop fires once then approves | `tests/test_agent_fallbacks.py:824` | `revision_count == 1` **and** `calls["develop"] == 2` |
| Clean ticket → promoted | `tests/test_pipeline_smoke.py:10`, `tests/test_block_determinism.py:33` | `status == "promoted"`, `security.verdict == "pass"` |

Writing Aya's Fault 2 and Reem's three flow tests would add **five more
`run_pipeline` calls** (measured 60–74 ms each, so ~350 ms) to the slowest lane in
the suite, and each would be a weaker version of a test that already exists. Worse,
**Aya's spec'd log assertion is wrong**: `len(changes) <= MAX_REVISION_LOOPS` fails
against the shipped graph — measured **5 events against a cap of 3**, because the
graph logs three mid-loop revisions plus a cap-exit line plus a terminal
`action="blocked"` line, all carrying `verdict="changes_requested"`. Implementing
her spec verbatim produces a red test that looks like a product bug and is not one.

**So this task writes exactly one thing that does not exist: a gate seam that
raises.** Nothing in the suite covers it. `test_gates_cli.py` covers a human saying
no; that is `status="rejected"`, an orderly stop. A gate that never returns at all
is a different fault.

**Interfaces consumed:**

```python
graph.run_pipeline(ticket_id: str, ticket_text: str, *, poisoned: bool = False,
                   auto_approve: bool = True) -> RunState
gates.pause(state: RunState, gate: str) -> pathlib.Path   # the seam that is made to raise
gates.save(state: RunState) -> pathlib.Path               # run_pipeline's finally clause
log.read(run_id: str) -> list[LogEvent]
```

**Steps:**

1. Create `tests/test_chaos_gate_and_loop.py`:

```python
"""Chaos: a gate that never returns. Owner: Aya.

The fault must FAIL SAFE: a run whose gate never produces a decision must not
promote, and it must leave a readable record behind rather than vanishing.

WHY THIS FILE IS SHORT, AND WHY THAT IS THE FINDING. Aya's week-2 spec also asks
for a runaway-reviewer test, and Reem's week-2 spec asks for three flow tests.
All four are already pinned, in stronger form, by tests written for other tasks:

    revision_count == MAX_REVISION_LOOPS      test_agent_fallbacks.py:731
                                              test_gates_cli.py:247
    the log is bounded at cap + 2             test_gates_cli.py:299
    loop fires once, then approves            test_agent_fallbacks.py:824
    clean ticket promotes                     test_pipeline_smoke.py:10

Re-asserting them here would add five run_pipeline calls (~60-74 ms each) and pin
nothing new. One of them would also be WRONG as specified: the spec's
`len(changes_requested events) <= MAX_REVISION_LOOPS` measures 5 against a cap of
3, because the graph emits three mid-loop lines, a cap-exit line and a terminal
`action="blocked"` line, all with verdict="changes_requested". The correct count
is MAX_REVISION_LOOPS + 2 and test_gates_cli.py:299 already asserts it.

A gate seam that RAISES is the one fault in this area nothing covers.
test_gates_cli.py covers a human who says no -- an orderly stop, status
"rejected". This file covers a gate that never answers at all.

Run: pytest -q tests/test_chaos_gate_and_loop.py
"""

import pytest

from agentorg import gates, graph, log

TICKET_TEXT = "Add a per-IP login rate limit."


def test_a_gate_that_never_returns_aborts_before_promoting(monkeypatch):
    """A stuck human is a gate seam that never hands back a decision.

    Modelled as `gates.pause` raising, which is what an exhausted wait looks
    like from the graph's side: no HumanDecision is ever produced. The graph
    does not catch it, so the run cannot reach step 8's `status = "promoted"`.

    ASSERTING THE EXCEPTION ALONE WOULD PROVE NOTHING -- a function patched to
    raise, raises. So the assertions below are about the STATE the aborted run
    left behind: run_pipeline's finally clause calls gates.save, so a run that
    died at gate 1 must still be on disk, and it must not say "promoted".
    """
    saved = []
    real_save = gates.save

    def recording_save(state):
        saved.append((state.run_id, state.status))
        return real_save(state)

    def hung_gate(state, gate):
        raise TimeoutError(f"gate {gate} never got a human decision")

    monkeypatch.setattr(gates, "save", recording_save)
    monkeypatch.setattr(graph.gates, "pause", hung_gate)

    with pytest.raises(TimeoutError):
        graph.run_pipeline("CLEAN-1", TICKET_TEXT, poisoned=False)

    # THE INSTRUMENT FIRST: if the recorder never fired, every assertion below
    # is vacuously true and this test measures nothing. This repo has shipped
    # exactly that bug -- a recorder on a seam a fixture had already replaced,
    # reporting a reassuring zero.
    assert saved, (
        "run_pipeline's finally clause must have saved the aborted run; if this "
        "is empty the recorder is on the wrong seam and nothing below is real"
    )

    run_id, status = saved[-1]
    assert status != "promoted", (
        f"a run whose gate never answered ended {status!r}; the graph reaches "
        f"status='promoted' only after passing gate3, which a raising gate "
        f"makes unreachable"
    )
    assert status == "running", (
        f"expected the aborted run to be persisted mid-flight as 'running', "
        f"got {status!r} -- if this changed, the graph grew a handler for a "
        f"raising gate and that is what needs asserting instead"
    )

    # And nothing was logged as promoted, on the artifact the judges read.
    assert "promoted" not in [e.action for e in log.read(run_id)]


def test_the_hung_gate_stops_the_run_at_the_first_gate(monkeypatch):
    """Which gate it died at is the difference between a stop and a near-miss.

    Gate 1 sits after PLAN and before DEVELOP, so a run that aborts there never
    produced a diff at all. Without this, the test above would pass identically
    for a run that died at gate 3 with everything else already done -- a far
    weaker property.
    """
    def hung_gate(state, gate):
        raise TimeoutError(f"gate {gate} never got a human decision")

    monkeypatch.setattr(graph.gates, "pause", hung_gate)

    states = []
    real_save = gates.save
    monkeypatch.setattr(gates, "save", lambda s: (states.append(s), real_save(s))[1])

    with pytest.raises(TimeoutError):
        graph.run_pipeline("CLEAN-1", TICKET_TEXT, poisoned=False)

    assert states, "the recorder never fired; see the note in the test above"
    state = states[-1]
    assert state.plan is not None, "the plan stage runs before gate 1"
    assert state.dev is None, "gate 1 is before DEVELOP: no diff can exist"
    assert state.review is None
    assert state.security is None
    assert state.sre is None
    assert state.decisions == [], "no decision was ever recorded, which is the fault"
```

2. `pytest -q tests/test_chaos_gate_and_loop.py` → expect `2 passed`. Aya's spec's
   done-when says `3 passed`; report `2` and say the third was the runaway-reviewer
   test that already exists elsewhere.
3. `ruff check agentorg scripts tests` → exit 0.
4. `pytest -q` → expect **182 passed, 1 skipped** (180 after Task 1, + 2).

**Verify it can fail — mandatory RED step.** Two mutations:

- In `agentorg/graph.py`, wrap the `ask(state, gate)` call inside `_decide` in
  `try: ... except TimeoutError: return True`. This makes a hung gate *approve*.
  → both tests go red (`pytest.raises` sees no exception). **Revert immediately** —
  this is the mutation that would take down the demo, which is why it is the one
  worth proving detectable.
- Delete the `assert saved` instrument check and change `recording_save` to never
  append. Confirm the remaining assertions pass vacuously. This demonstrates why the
  instrument check is not decoration. Revert.

**Cost:** 2 `run_pipeline` calls, both aborting at gate 1 before any git work.
Measured well under the 60 ms of a full run — this is the cheapest task in the plan.

**Decision: `monkeypatch.setattr(gates, "save", ...)` vs reading `runs/`.**
- Option A (chosen): patch `gates.save` and capture the state object.
- Option B: read `runs/<run_id>.state.json` off disk after the abort.
- **Recommend A.** Option B cannot work: the run id is unknowable because the call
  raised, and `runs/` already holds **10,509 files** (measured) from prior runs, so
  scanning it for "the newest" is a race and an "X was written" test where thousands
  of other things wrote the same bytes. That is failure mode 3 from the lessons list.

---

## Task 4 [Aya]: chaos — a broken scanner, seen from outside the pipeline

**Satisfies:** Aya wk2 "Mon–Tue Aug 17–18 — chaos test: killed scanner (pairs with
Habiba)", reframed against what shipped. Depends on Task 5's fixture.

**Files:**
- Create: `tests/test_chaos_scanner.py`

**Read before implementing.** Her spec's three tests are all now wrong or covered:

1. `test_scanner_that_crashes_does_not_promote` patches `graph.security.run` to
   raise and asserts `pytest.raises(RuntimeError)`. **It proves that a function
   patched to raise, raises.** It patches the seam that *decides*, so the block rule,
   the fixture fallback and `run_all_scanners` are all bypassed. This is lesson 1:
   an assertion that passes against deliberately broken code.
2. `test_scanner_failsafe_finding_blocks_instead_of_silently_passing` builds a
   `SecurityResult` by hand, patches `graph.security.run` to return it, and asserts
   the graph blocks. It pins `graph.py`'s `if state.security.verdict == "block"`,
   which is already covered by every poisoned test in the suite. It does not touch
   Habiba's fail-safe at all. Her fail-safe is `error_finding` at `severity="high"`,
   already asserted directly at
   `test_scanner_resilience.py:225`
   (`test_error_finding_is_at_the_block_threshold_so_a_dead_scanner_fails_closed`).
3. `test_empty_scanner_result_on_poison_is_the_dangerous_case` asserts
   `status == "promoted"` on a poisoned ticket. **Measured: true.** But it asserts
   the fail-open as the expected result, so it goes red the day someone fixes it —
   lesson 2's shape, inverted.

**What is genuinely uncovered, and it is the black-box view.**
`tests/test_scanner_resilience.py` covers every fault from the inside — calling
`gitleaks_tool.scan`, `run_all_scanners`, or `security_agent.run` directly. Nobody
has asked what **`run_pipeline` end to end** does on the **poisoned** ticket when
the scanners are broken. I measured all six provenance/fault combinations through
`run_pipeline`:

| PATH state | knob | ticket | status | verdict | blocking | rules |
|---|---|---|---|---|---|---|
| all 3 absent | off | poisoned | `blocked` | `block` | **2** | the two AWS rules (fixture) |
| all 3 absent | on | poisoned | `blocked` | `block` | **3** | three `*-scanner-error` |
| semgrep absent, other 2 broken | off | poisoned | `blocked` | `block` | **2** | the two AWS rules (fixture) |
| semgrep absent, other 2 broken | on | poisoned | `blocked` | `block` | **3** | three `*-scanner-error` |
| all 3 present, all broken | off | poisoned | `blocked` | `block` | **3** | three `*-scanner-error` |
| all 3 present, all broken | off | **clean** | `blocked` | `block` | **3** | three `*-scanner-error` |

**Two facts worth the task on their own.** The poisoned ticket blocks in *every*
mode — the demo's claim is more robust than anyone has asserted. And `len(blocking)`
is **2 or 3 depending on provenance and the knob**, so Aya's existing week-1
`assert len(state.security.blocking) == 2` is provenance-dependent. It passes today
only because CI and her laptop have no binaries and the knob is off.

**Interfaces consumed:** Task 5's `provenance` fixture and `fake_scanner` helper,
plus:

```python
graph.run_pipeline(ticket_id, ticket_text, *, poisoned=False, auto_approve=True) -> RunState
config.SCANNERS_REQUIRED: bool                # read through the module, never imported bare
```

**Steps:**

1. Create `tests/test_chaos_scanner.py`:

```python
"""Chaos: broken scanners, seen from OUTSIDE the pipeline. Owner: Aya.

Pairs with Habiba's agentorg/security/_run.py, and uses her vocabulary: a binary
that is ABSENT is a development and CI affordance that keeps the fixture-fallback
path; one that is present and BROKEN is a FAULT that must block. This file adds
nothing to tests/test_scanner_resilience.py, which covers every fault from the
INSIDE by calling the wrappers and the security agent directly. What was missing
is the black-box view: what does run_pipeline do, end to end, when the scanners
misbehave?

MEASURED ANSWER, all six combinations, and it is better news than expected: the
poisoned ticket ends `blocked` in every one of them. What MOVES is the count --
len(blocking) is 2 when the fixture answered and 3 when three scanner faults were
reported. So `== 2` is a provenance-dependent assertion, which is exactly why
every test here declares its mode through the `provenance` fixture.

Run: pytest -q tests/test_chaos_scanner.py
"""

from agentorg import graph
from agentorg.common import config

TICKET_TEXT = "Add a per-IP login rate limit."


def test_the_poisoned_ticket_blocks_even_when_every_scanner_is_broken(provenance):
    """A FAULT must never become a silent pass, through the whole pipeline.

    All three binaries present and exiting non-zero. Habiba's wrappers turn each
    into a blocking error_finding at severity "high", compute_security_verdict
    blocks on it, and the graph halts. No fixture is involved: this is the real
    scanner path, faulting.
    """
    provenance.all_broken()

    state = graph.run_pipeline("POISON-1", TICKET_TEXT, poisoned=True)

    assert state.status == "blocked"
    assert state.security.verdict == "block"
    assert state.sre is None, "a blocked run must never reach SRE"

    # WHICH mechanism blocked it, not merely that something did. Three
    # scanner-error findings mean the wrappers reported faults; two AWS-key
    # findings would mean the FIXTURE answered and this test proved nothing
    # about a broken scanner.
    rules = {f.rule for f in state.security.blocking}
    assert rules == {
        "semgrep-scanner-error",
        "gitleaks-scanner-error",
        "trivy-scanner-error",
    }, (
        f"expected three reported faults; got {sorted(rules)}. If these are the "
        f"aws-* rules, the fixture fallback answered and no scanner fault was "
        f"exercised at all."
    )


def test_a_broken_scanner_blocks_a_CLEAN_change_too(provenance):
    """The fail-closed direction, on the ticket where it is visible.

    On a poisoned diff, "blocked" is the right answer for two independent
    reasons, so it cannot distinguish a working gate from a broken one. On a
    CLEAN diff there is nothing to find, so blocking can only come from the
    faults -- which makes this the assertion that proves the fault reached the
    verdict rather than the credentials did.
    """
    provenance.all_broken()

    state = graph.run_pipeline("CLEAN-1", TICKET_TEXT, poisoned=False)

    assert state.status == "blocked", (
        "three faulting scanners must block a clean change: the gate reports "
        "that it could not scan, rather than reporting that it found nothing"
    )
    assert len(state.security.blocking) == 3
    assert all(f.severity == "high" for f in state.security.blocking)


def test_the_poisoned_ticket_blocks_with_no_scanners_installed(provenance):
    """The ABSENT path -- how CI and every laptop in this team actually runs.

    This is the mode Aya's week-1 determinism test has always run in, and it is
    named here so that fact is written down somewhere: the verdict comes from
    fixtures/security_result_block.json, and compute_security_verdict is never
    called. The block is real; its PROVENANCE is a fixture.
    """
    provenance.none_installed()

    state = graph.run_pipeline("POISON-1", TICKET_TEXT, poisoned=True)

    assert state.status == "blocked"
    assert len(state.security.blocking) == 2
    assert provenance.answered_from_fixture(state), (
        "with no binaries installed the fixture must be what answered; if this "
        "is False, someone installed scanners and this test is silently "
        "measuring the other mode"
    )


def test_a_blind_scanner_is_the_one_way_the_poison_could_ship(monkeypatch):
    """The boundary, asserted as a boundary rather than as an expectation.

    compute_security_verdict([]) returns ("pass", []). So a fan-out that
    returned zero findings on a poisoned diff would promote it. Habiba's whole
    module exists to make that unreachable -- a fault becomes a finding, never
    [] -- and this test pins the consequence from the outside so the reason the
    guarantee matters stays visible.

    The patch is on `security.run_all_scanners`, i.e. BELOW the security agent,
    so the agent's real code, the real block rule and the real graph all run.
    Patching `graph.security.run` instead -- which the original spec did -- would
    bypass every one of them and assert only that a returned value is returned.
    """
    from agentorg.agents import security

    monkeypatch.setattr(security, "run_all_scanners", lambda dev: [])

    state = graph.run_pipeline("POISON-1", TICKET_TEXT, poisoned=True)

    # This IS the fail-open, and it is asserted so that a future fix is visible
    # as a red test with a note telling the reader what to do -- not as a
    # surprise.
    assert state.status == "promoted", (
        "an empty findings list on a poisoned diff currently promotes. If this "
        "is red, a guard now rejects an empty scan -- that is a FIX: replace "
        "this test with one asserting the new blocking behaviour."
    )
    assert state.security.verdict == "pass"
    assert state.security.blocking == []


def test_scanners_required_with_no_binaries_blocks_the_CLEAN_run_too(provenance,
                                                                     monkeypatch):
    """THE CONFIGURATION TRAP, pinned from the outside. See Task 5's note.

    SCANNERS_REQUIRED promotes ABSENT to FAULT. On a machine with the binaries
    installed that is exactly right. On a machine WITHOUT them it converts a
    fail-open into a fail-everything: the clean half of the demo blocks.

    So the two demo-prep actions are an ORDERED PAIR -- install the binaries,
    THEN set the knob. This test is what makes that ordering a fact in the suite
    instead of a sentence in a runbook.
    """
    provenance.none_installed()
    monkeypatch.setattr(config, "SCANNERS_REQUIRED", True)

    clean = graph.run_pipeline("CLEAN-1", TICKET_TEXT, poisoned=False)

    assert clean.status == "blocked", (
        "with the knob on and no binaries installed, the CLEAN run blocks -- "
        "three absent scanners promoted to faults. This is the trap: the knob "
        "is only safe once the binaries are installed."
    )
    assert {f.rule for f in clean.security.blocking} == {
        "semgrep-scanner-error",
        "gitleaks-scanner-error",
        "trivy-scanner-error",
    }

    # And the poisoned run still blocks -- but on three faults, not on the two
    # AWS findings the demo script narrates. Both halves of the demo are wrong
    # in this configuration, in different ways.
    poisoned = graph.run_pipeline("POISON-1", TICKET_TEXT, poisoned=True)
    assert poisoned.status == "blocked"
    assert len(poisoned.security.blocking) == 3, (
        "the narrated line is 'blocking=2, the access key and the secret key'; "
        "in this configuration it is 3 scanner errors instead"
    )
```

2. `pytest -q tests/test_chaos_scanner.py` → expect `5 passed`.
3. `ruff check agentorg scripts tests` → exit 0.
4. `pytest -q` → expect **187 passed, 1 skipped**.

**Verify it can fail — mandatory RED step.** Four mutations, one per mechanism:

- In `agentorg/security/_run.py`, change `error_finding`'s `severity="high"` to
  `"medium"`. → `test_the_poisoned_ticket_blocks_even_when_every_scanner_is_broken`
  and `test_a_broken_scanner_blocks_a_CLEAN_change_too` go red. Revert. This is the
  exact regression `_run.py`'s docstring says would "silently revert this lane to
  failing open".
- In `agentorg/security/_run.py`, make `unrunnable_findings` `return []` instead of
  `[error_finding(...)]` for the fault branch. → the same two tests go red. Revert.
- In `tests/test_chaos_scanner.py`, change the `rules ==` assertion in the first
  test to `assert state.status == "blocked"` only, then run it with
  `provenance.none_installed()` instead. Confirm it still passes — proving the rule
  assertion is what distinguishes the modes. Revert.
- In `agentorg/graph.py`, change `if state.security.verdict == "block":` to
  `if False:`. → the first, second and fifth tests go red. Revert.

**Cost.** 7 `run_pipeline` calls. The fault path costs *less* than a normal run:
each broken scanner is a `/bin/sh` exiting immediately, versus the fixture path's
JSON load. Measured comparable at ~60–75 ms per run, so this file is **≈0.5 s**. It
does **not** need the real binaries and must not require them — every mode here is
synthesised by Task 5's fake-binary directory.

**Do not:** duplicate any timeout test. `test_scanner_resilience.py` has four,
each costing a real 1.01 s of wall clock (measured — they are 5 of the 6 slowest
tests in the suite). A black-box timeout test would cost the same second *plus* a
pipeline run, and would pin nothing the inside-out version does not.

---

## Task 5 [Aya + Reem, shared]: the provenance fixture — one place that controls which mode a test runs in

**Satisfies:**
- **Aya wk2 Fri Aug 21 "hard-deadline re-verify"** — turns a ritual into a command
  that names its own mode.
- **Reem wk2 Thu–Fri "Aug 21 deadline check"** — same check, same command; her spec
  and Aya's ask for the same thing on the same day, so it is planned once.
- Prerequisite for Tasks 4, 6, 7 and 11.

**Files:**
- Create: `tests/provenance.py`
- Create: `tests/test_provenance.py`
- Modify: `tests/conftest.py` — **append only**, one fixture registration, four
  autouse guards untouched

**Why this exists and why it goes before the metrics work.** From the Global
Constraints: both provenance modes produce `blocked / block / blocking=2` on the
poisoned fixture. So "10/10 blocked" is two different claims, and today **no test
in the repository states which one it is making**. Aya's determinism test, her
shape-stability test, the smoke test and every `len(blocking) == 2` assertion all
run in fixture-fallback mode and none of them say so. That is not a bug in those
tests — it is a missing vocabulary. This task supplies it once, so that six other
tests do not each invent a different half-correct version.

**Interfaces produced** — Tasks 4, 6, 7 and 11 use these exact names:

```python
# tests/provenance.py

FIXTURE_LINES: frozenset[int]          # {4, 5} -- security_result_block.json
REAL_SCANNER_LINES: frozenset[int]     # {3, 4} -- scan_gate.py EXPECTED_BLOCKING
SCANNER_TOOLS: tuple[str, str, str]    # ("semgrep", "gitleaks", "trivy")

def binaries_installed() -> list[str]: ...
def answered_from_fixture(state: RunState) -> bool: ...
def answered_from_real_scanners(state: RunState) -> bool: ...
def describe_mode() -> str: ...

class Provenance:                       # what the `provenance` fixture yields
    def none_installed(self) -> None: ...
    def all_broken(self) -> None: ...
    def some_absent_others_broken(self, absent: str) -> None: ...
    def fake_scanner(self, tool: str, script: str) -> pathlib.Path: ...
    @staticmethod
    def answered_from_fixture(state: RunState) -> bool: ...
```

**Steps:**

1. Create `tests/provenance.py`:

```python
"""Which scanner-provenance mode is this test running in? Owner: Aya.

THE PROBLEM THIS SOLVES, AND IT IS THE MOST CONFUSING THING IN THIS REPOSITORY.
The three scanner binaries are not on the default PATH. Without them every
wrapper raises FileNotFoundError, agents/security.py catches it, and the verdict
comes from fixtures/security_result_block.json -- `compute_security_verdict` is
never called. With them, the fan-out runs for real and the rule decides.

BOTH MODES PRODUCE THE SAME HEADLINE: status="blocked", verdict="block",
len(blocking) == 2, rules {aws-access-key-id, aws-secret-access-key}, severity
"critical". So "the poisoned ticket blocked 10 out of 10" is a claim about the
BLOCK RULE in one mode and a claim about JSON DESERIALISATION in the other, and
nothing in the suite used to say which.

THE DISCRIMINATOR IS THE LINE NUMBER, and it is the only field that differs.
MEASURED:

    fixture (security_result_block.json)   access key line 4, secret line 5
    real gitleaks 8.21.2 (scan_gate.py)    access key line 3, secret line 4

Both report tool="gitleaks", the same two rule names, the same file
"app/auth.py", and severity "critical". The line numbers differ because the
fixture was written against a slightly different rendering of the poisoned diff
than the one common/diff.py materialises. That divergence is load-bearing here
and must not be "fixed" by aligning the fixture: aligning them would remove the
only signal that tells a reader which path answered.

FRAGILITY, STATED HONESTLY: this discriminator breaks if the fixture is
regenerated to lines 3/4, or if gitleaks' reported lines move. Both are
possible. So `answered_from_fixture` cross-checks against
`binaries_installed()` and raises rather than guessing when the two disagree --
a wrong answer here would silently relabel every metric in the DORA table.
"""

import pathlib
import shutil

from agentorg.state import RunState

# Mirrors run_all_scanners' fan-out order in agentorg/security/__init__.py.
# semgrep is FIRST, which matters: the knob-off ABSENT path signals absence by
# raising, and one raise ends the loop -- see _run.py's accepted-limit section.
SCANNER_TOOLS = ("semgrep", "gitleaks", "trivy")

# fixtures/security_result_block.json, measured.
FIXTURE_LINES = frozenset({4, 5})

# scripts/scan_gate.py EXPECTED_BLOCKING, measured on gitleaks 8.21.2.
REAL_SCANNER_LINES = frozenset({3, 4})

_AWS_RULES = frozenset({"aws-access-key-id", "aws-secret-access-key"})


def binaries_installed() -> list[str]:
    """Which of the three scanners `shutil.which` can find, in fan-out order."""
    return [tool for tool in SCANNER_TOOLS if shutil.which(tool) is not None]


def describe_mode() -> str:
    """One line naming the ambient mode, for a test failure message or a report."""
    installed = binaries_installed()
    if not installed:
        return "FIXTURE-FALLBACK mode: no scanner binaries on PATH"
    if len(installed) == len(SCANNER_TOOLS):
        return "REAL-SCANNER mode: all three binaries on PATH"
    return (
        f"HALF-PROVISIONED: only {installed} on PATH -- the absent one raises and "
        f"ends the fan-out unless SCANNERS_REQUIRED is set"
    )


def _aws_lines(state: RunState) -> frozenset[int]:
    """Line numbers of the two AWS-credential findings, or an empty set."""
    if state.security is None:
        return frozenset()
    return frozenset(
        f.line for f in state.security.blocking if f.rule in _AWS_RULES
    )


def answered_from_fixture(state: RunState) -> bool:
    """Did the FIXTURE produce this verdict, rather than the real scanners?

    Raises RuntimeError when the line numbers and the installed binaries
    disagree, instead of returning a plausible guess. A silent wrong answer here
    would mislabel the provenance column of every DORA row, and a mislabelled
    metric is worse than a missing one -- it reads as evidence.
    """
    lines = _aws_lines(state)
    installed = binaries_installed()

    if not lines:
        # No AWS findings at all: either a clean run or a fault-reported run.
        # Provenance is not answerable from the findings, so fall back to PATH.
        return not installed

    if lines == FIXTURE_LINES:
        if len(installed) == len(SCANNER_TOOLS):
            raise RuntimeError(
                f"findings carry the FIXTURE line numbers {sorted(lines)} but all "
                f"three binaries are installed. Either the real scanners now "
                f"report these lines, or the fan-out silently fell back. Do not "
                f"guess: re-measure scripts/scan_gate.py's EXPECTED_BLOCKING and "
                f"update REAL_SCANNER_LINES."
            )
        return True

    if lines == REAL_SCANNER_LINES:
        if not installed:
            raise RuntimeError(
                f"findings carry the REAL-SCANNER line numbers {sorted(lines)} but "
                f"no binaries are on PATH. The fixture has probably been "
                f"regenerated onto lines 3/4, which destroys this discriminator. "
                f"Re-measure FIXTURE_LINES."
            )
        return False

    raise RuntimeError(
        f"AWS findings on lines {sorted(lines)}, which match neither the fixture "
        f"{sorted(FIXTURE_LINES)} nor the real scanners "
        f"{sorted(REAL_SCANNER_LINES)}. Provenance is unknown; re-measure both "
        f"before trusting any metric built on this run."
    )


def answered_from_real_scanners(state: RunState) -> bool:
    """The complement of answered_from_fixture, with the same raising behaviour."""
    return not answered_from_fixture(state)


class Provenance:
    """Puts a test into a chosen provenance mode. Yielded by the `provenance` fixture.

    WHY PATH IS PREPENDED AND NEVER REPLACED -- MEASURED, AND IT IS A REAL TRAP.
    tests/test_scanner_resilience.py's own `_fake_scanner` helper REPLACES
    os.environ["PATH"] with its fake directory, which is correct for its
    inside-out tests: they call gitleaks_tool.scan directly and never touch git.

    A black-box test cannot do that. github_ops.open_pr runs real `git init` /
    `checkout -B` / `add` / `commit` in the offline path that conftest.py forces
    on every test. With PATH replaced, `git` is unresolvable and run_pipeline
    dies with:

        FileNotFoundError: [Errno 2] No such file or directory: 'git'
        at agentorg/github_ops.py:114, in _ensure_offline_repo

    -- before the security stage is ever reached, so a test written that way
    fails for a reason that has nothing to do with scanners. So this class
    PREPENDS its directory and leaves the rest of PATH intact.
    """

    def __init__(self, bin_dir: pathlib.Path, monkeypatch) -> None:
        self._bin = bin_dir
        self._monkeypatch = monkeypatch
        self._bin.mkdir(parents=True, exist_ok=True)

    def _activate(self) -> None:
        """Prepend the fake directory, keeping the real PATH (and git) behind it."""
        self._monkeypatch.setenv("PATH", str(self._bin), prepend=":")

    def fake_scanner(self, tool: str, script: str) -> pathlib.Path:
        """Create an executable fake for one tool and put it first on PATH."""
        if tool not in SCANNER_TOOLS:
            raise ValueError(f"{tool!r} is not one of {SCANNER_TOOLS}")
        path = self._bin / tool
        path.write_text(f"#!/bin/sh\n{script}\n", encoding="utf-8")
        path.chmod(0o755)
        self._activate()
        return path

    def none_installed(self) -> None:
        """ABSENT for all three: CI's mode, and every laptop on this team.

        Prepends an EMPTY directory and also asserts the real ones are not
        reachable, because a machine that happens to have them installed would
        otherwise run a different test than the one that was written.
        """
        self._activate()
        found = binaries_installed()
        if found:
            raise RuntimeError(
                f"none_installed() cannot make {found} disappear: they are on "
                f"PATH behind the fake directory. Prepending cannot hide a real "
                f"binary. Run this test in a shell without them, or use "
                f"all_broken(), which shadows them instead."
            )

    def all_broken(self) -> None:
        """FAULT for all three: present, and exiting non-zero with stderr."""
        for tool in SCANNER_TOOLS:
            self.fake_scanner(tool, 'echo "internal error" >&2\nexit 2')

    def some_absent_others_broken(self, absent: str = "semgrep") -> None:
        """The HALF-PROVISIONED LAPTOP that _run.py's accepted limit describes.

        One scanner absent, the others present and broken. Defaults to semgrep
        because it runs FIRST in the fan-out, which is what makes its raise abort
        the loop before the other two faults are reported.
        """
        if absent not in SCANNER_TOOLS:
            raise ValueError(f"{absent!r} is not one of {SCANNER_TOOLS}")
        for tool in SCANNER_TOOLS:
            if tool != absent:
                self.fake_scanner(tool, 'echo "internal error" >&2\nexit 2')
        self._activate()
        if (self._bin / absent).exists():
            raise RuntimeError(f"{absent} must be the absent one, but a fake exists")

    @staticmethod
    def answered_from_fixture(state: RunState) -> bool:
        return answered_from_fixture(state)
```

2. Append to `tests/conftest.py` — do not touch the four autouse fixtures:

```python
@pytest.fixture()
def provenance(tmp_path, monkeypatch):
    """Put this test into a chosen scanner-provenance mode. See tests/provenance.py.

    NOT autouse: a test that does not ask for it keeps the ambient mode, which is
    what the existing suite relies on. Asking for it is how a test declares that
    its result depends on which mode produced the verdict.
    """
    from tests.provenance import Provenance

    return Provenance(tmp_path / "scanner-bin", monkeypatch)
```

3. Create `tests/test_provenance.py` — **the instrument must be tested before
   anything trusts it**:

```python
"""The provenance discriminator, tested against both modes. Owner: Aya.

An instrument that cannot report the failing case is not an instrument. This repo
has shipped two that could not: a recorder patched onto a seam a fixture had
already replaced, reporting a reassuring zero; and a same-size edit inside one
mtime second that left CPython serving stale bytecode. tests/provenance.py is
about to label every row of the DORA table, so it is pinned here first.

Run: pytest -q tests/test_provenance.py
"""

import pytest

from agentorg import graph
from agentorg.state import Finding, RunState, SecurityResult
from tests import provenance as prov

TICKET_TEXT = "Add a per-IP login rate limit."


def _state_with_lines(first: int, second: int) -> RunState:
    """A RunState carrying two AWS findings at chosen line numbers."""
    findings = [
        Finding(tool="gitleaks", severity="critical", rule="aws-access-key-id",
                file="app/auth.py", line=first, description="access key"),
        Finding(tool="gitleaks", severity="critical", rule="aws-secret-access-key",
                file="app/auth.py", line=second, description="secret key"),
    ]
    state = RunState(ticket_id="P", ticket_text=TICKET_TEXT)
    state.security = SecurityResult(verdict="block", findings=findings,
                                    blocking=findings, explanation="x")
    return state


def test_the_fixture_lines_and_the_real_scanner_lines_do_not_overlap():
    """The whole discriminator rests on this. If they ever coincide, it is dead."""
    assert prov.FIXTURE_LINES != prov.REAL_SCANNER_LINES
    assert prov.FIXTURE_LINES == frozenset({4, 5})
    assert prov.REAL_SCANNER_LINES == frozenset({3, 4})


def test_fixture_line_numbers_are_recognised_as_the_fixture():
    """Runs in whatever mode this machine is in; only meaningful without binaries."""
    if prov.binaries_installed():
        pytest.skip(f"needs a machine with no scanners: {prov.describe_mode()}")
    assert prov.answered_from_fixture(_state_with_lines(4, 5)) is True


def test_real_scanner_line_numbers_raise_when_no_binaries_are_installed():
    """The instrument must REFUSE rather than guess when the signals disagree."""
    if prov.binaries_installed():
        pytest.skip(f"needs a machine with no scanners: {prov.describe_mode()}")
    with pytest.raises(RuntimeError, match="no binaries are on PATH"):
        prov.answered_from_fixture(_state_with_lines(3, 4))


def test_unknown_line_numbers_raise_rather_than_defaulting():
    """A third line pair means both pins are stale. Refuse, do not pick one."""
    with pytest.raises(RuntimeError, match="matches neither"):
        prov.answered_from_fixture(_state_with_lines(11, 12))


def test_a_real_pipeline_run_is_labelled_by_the_discriminator(provenance):
    """End to end: the label the DORA table will print must match reality."""
    if prov.binaries_installed():
        pytest.skip(f"needs a machine with no scanners: {prov.describe_mode()}")
    provenance.none_installed()

    state = graph.run_pipeline("POISON-1", TICKET_TEXT, poisoned=True)

    assert state.status == "blocked"
    assert prov.answered_from_fixture(state) is True
    assert prov.answered_from_real_scanners(state) is False


def test_the_fake_scanner_directory_keeps_git_reachable(provenance):
    """THE MEASURED TRAP: replacing PATH breaks github_ops' real git calls.

    A fake-binary directory that REPLACES PATH makes run_pipeline die at
    github_ops.py:114 with FileNotFoundError for 'git', before the security
    stage. Prepending keeps git reachable. This test is what stops someone
    "simplifying" Provenance._activate into a setenv without prepend.
    """
    provenance.all_broken()

    state = graph.run_pipeline("POISON-1", TICKET_TEXT, poisoned=True)

    # It got past open_pr, which means git resolved.
    assert state.dev is not None and state.dev.pr_url, (
        "open_pr must have run; if this is None, PATH was replaced rather than "
        "prepended and git was unreachable"
    )
    assert state.status == "blocked"
    assert {f.rule for f in state.security.blocking} == {
        "semgrep-scanner-error", "gitleaks-scanner-error", "trivy-scanner-error",
    }


def test_describe_mode_names_the_ambient_mode():
    """Used in skip messages and in the DORA report header, so it must be true."""
    described = prov.describe_mode()
    if prov.binaries_installed():
        assert "REAL-SCANNER" in described or "HALF-PROVISIONED" in described
    else:
        assert "FIXTURE-FALLBACK" in described
```

4. `pytest -q tests/test_provenance.py` → expect `7 passed` on a machine with no
   binaries. On a machine with all three, expect `4 passed, 3 skipped`. Report which.
5. `ruff check agentorg scripts tests` → exit 0. Watch `I001`: in
   `test_provenance.py` the first-party block is
   `agentorg`, then `agentorg.state`, then `tests`.
6. `pytest -q` → expect **194 passed, 1 skipped** (187 after Task 4, + 7).

**Verify it can fail — mandatory RED step.**

- In `tests/provenance.py`, change `FIXTURE_LINES` to `frozenset({3, 4})`. →
  `test_the_fixture_lines_and_the_real_scanner_lines_do_not_overlap` goes red.
  Revert.
- In `Provenance._activate`, change
  `self._monkeypatch.setenv("PATH", str(self._bin), prepend=":")` to
  `self._monkeypatch.setenv("PATH", str(self._bin))`. →
  `test_the_fake_scanner_directory_keeps_git_reachable` goes red with the `git`
  `FileNotFoundError`. Revert. **This is the mutation that matters most:** it is the
  exact mistake a reader would make by copying `_fake_scanner` from
  `test_scanner_resilience.py`.
- In `answered_from_fixture`, replace each `raise RuntimeError` with
  `return not installed`. → `test_real_scanner_line_numbers_raise_when_no_binaries_are_installed` and `test_unknown_line_numbers_raise_rather_than_defaulting` go red.
  Revert.

**Cost:** 2 `run_pipeline` calls plus five pure-Python checks. Measured ≈0.2 s.

**Decision: line numbers vs. instrumenting `compute_security_verdict`.**
- Option A (chosen): the line-number discriminator, plus a `shutil.which` cross-check
  that raises on disagreement.
- Option B: install a counter on `state.compute_security_verdict` and assert it was
  called.
- Option C: assert on `shutil.which` alone.
- **Recommend A.** Option B is the recorded instrument-lies failure in this repo: the
  fixture path never calls `compute_security_verdict`, so a counter reads zero — but a
  counter also reads zero if it was attached to the wrong module object, and the two
  are indistinguishable from the test's side. Option C cannot detect the case that
  actually matters — binaries installed but the fan-out silently fell back to the
  fixture, which is what the accepted half-provisioned limit produces. A reads the
  *evidence in the verdict itself* and uses C only as a contradiction check. Its
  fragility is real and is written into the module docstring with instructions.

**Also deliver, as part of this task, the Aug 21 / Aug 25 go-no-go command** — this is
what Aya's Fri Aug 21 spec task and Reem's Aug 21 spec task both reduce to:

```bash
# Mode 1 -- the mode CI and every laptop runs. The block comes from the FIXTURE.
pytest -q tests/test_block_determinism.py tests/test_chaos_gate_and_loop.py \
          tests/test_chaos_scanner.py tests/test_provenance.py \
          tests/test_baseline.py tests/test_dora_harness.py
python -c "from tests.provenance import describe_mode; print(describe_mode())"

# Mode 2 -- the demo machine, binaries installed. The block comes from the RULE.
#   Install semgrep 1.172.0, gitleaks 8.21.2, trivy 0.74.0 first, then:
python scripts/scan_gate.py          # must print SCAN OK
python -m agentorg.graph             # must print status=promoted
python -m agentorg.graph --poisoned  # must print status=blocked, blocking=2
python -c "from tests.provenance import describe_mode; print(describe_mode())"
```

**Report both modes' output.** "It blocked" from mode 1 alone is not the claim the
demo makes. If mode 2 has never been run before Aug 25, the central claim has never
been observed.

---

## Task 6 [Aya]: the DORA runner

**Satisfies:** Aya wk2 "Wed–Thu Aug 19–20 — the metrics harness (DORA runner)", in
full. Depends on Task 1 (`run_baseline`) and Task 5 (provenance labelling).

**Files:**
- Create: `tests/dora_runner.py`
- Create: `tests/test_dora_harness.py`

**Three defects in the spec'd runner, all measured, all fixed below:**

1. `run_baseline_path` calls `run_baseline(ticket_text)` with **no `poisoned`
   argument**, then reports `bad_change_shipped = poisoned and status in (...)`.
   Measured: that produces the **clean** diff, so the baseline column of the headline
   table would report "shipped a poisoned change" for a diff containing no secret.
   The single most consequential bug in either spec — it fabricates the left-hand
   column of the judged comparison.
2. `_step_count` returns **0** for the baseline. Measured: `run_baseline` never calls
   `log.append`, so `log.read(state.run_id)` is empty. The table's "Avg pipeline steps"
   baseline cell would read `0`, which a judge reads as missing data.
3. Neither row records **which provenance mode** produced it. Task 5 exists so this
   can be fixed here.

**Interfaces produced** — Task 7 and Task 8 consume these exactly:

```python
@dataclass(frozen=True)
class DoraRow:
    ticket_id: str
    path: str                 # "baseline" | "agent_org"
    poisoned: bool
    final_status: str
    bad_change_shipped: bool
    step_count: int
    lead_time_s: float
    checks_run: int           # NEW -- see the decision below
    provenance: str           # NEW -- "fixture" | "real_scanners" | "n/a"

def run_agent_org(ticket_id: str, ticket_text: str, poisoned: bool) -> DoraRow: ...
def run_baseline_path(ticket_id: str, ticket_text: str, poisoned: bool) -> DoraRow: ...
def rows_to_dicts(rows: list[DoraRow]) -> list[dict]: ...
```

**Steps:**

1. Create `tests/dora_runner.py`:

```python
"""DORA metrics runner. Owner: Aya.

Runs one ticket through one path and returns one row of raw metrics. Consumed by
test_dora_harness.py and by tests/dora_batch.py, which builds the deck table.

THREE THINGS THIS FILE DOES DIFFERENTLY FROM THE WEEK-2 SPEC, each measured:

  1. The baseline is run with the SAME `poisoned` flag as the Agent Org path.
     The spec called `run_baseline(ticket_text)` with no flag, which produces the
     CLEAN diff, and then reported bad_change_shipped=True for it because the
     row's `poisoned` field said so. That would have put a fabricated number in
     the left-hand column of the judged comparison.

  2. `step_count` is not `len(log.read(run_id))` for the baseline. run_baseline
     writes NO log -- measured 0 events -- so that expression returns 0, which
     reads as "no data" rather than "no checks". The baseline's step count is
     counted from the stages it actually ran, and `checks_run` carries the
     number that makes the contrast legible: how many CHECKS each path applied.

  3. Every row records its scanner PROVENANCE. Both modes block the poisoned
     ticket with blocking=2, so a table that does not say which mode produced it
     is reporting two different claims under one number. See tests/provenance.py.
"""

import time
from dataclasses import asdict, dataclass

from agentorg import log
from agentorg.graph import run_pipeline
from tests import provenance as prov
from tests.test_baseline import run_baseline

# The stages the Agent Org path applies that the baseline does not. Counted
# rather than derived, because the point of the DORA table is the CONTRAST and a
# derived number would move silently if a stage were added.
AGENT_ORG_CHECKS = ("review", "security", "gate1", "gate2", "gate3", "sre")
BASELINE_CHECKS = ()


@dataclass(frozen=True)
class DoraRow:
    """One measured run. Frozen: a row is evidence, and evidence is not edited."""

    ticket_id: str
    path: str            # "baseline" | "agent_org"
    poisoned: bool
    final_status: str    # RunState.status
    bad_change_shipped: bool
    step_count: int
    lead_time_s: float
    checks_run: int
    provenance: str      # "fixture" | "real_scanners" | "n/a"


def _step_count(run_id: str) -> int:
    """One row per logged event in the append-only log."""
    return len(log.read(run_id))


def run_agent_org(ticket_id: str, ticket_text: str, poisoned: bool) -> DoraRow:
    """The full pipeline: five agents, three gates, the deterministic block rule."""
    t0 = time.perf_counter()
    state = run_pipeline(ticket_id, ticket_text, poisoned=poisoned)
    lead = time.perf_counter() - t0

    # A bad change "ships" only if a poisoned ticket ended promoted. A clean
    # ticket ending promoted is the correct outcome, not a shipped defect.
    shipped = poisoned and state.status == "promoted"

    try:
        answered_by_fixture = prov.answered_from_fixture(state)
        provenance = "fixture" if answered_by_fixture else "real_scanners"
    except RuntimeError:
        # provenance.py raises rather than guessing when its two signals
        # disagree. Record that instead of a plausible label -- an unknown
        # provenance is information; a wrong one is a fabricated metric.
        provenance = "unknown"

    return DoraRow(
        ticket_id=ticket_id,
        path="agent_org",
        poisoned=poisoned,
        final_status=state.status,
        bad_change_shipped=shipped,
        step_count=_step_count(state.run_id),
        lead_time_s=round(lead, 4),
        checks_run=len(AGENT_ORG_CHECKS),
        provenance=provenance,
    )


def run_baseline_path(ticket_id: str, ticket_text: str, poisoned: bool) -> DoraRow:
    """The no-checks path: plan -> develop -> merge. Reem owns run_baseline.

    `poisoned` is PASSED THROUGH. The week-2 spec omitted it, which ran the clean
    diff and then labelled the row as having shipped poison. See this module's
    docstring.
    """
    t0 = time.perf_counter()
    state = run_baseline(ticket_text, poisoned=poisoned)
    lead = time.perf_counter() - t0

    shipped = poisoned and state.status == "promoted"

    # Two stages ran -- plan and develop -- and neither is logged, because the
    # baseline has no log. Counted from the state rather than read from a log
    # that does not exist, so the number is 2 rather than a misleading 0.
    steps = sum(1 for result in (state.plan, state.dev) if result is not None)

    return DoraRow(
        ticket_id=ticket_id,
        path="baseline",
        poisoned=poisoned,
        final_status=state.status,
        bad_change_shipped=shipped,
        step_count=steps,
        lead_time_s=round(lead, 4),
        checks_run=len(BASELINE_CHECKS),
        # The baseline never runs a scanner, so provenance does not apply. Saying
        # "n/a" rather than "fixture" keeps the column honest.
        provenance="n/a",
    )


def rows_to_dicts(rows: list[DoraRow]) -> list[dict]:
    """JSON-serialisable form, for tests/dora_batch.py's report file."""
    return [asdict(row) for row in rows]
```

2. Create `tests/test_dora_harness.py`:

```python
"""The DORA harness produces correct raw numbers. Owner: Aya.

Run: pytest -q tests/test_dora_harness.py
"""

from tests import provenance as prov
from tests.dora_runner import DoraRow, run_agent_org, run_baseline_path, rows_to_dicts

TICKET_TEXT = "Add a per-IP login rate limit."


def test_agent_org_blocks_poison_so_no_bad_change_ships():
    row = run_agent_org("POISON-1", TICKET_TEXT, poisoned=True)
    assert isinstance(row, DoraRow)
    assert row.final_status == "blocked"
    assert row.bad_change_shipped is False
    assert row.step_count > 0
    assert row.lead_time_s > 0, "a real run cannot take zero measurable time"
    assert row.provenance in ("fixture", "real_scanners")


def test_agent_org_promotes_a_clean_change():
    row = run_agent_org("CLEAN-1", TICKET_TEXT, poisoned=False)
    assert row.final_status == "promoted"
    assert row.bad_change_shipped is False, (
        "a clean change being promoted is the correct outcome, not a defect"
    )
    assert row.step_count > 0


def test_the_baseline_row_reports_the_poison_it_actually_shipped():
    """The bug this test exists for: the spec ran the CLEAN diff here.

    Asserts the two halves that together mean the number is real -- the row says
    it shipped a bad change, AND the diff it shipped actually carried the key.
    """
    from tests.test_baseline import POISON_KEY, run_baseline

    row = run_baseline_path("POISON-1", TICKET_TEXT, poisoned=True)
    assert row.final_status == "promoted"
    assert row.bad_change_shipped is True
    assert row.provenance == "n/a", "the baseline runs no scanner"

    # The corroborating half: the diff really did carry the credential.
    state = run_baseline(TICKET_TEXT, poisoned=True)
    assert POISON_KEY in state.dev.diff, (
        "the baseline row claims a poisoned change shipped; if the diff has no "
        "key then the claim is fabricated, which is what the spec'd runner did"
    )


def test_the_clean_baseline_row_is_not_counted_as_shipped_poison():
    """The negative control on the same field. Without it, bad_change_shipped
    could be hardwired True for the baseline and both assertions above pass."""
    row = run_baseline_path("CLEAN-1", TICKET_TEXT, poisoned=False)
    assert row.final_status == "promoted"
    assert row.bad_change_shipped is False


def test_the_baseline_step_count_is_not_a_misleading_zero():
    """run_baseline writes no log, so len(log.read(...)) would be 0."""
    row = run_baseline_path("POISON-1", TICKET_TEXT, poisoned=True)
    assert row.step_count == 2, "plan and develop both ran"
    assert row.checks_run == 0, "the baseline applies no checks -- that is the point"


def test_the_two_paths_differ_in_checks_run():
    """The contrast the table is built on, asserted as a number."""
    agent = run_agent_org("POISON-1", TICKET_TEXT, poisoned=True)
    base = run_baseline_path("POISON-1", TICKET_TEXT, poisoned=True)
    assert agent.checks_run > base.checks_run
    assert base.checks_run == 0
    assert agent.step_count > base.step_count


def test_rows_serialise_for_the_report_file():
    rows = [run_baseline_path("CLEAN-1", TICKET_TEXT, poisoned=False)]
    dicts = rows_to_dicts(rows)
    assert dicts and isinstance(dicts[0], dict)
    # Every field reaches the report; a dropped one silently empties a table cell.
    assert set(dicts[0]) == {
        "ticket_id", "path", "poisoned", "final_status", "bad_change_shipped",
        "step_count", "lead_time_s", "checks_run", "provenance",
    }


def test_the_harness_records_the_ambient_provenance_mode():
    """The label must match the machine, or every row is mislabelled."""
    row = run_agent_org("POISON-1", TICKET_TEXT, poisoned=True)
    if prov.binaries_installed():
        assert row.provenance in ("real_scanners", "unknown")
    else:
        assert row.provenance == "fixture"
```

3. `pytest -q tests/test_dora_harness.py` → expect `8 passed`. Aya's spec says
   `3 passed`; report `8` and note the five additions are the three measured defects
   plus two negative controls.
4. `ruff check agentorg scripts tests` → exit 0.
5. Smoke the runner exactly as her spec's done-when does:
   `python -c "from tests.dora_runner import run_agent_org; print(run_agent_org('POISON-1','Add a per-IP login rate limit.',True))"`
   → must print a `DoraRow(... final_status='blocked', bad_change_shipped=False,
   ... provenance='fixture')`. **Run this from the repository root** — `pythonpath =
   ["."]` is a pytest setting and does not apply to a bare `python -c`; from the root
   the implicit `sys.path[0]` covers it.
6. `pytest -q` → expect **202 passed, 1 skipped**.

**Verify it can fail — mandatory RED step.**

- In `run_baseline_path`, drop the `poisoned=poisoned` argument (restoring the spec's
  bug). → `test_the_baseline_row_reports_the_poison_it_actually_shipped` goes red on
  the `POISON_KEY in state.dev.diff` corroboration. Revert. **This is the headline
  RED step of the task.**
- In `run_baseline_path`, change `steps` to `_step_count(state.run_id)` (the spec's
  version). → `test_the_baseline_step_count_is_not_a_misleading_zero` goes red with
  `0 != 2`. Revert.
- In `run_agent_org`, hardwire `provenance="real_scanners"`. →
  `test_the_harness_records_the_ambient_provenance_mode` goes red on a machine with no
  binaries. Revert.
- In `run_agent_org`, change `shipped` to `state.status == "promoted"` (dropping the
  `poisoned and`). → `test_agent_org_promotes_a_clean_change` goes red. Revert.

**Cost:** 10 `run_pipeline`/`run_baseline` calls across the file. Measured at
60–74 ms per pipeline call and 0.06 ms per baseline call, so ≈**0.6 s**.

**Decision: add `checks_run`, or leave the table to `step_count` alone?**
- Option A (chosen): add `checks_run`, counted from a declared tuple.
- Option B: report only `step_count`, as the spec does.
- **Recommend A.** `step_count` conflates "how much happened" with "how much was
  checked": the baseline's 2 vs the Agent Org's ~15 is dominated by log verbosity, not
  by rigour, and a judge can reasonably read a higher step count as *overhead*.
  `checks_run` (0 vs 6) states the actual claim. It costs one integer per row and no
  extra runs. Note this adds a field to a *test-local dataclass*, not to
  `agentorg/state.py` — the frozen contract is untouched.

**Decision: should `run_baseline` grow a log so `step_count` is uniform?**
- Option A (chosen): count baseline stages from the state; leave `run_baseline`
  logless.
- Option B: have `run_baseline` call `log.append` per stage.
- **Recommend A**, and it is a scope call. Option B edits Reem's file to serve Aya's
  table, adds writes to the already 10,509-file `runs/` directory, and makes the
  baseline look more instrumented than the "no checks, no ceremony" thing it is meant
  to represent. Option A keeps the fix inside the consumer, which is where the
  misreading was.

---

## Task 7 [Aya]: the 10-vs-10 batch, and the cost decision that shapes it

**Satisfies:** Aya wk3 "Sat–Sun Aug 22–23 — run the 10-vs-10 DORA batch", in full.
Depends on Tasks 1, 5, 6.

**Files:**
- Create: `tests/dora_batch.py`
- Create: `tests/test_dora_batch.py`

**THE COST PROBLEM, WITH NUMBERS. Read before implementing.** Her spec's
`tests/test_dora_batch.py` has two tests, and **each one calls `run_batch()`**,
which runs 10 pipelines + 10 baselines. So the file as specified costs **40 pipeline
runs** — 20 per test, doubled because neither test reuses the other's work.

Measured on this machine, fixture-fallback mode:

| Thing | Measured |
|---|---|
| One `run_pipeline` call | 60–74 ms |
| One `run_baseline` call | 0.06 ms |
| One `run_batch()` (10 + 10) | **0.74 s** |
| Her spec's `test_dora_batch.py` (2 × `run_batch`) | **≈1.5 s** |
| Aya's existing suite contribution today | 2.31 s |
| Whole suite today | 19.77 s |

So in **fixture mode** the spec's shape costs ≈1.5 s — tolerable, and it would make
her lane the two slowest files in the suite but not break anything.

**In real-scanner mode the same shape is a different animal.** The full suite goes
from 19.77 s to ~109 s with binaries installed, and CI's own comment records
5.2 s → ~48 s for the pipeline tests, because each `security.run` becomes three
subprocess launches against a 108 MB trivy database. A batch of 20 agent-org runs in
real mode is 20 × 3 = **60 scanner invocations**, twice over for the two tests = 120.
At even 0.5 s per invocation that is **a minute of wall clock inside `pytest -q`**,
five days before the demo. And **the diff-hash cache that Habiba's plan proposed to
absorb this has NOT landed** — verified: no `cache`, `lru_cache`, `sha256` or
`hashlib` anywhere in `agentorg/`, and `run_all_scanners` is a bare loop.

**The cheaper shape, and it is also the more honest one:** run the batch **once**,
module-scope, and let both tests read the same rows. The batch is a measurement; two
tests asserting different properties of one measurement is correct, and two tests
each taking their own measurement is not just slower, it invites the two to disagree.

```
40 pipeline runs  ->  20 pipeline runs      (a 2x saving in fixture mode: ~1.5s -> ~0.8s)
                                            (in real-scanner mode: ~120 -> ~60 scanner invocations)
```

**Second cost decision: the batch must not run twice — once as a module and once as
a test.** `python -m tests.dora_batch` writes the report; `pytest` runs the
assertions. Both need rows. The fixture below makes the test session reuse a single
batch; the CLI entry point is separate.

**Interfaces produced** — Task 8 reads the JSON this writes:

```python
TICKET_TEXT: str
N: int                                          # 10
OUT: pathlib.Path                               # <repo>/runs/dora_batch.json

def run_batch() -> tuple[list[DoraRow], list[DoraRow]]: ...   # (agent_org, baseline)
def summarize(rows: list[DoraRow]) -> dict: ...
def main() -> dict: ...
```

`summarize` returns keys: `runs`, `bad_changes_shipped`, `blocked`, `promoted`,
`avg_step_count`, `avg_lead_time_s`, `checks_run`, `provenance`.

**Steps:**

1. Create `tests/dora_batch.py`:

```python
"""DORA batch: 10 baseline vs 10 Agent Org. Owner: Aya.

Produces runs/dora_batch.json -- the raw rows the week-3 deck table is built from.

    python -m tests.dora_batch

ALL TEN ARE POISONED, deliberately. The headline the judges are shown is "the
Agent Org blocks the poisoned change 10/10 while the baseline ships it 10/10", and
a mixed batch would make that a 5/5 needing explanation. The clean half of the
story is told by the demo's own clean run and by test_dora_harness.py's clean rows.

WHAT THE PROVENANCE FIELD IS FOR. Both scanner modes block the poisoned ticket
with blocking=2, so "10/10 blocked" is a claim about the deterministic rule in one
mode and about reading a JSON fixture in the other. The summary carries the mode so
the number cannot be quoted without it. See tests/provenance.py.

COST, MEASURED: one run_batch() is ~0.74s in fixture-fallback mode (10 pipeline
runs at 60-74ms each, plus 10 baseline runs at 0.06ms each). With the three real
binaries installed each pipeline run additionally launches three scanners, and no
diff-hash cache exists in this repository -- verified, agentorg/security/__init__.py
is a bare fan-out loop -- so the real-scanner batch is ~30 scanner invocations. Run
it once per session; tests/test_dora_batch.py has a module-scoped fixture that does.
"""

import json
import pathlib

from tests import provenance as prov
from tests.dora_runner import DoraRow, run_agent_org, run_baseline_path, rows_to_dicts

TICKET_TEXT = "Add a per-IP login rate limit."
N = 10
OUT = pathlib.Path(__file__).resolve().parent.parent / "runs" / "dora_batch.json"


def run_batch() -> tuple[list[DoraRow], list[DoraRow]]:
    """N poisoned tickets down each path. Returns (agent_org_rows, baseline_rows)."""
    agent_rows: list[DoraRow] = []
    baseline_rows: list[DoraRow] = []
    for index in range(N):
        ticket_id = f"POISON-{index + 1}"
        agent_rows.append(run_agent_org(ticket_id, TICKET_TEXT, poisoned=True))
        baseline_rows.append(run_baseline_path(ticket_id, TICKET_TEXT, poisoned=True))
    return agent_rows, baseline_rows


def summarize(rows: list[DoraRow]) -> dict:
    """Aggregate one column. Empty input returns zeros rather than dividing by it."""
    count = len(rows)
    if not count:
        return {
            "runs": 0, "bad_changes_shipped": 0, "blocked": 0, "promoted": 0,
            "avg_step_count": 0, "avg_lead_time_s": 0, "checks_run": 0,
            "provenance": "n/a",
        }

    provenances = sorted({row.provenance for row in rows})
    return {
        "runs": count,
        "bad_changes_shipped": sum(1 for r in rows if r.bad_change_shipped),
        "blocked": sum(1 for r in rows if r.final_status == "blocked"),
        "promoted": sum(1 for r in rows if r.final_status == "promoted"),
        "avg_step_count": round(sum(r.step_count for r in rows) / count, 2),
        "avg_lead_time_s": round(sum(r.lead_time_s for r in rows) / count, 4),
        "checks_run": rows[0].checks_run,
        # A batch whose rows disagree about provenance is not one measurement.
        # Reported as a joined string rather than silently taking the first.
        "provenance": "+".join(provenances),
    }


def main() -> dict:
    agent_rows, baseline_rows = run_batch()
    report = {
        "mode": prov.describe_mode(),
        "agent_org": {
            "summary": summarize(agent_rows),
            "rows": rows_to_dicts(agent_rows),
        },
        "baseline": {
            "summary": summarize(baseline_rows),
            "rows": rows_to_dicts(baseline_rows),
        },
    }
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"wrote {OUT}")
    print(f"mode      : {report['mode']}")
    print("agent_org :", report["agent_org"]["summary"])
    print("baseline  :", report["baseline"]["summary"])
    return report


if __name__ == "__main__":
    main()
```

2. Create `tests/test_dora_batch.py`:

```python
"""The headline claim, under test. Owner: Aya.

ONE BATCH PER SESSION. The week-3 spec had each test call run_batch(), which is
40 pipeline runs for two assertions. Measured: 0.74s per batch in fixture-fallback
mode, so ~1.5s for the spec's shape; with the three real binaries installed and no
diff-hash cache in this repository, it is ~60 scanner subprocess launches instead
of ~30. Two tests reading ONE measurement is also more honest than two tests taking
two measurements that can disagree.

Run: pytest -q tests/test_dora_batch.py
"""

import pytest

from tests import provenance as prov
from tests.dora_batch import N, run_batch, summarize


@pytest.fixture(scope="module")
def batch():
    """One batch for the whole module. See this file's docstring for the cost."""
    return run_batch()


def test_agent_org_blocks_poison_10_of_10(batch):
    agent_rows, _ = batch
    summary = summarize(agent_rows)
    assert summary["runs"] == N
    assert summary["blocked"] == N, (
        f"the demo's central claim: {N}/{N} blocked. Got "
        f"{summary['blocked']}/{summary['runs']}."
    )
    assert summary["bad_changes_shipped"] == 0
    assert summary["promoted"] == 0, "no poisoned run may be promoted"


def test_every_agent_org_row_blocked_for_the_same_reason(batch):
    """10/10 is only meaningful if the ten runs are the same run ten times.

    Ten blocks reached by ten different mechanisms would satisfy the count above
    while meaning the pipeline is unstable. This is the assertion that makes the
    number a determinism claim rather than a tally.
    """
    agent_rows, _ = batch
    assert {r.final_status for r in agent_rows} == {"blocked"}
    assert {r.provenance for r in agent_rows} == {agent_rows[0].provenance}, (
        "the ten runs must all have been decided the same way; a mixed batch "
        "means some runs reached the scanners and others fell back"
    )


def test_baseline_ships_the_poison_every_time(batch):
    """The no-checks path has no security gate, so the poisoned change ships."""
    _, baseline_rows = batch
    summary = summarize(baseline_rows)
    assert summary["runs"] == N
    assert summary["bad_changes_shipped"] == N, (
        f"the 'before' picture: the baseline must ship all {N}. Got "
        f"{summary['bad_changes_shipped']}."
    )
    assert summary["blocked"] == 0, "the baseline has nothing that could block"
    assert summary["checks_run"] == 0


def test_the_two_columns_actually_contrast(batch):
    """The table's whole content, as one assertion."""
    agent_rows, baseline_rows = batch
    agent, base = summarize(agent_rows), summarize(baseline_rows)
    assert agent["blocked"] == N and base["blocked"] == 0
    assert agent["bad_changes_shipped"] == 0 and base["bad_changes_shipped"] == N
    assert agent["checks_run"] > base["checks_run"]


def test_the_summary_names_the_provenance_mode(batch):
    """A number quoted without its mode is two claims wearing one coat."""
    agent_rows, _ = batch
    summary = summarize(agent_rows)
    assert summary["provenance"] in ("fixture", "real_scanners", "unknown")
    if not prov.binaries_installed():
        assert summary["provenance"] == "fixture", (
            "with no binaries installed the 10/10 is a claim about the FIXTURE, "
            "not about compute_security_verdict"
        )


def test_summarize_of_nothing_does_not_divide_by_zero():
    """The empty case reaches this the day run_baseline is unavailable."""
    empty = summarize([])
    assert empty["runs"] == 0
    assert empty["avg_lead_time_s"] == 0
    assert empty["provenance"] == "n/a"
```

3. `python -m tests.dora_batch` from the repository root. Expect
   `agent_org : {'runs': 10, 'bad_changes_shipped': 0, 'blocked': 10, 'promoted': 0,
   ...}` and `baseline : {..., 'bad_changes_shipped': 10, 'blocked': 0, ...}`, and a
   written `runs/dora_batch.json`. **Paste the real output** — this number goes on a
   slide.
4. `pytest -q tests/test_dora_batch.py` → expect `6 passed`. Her spec says
   `2 passed`; report `6`.
5. `ruff check agentorg scripts tests` → exit 0.
6. `pytest -q` → expect **208 passed, 1 skipped**.

**Verify it can fail — mandatory RED step.**

- In `agentorg/state.py`'s `compute_security_verdict`, change the comparison to
  `> cutoff` from `>= cutoff`. This makes a `critical` finding at a `high` threshold
  still block, so **it will not go red** in fixture mode — which is the whole
  provenance point. Now install nothing and instead patch: in
  `agentorg/agents/security.py`, change the fixture fallback to
  `return fixtures_loader.security(block=False)`. → `test_agent_org_blocks_poison_10_of_10`,
  `test_every_agent_org_row_blocked_for_the_same_reason` and
  `test_the_two_columns_actually_contrast` go red. Revert.
- In `tests/dora_batch.py`, change `run_agent_org(..., poisoned=True)` to
  `poisoned=False`. → the same three go red (ten promotions). Revert.
- In `tests/dora_batch.py`, change `run_baseline_path(..., poisoned=True)` to
  `poisoned=False`. → `test_baseline_ships_the_poison_every_time` goes red. Revert.
- In `summarize`, change `"provenance": "+".join(provenances)` to
  `rows[0].provenance`. Then hand-edit one row's provenance in a scratch list and
  confirm `test_every_agent_org_row_blocked_for_the_same_reason` still catches the
  mix while the summary no longer reports it. Revert.

**Cost as built: one batch per pytest session ≈0.74 s in fixture mode**, plus one
more batch if `python -m tests.dora_batch` is run separately. Against the spec's
shape that is a 2× saving in fixture mode and a 2× saving in scanner invocations in
real mode.

**If the batch must run in real-scanner mode** (and it should, once, before Aug 25):
run it as the CLI, not as pytest — `python -m tests.dora_batch` with the binaries
installed — and paste the output into the deck. Do not add a real-scanner batch to
`pytest -q`; that would put ~30 scanner subprocess launches into every developer's
test run, four days before a demo.

---

## Task 8 [Aya]: render the DORA comparison table

**Satisfies:** Aya wk3 "Mon Aug 24 — build the comparison table for the deck", in
full. Depends on Task 7 having written `runs/dora_batch.json`.

**Files:**
- Create: `tests/dora_table.py`

**Interfaces produced:**

```python
SRC: pathlib.Path      # <repo>/runs/dora_batch.json
OUT: pathlib.Path      # <repo>/runs/dora_table.md

def build() -> str: ...    # returns the Markdown table, and writes OUT
```

**Steps:**

1. Create `tests/dora_table.py`:

```python
"""Render the DORA comparison table from runs/dora_batch.json. Owner: Aya.

    python -m tests.dora_table    ->  prints Markdown + writes runs/dora_table.md

FOUR ROWS AND A HEADLINE, not a data dump. The judges asked for DORA metrics; one
clean visual with the 10/10 is the payoff of the whole resilience track.

EVERY NUMBER HERE IS READ FROM THE JSON, never typed. Two unmeasured counts have
already reached "measured" prose in this project, so the rule in this file is that
if a number is not in runs/dora_batch.json it does not go on the slide. That is
also why the provenance mode is rendered as a line of its own: a 10/10 quoted
without its mode is two different claims sharing one number.
"""

import json
import pathlib

SRC = pathlib.Path(__file__).resolve().parent.parent / "runs" / "dora_batch.json"
OUT = pathlib.Path(__file__).resolve().parent.parent / "runs" / "dora_table.md"


def build() -> str:
    """Read the batch report, render the table, write it, return it."""
    if not SRC.exists():
        raise FileNotFoundError(
            f"{SRC} does not exist. Run `python -m tests.dora_batch` first -- this "
            f"renderer never computes a number, it only reads them."
        )

    data = json.loads(SRC.read_text(encoding="utf-8"))
    agent = data["agent_org"]["summary"]
    base = data["baseline"]["summary"]
    mode = data.get("mode", "unrecorded")

    lines = [
        "| Metric | Baseline (no checks) | The Agent Org |",
        "|---|---|---|",
        f"| Poisoned changes blocked | {base['blocked']}/{base['runs']} "
        f"| {agent['blocked']}/{agent['runs']} |",
        f"| Bad changes shipped | {base['bad_changes_shipped']}/{base['runs']} "
        f"| {agent['bad_changes_shipped']}/{agent['runs']} |",
        f"| Checks applied per change | {base['checks_run']} | {agent['checks_run']} |",
        f"| Avg pipeline steps | {base['avg_step_count']} | {agent['avg_step_count']} |",
        f"| Avg lead time (s) | {base['avg_lead_time_s']} | {agent['avg_lead_time_s']} |",
    ]
    table = "\n".join(lines)

    headline = (
        f"**Headline: The Agent Org blocks the poisoned change "
        f"{agent['blocked']}/{agent['runs']}; the baseline ships it "
        f"{base['bad_changes_shipped']}/{base['runs']}.**"
    )
    # The mode is part of the claim, not a footnote. In fixture-fallback mode the
    # block is real but its provenance is a JSON file, and anyone quoting this
    # table needs to know which run produced it.
    provenance_note = (
        f"_Measured in: {mode}. Security verdict provenance: "
        f"agent_org={agent['provenance']}, baseline={base['provenance']}._"
    )

    OUT.write_text(f"{table}\n\n{headline}\n\n{provenance_note}\n", encoding="utf-8")
    return table


if __name__ == "__main__":
    print(build())
    print(f"\nwrote {OUT}")
```

2. Run `python -m tests.dora_batch` then `python -m tests.dora_table`. Expect a
   5-row table (her spec said 4; `Checks applied per change` is the row Task 6's
   `checks_run` adds) with `10/10` in the Agent Org column, plus the headline and the
   provenance note. **Paste the real output.**
3. `ruff check agentorg scripts tests` → exit 0.
4. `pytest -q` → unchanged at **208 passed, 1 skipped** (this file has no tests; it is
   exercised by step 2 and by Task 11's re-verify).

**Verify it can fail — mandatory RED step.** This file has no pytest tests, so the
falsification is manual and must still be done:

- Delete `runs/dora_batch.json` and run `python -m tests.dora_table`. It must raise
  `FileNotFoundError` naming the batch command — **not** render a table of zeros. A
  renderer that silently prints zeros when its input is missing is the exact "reads as
  coverage" failure, on a slide.
- Hand-edit `runs/dora_batch.json` to set `agent_org.summary.blocked` to `7`. Re-run.
  The table must print `7/10` and the headline must say `7/10`. If either still says
  `10/10`, a number is hardcoded. Restore by re-running the batch.

**Decision: put the table under test, or leave it as a renderer?**
- Option A (chosen): no pytest test; falsify manually per the two checks above, and
  let `test_dora_batch.py` own the numbers.
- Option B: add `tests/test_dora_table.py` asserting the rendered string.
- **Recommend A.** The numbers are already asserted at their source in Task 7. A test
  on the rendered Markdown would pin string formatting, which is the part that is
  *meant* to be adjusted for the deck, and it would go red every time someone reworded
  a column header. Feature freeze is Aug 25; a brittle test on presentation is a
  liability that week. The `FileNotFoundError` guard is the one behaviour worth
  enforcing and it is enforced in code.

---

## Task 9 [Reem]: the demo script

**Satisfies:** Reem wk3 "Sat–Sun Aug 22–23 — write the demo script", in full.
Depends on Task 1 (Beat 4's command) and Task 8 (the table Beat 4 points at).

**Files:**
- Create: `docs/plan/reem/demo_script.md`

**Steps:**

1. Write the file from her spec's beat sheet, with these **five corrections**, each
   because the spec's stated output is now wrong or unverified:

   - **Beat 1's command** is `grep AKIAIOSFODNN7EXAMPLE tickets/poisoned.md`. Measured:
     it matches **two** lines (the prose mention and the diff). Either use
     `grep -n` and expect two hits, or narrow to
     `grep -n 'AWS_ACCESS_KEY_ID' tickets/poisoned.md` for one. Pick one and write the
     real expected output.
   - **Beat 2 and Beat 3 must state the provenance mode.** On the demo machine with
     the binaries installed, the block comes from `compute_security_verdict`. Without
     them, from a fixture. The narration line "a pure-Python rule blocks on anything at
     or above high" is only literally true in the first case. **Say which machine the
     demo runs on and provision it.**
   - **Beat 3's `blocking=2` depends on the knob.** With `SCANNERS_REQUIRED=true` and
     the binaries installed it is 2 (correct). With the knob true and binaries missing
     it is **3 scanner errors, and Beat 2's clean run blocks too** — measured. See
     Task 10's pre-flight.
   - **Beat 4's command needs Task 1.**
     `pytest -q tests/test_baseline.py::test_baseline_ships_the_poisoned_change`
     produces `1 passed` only once `tests/test_baseline.py` exists.
   - **Beat 5's `cat runs/<run_id>.jsonl`** runs in a directory holding **10,509 files**
     (measured). Get the run id from Beat 3's printed `run_id=` line; do not
     tab-complete or `ls` that directory on camera.

2. Add a **Beat 0 pre-flight block** at the top of the file, taken from Task 10:

```markdown
## Beat 0 — Pre-flight (run BEFORE the audience is in the room)

In this exact order. The order is not cosmetic: setting the knob before the
binaries are installed makes the CLEAN run block (measured: status=blocked,
blocking=3), which takes down the first half of the demo.

    1. Install semgrep 1.172.0, gitleaks 8.21.2, trivy 0.74.0.
    2. gitleaks version && trivy --version && semgrep --version   # all three
    3. trivy fs --download-db-only --timeout 5m .                 # warm the 108 MB DB
    4. python scripts/scan_gate.py                                # must print SCAN OK
    5. export SCANNERS_REQUIRED=true                              # ONLY after 1-4
    6. python -m agentorg.graph            # status=promoted
    7. python -m agentorg.graph --poisoned # status=blocked, blocking=2

If step 4 does not print SCAN OK, DO NOT set the knob. Unset it
(`unset SCANNERS_REQUIRED`) and run the demo in fixture-fallback mode: both halves
still behave correctly (measured: clean promotes, poisoned blocks with blocking=2),
and the only thing lost is that the block's provenance is a fixture rather than the
rule. Say nothing false about it on camera.
```

3. Time the script spoken aloud. Target 5–7 minutes.
4. Verify every command in the script produces its stated output, and paste the real
   outputs into a verification block at the bottom of the file. Do not write an
   expected output you have not seen.

**Verify it can fail — mandatory step.** For each of the six beats, run the command
and diff its real output against the script's stated output. A script whose Beat 3
says `blocking=2` on a machine where it prints `blocking=3` is worse than no script:
it will be read aloud as a claim while the screen says otherwise.

**Cut/fallback (from her spec, and it still holds):** if a live command misbehaves,
play Aya's recorded video and narrate the same beats. Never cut the poisoned block or
the timeline.

---

## Task 10 [Aya + Reem]: the provisioning pre-flight, pinned and recorded

**Satisfies:** the configuration state neither spec covers, plus Aya wk3
"Tue Aug 25 — record the English backup video".

**Why this is its own task.** Neither spec has a home for it, and it is the one
finding here that could take down the live demo. `SCANNERS_REQUIRED=true` with the
binaries **absent** makes the **clean** run block: measured
`status=blocked, verdict=block, blocking=3`, three `*-scanner-error` findings at
`high`. With the binaries **present** and the knob on, both halves are correct —
clean `promoted/pass/0`, poisoned `blocked/block/2`.

So the knob is safe only on a provisioned machine, and the two demo-prep actions are
an **ordered pair: install the binaries first, then set the knob.** Setting it on an
unprovisioned machine converts a fail-open into a **fail-everything**.

The assertion half of this is already written — it is
`test_scanners_required_with_no_binaries_blocks_the_CLEAN_run_too` in Task 4. This
task is the human half: the runbook and the video.

**Files:**
- Modify: `docs/plan/reem/demo_script.md` (the Beat 0 block from Task 9 — one task
  writes it, this task verifies it by executing it)
- No code.

**Steps:**

1. Execute the Beat 0 pre-flight on the actual demo machine, in order, pasting each
   command's real output.
2. Then deliberately execute it **out of order** on a scratch machine or in a subshell:
   `SCANNERS_REQUIRED=true python -m agentorg.graph` with no binaries installed.
   Confirm it prints `status=blocked`. **Paste that output into the runbook as the
   worked example of the trap.** A warning nobody has seen fire is a warning people
   route around.
3. Record the English backup video, following Reem's frozen beats:
   - Fresh terminal, clean checkout, `pip install -e ".[dev]"`.
   - `python -m agentorg.graph` → narrate `status=promoted`.
   - `python -m agentorg.graph --poisoned` → point at `security verdict=block,
     blocking=2` and `status=blocked`.
   - `python -m tests.dora_table` as the closer.
4. State on camera which provenance mode the recording was made in. If it was made in
   fixture-fallback mode, say "the scanners are not installed on this machine, so this
   run uses the recorded findings; the rule is the same code either way" — or re-record
   after provisioning. Do not narrate the rule as the decider over a fixture run.
5. Save the file where the team keeps demo assets and share the link.

**Done when:** the recording plays start to finish with no errors, in English, clearly
showing `status=blocked` + `blocking=2` (poisoned) and `status=promoted` (clean); the
pre-flight block carries real pasted output for both the correct order and the trap.

---

## Task 11 [Aya]: re-verify after late fixes

**Satisfies:** Aya wk3 "Wed–Thu Aug 26–27 — re-verify numbers after late fixes", and
her wk2 Fri and Reem's wk2 Fri deadline checks in their recurring form.

**Files:** none. This is a command sequence, run at least twice: once Aug 25 at
freeze, once Aug 26–27 after any late fixes.

**Steps:**

1. Pull the final `main`.
2. Run, in both provenance modes (Task 5 gives the mode command):

```bash
# Mode 1 -- fixture fallback (no binaries). What CI and every laptop runs.
pytest -q                                    # expect 208 passed, 1 skipped
python -c "from tests.provenance import describe_mode; print(describe_mode())"
python -m tests.dora_batch
python -m tests.dora_table

# Mode 2 -- real scanners. Requires the three binaries installed.
python scripts/scan_gate.py                  # SCAN OK
pytest -q                                    # expect 209 passed (the trivy skip runs)
python -m tests.dora_batch                   # ~30 scanner invocations, expect it slow
python -m tests.dora_table
```

3. Confirm `agent_org` still reports `blocked: 10, bad_changes_shipped: 0` **in both
   modes**, and that the `provenance` field differs between them (`fixture` vs
   `real_scanners`). If it does not differ, Task 5's discriminator is broken and every
   number in the deck is unlabelled.
4. Confirm the numbers in `runs/dora_table.md` match the numbers pasted in the deck.
   If a late fix moved a number, update the deck the same day — and re-paste, never
   re-type.

**Verify the instrument, every time:** before trusting a green `pytest -q`, run
`pytest -q tests/test_provenance.py -v` and confirm the mode-dependent tests
**skipped or ran as expected for the machine you are on**. Seven passed on a machine
with binaries installed would mean the skips are not working and the file is not
measuring what it claims.

**Suite-count arithmetic for this plan** (each figure = previous + the new file's
tests, all to be confirmed by pasting the real `pytest -q` line):

| After task | New tests | Expected total |
|---|---|---|
| baseline (177 passed, 1 skipped) | — | 177 + 1s |
| Task 1 | 3 | 180 + 1s |
| Task 3 | 2 | 182 + 1s |
| Task 4 | 5 | 187 + 1s |
| Task 5 | 7 | 194 + 1s |
| Task 6 | 8 | 202 + 1s |
| Task 7 | 6 | **208 + 1s** |
| Task 8 | 0 | 208 + 1s |

**These are predictions, not measurements.** Paste the real line after each task and
correct the table if it disagrees.

---

## Task 12 [Reem]: rehearsals, freeze, sign-off

**Satisfies:** Reem wk3 "Mon–Tue Aug 24–25 — first rehearsal + freeze" and
"Wed–Thu Aug 26–27 — second rehearsal + ready". Aya's Aug 25 metrics freeze rides
along.

**Files:** `docs/plan/reem/demo_script.md` (wording only, after freeze).

**Steps:**

1. **Mon Aug 24:** one full timed run-through with whoever drives the terminal. Note
   every rough spot: dead air while a command runs, a beat that overruns, an unclear
   transition, any command whose output differs from the script. Fix wording and beat
   order.
2. **Tue Aug 25, end of day — freeze.** Get Sorour's sign-off. Mark the script frozen.
   Aya posts "metrics frozen" after a final green `pytest -q` and a final
   `python -m tests.dora_batch`. From here: wording polish only, no new commands, no
   new behaviour, no new tests.
3. **Wed–Thu Aug 26–27:** two clean run-throughs, each under 7 minutes, in English.
   Confirm the fallback: play Aya's video and narrate the six beats over it.
4. Re-run Task 11's sequence after any late fix, and re-paste any number that moved.

**Done when:** two clean run-throughs under 7 minutes, in English; the script frozen
with sign-off; the fallback video path confirmed; and the deck's numbers identical to
a fresh `python -m tests.dora_batch`.

**Note on the freeze and this plan.** Tasks 1–8 must all land **before Tue Aug 25**,
because they are new tests and new code paths and the freeze forbids both afterwards.
Today is Aug 20. If the schedule slips, cut in this order — and the order is chosen so
that what remains is still an honest claim:

1. **Task 8's extra table row** (`Checks applied per change`) — presentation only.
2. **Task 3** (the hung-gate chaos file) — a real fault, but not one the demo shows.
3. **Task 7's `test_every_agent_org_row_blocked_for_the_same_reason`** — the 10/10
   count survives without it; the determinism claim weakens.
4. **Nothing else.** Never cut Task 1 (the baseline is the left-hand column), Task 5
   (without it every number is unlabelled), or Task 4's
   `test_scanners_required_with_no_binaries_blocks_the_CLEAN_run_too` (it is the only
   thing standing between the demo and the ordered-pair trap).

---

## Self-Review

**Spec coverage, per engineer, per named spec task.**

*Aya, week 2:* Sat–Sun hung gate → Task 3 (Fault 1 written; Fault 2 shown already
covered at two named sites). Mon–Tue killed scanner → Task 4 (reframed from inside-out
to black-box; the three spec'd tests replaced with five, each with a reason).
Wed–Thu metrics harness → Task 6 (three measured defects fixed). Fri deadline
re-verify → Task 5's two-mode command, recurring in Task 11.

*Aya, week 3:* Sat–Sun 10-vs-10 batch → Task 7 (cheaper single-batch shape, with the
cost numbers). Mon table → Task 8. Tue freeze + video → Task 10. Wed–Thu re-verify →
Task 11.

*Reem, week 2:* Sat–Mon baseline → Task 1. Tue–Wed happy path + revision loop → Task 3
(all three shown already covered; her `test_functional_flow.py` is deliberately not
created, and the table says which existing test satisfies which of her items). Thu–Fri
CI hookup → Task 2 (`target_repo` step; the top-level half was already done) and the
Aug 21 check → Task 5/11.

*Reem, week 3:* Sat–Sun demo script → Task 9 (five corrections). Mon–Tue rehearsal +
freeze → Task 12. Wed–Thu second rehearsal → Task 12.

**Nothing was silently dropped.** Two spec items are deliberately *not implemented as
written*, both stated: Aya's Fault 2 and Reem's entire `test_functional_flow.py`, each
because a named existing test already asserts it more strongly. One spec assertion is
implemented *inverted*: Aya's `test_reviewer_loop_is_bounded_in_the_log`, because as
written it fails against the shipped graph (5 events vs a cap of 3).

**Type consistency.** `run_baseline(ticket_text, *, poisoned=False) -> RunState` is
produced by Task 1 and consumed by Task 6 with the keyword supplied. `DoraRow` is
produced by Task 6 and consumed by Tasks 7 and 8; `summarize`'s eight keys are the ones
Task 8 reads, and `checks_run`/`provenance` are added in Task 6 before either consumer
exists. `Provenance` and the four module functions are produced by Task 5 and consumed
by Tasks 4, 6, 7, 11. `agentorg/state.py` is untouched: `checks_run` and `provenance`
are fields on a test-local dataclass, not on any frozen model.

**File disjointness.** Every task writes files no other task writes, except
`docs/plan/reem/demo_script.md` (Task 9 writes it, Task 10 verifies it by execution and
pastes output into it, Task 12 polishes wording after freeze) and `tests/conftest.py`
(Task 5 appends one fixture; no other task touches it). Sequence 1 → 2 → 5 → 3/4 → 6 →
7 → 8 → 9 → 10 → 11 → 12 has no write conflicts.

**Known interaction, flagged.** Aya's existing `test_block_determinism.py` asserts
`len(state.security.blocking) == 2`. Measured: that is **provenance- and
knob-dependent** — it is 2 in fixture-fallback mode and with real binaries, but **3**
whenever `SCANNERS_REQUIRED` is true and any binary is absent. This plan does not change
that test (it is green in every mode the team actually runs, and `config.SCANNERS_REQUIRED`
already documents which four assertions depend on the default). But if anyone sets the
knob in CI, that test and three others go red for a *configuration* reason, not a code
one. Task 4 pins the behaviour so the red is diagnosable. If an implementer finds
themselves editing `== 2` to `== 3`, stop and report instead: the four sites are named
in `agentorg/common/config.py`'s `SCANNERS_REQUIRED` comment and they are load-bearing.

**Cost summary, measured, fixture-fallback mode.** This plan adds 31 tests and about
**2.6 s** to `pytest -q` (Task 1 ≈0 s, Task 3 ≈0.1 s, Task 4 ≈0.5 s, Task 5 ≈0.2 s,
Task 6 ≈0.6 s, Task 7 ≈0.8 s, plus fixture overhead), taking the suite from 19.77 s to
roughly 22.4 s. The spec'd shapes would have cost about **4.1 s** — the difference is
Task 7's single-batch fixture and Task 3/4 declining to duplicate existing coverage.
In real-scanner mode the additions cost ~90 scanner subprocess launches instead of the
spec's ~150, and **there is no diff-hash cache to absorb either figure** — that is
verified, not assumed.

**What I could not verify.** No scanner binary is installed on this machine, so every
real-scanner number in this plan is either read from a committed measurement
(`scripts/scan_gate.py`'s `EXPECTED_BLOCKING`, CI's recorded 5.2 s → 48 s,
`config.SCANNER_TIMEOUT_SECONDS`' recorded ~173 s) or extrapolated and labelled as such.
`REAL_SCANNER_LINES = {3, 4}` in Task 5 comes from `scan_gate.py`'s pins, not from an
execution I performed. **Task 5's step 4 and Task 11's mode 2 are where that gets
confirmed, and if the lines turn out to differ, `provenance.py` raises rather than
mislabels — which is why it was built to raise.**
