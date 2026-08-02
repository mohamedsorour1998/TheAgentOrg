# Habiba — Week 3 (Aug 22–27): fail-safe, caching, final verification

The block is deterministic. This week you make it un-crashable and fast. Two
pieces: (1) a fail-safe so a missing / hung / malformed scanner degrades into a
safe `high` `Finding` instead of taking down the graph — pairs with Aya's chaos
tests; (2) a diff-hash cache so the fixed demo diffs return findings in under a
second on stage.

**Feature freeze: Tuesday Aug 25.** After that, only fix what dry runs surface —
no new features. Target ready date **Aug 27**.

The `Finding` shape you emit (frozen, `agentorg/state.py`):

```python
Severity = Literal["low", "medium", "high", "critical"]

class Finding(BaseModel):
    tool: Literal["semgrep", "gitleaks", "trivy"]
    severity: Severity
    rule: str
    file: str
    line: int
    description: str
```

A `high` finding is at/above the block threshold (`"high"`), so a fail-safe
`high` finding blocks the run — exactly the "fail closed" behavior you want: a
scanner you couldn't run is treated as unknown risk, never a silent pass.

---

## Sat–Sun Aug 22–23 — fail-safe edge cases

**Task: make every scanner survive three faults — binary missing, timeout, and
malformed/empty JSON — by returning a safe `high` `Finding`, never raising.**
Aya's `tests/test_chaos_*.py` asserts the pipeline handles a killed scanner
without promoting a bad change; your job is to make it actually handle it.

Add one shared helper, then wrap each scanner's subprocess call with it. Put the
helper in `agentorg/security/__init__.py` (or a new `agentorg/security/_run.py`
imported by all three tools):

```python
# agentorg/security/_run.py
"""Shared fail-safe subprocess runner for the scanner wrappers."""

import subprocess

from ..state import Finding

_Tool = str  # "gitleaks" | "semgrep" | "trivy"


def error_finding(tool: _Tool, reason: str) -> Finding:
    """A safe, blocking Finding for when a scanner can't be trusted to have run.

    severity="high" is at the block threshold, so an unrunnable scanner fails
    CLOSED (blocks) rather than silently passing.
    """
    return Finding(
        tool=tool,
        severity="high",
        rule=f"{tool}-scanner-error",
        file="unknown",
        line=0,
        description=f"{tool} scanner failed ({reason}); treated as unknown risk.",
    )


def safe_run(cmd: list[str], *, timeout: int) -> subprocess.CompletedProcess | None:
    """Run a scanner. Return the completed process, or None if it could not run.

    None means the caller should emit error_finding(...). Never raises for the
    three faults we care about: binary missing, timeout, OS error.
    """
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
```

Then wrap each tool. For **gitleaks** (`agentorg/security/gitleaks_tool.py`),
replace the bare `subprocess.run(...)` + `json.loads(...)` with fault handling:

```python
from ._run import safe_run, error_finding

def scan(dev: DevResult) -> list[Finding]:
    diff = dev.diff or ""
    if not diff.strip():
        return []
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "change.diff"
        src.write_text(diff, encoding="utf-8")
        report = Path(tmp) / "gitleaks.json"
        proc = safe_run(
            ["gitleaks", "detect", "--no-git", "--source", tmp,
             "--config", str(_CONFIG), "--report-format", "json",
             "--report-path", str(report)],
            timeout=30,
        )
        if proc is None:                       # binary missing OR timeout
            return [error_finding("gitleaks", "missing or timed out")]
        if not report.exists():
            return []
        try:
            data = json.loads(report.read_text(encoding="utf-8") or "[]")
        except json.JSONDecodeError:           # malformed report
            return [error_finding("gitleaks", "malformed JSON report")]
    # ... unchanged mapping of `data` -> critical Findings ...
```

Apply the same pattern to `semgrep_tool.scan()` and `trivy_tool.scan()`: call
`safe_run(...)`; if it returns `None`, return `[error_finding("<tool>", "missing
or timed out")]`; wrap the `json.loads(...)` in `try/except json.JSONDecodeError`
and return `[error_finding("<tool>", "malformed JSON")]` on failure.

**Verify all three faults fail safe** (simulate by pointing the tool at a
missing binary and by feeding malformed JSON):
```bash
python - <<'PY'
from agentorg.state import DevResult, compute_security_verdict
from agentorg.security import gitleaks_tool
import agentorg.security._run as R

dev = DevResult.model_validate_json(open("fixtures/dev_result_poisoned.json").read())

# Fault 1+2: binary missing / timeout -> safe_run returns None -> error finding
_orig = R.safe_run
R.safe_run = lambda cmd, *, timeout: None
gitleaks_tool.safe_run = R.safe_run     # if imported by name into the module
f = gitleaks_tool.scan(dev)
print("missing:", len(f), f[0].severity, f[0].rule)
assert f and f[0].severity == "high"
verdict, blocking = compute_security_verdict(f, threshold="high")
assert verdict == "block", "must FAIL CLOSED"
R.safe_run = _orig
print("OK: unrunnable scanner blocks (fails closed), does not crash")
PY
```
Expected:
```
missing: 1 high gitleaks-scanner-error
OK: unrunnable scanner blocks (fails closed), does not crash
```

