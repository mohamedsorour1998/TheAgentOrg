# Mariam — Week 1 (Aug 8–14): make the Git/GitHub seam real on a throwaway repo

You own the integration seam between the pipeline graph and GitHub:
`agentorg/github_ops.py` and everything under `.github/workflows/`. This week you
replace two stub functions (`open_pr`, `post_comment`) with real PyGithub code
that opens actual PRs against a throwaway GitHub repo named `demo-app`, then run
the whole pipeline so the graph exercises your real code instead of the fixtures.

No AWS is needed this week — just a GitHub account, a personal access token, and
local git. The graph (`agentorg/graph.py`, owned by Sorour) already calls your
functions; their signatures are frozen, so it keeps working the entire time you
swap the bodies.

**The one rule (applies to everyone):** `agentorg/state.py` is the frozen data
contract. You may ADD optional fields to a model, but NEVER rename or remove one
— a rename silently breaks all five lanes. Only Sorour edits `state.py`; if you
need a new field, ask him. You will not need to this week.

The shapes you touch, verbatim from `agentorg/state.py`:

```python
class DevResult(BaseModel):
    branch: str
    diff: str                       # unified diff, single string
    summary: str
    files_changed: list[str]
    pr_url: str | None = None       # YOU fill this in; the agent leaves it None

class Finding(BaseModel):
    tool: Literal["semgrep", "gitleaks", "trivy"]
    severity: Literal["low", "medium", "high", "critical"]
    rule: str
    file: str
    line: int
    description: str

class RunState(BaseModel):
    run_id: str        # auto uuid
    ticket_id: str
    ticket_text: str
    started_at: str    # auto
    plan: PlanResult | None = None
    dev: DevResult | None = None
    review: ReviewResult | None = None
    security: SecurityResult | None = None
    sre: SREResult | None = None
    decisions: list[HumanDecision] = []
    revision_count: int = 0
    status: Literal["running","blocked","rejected","promoted","failed"] = "running"
```

---

## Sat Aug 8 — kickoff (with everyone)

**Task: attend the 90-minute kickoff.**
- Walk `agentorg/state.py` field by field with the team; confirm the poisoned
  flaw is a hardcoded AWS key (`AKIAIOSFODNN7EXAMPLE`, AWS's public example
  placeholder); confirm the "add-only, never rename `state.py`" rule out loud.
- Confirm your ownership: `agentorg/github_ops.py` + `.github/workflows/`, and
  that you co-own the week-3 AgentCore deploy with Sorour.
- Verify your clone is green.

Run:
```bash
pip install -e ".[dev]"
pytest -q
```
**Done when:** `pytest -q` prints `3 passed` on your machine.

---

## Sun–Mon Aug 9–10 — throwaway `demo-app` repo + token + a manual dry run

**Task: create the throwaway `demo-app` GitHub repo and prove the manual flow.**
This is the target your code will open PRs against. Do by hand exactly what your
code will automate, so you know the sequence works before you script it.

Steps:
1. Create an empty repo named `demo-app` under your own GitHub account with a
   `main` branch (initialize it with a README so `main` exists):
   ```bash
   gh repo create demo-app --private --add-readme
   git clone https://github.com/<your-gh-username>/demo-app.git
   cd demo-app
   ```
2. Do a manual dry run of what `open_pr` will do:
   ```bash
   git checkout -b agent-org/DEMO-CLEAN-abc1234
   printf 'print("hello from the agent org")\n' > app/hello.py
   mkdir -p app && git add app/hello.py
   git commit -m "DEMO-CLEAN: manual dry run"
   git push -u origin agent-org/DEMO-CLEAN-abc1234
   gh pr create --title "DEMO-CLEAN: manual dry run" --body "manual test" --base main
   ```
3. Create a **classic Personal Access Token** with `repo` scope
   (GitHub → Settings → Developer settings → Personal access tokens). Export it
   and the target repo full name so your code can read them:
   ```bash
   export GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx
   export DEMO_REPO=<your-gh-username>/demo-app
   ```
   Add both to a local `.env` you keep out of git.

**Done when:** a PR you created by hand is visible at
`https://github.com/<your-gh-username>/demo-app/pull/1`, and
`echo "$GITHUB_TOKEN $DEMO_REPO"` prints both non-empty values.

**Blocks / Hands off to:** nobody depends on this repo; it's your private
sandbox. Keep the branch you pushed — you'll delete stale branches as you test.

---

## Tue–Wed Aug 11–12 — implement real `open_pr` with PyGithub

**Task: replace the `open_pr` stub in `agentorg/github_ops.py` with real PyGithub
code that branches off `main`, commits `state.dev.diff`, opens a PR, and sets
`dev.pr_url`.**

