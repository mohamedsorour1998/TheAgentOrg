# The Agent Org — 3-Week Plan (Overview)

**Team (5):** Mohamed Sorour (lead), Mariam, Habiba, Reem, Aya.
**Calendar:** Week 1 Aug 8–14 · Week 2 Aug 15–21 · Week 3 Aug 22–27.
**Assessment window opens Aug 23.** Target ready **Aug 27** — so email the
organizers now and ask for a slot in the **first week of September**. They pick
the date; asking costs nothing and buys a week of buffer.

---

## The one rule that makes this work

Everyone codes against **`agentorg/state.py`** (the frozen data contract) and
**`fixtures/`** (validated sample results) — never against each other's live
code. Each person owns their **own directory**, so on GitHub no two people edit
the same files. The stubbed pipeline already runs end-to-end today:

```bash
python -m agentorg.graph            # clean    -> promoted
python -m agentorg.graph --poisoned # poisoned -> blocked (2 findings)
pytest -q                           # 3 passed
```

You each swap your own stub for real code, task after task, and nobody waits on
anybody.

> You may **ADD** optional fields to the models. **Never rename or remove one** —
> a rename breaks all five lanes at once and nobody notices until integration.

---

## Who owns what

| Person | Lane | Directory |
|---|---|---|
| **Sorour** | AWS + the graph (the senior/hard work) | `infra/`, `agentorg/common/`, `graph.py`, `gates.py`, `log.py`, `agents/` |
| **Mariam** | Git/PR + CI + deploy (the integration seam) | `agentorg/github_ops.py`, `.github/workflows/` |
| **Habiba** | Security scanners | `agentorg/security/` |
| **Reem** | Target app + tickets | `target_repo/`, `tickets/` |
| **Aya** | Tests + metrics | `tests/` |

---

## The only cross-dependency to watch

```
Reem's poisoned ticket  ── due Wed Aug 12 ──▶  Habiba's scanners
```

Habiba needs a diff that actually trips gitleaks. Everything else is
parallel-safe because of fixtures. If Reem slips, Habiba keeps working against
the fixture `dev_result_poisoned.json` — but confirm the real ticket by Aug 12.

---

## Week-by-week shape

**Week 1 (Aug 8–14) — build the skeleton.**
Saturday Aug 8: 90-minute kickoff, everyone. Agree the contract (`state.py`) and
the log table, pick the poisoned flaw, nothing else. By end of week the pipeline
runs end-to-end on each person's own early code.

**Week 2 (Aug 15–21) — make it real.**
Real agents, real scanners, real PRs, the human gates. **End of Friday Aug 21 the
poisoned ticket must block every single time.** If it doesn't, stop everything
else and fix it — two people on it if needed.

**Week 3 (Aug 22–27) — polish and rehearse.**
Log timeline + approve/reject UI, SRE agent, offline demo, DORA metrics table,
backup video. **Feature freeze Tuesday Aug 25** — after that only bug fixes and
rehearsal.

---

## If you fall behind — cut in this order

1. SRE agent (keep it a stub that reads CI).
2. The approve/reject screen (use the command line instead).

**Never cut** the security block or the log timeline. The block **is** the demo;
the timeline is the UX the judges score.

---

Your detailed day-level plan is in your own file:
[`mohamed-sorour.md`](mohamed-sorour.md) ·
[`mariam.md`](mariam.md) ·
[`habiba.md`](habiba.md) ·
[`reem.md`](reem.md) ·
[`aya.md`](aya.md)
