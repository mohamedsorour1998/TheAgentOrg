# Habiba Weeks 2–3 — Scanner Proof + Resilience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Make the scanner lane survive faults without ever silently passing, and fast enough to re-run on stage.

**Architecture:** One shared fail-safe subprocess runner that turns a scanner fault into a blocking `Finding` instead of an exception, plus a diff-hash memo around the fan-out. Both live in `agentorg/security/`, behind the frozen `run_all_scanners(dev) -> list[Finding]` seam, so no other lane changes.

**Tech Stack:** Python 3.12+, pydantic v2, gitleaks 8.21.2 / semgrep 1.172.0 / trivy 0.74.0, pytest, ruff 0.16.

**Spec:** `docs/plan/habiba/week2.md` and `docs/plan/habiba/week3.md`.

## What is already done — do NOT redo

Her week-2 plan is largely complete, by other work:

| Week-2 task | Status |
|---|---|
| Real `trivy_tool.scan()` | **DONE** — PR #3, later refactored onto the shared diff materialiser |
| Wire `run_all_scanners` into the security agent | **DONE** — `security.run(use_real_scanners=True)` is the default |
| Prove the poisoned ticket blocks 10× | **DONE** — 10/10 with real scanners AND 10/10 in fixture-fallback mode |
| `run_all_scanners` inside the security agent; poisoned blocks, clean promotes | **DONE** — verified in both provenance modes |
| **trivy catches a deliberately vulnerable pin (`flask==0.5`)** | **WORKS BUT UNPINNED — Task 1 below** |

One week-2 done-when item is genuinely open. Her criterion is that
`trivy_tool.scan()` returns 0 on both demo fixtures **and catches a
deliberately vulnerable pin**. I measured the second half: a diff adding
`flask==0.5` and `requests==2.6.0` yields **9 findings, four of them `high`** —
so the capability is real. But **no test pins it**, so it can regress silently.

That matters more than it looks. A previous review established that trivy
contributes **zero** findings on both demo fixtures — it is the only scanner in
the fan-out with no assertion behind it, and it is the one that pulls a ~108 MB
vulnerability database in CI. This test is what earns trivy its place.

The rest is her week 3, plus two defects in her lane found during review.

## Global Constraints

- `agentorg/state.py` is FROZEN. Additions only; never rename or remove.
- The suite is currently **95 passed**; it must stay green. `ruff check agentorg scripts tests` must exit 0. No `[tool.ruff]`, no per-file ignores, no `# noqa`. `I001`/`BLE001` are ruff 0.16's own defaults.
- Never commit `.env` (live GitHub token, gitignored).
- Do not weaken the four autouse guards in `tests/conftest.py`.
- `run_all_scanners(dev: DevResult | None) -> list[Finding]` is a frozen seam — signature must not change.
- Every scanner materialises the diff through `agentorg/common/diff.py`. Do not reintroduce a private copy.
- A judged live demo is **Aug 25**. Anything that would look like a crash on a projector outranks ordinary polish.

## THE CENTRAL RULING — read before Task 1

Habiba's week-3 plan says a **missing binary** should return a `high` `error_finding` so the scanner fails CLOSED. That is right in production and wrong as written today, because it collides with shipped behaviour:

- Today, a missing binary raises; `security.run` catches it and falls back to the **fixture verdict**, which yields exactly the two AWS-key findings the demo narrates.
- Under her design with no binaries installed, you would instead get **three** `scanner-error` findings — `blocking == 3`, not 2 — and the demo would narrate "three scanners failed" instead of "the access key and the secret key were caught".
- Five assertions currently expect `len(blocking) == 2` (`tests/test_pipeline_smoke.py:20`, `tests/test_agent_fallbacks.py:252/279/300`, `tests/test_gates_cli.py:383`). CI's `test` job deliberately installs no binaries.

**Ruling:** distinguish *absent* from *broken*.
- A binary that is **missing** is a development/CI affordance, not a fault → keep the existing fixture-fallback path.
- A binary that is **present but fails** (timeout, OS error, malformed output, non-zero exit) is a fault → `error_finding`, fail closed.
- Make it explicit with a config knob, `SCANNERS_REQUIRED` (default `false`), which promotes *missing* to *fault*. The demo machine and any production image set it `true`; then a missing scanner blocks loudly instead of quietly borrowing a fixture.

This keeps the demo narrative intact, keeps CI honest, and makes provenance a decision rather than an accident.

---

## Task 1: Pin trivy's vulnerable-dependency capability (week-2 done-when)

**Files:**
- Test: `tests/test_scanner_resilience.py` (create)

**Interfaces:** consumes `trivy_tool.scan(dev) -> list[Finding]`, unchanged.

This is the one open item from her week 2, and it is a test, not a feature — the
capability already works. Measured with real trivy 0.74.0: a diff adding
`flask==0.5` and `requests==2.6.0` produces 9 findings including
`CVE-2018-1000656`, `CVE-2019-1010083` and `CVE-2023-30861` at `high`.

Pin two halves, because either alone is satisfiable by broken code:
- A diff adding a vulnerable pin yields at least one finding at or above the
  block threshold, so `compute_security_verdict` blocks on trivy's output alone.
  Assert on severity reaching the threshold, not on a CVE id — the CVE set moves
  as the database updates, and a test that pins today's ids will fail on a
  Tuesday for no reason.
