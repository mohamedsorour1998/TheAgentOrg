# Mariam — Week 3 (Aug 22–27): deploy + hand off

Feature freeze **Tuesday Aug 25**. After that: only fix what dry runs find.

---

## Sat–Mon Aug 22–24 — AgentCore deploy, with Sorour

We already have a working AgentCore deploy pattern from another project —
reuse it, don't reinvent. It's the `bedrock-agentcore-starter-toolkit` CLI:
`configure` once, then `launch` on every push.

**What you + Sorour split:** he owns the IAM roles (runtime role + the OIDC
`github-actions-role`) in Terraform — done in his week 1. You own the deploy
workflow and the `agentcore configure`/`launch` wiring. Pair on the ARNs —
that's the one thing you both touch.

**Task: configure + launch each agent.**
```bash
pip install bedrock-agentcore-starter-toolkit

cd agentorg/agents
agentcore configure -e planner.py -n theagentorg_planner \
  -er arn:aws:iam::<ACCOUNT_ID>:role/theagentorg-shared-agentcore-runtime-role \
  -rf requirements.txt -r us-east-1 -ni

agentcore launch --auto-update-on-conflict \
  --env BEDROCK_MODEL=us.amazon.nova-2-lite-v1:0

agentcore status                  # shows the runtime ARN
agentcore invoke '{"task":"..."}' # smoke-test the deployed agent
```
`agentcore launch` builds the ARM64 image, pushes it to ECR, and
creates/updates the AgentCore runtime — one command replaces a whole Docker +
ECR + runtime dance. Repeat for all five agents.
**Done when:** `agentcore status` shows a healthy runtime for each of the 5
agents, and `agentcore invoke` returns a real response for at least one.

**Task: CI deploy job.** Add this to `.github/workflows/` (adapted from the
workflow we already have working elsewhere). Uses OIDC — no long-lived AWS
keys — and only fires when an agent file changes:
```yaml
name: Deploy AgentCore Agent
on:
  workflow_dispatch:
  push:
    branches: [main]
    paths: ["agentorg/agents/**"]

env:
  AWS_ACCOUNT_ID: "<ACCOUNT_ID>"
  IAM_ROLE: github-actions-role      # OIDC deploy role (Sorour's Terraform)

jobs:
  deploy:
    runs-on: ubuntu-latest
    permissions: { id-token: write, contents: read }
    defaults: { run: { working-directory: agentorg/agents } }
    steps:
      - uses: actions/checkout@v4
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: arn:aws:iam::${{ env.AWS_ACCOUNT_ID }}:role/${{ env.IAM_ROLE }}
          aws-region: us-east-1
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - name: Install agentcore CLI
        run: pip install bedrock-agentcore-starter-toolkit
      - name: Deploy agent
        run: agentcore launch --auto-update-on-conflict --env BEDROCK_MODEL=us.amazon.nova-2-lite-v1:0
```
**Done when:** pushing a change to `agentorg/agents/**` on `main` triggers a
green deploy run with no static AWS keys anywhere in the workflow.

---

## Tue Aug 25 — freeze

**Task: from freeze onward, only fix what dry runs surface.** No new work.

---

## Wed–Thu Aug 26–27 — offline demo proof

**Task: prove the offline demo end-to-end, with Sorour.**
Run the full clean + poisoned demo with the network off, twice.
**Done when:** both runs behave identically to online.

---

## End of week 3 — done when

- The graph runs against AgentCore-hosted agents, not local.
- CI deploys an agent automatically on a relevant push, via OIDC.
- Two offline dry runs behave identically to online, by Aug 27.
