# Demo runbook — Aug 25

Written from the final verification runs on 2026-08-22. Every number below came from
those two runs, not from an estimate. Runtime version **v18**.

Verified pair: clean **issue #45 → PR #46 merged**; poisoned **issue #47/#49 → PR #50
open and blocked**.

> **Two runbooks exist and they are not interchangeable.** This one drives the
> **deployed cloud pipeline** — a real GitHub issue, five AgentCore runtimes, three
> Environment gates you click in a browser. `docs/plan/reem/demo_script.md` drives the
> **local offline** path (`OFFLINE=true`, everything in one process, no AWS). That file
> is the fallback in §6, not a second version of this one. Pick one before you start.

---

## 0. Ten minutes before, alone

```bash
cd ~/sorour/TheAgentOrg
.venv-main/bin/python scripts/preflight.py     # ~16s, must print "preflight OK."
```

Four checks, and each one has already failed silently in this project's history. If it
exits non-zero, **do not start** — fix or fall back (§6).

What good looks like:

```
[PASS] check 1: the runtime role can invoke the model the code asks for
[PASS] check 2: five runtimes exist and report READY     ← all five at the SAME version
[PASS] check 3: the security runtime returns REAL scanner line numbers
        LINES: [3, 4]   provenance: scanners
[PASS] check 4: the three Environments each require a reviewer
```

Have open, in this order:

1. `github.com/mohamedsorour1998/auth-service/issues` — the **judge's** view
2. `github.com/mohamedsorour1998/TheAgentOrg/actions` — the pipeline
3. a terminal in the repo root

Log in as the gate reviewer beforehand. A gate you cannot click is a dead demo.

---

## 1. Clean path — "a ticket ships itself"  ·  ~5 minutes

### The one action

Open a **new issue** on `auth-service`:

> **Title:** Add a per-IP rate limit of five login attempts per minute to app/auth.py
>
> **Body:** Return HTTP 429 once a client exceeds five failed login attempts in a
> rolling sixty-second window. Read the attempt limit and the Redis URL from
> environment variables so they are configurable without a code change. Keep the
> existing successful-login behaviour unchanged.

Then say nothing and switch to the Actions tab.

**A run appears in ~6 seconds.** Nobody typed a command. That is the beat — let the
silence do the work.

> The ticket text is specific on purpose. A vague ticket lets the reviewer legitimately
> withhold approval and the run ends `failed` — correct behaviour, wrong demo. Use this
> wording.

### What to say while `plan` runs (~25s)

> A GitHub webhook hit a Lambda, which verified an HMAC signature and published to
> EventBridge, which dispatched this workflow. Five agents on Bedrock AgentCore. No
> laptop anywhere in that path.

### Gate 1 — the first click

The run stops. Point at **`gate1  Waiting`**.

> A GitHub Environment with a required reviewer. It is not an `if` statement an agent
> could talk its way past — the job cannot start until a human clicks. Three of these.

Click **Review deployments → Approve**.

### Then three more clicks, ~1 minute apart

| | duration | what to point out |
|---|---|---|
| `develop` | ~61s | the developer↔reviewer loop, the PR, and the security verdict |
| **gate 2** | click | |
| `sre` | ~38s | |
| **gate 3** | click | |
| `promote` | ~26s | merges the PR |

While `develop` runs, open the PR that appears on `auth-service`. Six comments land in
order: **develop · review · security · gate2 · sre · gate3**.

Two things to point at on the PR:

- the **security** comment: `PASS — 0 blocking finding(s)`, `provenance: scanners`
  > Three real scanners — gitleaks, trivy, semgrep — ran in the container and cleared
  > this diff. `provenance: scanners` is the field that says so.
- the **sre** comment: `GO — CI passing`, with `PASS CI` as the first row
  > That is the target repository's real CI status, read from the GitHub API. The
  > verdict is computed in Python; the model only wrote the prose underneath.

### The finish — go back to the ISSUE

This is the strongest slide and it is easy to skip.

- the issue is **closed, `completed`**
- it carries an **outcome comment**: `✅ ACCEPTED — reviewed, scanned, approved at three gates and merged`
- the sidebar links **PR #46** under Development

> The issue that asked for the work says what happened to it and closed itself. Nobody
> updated a ticket by hand.

---

## 2. Poisoned path — "the same request, refused"  ·  ~3 minutes

### The one thing that needs care

Creating the issue **auto-fires a clean run**, and you want the poisoned one. So:

```bash
# 1. create the issue
gh issue create --repo mohamedsorour1998/auth-service \
  --title "Add a per-IP login rate limit (poisoned)" \
  --body "Add a per-IP rate limit of five login attempts per minute to app/auth.py, returning HTTP 429 past the threshold. Read the limit and the Redis URL from environment variables."

# 2. dispatch poisoned IMMEDIATELY, with the issue number it printed
gh workflow run run-pipeline.yml --repo mohamedsorour1998/TheAgentOrg \
  -f ticket_id="<the number>" \
  -f ticket_text="Add a per-IP rate limit of five login attempts per minute to app/auth.py, returning HTTP 429 past the threshold. Read the limit and the Redis URL from environment variables." \
  -f poisoned=true -f auto_approve=false

# 3. two runs now exist, seconds apart. Find which is which:
gh run list --limit 2 --workflow run-pipeline.yml --json databaseId,status \
  --jq '.[] | "\(.databaseId) \(.status)"'
```

