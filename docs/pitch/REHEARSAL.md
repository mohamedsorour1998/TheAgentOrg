# Rehearsal — DevOps Hackathon Finals, 26 September 2026

**TD63 RosettaTeam · Glass Room 1 · 3:50 PM · Creativa Giza**
Speaker: Sorour, alone. All five present; all five take questions in their own area.

**This file is also the deck's speaker notes.** `build_deck.py` copies each section's
SAY and IF ASKED text into that slide's notes, so Presenter View shows exactly what is
here — and the build fails if a section's title is not on its slide. Edit here, then
rebuild; never edit notes in PowerPoint.

**The slot is 20 minutes and it includes the judges' questions.** Budget:

```
slides     10:20   (17 slides)
live demo   5:00
questions   4:40
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

## 1 · The Agent Org — 0:15

**SHOW** The project's name, and one line on what it is.

**SAY** Good afternoon. This is The Agent Org: security gates for agent-written code.
Five AI agents take a ticket all the way to a pull request, and three human gates and
one deterministic rule decide whether it ships. I have twenty minutes including your
questions, so the slides are short and five of those minutes are the system running.

---

## 2 · RosettaTeam — 0:25

**SHOW** Five photographs, each with a title and a workplace. This is the speaker page.

**SAY** We are RosettaTeam. I am Mohamed Sorour, a senior DevOps engineer at Vezeeta
and a master's student in computer science, AI specialization, at Georgia Tech. Mariam
is an associate solution engineer at RENOSYSTEMS. Habiba is a junior DevOps engineer,
and Reem and Aya are junior testing engineers, all three Digilians alumni. All five of
us are here, and each of us takes questions on our own part.

---

## 3 · Twenty minutes, in this order — 0:20

**SHOW** Eight rows: each section, one line on what it covers, and its minutes.

**SAY** Here is the twenty minutes. Three on the overview, then the architecture, the
business impact, and how this differs from what vendors ship. Then what is built, your
ten notes from the pre-final, and what it does not do. What is next, five minutes live,
and the rest of the slot is yours.

---

## 4 · An agent can write the code. Who checks it? — 0:40

**SHOW** Three points, then the question in bold.

**SAY** Coding agents already open pull requests without a person in the loop. What is
new is that the reviewer of that change is increasingly another model. A model can be
persuaded, distracted or prompt-injected, which makes it the wrong thing to place
between a committed credential and your main branch. We measured our own baseline with
no checks in the loop: a poisoned change reached the branch every time. So the question
is not whether an agent can write the change. It is what stands between that change and
production.

**IF ASKED — "isn't this what branch protection is for?"** Branch protection enforces what
it is told to. The AI reviewers we checked leave a comment or report a neutral status by
default, so requiring them does not stop a merge. We come back to that on the
differentiation slide.

---

## 5 · Nine stages. Three of them are people. — 0:40

**SHOW** The nine stages. Point at the rings.

**SAY** This is the pipeline, and it is the same shape you will see on screen in a few
minutes. The filled dots are agents. The three rings are gates: the run stops there until
a named person approves it in GitHub. Five agents: planner, developer, reviewer,
security and SRE, each in its own runtime. Three human gates. And one rule that is not
a model, which is the next slide.

---

## 6 · Non-deterministic models. A deterministic gate. — 1:00

**THE MOST IMPORTANT SLIDE. Do not rush it.**

**SAY** Every agent here is a language model, and any of them can be wrong. So the
component that stops a change is deliberately not one of them. Compare the two columns.
The reviewer is a model reading the change. It catches intent, logic and a plan
mismatch, and it is advisory: when it objects, the change goes back to the developer,
but it cannot stop the run. The security stage is three scanners and a fixed rule. Same
input, same answer, every time, and no model takes part in the decision, so there is
nothing to argue with.

**IF ASKED — "but the security agent calls a model?"** Only to write the sentence that
explains the verdict, after the verdict is decided. That text cannot change it.

And the block is part of the pipeline's structure, not a status check someone has to
remember to require. When security refuses, its job fails, and the next gate is built
so that it cannot start without it.

**SAY — the line that lands** The demo you are about to see would still block with the
reviewer removed entirely. It would not block with the scanners removed.

**IF ASKED — "what if the scanners miss something?"** Then the reviewer is the only
thing that saw it, the reviewer is advisory, and the change can reach main past three
human gates. That is an accepted limit, not a defended one, and it is why each gate
needs a named person.

---

## 7 · How a finding becomes a verdict — 0:45

**SHOW** Three scanners, how each one's severity is decided, and four rules.

**SAY** This is the rule that decides, since you asked about it in the pre-final.
Semgrep and Trivy report their own severities, and one table translates them. Gitleaks
reports no severity at all, so a secret is critical by policy: a committed credential
has no lesser grade. Then one comparison: block when any finding is at or above the
threshold. Two things keep it honest. A severity the table does not recognise counts as
high, the blocking level, so an unknown can never slip through. And a threshold outside
the allowed values is rejected, never quietly adjusted.

**IF ASKED — "why is gitleaks always critical?"** It reports no severity at all, only
the rule, the file and the line. The severity has to come from somewhere, and ranking
credentials would mean deciding which ones we are willing to merge.

**IF ASKED — "what does Trivy's 'unknown' become?"** Low. Trivy uses it for a
vulnerability with no published score, which is a real answer, not a missing one. What
counts as high is a severity word the table has never seen.

---

## 8 · What runs where — 1:10

**SHOW** The AWS diagram. Point at each number as you say it.

**SAY** Follow the numbers. One: an engineer opens an issue, or starts a run from our
app. Two: GitHub sends a signed webhook to a Lambda function, which checks the signature
before anything else happens and hands the event to EventBridge. Three: a rule matches
new issues and starts the pipeline; if that fails, the event waits in an SQS dead-letter
queue. The pipeline is seven GitHub Actions jobs. Four: each agent job takes an AWS role
through OIDC, so there is no stored AWS key anywhere, and calls its agent on Bedrock
AgentCore: planner, developer, reviewer, security, SRE. Five: the agents call Amazon
Nova 2 Lite. Six: the security stage decides with three scanners and a fixed threshold,
no model; the model only writes the explanation afterwards. Seven: the gates wait for a
named person, in GitHub or in our app. Eight: the app, on Amplify, reads each run from
DynamoDB through a role that can only see its own tenant's data.

**IF ASKED — "the security agent calls the model too?"** Yes, for one thing: the
sentence explaining the verdict. The verdict is computed first, by the scanners and the
threshold, and the model's text goes into a separate field that cannot change it. Take
the model away and the block still happens.

**IF ASKED — "why AgentCore and not just Lambda?"** Each agent is an isolated runtime
with its own identity and logs, and the security image is the only one carrying the
three scanners. Isolation is the point.

**IF ASKED — "where is a run's state kept?"** It is passed from job to job by GitHub
Actions. The four jobs that hold an AWS role (plan, develop, SRE and promote) write a
copy to DynamoDB, which is what the app reads. The gates hold no AWS credentials on
purpose, so a gate's decision reaches DynamoDB with the next job.

**IF ASKED — "how do people sign in?"** Email through Amazon Cognito, or GitHub.

---

## 9 · What it costs, and what it buys — 0:45

**SHOW** Three figures, then two points and the honest caveat.

**SAY** A change costs between one point three and one point seven cents of model time,
measured over three clean runs and priced from the AWS Pricing API. The model is 99.9
percent of that. Lambda, EventBridge and DynamoDB together came to twelve millionths of
a dollar, which is why the only cost work worth doing is prompt caching. The median
time from ticket to merge is just under eight minutes, and none of the twelve merged
changes carried a credential.

**SAY THE CAVEAT — do not wait to be asked.** Twelve of fifty-seven runs merged, so the
median covers the runs that finished, and a person clicked every gate in it. And the zero is a real result: the same scan
finds three credentials in an unmerged pull request, so it is not a scan that finds
nothing.

**IF ASKED — "so it is cheaper than a human reviewer?"** I would not make that
comparison. The pipeline does not replace the reviewer, it decides what reaches them.
The honest measure is minutes of human attention per change, and we have not measured
that at scale.

---

## 10 · Every vendor's AI review is advisory. They say so. — 0:45

**SHOW** The table of their own documentation.

**SAY** None of this is our wording. GitHub Copilot's review leaves, by default, "a
Comment review, not a Request changes review", so it never blocks. Anthropic's code
review "always completes with a neutral conclusion so it never blocks merging". OpenAI's
Codex guidance says it doesn't "replace tests, branch protections, or required
approvals". Cursor's findings "default to neutral". And Snyk's own page puts our thesis
in six words: the generator cannot be the validator.

**IF ASKED — "can't Copilot approve pull requests now?"** Yes, if a team turns that on.
That lets the model pass a change; it still cannot block one. It makes our point
sharper, not weaker.

**SAY — and concede, because it is stronger** We are not the only ones with rules
outside the model. Claude Code's permission rules and Factory's commit blocks are
enforced outside the model too. The difference is where the check sits: theirs guard
one agent's actions; ours guards the hand-off between agents, with a named person
approving each stage.

**IF ASKED — "so what is genuinely new?"** No product we found combines all three:
several agents, a check with no model in it on each agent's output, and human approval
between stages, as one pipeline. Each of the three exists somewhere on its own.

---

## 11 · When their check breaks, the change goes through — 0:30

**SHOW** Three products, three ways a broken check lets a change through.

**SAY** This is the argument for the whole design. With Cursor's hooks, if the hook
itself fails, the action goes ahead; their documentation calls it fail-open by default.
With Claude Code's hooks, only one specific failure blocks; any other lets the action
through, and in their words, "a mistyped path in settings.json leaves the gate silently
disabled". And by default, if Semgrep crashes, it still reports success. A check that did not run, reading as a check that passed. That is
exactly what we refuse: in our pipeline, a missing or crashed scanner blocks the change.

---

## 12 · What is built — 0:40

**SHOW** Three figures and three cards.

**SAY** Two thousand one hundred and seventy-six automated tests across ninety-five
files, plus three hundred and thirty-eight for the web app. Five agent runtimes, all ready on
the same version; a split would mean a half-finished deploy, and we check for it before
every demo. It runs in the cloud from a real issue. It is a product, not a script: you
sign in, pick a repository, start a run, watch each stage, approve a gate and read what
it cost. And it is multi-tenant: each customer's data sits in its own partition, and
AWS itself refuses a read of anyone else's, so our code does not have to remember to.

---

## 13 · Your ten notes from the pre-final — 0:50

**THE SLIDE THAT EARNS THE MOST GOODWILL. Slow down.**

**SAY** You gave us ten notes in the pre-final, and every row here is an answer that
now exists. One in particular. You asked how the scanners' scores turn into a go or
no-go, because you doubted the determinism claim, and you were right to. It held for
Trivy and Semgrep, which report their own severities. For gitleaks the word "critical"
was simply typed where each finding was created: no table, no policy. There is now one
scoring table for all three scanners, and the rule for secrets is written down as a
policy. Your note produced a real correction, and I would rather say so than claim we
were right all along.

**IF ASKED about any single row** — scoring is slide 7 and the limits are slide 14; ask
and I will go back to either.

---

## 14 · What this does not do — 0:35

**SHOW** Four limits, in the same layout as the roadmap. Say them before it, not after.

**SAY** And what it does not do. A repository admin can bypass a gate; it is a setting,
and our pre-flight check reports it on every run. If the scanners miss something, only
the reviewer saw it, and the reviewer cannot stop the run, so the human gates are the
last line. It covers one language and three scanners today, and breadth is where every
competitor is ahead. And a merged pull request carries the reviewed change as a file;
applying it to the source is next.

---

## 15 · What is next — 0:40

**SHOW** Four items. The first is the last limit, answered.

**SAY** Four things. First, apply the change rather than carry it; that is also what
lets a generated test run against the change. Second, join the two test layers: tests
are generated from each ticket and Selenium runs in CI, but neither checks the other yet.
Third, prompt caching: nothing is cached today, so every agent pays full price for the
same repository snapshot, and cached input costs a quarter as much. And more languages
and more scanners; the scoring is a table, so a new scanner is one row.

---

## 16 · The demonstration — 0:10

**SAY** Let me show you the half that matters: a ticket that deliberately carries a
credential. Watch the line numbers when it blocks.

**IF ASKED — "what are the three cards?"** One per scanner, each showing its own worst
finding against the threshold. Only gitleaks reached it, so only gitleaks blocks. That
comparison is the whole decision, and a run that passes shows it too.

→ **switch to the browser. See DEMO-PLAN.md.**

---

## 17 · Thank you — 0:10

**SAY** That is The Agent Org. It is live at theagentorg.rosettacloud.app. Questions.

---

# Contingencies

| If | Then |
|---|---|
| the live run stalls | open the poisoned run from earlier (run 35679536930) in the product and its pull request, and say plainly that it is an earlier run |
| the venue network fails | the deck needs nothing from the network; walk slide 16's stages and describe the block in words |
| a judge asks for the clean run | open the merged pull request and its promoted run in the list — both already exist |
| a gate approval is refused | say the token needs the deployments permission and move on; do not debug on stage |
| you are running long | say slide 11 in one sentence and slide 14 as its first point, about 0:45 back. The team slide stays: it is the speaker page |

**There is no recording in the deck.** Recording the two runs is still an open task; until it
is done, the earlier run in the product is the fallback.

# Questions to have answers ready for

**"Can a gate be skipped?"** Yes, by a repository admin. It is a setting, our pre-flight
check prints it on every run, and we chose to report it rather than fail on it. Say this
before being asked if the topic comes near.

**"How do you know the scanners really ran?"** The line numbers. The real scanners
report the credential at lines 3 and 4 of the added code; the built-in stand-in, used
only when a scanner is unavailable, reports 4 and 5. That pair is the only thing that
tells the two apart, and our pre-flight check confirms it against the deployed system.

**"Did the code actually change?"** The pull request carries the reviewed change as a
file: every gate reads it, the scanners read it, the humans approve it. Applying it to
the source is the first roadmap item. Say it plainly.

**"Why should I trust a number on your slide?"** Every figure is a constant in the
script that builds the deck, next to the command that produced it, and the build fails
if a required section is missing. I can show you the script.

**"What happens if the model is unavailable?"** Every agent falls back to a canned
answer instead of failing, and the run records that it did: the run page says "a stage
used a canned answer". A run that was not really measured must never look like one that
was.
