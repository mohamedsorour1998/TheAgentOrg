# What one change costs, three ways

**Spec §4.** Three scenarios: a human developer working with Claude Code, this
pipeline on AWS, and this pipeline self-hosted. **Only one of the three has a bill
this repository can read**, and this document is arranged around that fact rather
than around it.

Regenerate with:

```bash
DEMO_REPO=mohamedsorour1998/auth-service PYTHONPATH=. \
  .venv-main/bin/python scripts/measure_cost.py --runs 3 --require-model
```

Raw data in `cost-comparison.json`. Measured at **`d6165c8`**, 2026-08-28.

---

## 1 · The headline, and what is load-bearing about its shape

| Scenario | Marginal cost per clean change | Wall clock | Status |
|---|---|---|---|
| **This pipeline, on AWS** | **$0.013036 – $0.016931** | 28.9 – 29.0 s of model time | **MEASURED** |
| **This pipeline, self-hosted** | **$0.00 in tokens** | **132.1 – 495.2 s** | **MEASURED IN TIME, NOT MONEY** |
| **A human, with Claude Code** | *your hourly rate × the minutes* | not measured | **NOT MEASURED, AND NOT ESTIMATED** |

**The third row is deliberately empty and it is the most important row in this
table.** A developer's hourly rate is not in this repository and neither is the wall
clock of a human review. A plausible-looking number there would become the most
quoted figure in this entire document and the least defensible — and CLAUDE.md's rule
is that a gap invites the measurement while a number ends it. §4 gives the arithmetic
and the two inputs a reader must supply themselves.

---

## 2 · The AWS scenario — measured, and 99.9% of it is the model

```
price       in $0.33/1M  out $2.75/1M   read 2026-08-28 from the AWS Pricing API
run 1  promoted  model=5/6  in=28362  out=1337  cached=0  usd=0.013036
run 2  promoted  model=6/7  in=33623  out=2122  cached=0  usd=0.016931
run 3  promoted  model=5/7  in=28404  out=1356  cached=0  usd=0.013102

MODEL COST PER CLEAN CHANGE   $0.013036 - $0.016931   median $0.013102
```

### The non-model lines, each with the published rate beside it

| Line | Per run | USD | Rate |
|---|---:|---|---|
| Lambda invocation | 1 | $0.00000020 | AWS Lambda, $0.20 per 1M requests |
| EventBridge event | 1 | $0.00000100 | Amazon EventBridge, $1.00 per 1M events |
| DynamoDB write | 9 | $0.00001125 | DynamoDB on-demand, $1.25 per 1M WCU |
| **TOTAL** | | **$0.00001245** | |

$0.0000125 against $0.0131 of model. **The model is 99.9% of the marginal cost of a
run**, and every piece of infrastructure in the architecture diagram put together is
three orders of magnitude below it. That is the finding: an optimisation effort spent
anywhere but the token count is spent on noise.

Reported to eight decimal places rather than to cents on purpose. Rounded to cents
the entire non-model cost of a run reads **$0.00**, which a reader takes as *free*
rather than as *below the resolution of this table*.

### Four lines deliberately excluded, each with a reason a reader can check

- **GitHub Actions minutes.** Free on a public repository, and this one is public. On
  a *private* repository they would dominate every line above — seven jobs plus three
  recorders, at 2,000 free minutes a month and $0.008/minute after.
- **ECR storage and CloudWatch retention.** Standing per-account costs, not per-run.
  Attributing them to a run requires a run count nobody has.
- **The Lambda's 256 MB × 10 s configuration.** One sub-second invocation at that size
  is four orders of magnitude below the free tier's monthly floor; pricing it produces
  a figure whose leading digit is noise.
- **Flex and priority inference tiers.** The same Pricing API query returns them at
  0.5× and 1.75×, and this pipeline selects neither — so pricing a run at the flex
  rate would halve every figure here.

### THE CACHE HIT RATE IS ZERO, AND THAT IS THE LARGEST SILENT COST IN THE DESIGN

```
CACHE: 0 cached tokens across 3 runs; provider reported caching at all: False
```

Two facts in one line, and they are different facts. `cached_tokens=0` is a
measurement. `cached_reported=False` means Nova reported nothing about caching **at
all** — the key is genuinely absent from the response, not zero. Lane E's `Usage`
separates them for the same reason `scan_provenance` exists.

Nothing in `agentorg/` sets a Bedrock cache point. So all five agents re-send the
repository snapshot on **every** call and pay $0.33/1M for input that would cost
$0.0825/1M cached — a **4× premium** on the largest part of every prompt. Measured:
input is 28,362 of 29,699 tokens in run 1, so **95.5% of the token volume is input**,
and it is the same repository view five times over.

Cache points are the single highest-leverage cost change available and are named in
`limitations.md` with what removing the limit would take.

---

## 3 · The self-hosted scenario — measured in time, not in money

From Lane F, `docs/selfhost-runbook.md`, six runs of the poisoned ticket, three per
side, same machine, same ticket, same commit:

```
              bedrock nova-2-lite           ollama qwen2.5-coder:7b
source        fixture|model                 fixture                       <- differs
status        blocked                       blocked
verdict       block                         block                         <- invariant, held
provenance    scanners                      scanners
wall_clock_s  28.9-29.0                     291.1-353.4                   <- differs
```