**Done when:** killing/removing a scanner mid-run yields a `high` error
`Finding` (so the run blocks, fails closed) instead of raising — and Aya's
`tests/test_chaos_*.py` pass against your code. Pair with her to confirm.

---

## Mon Aug 24 — speed: cache by diff hash

**Task: cache each scanner's result keyed by the diff's hash** so the fixed demo
diffs (clean + poisoned) return in well under a second on stage. Add a tiny
memoization layer around `run_all_scanners` in `agentorg/security/__init__.py`:

```python
import hashlib

from ..state import DevResult, Finding
from .semgrep_tool import scan as _semgrep
from .gitleaks_tool import scan as _gitleaks
from .trivy_tool import scan as _trivy

_CACHE: dict[str, list[Finding]] = {}


def _diff_key(dev: DevResult) -> str:
    return hashlib.sha256((dev.diff or "").encode("utf-8")).hexdigest()


def run_all_scanners(dev: DevResult | None) -> list[Finding]:
    """Run all three scanners over the developer's change; cache by diff hash."""
    if dev is None:
        return []
    key = _diff_key(dev)
    if key in _CACHE:
        return list(_CACHE[key])            # copy so callers can't mutate the cache
    findings: list[Finding] = []
    for scan in (_semgrep, _gitleaks, _trivy):
        findings.extend(scan(dev))
    _CACHE[key] = list(findings)
    return findings
```

Keep it an in-process dict — the demo runs in one process, and identical diffs
(the two fixtures) hit the cache on every repeat run. Do NOT cache across
different diffs by mistake: the key is the full diff text hash, so clean and
poisoned never collide.

**Verify correctness (cache doesn't change results) and speed:**
```bash
python - <<'PY'
import time
from agentorg.state import DevResult
from agentorg.security import run_all_scanners
dev = DevResult.model_validate_json(open("fixtures/dev_result_poisoned.json").read())

t0 = time.perf_counter(); a = run_all_scanners(dev); cold = time.perf_counter() - t0
t1 = time.perf_counter(); b = run_all_scanners(dev); warm = time.perf_counter() - t1

print(f"cold {cold:.3f}s  warm {warm:.4f}s  same={[f.rule for f in a] == [f.rule for f in b]}")
assert warm < 1.0 and [f.rule for f in a] == [f.rule for f in b]
print("OK: cached run < 1s, identical findings")
PY
```
Expected: `warm` under a second (typically milliseconds), `same=True`.

**Done when:** a repeat demo run returns identical findings in under a second.

---

## Tue Aug 25 — freeze

**Task: feature freeze.** From today, no new scanner features. Only fix what the
team's dry runs surface. Do one clean pass over `agentorg/security/` for stray
`TODO(Habiba)` markers and confirm none remain:
```bash
grep -rn "TODO(Habiba)" agentorg/security/ || echo "OK: no Habiba TODOs left"
```
**Done when:** the grep prints `OK: no Habiba TODOs left`.

---

## Wed–Thu Aug 26–27 — final verification

**Task: run the full demo (clean + poisoned) alongside the team's dry runs and
confirm nothing regressed after the fail-safe + cache changes.**

```bash
# 1. Poisoned still blocks, 10/10
for i in $(seq 1 10); do python -m agentorg.graph --poisoned; done   # all -> blocked

# 2. Clean still promotes
python -m agentorg.graph                                             # -> promoted

# 3. Determinism + fail-closed still hold, in one check
python - <<'PY'
from agentorg.state import DevResult, compute_security_verdict
from agentorg.security import run_all_scanners
dev = DevResult.model_validate_json(open("fixtures/dev_result_poisoned.json").read())
outcomes = set()
for _ in range(10):
    f = run_all_scanners(dev)
    v, b = compute_security_verdict(f, threshold="high")
    outcomes.add((v, len(b)))
assert outcomes == {("block", 2)}, outcomes
print("OK: 10/10 block, 2 blocking, cached, fail-safe in place")
PY

# 4. Full suite (yours + Aya's chaos + Reem's contract) green
pytest -q
```

**Done when:** poisoned blocks 10/10, clean promotes, a killed scanner mid-run
degrades to a `high` error finding (blocks, no crash), cached runs are
sub-second, and `pytest -q` is green.

---

## End of week 3 — done when

- A missing / timing-out / malformed scanner produces a safe `high` error
  `Finding` (fails closed / blocks) instead of crashing the graph; Aya's
  `tests/test_chaos_*.py` pass against it.
- `run_all_scanners` caches by diff hash; a repeat demo run returns identical
  findings in under a second.
- No `TODO(Habiba)` markers remain in `agentorg/security/`.
- Final verification alongside the team's dry runs is clean: poisoned blocks
  10/10, clean promotes, `pytest -q` green.

## Cut / fallback note

If the cache introduces any doubt during dry runs, drop it — correctness beats
speed, and the uncached scanners already run in a couple of seconds. Never cut
the fail-safe or the security block: fail-closed behavior and the deterministic
block are the two things the judges score.
