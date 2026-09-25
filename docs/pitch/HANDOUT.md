# Team handout — The Agent Org at the DevOps Hackathon Finals

**TD63 RosettaTeam · 26 September 2026 · Glass Room 1 · 3:50 PM · Creativa Giza**

Mohamed Sorour presents; all five of us stand up, and each of us takes the questions on
our own part. **Everyone reads sections 1 to 5. Then study your own part in section 6,
and at least skim the others'.** Every number here is on a slide or measured; if a judge
asks for one that is not here, say you will check rather than guess.

---

## 1 · The pitch in one minute

Coding agents now write pull requests, and more and more often the reviewer is another
model. A model can be persuaded or tricked, so it is the wrong thing to stand between a
leaked credential and the main branch.

The Agent Org is a pipeline of five AI agents — planner, developer, reviewer, security,
SRE — that take a ticket to a pull request. **Three human gates** pause the run for a
named person. And **one rule with no model in it** decides whether the change is blocked:
three security scanners and a fixed severity threshold. Same input, same answer, every
time.

The sentence to remember: **the demo would still block with the reviewer removed
entirely. It would not block with the scanners removed.**

---

## 2 · The twenty minutes

```
slides          10:20   17 slides — Sorour
live demo        5:00   one poisoned ticket, blocked
questions        4:40   all five of us
```

The slot includes the questions. If a question comes up mid-talk, answer it in one
sentence and offer the rest at the end.

---

## 3 · The deck, slide by slide

| # | Slide | What it says, in one line |
|---:|---|---|
| 1 | The Agent Org | security gates for agent-written code |
| 2 | RosettaTeam | who we are: title and workplace for each of us |
| 3 | Twenty minutes, in this order | the agenda, with minutes |
| 4 | An agent can write the code. Who checks it? | with no checks, a poisoned change reached the branch every time |
| 5 | Nine stages. Three of them are people. | five agents, three human gates, one rule that is not a model |
| 6 | Non-deterministic models. A deterministic gate. | **the core idea** — the reviewer advises, the scanners decide |
| 7 | How a finding becomes a verdict | the scoring: one table, gitleaks is always critical, unknowns cannot pass |
| 8 | What runs where | the AWS architecture, eight numbered steps |
| 9 | What it costs, and what it buys | 1.3–1.7¢ per change, 7.88 min median, 0 of 12 merged changes carried a credential |
| 10 | Every vendor's AI review is advisory | their own documentation says their AI review never blocks |
| 11 | When their check breaks, the change goes through | three products that let a change through when the check itself fails |
| 12 | What is built | 2176 + 338 tests, five runtimes on one version, a real product, multi-tenant |
| 13 | Your ten notes from the pre-final | every note answered |
| 14 | What this does not do | four honest limits |
| 15 | What is next | four next steps; the first answers the last limit |
| 16 | The demonstration | hand over to the live product |
| 17 | Thank you | questions |

---

## 4 · The numbers to know

| Number | What it means | Where it comes from |
|---|---|---|
| **1.3–1.7¢** | model cost of one change | three clean runs, priced from the AWS Pricing API |
| **99.9%** | the model's share of that cost; the rest came to $0.0000125 | the same three runs |
| **7.88 min** | median time from ticket to merged pull request; a person clicked every gate | 12 merges, measured 25 September |
| **12 of 57** | runs that reached a merge (the rest were blocked, refused or unfinished) | GitHub's own records |
| **0 of 12** | merged changes carrying a credential | the same scan finds 3 in unmerged pull request #72, so the zero is real |
| **2 min 19 s** | the poisoned run, from approving gate1 to the block | run 35679536930 |
| **lines 3 and 4** | where the real scanners report the planted key | the stand-in reports 4 and 5 — that pair proves a real scan |
| **2176** | automated tests, across 95 files | plus **338** for the web app, across 26 files |
| **v55** | the version all five agent runtimes are on (deployed 25 September with the scanner fix) | checked before every demo |
| **6/8 → 8/8** | the reviewer catching a plan mismatch, before and after adding its knowledge base | measured over 8 trials each |
| **8 of 83** | code modules that touch a vendor SDK; only 2 load one at start-up | measured by reading the code |
| **20 in a row** | consecutive poisoned runs that all blocked, in the determinism test | the test suite |

---

## 5 · The demo

1. The run list, signed in. Point at the promoted run with a merged pull request: that is
   the clean half, already done.
2. **Start a run**: pick the repository, tick *Demonstrate a blocked run*, press Start.
3. A new row appears. **Approve gate1.**
4. The develop stage takes about two minutes. Sorour explains: the reviewer is advisory,
   the scanners are not.
5. **The block**: status blocked, two findings, provenance `scanners`, lines 3 and 4.

**The screen marks the block on security, as the slide does.** The line runs green
through develop. Review shows a hollow rose mark: the reviewer asked for changes, which
is advisory, so the line carries on. It stops in filled rose at security. The security panel shows
`critical ≥ high` and one card per scanner, each with its own worst finding against the
threshold; gitleaks is the one that blocks. A run that passes shows the same comparison
the other way round, for example `low < high`.

**If the live run stalls:** open the earlier poisoned run, 35679536930, and say plainly it
is an earlier run. There is no recording in the deck.

---

## 6 · Who answers what

When a question lands in your area, take it. Answer in two or three sentences, then stop.
If it is not yours, pass it by name: "Habiba built that part."

### Mohamed Sorour — the architecture, AWS, the product, the cost
Slides 1–9 and 12. The eight numbered steps on the architecture slide; why AgentCore;
how multi-tenancy works; what a run costs and why.
> "Every AWS step takes a role through OIDC. There is not one stored AWS key anywhere."

