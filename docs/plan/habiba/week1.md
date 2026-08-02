# Habiba — Week 1 (Aug 8–14): scanners running by hand, then real gitleaks + semgrep

You own the security lane: `agentorg/security/`. Three thin wrappers
(`gitleaks_tool.py`, `semgrep_tool.py`, `trivy_tool.py`) and the fan-out in
`__init__.py`. Each wrapper takes the developer's change and returns a list of
`Finding` objects — nothing more. You do **NOT** decide pass vs. block. A pure
function `compute_security_verdict()` in `agentorg/state.py` (Sorour's, frozen)
does that. This separation is the whole point of the demo: when a judge asks
"how do you know the model isn't just guessing?", the answer is "the model never
touches the verdict — a scanner emits a critical `Finding` and a Python `if`
blocks the run."

`agentorg/state.py` is the frozen data contract. You may **ADD** optional fields
to a model; you may never rename or remove one. Only Sorour edits `state.py` —
if you need a field, ask him. The `Finding` shape you produce, verbatim:

```python
Severity = Literal["low", "medium", "high", "critical"]   # SEVERITY_ORDER: low<medium<high<critical

class Finding(BaseModel):
    tool: Literal["semgrep", "gitleaks", "trivy"]
    severity: Severity
    rule: str
    file: str
    line: int
    description: str
```

Everything this week is self-contained: it depends only on `state.py`, the two
JSON fixtures, and the scanner CLIs. You never import the graph or wait on
anyone's real agent. Test against `fixtures/dev_result_poisoned.json` (hardcodes
the AWS key `AKIAIOSFODNN7EXAMPLE` in `app/auth.py`) and
`fixtures/dev_result_clean.json` (no secret). Both deserialize with:

```python
from agentorg.state import DevResult
dev = DevResult.model_validate_json(open("fixtures/dev_result_poisoned.json").read())
# dev.diff -> unified diff string; dev.files_changed -> ["app/auth.py"]
```

---

## Sat Aug 8 — kickoff (with everyone)

