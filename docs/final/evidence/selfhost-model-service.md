# The compose model service — what it is for, and when it runs

**Open item 10: "`docker compose up` for the model service — the ollama half of the
compose file is unused while the pipeline stays on GitHub."**

**Ruling: closable by documentation, and closed here.** It is not orphaned code and
not a defect. It is the containerised half of a path the operator has decided not to
take, and the item existed because nothing said *when* that path is taken. This
document says it. One real gap was found while writing it, and it is in a file this
lane does not own — §4.

---

## 1 · What it is for

`config.LLM_BASE_URL` routes the model call to any OpenAI-compatible gateway instead
of Bedrock. The `model` service serves exactly that, and **nothing in `agentorg/`
knows it is ollama** — the compose file says so in its own header. The seam is the
product; ollama is one implementation of it.

It is the deployment half of a subsystem that already exists and is measured:

| Where | What |
|---|---|
| `agentorg/selfhost/parity.py` | Bedrock-vs-local answer parity, per label |
| `agentorg/selfhost/airgap.py` | what still reaches the network when the model does not |
| `scripts/selfhost_measure.py` · `selfhost_parity.py` | the harness and the comparison |
| `docs/selfhost-runbook.md` | the operator's route, **natively** |
| `tests/test_selfhost_parity.py` · `test_selfhost_compose.py` | what can be asserted with no daemon |

So the honest sentence is **not** "unused code". It is: *the seam is exercised, the
parity is measured, and the compose expression of it has never been started.*

## 2 · When it runs — and when it does not

**It does not run in the documented local stack.** CLAUDE.md's recipe names services
one at a time and never names this one:

```bash
podman compose up -d postgres          # then both schemas
podman compose up -d api web
```

`api` and `web` declare no dependency on `model`, so neither pulls it in. The
services that do are `worker` → `model-pull` → `model`.

**It runs when somebody wants the pipeline off Bedrock**, which is the whole of Lane
F: an air-gapped or cost-capped deployment where a local gateway answers instead. The
operator's standing decision is that **the pipeline stays on GitHub Actions and the
database is local**, so that path is deliberately dormant.

**Measured on Apple silicon, and it is why the runbook measures natively:** the VM has
no Metal access, so a containerised model runs on **CPU** and is slower than the same
model served natively. No `deploy.resources.reservations.devices` block is present,
deliberately — a GPU reservation that silently does nothing is a performance claim
nobody can check.

## 3 · What "dormant" costs, stated

Nothing, per invocation — and that is the point. The compose file is declaration, not
a running service; there is no standing charge and no AWS resource behind it. Compare
the one thing in this repository that *would* have had a standing charge: a
db.t4g.micro Single-AZ Postgres at **$0.0160/hour ≈ $11.68/month**, refused by
decision. The model service is refused by *configuration*, which is cheaper to
reverse and cheaper to keep.

## 4 · THE ONE REAL GAP, and it is not in a file this lane owns

**There are no compose profiles.** Measured 2026-09-09 over
`infra/selfhost/docker-compose.yml`:

```
services: ['postgres', 'model', 'model-pull', 'worker', 'api', 'web']
profiles:  NONE — every service starts on a bare `compose up`
```

So the ollama half is dormant **only because the documented recipe never types
`compose up` without arguments.** Anyone who types the obvious command gets a 4.7 GB
image pull, a model pull, and a `worker` that starts claiming jobs from the queue —
none of which they asked for, and the first two of which look like a hang.

The fix is a `profiles: ["selfhost-model"]` key on `model`, `model-pull` and
`worker`, so the default `up` brings up exactly the three services the runbook
documents and the local path becomes an explicit `--profile selfhost-model`. That is
`infra/selfhost/docker-compose.yml`, which this lane does not own. **Reported, not
taken.**

## 5 · Two stale claims in `docs/selfhost-runbook.md`

Also not this lane's file, also reported rather than edited:

| It says | Measured |
|---|---|
| "Four services" | **six**: postgres, model, model-pull, worker, api, web |
| "`docker compose up` unrun" · "`psycopg` is not installed" | CLAUDE.md records postgres + api + web verified under podman on 2026-08-28, and `.venv-main` carries psycopg 3.3.4 |

The runbook's core claim survives both corrections: **the model, model-pull and
worker services have still never been started.** That is what item 10 was about, and
it remains true — by decision.

## 6 · What would close it by execution rather than by decision

Named so the next person does not have to derive it, and deliberately not done here:

```bash
cd infra/selfhost
podman compose up -d model            # ~4.7 GB image
podman compose up model-pull          # one-shot; pulls SELFHOST_MODEL, exits 0
podman compose up -d worker
.venv-main/bin/python scripts/selfhost_measure.py --label ollama --strict
```

Not run, and the reason is a judgement rather than an obstacle: the operator's
decision is that the pipeline stays on GitHub, so a 4.7 GB pull would verify a path
nobody is taking. **That is a decision, not a measurement** — and unlike item 5's
"no browser on this host", it is stated as one.
