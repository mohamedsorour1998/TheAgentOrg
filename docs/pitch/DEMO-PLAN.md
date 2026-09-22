# The demonstration — what it shows and how it is run

**TD63 RosettaTeam · DevOps Hackathon Finals, 26 September 2026**
Five minutes, live, from the presenting laptop. A recording of the same run is
embedded in the deck as a fallback.

---

## What is being demonstrated

**One ticket that deliberately carries a credential, walked through the pipeline
until the deterministic rule refuses it.**

The clean half — a change that passes and merges — is shown as evidence that
already exists rather than performed live: a merged pull request and a promoted run
in the list. Both are real and openable. Running it live costs about six minutes of
a twenty-minute slot, which is why it is not.

---

## The claim the demo is making

A language model writes the change. A different language model reviews it. Neither
of them decides whether it ships. The component that refuses it contains no model:
three scanners and a severity comparison. The refusal is expressed as a dependency
between jobs, so no configuration mistake can let it through.

---

## The five minutes, in order

| | What happens | About |
|---|---|---|
| 1 | Runs list, already signed in. One promoted run with a merged pull request is visible — that is the clean half, already done | 0:20 |
| 2 | **Start a run**: choose the repository, tick *Demonstrate a blocked run*, press Start | 0:20 |
| 3 | The form collapses; a pending row appears with the issue number. The pipeline does not exist yet — the run id is minted by the first job | 0:40 |
| 4 | **gate1 — approve.** A gate is a GitHub Environment with a required reviewer; the pipeline is paused, not polling | 0:30 |
| 5 | `develop` runs. Measured at **2 min 19 s** on run 35679536930. This is the window for explaining that the reviewer is advisory and the scanners are not | 2:20 |
| 6 | **The block.** Status `BLOCKED`, two blocking findings, provenance `scanners`, and every stage after it marked as never having run | 0:50 |

## The one field that proves it

Real scanners report the credential at **added lines 3 and 4**. The stand-in
fixture, used when a scanner is unavailable, reports **4 and 5**. That pair is the
only field that distinguishes a genuine scan from a fallback — the verdict, the
count, the rule names, the file and the severity are identical either way.

The pre-flight check asserts it against the deployed runtime before every demo.

---

## What the audience should be able to verify afterwards

- The issue, on the target repository, with the pipeline's own comments on it
- The GitHub Actions run, with `develop` red and everything after it skipped
- The pull request, carrying the reviewed diff
- The same verdict on the product's own screen, with the two line numbers

---

## Contingency

| Condition | Action |
|---|---|
| The run stalls or the network fails | Play the recording embedded on the same slide. Say out loud that it is a recording |
| A gate approval is refused | Say the token needs `deployments: write`; do not debug on stage |
| The venue machine is used instead of ours | The deck carries the recordings; nothing is fetched at runtime |
| Time runs short | Stop after step 5 and show the block on the recording |

---

## What is NOT claimed

- **The merged pull request carries the reviewed diff as an artifact**; it does not
  apply it to the application source. Applying it is the next roadmap item.
- **The generated tests and the browser tests are two layers that do not meet.** A
  test is generated from the ticket on every run; Selenium runs in CI against the
  application. Neither verifies the other yet.
- **A repository admin can bypass a gate.** It is an operator setting and the
  pre-flight check reports it on every run.
