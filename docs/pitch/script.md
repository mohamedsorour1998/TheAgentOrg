# Presentation script — pre-final evaluation

**Tue 25 Aug 2026, 3:00–3:30 PM · Microsoft Teams · recorded, cameras on**
Deck: `TheAgentOrg-prefinal.pptx` — 16 slides. Regenerate with
`.venv-main/bin/python scripts/make_deck.py`.

## The clock

| | minutes |
|---|---|
| Slides 1–3 — title, agenda, the team | 0:00 → 1:30 |
| Slides 4–7 — the problem, and what we built | 1:30 → 6:00 |
| Slides 8–10 — architecture (8 is the diagram) | 6:00 → 8:40 |
| Slides 11–13 — progress, and what it is worth | 8:40 → 11:00 |
| Slide 14 — roadmap · slide 15 — hand over | 11:00 → 12:00 |
| **Live demo** — clean, then poisoned (`docs/demo-runbook.md`) | 12:00 → 20:00 |
| Questions, slide 16 on screen | 20:00 → 30:00 |

**Why 12 minutes of slides and not 20.** The demo is ~8 minutes measured. A 20-minute
deck plus that demo is 28 of a 30-minute slot — before Teams join, screen share, or one
question, in a session where *"delays cannot be accommodated."* If a judge raises it:
*"We budgeted twelve minutes of slides so the live demonstration and your questions both
fit inside the thirty."*

**It is a team presentation.** No slide is headed with one person's name — the work is
presented as the team's. Split the slides between you however you rehearse best; a
natural division is on the section boundaries in the table above. Whoever is not
speaking stays on camera and unmuted-ready for questions.

**Animations are click-advanced.** Fourteen of sixteen slides reveal their lines one click
at a time. The pauses are the pacing — do not rush the clicker.

---

## Slide 1 — Title · 15s

**SAY:** "Good afternoon. We're RosettaTeam, and this is The Agent Org — AI agents that
ship code the way an engineering team does, with a safety check they cannot argue with."

Do not read the subtitle aloud; it is on screen. Go straight to slide 2.

---

## Slide 2 — Agenda · 20s

**SAY:** "Twenty minutes, five parts. Who we are, the problem and what we built,
the architecture, where we are today, and the roadmap — then we'll show you the thing
running."

Do not read the five lines out. The slide does that; you are only signposting.

---

## Slide 3 — Meet the team · 45s

**SHOW:** five photographs, names, roles.

**SAY:** "Quickly, who built this. I'm Mohamed Sorour, senior DevOps at Vezeeta.
Mariam is an associate solution engineer at RENOSYSTEMS. Habiba is a junior DevOps
engineer, and Reem and Aya are junior testing engineers.

*(click)* Three of us trained together at Digilians — this is the first thing we've built
as one team."

Let each person nod or wave on camera as you name them. It is 45 seconds that makes the
rest of the pitch land as a team's work rather than a slide deck's.

---

## Slide 4 — The problem · 60s

**SHOW:** three lines, then `10 of 10` in coral.

**SAY:** "AI can already write working code faster than any team can review it. That's
the real bottleneck — and the temptation is to trust the agent and merge.

*(click)* So the first thing we built was the unchecked version. Deliberately. An agent
pipeline with no review, no scanning, no human approval. Then we gave it a ticket whose
reference implementation hardcodes cloud credentials.

*(click)* It merged them. Ten times out of ten."

**IF ASKED — "is that a real credential?"** — It's the placeholder from Amazon's own
public documentation. Nothing sensitive is anywhere in our repository. It's a
real-*shaped* key, which is what makes the scanners genuinely detect it.

---

## Slide 5 — And nobody noticed · 45s

**This is the emotional beat of the pitch. Let the first line sit for a moment.**

**SAY:** "And nobody noticed.

*(click)* Every job passed. Every dashboard was green.

*(click)* The credential reached the main branch, and the pipeline reported success.

*(click)* Because a check that never ran looks exactly like a check that passed. That's
the problem we set out to solve — not 'can an AI write code', but 'how do you know
anything actually checked it'."

---

## Slide 6 — The solution · 75s

**SHOW:** the five-stage strip, then three lines.

**SAY:** "So we gave the agents an organisation. A ticket walks the same path it would in
a real team — it gets planned, built, reviewed, scanned, and released.

*(click)* Five specialist agents do that work. They plan, write, critique, scan and sign
off.

*(click)* Three times, the run stops and waits for a named human to approve it.

*(click)* And the decision about whether a change is safe to ship is ordinary
arithmetic — not judgement."

---

## Slide 7 — The safety check · 75s

**The most important slide. Slow down.**

**SAY:** "That last point is the one I'd like to dwell on, because it's what makes this
trustworthy rather than just fast.

*(click)* Real scanners read the change and report what they find — hardcoded
credentials, known vulnerabilities, dangerous patterns.

*(click)* Then a fixed severity threshold decides. At or above it, the change stops.

*(click)* That decision is arithmetic. There is nothing in it to convince.

*(click)* We tested that directly. We tried talking it out of a block — the reply
insisted the change was safe and the scanners were wrong. The change stayed blocked,
because those words were never part of the decision."

**IF ASKED — "so where is the AI actually useful?"** — Everywhere judgement helps and
nothing is at stake: breaking the ticket down, writing the change, catching a logic
problem a scanner can't see, explaining a finding in language a human can act on.

---

## Slide 8 — What runs where · 70s

**SHOW:** the diagram. Three dashed zones, left to right: GitHub, AWS, the five agents.

