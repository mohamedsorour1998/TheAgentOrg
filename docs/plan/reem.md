# Plan — Reem

**Your lane:** the target app the agents modify, and the two tickets.
`target_repo/` and `tickets/`.

You do **not** need Strands, AWS, or the pipeline internals. This is plain Python
plus two short ticket files. Your work is small but it sets up the entire demo —
the poisoned ticket is what everything else exists to catch.

---

## Week 1 — Aug 8 to 14: build the target and the tickets

Starter versions already exist (`target_repo/app/auth.py`,
`tickets/clean.md`, `tickets/poisoned.md`). Your job is to finish them and make
them realistic.

- [ ] **Finish the target app.** Keep `app/auth.py` tiny but real — a Flask login
  handler with a couple of functions worth changing.
  *Done when:* `python -m pytest target_repo/tests` passes.

- [ ] **Write the clean ticket** (`tickets/clean.md`): "add a per-IP login rate
  limit." Clear description + acceptance criteria. (Draft is there — refine it.)
  *Done when:* a developer could implement it from the ticket alone.

- [ ] **Write the poisoned ticket** (`tickets/poisoned.md`): same feature, but the
  attached reference hardcodes an AWS key
  (`AKIAIOSFODNN7EXAMPLE` — AWS's public placeholder, nothing sensitive).
  *Done when:* the poisoned reference diff, scanned on its own, trips gitleaks.

- [ ] **★ Hand the poisoned ticket to Habiba by Wed Aug 12.** She needs a diff
  that actually trips her scanner. This is the team's single cross-dependency —
  don't let it slip.
  *Done when:* Habiba confirms gitleaks flags the AWS key on your ticket.

*End of week 1:* both tickets exist; the poisoned one is confirmed to trip a
scanner.

---

## Week 2 — Aug 15 to 21: make the app CI-ready

- [ ] **Add a real test or two** in `target_repo/tests/` so CI (Mariam's) has
  something meaningful to run.
  *Done when:* the tests pass locally and in Mariam's CI workflow.

- [ ] **Sanity-check both tickets through the pipeline** with Sorour: clean →
  promoted, poisoned → blocked.
  *Done when:* `python -m agentorg.graph` and `--poisoned` behave as expected on
  your app.

- [ ] **Start the demo script** (the spoken walkthrough): what you click, what
  you say, in what order. You'll rehearse it in week 3.
  *Done when:* a first draft of the script exists.

*End of week 2:* the app is CI-ready and both tickets are proven through the
pipeline.

---

## Week 3 — Aug 22 to 27: the demo script + rehearsal

- [ ] **Finish the demo script.** Tight, 5–7 minutes: clean run passes, poisoned
  run blocks, show the timeline.
  *Done when:* the script is written and reviewed by Sorour.

- [ ] **Rehearse it with the team, twice.** Time it. Note every rough spot.
  *Done when:* two clean run-throughs, under time.

- [ ] **After freeze (Tue Aug 25):** only rehearsal and small wording fixes.

---

## How you stay unblocked

Nothing you do waits on anyone. You write plain Python and two markdown tickets.
The only handoff is **outbound**: your poisoned ticket → Habiba by Aug 12. Get
that done early and you're free the rest of the sprint to own the demo script.