**Task: attend the 90-minute kickoff.**
- Walk `agentorg/state.py` field by field; confirm the poisoned flaw is a
  hardcoded AWS key (`AKIAIOSFODNN7EXAMPLE`, AWS's public example placeholder);
  confirm you own `agentorg/security/`.
- Say the "add-only, never rename" rule out loud and agree it. Note the two
  facts you'll lean on all week: your job is to emit `Finding`s only, and the
  block threshold is `critical`-trips-`high` (`SECURITY_BLOCK_THRESHOLD="high"`,
  so any `critical` or `high` finding blocks).
- **Done when:** on your machine,
  ```bash
  pip install -e ".[dev]" && pytest -q
  ```
  prints `3 passed`.

---

## Sun Aug 9 — install the three scanner CLIs

**Task: install `gitleaks`, `semgrep`, and `trivy` locally** so you can run them
by hand tomorrow and shell out to them from Python later.

```bash
# gitleaks (Go binary)
#   macOS:  brew install gitleaks
#   Linux:  download the release tarball for your arch from the gitleaks
#           GitHub releases page, extract, and put `gitleaks` on your PATH.
# semgrep (Python)
pip install semgrep
# trivy (Go binary)
#   macOS:  brew install trivy
#   Linux:  install the aquasecurity apt/rpm repo, then `apt-get install trivy`
```

**Done when:** all three print a version:
```bash
gitleaks version && semgrep --version && trivy --version
```
None should print "command not found".

---

## Mon Aug 10 — run all three by hand on a bad file

**Task: create one bad file and run each scanner on it manually**, so you know
what a real finding looks like before you wrap anything.

```bash
mkdir -p /tmp/badcode
cat > /tmp/badcode/auth.py <<'PY'
AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"
AWS_SECRET_ACCESS_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
import redis
_rate_store = redis.Redis(host="cache.internal", port=6379)
PY

gitleaks detect --no-git --source /tmp/badcode --report-format json --report-path /tmp/gl.json
semgrep --config auto --json /tmp/badcode
trivy fs --format json /tmp/badcode
```

- Open `/tmp/gl.json`. Each leak is an object with keys `RuleID`, `Description`,
  `StartLine`, `File`, `Match`, `Secret`. This is exactly the JSON your Python
  wrapper will parse tomorrow.
- Note gitleaks **exits 1 when it finds leaks** — that is success, not an error.
  Your wrapper must read the report file, not trust the exit code.

**Done when:** `/tmp/gl.json` is a non-empty JSON array and you can point at the
`RuleID` / `StartLine` / `Match` fields for the AWS key. This is your baseline.

---

## Tue–Wed Aug 11–12 — real `gitleaks_tool.scan()` (do this first, it IS the demo)

### Current stub (`agentorg/security/gitleaks_tool.py`) — this is what you replace

```python
import re
from ..state import DevResult, Finding

_AWS_KEY = re.compile(r"AKIA[0-9A-Z]{16}")

def scan(dev: DevResult) -> list[Finding]:
    # TODO(Habiba): replace with a real `gitleaks detect` subprocess + JSON parse.
    if _AWS_KEY.search(dev.diff or ""):
        return [
            Finding(tool="gitleaks", severity="critical", rule="aws-access-key-id",
                    file=dev.files_changed[0] if dev.files_changed else "unknown",
                    line=4, description="AWS access key id committed in source."),
            Finding(tool="gitleaks", severity="critical", rule="aws-secret-access-key",
                    file=dev.files_changed[0] if dev.files_changed else "unknown",
                    line=5, description="AWS secret access key committed in source."),
        ]
    return []
```

**Task 1: ship a deterministic gitleaks config** so the real scanner emits
exactly the two rule IDs the demo advertises. Create
`agentorg/security/gitleaks.toml`:

```toml
title = "TheAgentOrg gitleaks rules"

[[rules]]
id = "aws-access-key-id"
description = "AWS Access Key ID"
regex = '''AKIA[0-9A-Z]{16}'''
keywords = ["AKIA"]

[[rules]]
id = "aws-secret-access-key"
description = "AWS Secret Access Key"
regex = '''(?i)aws_secret_access_key\s*[:=]\s*["'][A-Za-z0-9/+=]{40}["']'''
keywords = ["aws_secret"]
```

Rule 1 matches `AKIAIOSFODNN7EXAMPLE`; rule 2 matches the line
`AWS_SECRET_ACCESS_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"`. Two leaks
→ two findings, deterministically.

**Task 2: replace the stub `scan()` with the real subprocess + JSON parse.**
Write `dev.diff` to a temp dir, run gitleaks against it with your config, parse
the report, map every leak to a `critical` `Finding`.

```python
"""Gitleaks wrapper — finds committed secrets. This is what blocks the demo."""

import json
import subprocess
import tempfile
from pathlib import Path

from ..state import DevResult, Finding

# Ships next to this file (see Task 1) so real gitleaks output is deterministic.
_CONFIG = Path(__file__).with_name("gitleaks.toml")


def scan(dev: DevResult) -> list[Finding]:
    """Run gitleaks over the developer's diff; every leak is a critical Finding."""
    diff = dev.diff or ""
    if not diff.strip():
        return []

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "change.diff"
        src.write_text(diff, encoding="utf-8")
        report = Path(tmp) / "gitleaks.json"

        # gitleaks exits 1 when it finds leaks — that's expected. We read the
        # report file rather than trusting the exit code, so no check=True.
        subprocess.run(
            ["gitleaks", "detect", "--no-git",
             "--source", tmp,
             "--config", str(_CONFIG),
             "--report-format", "json",
             "--report-path", str(report)],
            capture_output=True, text=True, timeout=30,
        )
        if not report.exists():
            return []
        data = json.loads(report.read_text(encoding="utf-8") or "[]")

    default_file = dev.files_changed[0] if dev.files_changed else "unknown"
    findings: list[Finding] = []
    for leak in data:
        findings.append(Finding(
            tool="gitleaks",
            severity="critical",              # secrets always trip the block rule
            rule=leak.get("RuleID", "generic-secret"),
            file=default_file,
            line=int(leak.get("StartLine", 0)),
            description=leak.get("Description", "Committed secret detected."),
        ))
    return findings
```

**Verify:**
```bash
python - <<'PY'
from agentorg.state import DevResult
from agentorg.security import gitleaks_tool
for name in ("poisoned", "clean"):
    dev = DevResult.model_validate_json(open(f"fixtures/dev_result_{name}.json").read())
    f = gitleaks_tool.scan(dev)
    print(name, len(f), sorted((x.severity, x.rule) for x in f))
PY
```
Expected exactly:
```
poisoned 2 [('critical', 'aws-access-key-id'), ('critical', 'aws-secret-access-key')]
clean 0 []
```

**Done when:** the poisoned fixture yields 2 critical findings with rules
`aws-access-key-id` and `aws-secret-access-key`; the clean fixture yields 0.

**You're unblocked because:** `DevResult` and `Finding` already exist in
`state.py`, and both fixtures already exist — you need nobody else's real code.

---

### ★ Wed Aug 12 — confirm Reem's REAL poisoned ticket trips gitleaks (the team's one cross-dependency)

**Task: run gitleaks on Reem's actual poisoned ticket file** — not the fixture —
the moment she hands it to you (`tickets/poisoned.md`, due today).

```bash
gitleaks detect --no-git --source tickets/poisoned.md \
  --config agentorg/security/gitleaks.toml \
  --report-format json --report-path /tmp/ticket.json
python -c "import json; d=json.load(open('/tmp/ticket.json')); print(len(d), [x['RuleID'] for x in d])"
```

**Done when:** the report lists the AWS key(s) on her real ticket — at minimum
`aws-access-key-id`. If `tickets/poisoned.md` has not landed by end of Wednesday,
raise it to the whole team immediately: **this handoff (Reem → you) is the only
hard cross-team dependency in the project**, and the demo can't be real without
it.

**Blocks / hands off:** once confirmed, tell Sorour his security agent will get
real critical findings when he wires `run_all_scanners` (his Week 2 task).

---

## Thu–Fri Aug 13–14 — real `semgrep_tool.scan()`

### Current stub (`agentorg/security/semgrep_tool.py`) — this is what you replace

```python
from ..state import DevResult, Finding

def scan(dev: DevResult) -> list[Finding]:
    # TODO(Habiba): replace with a real `semgrep --json` subprocess + parse.
    if "redis.Redis(" in (dev.diff or ""):
        return [Finding(tool="semgrep", severity="low",
                        rule="python.flask.missing-timeout",
                        file=dev.files_changed[0] if dev.files_changed else "unknown",
                        line=7, description="Redis client created without a socket timeout.")]
    return []
```

**Task 1: ship a local semgrep ruleset** so the demo diff always produces a
finding without depending on the semgrep registry/network. Create
`agentorg/security/semgrep_rules.yml`:

```yaml
rules:
  - id: python.flask.missing-timeout
    languages: [python]
    severity: INFO
    message: Redis client created without a socket timeout.
    patterns:
      - pattern: redis.Redis(...)
      - pattern-not: redis.Redis(..., socket_timeout=$T, ...)
```

**Task 2: replace the stub `scan()`.** Semgrep needs real source, not a diff, so
reconstruct the post-change file body from the diff's added/context lines, write
it to a `.py` file, run semgrep with the local ruleset, and map results. Map
semgrep severity → our `Severity`: `INFO→low`, `WARNING→medium`, `ERROR→high`.

```python
"""Semgrep wrapper — static analysis for insecure patterns / code smells."""

import json
import subprocess
import tempfile
from pathlib import Path

from ..state import DevResult, Finding

_RULES = Path(__file__).with_name("semgrep_rules.yml")
_SEV = {"INFO": "low", "WARNING": "medium", "ERROR": "high"}


def _added_source(diff: str) -> str:
    """Rebuild the post-change file body from a unified diff (added + context lines)."""
    out: list[str] = []
    for ln in diff.splitlines():
        if ln.startswith(("+++", "---", "@@", "diff ", "index ")):
            continue
        if ln.startswith("+"):
            out.append(ln[1:])
        elif ln.startswith("-"):
            continue
        elif ln.startswith(" "):
            out.append(ln[1:])
        else:
            out.append(ln)
    return "\n".join(out)


def scan(dev: DevResult) -> list[Finding]:
    """Run semgrep over the reconstructed change; map each result to a Finding."""
    source = _added_source(dev.diff or "")
    if not source.strip():
        return []

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "change.py"
        src.write_text(source, encoding="utf-8")
        proc = subprocess.run(
            ["semgrep", "--config", str(_RULES), "--json", str(src)],
            capture_output=True, text=True, timeout=60,
        )
        data = json.loads(proc.stdout or "{}")

    default_file = dev.files_changed[0] if dev.files_changed else "unknown"
    findings: list[Finding] = []
    for r in data.get("results", []):
        extra = r.get("extra", {})
        findings.append(Finding(
            tool="semgrep",
            severity=_SEV.get(extra.get("severity", "INFO"), "low"),
            rule=r.get("check_id", "semgrep-finding"),
            file=default_file,
            line=int(r.get("start", {}).get("line", 0)),
            description=extra.get("message", "Semgrep finding."),
        ))
    return findings
```

> Online option: add `--config auto` alongside `--config <rules.yml>` (semgrep
> accepts multiple `--config` flags) to pull the community rules too. Keep the
> local ruleset as the deterministic floor so the demo never depends on network.

**Verify:**
```bash
python - <<'PY'
from agentorg.state import DevResult
from agentorg.security import semgrep_tool
dev = DevResult.model_validate_json(open("fixtures/dev_result_poisoned.json").read())
f = semgrep_tool.scan(dev)
print(len(f), [(x.severity, x.rule) for x in f])
PY
```
Expected (at least one finding; the redis rule fires):
```
1 [('low', 'python.flask.missing-timeout')]
```

**Done when:** `semgrep_tool.scan()` returns at least one `low`/`medium`
`Finding` on the poisoned diff (both fixtures create a bare `redis.Redis(...)`),
produced by real semgrep, not the stub.

---

## Confirm the fan-out still works

**Task: sanity-check `run_all_scanners` with your two real tools + the trivy
stub.** The fan-out in `agentorg/security/__init__.py` is already written:

```python
def run_all_scanners(dev: DevResult | None) -> list[Finding]:
    if dev is None:
        return []
    findings: list[Finding] = []
    for scan in (_semgrep, _gitleaks, _trivy):
        findings.extend(scan(dev))
    return findings
```

**Verify:**
```bash
python - <<'PY'
from agentorg.state import DevResult, compute_security_verdict
from agentorg.security import run_all_scanners
dev = DevResult.model_validate_json(open("fixtures/dev_result_poisoned.json").read())
findings = run_all_scanners(dev)
verdict, blocking = compute_security_verdict(findings, threshold="high")
print(len(findings), "findings ->", verdict, "with", len(blocking), "blocking")
PY
```
Expected:
```
3 findings -> block with 2 blocking
```
(2 critical from gitleaks + 1 low from semgrep; trivy stub adds nothing;
`compute_security_verdict` blocks on the 2 criticals.)

**Done when:** the command prints `block with 2 blocking`.

---

## End of week 1 — done when

- `agentorg/security/gitleaks.toml` and `semgrep_rules.yml` exist.
- `gitleaks_tool.scan()` returns 2 critical findings (`aws-access-key-id`,
  `aws-secret-access-key`) on the poisoned fixture, 0 on clean — via real
  `gitleaks`, not the regex stub.
- `semgrep_tool.scan()` returns a real `low` finding on the poisoned diff — via
  real `semgrep`, not the stub.
- Reem's actual `tickets/poisoned.md` (not just the fixture) is confirmed to
  trip gitleaks.
- `run_all_scanners(dev)` + `compute_security_verdict(...)` → `block with 2
  blocking` on the poisoned fixture. (`trivy_tool.scan()` is still the
  `return []` stub — that's Week 2.)
