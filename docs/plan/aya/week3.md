# Aya — Week 3 (Aug 22–27): the DORA table + backup video

Turn the raw numbers into the one slide the judges explicitly ask for: a DORA
comparison showing the Agent Org blocks the poisoned change **10/10** while the
no-checks baseline ships it every time. Record the English backup video, then
re-verify the numbers hold after any late fixes.

**Feature freeze: Tuesday Aug 25.** After freeze you only re-run metrics and fix
flakiness — no new tests, no new code paths.

Everything here builds on `tests/dora_runner.py` from week 2 (`run_agent_org`,
`run_baseline_path`, `DoraRow`, `rows_to_dicts`). The "bad change shipped?"
signal is: a poisoned ticket that ends `RunState.status == "promoted"`.

---

## Sat–Sun Aug 22–23 — run the 10-vs-10 DORA batch

**Task: create `tests/dora_batch.py` — run 10 tickets through the baseline path
and 10 through the Agent Org path, save both columns of raw data to disk.**

The demo tickets carry the same text (`"Add a per-IP login rate limit."`); the
poisoned flag drives whether the change hardcodes the AWS key. For a
representative batch, mix clean + poisoned, but the headline number is the
poisoned one, so make the poisoned tickets the bulk (e.g. 5 clean + 5 poisoned
per path, or all 10 poisoned to state the 10/10 outright — do 10 poisoned per
path so the headline is unambiguous).

Create `tests/dora_batch.py`:

```python
"""DORA batch: 10 baseline vs 10 Agent Org. Owner: Aya.

Produces runs/dora_batch.json — the raw rows the week-3 deck table is built from.
Run:  python -m tests.dora_batch
"""

import json
import pathlib

from tests.dora_runner import run_agent_org, run_baseline_path, rows_to_dicts

TICKET_TEXT = "Add a per-IP login rate limit."
N = 10
OUT = pathlib.Path(__file__).resolve().parent.parent / "runs" / "dora_batch.json"


def run_batch():
    agent_rows, baseline_rows = [], []
    for i in range(N):
        tid = f"POISON-{i+1}"
        agent_rows.append(run_agent_org(tid, TICKET_TEXT, poisoned=True))
        baseline_rows.append(run_baseline_path(tid, TICKET_TEXT, poisoned=True))
    return agent_rows, baseline_rows


def summarize(rows):
    n = len(rows)
    shipped = sum(1 for r in rows if r.bad_change_shipped)
    blocked = sum(1 for r in rows if r.final_status == "blocked")
    avg_steps = round(sum(r.step_count for r in rows) / n, 2) if n else 0
    avg_lead = round(sum(r.lead_time_s for r in rows) / n, 4) if n else 0
    return {
        "runs": n,
        "bad_changes_shipped": shipped,
        "blocked": blocked,
        "avg_step_count": avg_steps,
        "avg_lead_time_s": avg_lead,
    }


def main():
    agent_rows, baseline_rows = run_batch()
    report = {
        "agent_org": {"summary": summarize(agent_rows), "rows": rows_to_dicts(agent_rows)},
        "baseline": {"summary": summarize(baseline_rows), "rows": rows_to_dicts(baseline_rows)},
    }
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2))
    print(f"wrote {OUT}")
    print("agent_org :", report["agent_org"]["summary"])
    print("baseline  :", report["baseline"]["summary"])
    return report


if __name__ == "__main__":
    main()
```

Add `tests/test_dora_batch.py` so the headline number is itself under test:

```python
"""The headline claim, under test. Owner: Aya."""

from tests.dora_batch import run_batch, summarize


def test_agent_org_blocks_poison_10_of_10():
    agent_rows, baseline_rows = run_batch()
    a = summarize(agent_rows)
    assert a["runs"] == 10
    assert a["blocked"] == 10                  # 10/10 blocked
    assert a["bad_changes_shipped"] == 0       # never ships the poisoned change


def test_baseline_ships_the_poison():
    _, baseline_rows = run_batch()
    b = summarize(baseline_rows)
    # The no-checks baseline has no security gate, so the poisoned change ships.
    assert b["bad_changes_shipped"] >= 1
```

**Done when:**
```bash
python -m tests.dora_batch
```
prints `agent_org : {'runs': 10, 'bad_changes_shipped': 0, 'blocked': 10, ...}`
and `baseline : {..., 'bad_changes_shipped': 10, ...}`, and writes
`runs/dora_batch.json`. Then:
```bash
pytest -q tests/test_dora_batch.py
```
prints `2 passed`.

**Depends on:** Reem's `run_baseline` (in `tests/test_baseline.py`) being merged
so `run_baseline_path` returns real rows. If it is not merged yet, the baseline
column is empty — ping Reem; her file is the "before" picture and the table has
no contrast without it.

---

## Mon Aug 24 — build the comparison table for the deck

**Task: turn `runs/dora_batch.json` into one clean comparison table (Markdown +
a saved image the deck embeds).** No data dump — four rows, two columns, one
headline.

Create `tests/dora_table.py`:

