# Sorour — Week 2 (Aug 15–21): make the agents real

Replace the agent stubs in `agentorg/agents/` one at a time. Each is a thin
`Agent(create_model(), SYSTEM_PROMPT, tools=[...])` — the stub shows exactly
where the real call goes (`# TODO(Sorour, wk2)`).

Hard deadline this week: **by end of Friday Aug 21 the poisoned ticket blocks
every single time on real scanners + real agents.**

---

## Mon–Tue Aug 15–16 — planner + developer

**Task: planner agent.**
Real Strands agent producing `PlanResult` from a ticket's text.
```python
# agentorg/agents/planner.py
agent = Agent(model=create_model(), system_prompt=PLANNER_PROMPT)
```
**Done when:** `python -m agentorg.graph` produces a real plan (not the
fixture) with sane, non-empty `tasks`.

**Task: developer agent.**
Real Strands agent producing `DevResult` (a diff) from the plan.
**Done when:** the same run produces a real, non-fixture diff and still ends
`promoted` on the clean ticket.

---

## Wed Aug 17 — reviewer + revision loop

**Task: reviewer agent.**
Wire `changes_requested` back to the developer, capped by
`MAX_REVISION_LOOPS` (loop already lives in `graph.py`).
**Done when:** a deliberately weak diff triggers exactly one revision, then
the reviewer approves.

---

## Thu Aug 18 — security agent

**Task: wire the security agent.**
Call Habiba's `run_all_scanners(dev)`, apply `compute_security_verdict()`
(already in `state.py`), let the LLM write only the human-readable
`explanation` field — never the verdict itself. The verdict is pure code, on
purpose — that's the demo's deterministic guarantee.
**Done when:** the poisoned run blocks using **real findings**, not the stub.
**You're unblocked because:** until Habiba's scanners land, `security.run()`
falls back to the fixture — the graph never waits on her.

---

## Fri Aug 19–21 — human gates + the deadline

**Task: the three human gates.**
Real `pause()`/`resume()` via `gates.py`: save state at a gate, resume after
a `HumanDecision` is recorded.
**Done when:** a run stops at gate 1, you record a decision from the CLI, and
it continues to completion.

**★ Hard deadline: by end of Friday Aug 21, the poisoned ticket blocks every
single time on real scanners + real agents.**
```bash
for i in $(seq 1 10); do python -m agentorg.graph --poisoned; done
```
**Done when:** all 10 runs show `status=blocked`. If not, stop everything
else — pull in whoever's free — until it does.

---

## End of week 2 — done when

- Real planner, developer, reviewer, security agents all run (no fixtures on
  the happy path).
- The revision loop fires and terminates correctly.
- The poisoned ticket blocks **every** time on real scanners.
- The three human gates pause and resume correctly.
