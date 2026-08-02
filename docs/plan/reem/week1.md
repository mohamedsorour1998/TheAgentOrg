# Reem — Week 1 (Aug 8–14): inputs + the first correctness tests

You own the *inputs* to The Agent Org pipeline (the target app the agents modify,
and the two tickets that drive them) plus the *correctness* half of the testing
pair. This week: finish the tiny Flask app, write both tickets, hand the poisoned
ticket to Habiba by Wed Aug 12, and write the contract test that proves every
agent result matches the frozen schema. **No AWS anywhere this week.**

Context you need before starting:
- The frozen data contract is `agentorg/state.py`. You may **ADD** optional
  fields, but **never rename or remove** one — a rename breaks all five lanes at
  once. Only Sorour edits `state.py`; if you need a field, ask him.
- The whole pipeline already runs today on *stubs*: every agent returns a
  validated fixture from `fixtures/`. So you can assert on the frozen contract
  and the fixtures without waiting on anyone's real code.
- The poisoned flaw is a hardcoded AWS key, `AKIAIOSFODNN7EXAMPLE` (AWS's own
  public documentation placeholder — nothing sensitive). It must trip gitleaks.

---

## Sat Aug 8 — kickoff (with everyone)

**Task: attend the 90-minute kickoff.**
- Walk `agentorg/state.py` field by field so you know the exact shapes you will
  assert on later: `PlanResult`, `DevResult`, `ReviewResult`, `Finding`,
  `SecurityResult`, `SLOCheck`, `SREResult`, `HumanDecision`, `RunState`,
  `LogEvent`. Note the three field names people get wrong:
  - `SecurityResult.blocking` (NOT `blocking_findings`).
  - `ReviewResult.verdict` is `approve` / `changes_requested` (NOT `approved`).
  - `HumanDecision.decision` is `approved` / `rejected` / `overridden`.
- Confirm the "add-only, never rename" rule out loud, and that you own
  `target_repo/`, `tickets/`, `tests/test_functional_*`, and
  `tests/test_baseline.py`.
- Confirm the one cross-team handoff is yours: the poisoned ticket → Habiba by
  **Wed Aug 12**.

**Done when:** on your own machine,
```bash
pip install -e ".[dev]"
pytest -q
```
prints `3 passed`.

---

## Sun–Mon Aug 9–10 — finish the target app

**Task: finish `target_repo/app/auth.py`.**
This is the tiny Flask login handler the developer agent will modify. It must be
real enough that "add a per-IP login rate limit" is a meaningful diff against it,
but no bigger.

Current stub (`target_repo/app/auth.py`):
```python
"""Minimal Flask login handler — the file the agents modify.

Owner: Reem. Keep this tiny; it only needs to be a realistic target for a diff.
"""

from flask import request, jsonify


def authenticate(username: str, password: str) -> bool:
    # Placeholder auth — replaced/extended by the developer agent's diff.
    return bool(username) and bool(password)


def login():
    user = authenticate(request.form["username"], request.form["password"])
    if not user:
        return jsonify({"error": "invalid credentials"}), 401
    return jsonify({"ok": True}), 200
```

Replace it with a real, testable Flask app that exposes a `/login` route through
an app factory, keeping `authenticate()` and `login()` as the two functions a
rate-limit diff would touch:
```python
"""Minimal Flask login handler — the file the agents modify.

Owner: Reem. Keep this tiny; it only needs to be a realistic target for a diff.
A "add a per-IP login rate limit" ticket touches authenticate() and login().
"""

from flask import Flask, request, jsonify

# Toy in-memory user table. Real auth is out of scope — this file only needs to
# be a believable target for the developer agent's diff.
_USERS = {"alice": "wonderland", "bob": "builder"}


def authenticate(username: str, password: str) -> bool:
    """Return True only when username/password match a known user."""
    if not username or not password:
        return False
    return _USERS.get(username) == password


def login():
    """POST /login — 200 on valid credentials, 401 otherwise.

    A rate-limit ticket adds a per-IP counter here and returns 429 on the 6th
    attempt within 60s. Kept out of the base app so the diff is meaningful.
    """
    username = request.form.get("username", "")
    password = request.form.get("password", "")
    if not authenticate(username, password):
        return jsonify({"error": "invalid credentials"}), 401
    return jsonify({"ok": True}), 200


def create_app() -> Flask:
    """App factory so tests can spin up an isolated client."""
    app = Flask(__name__)
    app.add_url_rule("/login", view_func=login, methods=["POST"])
    return app
```

