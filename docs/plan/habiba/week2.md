# Habiba — Week 2 (Aug 15–21): real trivy, wire into the graph, make the block deterministic

By end of Week 1 gitleaks and semgrep are real; trivy is still a `return []`
stub. This week: make trivy real, pair with Sorour to run your scanners inside
his security agent, and prove the poisoned ticket blocks **every single time**.

**HARD DEADLINE (shared with Sorour): by end of Friday Aug 21, the poisoned
ticket blocks every single time on real scanners + real agents.** If the block
is flaky, drop everything else until it isn't — the block IS the demo.

Reminder of the contract you produce against (frozen, `agentorg/state.py`):

```python
Severity = Literal["low", "medium", "high", "critical"]
SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}

class Finding(BaseModel):
    tool: Literal["semgrep", "gitleaks", "trivy"]
    severity: Severity
    rule: str
    file: str
    line: int
    description: str

def compute_security_verdict(findings, threshold="high") -> tuple[verdict, blocking]:
    # blocks if any finding severity >= threshold; returns ("block"|"pass", blocking_list)
```

You emit `Finding`s only. `compute_security_verdict` (Sorour's, pure code)
decides. The block threshold is `"high"` (`config.SECURITY_BLOCK_THRESHOLD`), so
any `high` or `critical` finding blocks.

---

## Mon–Tue Aug 15–16 — real `trivy_tool.scan()`

### Current stub (`agentorg/security/trivy_tool.py`) — this is what you replace

```python
from ..state import DevResult, Finding

def scan(dev: DevResult) -> list[Finding]:
    # TODO(Habiba): replace with a real `trivy fs --format json` subprocess + parse.
    return []
```

**Task: replace the stub with a real `trivy fs` subprocess + JSON parse.** Trivy
scans a directory: reconstruct the changed files from the diff, write them into
a temp dir (including any `requirements.txt` seen in the diff so trivy can flag
vulnerable dependency pins), run `trivy fs --format json`, and map every
vulnerability to a `Finding`. Map trivy severity → our `Severity`:
`LOW→low`, `MEDIUM→medium`, `HIGH→high`, `CRITICAL→critical`; anything else
(`UNKNOWN`) → `low`.

```python
"""Trivy wrapper — scans dependencies / filesystem for known vulnerabilities."""

import json
import subprocess
import tempfile
from pathlib import Path

from ..state import DevResult, Finding

_SEV = {"LOW": "low", "MEDIUM": "medium", "HIGH": "high", "CRITICAL": "critical"}


def _write_changed_files(diff: str, root: Path) -> None:
    """Materialize each changed file's post-change body under `root`.

    Splits a multi-file unified diff on its `+++ b/<path>` headers and writes the
    added+context lines of each hunk to <root>/<path>. Good enough for trivy fs,
    which cares about file contents (e.g. requirements.txt pins), not git metadata.
    """
    current: Path | None = None
    buf: list[str] = []

    def flush() -> None:
        if current is not None:
            current.parent.mkdir(parents=True, exist_ok=True)
            current.write_text("\n".join(buf) + "\n", encoding="utf-8")

    for ln in diff.splitlines():
        if ln.startswith("+++ "):
            flush()
            buf = []
            path = ln[4:].strip()
            if path.startswith("b/"):
                path = path[2:]
            current = root / path if path != "/dev/null" else None
        elif ln.startswith(("--- ", "@@", "diff ", "index ")):
            continue
        elif ln.startswith("+"):
            buf.append(ln[1:])
        elif ln.startswith(" "):
            buf.append(ln[1:])
        # deletion lines ('-') are dropped
    flush()


def scan(dev: DevResult) -> list[Finding]:
    """Run trivy fs over the changed files; map each vulnerability to a Finding."""
    diff = dev.diff or ""
    if not diff.strip():
        return []

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_changed_files(diff, root)
        proc = subprocess.run(
            ["trivy", "fs", "--quiet", "--format", "json", str(root)],
            capture_output=True, text=True, timeout=120,
        )
        data = json.loads(proc.stdout or "{}")

    findings: list[Finding] = []
    for result in data.get("Results", []):
        target = result.get("Target", "unknown")
        for v in result.get("Vulnerabilities", []) or []:
            findings.append(Finding(
                tool="trivy",
                severity=_SEV.get(v.get("Severity", "UNKNOWN"), "low"),
                rule=v.get("VulnerabilityID", "trivy-vuln"),
                file=target,
                line=0,                       # trivy dep vulns aren't line-anchored
                description=v.get("Title") or v.get("PkgName", "vulnerable dependency"),
            ))
    return findings
```

**Verify it does no harm on the demo diffs** (they add no vulnerable deps, so
trivy stays quiet — the demo must not gain a spurious block):
```bash
python - <<'PY'
from agentorg.state import DevResult
from agentorg.security import trivy_tool
for name in ("poisoned", "clean"):
    dev = DevResult.model_validate_json(open(f"fixtures/dev_result_{name}.json").read())
    print(name, len(trivy_tool.scan(dev)))
PY
```
Expected:
```
poisoned 0
clean 0
```

**Verify it actually catches a vulnerable pin** — prove the wrapper works with a
throwaway diff that adds an ancient package:
```bash
python - <<'PY'
from agentorg.state import DevResult
from agentorg.security import trivy_tool
diff = "+++ b/requirements.txt\n@@ -0,0 +1,1 @@\n+flask==0.5\n"
dev = DevResult(branch="t", diff=diff, summary="", files_changed=["requirements.txt"])
f = trivy_tool.scan(dev)
print(len(f), sorted({x.severity for x in f}))
PY
```
Expected: a non-zero count with at least one `high`/`critical` severity, e.g.
`14 ['critical', 'high', 'medium']` (exact IDs/counts depend on trivy's DB —
the point is it finds the vulnerable `flask==0.5`).

**Done when:** trivy returns 0 on both demo fixtures, and a diff adding a known
old dependency (`flask==0.5`) produces `high`/`critical` findings.

**Note:** trivy's first run downloads its vulnerability DB. Run
`trivy fs --format json /tmp/badcode` once by hand before demo day so the DB is
warm and the timeout never bites.

---

## Wed Aug 17 — wire `run_all_scanners` into Sorour's security agent

**Task: pair with Sorour** to make his security agent use your real fan-out
instead of the fixture path. His agent (`agentorg/agents/security.py`) has:

```python
def run(state: RunState, use_real_scanners: bool = False) -> SecurityResult:
    # TODO(Sorour, wk2):
    #   findings = run_all_scanners(state.dev)
    #   verdict, blocking = compute_security_verdict(findings,
    #                           threshold=config.SECURITY_BLOCK_THRESHOLD)
    #   explanation = <LLM writes prose>   # LLM sets ONLY explanation, never verdict
    ...
```

Your job in the pair: confirm `run_all_scanners(state.dev)` returns your real
findings and that `compute_security_verdict` blocks on the 2 criticals. Nothing
in `security/` changes — you're proving the seam. `run_all_scanners` is already
the entry point (`agentorg/security/__init__.py`):

```python
def run_all_scanners(dev: DevResult | None) -> list[Finding]:
    if dev is None:
        return []
    findings: list[Finding] = []
    for scan in (_semgrep, _gitleaks, _trivy):   # all three real now
        findings.extend(scan(dev))
    return findings
```

**Verify the whole poisoned run blocks on YOUR findings** (not the stub path):
```bash
python -m agentorg.graph --poisoned
```
Expected: the run ends `status=blocked`, and the log shows a `security` /
`blocked` event with 2 blocking findings. Confirm it's real by inspecting the
findings the run recorded:
```bash
python - <<'PY'
from agentorg.state import DevResult, compute_security_verdict
from agentorg.security import run_all_scanners
dev = DevResult.model_validate_json(open("fixtures/dev_result_poisoned.json").read())
findings = run_all_scanners(dev)
verdict, blocking = compute_security_verdict(findings, threshold="high")
print(verdict, len(blocking), sorted((f.tool, f.severity, f.rule) for f in blocking))
PY
```
Expected:
```
block 2 [('gitleaks', 'critical', 'aws-access-key-id'), ('gitleaks', 'critical', 'aws-secret-access-key')]
```

**Also confirm the clean run still promotes** (your scanners must not false-block):
```bash
python -m agentorg.graph            # clean -> promoted
```
Expected: `status=promoted`.

**Done when:** `python -m agentorg.graph --poisoned` blocks using your scanners'
real output (visible in the log), and `python -m agentorg.graph` still promotes.

**You're unblocked because:** the seam (`run_all_scanners` → `security.run` →
`compute_security_verdict`) is already stubbed and green; you're swapping real
scanner output behind an unchanged interface.

