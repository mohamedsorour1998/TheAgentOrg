# Reem — Week 3 (Aug 22–27): the demo script + rehearsals

You own the spoken walkthrough — you know the tickets best. Write a tight
**5–7 minute** demo script in **English** (the judges are international),
rehearse it twice with the team, and lock it at feature freeze **Tuesday Aug 25**.
After freeze: only wording polish, no new behavior. Target ready date **Aug 27**.
**No AWS.**

The two things the demo must show, both already runnable end to end:
```bash
python -m agentorg.graph            # clean    -> status=promoted, security verdict=pass
python -m agentorg.graph --poisoned # poisoned -> status=blocked, security verdict=block, blocking=2
```
And the log timeline (Sorour's week-3 build) at `runs/<run_id>.jsonl`, one
`LogEvent` per stage, that the judges score as the UX.

---

## Sat–Sun Aug 22–23 — write the demo script

**Task: write the demo script as `docs/plan/reem/demo_script.md`.**
Structure it as a beat sheet: each beat has the spoken line (English), the exact
command you run on screen, and the expected on-screen result — so anyone on the
team can drive it. Keep the whole thing to 5–7 minutes. Write the file:

````markdown
# The Agent Org — Live Demo Script (5–7 min, English)

Owner: Reem. Freeze: Tue Aug 25. Everything below runs offline on stubs + real
agents; no live AWS call is required during the talk.

## Beat 0 — Setup (before the timer, ~0:00)
Terminal open in the repo root, font large, `runs/` empty. One-liner check:
> "The Agent Org is a multi-agent CI/CD pipeline. A code ticket walks through
> five AI agents — planner, developer, reviewer, security, SRE — and three human
> gates. The headline is a security block that fires deterministically, in code,
> not by the model's mood."

## Beat 1 — The two tickets (~0:30)
Show `tickets/clean.md` and `tickets/poisoned.md` side by side.
> "Same feature in both tickets: add a per-IP login rate limit. The only
> difference: the poisoned ticket's reference implementation hardcodes an AWS
> key — `AKIAIOSFODNN7EXAMPLE`, AWS's own public placeholder."
Command:
```bash
grep AKIAIOSFODNN7EXAMPLE tickets/poisoned.md
```
Expected: the line with the hardcoded key prints.

## Beat 2 — The clean run passes (~1:30)
> "First the clean ticket. Plan, develop, review, security scan, SRE, three
> gates — all the way to promoted."
Command:
```bash
python -m agentorg.graph
```
Expected tail:
```
status=promoted
security verdict=pass, blocking=0
```
> "Clean change, nothing at or above the block threshold, promoted."

## Beat 3 — The poisoned run blocks — every time (~3:00)
> "Now the poisoned ticket. The developer follows the poisoned reference, so the
> diff carries the hardcoded key. Watch the security stage."
Command:
```bash
python -m agentorg.graph --poisoned
```
Expected tail:
```
status=blocked
security verdict=block, blocking=2
```
> "Two critical findings — the access key and the secret key. And this is the
> important part: the verdict is not the LLM's opinion. Gitleaks produces
> findings; a pure-Python rule, `compute_security_verdict` in state.py, blocks on
> anything at or above `high`. So it fires on every single run, not by luck."

## Beat 4 — Before vs after (~4:00)
> "Why does this matter? Here's the same poisoned change with the checks removed
> — a plain plan-develop-merge baseline."
Command:
```bash
pytest -q tests/test_baseline.py::test_baseline_ships_the_poisoned_change
```
Expected: `1 passed`.
> "Without the Agent Org, that change ships with the secret in it. With it, it's
> blocked 10 out of 10." (Point to Aya's DORA table on the slide.)

## Beat 5 — The timeline (the UX) (~5:00)
> "Every step is logged, append-only, one row per stage."
Command (use the run_id printed by Beat 3):
```bash
cat runs/<run_id>.jsonl
```
Expected: JSONL LogEvents ending with actor `security`, stage `security`, action
`blocked`.
> "planner proposed, developer proposed, reviewer reviewed, security blocked —
> the pipeline halts, posts the block reason as a PR comment, and never reaches
> the deploy gates. That timeline is the audit trail."

## Beat 6 — Close (~6:00)
> "Five agents, three human gates, one deterministic block that a hardcoded
> secret can never sneak past. That's The Agent Org."

## Fallback if a live command misbehaves
Play Aya's recorded English backup video (locked Tue Aug 25) and narrate the same
six beats over it.
````

**Done when:** the script exists, reads in 5–7 minutes when spoken aloud (time
it), and every command in it actually produces the stated output:
```bash
python -m agentorg.graph            # status=promoted
python -m agentorg.graph --poisoned # status=blocked, blocking=2
pytest -q tests/test_baseline.py::test_baseline_ships_the_poisoned_change  # 1 passed
```
**You're unblocked because:** all three commands already run today on stubs, so
you can draft and time the script without waiting on real agents.
**Blocks / Hands off to:** Sorour reviews the script; Aya records the English
backup video Tue Aug 25 following these same six beats.

---

## Mon–Tue Aug 24–25 — first rehearsal + freeze

**Task: rehearse with the whole team once, timed.**
- Run the demo top to bottom with the person who will drive the terminal.
- Time it end to end; note every rough spot: dead air while a command runs, a
  beat that overruns, an unclear transition, any command whose output differs
  from the script.
- Fix the script wording and beat order from the notes.

**Done when:** one full run-through completes under 7 minutes with notes taken,
and every command still matches its stated output (re-run the three commands
above).

**Task: at feature freeze (Tue Aug 25, end of day), lock the script.**
Get Sorour's sign-off, then freeze `docs/plan/reem/demo_script.md`. From here
only wording polish — no new commands, no new behavior.
**Done when:** the script is marked frozen and Sorour has signed off; Aya's
English backup video is recorded against the frozen beats.

---

## Wed–Thu Aug 26–27 — second rehearsal + ready

**Task: rehearse again, twice if time allows, in English, under time.**
- Run it clean start to finish; if the first is clean and time remains, run once
  more.
- Confirm the fallback works: play Aya's backup video and narrate the six beats
  over it, so a live-command hiccup on the day is a non-event.

**Done when:** two clean run-throughs complete, each under 7 minutes, in English,
and the fallback video path is confirmed — ready for **Aug 27**.

---

## End of week 3 — done when

- `docs/plan/reem/demo_script.md` exists, is 5–7 minutes spoken, in English,
  reviewed by Sorour, and frozen Tue Aug 25.
- Every command in the script produces its stated output (`python -m
  agentorg.graph` → promoted; `--poisoned` → blocked/blocking=2; baseline test
  → `1 passed`).
- Two clean rehearsals completed with the whole team, and Aya's English backup
  video is confirmed as the fallback.

**Cut/fallback note:** if a live command misbehaves on the day, switch to Aya's
recorded English video and narrate the same six beats — never cut the poisoned
block or the timeline; the block IS the demo and the timeline is the UX the
judges score.
