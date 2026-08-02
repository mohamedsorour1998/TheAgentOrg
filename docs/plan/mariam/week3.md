# Mariam — Week 3 (Aug 22–27): deploy to AgentCore + OIDC deploy job, then prove offline

Weeks 1–2 made the GitHub seam and CI real. This week you deploy all five agents
to AWS Bedrock AgentCore with the `bedrock-agentcore-starter-toolkit` CLI, add a
GitHub Actions deploy job that assumes an AWS role via OIDC (no static keys), and
prove the offline demo runs end to end with Sorour.

You co-own this deploy with Sorour: he owns the IAM roles in Terraform (done in
his week 1) and hands you two values from `terraform output`:
- `agentcore_runtime_role_arn` — the runtime role AgentCore assumes
  (`theagentorg-shared-agentcore-runtime-role`, trusts
  `bedrock-agentcore.amazonaws.com`).
- `github_actions_role_arns` — the pre-existing `github-actions-role` (shared
  OIDC role, looked up via a Terraform `data` source, never Terraform-managed).

AWS account is Sorour's: id `339712964409`, region `us-east-1`. Bedrock model is
Nova Lite `us.amazon.nova-2-lite-v1:0`.

**Feature freeze — Tuesday Aug 25.** After freeze, only fix what dry runs
surface; no new work. Target ready date **Aug 27**.

**Frozen-contract rule still holds:** ADD optional fields only, never rename or
remove anything in `agentorg/state.py`.

---

## Sat–Mon Aug 22–24 — AgentCore deploy for all 5 agents (with Sorour)

**Task: get the two ARNs from Sorour, then configure + launch each agent.**

The five agent files live in `agentorg/agents/`: `planner.py`, `developer.py`,
`reviewer.py`, `security.py`, `sre.py`. AgentCore names use underscores:
`theagentorg_planner`, `theagentorg_developer`, `theagentorg_reviewer`,
`theagentorg_security`, `theagentorg_sre`.

Steps:
1. Get the ARNs from Sorour (he runs `terraform output` in
   `infra/Terraform/environments/shared/`):
   ```bash
   export RUNTIME_ROLE_ARN=arn:aws:iam::339712964409:role/theagentorg-shared-agentcore-runtime-role
   ```
2. Install the toolkit and make sure a `requirements.txt` exists next to the
   agents (AgentCore builds from it). Create
   `agentorg/agents/requirements.txt` if missing:
   ```
   strands-agents
   bedrock-agentcore
   pydantic>=2
   ```
   Install the CLI:
   ```bash
   pip install bedrock-agentcore-starter-toolkit
   ```
3. `configure` then `launch` each agent. `agentcore launch` builds the ARM64
   image, pushes it to the ECR repo, and creates/updates the AgentCore runtime —
   one command replaces the whole Docker + ECR + runtime dance. Do the planner
   first, verify, then repeat for the other four.

```bash
cd agentorg/agents

# --- planner ---
agentcore configure -e planner.py -n theagentorg_planner \
  -er "$RUNTIME_ROLE_ARN" \
  -rf requirements.txt -r us-east-1 -ni
agentcore launch --auto-update-on-conflict \
  --env BEDROCK_MODEL=us.amazon.nova-2-lite-v1:0
agentcore status                    # shows the runtime ARN + health
agentcore invoke '{"task":"say hi"}'  # smoke-test the deployed agent

# --- repeat for the other four (swap -e and -n) ---
agentcore configure -e developer.py -n theagentorg_developer -er "$RUNTIME_ROLE_ARN" -rf requirements.txt -r us-east-1 -ni
agentcore launch --auto-update-on-conflict --env BEDROCK_MODEL=us.amazon.nova-2-lite-v1:0

agentcore configure -e reviewer.py -n theagentorg_reviewer -er "$RUNTIME_ROLE_ARN" -rf requirements.txt -r us-east-1 -ni
agentcore launch --auto-update-on-conflict --env BEDROCK_MODEL=us.amazon.nova-2-lite-v1:0

agentcore configure -e security.py -n theagentorg_security -er "$RUNTIME_ROLE_ARN" -rf requirements.txt -r us-east-1 -ni
agentcore launch --auto-update-on-conflict --env BEDROCK_MODEL=us.amazon.nova-2-lite-v1:0

agentcore configure -e sre.py -n theagentorg_sre -er "$RUNTIME_ROLE_ARN" -rf requirements.txt -r us-east-1 -ni
agentcore launch --auto-update-on-conflict --env BEDROCK_MODEL=us.amazon.nova-2-lite-v1:0
```