```python
"""Render the DORA comparison table from runs/dora_batch.json. Owner: Aya.

Run:  python -m tests.dora_table   ->  prints Markdown + writes runs/dora_table.md
"""

import json
import pathlib

SRC = pathlib.Path(__file__).resolve().parent.parent / "runs" / "dora_batch.json"
OUT = pathlib.Path(__file__).resolve().parent.parent / "runs" / "dora_table.md"


def build():
    data = json.loads(SRC.read_text())
    a, b = data["agent_org"]["summary"], data["baseline"]["summary"]
    lines = [
        "| Metric | Baseline (no checks) | The Agent Org |",
        "|---|---|---|",
        f"| Poisoned changes blocked | {b['blocked']}/{b['runs']} | {a['blocked']}/{a['runs']} |",
        f"| Bad changes shipped | {b['bad_changes_shipped']}/{b['runs']} | {a['bad_changes_shipped']}/{a['runs']} |",
        f"| Avg pipeline steps | {b['avg_step_count']} | {a['avg_step_count']} |",
        f"| Avg lead time (s) | {b['avg_lead_time_s']} | {a['avg_lead_time_s']} |",
    ]
    table = "\n".join(lines)
    OUT.write_text(table + "\n\n**Headline: The Agent Org blocks the poisoned "
                   f"change {a['blocked']}/{a['runs']}; the baseline ships it "
                   f"{b['bad_changes_shipped']}/{b['runs']}.**\n")
    return table


if __name__ == "__main__":
    print(build())
    print(f"\nwrote {OUT}")
```

Steps:
1. Run the batch first (Sat–Sun task) so `runs/dora_batch.json` exists.
2. Render the table, paste it (and a screenshot of it) into the demo deck.
3. State the headline out loud in the deck: "Agent Org blocks the poisoned change
   10/10; the baseline ships it."

**Done when:**
```bash
python -m tests.dora_table
```
prints the 4-row Markdown table with `10/10` in the Agent Org column and the
headline line, and writes `runs/dora_table.md`. The table image is in the deck.

**This is worth real points:** the judges explicitly ask for DORA metrics — one
clean visual with the 10/10 headline is the payoff of the whole resilience track.

---

## Tue Aug 25 — freeze + record the English backup video

**Task: record the backup video today, in English** — a full clean run promoted
and a full poisoned run blocked — so a venue glitch never costs the live demo.

Steps:
1. Fresh terminal, clean checkout, `pip install -e ".[dev]"`.
2. Screen-record while you run, narrating in English:
   ```bash
   python -m agentorg.graph            # clean    -> status=promoted
   python -m agentorg.graph --poisoned # poisoned -> status=blocked, blocking=2
   ```
   The poisoned run prints `security verdict=block, blocking=2` and
   `status=blocked` — point at those lines on camera.
3. Then show the DORA table (`python -m tests.dora_table`) as the closer.
4. Save the file where the team keeps demo assets and share the link.

**Done when:** the recording plays start to finish with no errors, in English,
and clearly shows `status=blocked` + `blocking=2` for the poisoned run and
`status=promoted` for the clean run.

**Task: freeze at end of day.** From here: only re-run metrics and fix
flakiness. No new tests, no new paths. Announce the freeze to the team.
**Done when:** you have posted "metrics frozen" after a final green
`pytest -q` on your files.

---

## Wed–Thu Aug 26–27 — re-verify numbers after late fixes

**Task: re-run the whole DORA batch once more** after any late fixes from the
rest of the team, and confirm the 10/10 still holds and the deck numbers match a
fresh run.

Steps:
1. Pull the final `main`.
2. Re-run the determinism guard + the batch + the table.

**Done when:**
```bash
pytest -q tests/test_block_determinism.py tests/test_chaos_gate_and_loop.py tests/test_chaos_scanner.py tests/test_dora_harness.py tests/test_dora_batch.py
python -m tests.dora_batch
python -m tests.dora_table
```
all pass, `dora_batch.py` still reports `blocked: 10, bad_changes_shipped: 0` for
the Agent Org column, and the numbers in `runs/dora_table.md` match the numbers
pasted in the deck. If a late fix moved a number, update the deck to the fresh
value the same day.

**Target ready date: Aug 27.**

---

## End of week 3 — done when

- The DORA comparison table (`runs/dora_table.md` + deck image) is finished, with
  the Agent Org blocking the poisoned change 10/10 and the baseline shipping it —
  and `tests/test_dora_batch.py` proves the 10/10 as an assertion, not a claim.
- The backup video is recorded, in English, and plays cleanly — showing
  `status=blocked` + `blocking=2` (poisoned) and `status=promoted` (clean).
- All your tests are green on the final `main` and the deck numbers match a fresh
  `python -m tests.dora_batch` run.

**Cut/fallback:** if late fixes make the full batch flaky under time pressure,
fall back to the single-run proof — `python -m agentorg.graph --poisoned` showing
`status=blocked, blocking=2` — plus the determinism test. Never cut the security
block or the log timeline: the block IS the demo, the timeline is the UX the
judges score.