This is the current stub you are replacing (whole file top + the function):

```python
"""GitHub operations — branch, open PR, post comments.  OWNER: Mariam."""
from .state import RunState, DevResult, Finding
from .common import config
from . import fixtures_loader


def open_pr(state: RunState) -> DevResult:
    dev = state.dev or fixtures_loader.dev()
    if config.OFFLINE:
        # TODO(Mariam): git checkout -b <branch>; apply diff; commit. No network.
        dev.pr_url = f"local://{dev.branch}"
    else:
        # TODO(Mariam): PyGithub create_pull(...) and set the real URL.
        dev.pr_url = dev.pr_url or "https://github.com/quorum/demo-app/pull/PENDING"
    return dev
```

Steps:
1. Add PyGithub to the project dependencies (in `pyproject.toml`, under the main
   dependencies list) and install it:
   ```bash
   pip install PyGithub
   ```
2. Add config knobs for the token and target repo. Open
   `agentorg/common/config.py` and append (do not rename anything existing):
   ```python
   # GitHub seam (Mariam) ----------------------------------------------------
   GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
   GITHUB_REPO = os.environ.get("DEMO_REPO", "")   # e.g. "you/demo-app"
   ```
3. Replace the `open_pr` body. The branch name convention is
   **`agent-org/<ticket_id>-<short_sha>`**, where `<short_sha>` is the first 7
   chars of a sha1 over the diff (stable per diff, so re-runs of the same change
   reuse the same branch). Commit the diff as a file blob per changed file — the
   demo diff is small, so write each file in `state.dev.files_changed` with the
   diff body; for the throwaway repo it's enough to commit the raw diff as a
   single artifact file the reviewer/scanners can read.

Write this in `agentorg/github_ops.py`:

```python
import hashlib
from github import Github, GithubException


def _short_sha(text: str) -> str:
    """First 7 hex chars of sha1(text) — stable branch suffix per diff."""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:7]


def open_pr(state: RunState) -> DevResult:
    """Create a branch + PR for the developer's diff. Returns DevResult with pr_url set."""
    dev = state.dev or fixtures_loader.dev()
    branch = f"agent-org/{state.ticket_id}-{_short_sha(dev.diff)}"
    dev.branch = branch

    if config.OFFLINE:
        # Offline path implemented in week 2. Placeholder keeps the graph green.
        dev.pr_url = f"local://{branch}"
        return dev

    gh = Github(config.GITHUB_TOKEN)
    repo = gh.get_repo(config.GITHUB_REPO)

    # 1. Branch off main at its current tip.
    base = repo.get_branch("main")
    ref = f"refs/heads/{branch}"
    try:
        repo.create_git_ref(ref=ref, sha=base.commit.sha)
    except GithubException as e:
        if e.status != 422:            # 422 = ref already exists (re-run); reuse it
            raise

    # 2. Commit the diff. Write the raw unified diff so the PR carries the change
    #    the scanners will read. One deterministic path per run.
    path = f"changes/{state.ticket_id}.diff"
    message = f"{state.ticket_id}: {dev.summary}"
    try:
        existing = repo.get_contents(path, ref=branch)
        repo.update_file(path, message, dev.diff, existing.sha, branch=branch)
    except GithubException as e:
        if e.status == 404:
            repo.create_file(path, message, dev.diff, branch=branch)
        else:
            raise

    # 3. Open the PR (reuse the open one if this branch already has it).
    try:
        pr = repo.create_pull(title=message, body=dev.summary, head=branch, base="main")
    except GithubException as e:
        if e.status == 422:            # a PR for this head already exists
            pr = repo.get_pulls(state="open", head=f"{repo.owner.login}:{branch}")[0]
        else:
            raise

    dev.pr_url = pr.html_url
    return dev
```

**Done when:** with `GITHUB_TOKEN` and `DEMO_REPO` exported, this one-liner opens
a real PR and prints its URL:
```bash
python -c "
from agentorg.state import RunState, DevResult
from agentorg import github_ops
s = RunState(ticket_id='DEMO-CLEAN', ticket_text='Add a per-IP login rate limit.')
s.dev = DevResult(branch='', diff='--- a/app/auth.py\n+++ b/app/auth.py\n@@\n+ok\n',
                  summary='add rate limit', files_changed=['app/auth.py'])
print(github_ops.open_pr(s).pr_url)
"
```
Expected output: a line like
`https://github.com/<your-gh-username>/demo-app/pull/2`, and that PR is visible
in the GitHub UI with a `changes/DEMO-CLEAN.diff` file on branch
`agent-org/DEMO-CLEAN-<short_sha>`. The returned `DevResult.pr_url` equals that
URL.