**Done when:** `agentcore status` shows a healthy runtime for each of the 5
agents, and `agentcore invoke '{"task":"say hi"}'` returns a real text response
for at least one:
```bash
cd agentorg/agents
agentcore status
agentcore invoke '{"task":"say hi"}'
```
Expected: `agentcore status` prints a runtime ARN like
`arn:aws:bedrock-agentcore:us-east-1:339712964409:runtime/theagentorg_planner-...`
with status `READY`; `agentcore invoke` returns a JSON payload with a non-empty
model response, not an error.

**You're unblocked because:** the ECR repos and the runtime role already exist
(Sorour's week-1 Terraform); you only need the ARN he hands you.

**Blocks / Hands off to:** once agents are deployed, the graph can point at
AgentCore-hosted agents instead of local stubs. Coordinate the switch with
Sorour (he owns `agentorg/agents/*` and `agentorg/graph.py`).

---

**Task: wire the `deploy_note()` placeholder to report the deployed runtimes.**
`agentorg/github_ops.py` has a stub you own:

```python
def deploy_note() -> str:
    """Placeholder for the AgentCore deploy step you co-own with Sorour."""
    return "deploy not wired yet — pair with Sorour on infra/agentcore/"
```

Replace it with a one-liner that reports the deployed agent set, so the graph/log
can surface deploy status:

```python
def deploy_note() -> str:
    """Report the AgentCore deploy target for the log/UI."""
    agents = ["planner", "developer", "reviewer", "security", "sre"]
    names = ", ".join(f"theagentorg_{a}" for a in agents)
    return f"AgentCore runtimes (us-east-1): {names}"
```

**Done when:**
```bash
python -c "from agentorg import github_ops; print(github_ops.deploy_note())"
```
prints `AgentCore runtimes (us-east-1): theagentorg_planner, theagentorg_developer, theagentorg_reviewer, theagentorg_security, theagentorg_sre`.

---

## Mon Aug 24 — GitHub Actions deploy job via OIDC (no static keys)

**Task: add a deploy workflow that assumes `github-actions-role` via OIDC and
launches on pushes that touch an agent file.** No long-lived AWS keys anywhere.

Create `.github/workflows/deploy.yml`:

```yaml
# Deploy agents to AgentCore. OWNER: Mariam. OIDC only — no static AWS keys.
name: deploy-agentcore

on:
  workflow_dispatch:
  push:
    branches: [main]
    paths: ["agentorg/agents/**"]

env:
  AWS_ACCOUNT_ID: "339712964409"
  AWS_REGION: us-east-1
  IAM_ROLE: github-actions-role      # OIDC deploy role (Sorour's Terraform)

jobs:
  deploy:
    runs-on: ubuntu-latest
    permissions:
      id-token: write                # required for OIDC
      contents: read
    defaults:
      run:
        working-directory: agentorg/agents
    strategy:
      matrix:
        agent:
          - { entry: planner.py,   name: theagentorg_planner }
          - { entry: developer.py, name: theagentorg_developer }
          - { entry: reviewer.py,  name: theagentorg_reviewer }
          - { entry: security.py,  name: theagentorg_security }
          - { entry: sre.py,       name: theagentorg_sre }
    steps:
      - uses: actions/checkout@v4

      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: arn:aws:iam::${{ env.AWS_ACCOUNT_ID }}:role/${{ env.IAM_ROLE }}
          aws-region: ${{ env.AWS_REGION }}

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install agentcore CLI
        run: pip install bedrock-agentcore-starter-toolkit

      - name: Configure ${{ matrix.agent.name }}
        run: |
          agentcore configure -e ${{ matrix.agent.entry }} -n ${{ matrix.agent.name }} \
            -er arn:aws:iam::${{ env.AWS_ACCOUNT_ID }}:role/theagentorg-shared-agentcore-runtime-role \
            -rf requirements.txt -r ${{ env.AWS_REGION }} -ni

      - name: Launch ${{ matrix.agent.name }}
        run: agentcore launch --auto-update-on-conflict --env BEDROCK_MODEL=us.amazon.nova-2-lite-v1:0
```

Notes for the fresh agent:
- `permissions: { id-token: write }` is what lets `configure-aws-credentials`
  exchange the GitHub OIDC token for temporary AWS creds — there are no
  `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` secrets anywhere in this repo.
- `paths: ["agentorg/agents/**"]` scopes the trigger to agent changes only, so a
  docs or CI edit does not redeploy.
- The `github-actions-role` already trusts subject
  `repo:mohamedsorour1998/TheAgentOrg:*` (added outside Terraform via AWS CLI),
  so this repo's Actions can assume it.

**Done when:** pushing a trivial change under `agentorg/agents/**` on `main`
triggers a green `deploy-agentcore` run. Test the trigger safely first:
```bash
git switch -c deploy-smoke
printf '\n# deploy smoke %s\n' "$(date +%s)" >> agentorg/agents/planner.py
git add agentorg/agents/planner.py
git commit -m "chore: trigger deploy smoke"
git push -u origin deploy-smoke
gh pr create --fill --base main && gh pr merge --squash --admin
```
Then watch the run:
```bash
gh run watch --exit-status
```
Expected: `gh run watch` exits 0 (green); the run log shows
`Configure`/`Launch` steps for each matrix agent, and no step references a
static AWS key. Confirm in the Actions UI that the `configure-aws-credentials`
step assumed `arn:aws:iam::339712964409:role/github-actions-role` via OIDC.

**You're unblocked because:** the OIDC role and its trust subject already exist
(shared, pre-provisioned); you only reference its ARN.

---

## Tue Aug 25 — feature freeze + record the deploy path is reproducible

**Task: freeze. From here, only fix what dry runs surface — no new features.**
Do a final full deploy-from-clean to prove reproducibility, then stop adding:
```bash
gh workflow run deploy-agentcore
gh run watch --exit-status
```
**Done when:** a `workflow_dispatch`-triggered `deploy-agentcore` run goes green
end to end with no code changes, proving the deploy is reproducible from a clean
trigger. After this, your only commits are dry-run fixes.

---

## Wed–Thu Aug 26–27 — prove the offline demo end to end (with Sorour)

**Task: run the full clean + poisoned demo with the network off, twice, and
confirm it behaves identically to online.** This is the venue-network insurance:
the demo must never depend on the venue wifi.

Steps:
1. Turn wifi off physically.
2. Run both paths offline:
   ```bash
   OFFLINE=true python -m agentorg.graph            # clean    -> promoted
   OFFLINE=true python -m agentorg.graph --poisoned # poisoned -> blocked (2 blocking)
   ```
3. Inspect the offline artifacts your week-2 code wrote:
   ```bash
   git -C runs/offline-demo branch --list 'agent-org/*'
   cat runs/offline-demo/NOTES.md
   ```
4. Repeat the whole sequence a second time (re-run safe — branches use `-B`,
   the diff file path is deterministic).

**Done when:** both offline runs behave identically to their online counterparts:
- clean prints `status=promoted`;
- poisoned prints `status=blocked` and `security verdict=block, blocking=2`;
- `git branch` lists `agent-org/DEMO-CLEAN-<short_sha>` and
  `agent-org/DEMO-POISON-<short_sha>`;
- `NOTES.md` has a `## DEMO-POISON` section with the block explanation
  mentioning the hardcoded AWS key;
- the second run produces the same result as the first, with wifi off the whole
  time.

**Cross-check:** Reem owns and rehearses the 5–7 min English demo script; run
your offline proof during one of her two rehearsals so the exact commands she'll
speak to are the ones you've verified.

---

## End of week 3 — done when

- All 5 agents are deployed to AgentCore: `agentcore status` shows a `READY`
  runtime for each, and `agentcore invoke '{"task":"say hi"}'` returns a real
  response for at least one.
- `deploy_note()` reports the five `theagentorg_*` runtimes.
- `.github/workflows/deploy.yml` deploys on a push to `agentorg/agents/**` via
  OIDC (`github-actions-role`) with zero static AWS keys — a triggered
  `deploy-agentcore` run goes green (`gh run watch --exit-status` exits 0).
- Two offline dry runs (clean + poisoned) behave identically to online, verified
  with wifi off by Aug 27.

**Cut/fallback note:** if AgentCore launch is unstable at the venue, the offline
path (`OFFLINE=true python -m agentorg.graph`) runs the entire demo locally with
no AWS at all — that is the fallback you rehearsed Wed–Thu, so a cloud outage
never blocks the demo. Never cut the offline path.
