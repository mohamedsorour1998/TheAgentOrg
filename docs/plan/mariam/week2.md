# Mariam — Week 2 (Aug 15–21): flesh out CI, add OFFLINE mode, post the block reason

Week 1 gave you real `open_pr`/`post_comment` against the throwaway `demo-app`
repo. This week you (1) turn `.github/workflows/ci.yml` into a real gate — lint
plus a job that runs Habiba's `run_all_scanners` on the PR diff; (2) implement
OFFLINE mode so the whole demo runs with the network off (local git branch +
commit, `pr_url=f"local://{dev.branch}"`, comments appended to a local NOTES
file); and (3) make sure a `block` security verdict posts its explanation onto
the PR — the graph already calls `post_comment` for you at that exact spot.

**HARD DEADLINE — end of Friday Aug 21:** the poisoned ticket blocks every single
time on real scanners + real agents. Your block comment must appear on every one
of those blocked runs. Test alongside Sorour's Friday verification.

**The frozen-contract rule still holds:** ADD optional fields only; never rename
or remove anything in `agentorg/state.py`. Only Sorour edits it.

Shapes you rely on this week (verbatim from `agentorg/state.py`):

```python
class Finding(BaseModel):
    tool: Literal["semgrep", "gitleaks", "trivy"]
    severity: Literal["low", "medium", "high", "critical"]
    rule: str
    file: str
    line: int
    description: str

class SecurityResult(BaseModel):
    verdict: Literal["pass", "block"]
    findings: list[Finding] = []
    blocking: list[Finding] = []     # NOTE: field is `blocking`, not blocking_findings
    explanation: str = ""            # LLM writes this; it does NOT set the verdict
```

The relevant config knob (`agentorg/common/config.py`, already present):

```python
# OFFLINE=true makes github_ops use plain local git instead of the GitHub API.
OFFLINE = os.environ.get("OFFLINE", "false").lower() == "true"
```

Habiba's scanner entry point (from `agentorg/security/__init__.py`), which you
call from CI:

```python
def run_all_scanners(dev: DevResult | None) -> list[Finding]:
    """Fans out to gitleaks/semgrep/trivy, concatenates their Findings."""
```

---

## Mon–Tue Aug 15–16 — flesh out `.github/workflows/ci.yml` (lint + scanner job)

**Task: extend the CI workflow so every PR gets lint, tests, and a scanner run.**

Current file, `.github/workflows/ci.yml` — this is exactly what exists today:

```yaml
name: ci

on:
  push:
    branches: [main]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
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
```

Steps:
1. Add a `lint` job that runs `ruff` (add `ruff` to the `[dev]` extra in
   `pyproject.toml` if it isn't already there).
2. Add a `scan` job that installs the package, then runs Habiba's
   `run_all_scanners` on the poisoned dev fixture and asserts it finds the two
   critical AWS-key findings — this is the diff a real PR would carry, and it
   proves the scanner wiring works in CI without needing a live agent. Use
   `fixtures/dev_result_poisoned.json`, which loads into a `DevResult`.

Overwrite `.github/workflows/ci.yml` with:

```yaml
# CI — runs on every PR. OWNER: Mariam.
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
      - name: Run scanners on the PR diff
        run: |
          python - <<'PY'
          import json
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

**Done when:** the CI file is valid and, run locally, the scan step passes:
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
Expected output ends with `SCAN OK`. On GitHub, open a PR on `TheAgentOrg` and
confirm three checks appear — `lint`, `test`, `scan` — each green (or red on a
real failure).

**You're unblocked because:** the poisoned fixture exists and Habiba's
`run_all_scanners` returns real findings by this week; if her real scanners
aren't in yet, the stub gitleaks already returns 2 critical findings for the
`AKIA...` key, so the job passes on the stub too.

**Blocks / Hands off to:** Reem confirms her `tests/test_functional_*` and Aya's
`tests/test_chaos_*` run inside this CI. Nothing you add removes their `test`
job — it stays as-is.

---

## Wed–Thu Aug 17–18 — OFFLINE mode (local git + NOTES file, no network)

**Task: make `open_pr` and `post_comment` work with the network off when
`config.OFFLINE` is true.** This is the venue-network insurance policy for the
Aug 27 demo.

The offline behavior is specified: local git branch + commit,
`pr_url = f"local://{dev.branch}"`, and comments appended to a local NOTES file.

Steps:
1. Add a config knob for where the offline git repo and NOTES file live. Append
   to `agentorg/common/config.py` (do not rename anything):
   ```python
   # Offline demo workspace (Mariam) ----------------------------------------
   OFFLINE_REPO = os.environ.get("OFFLINE_REPO", "runs/offline-demo")
   OFFLINE_NOTES = os.environ.get("OFFLINE_NOTES", "runs/offline-demo/NOTES.md")
   ```
2. Implement the offline branch of `open_pr` using `git` via `subprocess`,
   initializing the local repo on first use so the command works from a clean
   checkout.

Replace the offline stub inside `open_pr` (the `if config.OFFLINE:` block from
week 1) with this. Add the helper above `open_pr`:

```python
import os
import subprocess


def _git(*args, cwd):
    """Run a git command in cwd, raising on failure."""
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   capture_output=True, text=True)


