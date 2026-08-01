# Habiba — General Plan

**Role:** DevOps. **Lane:** the security scanners.

Owns: `agentorg/security/` — three wrappers (`semgrep_tool.py`,
`gitleaks_tool.py`, `trivy_tool.py`) and `run_all_scanners` in `__init__.py`.

Your lane is the most self-contained on the team: it depends only on
`state.py` and the scanner CLIs, and never imports the graph. You can build
and test it entirely on your own. **It is also the most important — your
findings are what block the poisoned ticket, the whole demo.**

You do **not** decide pass/block. You only produce `Finding` objects. The
verdict is computed by `compute_security_verdict()` in `state.py` (pure
code) — that separation is what makes the demo deterministic. Good answer
when a judge asks "how do you know it isn't the model guessing?"

## The shape of your 3 weeks

| Week | Theme | The one thing that must be true by Friday |
|---|---|---|
| [1](week1.md) | Scanners run by hand, then wrapped | `run_all_scanners` returns real findings from real tools |
| [2](week2.md) | The block goes deterministic | Poisoned ticket blocks every time on your real scanners |
| [3](week3.md) | Harden + hand off | Failures are safe; demo runs are fast |

## Where you plug into Sorour

His security agent calls your `run_all_scanners(dev) -> list[Finding]`, then
`compute_security_verdict(findings)` decides pass/block. You never touch the
graph directly — you just need to return correct `Finding` objects for a
given `DevResult`.

## The one cross-dependency

Reem's poisoned ticket → your scanners, due **Wed Aug 12**. Until it lands,
work against `fixtures/dev_result_poisoned.json` — you're never actually
blocked, just confirm the real ticket by Aug 12.
