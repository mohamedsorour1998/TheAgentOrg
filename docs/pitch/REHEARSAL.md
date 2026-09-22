# Rehearsal — DevOps Hackathon Finals, 26 September 2026

**TD63 RosettaTeam · Glass Room 1 · 3:50 PM · Creativa Giza**
Speaker: Sorour, alone. All five present; all five take questions in their own area.

**The slot is 20 minutes and it includes the judges' questions.** Budget:

```
slides      9:30
live demo   5:00
questions   5:30
```

Verify the sum rather than trusting it:

```zsh
python3 - <<'PY'
import re, pathlib
t = pathlib.Path('pitch/REHEARSAL.md').read_text()
total = sum(int(m)*60+int(s) for _,_,m,s in
            re.findall(r'^## ([\d–\-]+) · ([^—\n]+)—\s*(\d+):(\d+)', t, re.M))
print(f"{total//60}:{total%60:02d} of slides -> {20 - total/60 - 5:.1f} min for questions")
PY
```

**Read every number off the slide, never from memory.** If a figure has moved since
the last build, rebuild the deck rather than correcting it aloud.

---

## 1 · Title — 0:20

**SHOW** Security gates for agent-written code.

**SAY** Good afternoon. We are RosettaTeam. Our project is called The Agent Org.
Five AI agents take a ticket all the way to a merged pull request — and three human
gates and one deterministic rule decide whether it ships. I have twenty minutes
including your questions, so I will keep the slides short and spend five of those
minutes showing you the thing running.

---

## 2 · An agent can write the code. Who checks it? — 0:45

**SHOW** Three bullets, then the line in cyan.

**SAY** Coding agents already open pull requests without a person in the loop. What
is new is that the reviewer of that change is increasingly another model. A model
can be persuaded, distracted, or prompt-injected — which makes it the wrong thing
to place between a committed credential and your default branch. We measured our
own baseline with no checks in the loop: a poisoned change reached the branch every
single time. So the question is not whether an agent can write the change. It is
what stands between that change and production.

**IF ASKED — "isn't this what branch protection is for?"** Branch protection is a
status check, and every product in our research publishes a *neutral* status. We
will come back to that on the differentiation slide.

---

## 3 · Nine stages. Three of them are people. — 0:45

**SHOW** The nine-stage spine. Point at the rings.

**SAY** This is the pipeline, and it is the same shape you will see on screen in a
few minutes. Filled dots are agent stages. The three rings are gates — the pipeline
stops there until a named reviewer approves it in GitHub. Five agents: planner,
developer, reviewer, security, SRE. Three human gates. And one rule that is not a
model, which is the next slide.

---

## 4 · Non-deterministic models. A deterministic gate. — 1:00

**THE MOST IMPORTANT SLIDE. Do not rush it.**

**SAY** Every agent here is a language model and every one of them can be wrong. So
the component that stops a change is deliberately not one of them. Look at the last
two rows. The reviewer is a model reading the diff — it catches intent and logic and
plan mismatch, and it is *advisory*: when it objects, the pipeline loops, it does not
stop. The security stage is three scanners and a fixed severity comparison. Same
input, same answer, every time, and there is no model in it — so there is nothing to
argue with.

And the block is not a status check. The develop stage exits with code 3, and the
next gate declares that it *needs* that stage. So the refusal is a dependency edge
in the graph. There is no condition to misconfigure and no required check for
somebody to forget to tick.

**SAY — the line that lands** The demo you are about to see would still block with
the reviewer removed entirely. It would not block with the scanners removed.

**IF ASKED — "what if the scanners miss something?"** Then the reviewer is the only
thing that saw it, the reviewer is advisory, and the change can reach main past three
human gates. That is an accepted limit, not a defended one — and it is why the gates
require a named person.

---

## 5 · What runs where — 1:05

**SHOW** Six lanes: trigger, orchestration, agents, the gate, state, the product.