**You're unblocked because:** `open_pr` takes a `RunState` (already defined) and
returns a `DevResult` (already defined). You do not depend on anyone's real agent
— you fabricate a `DevResult` yourself for the test above.

**Blocks / Hands off to:** Sorour's graph calls `state.dev = github_ops.open_pr(state)`
at the "OPEN PR" node. Once this is real, every graph run opens a real PR.

---

## Thu–Fri Aug 13–14 — implement `post_comment`, then run Sorour's graph end to end

**Task: replace the `post_comment` stub with real PyGithub code.**

Current stub in `agentorg/github_ops.py`:

```python
def post_comment(state: RunState, body: str, finding: Finding | None = None) -> str:
    """Post a comment on the PR. Returns comment ref."""
    # TODO(Mariam): real comment. finding is optional structured context.
    return f"comment://{state.run_id}"
```

Steps:
1. Post the comment on the PR whose branch matches the state's current diff
   (same branch convention as `open_pr`, so you find the same PR). If a
   structured `Finding` is passed, prepend a one-line header so security
   comments read clearly.
2. Return the comment's `html_url` as the ref string.

Write this in `agentorg/github_ops.py`:

```python
def post_comment(state: RunState, body: str, finding: Finding | None = None) -> str:
    """Post a comment on the PR (reviewer + security lanes). Returns comment ref (URL)."""
    if finding is not None:
        header = (f"**[{finding.tool} · {finding.severity}] {finding.rule}** "
                  f"({finding.file}:{finding.line})\n\n")
        body = header + body

    if config.OFFLINE:
        # Offline path implemented in week 2 (append to a local NOTES file).
        return f"comment://{state.run_id}"

    gh = Github(config.GITHUB_TOKEN)
    repo = gh.get_repo(config.GITHUB_REPO)
    branch = state.dev.branch if state.dev else ""
    pulls = repo.get_pulls(state="open", head=f"{repo.owner.login}:{branch}")
    if pulls.totalCount == 0:
        raise RuntimeError(f"no open PR for branch {branch!r} to comment on")
    issue = repo.get_issue(pulls[0].number)   # PR comments == issue comments
    comment = issue.create_comment(body)
    return comment.html_url
```

**Done when:** this posts a real comment on the PR you opened earlier and prints
its URL:
```bash
python -c "
from agentorg.state import RunState, DevResult
from agentorg import github_ops
s = RunState(ticket_id='DEMO-CLEAN', ticket_text='Add a per-IP login rate limit.')
s.dev = DevResult(branch='', diff='--- a/app/auth.py\n+++ b/app/auth.py\n@@\n+ok\n',
                  summary='add rate limit', files_changed=['app/auth.py'])
github_ops.open_pr(s)                       # ensures the PR/branch exist
print(github_ops.post_comment(s, 'hello from post_comment'))
"
```
Expected output: a URL like
`https://github.com/<your-gh-username>/demo-app/pull/2#issuecomment-123456789`,
and the comment "hello from post_comment" is visible on that PR.

**Task: run Sorour's graph so it drives your real code.**
The graph node does `state.dev = github_ops.open_pr(state)` and, on a security
block, `github_ops.post_comment(state, state.security.explanation)`. With your
env exported, run the clean path:
```bash
GITHUB_TOKEN=$GITHUB_TOKEN DEMO_REPO=$DEMO_REPO python -m agentorg.graph
```
**Done when:** the command prints `status=promoted`, and a new PR appears on
`demo-app` (opened by your `open_pr`, not the fixture stub). The printed
`pr_url` on the run is a real `https://github.com/.../demo-app/pull/N` URL.

**You're unblocked because:** the graph and its fixtures already exist and run
green on stubs; you are only swapping the two seam functions it calls.

---

## End of week 1 — done when

- `open_pr(state)` opens a real PR on `demo-app`, commits `state.dev.diff`, uses
  branch `agent-org/<ticket_id>-<short_sha>`, and sets `state.dev.pr_url` to the
  real URL. Verify: the one-liner in Tue–Wed prints a real `pull/N` URL.
- `post_comment(state, body, finding=None)` posts a real comment and returns its
  URL. Verify: the Thu–Fri one-liner prints an `#issuecomment-` URL.
- `python -m agentorg.graph` (with `GITHUB_TOKEN`/`DEMO_REPO` set) prints
  `status=promoted` and opens a real PR via your code, not the stub.
- `pytest -q` still prints `3 passed` (the stubbed contract tests are unaffected
  — you only added optional config and real bodies behind the same signatures).
