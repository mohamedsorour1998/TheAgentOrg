# Lane L — evidence

Seven judge requirements answered with **evidence rather than code**. Every number in
every document here came from a script in this repository, and the script is named
beside the number.

**Two rows, two commits.** The baseline was captured at `9b2b1ee` before Phases 1–3
changed the system it describes; the second row is `d6165c8`, twelve lanes later. Both
are kept — a baseline is unrecoverable once the system has moved, so a superseded row is
evidence rather than clutter.

## The documents

| File | Requirement | What it answers |
|---|---|---|
| `scorecard.md` | §3 · evolution criteria | seven dimensions, **two** measured rows, a promotion rule with a no-regression veto, and **seven recorded rejections** — six from before this phase and one earned in it |
| `cost-comparison.md` | §4 · cost | three scenarios, and the arithmetic for the one that is deliberately not measured |
| `dependency-inventory.md` | §5 · external dependency | substitutable / seam-bound / load-bearing, with a named blast radius each, plus `web/`'s closure which the baseline had no row for |
| `sbom.md` | §5 · supply chain | CycloneDX SBOM, pinned versions, the deployed **digest**, and the five-step scanner-update process |
| `limitations.md` | §13 · limitations | **seventeen** limitations, each **costed** — what removing it would take, and why not now |
| `competitors.md` | §7 · competitive landscape | where each competitor is **better**, and the one row nobody else has |

## The data

| File | Produced by |
|---|---|
| `scorecard-baseline.json` | `scripts/measure_scorecard.py` |
| `cost-comparison.json` | `scripts/measure_cost.py` |
| `dependency-inventory.json` | `scripts/measure_dependencies.py` |
| `sbom.json` (CycloneDX 1.5) | `scripts/measure_sbom.py` |

## Regenerating everything

```bash
PYTHONPATH=. .venv-main/bin/python scripts/measure_scorecard.py --runs 10 --require-real-scanners
PYTHONPATH=. .venv-main/bin/python scripts/measure_dependencies.py
PYTHONPATH=. .venv-main/bin/python scripts/measure_sbom.py
DEMO_REPO=mohamedsorour1998/auth-service PYTHONPATH=. \
  .venv-main/bin/python scripts/measure_cost.py --runs 3 --require-model
```

`PYTHONPATH=.` is **not optional in a worktree**. Run as a script, `sys.path[0]` is
`scripts/`, so the worktree root never reaches `sys.path` and the editable install
resolves `agentorg` to the *shared* checkout — the run then executes another tree's
code. CLAUDE.md records this as `cf5cb83`, where three lanes each diagnosed it as their
own regression.

Each script prints what it wrote and **exits non-zero when it cannot measure what it
claims**. `measure_cost.py --require-model` and `measure_scorecard.py
--require-real-scanners` are the two that matter: without them a figure measured from a
fixture wears the same number as one measured from the real thing.

## Three rules these documents follow

**A number is a value plus its conditions.** CLAUDE.md records 116.88 s → 149.68 s →
102.83 s for one unchanged test snapshot. So every timing here is a range with the
regime that produced it, and the cost is a range too — its spread is 30% across three
consecutive runs of one unchanged ticket.

**An honest gap beats an invented number.** A gap invites the measurement; a number ends
it. Two scorecard dimensions and one whole column of the cost comparison are marked
unmeasured with the reason and the command that would close them.

**A rubric that has never rejected anything is decoration.** `scorecard.md` §4 and §7
record seven changes that were made and then found wrong by measurement rather than by
review. Six were caught by a deployed run or a deliberate mutation; none by the test
suite.

## Findings that belong to other lanes

Recorded here because Lane L found them and cannot fix them:

1. **Lane L's own Phase 1 evidence never reached `main`.** `git merge-base
   --is-ancestor 4172bd4 main` → NO. Eleven of twelve files sat on an unmerged branch;
   only `measure_dependencies.py` landed. That is CLAUDE.md's "a correct answer nobody
   asks for" arriving in documentation. Restored in `25ba200`.
2. **`tests/test_evidence.py` is NOT restored** — 708 lines, 27 tests, recoverable at
   `4172bd4:tests/test_evidence.py`. `tests/` is not this lane's directory in Phase 4,
   and it is the only thing pinning `measure_scorecard.py` and `measure_sbom.py`.
3. **`config.py`'s comment contradicts its own code, three lines apart.** *"The durable
   backend is the deployed default and is chosen deliberately, never inherited"* beside
   a measured `config.QUEUE_BACKEND == 'memory'`. See `limitations.md` §12.
4. **The four Selenium tests are not collected by `pytest -q` at all.**
   `pyproject.toml:77` is `testpaths = ["tests"]`, so `--collect-only | grep -ci
   selenium` returns 0. They behave correctly when run from `target_repo/`. See
   `limitations.md` §14.
5. **The `+x` bit trap fired again**, on `measure_scorecard.py` and `measure_sbom.py`.
   Both were committed `100644` with a shebang, so `ruff` printed `Found 2 errors` in
   every worktree and `All checks passed!` where an untracked `chmod` made it pass.
   Fixed with `git update-index --chmod=+x` — the index, not the disk.
6. **The dispatch token's rotation status is still contradicted** by two files at
   `d6165c8`. Nothing in the repository can settle it. See `limitations.md` §16.
