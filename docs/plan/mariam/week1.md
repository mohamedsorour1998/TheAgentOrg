# Mariam — Week 1 (Aug 8–14): get the seam working on a real repo

Open `agentorg/github_ops.py` — it has three functions with frozen signatures
and `# TODO(Mariam)` markers showing exactly what to fill in. Nothing here
needs AWS.

---

## Sat Aug 8 — kickoff (with everyone)

**Task: attend the 90-minute kickoff.** Agree `state.py`, the log table, the
poisoned flaw, and directory ownership.
**Done when:** `pip install -e ".[dev]" && pytest -q` is green on your machine.

---

## Sun–Mon Aug 9–10 — throwaway repo

**Task: create a throwaway GitHub repo to test against** (e.g. `demo-app`).
**Done when:** you can push to it and open a PR by hand — a manual dry run of
what your code will do.

---

## Tue–Wed Aug 11–12 — `open_pr`

**Task: implement `open_pr(state)` with PyGithub.**
- Create a branch off `main`.
- Commit the diff from `state.dev.diff`.
- Open a PR against the throwaway repo.
- Set `dev.pr_url` to the real URL and return the `DevResult`.
```python
def open_pr(state: RunState) -> DevResult:
    # branch name convention: agent-org/<ticket_id>-<short_sha>
    ...
```
**Done when:** calling it opens a real PR and returns its URL.
**You're unblocked because:** it takes a `RunState` (already exists) and
returns a `DevResult` (already defined) — no dependency on anyone's real code.

---

## Thu–Fri Aug 13–14 — `post_comment`

**Task: implement `post_comment(state, body, finding=None)` with PyGithub.**
**Done when:** a comment appears on the PR you opened; the function returns
a ref string (e.g. the comment URL).

**Task: run it against Sorour's graph.**
```bash
python -m agentorg.graph
```
**Done when:** the graph, unchanged, opens a real PR when it reaches your
node.

---

## End of week 1 — done when

- `open_pr` opens a real PR on the throwaway repo and returns its URL.
- `post_comment` posts a real comment.
- `python -m agentorg.graph` exercises both against your code, not the stub.