| | Value |
|---|---|
| marginal token cost | **$0.00** — no API is called |
| wall clock, all four local runs measured | **132.1 s · 291.1 s · 353.4 s · 495.2 s** |
| the defensible statement | **roughly an order of magnitude slower** |
| model-sourced runs, local | **0 of 3** |
| model-sourced runs, Bedrock | **2 of 3** |
| **the security verdict** | **identical — `block` on both sides, `provenance: scanners`, added lines `[3, 4]`** |

**Three things this row is not.** It is not $0.00 *total* — electricity and amortised
hardware are real and are not readable from inside this repository. It is not "10×
slower": the local spread is nearly **4× wide on its own**, so a specific multiple is
not supportable and a range is. And it is not free of quality cost: at a
**21,821-character** reviewer prompt the 7B model abandons the JSON instruction and
answers in prose, so **0 of 3** local runs were model-sourced and every number on
that side describes a fixture read.

**The one column that does not move is the one that matters.** Both sides `block`,
both `provenance: scanners`, both at added lines `[3, 4]`. That is not luck — no model
participates in computing the verdict, so it *cannot* move with the model. The
self-hosted path trades latency and model quality for a token bill of zero and buys
back nothing on the security guarantee, because there was nothing there to buy.

**And the honest limit on the whole scenario:** `infra/selfhost/docker-compose.yml`
**has never been run.** `docker compose config` parses it and resolves every
interpolation — a syntax and reference check. `docker compose up` has not, because
there is no Docker daemon on this machine. Re-measured 2026-08-28: `command -v docker`
finds `/opt/homebrew/bin/docker`; `docker info` reports no daemon. The measured runs
above used the local gateway directly, not the container stack.

---

## 4 · The human scenario — the arithmetic, and the two numbers you must supply

This row is **not estimated**, and the refusal is the point. What can be stated is the
shape of the calculation and where each input comes from:

```
cost_per_change  =  hourly_rate  ×  (review_minutes + rework_minutes) / 60
                    ─────────────    ────────────────────────────────
                    NOT in this       NOT in this repository. The pipeline's
                    repository        own human cost is 3 gate decisions on a
                                      promoted run and 1 on a blocked one —
                                      measured, `len(RunState.decisions)`.
```

**What this repository can honestly contribute is the right-hand term, and only for
itself.** `scorecard.md` measures **3 human touches** on a promoted run and **1** on a
blocked one, as medians of ten runs each. A gate decision is a person reading a PR
comment and clicking; the reading is the cost and its duration is not instrumented
anywhere.

**Two comparisons that would be wrong**, stated because both are tempting:

- **$0.013 against an hourly rate is not a like-for-like.** The pipeline does not
  remove the human; it removes the human from the *first* pass and keeps them at three
  gates. The comparable quantity is *minutes of human attention per change*, and this
  repository measures its own (3 decisions) and not the baseline's.
- **"$0.013 versus $75/hour" is an argument the numbers do not support.** It compares a
  measured token bill against an invented wage on an invented duration, and the ratio
  it produces would be the most-repeated number in this document.

The defensible claim, and the one on the deck:

> The machine cost of running every check on every change is **about one and a half
> cents**. Whether that is cheaper than the alternative depends on a wage and a
> duration this repository does not measure — but it is small enough that the decision
> is not about the token bill.

---

## 5 · What each figure is sensitive to

| Figure | Moves when | Direction |
|---|---|---|
| $0.0131 median | the model changes | `Nova Lite` is 5.5× cheaper on input and 11× on output; `nova-2-lite` is the shipped id and the two are **different models** — reading the old row for the new one understates output by an order of magnitude |
| $0.0131 median | a cache point is added | up to **4× less** on 95.5% of the volume |
| $0.0131 median | `MAX_REVISION_LOOPS` | each extra developer↔reviewer pass is two more model calls on the largest prompts |
| the 30% spread | nothing in the code | three consecutive runs of one unchanged ticket, so the spread is the model's own output-length variance |
| $0.0000125 infra | the repository goes private | Actions minutes then dominate everything above |
| 132–495 s local | contention | Lane F: runs 2–4 competed with other work against one single-model server; **132 s is the floor and 495 s an upper bound under load** |

---

## 6 · Why the model cost is read from a walk and not from a deployed run

`REMOTE_AGENTS=true` puts the model call inside the AgentCore container, and Lane E
measured the consequence: usage crosses that seam only through two wiring lines in
`agents/server.py` and `common/agent_client.py`. Those **are** now present — measured,
`server.py:203` sends `"usage": llm.usage_payload()` and `agent_client.py:556` calls
`absorb_usage_payload`. But **nothing assigns `RunState.cost`**, measured over the AST
in both pipelines:

```
agentorg/graph.py       state.cost stores at NONE   cost-API calls NONE
scripts/run_stage.py    state.cost stores at NONE   cost-API calls NONE
grep -rn '.cost =' agentorg/ scripts/   ->  (nothing)
```

So a deployed run carries no cost record even though the tokens now cross the wire.
`measure_cost.py` therefore drives `graph.run_pipeline` **in process**, where
`llm.usage()` sees every call directly, and reports `stages` beside `usd` for Lane E's
reason: an unwired run has zero rows with `usd=None`, a wired run that fell back has a
row per stage with `usd=0.0`, and **`usd == 0.0` cannot tell them apart**.

The gap is costed in `limitations.md`.
