# The demonstration — what it shows and how it is run

**TD63 RosettaTeam · DevOps Hackathon Finals, 26 September 2026**
Five minutes, live, from the presenting laptop, in the product at
`theagentorg.rosettacloud.app`.

---

## What is being demonstrated

**One ticket that deliberately carries a credential, walked through the pipeline
until the deterministic rule refuses it.**

The clean half, a change that passes and merges, is shown as evidence that already
exists rather than performed live: a merged pull request and a promoted run in the run
list. Both are real and can be opened. Running it live would take about six minutes of a
twenty-minute slot, which is why it is not.

---

## The claim the demo is making

A language model writes the change. A different language model reviews it. Neither of
them decides whether it ships. The component that refuses it contains no model: three
scanners and a severity comparison. And the refusal is part of the pipeline's structure,
so no forgotten setting can let it through.

---

## The five minutes, in order

| | What happens | About |
|---|---|---|
| 1 | The run list, already signed in. One promoted run with a merged pull request is visible: that is the clean half, already done | 0:20 |
| 2 | **Start a run**: choose the repository, tick *Demonstrate a blocked run*, press Start | 0:20 |
| 3 | The form closes and a new row appears with its issue number. The pipeline starts a few seconds later | 0:40 |
| 4 | **gate1 — approve.** A gate is a GitHub Environment with a required reviewer; the pipeline waits there for a person | 0:30 |
| 5 | The develop stage runs: about **2 min 19 s**, measured on run 35679536930. Use the time to explain that the reviewer is advisory and the scanners are not | 2:20 |
| 6 | **The block.** Status `blocked`, two blocking findings, provenance `scanners`, and nothing after it runs | 0:50 |

**On screen, the block is marked on `security`, as on the slide.** The line runs green
through develop; review shows a hollow rose mark, because the reviewer asked for changes,
and that is advisory, so the line carries on; it stops in filled rose at security, and
nothing after it runs. The
security panel opens on its own: `critical ≥ high`, then one card per scanner, each
showing its own worst finding against the threshold. Point at the gitleaks card, the one
that blocks, then at the two line numbers in the findings table.

## The one field that proves it

The real scanners report the credential at **added lines 3 and 4**. The built-in
stand-in, used only when a scanner is unavailable, reports **4 and 5**. That pair is the
only thing that tells a real scan from the stand-in: the verdict, the count, the rule
names, the file and the severity are identical either way.

Our pre-flight check confirms the pair against the deployed system before every demo.

---

## What the audience can check afterwards

- The issue, on the target repository, with the pipeline's own comments on it
- The GitHub Actions run, with `develop` failed and everything after it skipped
- The pull request, carrying the reviewed change
- The same verdict in the product, with the two line numbers

---

## Contingency

| Condition | Action |
|---|---|
| The run stalls or the network fails | Open the poisoned run from 22 September (run 35679536930) in the product and its pull request. Say plainly that it is an earlier run |
| A gate approval is refused | Say the token needs the deployments permission; do not debug on stage |
| The venue machine is used instead of ours | The deck needs nothing from the network; the product needs the network and a signed-in session |
| Time runs short | Stop after step 5 and show the earlier run's block instead |

**There is no recording in the deck.** Recording the two runs is still an open task.

---

## What is NOT claimed

- **The merged pull request carries the reviewed change as a file**; it does not apply
  it to the application source. Applying it is the first roadmap item.
- **The generated tests and the browser tests do not check each other yet.** Tests are
  generated from each ticket; Selenium runs in CI against the application.
- **A repository admin can bypass a gate.** It is a setting, and our pre-flight check
  reports it on every run.
