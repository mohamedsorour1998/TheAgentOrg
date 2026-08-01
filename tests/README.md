# tests/ — pipeline test + metrics harness

**Owners: Reem + Aya (the testing pair).** You split this folder evenly by
**filename**, so you never edit the same file and never conflict on GitHub.

You both test the pipeline as a **black box**: call `run_pipeline(...)` and assert
on the final `RunState`. Neither of you needs to know how an agent works inside.

## Who writes which files

| File(s) | Owner | What it checks |
|---|---|---|
| `test_functional_contract.py` | **Reem** | Each agent's output validates against `state.py` and the values are sane. |
| `test_functional_flow.py` | **Reem** | Clean ticket → `promoted`; the revision loop fires on `changes_requested`. |
| `test_baseline.py` | **Reem** | The "no-checks" path (one agent writes + merges, no gates) — the "before" for DORA. |
| `test_block_determinism.py` | **Aya** | Poisoned ticket → `blocked` with 2 findings, across 20+ repeated runs. |
| `test_chaos_*.py` | **Aya** | Hang a gate / loop the reviewer / kill a scanner → pipeline fails safe. |
| `test_dora_*.py` | **Aya** | Metrics harness: run N tickets through baseline vs Agent Org, collect numbers. |
| `test_pipeline_smoke.py` | shared (starter) | Already green — the pattern to copy. |

## The one handoff between you

Reem builds `test_baseline.py` (the no-checks path). Aya consumes it in
`test_dora_*.py` to build the before/after table. Until Reem's baseline lands,
Aya works against the smoke test — nobody is blocked.

## Run everything

```bash
pytest -q
```
