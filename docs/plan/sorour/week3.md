# Sorour — Week 3 (Aug 22–27): polish, deploy, rehearse

Feature freeze **Tuesday Aug 25**. After that, only dry runs and fixing what
they surface. Target ready date **Aug 27**.

---

## Sat–Sun Aug 22–23 — log timeline UI

**Task: render the log as a timeline.**
`log.read(run_id)` → a timeline view (opened → proposed → reviewed →
blocked/passed → promoted). This is scored for UX — do not cut it.
**Done when:** a judge can watch a run's full history on one screen.

---

## Mon Aug 24 — approve/reject screen

**Task: buttons that call `gates.resume(...)`.**
Cut to CLI first if time is tight — the CLI already works from week 2.
**Done when:** a human approves/rejects a gate from the UI (or the CLI
fallback is confirmed good enough for the demo).

---

## Tue Aug 25 — AgentCore deploy + freeze

**Task: deploy to AgentCore, with Mariam.**
Build arm64 images → push to the ECR repos → create the AgentCore runtimes
via `agentcore launch` (Mariam drives the CLI, you own the IAM/ECR side —
see `docs/plan/mariam/week3.md`).
**Done when:** the graph runs against agents hosted on AgentCore, not local.

**Task: freeze at end of day.**
From here: only dry runs and fixing what they surface. No new features.

---

## Wed–Thu Aug 26–27 — dry run + ready

**Task: dry-run the full demo, twice, online and offline.**
Offline mode is Mariam's build, but you run it with her.
**Done when:** both runs behave identically; ready date **Aug 27** hit.

---

## End of week 3 — done when

- Log timeline UI shows a full run history.
- Approve/reject works (UI or confirmed CLI fallback).
- The graph runs against AgentCore-hosted agents, not local stubs.
- Two clean dry runs, online and offline, by Aug 27.

**Cut order if behind:** SRE agent first (keep it a stub), then the
approve/reject UI (use CLI). Never cut the security block or the log
timeline — the block **is** the demo, the timeline is the UX the judges score.
