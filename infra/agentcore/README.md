# infra/agentcore/ — AgentCore deploy assets

The assets that create the five AgentCore runtimes. **Owner: Sorour (IAM/ECR),
driven by Mariam (CLI).** Specs: `docs/plan/sorour/week3.md:259-311` and
`docs/plan/mariam/week3.md:38-100`.

## The live deploy has NOT been run

**Status: BLOCKED-ON-APPROVAL.** Everything here was written and validated
offline. No `agentcore configure`, no `agentcore launch`, no `docker push`, no
`aws` mutation, no `terraform apply` has been executed. The five runtimes do not
exist yet.

That is measured, not assumed. `deploy_note()` reports:

```
AgentCore deploy unverified: 0 of 5 runtimes ready (not ready: theagentorg_planner,
theagentorg_developer, theagentorg_reviewer, theagentorg_security, theagentorg_sre)
```

`ListAgentRuntimes` in `us-east-1` returns 10 READY runtimes and none is
`theagentorg_*` — they belong to another project. So `deploy.sh` is not a
rehearsal: its first run creates all five from nothing.

## Files

| File | What it is |
|---|---|
| `deploy.sh` | The five `configure`/`launch` pairs. **LIVE and BILLABLE.** Refuses to run without an env var and a typed confirmation. |
| `Dockerfile` | What the runtime image must contain. Reviewable specification; see the caveat below. |
| `../../agentorg/agents/requirements.txt` | What AgentCore installs into the image. |
| `../../.dockerignore` | Build-context exclusions. Lives at the repo root because that is the context root. |

## Identifiers — read, not derived

Every identifier comes from `docs/plan/week1-verification-log.md:11-30`. None was
re-derived from Terraform state (the task text forbids it) and none was recalled.

```
AWS account                : 339712964409
Region                     : us-east-1
AgentCore runtime role ARN : arn:aws:iam::339712964409:role/theagentorg-shared-agentcore-runtime-role
GitHub Actions OIDC role   : arn:aws:iam::339712964409:role/github-actions-role
Trust subject              : repo:mohamedsorour1998/TheAgentOrg:*
```

**Two namespaces, both real, both correct in their own place** — conflating them
is the easiest way to break this deploy:

| Agent | AgentCore runtime name (underscores) | ECR repository (hyphens) |
|---|---|---|
| planner | `theagentorg_planner` | `theagentorg-shared-planner-agent` |
| developer | `theagentorg_developer` | `theagentorg-shared-developer-agent` |
| reviewer | `theagentorg_reviewer` | `theagentorg-shared-reviewer-agent` |
| security | `theagentorg_security` | `theagentorg-shared-security-agent` |
| sre | `theagentorg_sre` | `theagentorg-shared-sre-agent` |

Runtime names come from `docs/plan/sorour/week3.md:292` (`-n theagentorg_planner`).
All five ECR repos sit under `339712964409.dkr.ecr.us-east-1.amazonaws.com/`.
Corroborating evidence for the underscores: the account's other 10 runtimes all
use them.

## Running the deploy (under approval)

```bash
bash infra/agentcore/deploy.sh --dry-run          # prints the commands, touches nothing

export AGENTORG_DEPLOY_I_MEAN_IT=yes              # then type 'deploy' at the prompt
bash infra/agentcore/deploy.sh
```

Four gates, in this order: a mode argument or the env var; the env var equal to
exactly `yes`; the `agentcore` CLI and `requirements.txt` both present; and a
typed `deploy`. With no arguments and no env var the script prints why it refused
and exits non-zero.

Install the CLI first (`docs/plan/mariam/week3.md:50`):

```bash
pip install bedrock-agentcore-starter-toolkit
```

## After the deploy — verify with two independent checks

```bash
cd agentorg/agents
agentcore status                       # each runtime READY
agentcore invoke '{"task":"say hi"}'   # a real completion, not an auth error
```

```bash
python -c 'from agentorg.github_ops import deploy_note; print(deploy_note())'
```

The second reads `ListAgentRuntimes` rather than the CLI's local state, so it can
disagree with `agentcore status` — and if it does, that disagreement is the
finding. It has never run against a real deployed runtime, only a fake, so one
live read-only call is also the cheapest end-to-end confirmation that that code
path works.

If `invoke` returns `AccessDenied` on Bedrock or on the ECR pull, the fix is the
runtime role's policy, not the CLI (`docs/plan/sorour/week3.md:302-304`).

## What was validated, and what was not

Validated on the authoring machine:

- `bash -n deploy.sh` and `shellcheck deploy.sh` both exit 0.
- All four of `deploy.sh`'s gates, exercised by running it — including with a
  **fake `agentcore` on PATH**, which is what makes the confirmation gate
  testable at all. Without the fake the script exits at its missing-CLI check
  and never reaches the prompt, so a gate test without it would pass while
  proving nothing. With the fake: a wrong confirmation recorded **zero**
  `agentcore` invocations; `deploy` recorded exactly the ten commands the spec
  specifies, plus a `status` per agent.
