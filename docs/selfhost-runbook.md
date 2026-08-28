# Running this pipeline on your own compute

Lane F. **The seam already existed** — `config.LLM_BASE_URL` routes every agent to
an OpenAI-compatible gateway, and the three scanners have always been local
binaries. This document is the proof and the price, not a feature announcement.

The headline, measured, and narrower than "it works":

> A poisoned ticket is **blocked** by a pipeline whose model runs on this laptop,
> with **no AWS hostname resolved or contacted**. The local model is **an order of
> magnitude slower** (28.9s against 132–495s over four runs) and **fails to produce
> parseable JSON far more often**, which sends agents to their fixtures. The security
> verdict is **identical** — which it must be, because no model is involved in
> computing it.

---

## The parity table

Six runs of the poisoned ticket, three per side, same machine, same ticket,
same commit. Produced by:

```bash
PYTHONPATH=. .venv-main/bin/python scripts/selfhost_parity.py \
  --baseline /tmp/lane-f-runs/bedrock-poisoned-{1,2,3}.json \
  --local    /tmp/lane-f-runs/ollama-poisoned-{1,2,3}.json
```

```
              bedrock nova-2-lite           ollama qwen2.5-coder:7b
------------  ----------------------------  ----------------------------
source        fixture|model                 fixture                       <- differs
model         us.amazon.nova-2-lite-v1:0    qwen2.5-coder:7b              <- differs
status        blocked                       blocked
verdict       block                         block                         <- invariant, held
provenance    scanners                      scanners
revisions     0                             0
wall_clock_s  28.9-29.0                     291.1-353.4                   <- differs
samples       3                             3
```

**Read `source` first.** `fixture` means the model did not answer and every other
number on that row describes a fixture. The table says so above itself, because a
reader who starts at the numbers would otherwise take a fixture run for a model run
— which is exactly what happened on this lane's first local measurement.

### What the numbers say

| Column | Finding |
|---|---|
| `wall_clock_s` | **28.9–29.0s against 291.1–353.4s** in the three-run table, and **132.1–495.2s** across all four local runs measured. So "roughly an order of magnitude" is the defensible statement and a specific multiple is not — the local spread is nearly 4× wide on its own. See the contamination note below. |
| `source` | The local model produced **0 model-sourced runs out of 3**. Bedrock produced **2 of 3** — so this is a difference of degree, not of kind. |
| `verdict` | **Identical, and this is the one that must not move.** Both sides `block`. |
| `provenance` | Both `scanners`. Real gitleaks on both sides, at added-lines `[3, 4]` — the fixture reports `[4, 5]`. |
| `revisions` | `0` on both. Not a finding: this poisoned ticket's reviewer approved on the first pass in every run, so the loop never iterated. **The revision-count delta this lane expected to report is unmeasured.** |

### Why `source=fixture` on the local side — the actual degradation

Not a timeout, not a crash, and not a schema mismatch. At a **21,821-character**
reviewer prompt the 7B model abandons the JSON instruction and answers in prose.
Measured directly:

```
REVIEWER PROMPT CHARS: 21821
REPLY CHARS: 1421
EXTRACTED starts with brace: False
VALIDATES: no -> 1 validation error for ReviewResult
  Invalid JSON: expected value at line 1 column 1
  [input_value="This code snippet is a F...lity of the login flow.", input_type=str]
```

Per-agent, three agents driven directly against the local gateway:

```
planner    source=model     (short prompt, no repository snapshot)
developer  source=model     20,686 chars — complies
reviewer   source=fixture   21,821 chars — prose, not JSON
security   source=model     no model needed for the verdict
sre        source=fixture   21,281 chars — prose, not JSON
```

**Prompt size alone is not the explanation** — the developer's prompt is only 1.1k
shorter than the reviewer's and it complies. All three call the same
`llm.structured`, so the difference is the model's behaviour on those particular
prompts, not anything in our code.

**Bedrock is not immune.** Run 2 of 3 on the baseline also recorded `fixture`. So
the honest statement is *the local model fails JSON compliance much more often*,
not *the local model cannot do this*.

### What this table cannot say

- **n=3 per side.** A single-run delta would be a sample, not a property of the
  model; three is still small. The `samples` row is printed for that reason.
- **The clean ticket was not measured** on the local side. Only the poisoned one,
  because the block is the claim. A clean run's `promoted`-versus-`failed` split is
  the more interesting number and it is **unmeasured**.
- **The local timings vary by nearly 4×, and none is a clean measurement.** Four
  runs of the same configuration: **132.1s**, **291.1s**, **353.4s**, **495.2s**.
  Runs 2–4 competed with other work against the same single-model server. So 132s is
  the floor and 495s an upper bound under contention — quote the range, never a
  point. This is CLAUDE.md's own measured trap (116.88 / 149.68 / 102.83 for one
  unchanged snapshot), and a 10× headline computed from a single pair would be the
  same over-claim.

---

## The "no AWS call" evidence — what it actually proves

`agentorg/selfhost/airgap.py` intercepts **`socket.getaddrinfo` and
`socket.socket.connect`** and records every host. All three local runs:

