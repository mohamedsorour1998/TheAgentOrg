# The competitive landscape

**Spec §7.** Where each competitor is **better**, and the one row that survives scrutiny.

**A matrix we win every row of is believed by nobody** — and the first draft of this
document was that matrix. It claimed Copilot ran no scanners and Claude Code had no
deterministic rule. **Both were wrong**, and §6 records how. Every claim below carries the
URL it came from; anything unverified is marked **UNVERIFIED** rather than asserted.

Read 2026-08-28. **Names change fast**: "Copilot coding agent" is now **Copilot cloud
agent** (the `/coding-agent/` doc URLs 404), Copilot **Workspace** is withdrawn, and
Windsurf was renamed **Devin Desktop** on 2026-06-02.

---

## 1 · THE STRONGEST ROW, and it is not the one I first wrote

**Every major vendor's LLM code review is advisory, and each says so in its own
documentation.** This is the exact advisory-reviewer / binding-security split this
pipeline is built around, conceded by the people selling the alternative:

| Product | Their words |
|---|---|
| **Copilot code review** | *"Copilot always leaves a 'Comment' review, not an 'Approve' review or a 'Request changes' review… will not block merging changes"* — [docs](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/request-a-code-review/use-code-review) |
| **Anthropic managed Code Review** | *"The check run **always** completes with a neutral conclusion so it never blocks merging through branch protection rules"* — [docs](https://code.claude.com/docs/en/code-review) |
| **OpenAI Codex** | review rules *"don't replace tests, branch protections, or required approvals"* — [docs](https://learn.chatgpt.com/docs/third-party/github) |
| **Cursor Bugbot** | *"Requiring the status alone **does not block merges on findings because findings default to `neutral`**"* — [docs](https://cursor.com/docs/bugbot.md) |

And **Snyk's own platform page argues this repository's thesis verbatim: "The generator
cannot be the validator."**

**Three of four security vendors then describe the same architecture** — the scanner
publishes a status and the *SCM's branch protection* does the blocking. Snyk: without
branch protection *"the status is informational only."* Semgrep: blocking *"is dependent
on your CI provider."* SonarQube: *"the gate itself does not prevent merging."*

**Ours is structural instead, and that is the difference worth defending.** `develop`
exits 3 and `gate2` declares `needs: develop`, so **no `if:` expresses the block — the
dependency graph does**, and no misconfigured status check can let it through.

---

## 2 · WHERE THEY ARE BETTER, and it is not close

### Language breadth — everyone wins, and our number is embarrassing

Measured on our own repository:

```
agentorg/security/semgrep_rules.yml    1 rule    languages: [python]
agentorg/security/gitleaks.toml        2 rules   aws-access-key-id, aws-secret-access-key
```

**And both scanners run with `--config` pointed at those files** —
`semgrep_tool.py:132`, `gitleaks_tool.py:105` — so neither tool's default registry is in
play. **Three rules, one language.** Semgrep's registry carries thousands; CodeRabbit
ships **61 tool integrations** including semgrep, trivy, checkov and osv-scanner.

Every competitor is language-agnostic because a general model is. Our prompts **name the
stack explicitly** (Flask, Python — 2–3 mentions each) because CLAUDE.md records the
developer agent writing **Go for a Flask app** without it. That fix bought reliability by
buying specificity, and the bill is a pipeline tuned for one stack.

### IDE integration — Cursor and Claude Code win outright

Cursor is a **VS Code fork**, so it inherits the editor, keybindings, settings import and
the extension model — language servers and debuggers for everything. Claude Code runs on
**six surfaces** from one engine. **We have none.** No editor can invoke this pipeline;
the entry points are a GitHub issue, `workflow_dispatch`, the control-plane API and the
web app. The moment a developer most wants a check is while they are writing.

### SCM breadth, trigger surface, ecosystem, enterprise plumbing

- **SCM:** Devin covers GitHub/GitLab/Bitbucket/Azure DevOps; Tembo does coordinated
  multi-repo PRs across three forges. We are GitHub-Actions-shaped.
- **Triggers:** Copilot fires from Slack, Teams, Raycast, Mobile, JIRA, Linear, Azure
  Boards and cron. We have an issue and a dispatch.
- **Enterprise:** SAML/SCIM, zero data retention, audit logs, air-gapped deployment,
  CMEK, data residency, SLAs. We have none of it.
- **Scale:** the Codex CLI alone has **119,472 GitHub stars**.

### Sandbox and gate design — two places theirs is genuinely better

**Azure DevOps has a stronger tamper property than ours.** Five ordered check categories,
and *"Checks are not defined in YAML"* — resource owners manage them in the web UI, so
*"users modifying the pipeline yaml file can't modify the checks."* Its FAQ even
recommends Invoke REST API to wait on an external scanner: Microsoft recommending the
assembly we built. **GitHub also documents "Prevent self-review" and an option to
disallow admin bypass** — relevant because all three of our Environments measure
`can_admins_bypass=True`.

**Copilot's run cap is real and ours is not.** A 59-minute hard limit *"that cannot be
extended or bypassed."* We have a per-scanner timeout and no whole-run cap.

### They are shipping products and we are not

Our web sign-in has never completed (`limitations.md` §11), our durable queue is invoked
by nothing in production (§13), and no browser has run our browser tests (§14).
**Seventeen** limitations are written down. That is a real answer to "how does this
compare", and not a favourable one.

---

## 3 · The row that survives — and it is a SEAM distinction, not "deterministic vs not"

**The category is not empty and the first draft implied it was.** Four products come close
enough that a judge will find them:

| Product | Has | Missing |
|---|---|---|
| **Factory AI · Droid Shield** | a genuine deterministic block — pattern + entropy scan that **hard-blocks `git commit` and `git push`**, with patterns blocking and ML classifiers only *warning*. Plus four autonomy levels that pause for approval | guards only secrets, only inside Droid; **bypassed by running git manually**. Not a pipeline stage with an exit code |
| **OpenHands** (OSS, 85,408 stars) | deterministic `PatternSecurityAnalyzer` / `PolicyRailSecurityAnalyzer` and `ConfirmRisky(threshold=HIGH)` | its stated principle is **"Confirm, don't block"** — no hard deny; `conversation.execute_tool()` bypasses both layers; the issue→PR path has **no** approval step |
| **GitLab Duo Agent Platform** | genuinely multi-agent (Planner, Security Analyst, CI Expert) with autonomous MR commits, **and "Agent tool governance" policies "to gate sensitive agent actions with human approval at execution time"** | governance is **beta**, and it gates **tool calls mid-execution**, not stage output. Code-producing flows land as ordinary MRs |
| **Google Jules** | the cleanest **two** human gates in the market: plan confirm, then diff approve, then PR | no deterministic rule at all |

**And two products have deterministic primitives I wrongly said they lacked:**

- **Claude Code's permission deny rules** are *"enforced by Claude Code, not by the
  model"*, take precedence deny → ask → allow, hold even under `bypassPermissions`, and
  *"Hooks can tighten restrictions but not loosen them."* That is a real non-LLM gate.
- **`codex-security` is the closest thing to `compute_security_verdict` that ships:**
  `--fail-on-severity high`, **exit 1 for a finding at or above threshold**, exit 2 for
  input error *or incomplete coverage*, SARIF export. Research preview, opt-in, still
  model-driven rather than a pattern matcher — and both of OpenAI's sample pipelines *"are
  report-only because they omit `--fail-on-severity`."*

### So the honest claim is narrower, and it is about the SEAM

> Every gate above guards a **tool call inside one agent's session**. Ours guards a
> **pipeline stage between agents**, with a named human reviewer, and the block is
> expressed as a dependency edge rather than a status check.

That distinction survives scrutiny. **"We are the only deterministic one" does not** — and
the useful version of the original claim is: *no product ships multi-agent generation, a
deterministic non-LLM block on stage output, and human approval gates between stages as
one pipeline.*

### Two fail-open behaviours worth naming, because they are our own design in reverse

- **Cursor hooks:** exit 2 denies, but *"Other exit codes — Hook failed, action proceeds
  (fail-open by default)."*
- **Claude Code PreToolUse hooks:** exit 2 blocks and cannot be overridden — but **exit 1
  does not block**, and a missing or non-executable hook **fails open**: *"A mistyped path
  silently disables the gate."*
- **Semgrep:** on an internal crash it *"sends anonymous crash report… and returns exit
  code 0."*

All three are this repository's signature defect shape — a check that did not run reading
as a check that passed — in shipped products. It is the direct argument for
`SCANNERS_REQUIRED` and for `unrunnable_findings` **raising** rather than returning `[]`.

---

## 4 · Copilot runs real scanners, and the first draft said it did not

The correction that matters most, because it was the load-bearing error. The Copilot cloud
agent runs **CodeQL, secret scanning, and GitHub Advisory Database checks** for malware and
CVSS High/Critical, plus Copilot code review as a second opinion — and this *"does not
require a GitHub Advanced Security or secret protection license."*

**It also has genuine agent-specific gates**, not just ordinary branch protection:

- *"**By default, GitHub Actions workflows will not run automatically when Copilot pushes
  changes to a pull request**"* — a human with write access clicks **Approve and run
  workflows**.
- *"Copilot cloud agent cannot mark its pull requests as 'Ready for review' and cannot
  approve or merge a pull request."*
- The triggering human's approval *"won't count toward the required number."*

**Two defensible distinctions remain.** Theirs runs *inside the agent's own loop* as a
self-check it *"attempts to resolve"* — the generator validating itself, which is the thing
Snyk's page names as impossible. And **a repository admin can switch each validation tool
off**, with GitHub's own warning that doing so *"may allow unreviewed code written by
Copilot to gain write access to your repository or access your GitHub Actions secrets."*

**GitHub push protection is the one genuinely deterministic non-LLM block on the merge path
in this whole survey**, and its **delegated bypass is a real approval workflow** —
nominated actors review and approve bypass requests, every bypass alerts and audit-logs.
That is better than our bypass story, where an admin simply proceeds.

---

## 5 · The category collapsed, and that is evidence for the thesis

The 2023-era issue→PR bot **no longer exists as a category**:

| Product | Status |
|---|---|
| **Sweep** | pivoted to a JetBrains autocomplete plugin. Founders on [YC](https://www.ycombinator.com/companies/sweep): *"We originally started Sweep in 2023 to build an AI junior developer… We soon realized that this was many years out."* |
| **Codegen** | **dead** — acquired by ClickUp, *"officially deprecated on January 16, 2026"* |
| **Cosine** (Genie) | pivoted to air-gapped/COBOL work |
| **Ellipsis** | pivoted to *"spawn Claude Code and Codex in the cloud"* with budget caps |
| **Solver / Laredo** | dead (circumstantial — expired site, bare 404) |
| **SWE-agent** | maintenance-only, superseded |
| **Survivors** | **CodeRabbit** ($143M Series C at $1.5B), **Factory**, **Tembo**, **Qodo**, **Greptile**, **Jules**, **OpenHands** |

**The survivors converged on review and orchestration with human control, not autonomy.**
Sweep concluded the autonomous junior developer was years out; Ellipsis and Tembo both
retreated to running someone else's agent in a sandbox with budget caps and scoped
credentials. That is the market arriving at this pipeline's premise from the other
direction.

---

## 6 · MY OWN CLAIMS, DISPROVED BY THE RESEARCH I COMMISSIONED

Recorded because it is this repository's named pattern arriving in a **competitive
analysis** — an assertion that could not be falsified by reading, only by checking:

| I wrote | Actually |
|---|---|
| Copilot has **no** deterministic blocking rule; scanning is *"something you add"* | it runs **CodeQL + secret scanning + Advisory DB**, no extra licence |
| Copilot's approval gates are **"not described"** | Actions **do not run** on an agent push until a human clicks *Approve and run workflows* |
| Claude Code has **no** deterministic rule, only per-action prompts | **permission deny rules** are *"enforced by Claude Code, not by the model"* and survive `bypassPermissions` |
| **"Nothing found combines all three"** | Factory, OpenHands, GitLab Duo and Jules each have two of the three |
| Devin's gates: *"the page never says so"* | *"Devin is subject to the exact same branch protections and SDLC policies as any human engineer"* |

**The first draft was written from one doc page per product**, and a doc page describes
what a vendor chose to put on it. Four of the five errors above are in the *flattering*
direction — which is what a competitive matrix does to its author unless somebody checks.

**Four assumptions in my own brief were also stale:** Copilot's premium requests became
**AI Credits** on 2026-06-01 (1 credit = $0.01); Devin's ACUs survive **only on
Enterprise** (Teams is **$80/mo minimum**, not ~$500); Cursor's docs cite **3.7+**, not
2.x; and Codex's `suggest`/`auto-edit`/`full-auto` naming is gone, replaced by sandbox
modes plus approval policies.

---

## 7 · Who should not buy this

The most useful section, and the one a matrix normally omits.

- **A team wanting an assistant while they type.** Buy Cursor or Claude Code.
- **A polyglot codebase.** Three rules, one language. Buy Semgrep and configure it.
- **A team on GitLab, Bitbucket or Azure DevOps.** Buy GitLab Duo — and its check model
  is arguably better than ours.
- **A team that needs enterprise plumbing or support.** There is none here.
- **A team without a human who will click three times per change.** The gates are the
  product; unstaffed, the pipeline is theatre with extra steps.

**Who should:** a team that has already decided to let agents open pull requests, and now
needs the answer to *"what stops one of them merging a credential?"* to be a dependency
edge rather than a person's attention.