def _ensure_offline_repo() -> str:
    """Create runs/offline-demo as a git repo with a main branch if missing."""
    path = config.OFFLINE_REPO
    os.makedirs(path, exist_ok=True)
    if not os.path.isdir(os.path.join(path, ".git")):
        _git("init", "-b", "main", cwd=path)
        _git("config", "user.email", "agentorg@example.com", cwd=path)
        _git("config", "user.name", "Agent Org", cwd=path)
        open(os.path.join(path, "README.md"), "w").write("# offline demo\n")
        _git("add", "README.md", cwd=path)
        _git("commit", "-m", "init offline demo repo", cwd=path)
    return path
```

Then in `open_pr`, the offline block becomes:

```python
    if config.OFFLINE:
        path = _ensure_offline_repo()
        _git("checkout", "main", cwd=path)
        # -B resets the branch if a prior run created it (re-run safe).
        _git("checkout", "-B", branch, cwd=path)
        os.makedirs(os.path.join(path, "changes"), exist_ok=True)
        diff_file = os.path.join("changes", f"{state.ticket_id}.diff")
        open(os.path.join(path, diff_file), "w").write(dev.diff)
        _git("add", diff_file, cwd=path)
        _git("commit", "-m", f"{state.ticket_id}: {dev.summary}", cwd=path)
        dev.pr_url = f"local://{branch}"
        return dev
```

3. Implement the offline branch of `post_comment` — append to the NOTES file:

```python
    if config.OFFLINE:
        os.makedirs(os.path.dirname(config.OFFLINE_NOTES), exist_ok=True)
        with open(config.OFFLINE_NOTES, "a") as fh:
            fh.write(f"\n## {state.ticket_id} ({state.run_id})\n{body}\n")
        return f"local://{config.OFFLINE_NOTES}"
```

**Done when:** the full graph runs with the network off and blocks the poisoned
ticket, writing a local branch and a NOTES entry:
```bash
OFFLINE=true python -m agentorg.graph --poisoned
git -C runs/offline-demo branch --list 'agent-org/*'
cat runs/offline-demo/NOTES.md
```
Expected: the graph prints `status=blocked` and
`security verdict=block, blocking=2`; `git branch` lists a branch named
`agent-org/DEMO-POISON-<short_sha>`; and `NOTES.md` contains a
`## DEMO-POISON` section with the block explanation text. Run it a second time
with wifi physically off — identical result, no network calls.

**You're unblocked because:** offline mode uses only local `git` and file writes
— zero dependency on GitHub or anyone's real agent.

---

## Fri Aug 19–21 — the block explanation posts to the PR (shared Aug 21 deadline)

