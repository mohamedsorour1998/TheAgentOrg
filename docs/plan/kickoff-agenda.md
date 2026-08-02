# Kickoff Agenda — Sat Aug 8, 90 minutes

**Attendees:** Sorour, Mariam, Habiba, Reem, Aya.
**Goal:** everyone leaves with a green local clone and agreement on the contract.

## Agenda

1. **(10 min) The idea + the demo.** One clean ticket ships, one poisoned
   ticket (hardcoded AWS key) blocks — every time, because the verdict is
   computed by code (`compute_security_verdict` in `agentorg/state.py`), not
   guessed by a model.

2. **(30 min) Walk `agentorg/state.py` field by field.**
   - `PlanResult` — planner output: `tasks: list[str]`
   - `DevResult` — developer output: `diff`, `files_changed`, `pr_url`
   - `ReviewResult` — reviewer output: `verdict` (`approve` / `changes_requested`), `comments`
   - `Finding` — one security finding: `severity`, `tool`, `description`
   - `SecurityResult` — `findings: list[Finding]`, `verdict` (`pass` / `block`), `blocking`
   - `SLOCheck`, `SREResult` — SRE gate output
   - `HumanDecision` — recorded at each of the 3 gates
   - `RunState` — the whole run: ticket, plan, dev, review, security, sre, status, gate decisions
   - `LogEvent` — one append-only log line
   - Show `compute_security_verdict(findings, threshold="high")` — pure
     Python, no LLM call, deterministic.

3. **(10 min) State the rule out loud.** You may **add** optional fields to
   any model in `state.py`. Never rename or remove one — a rename breaks all
   five lanes simultaneously. Get a verbal yes from each person.

4. **(10 min) Confirm the poisoned flaw.** Hardcoded AWS key
   (`AKIAIOSFODNN7EXAMPLE`, AWS's public example placeholder) is the flaw
   Reem's poisoned ticket carries and Habiba's gitleaks wrapper catches.

5. **(10 min) Confirm directory ownership**, pointing each person at their
   `docs/plan/<name>/README.md`:
   - Sorour: `infra/`, `agentorg/common/`, `graph.py`, `gates.py`, `log.py`, `agentorg/agents/`
   - Mariam: `agentorg/github_ops.py`, `.github/workflows/`
   - Habiba: `agentorg/security/`
   - Reem: `target_repo/`, `tickets/`, `tests/test_functional_*`, `test_baseline.py`
   - Aya: `tests/test_block_*`, `test_chaos_*`, `test_dora_*`

6. **(15 min) Live clone-and-run, together.** Everyone runs, on their own
   machine, in this order:
   ```bash
   git clone https://github.com/mohamedsorour1998/TheAgentOrg.git
   cd TheAgentOrg
   pip install -e ".[dev]"
   python make_fixtures.py
   pytest -q
   python -m agentorg.graph
   python -m agentorg.graph --poisoned
   ```
   Fix issues live — usually a Python version mismatch or a missing
   `pip install -e ".[dev]"`.

## Done when

- [ ] All 5 people have a local clone.
- [ ] `pytest -q` shows **3 passed** on every machine.
- [ ] `python -m agentorg.graph` prints `status=promoted`.
- [ ] `python -m agentorg.graph --poisoned` prints `status=blocked`,
      `security verdict=block, blocking=2`.
- [ ] Everyone has verbally agreed to the add-only rule and knows their
      directory.