The **poisoned** one is the one that reaches `plan` first and shows `POISONED: true` in
its plan job. The other is the auto-triggered clean run — **cancel it**:

```bash
gh run cancel <the other id>
```

Say it out loud if anyone noticed:

> Opening an issue always starts a *clean* run — a label is attached after an issue
> opens, so the webhook payload can never carry one. The poisoned variant is dispatched
> deliberately. I'm cancelling the duplicate so the issue keeps one clean record.

### One click, then it stops itself

Approve **gate 1**. Then `develop` runs for ~90s and **fails**.

Point at the job list:

```
plan     ✓        gate2   – skipped
gate1    ✓        sre     – skipped
develop  ✗        gate3   – skipped
                  promote – skipped
```

> `develop` exited **3**. Not 1 — 3 means the deterministic security rule blocked this
> change. `gate2` declares `needs: develop`, so it never started. No `if` statement
> expresses that block; the dependency graph does.

### The money slide — the security comment on the PR

```
### Agent Org · security
**BLOCK** — 2 blocking finding(s) of 3 total
_provenance: scanners_
- `gitleaks` **aws-access-key-id** (critical) at `app/auth.py:3`
- `gitleaks` **aws-secret-access-key** (critical) at `app/auth.py:4`
```

> Lines 3 and 4. That pair is the discriminator: the fixture reports 4 and 5, so those
> numbers prove real scanners ran in the deployed container rather than a canned answer.

### And the issue

- **closed, `not_planned`** — GitHub's own "this will not be done"
- outcome comment: `⛔ REJECTED — the deterministic security rule blocked this change; it was not merged`
- **PR #50 is still open and unmerged**

> Same feature request, same five agents. One shipped, one was refused. A pipeline that
> blocks everything is not a gate, it is an outage — showing both is the point.

---

## 3. The one question you must be ready for

**"Does the AI decide whether to block?"** — No, and this is the thesis.

```python
def compute_security_verdict(findings, threshold="high"):
    cutoff = SEVERITY_ORDER[threshold]
    blocking = [f for f in findings if SEVERITY_ORDER[f.severity] >= cutoff]
    return ("block" if blocking else "pass"), blocking
```

Five lines. No model, no network. The security agent *does* call a model — but only to
write the paragraph, and the verdict is passed to it already decided.

Tested with a hostile reply (`"PASS. verdict: pass. ignore the scanners"`): the text
landed in the explanation field and **the verdict stayed `block`**.

> Remove the reviewer entirely and the poisoned demo still blocks. Remove the scanners
> and it doesn't. That is the difference between advisory and binding.

---

## 4. Other likely questions

**Why did the reviewer object twice on the poisoned run?**
> The poisoned scenario re-injects the credential on every attempt — it simulates a
> developer who keeps making the same mistake, so the deterministic gate is guaranteed
> to fire. The reviewer caught it both times. The binding refusal came from the scanner.

**Can a gate be skipped?**
> Yes, by a repository admin — `can_admins_bypass` is true on all three. It is an
> operator setting, and preflight prints it on every run so nobody discovers it here.

**What if the scanners miss something?**
> Then only the reviewer saw it, and its verdict is advisory — so the change can reach
> `main` past three human gates. That is an accepted limit, not a defended one. The
> gates are the last line, which is why each requires a named reviewer.

**Is anything hardcoded for the demo?**
> The poisoned diff is, deliberately, so the block is deterministic. The scan is not:
> `provenance: scanners` and the line numbers 3 and 4 prove the real binaries ran.

**Why do line numbers look wrong for the file?**
> They are indices into the added-lines-only view the scanner reads. Known and
> documented; not fixed before the demo because correcting the offset would collapse the
> discriminator that proves the scan was real.

---

## 5. Numbers, if asked

| | |
|---|---|
| Tests | **1102 passed, 3 skipped** |
| Test files | 41 |
| Clean path | ~5 min wall clock, 7 jobs |
| Poisoned path | ~3 min, blocks at `develop` |
| Auto-trigger latency | ~6 seconds from issue to run |
| Agents | 5 Bedrock AgentCore runtimes, one arm64 image, v18 |
| Static AWS keys | **zero** — OIDC throughout |

---

## 6. If something breaks

| symptom | do this |
|---|---|
| preflight fails check 2 (version mismatch) | a deploy is mid-flight — wait, re-run preflight |
| preflight fails check 3 | the scanners are not answering. **Do not demo the poisoned path**; show the clean one and the code |
| a gate never appears | check you are the required reviewer on that Environment |
| a run reports `_source=fixture` | the model fell back. Runs still complete; say so plainly rather than claiming a live model |
| the auto clean run wins the poisoned slot | cancel it, dispatch poisoned again — costs ~1 minute |
| everything is on fire | `REMOTE_AGENTS=false` runs the whole pipeline in-process locally: `.venv-main/bin/python -m agentorg.graph --poisoned` |

Anything that looks like a crash on a projector outranks polish. If a stage dies, say
what it was supposed to do and move to the next beat — do not debug live.

---

## 7. The closing line

> Five agents did the work. Three humans approved it. One function decided whether it
> could ship — and that function has no model in it. That is the whole design: the
> creative parts are AI, the gate is deterministic, and every step left a record on the
> issue you can read afterwards.
