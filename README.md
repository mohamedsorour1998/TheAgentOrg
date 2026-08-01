# The Agent Org

*by **RosettaTeam** — Sorour · Mariam · Habiba · Reem · Aya*

A multi-agent CI/CD pipeline. A ticket flows through five role agents —
**planner → developer → reviewer → security → SRE** — with three human approval
gates. A deterministic security **block rule** (pure code in `state.py`, not a
prompt) halts any change carrying a high/critical finding.

Built on **AWS Bedrock AgentCore** + **Strands** + **Terraform**.

> **The demo:** a poisoned ticket (hardcoded AWS credentials) is blocked on every
> single run — because the verdict is computed by code, not guessed by a model.

## Quick start

```bash
pip install -e ".[dev]"

python make_fixtures.py            # regenerate + validate all fixtures
pytest -q                          # run the test suite

python -m agentorg.graph           # clean ticket   -> promoted
python -m agentorg.graph --poisoned # poisoned ticket -> blocked
```

Everything runs **on stubs** from day 1 — no AWS, no GitHub, no scanners needed
to see a ticket walk the whole pipeline. Each teammate swaps their own stub for
real code without breaking anyone else, because every lane talks through the
frozen contract in `agentorg/state.py`.

## Who owns what

| Directory | Owner | What |
|---|---|---|
| `infra/` | **Sorour** | All Terraform: ECR, IAM AgentCore role, S3 backend |
| `agentorg/common/`, `graph.py`, `gates.py`, `log.py`, `agents/` | **Sorour** | Model provider, the graph, human gates, decision log, agent stubs |
| `agentorg/github_ops.py`, `.github/workflows/` | **Mariam** | Branch/PR/comments + CI; co-owns AgentCore deploy |
| `agentorg/security/` | **Habiba** | semgrep / gitleaks / trivy wrappers |
| `target_repo/`, `tickets/`, `tests/test_functional_*`, `test_baseline` | **Reem** | The app + tickets + correctness tests + the no-checks baseline |
| `tests/test_block_*`, `test_chaos_*`, `test_dora_*` | **Aya** | Determinism, chaos, DORA metrics |

## How nobody blocks anybody

1. `agentorg/state.py` is the frozen contract. You may **add** optional fields;
   never rename or remove one.
2. `fixtures/` holds a validated sample of every result. Your stub loads a
   teammate's fixture instead of waiting for their real code.
3. Each person owns their **own directory** — no two people edit the same files,
   so no merge conflicts on GitHub.

The one real cross-dependency: Reem's poisoned ticket → Habiba's scanners, due
**Wed Aug 12**. Everything else is parallel-safe.

## The plan

Per-person plans are in [`docs/plan/`](docs/plan/) — one folder per person
(general README + week1/week2/week3 specs). Start with
[`00-timeline.md`](docs/plan/00-timeline.md).