- `requirements.txt` parses as valid requirements, and covers every third-party
  import derived from an AST walk over `agentorg/**/*.py`.
- The five pins **resolve together** against the real index —
  `pip install --dry-run -r agentorg/agents/requirements.txt` exits 0. A pin
  conflict would otherwise have surfaced inside an ARM64 build nobody is
  watching. That run also resolved `bedrock-agentcore` to `1.22.0`, which is why
  the file says a version exists but deliberately does not pin to it.
- The fixtures defect below, reproduced against a real `pip install --target`.
- `tests/test_agentcore_deploy_assets.py` — 49 tests, all 24 behaviour-changing
  mutations caught, with a no-op self-test staying green. Three of those
  mutations initially went **undetected**, and fixing that changed the tests
  rather than the assets: they had been substring-matching the whole file, which
  the assets' own explanatory comments satisfied. Both `deploy.sh`'s agent table
  and the `Dockerfile`'s instructions are now parsed as data, so prose cannot
  stand in for a working instruction.
- `OFFLINE=true python -m agentorg.graph` still completes with no AWS (rc=0).

**NOT validated — no container was ever built.** `docker`, `podman` and `finch`
are all absent from the authoring machine (`command -v` returned rc=1 for each).
So the `Dockerfile` is a reviewed specification, not a tested artifact:

- **The scanner-install URLs are unverified.** They are adapted from
  `.github/workflows/ci.yml:143-169` — real, working truth, but for `linux_x64`,
  while `agentcore launch` builds ARM64. The ARM64 asset names were not confirmed
  by any fetch, so they are the first thing to check if a build fails. The stage
  cannot fail *quietly*, which is what makes shipping it defensible: `curl -sSfL`
  turns a wrong asset name into a failed build, and the stage ends with
  `gitleaks version && trivy --version && semgrep --version`, which also catches
  a binary that downloads fine and cannot execute (a wrong-arch download).
- The base image is pinned by **tag**, not digest: no container runtime here
  could resolve a real digest, and a fabricated digest is worse than a tag.
- `agentcore status` and `agentcore invoke` were never run. No output from either
  appears anywhere in this repository.

Also note: `agentcore launch` generates **its own** Dockerfile and does the ARM64
build and ECR push itself, so this `Dockerfile` is not on that path. It exists as
a reviewable statement of what the image must contain, and as something a
teammate can build on a laptop. If the two disagree, this file is the
specification.

## The fixtures defect this Dockerfile works around

`agentorg/fixtures_loader.py:21` computes:

```python
_FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "fixtures"
```

That resolves relative to the **install location**. In a non-editable install —
which is what a container has — it points inside site-packages, not at the repo
root. Measured with `pip install --no-deps --no-cache-dir --target=/tmp/nei .`:

```
_FIXTURES resolves to: /private/tmp/nei/fixtures
exists? False
plan() RAISED: FileNotFoundError [Errno 2] .../nei/fixtures/plan_result.json
```

All five agents call `fixtures_loader` on their fallback path, and that path is
the **normal** one whenever a model call returns `None` — which is what happens
with no credentials or an unreachable Bedrock. So without a fix the runtime does
not degrade gracefully to fixtures; it raises `FileNotFoundError`. The Dockerfile
copies `fixtures/` beside the installed package, with the destination computed
from `sysconfig` so a base-image bump cannot orphan it. After that copy all five
loaders return their models.

The cleaner fix is to make `fixtures/` package data, but that means editing
`pyproject.toml` and `fixtures_loader.py`, which belong to a landed task and to
the offline demo path respectively. Left as a container-layer workaround
deliberately, pinned by
`test_fixtures_are_unreachable_from_a_target_install` — if someone later makes
it package data, that test goes red as a signal to re-read, not as a regression.

## The gap this task did not close

**The five agent modules are library functions, not servers.** Each exposes
`run(state: RunState) -> ...Result`. None has a `__main__` guard, none imports
`bedrock_agentcore`, and none constructs an app object — verified by reading all
five. Running one directly fails:

```
$ python agentorg/agents/planner.py
ImportError: attempted relative import with no known parent package
```

(from the `from .. import fixtures_loader` at `planner.py:13`).

`agentcore configure -e planner.py` expects an entrypoint it can serve, so
**an adapter is still required** between these functions and the AgentCore
contract. That work is not in this task and not in these assets. Whoever runs the
deploy should expect `agentcore launch` to need it — this is the most likely
reason a first launch fails, and it is a code gap, not a credentials problem.

`SCANNERS_REQUIRED=true` is set in the image because
`agentorg/common/config.py:73-77` requires it for any production image: with the
default `false`, an absent scanner makes the gate borrow a fixture verdict and
report clean because it never ran — failing open. That flag and the scanner
install are two halves of one decision; the flag alone would make every scan a
hard fault.