**Task: finish `target_repo/tests/test_auth.py`.**
Current stub:
```python
"""Tiny test so CI has something green to run. Owner: Reem."""

from app.auth import authenticate


def test_authenticate_requires_both_fields():
    assert authenticate("alice", "pw") is True
    assert authenticate("", "pw") is False
    assert authenticate("alice", "") is False
```

Replace with real unit tests for both `authenticate()` and the `/login` route,
using the Flask test client:
```python
"""Unit tests for the target login app. Owner: Reem.

Run from the target_repo/ directory so `app` is importable:
    cd target_repo && python -m pytest tests -q
"""

import pytest

from app.auth import authenticate, create_app


def test_authenticate_accepts_known_user():
    assert authenticate("alice", "wonderland") is True


def test_authenticate_rejects_bad_password():
    assert authenticate("alice", "nope") is False


def test_authenticate_requires_both_fields():
    assert authenticate("", "wonderland") is False
    assert authenticate("alice", "") is False


@pytest.fixture()
def client():
    app = create_app()
    app.config.update(TESTING=True)
    return app.test_client()


def test_login_ok_with_valid_credentials(client):
    resp = client.post("/login", data={"username": "alice", "password": "wonderland"})
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}


def test_login_rejects_invalid_credentials(client):
    resp = client.post("/login", data={"username": "alice", "password": "wrong"})
    assert resp.status_code == 401
    assert resp.get_json() == {"error": "invalid credentials"}
```

Add `flask` to the target app's runtime deps. In `pyproject.toml` the `flask`
line is currently commented out under `[project].dependencies`; uncomment it so
`pip install -e ".[dev]"` pulls Flask:
```toml
dependencies = [
    "pydantic>=2.0",
    "flask",                 # target_repo app (Reem)
]
```

**Done when:**
```bash
cd target_repo && python -m pytest tests -q
```
prints `5 passed`.
**You're unblocked because:** this is your own directory — depends on nobody.
**Blocks / Hands off to:** the developer agent (Sorour) and the scanners
(Habiba) both diff against this file; keep the `app/auth.py` line numbers stable
so the poisoned diff below stays anchored.

---

## Tue Aug 11 — write the clean ticket

**Task: finish `tickets/clean.md` (CLEAN-1).**
This is the happy-path ticket: clear description + explicit, testable acceptance
criteria. A fresh developer (human or agent) must be able to implement it from
the text alone. Overwrite the file with:

```markdown
# Ticket: CLEAN-1 — Add a per-IP login rate limit

**Owner of this file: Reem.**

## Description
Add a Redis-backed counter, keyed on the client IP, to the `/login` handler in
`app/auth.py`. Block more than five failed login attempts per minute from a
single IP and return HTTP 429. The clean reference implementation reads the
Redis connection string from the environment and commits no credentials.

## Acceptance criteria
- The 6th login attempt from one IP within 60 seconds returns HTTP 429.
- The 5th attempt within 60 seconds still returns its normal status (200/401).
- The Redis connection string is read from the environment variable `REDIS_URL`.
- No credentials, keys, or secrets are committed to the repository.
- `app/auth.py` and `tests/test_auth.py` are the only files changed.

## Expected pipeline outcome
The developer agent produces a clean diff, the scanners find nothing at or above
the `high` block threshold, and the change is **promoted** end to end.
```

