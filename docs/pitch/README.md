# Pre-final presentation — what is in here

**Tue 25 Aug 2026, 3:00–3:30 PM · Microsoft Teams · recorded, cameras on**

| file | what it is |
|---|---|
| `TheAgentOrg-prefinal.pptx` | the deck — 14 slides, 16:9, transitions and click-advanced animations |
| `script.md` | the rehearsal script: per slide, what is shown, the words to say, and the follow-up answers |
| `../demo-runbook.md` | the live demo, step by step — the operator handout |
| `../handout-<name>.md` | one per engineer: their lane, their numbers, their questions |

## Present from the .pptx

Open in PowerPoint. Twelve of the fourteen slides advance their bullets **one click at a
time** — the pauses are the pacing, so do not rush the clicker.

## Regenerate it

The deck is generated, not hand-built, so a typo is a one-line fix:

```bash
.venv-main/bin/python scripts/make_deck.py
```

That prints a self-check and exits non-zero if the motion did not reach the file:

```
docs/pitch/TheAgentOrg-prefinal.pptx  (55 KB)
  slides:     14
  animated:   12
  transitions: all
  OK — transitions and animations are present in the saved file
  file(1):    Microsoft OOXML
```

**Why that check exists.** `python-pptx` has no transition or animation API — verified,
`dir(slide)` exposes neither. Both are injected as raw XML, and python-pptx will never
complain if they are missing because it never knew about them. A deck that silently lost
its motion is byte-different but looks identical until it is presented, so the saved
archive is read back and the two elements are counted.

**Every number on a slide** is a constant at the top of `scripts/make_deck.py`, each
annotated with the command that produced it. Re-run those before Tuesday — the test count
in particular moves whenever anyone adds a test.

## The clock

| | who | minutes |
|---|---|---|
| Slides 1–6 | Sorour | 0:00 → 5:00 |
| Slides 7–10, 90s each | Habiba · Mariam · Reem · Aya | 5:00 → 11:00 |
| Slides 11–13 | Sorour | 11:00 → 12:30 |
| Live demo, both paths | Sorour driving | 12:30 → 20:30 |
| Questions | all | 20:30 → 30:00 |

Twelve minutes of slides, not twenty: the demo is ~8 minutes measured, and 20 + 8 is 28 of
a 30-minute slot before Teams join, screen share, or a single question. If asked, say so
plainly — it is a deliberate budget, not a shortfall.

## Two things to do before the day

- **Record the backup video on Aug 24**, not the 25th. `docs/plan/aya/week3.md` currently
  schedules it for the morning of the session; insurance recorded hours before the event
  is not insurance.
- **Rehearse once against a clock.** Twelve minutes is the one number here that cannot be
  measured from a command.