**SAY** Top to bottom. An issue opens on the target repository; a Lambda verifies an
HMAC signature and publishes to EventBridge, which dispatches the workflow — with a
dead-letter queue, which has earned its keep once already. GitHub Actions runs seven
jobs, and three of them are Environments with required reviewers: those Environments
*are* the gates. Each agent call goes to its own Bedrock AgentCore runtime — five
runtimes, one arm64 image, differing only by a role variable — and the model is Nova
2 Lite. The security lane is the one with no AWS icon on purpose: three scanners and
our own comparison. State is two DynamoDB tables, one for the audit trail and one
for tenancy. And the product on top is Cognito, Amplify server-side rendering, and
Next.js.

Fourteen AWS services. Every step assumes a role through OIDC — there is not a single
static AWS key anywhere in the project.

**IF ASKED — "why AgentCore and not just Lambda?"** Each agent is an isolated runtime
with its own identity and its own log group, and the security image is the only one
carrying the three scanner binaries. Isolation is the point.

---

## 6 · What it costs, and what it buys — 0:50

**SAY** A change costs between one and one-point-seven cents of model time. That is
measured over three consecutive clean runs, priced from the AWS Pricing API. The
infrastructure beside it is twelve millionths of a dollar — the model is essentially
all of the marginal cost, which is why the only cost work worth doing is prompt
caching, and our cache hit rate is a measured zero today.

Median ticket to merge is five point four minutes over eight merges. And zero of
those eight merged changes carried a credential.

**SAY THE CAVEATS — do not wait to be asked.** Eight merges out of thirty-seven runs,
so that median is over the ones that finished. And the zero has a positive control:
the same scan finds three credentials in an unmerged pull request, so it is a
measurement rather than a broken grep.

**IF ASKED — "so it is cheaper than a human reviewer?"** I would not make that
comparison. One cent against an hourly rate is not like-for-like: the pipeline does
not replace the reviewer, it decides what reaches them. The honest quantity is
minutes of human attention per change, and we have not measured that at scale.

---

## 7 · Every vendor's AI review is advisory. They say so. — 0:50

**SHOW** The table of their own documentation.

**SAY** This is the row we are most confident about, and none of it is our wording.
GitHub Copilot's review "will not block merging changes". Anthropic's managed code
review "always completes with a neutral conclusion so it never blocks". OpenAI's
Codex rules "don't replace tests, branch protections, or required approvals".
Cursor's findings default to neutral. And Snyk's own platform page argues our thesis
in four words: the generator cannot be the validator.

**SAY — and concede, because it is stronger** We are not the only deterministic gate
and I want to be precise about that. Claude Code's permission rules are enforced by
the harness rather than the model and hold even under a bypass flag. Factory blocks a
commit outright. The distinction is the *seam*: every gate they ship guards a tool
call inside one agent's session. Ours guards a pipeline stage between agents, with a
named human reviewer, and the block is a dependency edge.

**IF ASKED — "so what is genuinely unique?"** No product we found combines all three:
multi-agent generation, a deterministic non-model block on stage output, and human
approval gates between stages, as one pipeline. Individually, each exists.

---

## 8 · Three shipped products fail open — 0:40

**SAY** And this is the argument for the whole design. Cursor's hooks: exit code 2
denies, any other exit code and the action proceeds — fail-open by default. Claude
Code: exit 2 blocks, but exit 1 does not, and a mistyped path silently disables the
gate. Semgrep: on an internal crash it sends a crash report and returns exit code
zero. A check that did not run, reading as a check that passed. That is the one
defect shape we built this to refuse — which is why in our pipeline a missing scanner
raises rather than returning an empty list of findings.

---

## 9 · What is built — 0:45