**SAY:** "This is the whole system on one slide, and the thing I want you to take from it
is the boundary.

*(click)* On the left, two GitHub repositories. `auth-service` is the target — that's
where somebody opens a ticket, and it isn't our code. `TheAgentOrg` is the pipeline.

*(click)* In the middle, what AWS owns. A Lambda verifies the webhook signature before
anything else runs. EventBridge routes it, with a dead-letter queue so a failed dispatch
is recorded rather than lost. And Bedrock is the model the agents call.

*(click)* On the right, five agents — each in its own isolated runtime, all built from one
image and differing only by which role they're told to play.

And in amber, on the GitHub side: the three approval gates. They're deliberately drawn
*there* rather than inside our pipeline, because they're GitHub's own mechanism. They are
not ours to bypass."

**IF ASKED — "why is the gate on the GitHub side?"** — Because that's literally where it
is enforced. If it were a step in our own code, we could skip it; being a platform
approval means we cannot.

**IF ASKED — "what talks to what?"** — Three hops, all on the slide: the webhook from the
target repo to the Lambda, the dispatch from AWS back to the pipeline, and the invoke from
the pipeline out to the agents.

---

## Slide 9 — Architecture · 80s

**SHOW:** five numbered steps, then the infrastructure line.

**SAY:** "Briefly, how it runs — and the important thing is that it's entirely
cloud-native. There's no laptop anywhere in this path.

*(click)* Someone opens a ticket. That's the only trigger.

*(click)* The request is cryptographically verified before anything runs — nothing that
costs money happens before that check passes.

*(click)* It's routed onto an event bus, with a dead-letter queue so a failed dispatch is
recorded rather than lost.

*(click)* Five agents run in five isolated cloud runtimes, all built from one image.

*(click)* And three times, the pipeline pauses for a human.

Twenty infrastructure resources, all defined as code. And no long-lived cloud credentials
anywhere — every step authenticates for the moment it needs."

---

## Slide 10 — The humans are not a formality · 45s

**SAY:** "One thing worth being precise about, because it's easy to claim a human is in
the loop when they aren't.

*(click)* Each approval is enforced by the platform, not by a step in our own code that
could be skipped.

*(click)* Until someone clicks, the next stage doesn't exist — it's never even queued.

*(click)* And refusing is recorded on the ticket: who refused, and when.

*(click)* The same is true when the safety check stops a change. The stages after it are
never created. There's no branch to take and no flag to flip — the refusal is
structural."

---

## Slide 11 — Progress · 60s

**SAY:** "Where we are today. The full pipeline runs in the cloud on every ticket — not
on a developer's machine.

*(click)* Three real scanners run inside the agents' own environment.

*(click)* Both outcomes — shipped and refused — were verified this week against the live
system.

*(click)* And every run leaves a record on the ticket it came from, readable without
opening a build log.

*(click)* Eleven hundred automated tests behind it, and a clean ticket goes from opened
to merged in about five minutes."

---

## Slide 12 — The same request, twice · 60s

**SHOW:** SHIPPED in sage, REFUSED in coral, side by side.

**SAY:** "This is the comparison the whole project rests on. Two tickets. Same feature
request, word for word. One of them just carries a hardcoded credential.

*(click)* The clean one was planned, written, reviewed, scanned, approved three times,
and merged — and the ticket closed itself.

*(click)* The other was stopped. Two credentials found in the change, refused before
anyone could approve it, and the ticket says why.

*(click)* About three minutes to refuse. Nothing merged. Same agents, same gates — the
only difference is the change itself."

---

## Slide 13 — What this is worth · 45s

**SAY:** "Why this matters beyond the demo.

*(click)* Review capacity stops being the limit on how fast a team can ship.

*(click)* The checks that matter run on every single change, not when somebody remembers.

*(click)* Every refusal is auditable — what was found, who approved, when.

*(click)* And it bolts onto what a team already uses. Issues, branches, approvals. Nobody
has to adopt a new tool to get this."

---

## Slide 14 — Roadmap · 60s

**SAY:** "Four things between here and the final phase.

*(click)* Broaden the checks — dependency and licence scanning, and per-project
thresholds so a team can set its own bar.

*(click)* Harden the record — durable run history, and a timeline a reviewer can read
without opening a build log.

*(click)* Scale out — many repositories at once, and a queue so runs never contend.

*(click)* And prove it at volume: ten times the sample behind those numbers.

*(click)* Every one of those is a gap we've already written down. We'd rather show you a
known limitation than discover one on stage."

---

## Slide 15 — Hand over · 20s

**SAY:** "So — two tickets, the same feature request. One ships itself, one is refused.
Let us show you."

**Switch to the browser and follow `docs/demo-runbook.md`.**

---

## Slide 16 — Close

Return to this slide after the demo, before questions.

**SAY:** "Agents did the work. Humans stayed in control of what shipped. And the change
that should not have shipped, did not. Thank you — we're happy to take questions."

---

## Before you present

- [ ] `.venv-main/bin/python scripts/preflight.py` → `preflight OK.` (~16s)
- [ ] Deck open in PowerPoint, first slide up, presenter view checked
- [ ] Logged in as the gate reviewer on GitHub
- [ ] Two browser tabs: the target repo's Issues, and the pipeline's Actions
- [ ] Backup video ready **on Aug 24**, not the 25th — insurance recorded the morning of
      the event is not insurance
- [ ] Rehearsed once against a clock: twelve minutes for slides 1–15