### Habiba Megahed — the security scanners and the scoring
Slides 6, 7 and 11. The three scanner wrappers, the scoring table, and the difference
between a scanner that is missing and one that is broken.
> "My findings are what block the poisoned ticket. I deliberately do not return a
> verdict: the decision is one comparison — arithmetic, not judgement."

### Mariam Abdelkader — GitHub, the workflows, the deploy
Slides 5 and 8 (steps 2, 3 and 7). How an issue starts a run, the seven jobs and the
three gates, the pull request and issue comments, and how the five runtimes are deployed.
> "Everything a judge can see on GitHub, my code wrote."

### Reem Shkeep — the app the agents edit, the tickets, the baseline
Slides 4 and 16. The subject app, the two tickets — the same feature request, one clean
and one carrying a key — and the baseline with no checks at all.
> "The two tickets ask for the same feature. One ships and one is refused. That makes the
> demo a comparison, not a claim."

### Aya Ebrahim — determinism, failure testing, the metrics
Slides 6 and 9. The twenty-in-a-row determinism test, what happens when a gate is never
answered, and the numbers on the cost slide.
> "A demo that blocks once proves nothing. I run the poisoned ticket twenty times in a row
> and every one blocks."

---

## 7 · Questions judges are likely to ask

**"Can a gate be skipped?"** — *Mariam.* Yes, by a repository admin. It is a GitHub
setting; our pre-flight check reports it before every demo. We report it rather than hide
it.

**"How do you know the scanners really ran?"** — *Habiba.* The line numbers. The real
scanners report the key at lines 3 and 4 of the added code; the built-in stand-in, used
only if a scanner is unavailable, reports 4 and 5. Everything else looks identical, so we
check the pair.

**"What if the scanners miss something?"** — *Habiba.* Then only the reviewer saw it, and
the reviewer is advisory. The human gates are the last line. We state that limit rather
than overclaim.

**"What if a scanner crashes?"** — *Habiba.* The change is blocked. A crashed or missing
scanner on the deployed system produces a blocking finding, never an empty result — an
empty result would read as a pass.

**"The security agent calls an AI model — so the gate uses a model?"** — *Habiba.* No. The
scanners and the fixed threshold decide first. Only then does the agent ask the model to
write one sentence explaining the verdict, and that text goes into a separate field that
cannot change the decision. Take the model away and the block still happens.

**"Why is the reviewer only advisory?"** — *Sorour.* Because it is a model, and a model can
be wrong or talked into things. When it objects, the change goes back to the developer,
but it cannot stop the run. Only the scanners can.

**"Could someone prompt-inject the agents?"** — *Sorour.* They could influence the
developer or the reviewer. They cannot influence the block: the security decision has no
model in it, so there is nothing to talk to.

**"Did the code actually change?"** — *Sorour.* The pull request carries the reviewed change
as a file; every gate and the scanners read it. Applying it to the source is our first
next step. Say it plainly — it is on slide 14.

**"How is the webhook protected?"** — *Mariam.* GitHub signs each delivery, and the Lambda
checks the signature before it does anything else. An unsigned or wrongly signed request
is refused before it can start anything.

**"Why Bedrock AgentCore and not Lambda?"** — *Sorour.* Each agent runs in its own isolated
runtime with its own identity and logs, and only the security runtime carries the three
scanners.

**"How does multi-tenancy work?"** — *Sorour.* Each customer's data sits in its own
partition in DynamoDB, and the app reads through a role that AWS itself limits to that
partition. A read of another tenant's data is refused by AWS, not by our code.

**"What happens if the model is down?"** — *Aya.* Every agent falls back to a canned answer
instead of failing, and the run records that it did: the run page says "a stage used a
canned answer". A run that was not really measured never looks like one that was.

**"Is it deterministic?"** — *Aya.* The decision is. Twenty poisoned runs in a row all
blocked, and the scoring is one fixed table. The agents' text is not deterministic — that
is exactly why they do not decide.

**"Is it cheaper than a human reviewer?"** — *Sorour.* We would not make that comparison.
The pipeline does not replace the reviewer; it decides what reaches them.

**"What makes this different from GitHub Copilot or Claude Code?"** — *Sorour.* Their AI
review is advisory by their own documentation. Where they do enforce rules, the rule
guards one agent's actions. Ours guards the hand-off between agents, with a named person
approving each stage.

**"Why does a clean run sometimes not merge?"** — *Reem.* The reviewer is a real model: if
the change does not do what the ticket asked, it sends it back, up to three times, and the
run ends without merging. That is the reviewer doing its job.

**"Can we try it?"** — *Sorour.* Yes: theagentorg.rosettacloud.app. Sign up, and you get your
own empty workspace — you cannot see anyone else's runs.

---

## 8 · What we never claim

- That the merged change is applied to the source. It is carried as a file; applying it is next.
- That the generated tests and the browser tests check each other. Not yet.
- That a gate cannot be bypassed. An admin can, and we say so.
- That the scanners catch logic bugs. They catch credentials, known vulnerabilities and unsafe code.
- That we are the only deterministic check on the market. We are the only one we found that
  guards the hand-off between agents, with a person at each gate.

---

## 9 · Before the finals

- [ ] Everyone: read sections 1–5; study your part in section 6; practise your answers aloud
- [ ] Sorour: one timed rehearsal on the presenting laptop, with the HDMI adapter
- [ ] Sorour: the morning of, run the pre-flight check and confirm the numbers on the slides
- [ ] Sorour: sign in to the product and open the earlier poisoned run, ready as the fallback
- [ ] Optional: record the clean and the poisoned run as a backup video
