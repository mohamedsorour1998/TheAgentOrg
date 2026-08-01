# The Agent Org — Repo Structure & 3-Week Team Plan (Design)

**Date:** 2026-08-01
**Author:** Mohamed Sorour + Claude
**Status:** Approved

## What this is

The Agent Org is a multi-agent CI/CD pipeline built on **AWS Bedrock AgentCore** with
**Strands** agents and **Terraform** infrastructure. A ticket flows through five role
agents — planner → developer → reviewer → security → SRE — with three human approval
gates. A deterministic security **block rule** (in `state.py`, not a prompt) stops any
change carrying a high/critical finding. The demo: a poisoned ticket (hardcoded AWS
credentials) is blocked every single run.

The assessment window opens **Aug 23**. Target ready date **Aug 27**, assuming the team
requests and receives an early-September slot from the organizers.

## Conventions

The stack follows a small set of fixed conventions so every lane looks like the same
codebase:

- **Strands agent = FastMCP server** exposing one `run()` tool, built with
  `Agent(model=create_model(), system_prompt=..., tools=[...])`, deployed on AgentCore.
- **`create_model()`** returns a `BedrockModel` (Nova) via IAM role, with an
  OpenAI-compatible fallback when `LLM_BASE_URL` is set. Lives in `agentorg/common/`.
- **Terraform** in a `modules/` + `environments/shared/` layout (`main.tf`,
  `providers.tf`, `backend.tf`, `variables.tf`, `outputs.tf`) with an **S3 backend**,
  `aws ~> 6.28`, and a `locals { name, region, tags }` block; ECR repos + an IAM role
  trusting `bedrock-agentcore.amazonaws.com` + a GitHub OIDC CI role.
- **`common/`** shared `model.py`, `config.py`, `health.py`, `validation.py`.

## The decoupling principle (why nobody blocks anybody)

Everyone codes against `state.py` (the frozen data contract) and `fixtures/` (validated
sample results) — **never against each other's live code**. Each person owns their **own
directory**, so on GitHub no two people edit the same files → no merge conflicts. A fully
**stubbed pipeline runs end-to-end on day 1**; each person swaps their own stub for real
code independently, task after task. The function signatures are frozen in `state.py`, so a
swapped implementation flows into everyone else's lane without breaking it.

Rule after week 1: you may **ADD** optional fields to the models. Never rename or remove one.

## Ownership

| Person | Skill | Owns (their own dirs) |
|---|---|---|
| **Mohamed Sorour** (senior) | devops / fullstack / ai | `infra/` (all Terraform: ECR, IAM AgentCore role, AgentCore runtimes, S3 backend), `agentorg/common/`, `agentorg/graph.py`, `agentorg/gates.py`, `agentorg/log.py`, the agent stubs, AgentCore deploy |
| **Mariam** | DevOps | `agentorg/github_ops.py`, `.github/workflows/` (CI), **co-owns AgentCore deploy with Sorour** — the integration/deploy seam that wires into the graph and AWS |
| **Habiba** | DevOps | `agentorg/security/` (semgrep/gitleaks/trivy wrappers) |
| **Reem** | testing | `target_repo/` (the app agents modify) + `tickets/` (clean + poisoned) + correctness tests (`tests/test_functional_*`) + the no-checks baseline (`tests/test_baseline.py`) |
| **Aya** | testing | resilience + metrics tests: block determinism (`tests/test_block_*`), chaos (`tests/test_chaos_*`), DORA (`tests/test_dora_*`) |

Reem and Aya are the testing pair; they share `tests/` split by filename (different files, no
conflicts). Reem builds the no-checks baseline, Aya consumes it in the DORA metrics.

**Sorour takes all AWS and all Terraform** — the senior, hard work. The other four lanes are
self-contained and require no AWS. Mariam's lane is deliberately the *integration seam*:
`graph.py` calls `github_ops.open_pr(state) -> pr_url`, stubbed on day 1, so the graph runs
immediately while Mariam fills in the real PyGithub/AWS calls on her own branch. The
dependency is one-directional and never stalls the graph.

## Structure

```
TheAgentOrg/
├── README.md  .gitignore  pyproject.toml  .env.example
├── infra/                          # SOROUR — Terraform, S3 backend
│   ├── Terraform/
│   │   ├── environments/shared/ {backend,providers,variables,main,outputs}.tf + terraform.tfvars
│   │   └── modules/agentcore/ {main,variables,outputs}.tf
│   └── README.md
├── agentorg/
│   ├── state.py                    # the contract, unchanged
│   ├── common/ {model,config,health,validation}.py   # SOROUR
│   ├── graph.py  gates.py  log.py  # SOROUR
│   ├── github_ops.py               # MARIAM
│   ├── agents/ {planner,developer,reviewer,security,sre}.py   # stubs, SOROUR
│   └── security/ {semgrep,gitleaks,trivy}_tool.py             # HABIBA
├── target_repo/  tickets/          # REEM
├── tests/                          # AYA
├── fixtures/  make_fixtures.py     # canonical, done
└── docs/plan/ {00-timeline, mohamed-sorour, mariam, habiba, reem, aya}.md
```

## Plan docs

`docs/plan/` — one file per person plus `00-timeline.md`. Each person's file has
**Week 1 / Week 2 / Week 3**; every task carries a **"done when"** line and an explicit
**"unblocked because…"** note so the flow is natural. Calendar Aug 8 → Aug 27.

The one true cross-dependency — Reem's poisoned ticket → Habiba's scanners, due **Wed
Aug 12** — is flagged in both files. Everything else is parallel-safe via fixtures.

## Testing

- `make_fixtures.py` validates all fixtures against `state.py` on every run (already green:
  block rule fires with 2 blocking findings).
- Aya's `tests/` treat the pipeline as a black box against fixtures.
- The stubbed pipeline is importable and runnable end-to-end from commit 1.

## Cut-list (if behind)

Cut in order: SRE agent first, then the approve/reject UI (use CLI instead). **Never** cut
the security block or the log timeline — the block is the demo, the timeline is the UX the
judges score.