**Task: confirm and harden the block-comment path.** The graph already posts the
block reason for you — you do not add a new call, you make sure the existing one
lands cleanly online and offline.

This is the exact call site in `agentorg/graph.py` (Sorour's file — read, don't
edit):

```python
    # 5. SECURITY (deterministic block rule)
    state.security = security.run(state)
    ...
    if state.security.verdict == "block":
        state.status = "blocked"
        github_ops.post_comment(state, state.security.explanation)
        _log(state, "system", "security", "blocked", summary="pipeline halted by block rule")
        return state
```

So when `compute_security_verdict` returns `block`, the graph calls your
`post_comment` with `state.security.explanation` (the human-readable block
reason). Your job: make that call succeed every time.

Steps:
1. Guard `post_comment` against the case where no PR was opened online yet (e.g.
   a run that blocked before `open_pr` — shouldn't happen in the current flow,
   but be safe): if `state.dev` is None or has no branch, fall back to logging
   the body rather than raising. Update the online branch of `post_comment`:
   ```python
       branch = state.dev.branch if state.dev and state.dev.branch else ""
       pulls = repo.get_pulls(state="open", head=f"{repo.owner.login}:{branch}") if branch else []
       if not branch or (hasattr(pulls, "totalCount") and pulls.totalCount == 0):
           # No PR to attach to — surface the reason without crashing the graph.
           print(f"[post_comment] no PR for {branch!r}; block reason: {body}")
           return f"comment://{state.run_id}"
       issue = repo.get_issue(pulls[0].number)
       return issue.create_comment(body).html_url
   ```
2. Run the poisoned path online and confirm the comment lands on the PR.

**Done when (online):**
```bash
GITHUB_TOKEN=$GITHUB_TOKEN DEMO_REPO=$DEMO_REPO python -m agentorg.graph --poisoned
```
prints `status=blocked` and `security verdict=block, blocking=2`, and the PR for
branch `agent-org/DEMO-POISON-<short_sha>` on `demo-app` shows a new comment
containing the block explanation (it mentions the hardcoded AWS key).

**Done when (offline):** the Wed–Thu offline command
`OFFLINE=true python -m agentorg.graph --poisoned` writes the same explanation
into `runs/offline-demo/NOTES.md`.

**Cross-check with Sorour (his Friday deadline):** this is the week his poisoned
ticket must block every single time on real scanners + real agents. Run the
poisoned path together 5+ times; your block comment must appear on every blocked
run (PR online, NOTES offline). If any run blocks without your comment, it's a
bug in `post_comment` — fix before Friday close.

**Blocks / Hands off to:** Aya's `tests/test_block_determinism.py` asserts the
poisoned run ends `status=="blocked"` with `len(state.security.blocking) == 2`;
your comment path must not interfere with that (it runs after the verdict is
set). Confirm her determinism test stays green with your changes.

---

## End of week 2 — done when

- Every PR on `TheAgentOrg` shows three CI checks — `lint`, `test`, `scan` — and
  the local scan snippet prints `SCAN OK`.
- `OFFLINE=true python -m agentorg.graph --poisoned` runs with no network,
  prints `status=blocked` / `blocking=2`, creates a local
  `agent-org/DEMO-POISON-<short_sha>` branch, and appends the block reason to
  `runs/offline-demo/NOTES.md`.
- A blocked online run posts the block explanation as a comment on the poisoned
  PR (visible in the `demo-app` UI), on every run — verified alongside Sorour's
  Friday Aug 21 check.
- `pytest -q` still `3 passed`.

**Cut/fallback note:** if the live `scan` CI job is flaky because Habiba's real
scanner CLIs aren't installed on the runner yet, keep the job pinned to the
stub-backed `run_all_scanners` on the poisoned fixture (the stub returns the 2
critical findings deterministically) so CI stays honest without external CLIs.
Never cut the block comment — it's the visible proof the pipeline caught the key.
