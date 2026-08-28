# Lane L — evidence

Four judge requirements answered with **evidence rather than code**. Every number
in every document here came from a script in this repository, and the script is
named beside the number.

Baseline commit `9b2b1ee`, measured 2026-08-28. This is the **Phase 1** half of
Lane L: the baseline row, captured before Lanes A, B, C and E change the system it
describes. Phase 4 adds the cost comparison, the competitor matrix and the deck.

## The documents

| File | Requirement | What it answers |
|---|---|---|
| `scorecard.md` | §3 · evolution criteria | seven dimensions, a baseline row, a promotion rule with a no-regression veto, and **five recorded rejections** |
| `dependency-inventory.md` | §5 · external dependency | substitutable / seam-bound / load-bearing, with a named blast radius each |
| `sbom.md` | §5 · supply chain | CycloneDX SBOM, pinned versions, and the five-step scanner-update process |
| `limitations.md` | §13 · limitations | nine limitations, each **costed** — what removing it would take, and why not now |

## The data

| File | Produced by |
|---|---|
| `scorecard-baseline.json` | `scripts/measure_scorecard.py` |
| `dependency-inventory.json` | `scripts/measure_dependencies.py` |
| `sbom.json` (CycloneDX 1.5) | `scripts/measure_sbom.py` |

## Regenerating everything

```bash
.venv-main/bin/python scripts/measure_scorecard.py --runs 10 --require-real-scanners
.venv-main/bin/python scripts/measure_dependencies.py
.venv-main/bin/python scripts/measure_sbom.py
```

Each prints what it wrote and exits non-zero when it cannot measure what it
claims. `tests/test_evidence.py` pins the properties that make these numbers
trustworthy — 27 tests, including three that had to be rewritten because they
passed on their own mutation.

## Two rules these documents follow

**A number is a value plus its conditions.** `CLAUDE.md` records 116.88 s →
149.68 s → 102.83 s for one unchanged test snapshot. So every timing here is a
range with the regime that produced it, never a point value.

**An honest gap beats an invented number.** Three of the seven scorecard
dimensions cannot be measured at this commit. Each is marked `measured: false`
with the reason and the command a human would run — because a gap invites the
measurement and a number ends it.

## Three findings that belong to other lanes

Recorded here because Lane L found them and cannot fix them:

1. **The specification's own §5 reference counts do not reproduce.** Three of four.
   It records no command for them. See `dependency-inventory.md` §6.
2. **`terraform.yml:213` and `CLAUDE.md:1876` disagree about whether the exposed
   dispatch token has been rotated.** See `limitations.md` §9. Until resolved,
   treat it as compromised — that is the safe reading of a disagreement.
3. **`test_the_stage_records_the_trigger_onto_the_run_state` fails in any git
   worktree**, for an environment reason with a diagnosed cause. See
   `scorecard.md` §6. It will be misread as a lane breakage otherwise.
