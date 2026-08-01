# The Agent Org — Meeting Overview

**Team: RosettaTeam** · Sorour · Mariam · Habiba · Reem · Aya

*Read this in 5 minutes. It's what we're building, why, and who does what.*

---

## The idea in one line

We're building a team of AI agents that reviews and ships code **like a real
engineering org** — with a security guard that **cannot be talked out of
blocking dangerous code**.

## The problem we're solving

When you let an AI write and merge code with no checks, it happily ships secrets,
bugs, and vulnerabilities. Nobody's watching. We prove that, then we fix it.

## What we built

A pipeline a code ticket walks through, start to finish:

```
   ticket
     │
     ▼
  PLANNER ─▶ gate 1 ─▶ DEVELOPER ⇄ REVIEWER ─▶ SECURITY ─▶ gate 2 ─▶ SRE ─▶ gate 3 ─▶ shipped
  (plans)   (human)    (writes)   (checks)     (scans)     (human)  (ok?)  (human)
                                                  │
                                          finds a hardcoded
                                          AWS key ──▶ 🚫 BLOCKED
```

- **5 AI agents**, each with one job: plan, develop, review, security, SRE.
- **3 human gates** — a person approves at key moments, so it's AI-assisted, not
  AI-unsupervised.
- **A hard security block** — if a scanner finds a secret or a critical flaw, the
  pipeline **stops**. This is decided by plain code, not by asking the AI nicely,
  so it fires **every single time**.

## The demo (this is what we show the judges)

Two tickets, same feature ("add a login rate limit"):

| Ticket | What happens |
|---|---|
| **Clean** | Flows all the way through → ✅ shipped |
| **Poisoned** (has a hardcoded AWS key) | Security scanner catches it → 🚫 **blocked, every time** |

Then a **timeline screen** shows exactly what each agent did and why it blocked —
that's the story the judges score.

## The tech (so you know the words)

- **AWS Bedrock AgentCore** — where the agents run (Sorour's AWS account).
- **Strands** — the framework each agent is built with.
- **Terraform** — infrastructure as code for the AWS setup.
- Security scanners: **gitleaks** (secrets), **semgrep** (bad code), **trivy**
  (vulnerable dependencies).

---

## Who does what

| Person | Owns | In plain words |
|---|---|---|
| **Sorour** | AWS + the pipeline brain | Sets up everything on AWS, wires the 5 agents and 3 gates together. The heavy/senior part. |
| **Mariam** | Git + GitHub + deploy | Makes the pipeline open real pull requests, post comments, and run CI. Connects our code to AWS with Sorour. |
| **Habiba** | Security scanners | Runs the tools that catch the hardcoded key. **Her part is what makes the block happen** — the star of the demo. |
| **Reem** | Inputs + correctness testing | Builds the app the agents edit + the two tickets, then tests that every agent's output is **correct**, and builds the "no-checks" baseline. |
| **Aya** | Resilience + metrics testing | Proves the block works **every time**, breaks the pipeline on purpose to prove it fails safe, and builds the before/after DORA table (blocks bad code 10/10). |

*Reem and Aya are the testing pair — they split the test suite evenly and own the
demo together.*

## How we work without stepping on each other

- Everyone owns their **own folder** — no two people edit the same files, so no
  clashes on GitHub.
- We agreed the **data shapes** up front (one file, `state.py`). Everyone builds
  against those shapes, not against each other's half-finished code.
- The whole pipeline **already runs today** on placeholder data. Each of us
  swaps our placeholder for real code, one piece at a time, and nothing breaks.

## The timeline

- **Week 1 (Aug 8–14):** build the skeleton — it runs end to end.
- **Week 2 (Aug 15–21):** make it real — by Fri Aug 21 the poisoned ticket
  blocks every time.
- **Week 3 (Aug 22–27):** polish, rehearse, record a backup video. Ready to
  present.

**One thing to remember:** Reem gives Habiba the poisoned ticket by **Wed Aug
12** — that's the only handoff we can't be late on.

---

## How this fits the hackathon (DevOpsDays Cairo 2026)

**Theme:** *Automate, Accelerate, and Innovate with DevOps & AI* — that's exactly
what we're doing.

**Track:** we hit **Track 1 (Automate Deployment & Operations — smart CI/CD +
security)** and touch **Track 2 (Accelerate Development — AI code review &
testing)**.

**We're scored on 5 things — here's how we win each:**

| Their criterion | Our answer |
|---|---|
| Innovation & Creativity | A deterministic security block an AI *can't* be talked out of — code decides, not a prompt. |
| Practical Impact (they ask for **DORA metrics**) | Aya's before/after table: no-checks ships bad code, Agent Org blocks it 10/10. |
| Technical Implementation | A working pipeline on AWS AgentCore + Strands + Terraform. |
| Usability & UX | The timeline screen — watch every agent's decision on one page. |
| Presentation | Tight 5–7 min live demo: clean passes, poisoned blocks. |

**Two rounds:**
- **Online pre-final: Aug 23 – Sep 8** — live PoC demo. *We're ready Aug 27.*
- **Physical final: Sep 26** at Creativa Giza — working prototype.

> ⚠️ **All slides and the spoken demo must be in English** (international judges).
> We rehearse in English.

---

*Your detailed day-by-day plan is in your own folder here
(`mariam/`, `habiba/`, `reem/`, `aya/` — each has a general README plus
week1/week2/week3 specs). The repo runs right now —
`python -m agentorg.graph --poisoned` shows the block live.*