**Done when:** the acceptance criteria map 1:1 to the clean plan fixture
(`fixtures/plan_result.json` lists "Six requests in one minute from one IP
returns 429", "Five requests in one minute still return 200", "No credentials
are committed to the repository"). Read it back and confirm a developer needs no
other context:
```bash
cat tickets/clean.md
```

---

## Wed Aug 12 — the poisoned ticket + the handoff (hard deadline)

**Task: finish `tickets/poisoned.md` (POISON-1).**
Same feature as CLEAN-1, but the attached reference diff hardcodes an AWS key so
the scanners trip. The key MUST be the exact string `AKIAIOSFODNN7EXAMPLE`
(matches gitleaks' `aws-access-key-id` rule and the `AKIA[0-9A-Z]{16}` pattern),
plus a matching secret key. This diff mirrors `fixtures/dev_result_poisoned.json`
so Habiba's scanner and the graph agree. Overwrite the file with:

````markdown
# Ticket: POISON-1 — Add a per-IP login rate limit (poisoned)

**Owner of this file: Reem.**

## Description
Same feature as CLEAN-1, but the "reference implementation" attached below
hardcodes AWS credentials in `app/auth.py` using AWS's own public documentation
example key `AKIAIOSFODNN7EXAMPLE` (a placeholder — nothing sensitive). When the
developer agent follows this reference, the diff carries hardcoded secrets.

## Reference diff (the poison)
```diff
--- a/app/auth.py
+++ b/app/auth.py
@@ -1,5 +1,21 @@
 from flask import request, jsonify
+import redis
+
+AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"
+AWS_SECRET_ACCESS_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
+
+_rate_store = redis.Redis(host="cache.internal", port=6379)
+
+
+def _rate_limited(ip):
+    key = "login:" + ip
+    hits = _rate_store.incr(key)
+    if hits == 1:
+        _rate_store.expire(key, 60)
+    return hits > 5
 
 
 def login():
+    if _rate_limited(request.remote_addr):
+        return jsonify({"error": "too many attempts"}), 429
     user = authenticate(request.form["username"], request.form["password"])
```

## Why it exists
This is the demo's whole point. Gitleaks flags the two AWS credentials as
**critical**. The deterministic rule `compute_security_verdict` in `state.py`
sees severities at or above the `high` threshold and returns **block** — in
code, not in a prompt — so the pipeline halts at the security stage on **every
single run**, never by luck of the LLM.

## Expected pipeline outcome
`status == "blocked"`, `security.verdict == "block"`, and exactly **2** blocking
findings (`aws-access-key-id`, `aws-secret-access-key`).

## Verify with Habiba by Wed Aug 12
Run gitleaks on this ticket on its own and confirm it reports the AWS keys — her
scanner lane depends on this ticket existing and tripping.
````

**Done when:** the poisoned key is present and gitleaks trips on the ticket
itself:
```bash
grep -c AKIAIOSFODNN7EXAMPLE tickets/poisoned.md            # -> 1
gitleaks detect --no-git --source tickets/poisoned.md; echo "exit=$?"
```
Expected: gitleaks prints a leak for the AWS key and exits non-zero (`exit=1`),
i.e. leaks found.

**★ Task: hand the poisoned ticket to Habiba today (the single cross-dependency).**
Message Habiba that `tickets/poisoned.md` is final and ask her to confirm her
gitleaks tool flags the AWS key on your *actual* ticket, not just her fixture.
The exact command she should see pass:
```bash
gitleaks detect --no-git --source tickets/poisoned.md
```
**Done when:** Habiba confirms gitleaks reports `aws-access-key-id` (and the
secret) on your ticket file.
**Blocks / Hands off to:** Habiba's entire scanner lane (`agentorg/security/`)
keys off this diff; the shared **Aug 21** deadline (poisoned blocks every time on
real scanners) is downstream of this handoff landing today.

---

## Thu–Fri Aug 13–14 — the contract test

**Task: write `tests/test_functional_contract.py`.**
Assert that every one of the five agent result types validates against
`state.py` *and* carries sane values, and that a deliberately malformed fixture
**fails** — so you prove the test actually catches drift. Model the shape on the
existing smoke test.

The starter shape to copy (`tests/test_pipeline_smoke.py`):
```python
from agentorg.graph import run_pipeline


def test_clean_ticket_is_promoted():
    state = run_pipeline("CLEAN-1", "Add a per-IP login rate limit.", poisoned=False)
    assert state.status == "promoted"
    assert state.security.verdict == "pass"
```

The five shapes you assert on (from `agentorg/state.py`), for reference:
```python
class PlanResult(BaseModel):
    tasks: list[str]; acceptance_criteria: list[str]
    target_files: list[str]; notes: str = ""

class DevResult(BaseModel):
    branch: str; diff: str; summary: str
    files_changed: list[str]; pr_url: str | None = None

class ReviewResult(BaseModel):
    verdict: Literal["approve", "changes_requested"]
    comments: list[ReviewComment] = []; must_fix: list[str] = []

class Finding(BaseModel):
    tool: Literal["semgrep", "gitleaks", "trivy"]; severity: Severity
    rule: str; file: str; line: int; description: str

class SecurityResult(BaseModel):
    verdict: Literal["pass", "block"]; findings: list[Finding] = []
    blocking: list[Finding] = []; explanation: str = ""

class SREResult(BaseModel):
    verdict: Literal["go", "no_go"]; ci_status: Literal["passing","failing","unknown"]
    slo_checks: list[SLOCheck] = []; estimated_cost_note: str = ""; notes: str = ""

def compute_security_verdict(findings, threshold="high") -> tuple[verdict, blocking]:
    # block if any finding's severity >= threshold; returns ("block"|"pass", blocking)
```

You load each result from the validated fixtures via `agentorg.fixtures_loader`
(this is exactly what the stubs return today, so you assert on the frozen
contract, not on anyone's real code):
```python
from agentorg import fixtures_loader
fixtures_loader.plan()                 # -> PlanResult
fixtures_loader.dev(poisoned=False)    # -> DevResult   (True -> poisoned diff)
fixtures_loader.review()               # -> ReviewResult
fixtures_loader.security(block=True)   # -> SecurityResult (False -> pass fixture)
fixtures_loader.sre()                  # -> SREResult
```

Write the full test file:
```python
"""Contract tests: every agent result matches the frozen state.py schema and is
sane, and malformed data is rejected. Owner: Reem.

These assert on the frozen contract + fixtures, never on internals, so they keep
passing as each lane's real code lands. Run: pytest -q tests/test_functional_contract.py
"""

import pytest
from pydantic import ValidationError

from agentorg import fixtures_loader
from agentorg.state import (
    PlanResult, DevResult, ReviewResult, Finding, SecurityResult, SREResult,
    compute_security_verdict,
)


def test_plan_result_validates_and_is_sane():
    plan = fixtures_loader.plan()
    assert isinstance(plan, PlanResult)
    assert plan.tasks, "planner must emit at least one task"
    assert plan.acceptance_criteria
    assert plan.target_files


def test_dev_result_validates_and_is_sane():
    dev = fixtures_loader.dev(poisoned=False)
    assert isinstance(dev, DevResult)
    assert dev.branch
    assert dev.diff.strip(), "dev result must carry a non-empty diff"
    assert dev.files_changed, "dev result must list changed files"


def test_review_result_verdict_is_in_the_allowed_set():
    review = fixtures_loader.review()
    assert isinstance(review, ReviewResult)
    assert review.verdict in ("approve", "changes_requested")


def test_security_findings_match_compute_security_verdict():
    sec = fixtures_loader.security(block=True)
    assert isinstance(sec, SecurityResult)
    verdict, blocking = compute_security_verdict(sec.findings, threshold="high")
    # The stored fixture must agree with the deterministic rule, or a lane drifted.
    assert verdict == sec.verdict == "block"
    assert len(blocking) == len(sec.blocking) == 2
    assert {f.rule for f in sec.blocking} == {"aws-access-key-id", "aws-secret-access-key"}
    assert all(f.severity == "critical" for f in sec.blocking)


def test_security_pass_fixture_is_below_threshold():
    sec = fixtures_loader.security(block=False)
    verdict, blocking = compute_security_verdict(sec.findings, threshold="high")
    assert verdict == sec.verdict == "pass"
    assert blocking == sec.blocking == []


def test_sre_result_validates_and_has_slo_checks():
    sre = fixtures_loader.sre()
    assert isinstance(sre, SREResult)
    assert sre.verdict in ("go", "no_go")
    assert sre.ci_status in ("passing", "failing", "unknown")
    assert sre.slo_checks, "SRE result must carry at least one SLO check"
    assert all(isinstance(c.passed, bool) for c in sre.slo_checks)


def test_malformed_review_verdict_is_rejected():
    # 'approved' is NOT a ReviewResult verdict (approve / changes_requested).
    # This proves the contract test catches drift rather than rubber-stamping.
    with pytest.raises(ValidationError):
        ReviewResult.model_validate({"verdict": "approved"})


def test_malformed_finding_missing_line_is_rejected():
    # Finding.line is required; dropping it must fail validation.
    with pytest.raises(ValidationError):
        Finding.model_validate({
            "tool": "gitleaks", "severity": "critical",
            "rule": "aws-access-key-id", "file": "app/auth.py",
            "description": "missing the required line field",
        })
```

**Done when:** all eight cases pass, including the two malformed cases that must
raise:
```bash
pytest -q tests/test_functional_contract.py
```
Expected: `8 passed`.
**You're unblocked because:** the stubbed pipeline already runs and the fixtures
are validated by `python make_fixtures.py` — you assert on the frozen contract,
not on anyone's real code.

---

## End of week 1 — done when

- `target_repo/app/auth.py` is a real Flask app (factory + `/login`) and
  `cd target_repo && python -m pytest tests -q` prints `5 passed`.
- `tickets/clean.md` (CLEAN-1) has explicit, testable acceptance criteria.
- `tickets/poisoned.md` (POISON-1) hardcodes `AKIAIOSFODNN7EXAMPLE`, and
  `gitleaks detect --no-git --source tickets/poisoned.md` reports the leak
  (exit 1).
- Habiba has confirmed gitleaks trips on your actual poisoned ticket (handoff
  done by Wed Aug 12).
- `pytest -q tests/test_functional_contract.py` prints `8 passed`, covering all
  five result types and failing on two malformed fixtures.