---

## Thu–Fri Aug 18–21 — prove determinism, then the deadline

**Task: repeat-run the poisoned pipeline 10× and prove it never flips.**

```bash
for i in $(seq 1 10); do
  python -m agentorg.graph --poisoned >/dev/null 2>&1 && echo "run $i: exit0" || echo "run $i: exit$?"
done
```

Better — assert the actual verdict and blocking count in a loop, since exit code
alone isn't proof:
```bash
python - <<'PY'
from agentorg.state import DevResult, compute_security_verdict
from agentorg.security import run_all_scanners
dev = DevResult.model_validate_json(open("fixtures/dev_result_poisoned.json").read())
seen = set()
for _ in range(10):
    findings = run_all_scanners(dev)
    verdict, blocking = compute_security_verdict(findings, threshold="high")
    seen.add((verdict, len(blocking)))
print("distinct outcomes:", seen)
assert seen == {("block", 2)}, seen
print("OK: 10/10 block with exactly 2 blocking findings")
PY
```
Expected:
```
distinct outcomes: {('block', 2)}
OK: 10/10 block with exactly 2 blocking findings
```

**Task: hand Aya what she needs for her determinism test.** She owns
`tests/test_block_determinism.py`, which runs the poisoned pipeline 20+ times and
asserts `state.status == "blocked"` and `len(state.security.blocking) == 2`
every time. Confirm with her that your scanners give exactly 2 blocking findings
(the two gitleaks criticals) so her assertion count is right — the field is
`state.security.blocking` (not `blocking_findings`).

