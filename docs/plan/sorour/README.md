# Sorour — General Plan

**Role:** lead — devops / fullstack / ai. **Lane:** all of AWS + the graph.

Owns: `infra/` (all Terraform), `agentorg/common/`, `agentorg/graph.py`,
`agentorg/gates.py`, `agentorg/log.py`, the agent stubs in `agentorg/agents/`,
and the AgentCore deploy (co-owned with Mariam in week 3).

You are the senior. You take the hard AWS work so the other four lanes are
clean and self-contained, requiring no AWS account or credentials of their
own. Your job shifts week over week: week 1 you stand up infrastructure, week
2 you make the agents real, week 3 you make the whole thing run for the demo.

## The shape of your 3 weeks

| Week | Theme | The one thing that must be true by Friday |
|---|---|---|
| [1](week1.md) | Skeleton + AWS live | ECR + IAM roles exist, Bedrock answers a real prompt, stubbed pipeline still green |
| [2](week2.md) | Agents go real | Poisoned ticket blocks every time on real scanners + real agents |
| [3](week3.md) | Deploy + rehearse | Full demo runs on AgentCore, online and offline, twice, clean |

## Where you plug into everyone else

- **Mariam** — your `graph.py` calls her `github_ops.open_pr(state)` and
  `github_ops.post_comment(...)`. Stubbed today, so your graph runs before she
  writes a line. You two co-own the week-3 AgentCore deploy — coordinate on
  the IAM ARNs and what `DevResult` fields the PR node needs.
- **Habiba** — your security agent calls her `run_all_scanners(dev)` and
  applies `compute_security_verdict()` (already in `state.py`). Until her
  scanners land, the stub findings keep your graph moving.
- **Reem / Aya** — they test against the frozen contract and `fixtures/`, never
  against your live code. You don't block them; they don't block you.

## The one rule

`agentorg/state.py` is frozen. You may **add** optional fields. Never rename
or remove one — a rename breaks all five lanes at once.