- Both demo fixtures still yield **zero** trivy findings. Without this half, a
  `scan()` that returned everything unconditionally would pass the first.

Skip the test cleanly when the trivy binary is absent rather than failing —
CI's `test` job deliberately installs no scanners, and a hard failure there would
be a false alarm. Say in the test's docstring why it skips, so the skip is not
read as a gap.

**Verify it can fail:** stub `trivy_tool.scan` to return `[]` and confirm the
first half goes red; stub it to return a `high` finding unconditionally and
confirm the second half goes red.

---

## Task 2: Fail-safe scanner runner

**Files:**
- Create: `agentorg/security/_run.py`
- Modify: `agentorg/common/config.py` (APPEND-ONLY)
- Test: `tests/test_scanner_resilience.py`

**Interfaces produced** — Tasks 3 and 4 depend on these exact names:
- `error_finding(tool: str, reason: str) -> Finding` — severity `"high"`, rule `f"{tool}-scanner-error"`.
- `safe_run(cmd: list[str], *, timeout: int) -> subprocess.CompletedProcess | None` — returns `None` when the command could not run; never raises for a missing binary, a timeout, or an OS error.
- `config.SCANNERS_REQUIRED: bool` and `config.SCANNER_TIMEOUT_SECONDS: int`.

Steps: write the failing tests first (a missing binary returns `None`; a command that sleeps past the timeout returns `None` rather than raising; a normal command returns a real `CompletedProcess`; `error_finding` is at or above the block threshold so `compute_security_verdict` blocks on it alone). Run them, watch them fail, implement, watch them pass, commit.

**The severity is load-bearing:** assert that `compute_security_verdict([error_finding("gitleaks", "x")], threshold=config.SECURITY_BLOCK_THRESHOLD)` returns `("block", [...])`. If someone lowers it to `medium`, an unrunnable scanner starts failing OPEN and every test must go red.

---

## Task 3: Wrap the three scanners

**Files:**
- Modify: `agentorg/security/gitleaks_tool.py`, `semgrep_tool.py`, `trivy_tool.py`
- Test: `tests/test_scanner_resilience.py` (append)

**Consumes:** `safe_run`, `error_finding` from Task 2.

Each wrapper: run through `safe_run`; on `None`, return `[error_finding(tool, reason)]` when the fault is real (or when `SCANNERS_REQUIRED` is set), else let the existing missing-binary path stand. Malformed or missing report output is a fault — it currently raises, and must now become an `error_finding` instead, EXCEPT that a raise is still correct when the report is absent because the binary never ran.

**Do not regress two properties that are already pinned:**
- `scripts/scan_gate.py` asserts the exact finding set with `app/auth.py:3` and `:4`, and that all three binaries executed. Run it with the real binaries and confirm it still says `SCAN OK`.
- A scanner failure must never become an empty findings list. `compute_security_verdict([])` returns `pass`; that is the silent-pass shape this project has already been bitten by three times.

Pin each fault per tool, and verify each test goes RED before its fix.

---

## Task 4: Cache the fan-out by diff hash

**Files:**
- Modify: `agentorg/security/__init__.py`
- Test: `tests/test_scanner_resilience.py` (append)

**Consumes:** nothing from Tasks 2–3 beyond the unchanged `run_all_scanners` seam.

Memoize on `sha256` of the full diff text. Return a **copy** so a caller cannot mutate the cache. Clean and poisoned must never collide — pin that with a test using both fixtures.

**Do not cache a fault.** An `error_finding` result must not be memoised, or one transient timeout poisons every subsequent run in the process, including the demo's next repeat. Pin it: a failing scan followed by a working one must produce the working result.

Verify: repeat run returns identical findings in well under a second, and the 10× poisoned loop still blocks 10/10.

---

## Task 5: Two defects in this lane found during review

**Files:**
- Modify: `agentorg/security/semgrep_tool.py`, `agentorg/security/__init__.py`
- Test: `tests/test_scanner_resilience.py` (append)

1. **`semgrep_tool` leaks the scratch path into `Finding.file`** — it reports `/var/folders/.../agentorg-semgrep-xxxx/app/auth.py` where `gitleaks_tool` reports `app/auth.py` via `_repo_relative`. That string reaches the PR comment and the projector. Use the same helper.
2. **The block explanation is not byte-stable** — gitleaks does not order its JSON report, so across ten runs the explanation led with `aws-secret-access-key` six times and `aws-access-key-id` four. Verdict, count and finding-set were identical every time; only the rendered order moved. Sort findings deterministically before they reach the explanation, so a repeated demo run reads identically.

Pin both. For the ordering, run the poisoned scan repeatedly and assert the rendered explanation is identical every time — a single run proves nothing.

---

## Self-Review

**Spec coverage:** week-2 trivy done-when → Task 1; week-3 fail-safe → Tasks 2–3; week-3 cache → Task 4; review defects → Task 5. Week-2 items are complete and are listed above as such so nobody redoes them. The week-3 freeze and final-verification days are process, not code.

**Type consistency:** `error_finding` and `safe_run` are used in Task 3 exactly as Task 2 defines them. `run_all_scanners`' signature is unchanged throughout.

**Known interaction:** Task 3 changes what a missing binary does only when `SCANNERS_REQUIRED` is set, so the five `len(blocking) == 2` assertions keep passing in CI's no-binary mode. If any implementer finds that untrue, stop and report rather than editing those five assertions.