```
network: no AWS hostname resolved or contacted -- 4 connection(s),
         0 name(s) resolved; 1 bare IP address(es) contacted whose
         ownership this witness cannot determine
    other hosts: 127.0.0.1
```

All three baseline runs, as a control:

```
network: REACHED AWS -- 10 contact(s) with 1 AWS host(s):
         bedrock-runtime.us-east-1.amazonaws.com
```

**The control is what makes the local result meaningful.** A witness that reported
"no AWS" for both would prove nothing.

### Three things this evidence is not

1. **It is not "we unset the credentials".** That is refuted: with no credentials
   `llm.available()` returns False, so the run makes no AWS call *and* does no model
   work, and the result cannot tell those apart. This machine holds credentials in
   `~/.aws/credentials` throughout — the local runs had every ability to reach
   Bedrock and did not.
2. **It observes this process only.** A subprocess — `git clone`, a scanner binary —
   has its own socket module. Printed with every summary.
3. **A bare IP is unattributable.** One connection per local run was to an address
   with no name behind it (`127.0.0.1`). Reported as a count rather than suppressed.

So the claim the evidence supports: *no connection to any AWS-owned hostname was
resolved or attempted from this process, and one loopback address was contacted.*

### The defect this witness had, and why it is worth knowing

The first version patched only `connect`. It reported a **real Bedrock run** —
`source=model`, ten calls — as `is_airgapped_from_aws() -> True`, because botocore
resolves the name before connecting so every contact was a bare IP matching no
marker. **The check was fail-open in the only direction that matters**, and nothing
in its output looked wrong. Fixed by intercepting the resolver too; pinned by
`tests/test_selfhost_airgap.py`, whose RED step is that exact reversion.

---

## Running it

### One command

```bash
scripts/selfhost_demo.sh
```

Refuses, before spending any wall clock, if the gateway is absent or the model is
not pulled — both of which otherwise produce a **silent** all-fixture run that still
prints a correct-looking `block`. Warns (does not refuse) on absent scanners, since
the pipeline records `fixture-fallback` honestly but a reader needs to know before
the verdict appears.

### From scratch

```bash
brew install ollama
ollama serve &
ollama pull qwen2.5-coder:7b            # 4.7 GB

# NOT DECLARED ANYWHERE IN THIS REPOSITORY — see the gaps below
.venv-main/bin/python -m pip install 'strands-agents[openai]'

scripts/selfhost_demo.sh
```

### The two configuration traps

**`LLM_API_KEY` must be set, and not to its default.** Measured:

```
LLM_BASE_URL=http://127.0.0.1:11434/v1                ->  available() = False
LLM_BASE_URL=... LLM_API_KEY=local                    ->  available() = True
```

Both `llm.available()` and `common/model.create_model()` refuse the literal
`not-needed`. A local gateway ignores the value, so **nothing downstream complains**
— the only symptom is `_source=fixture`. The naive local-gateway configuration (set
only `LLM_BASE_URL`) is a fully green run in which the local model was never
contacted.

**`DEMO_REPO`, not `GITHUB_REPO`** — `config.py`'s one name mismatch. Unset,
`repo_snapshot.snapshot()` returns `{}` and every agent reasons about a file it
cannot see.

---

## The Compose stack

`infra/selfhost/docker-compose.yml`. Four services: one Postgres for **both** the
queue and the application (Phase 0's `QUEUE_BACKEND=postgres`), ollama, a one-shot
model pull, and Lane A's worker.

**It has never been started.** `docker-compose config` parses it and resolves every
interpolation — a syntax and reference check. There is no Docker daemon on this
machine (`docker info` fails on `/var/run/docker.sock`) and neither podman nor
colima has a VM, so **the image builds are unproven**. A compose file that parses is
not a stack that runs.

`tests/test_selfhost_compose.py` asserts what can be checked without a daemon: every
published port bound to `127.0.0.1`, no AWS credential or region in any service
environment, the non-default `LLM_API_KEY`, the worker waiting on the model pull
*finishing*, `SCANNERS_REQUIRED=true`, and `approve_server` absent from every command.

---

## Gaps, stated rather than closed

| Gap | Why it matters |
|---|---|
| **`openai` is not a declared dependency** | `strands.models.openai` needs it; nothing in `pyproject.toml` or `agentorg/agents/requirements.txt` names it, and `strands-agents` does not pull it. So **the documented self-hosted path fails on a clean install** with `ModuleNotFoundError: No module named 'openai'`. It belongs in a `selfhost` extra. Not this lane's file. |
| **`psycopg` is not installed** | So `QUEUE_BACKEND=postgres` cannot be exercised on this host at all. The Compose stack sets it; nothing here has run it. |
| **`docker compose up` unrun** | Above. |
| **The clean ticket, locally** | Unmeasured. The `promoted`-versus-`failed` split is the number a judge is most likely to ask for. |
| **The revision-count delta** | `0` on both sides here, so this lane's expected sharpest signal produced nothing. It needs a ticket whose reviewer objects. |
| **Helm** | Not attempted. The calendar did not allow it and Compose is the stated vehicle. |
