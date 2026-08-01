# Plan — Mohamed Sorour (Lead)

**Your lane:** all of AWS + the graph. `infra/` (Terraform), `agentorg/common/`,
`agentorg/graph.py`, `agentorg/gates.py`, `agentorg/log.py`, `agentorg/agents/`,
and the AgentCore deploy (co-owned with Mariam).

You are the senior. You take the hard AWS work so the other four have clean,
self-contained lanes. Your job by week 3 shifts from writing features to making
the whole thing run.

---

## Week 1 — Aug 8 to 14: the skeleton (mostly done already)

The scaffold is built and green. Week 1 is about locking it and standing up AWS.

- [ ] **Run the Aug 8 kickoff (90 min, everyone).**
  Agree `state.py` and the log table, pick the poisoned flaw (hardcoded AWS
  key), assign directories. Nothing else.
  *Done when:* everyone has cloned the repo and run `pytest -q` green locally.

- [ ] **Freeze the contract.** Walk the team through `state.py` field by field.
  *Done when:* everyone agrees the shapes; the "add-only, never rename" rule is
  understood.

- [ ] **Stand up the AWS state backend.**
  `aws s3 mb s3://theagentorg-shared-terraform-backend` + enable versioning (see
  `infra/Terraform/environments/shared/backend.tf`).
  *Done when:* `cd infra/Terraform/environments/shared && terraform init` succeeds.

- [ ] **Apply the AgentCore infra.** `terraform apply` — creates the 5 ECR repos,
  the `bedrock-agentcore` runtime role, and the GitHub OIDC CI role.
  *Done when:* `terraform output` shows 5 ECR URLs, the runtime role ARN, and the
  `github-actions-role` ARN.
  *You're unblocked because:* this depends on nobody. Start day 1.

- [ ] **Prove Bedrock works.** One tiny script: `create_model()` + a one-line
  `Agent(...)("say hi")` against Nova.
  *Done when:* you get a real completion back from Bedrock in `us-east-1`.

*End of week 1:* pipeline runs end-to-end on stubs (already true) **and** AWS is
live for real agents next week.

---

## Week 2 — Aug 15 to 21: make the agents real

Replace the agent stubs in `agentorg/agents/` one at a time. Each is a thin
`Agent(create_model(), SYSTEM_PROMPT, tools=[...])` — the stub shows you exactly
where the real call goes (`# TODO(Sorour, wk2)`).

- [ ] **Planner + Developer agents.** Real Strands agents producing `PlanResult`
  / `DevResult`.
  *Done when:* `python -m agentorg.graph` produces a real plan and diff (not the
  fixture) and still ends `promoted`.

- [ ] **Reviewer agent + the revision loop.** Wire `changes_requested` back to the
  developer, capped by `MAX_REVISION_LOOPS` (loop already in `graph.py`).
  *Done when:* a deliberately weak diff triggers one revision, then approves.

- [ ] **Security agent wiring.** Call Habiba's `run_all_scanners`, apply
  `compute_security_verdict` (already in `state.py`), let the LLM write only the
  `explanation`.
  *Done when:* poisoned run blocks using **real findings**, not the stub.
  *You're unblocked because:* until Habiba's scanners land, `security.run()`
  falls back to the fixture — the graph never waits.

- [ ] **The three human gates.** Real `pause()`/`resume()` via `gates.py`: save
  state at a gate, resume after a decision.
  *Done when:* a run stops at gate1, you record a `HumanDecision` from the CLI,
  and it continues.

*End of Friday Aug 21:* the poisoned ticket blocks **every** time on real
scanners + real agents. If not, drop everything else.

---

## Week 3 — Aug 22 to 27: polish, deploy, rehearse

- [ ] **Log timeline UI.** Render `log.read(run_id)` as a timeline (opened →
  proposed → reviewed → blocked/passed → promoted). This is scored for UX — do
  not cut it.
  *Done when:* a judge can watch a run's full history on one screen.

- [ ] **Approve/reject screen.** Buttons that call `gates.resume(...)`. (Cut to
  CLI first if time is tight.)
  *Done when:* a human approves/rejects a gate from the UI.

- [ ] **Deploy to AgentCore with Mariam.** Build arm64 images → push to the ECR
  repos → create the AgentCore runtimes.
  *Done when:* the graph runs against agents hosted on AgentCore, not local.

- [ ] **Stop writing features (freeze Tue Aug 25).** Switch to running dry runs
  and fixing only what they surface.

**Cut order if behind:** SRE agent first (keep the stub), then the approve/reject
UI (use CLI). Never the block or the timeline.

---

## Where Mariam plugs into you

Your `graph.py` calls `github_ops.open_pr(state)` and
`github_ops.post_comment(...)`. Those are Mariam's — stubbed today, so your graph
already runs. As she fills them in, the signatures don't change, so nothing on
your side breaks. You two coordinate on: what the PR node needs in `DevResult`,
and the week-3 AgentCore deploy. Natural, frequent, low-stakes back-and-forth.
