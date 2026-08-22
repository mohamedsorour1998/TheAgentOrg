# Pre-final presentation — what is in here

**Tue 25 Aug 2026, 3:00–3:30 PM · Microsoft Teams · recorded, cameras on**

| file | what it is |
|---|---|
| `TheAgentOrg-prefinal.pptx` | the deck — 13 slides, 16:9, transitions and click-advanced animations |
| `script.md` | the rehearsal script: per slide, what is shown, the words to say, and the follow-up answers |
| `../demo-runbook.md` | the live demo, step by step — the operator handout |
| `../handout-<name>.md` | one per engineer: their lane, their numbers, their questions |

## Present from the .pptx

Open in PowerPoint. Eleven of the thirteen slides advance their lines **one click at a
time** — the pauses are the pacing, so do not rush the clicker.

**It is a pitch, and it is the team's.** No slide is headed with one person's name, there
is no source code on any slide, and the four sections the organiser asked for are each
covered — the generator fails the build if one goes missing.

## Regenerate it

The deck is generated, not hand-built, so a typo is a one-line fix:

```bash
.venv-main/bin/python scripts/make_deck.py
```

That prints a self-check and exits non-zero if the motion did not reach the file:

```
docs/pitch/TheAgentOrg-prefinal.pptx  (53 KB)
  slides:      13
  animated:    11
  layout:      clean
  sections:    all four covered
  transitions: all
  OK — motion, content rules and layout verified in the saved file
  file(1):     Microsoft OOXML
```

**Why those checks exist.** `python-pptx` has no transition or animation API — verified,
`dir(slide)` exposes neither. Both are injected as raw XML, and python-pptx will never
complain if they go missing because it never knew about them. A deck that silently lost
its motion is byte-different but looks identical until it is presented, so the archive is
read back and the elements are counted.

The same logic covers the content rules. `layout` estimates each box's WRAPPED height and
flags collisions — a width-only check reported clean while six boxes overlapped, because
with word-wrap on a long line does not overflow sideways, it wraps and grows downward.
`sections` asserts the organiser's four required topics are each on a slide. And a banned
phrase list fails the build if cut copy — or any source code — comes back.

**Every number on a slide** is a constant at the top of `scripts/make_deck.py`, each
annotated with the command that produced it. Re-run those before Tuesday — the test count
in particular moves whenever anyone adds a test.

## The clock

| | minutes |
|---|---|
| Slides 1–5 — problem, and what we built | 0:00 → 5:00 |
| Slides 6–7 — architecture | 5:00 → 7:30 |
| Slides 8–10 — progress, and what it is worth | 7:30 → 10:30 |
| Slide 11 — roadmap · slide 12 — hand over | 10:30 → 12:00 |
| Live demo, both paths | 12:00 → 20:00 |
| Questions, slide 13 on screen | 20:00 → 30:00 |

Twelve minutes of slides, not twenty: the demo is ~8 minutes measured, and 20 + 8 is 28 of
a 30-minute slot before Teams join, screen share, or a single question. If asked, say so
plainly — it is a deliberate budget, not a shortfall.

## Two things to do before the day

- **Record the backup video on Aug 24**, not the 25th. `docs/plan/aya/week3.md` currently
  schedules it for the morning of the session; insurance recorded hours before the event
  is not insurance.
- **Rehearse once against a clock.** Twelve minutes is the one number here that cannot be
  measured from a command.