**SAY** Two thousand one hundred and seventy-one automated tests across ninety-three
files, plus three hundred and eleven for the web application. Five agent runtimes
deployed, all reporting ready at the same version — a split version would mean a
partial deploy and we check for it before every demo. It runs in the cloud on a real
issue. It is a product, not a script: you sign in with GitHub, pick a repository,
start a run, watch it, approve a gate, read what it cost. And it is multi-tenant,
with isolation enforced by the credential rather than by application code — AWS
refuses another tenant's data, our code does not have to remember to.

---

## 10 · Your ten notes from the pre-final — 1:00

**THE SLIDE THAT EARNS THE MOST GOODWILL. Slow down.**

**SAY** You gave us ten notes in the pre-final. Nine of them have shipped answers, and
I want to call out one in particular. You asked how gitleaks and Trivy scoring
actually produces a go or no-go, because you doubted the determinism claim. You were
right to. It was exactly true for Trivy and Semgrep, which map their own severities —
and it was *vacuously* true for gitleaks, which had the word "critical" hardcoded at
the point a finding was constructed. Three tables in three files, one of which was not
a table at all. That is now one policy table, with a floor derived from it rather than
written down twice. Your note produced a real correction, and I would rather say that
than claim we were right all along.

**IF ASKED about any single row** — the backup slides after the close cover scoring
and limits in detail; ask and I will jump to them.

---

## 11 · What is next — 0:45

**SAY** Four things, and the first two are honest gaps. Today a merged pull request
carries the reviewed diff as an artifact rather than applying it to the source —
applying it is the next step, and it is also what would let a generated test run
against the change. Second: we generate a test from the ticket on every run, and
Selenium runs in CI against the application, but neither verifies the other yet. Then
prompt caching, where our measured zero is the largest silent cost in the design. And
more languages and more scanners — the gate is a table, so adding a scanner is a row.

---

## 12 · The demonstration — 0:15

**SAY** Let me show you the half that matters: a ticket that deliberately carries a
credential.

→ **switch to the browser. See DEMO-PLAN.md.**

---

## 13 · RosettaTeam — 0:20

**SAY** Five engineers. We divided the work by file rather than by feature, which is
how fourteen parallel workstreams landed without collisions. They are all here and
they will take questions in their own areas.

---

## 14 · Thank you — 0:10

**SAY** That is The Agent Org. It is live at theagentorg.rosettacloud.app. Questions.

---

# Contingencies

| If | Then |
|---|---|
| the live run stalls | play the recording on the same slide — say "this is a recording" out loud |
| the venue network fails | everything needed is in the deck; nothing is fetched at runtime |
| a judge asks for the clean run | open the merged pull request, then play the 90-second recording |
| a video will not play | press Escape; the poster frame is the LAST frame, so narrate the still |
| a gate approval 403s | say the token needs `deployments: write` and move on; do not debug on stage |
| you are running long | cut slide 8 (fail-open) and slide 13 (team) — 1:00 back |

# Questions to have answers ready for

**"Can a gate be skipped?"** Yes — by a repository admin. It is an operator setting,
our pre-flight check prints it on every run, and we chose not to fail on it. Say this
before being asked if the topic comes near.

**"How do you know the scanners really ran?"** The line numbers. Real scanners report
lines 3 and 4 of the added lines; our stand-in fixture reports 4 and 5. That pair is
the only field that separates the two, and the pre-flight check asserts it.

**"Did the code actually change?"** The pull request carries the reviewed diff as an
artifact — every gate reads it, the scanners read it, the humans approve it. Applying
it to the source is the next roadmap item. Say it plainly; it is on the roadmap slide.

**"Why should I trust a number on your slide?"** Every figure is a constant in the
build script annotated with the command that produced it, and the build fails if a
required section is missing or the prose slips. I can show you the script.

**"What happens if the model is unavailable?"** Every agent degrades to a fixture
rather than erroring, and the run records that it did — there is a field for it, and
the UI shows "a stage used a canned answer" in red. An unmeasured run must never read
as a measured one.
