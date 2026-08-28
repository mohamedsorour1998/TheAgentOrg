# The competitive landscape

**Spec §7.** Where each competitor is **better**, and the one row nobody else has.

**A matrix we win every row of is believed by nobody**, so §2 is where they beat us and
it is longer than §3. Every claim about another product carries the URL it came from and
the date it was read; anything not verified against a primary source is marked
**UNVERIFIED** rather than asserted.

Read 2026-08-28.

---

## 1 · What each product actually is

| Product | What it is | Primary source |
|---|---|---|
| **GitHub Copilot coding agent** | an agent assigned a task, working in an ephemeral GitHub-hosted environment, opening **exactly one pull request per task** | [docs.github.com](https://docs.github.com/en/copilot/concepts/agents/coding-agent/about-coding-agent) |
| **Claude Code** | an agentic coding tool across terminal, IDE, desktop, web and mobile, that stages changes, writes commits and opens pull requests | [code.claude.com](https://code.claude.com/docs/en/overview) |
| **Devin (Cognition)** | *"an autonomous AI software engineer that can write, run and test code"*, prompt to PR | [docs.devin.ai](https://docs.devin.ai/get-started/devin-intro) |
| **Cursor** | an IDE with an agent mode | **UNVERIFIED** — not read against a primary source for this document |
| **Snyk · GitGuardian · Semgrep** | CI security gates. **Not agent tools** — the comparison for our *gate*, not our *agents* | **UNVERIFIED** on current policy specifics |
| **The Agent Org** | five role agents, three human approval gates, and a deterministic block rule | this repository |

---

## 2 · WHERE THEY ARE BETTER, and it is not close

### IDE integration — Claude Code and Cursor win outright

Claude Code runs on **five surfaces** from one engine — terminal, VS Code, JetBrains,
desktop and browser — with inline diffs, `@`-mentions and plan review in the editor, plus
Slack, Chrome, mobile and Remote Control to move a session between devices.

**We have no IDE integration at all.** A developer cannot invoke this pipeline from their
editor. The only entry points are a GitHub issue, a `workflow_dispatch`, the control-plane
API, and the web application. That is a deliberate consequence of building a *pipeline*
rather than an *assistant*, and it is still a real gap: the moment a developer most wants
a check is while they are writing, and we cannot be there.

### Language breadth — everyone wins, and our number is embarrassing

Measured on our own repository rather than estimated:

```
agentorg/security/semgrep_rules.yml    1 rule    languages: [python]
agentorg/security/gitleaks.toml        2 rules   aws-access-key-id, aws-secret-access-key
```

**And both scanners run with `--config` pointed at those files**, measured at
`semgrep_tool.py:132` and `gitleaks_tool.py:105` — so neither tool's default registry is
in play. Semgrep's own registry carries thousands of rules across dozens of languages;
gitleaks ships ~150 secret patterns. **We use three rules total, and one language.**

Trivy is the exception and is language-agnostic, but it is scanning a materialised diff
rather than a dependency manifest, so its CVE database is largely unexercised.

Every competitor above is language-agnostic because a general model is. Our agents'
prompts **name the stack explicitly** — Flask and Python, measured, 2–3 mentions per
prompt — and CLAUDE.md records exactly why: without it the developer agent wrote **Go for
a Flask app** and every revision inherited the guess. That fix bought reliability by
buying specificity, and the bill is that the pipeline is tuned for one stack.

### Polish, ecosystem and support — not comparable

Copilot's agent has scheduled automations, Azure Boards / JIRA / Linear entry points, MCP
configuration, and a metrics dashboard. Claude Code has skills, hooks, sub-agents, an
Agent SDK, routines, and eight documented install paths. Both have a support
organisation, a release channel, and a security team.

We have one repository, five people, and a hackathon deadline. **A judge should assume
every ergonomic comparison goes against us**, and the honest framing is that we are not
competing on ergonomics.

### Sandbox maturity — Copilot's is better than ours

Copilot's agent runs in an ephemeral environment with a **59-minute hard cap** described
as *"a hard limit that cannot be extended or bypassed"* and a configurable
`timeout-minutes`. Ours has `SCANNER_TIMEOUT_SECONDS` per scanner invocation and no
whole-run cap at all — a hung stage holds an Actions runner until GitHub's own six-hour
limit.

### And the one that matters most: they are shipping products and we are not

Copilot's agent, Claude Code and Devin are generally available with paying customers.
Our sign-in flow **has never completed once** (`limitations.md` §11), our durable queue
is invoked by nothing in production (§13), and no browser has run our browser tests
(§14). Sixteen limitations are written down. That is a real answer to "how does this
compare", and it is not a favourable one.

---

## 3 · The one row nobody else has

| | deterministic non-LLM blocking rule | human approval gates | multi-agent generation |
|---|---|---|---|
| Copilot coding agent | **no** — security scanning is something *you add* via hooks, for *"validation, logging, security scanning"* | not described; repository rules can **block the agent** instead | no |
| Claude Code | **no** — permission prompts are per-action, not a policy threshold | per-action permission prompts (a different thing) | yes — sub-agents, an Agent SDK |
| Devin | **not described.** Nothing on permission prompts, confirmation steps, *"or any deterministic non-LLM rule that halts execution"*. The closest is **advice**: write clear prompts, make tasks easy to verify | draft PRs awaiting review implies a human merges — **the page never says so** | no |
| Snyk / Semgrep / GitGuardian in CI | **yes** — this is exactly what they do | no — they are checks, not workflows | no |
| **The Agent Org** | **yes** | **yes — three, each a platform-level Environment** | **yes — five agents** |

**Nothing found combines all three.** The security tools have the deterministic gate and
no agents; the agent tools have the agents and no gate. We are the intersection, and the
intersection is the entire thesis: *a model that can be persuaded, distracted or
prompt-injected must not be the thing standing between a credential and `main`.*

### Three qualifications, because the row above is the one to be sceptical of

**"Nothing found" is not "nothing exists."** This is a search, not a market survey. A
private platform team somewhere has almost certainly wired Semgrep's exit code into an
agent's PR — that is a few days of work and it is the obvious thing to do. The claim is
about *products*, and the useful version is narrower: **no product ships this
combination as its central promise.**

**Our gate is three rules deep and theirs is thousands.** §2 measured it. A Snyk or
Semgrep deployment blocks on a vastly larger rule set than ours, so "we have a
deterministic gate and they do not" is true *of the agent tools* and false of the security
tools — and the security tools are the ones a real team already runs. The honest
composition is: **their rules, our structure.**

**Copilot's hooks make this achievable rather than impossible.** Its documentation says
hooks run shell commands at execution checkpoints for *"validation, logging, security
scanning, or workflow automation."* So a Copilot user can bolt a scanner on. What they
cannot easily get from that page is the part we are actually claiming — a *verdict* that
is computed by arithmetic, recorded on the state, and structurally unable to be argued
with by the model that wrote the change.

---

## 4 · The four claims that survive scrutiny

Each one is narrow on purpose, and each is checkable in this repository:

1. **The verdict is arithmetic and the model cannot reach it.** `compute_security_verdict`
   is five lines of Python with no model and no I/O, called in exactly one place on the
   pipeline path. Measured: a hostile retrieved document claiming an approved exception
   moved the verdict **0 times in 8 trials**, and `retrieval/guard.py` refuses the six
   argument names a verdict reads.
2. **Three approvals are platform-level, not steps a pipeline can skip.** A rejected
   GitHub Environment *skips* its job. **And the honest limit is on the same slide:** all
   three measure `can_admins_bypass=True`, so a repository admin can bypass them —
   reported by `preflight.py` check 4 on every run, and deliberately not failed on.
3. **A real scan is distinguishable from a fixture read, by one field.** Real scanners
   report added lines `[3, 4]`; the fixture reports `[4, 5]`. Everything else — the
   verdict, `blocking=2`, both rule names, the file, the tool, the severity — is
   byte-identical between the two paths. This is the check no competitor needs and we do,
   because our agents degrade to fixtures by design.
4. **It costs 1.3–1.7¢ per change and the whole bill is legible.** Measured, priced from
   the AWS Pricing API with the read date attached, with 99.9% of it identified as the
   model. None of the products above publishes a per-change cost.

---

## 5 · Who should not buy this

The most useful section, and the one a matrix normally omits.

- **A team wanting an assistant while they type.** Buy Claude Code or Cursor. We are not
  in the editor and will not be soon.
- **A polyglot codebase.** Three rules and one language. Buy Semgrep and configure it.
- **A team without a human who will click three times per change.** The gates are the
  product; if nobody will staff them, the pipeline is theatre with extra steps.
- **A team that needs support.** There is none.

**Who should:** a team that has already decided to let agents open pull requests, and now
needs the answer to *"what stops one of them merging a credential?"* to be something other
than a person's attention.
