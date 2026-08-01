# Mariam — General Plan

**Role:** DevOps. **Lane:** the integration seam between Git/GitHub and the
pipeline.

Owns: `agentorg/github_ops.py` and `.github/workflows/` (CI), plus you co-own
the AgentCore deploy with Sorour in week 3.

Your code is what connects the graph to the outside world — every run opens a
PR and posts comments through you. It plugs directly into Sorour's graph, so
you two coordinate often — but it's fully stubbed today, so you can **never**
block him. You don't need AWS for weeks 1–2; everything runs against a
throwaway GitHub repo and local git.

## The shape of your 3 weeks

| Week | Theme | The one thing that must be true by Friday |
|---|---|---|
| [1](week1.md) | Seam works on a real repo | `open_pr` and `post_comment` open a real PR and comment |
| [2](week2.md) | CI + offline mode | Every PR shows a CI check; the pipeline also runs with wifi off |
| [3](week3.md) | Deploy + hand off | The graph runs against AgentCore-hosted agents |

## Where you plug into Sorour

He calls your two functions from `graph.py`:
```python
state.dev = github_ops.open_pr(state)          # you return DevResult w/ pr_url
github_ops.post_comment(state, explanation)     # you post the block reason
```
The signatures are frozen in the stub, so his graph runs whether your insides
are stubs or real. Coordinate with him on: what fields the PR node reads from
`DevResult`, and the week-3 AgentCore deploy. Ask him early what he expects
the PR comment to contain — cheaper to agree than to redo.

## The one rule

Never touch `agentorg/state.py`. If you need a field, ask Sorour to add it —
add-only, never rename.