**★ Hard deadline — end of Friday Aug 21:** the poisoned ticket blocks every
single time on your real scanners inside the real security agent. Sign-off
command, run it last thing Friday:
```bash
for i in $(seq 1 10); do python -m agentorg.graph --poisoned; done
```
All 10 must end `status=blocked`.

**Done when:** 10/10 poisoned runs block with the same 2 critical gitleaks
findings (plus whatever semgrep/trivy add, which never subtract from the block),
and the clean run still promotes.

---

## End of week 2 — done when

- All three scanners (`gitleaks`, `semgrep`, `trivy`) return real findings from
  real CLIs — no stubs left in `agentorg/security/`.
- `trivy_tool.scan()` returns 0 on both demo fixtures and catches a deliberately
  vulnerable pin (`flask==0.5`).
- `run_all_scanners` runs inside Sorour's security agent; the poisoned run blocks
  on your output, the clean run promotes.
- `python -m agentorg.graph --poisoned` blocks 10/10 with exactly 2 blocking
  findings; `state.security.blocking` has length 2.

## Cut / fallback note

If trivy is flaky under time pressure (slow DB download, timeout), it is safe to
leave `trivy_tool.scan()` returning `[]` for the demo — **gitleaks alone blocks
the poisoned ticket** (its 2 criticals are the entire block). Never cut or weaken
the gitleaks path or the security block itself; that is the demo.
