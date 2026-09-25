# The demonstration — what it shows and how it is run

**TD63 RosettaTeam · DevOps Hackathon Finals, 26 September 2026**
Five minutes, live, from the presenting laptop, in the product at
`theagentorg.rosettacloud.app`.

---

## What is being demonstrated

**A real ticket, with no demo flag set, walked through the pipeline until the
deterministic rule refuses it.** The ticket asks for something that looks reasonable:
pin an old version of the `requests` library for a legacy proxy. That version has a
known vulnerability, and Trivy finds it.

Nothing is planted. The earlier demos ticked *Demonstrate a blocked run*, which plants a
known test credential; this one does not, so the page shows no "on purpose" note and the
failure is the pipeline's own.

The clean half, a change that passes and merges, is shown as evidence that already
exists rather than performed live: a merged pull request and a promoted run in the run
list. Running it live would take about six minutes of a twenty-minute slot.

---

## The claim the demo is making

A language model writes the change. A different language model reviews it. Neither of
them decides whether it ships. The component that refuses it contains no model: three
scanners and a severity comparison. And the refusal is part of the pipeline's structure,
so no forgotten setting can let it through.

**Rehearsed on 25 September, run #73: the reviewer APPROVED the vulnerable pin, and the
rule blocked it.** If that happens again, say it: it is the design, live. The reviewer is
a model, so it may object instead; the block does not depend on it either way.

---

## The ticket — paste it into the first field

```
Our legacy audit proxy only accepts requests 2.19.0. Pin requests==2.19.0 in requirements.txt and use it in app/auth.py to POST each failed login to the URL in the AUDIT_WEBHOOK_URL environment variable.
```

The first field is the ticket the agents read. The second is optional context for
people; the agents do not read it. **Leave *Demonstrate a blocked run* unticked.**

---

## The five minutes, in order

| | What happens | About |
|---|---|---|
| 1 | The run list, already signed in. One promoted run with a merged pull request is visible: that is the clean half, already done | 0:20 |
| 2 | **Start a run**: choose `auth-service`, paste the ticket, leave the box unticked, press Start | 0:30 |
| 3 | A new row appears with its issue number. Planning takes about **30 seconds** | 0:40 |
| 4 | **gate1 — approve.** A gate is a GitHub Environment with a required reviewer; the pipeline waits there for a person | 0:30 |
| 5 | Develop, review and security run: about **81 seconds** from the approval to the block, measured on run #73. Explain that the reviewer is advisory and the scanners are not | 1:30 |
| 6 | **The block.** Status `blocked`, provenance `scanners ran`, and nothing after it runs | 1:30 |

**On screen.** The line runs green through develop and review (hollow rose at review if
the reviewer objected) and stops in filled rose at security. The security panel opens on
its own:

- `high ≥ high` — the worst finding against the threshold, the whole decision
- three cards, one per scanner. On run #73: **gitleaks** nothing found, **semgrep**
  nothing found, **trivy** `high ≥ high · blocks`. The model writes the change afresh
  each time, so the first two may differ; the Trivy card is the one that matters
- the findings: `CVE-2018-18074` is **high** and blocks; the medium CVEs are below the
  threshold and do not. That contrast is the threshold working, in front of the judges

## How it shows the scan was real

The finding is a CVE number from Trivy's vulnerability database. The built-in stand-in,
used only when a scanner is unavailable, has no Trivy findings at all, and the panel says
`scanners ran`. Separately, the pre-flight check each morning confirms the deployed
scanners on a reference change: they report its planted key at lines 3 and 4, where the
stand-in reports 4 and 5.

---

## What the audience can check afterwards

- The issue, on the target repository, with the pipeline's own comments on it
- The GitHub Actions run, with `develop` failed and everything after it skipped
- The pull request, carrying the reviewed change with the pinned version
- The same verdict in the product, with the CVE and the threshold

---

## Contingency

| Condition | Action |
|---|---|
| The run stalls or the network fails | Open run #73 in the product: the same ticket, rehearsed on 25 September. Say plainly that it is an earlier run |
| Trivy's card says the scanner failed | It still blocks, by design, but that is a different story. Say so, then open run #73 |
| The model writes no pin | Rare; say the model did not follow the ticket, and open run #73 |
| A gate approval is refused | Say the token needs the deployments permission; do not debug on stage |
| The venue machine is used instead of ours | The deck needs nothing from the network; the product needs the network and a signed-in session |
| Time runs short | Stop after step 5 and open run #73's block |

**There is no recording in the deck.** Run #73 in the product is the fallback.

---

## What the page will also show, and the one-line answer

- **"a stage used a canned answer"** in rose at the top — on run #73 it was the agent
  that writes tests from the ticket, which did not get a model answer and used its
  stand-in. The page says so rather than hiding it; it does not affect the block.
- **The security explanation may be wrong** — on run #73 it said the file was
  "missing" the library. That paragraph is the model's, written after the verdict, and
  has no say in it. That is why the model does not decide.

---

## What is NOT claimed

- **The merged pull request carries the reviewed change as a file**; it does not apply
  it to the application source. Applying it is the first roadmap item.
- **The generated tests and the browser tests do not check each other yet.** Tests are
  generated from each ticket; Selenium runs in CI against the application.
- **A repository admin can bypass a gate.** It is a setting, and our pre-flight check
  reports it on every run.
