# The Agent Org — Live Demo Script (target 5–7 min, English)

Owner: Reem. Freeze: Tue Aug 25. Judged live demo: **Tue Aug 25**.

Everything below runs offline. No live AWS call is required during the talk.

## How to read this file

Each beat has three parts: the **spoken line** (English), the **exact command**,
and the **verified on-screen result**. Every result in this file was produced by
running the command on 2026-08-20 and pasting its output — see
[Verification block](#verification-block-all-output-pasted-from-real-runs-2026-08-20)
at the bottom. Nothing here is recalled from memory.

Two things the driver must internalise before reading any further:

1. **Every command is preceded by `cd <repo root>`.** `tests/` has no
   `__init__.py` and `pyproject.toml` sets `pythonpath = ["."]`, which resolves
   against the rootdir. From any other directory the imports die with
   `ModuleNotFoundError: No module named 'tests'` — a traceback on the projector.
   Measured, both directions, in the verification block.
2. **Set `LLM_DISABLED=true` for the whole session** (see Beat 0). With live model
   calls a pipeline run takes **~10.7 s**; with the knob it takes **~0.30 s**.
   That 30x gap is dead air on stage, times two runs.

**Which narration you speak depends on which machine you are on.** Beats 2 and 3
have two versions, A and B. Read [Beat 0 step 6](#beat-0--pre-flight-run-before-the-audience-is-in-the-room)
and know which one you are on **before** you walk in. Speaking narration A on a
fixture-fallback machine is a false claim a judge can puncture.

---

## Beat 0 — Pre-flight (run BEFORE the audience is in the room)

In this exact order. The order is not cosmetic: setting the knob before the
binaries are installed makes the CLEAN run block (measured: `status=blocked`,
`blocking=3`), which takes down the first half of the demo.

    1. Install semgrep 1.172.0, gitleaks 8.21.2, trivy 0.74.0.
    2. cd <repo root> && gitleaks version && trivy --version && semgrep --version
    3. cd <repo root> && trivy fs --download-db-only --timeout 5m .   # warm the 108 MB DB
    4. cd <repo root> && python scripts/scan_gate.py                  # must exit 0
    5. export SCANNERS_REQUIRED=true                                  # ONLY after 1-4 pass
    6. export LLM_DISABLED=true                                       # pacing; see above
    7. cd <repo root> && python -m agentorg.graph            # status=promoted
    8. cd <repo root> && python -m agentorg.graph --poisoned # status=blocked, blocking=2

**If step 4 does not exit 0, DO NOT set the knob in step 5.** Run
`unset SCANNERS_REQUIRED` and do the demo in fixture-fallback mode: both halves
still behave correctly (measured: clean promotes with `pass/0`, poisoned blocks
with `block/2`), and the only thing lost is that the block's provenance is a
fixture rather than the rule. **Say nothing false about that on camera** — use
narration **B** in Beats 2 and 3.

On the machine this script was written on, step 4 **fails**: `scan_gate.py` exits
**1** with a `FileNotFoundError` traceback (`semgrep is not installed…`), because
all three binaries are absent from PATH. So as of today the demo is a
**narration B** demo. If you provision the machine, re-run step 4 and switch to A.

**Judge step 4 by its exit code, not by the text on screen.** The script *does*
print `SCAN OK` when all three scanners are installed and the findings match its
pins (`scripts/scan_gate.py:217`) — that is the line you are hoping to see. But on
an unprovisioned machine it never gets that far: it dies in a wrapper with a
traceback, and a traceback is easy to mistake for a different kind of failure. So
run `python scripts/scan_gate.py; echo "exit=$?"` and read the number. `exit=0`
means narration A is available; anything else means narration B, whatever the
output looks like.

Also before the room fills: terminal in the repo root, font large enough for the
back row, scrollback cleared.

### The trap, executed — a warning nobody has seen fire is a warning people route around

Both runs below are real, from this machine, minutes apart. Same command, same
ticket, opposite demo outcomes — the only difference is whether the knob was set
before the binaries existed.

**Out of order (knob set first). The CLEAN half of the demo dies:**

```
$ SCANNERS_REQUIRED=true LLM_DISABLED=true python -m agentorg.graph
run_id=88028c44-fba7-4121-bfa9-872d05d9e0fd
status=blocked
security verdict=block, blocking=3
```

That is the clean ticket. `blocking=3` is three `*-scanner-error` findings: the knob
promoted "scanner absent" to "scanner faulted", and a change nothing could scan
fails closed. Correct behaviour, catastrophic timing — Beat 2 is supposed to show
`promoted`.

**Knob unset (the safe fallback). Both halves behave:**

```
$ LLM_DISABLED=true python -m agentorg.graph
run_id=32cdf9c5-08dd-4c82-92c3-3d12e182b324
status=promoted
security verdict=pass, blocking=0
```

**Pre-flight result on this machine, run in order, today:** step 1 halts —
`gitleaks`, `trivy` and `semgrep` are all ABSENT from PATH; step 2's
`gitleaks version` gives `command not found`; step 4 gives `exit=1`. So this
machine is **narration B** and the knob must stay unset. If you provision the
demo machine, re-run steps 1-4 and only then set the knob.

---

## Beat 1 — The two tickets (~0:30)

> "The Agent Org is a multi-agent CI/CD pipeline. A code ticket walks through five
> AI agents — planner, developer, reviewer, security, SRE — and three human gates.
> The headline is a security block that fires deterministically, in code, not by
> the model's mood.
>
> Here are two tickets. Same feature in both: add a per-IP login rate limit. The
> only difference is that the poisoned ticket's reference implementation hardcodes
> an AWS key — `AKIAIOSFODNN7EXAMPLE`, AWS's own published placeholder."

```bash
cd <repo root> && grep -n AWS_ACCESS_KEY_ID tickets/poisoned.md
```

Verified output — exactly one line:

```
17:+AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"
```

> "Line 17, inside the diff the ticket tells the developer agent to copy."

**Why this grep and not the other one.** The obvious command,
`grep -n AKIAIOSFODNN7EXAMPLE tickets/poisoned.md`, matches **two** lines
(measured: `:6` in the prose and `:17` in the diff). That is fine but it invites a
question you do not want mid-demo — "which one is the real one?" — because the
line 6 hit is the ticket *describing* the poison, not carrying it. Narrowing to
`AWS_ACCESS_KEY_ID` prints the one line that is actually the payload. Both
variants are in the verification block if you prefer the two-hit version; if you
switch, change the expected output above to match.

`tickets/` holds exactly two files, `clean.md` and `poisoned.md`, so showing them
side by side needs no directory listing.

---

## Beat 2 — The clean run passes (~1:30)

> "First the clean ticket. Plan, develop, review, security scan, SRE, three gates
> — all the way to promoted."

```bash
cd <repo root> && python -m agentorg.graph
```

Verified tail:

```
run_id=00bf79c7-bee1-4ebf-afca-fb91a2e722a8
status=promoted
security verdict=pass, blocking=0
```

The `run_id` is a fresh UUID every run. **Read the words, not the digits** —
`status=promoted`, `security verdict=pass, blocking=0` are the stable parts.

Measured wall time: **0.36 s** with `LLM_DISABLED=true`. No dead air, no filler
needed.

### Narration A — provisioned machine (Beat 0 steps 1–5 all passed)

> "Clean change. The three scanners ran for real — gitleaks, semgrep, trivy —
> found nothing at or above the block threshold, and the change was promoted."

### Narration B — fixture-fallback machine (Beat 0 step 4 failed; knob unset)

> "Clean change, nothing at or above the block threshold, promoted end to end."

On a fixture-fallback machine the terminal prints a warning line above the result:

```
scanners failed (FileNotFoundError: semgrep is not installed, so this change was NOT scanned by it. Set SCANNERS_REQUIRED=true to make that a blocking finding instead of a fixture fallback. Detail: the semgrep command could not be run (... [238 chars total, full text at DEBUG]); falling back to the fixture verdict
```

**Do not pretend that line is not there** — it is on the projector. If a judge
asks, the honest answer is short and it is the truth:

> "On this machine the scanner binaries aren't installed, so the pipeline falls
> back to a recorded scanner report. The block logic is the same either way; what
> you're seeing verified here is the pipeline's handling of the verdict, not the
> scanners themselves. On a provisioned machine that same line is absent and the
> findings come from gitleaks."

Do **not** say "a pure-Python rule blocks on anything at or above high" in
narration B. In fixture-fallback mode `compute_security_verdict` is never called;
the verdict is read from `fixtures/security_result_block.json`. That sentence
belongs to narration A only.

---

## Beat 3 — The poisoned run blocks (~3:00)

> "Now the poisoned ticket. The developer agent follows the poisoned reference, so
> the diff carries the hardcoded key. Watch the security stage."

```bash
cd <repo root> && python -m agentorg.graph --poisoned
```

Verified tail:

```
run_id=a6899357-f9ca-43a4-8c55-66a00b704179
status=blocked
security verdict=block, blocking=2
```

Measured wall time: **0.30 s**.

**Write the printed `run_id` down.** Beat 5 needs it. Copy it off the screen; do
not try to remember it.

> "Two blocking findings — the access key and the secret key. Status: blocked. The
> pipeline halts here. It never reaches the deploy gates."

### Narration A — provisioned machine

> "And this is the important part: that verdict is not the LLM's opinion. Gitleaks
> produces the findings; a pure-Python rule, `compute_security_verdict`, blocks on
> anything at or above `high`. Same input, same verdict, every run — it fires on
> every single run, not by luck."

### Narration B — fixture-fallback machine

> "And this is the important part: that verdict is not the LLM's opinion. The
> block is a code path with a fixed threshold, not a judgement call — the model
> never gets a vote on it. On this machine the findings come from a recorded
> scanner report rather than a live gitleaks process, so what's deterministic here
> is the pipeline's response to those findings. Wire up the real scanners and the
> same rule decides on their live output."

**The knob dependency, measured.** `blocking=2` holds in *both* modes with the
knob in its correct state. It becomes **3** — and the clean run in Beat 2 blocks
too — if `SCANNERS_REQUIRED=true` is set while the binaries are missing. That is
the exact failure Beat 0's ordering exists to prevent.

Both modes print `blocked / block / blocking=2` and differ only in the finding
line numbers: fixture reports lines **4 and 5**, real gitleaks 8.21.2 reports
**3 and 4**. If someone asks you on stage which mode you are in and you do not
remember, that is the discriminator — and it is not visible in this beat's
output, so answer from Beat 0, not from the screen.

---

## Beat 4 — Before vs after (~4:00)

> "Why does this matter? Here is the same poisoned change with the checks removed
> — a plain plan-develop-merge baseline, no security stage at all."

```bash
cd <repo root> && pytest -q "tests/test_baseline.py::test_baseline_ships_the_poisoned_change"
```

Verified output:

```
.                                                                        [100%]
1 passed in 0.02s
```

> "That test passes, and what it asserts is the uncomfortable part: without the
> Agent Org, the change ships with the secret in it."

Then point at the table on the slide (regenerated from
`runs/dora_table.md` — see [Beat 6](#beat-6--close-600)).

---

## Beat 5 — The timeline (the UX) (~5:00)

> "Every step is logged, append-only, one row per stage."

Use the `run_id` you wrote down in Beat 3:

```bash
cd <repo root> && head -2 runs/<run_id>.jsonl
```

Verified output for run `a6899357-f9ca-43a4-8c55-66a00b704179` (line breaks added
here for readability; on screen each event is one line):

```
{"event_id": "8dbff42c-8aaf-4447-86fe-acbb545c2315", "ts": "2026-08-20T05:03:09.106741+00:00",
 "run_id": "a6899357-f9ca-43a4-8c55-66a00b704179", "ticket_id": "DEMO-POISON", "actor": "system",
 "stage": "plan", "action": "opened", "verdict": "", "summary": "run started for DEMO-POISON",
 "artifact_ref": ""}
{"event_id": "fdd01a1c-aaa3-4e17-84e3-41f9bb44ce8e", "ts": "2026-08-20T05:03:09.107061+00:00",
 "run_id": "a6899357-f9ca-43a4-8c55-66a00b704179", "ticket_id": "DEMO-POISON", "actor": "planner",
 "stage": "plan", "action": "proposed", "verdict": "", "summary": "3 tasks", "artifact_ref": ""}
```

**Never `ls` or tab-complete inside `runs/`.** It is a large shared directory —
its file count drifts every time anyone runs the batch (measured across
consecutive runs: 600, then 2015, then 2561, then 2841), and other people's runs
land there while you are on stage. Tab-completion will either hang or dump
thousands of names onto the projector. Take the id from Beat 3's printed line and
type it, or paste it.

The poisoned run's timeline is **9 events**. Read the shape aloud rather than the
JSON:

> "planner proposed, a human gate approved, developer proposed, reviewer approved,
> then security blocked with a block verdict and two blocking findings — and the
> last row is the pipeline halting on the block rule. That timeline is the audit
> trail: who did what, in what order, and where it stopped."

The nine events in order, verified:

| # | actor | stage | action | verdict |
|---|---|---|---|---|
| 1 | system | plan | opened | |
| 2 | planner | plan | proposed | |
| 3 | system | gate1 | opened | |
| 4 | human | gate1 | approved | approved |
| 5 | developer | develop | proposed | |
| 6 | reviewer | review | reviewed | approve |
| 7 | system | develop | opened | |
| 8 | **security** | **security** | **blocked** | **block** |
| 9 | system | security | blocked | |

Note for the driver: the **last** event's actor is `system`, not `security`. The
`security` actor's block is event **8**. If you want the block itself on screen,
`tail -2` shows both; `tail -1` alone shows the system halt without the verdict.

---

## Beat 6 — Close (~6:00)

Point at the table (regenerate before the demo with
`cd <repo root> && LLM_DISABLED=true python -m tests.dora_batch && python -m tests.dora_table`):

> **Always in that order, chained with `&&`.** The renderer cannot tell a fresh
> batch report from a stale one — `runs/dora_batch.json` carries no generation
> timestamp — so running `dora_table` alone will happily render numbers from an
> earlier run with no sign that it did. The `&&` also means a failed batch stops
> before the render rather than leaving the previous table on screen.

```
| Metric | Baseline (no checks) | The Agent Org |
|---|---|---|
| Poisoned changes blocked | 0/10 | 10/10 |
| Bad changes shipped | 10/10 | 0/10 |
| Checks applied per change | 0 | 6 |
| Avg pipeline steps | 2.0 | 9.0 |
| Avg lead time (s) | 0.000100 | 0.063282 |

**Headline: The Agent Org blocks the poisoned change 10/10; the baseline ships it 10/10.**

_Measured in: FIXTURE-FALLBACK mode: no scanner binaries on PATH. Security verdict provenance: agent_org=fixture, baseline=n/a._
```

**The lead-time figures move run to run** (this run: `0.000100` baseline,
`0.063282` Agent Org; an earlier run gave `0.000099` and `0.060436`). Do not read
the decimals aloud and do not memorise them — the slide is regenerated the morning
of the demo and the digits will differ. The counts (`0/10`, `10/10`, `0`, `6`) and
the step averages (`2.0`, `9.0`) were stable across both runs.

**The provenance footer is part of the table and it stays.** On a fixture-fallback
machine it says so, in the artifact, and that is deliberate.

> "Five agents, three human gates, one deterministic block that a hardcoded secret
> can never sneak past. That's The Agent Org."

---

## Fallback if a live command misbehaves

Play the recorded backup video and narrate the same beats over it.

**Never cut the poisoned block (Beat 3) or the timeline (Beat 5)** — the block IS
the demo and the timeline is the UX the judges score. Cut Beat 4 or trim Beat 6
first if you are over time.

**The video does not exist yet.** It is Task 10 step 3, owner Aya, due **Aug 25** —
the same day as the demo. Until it is recorded, this fallback is a plan, not an
asset. If Aug 25 arrives with no video, the fallback is to re-run the live command
once; if it fails twice, describe Beat 3's output from this script and move to
Beat 5.

Whoever records the video must record it against **the narration this machine
warrants** (A or B per Beat 0). A video narrating provisioned-machine claims over
fixture-fallback output is the same false claim, just pre-recorded.

---

## Timing

**The 5–7 minute target is not verified in this file, because speech cannot be
timed by running commands.** What is verified is that the machine time is
negligible: the four on-screen commands total well under a minute of wall time
with `LLM_DISABLED=true` set.

| Beat | Command | Measured wall time |
|---|---|---|
| 1 | `grep -n AWS_ACCESS_KEY_ID tickets/poisoned.md` | instant |
| 2 | `python -m agentorg.graph` | 0.36 s |
| 3 | `python -m agentorg.graph --poisoned` | 0.30 s |
| 4 | `pytest -q …test_baseline_ships_the_poisoned_change` | 0.47 s (test itself 0.02 s) |
| 5 | `head -2 runs/<run_id>.jsonl` | instant |
| 6 | `dora_batch` then `dora_table` (run beforehand) | 0.89 s + 0.03 s |

So essentially the entire 5–7 minutes is speech, and the pacing risk is the
talking, not the tooling. **Without `LLM_DISABLED=true`, Beats 2 and 3 cost
~10.7 s each instead of ~0.3 s** — a 30x difference, roughly 20 seconds of
silence in front of judges. That is why it is Beat 0 step 6.

**Timing the spoken script requires a human rehearsal. That is Task 12, dated
Aug 24**, and it is the only way to confirm the 5–7 minute claim. Until that
rehearsal happens, treat the per-beat timestamps in the headings as intended
pacing, not measurement.

---

## Verification block — all output pasted from real runs (2026-08-20)

Environment: worktree
`/Users/sorour/sorour/TheAgentOrg/.claude/worktrees/agent-a7f2767bbef921e96`,
`.venv-testing/bin/python`, all three scanner binaries **absent** from PATH
(`gitleaks`, `semgrep`, `trivy` — each `command -v` returned nothing), so every
run below is **FIXTURE-FALLBACK mode**.

### Beat 1 — both grep variants

```
$ grep -n AWS_ACCESS_KEY_ID tickets/poisoned.md
17:+AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"
(exit 0)

$ grep -n AKIAIOSFODNN7EXAMPLE tickets/poisoned.md
6:example key `AKIAIOSFODNN7EXAMPLE` (a placeholder — nothing sensitive). When the
17:+AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"
(exit 0)

$ grep -c AKIAIOSFODNN7EXAMPLE tickets/clean.md
0
(exit 1 — grep exits 1 on no match; the clean ticket is genuinely clean)
```

### Beat 2 — clean run

```
$ LLM_DISABLED=true python -m agentorg.graph
scanners failed (FileNotFoundError: semgrep is not installed, so this change was NOT scanned by it. Set SCANNERS_REQUIRED=true to make that a blocking finding instead of a fixture fallback. Detail: the semgrep command could not be run (... [238 chars total, full text at DEBUG]); falling back to the fixture verdict

run_id=00bf79c7-bee1-4ebf-afca-fb91a2e722a8
status=promoted
security verdict=pass, blocking=0

real 0.355s (0.18s user, 0.08s system)
```

### Beat 3 — poisoned run

```
$ LLM_DISABLED=true python -m agentorg.graph --poisoned
scanners failed (FileNotFoundError: semgrep is not installed, so this change was NOT scanned by it. Set SCANNERS_REQUIRED=true to make that a blocking finding instead of a fixture fallback. Detail: the semgrep command could not be run (... [238 chars total, full text at DEBUG]); falling back to the fixture verdict

run_id=a6899357-f9ca-43a4-8c55-66a00b704179
status=blocked
security verdict=block, blocking=2

real 0.301s (0.17s user, 0.07s system)
```

### Beat 4 — baseline test

```
$ pytest -q "tests/test_baseline.py::test_baseline_ships_the_poisoned_change"
.                                                                        [100%]
1 passed in 0.02s

real 0.468s
```

### Beat 5 — timeline for the Beat 3 run id

```
$ head -2 runs/a6899357-f9ca-43a4-8c55-66a00b704179.jsonl
{"event_id": "8dbff42c-8aaf-4447-86fe-acbb545c2315", "ts": "2026-08-20T05:03:09.106741+00:00", "run_id": "a6899357-f9ca-43a4-8c55-66a00b704179", "ticket_id": "DEMO-POISON", "actor": "system", "stage": "plan", "action": "opened", "verdict": "", "summary": "run started for DEMO-POISON", "artifact_ref": ""}
{"event_id": "fdd01a1c-aaa3-4e17-84e3-41f9bb44ce8e", "ts": "2026-08-20T05:03:09.107061+00:00", "run_id": "a6899357-f9ca-43a4-8c55-66a00b704179", "ticket_id": "DEMO-POISON", "actor": "planner", "stage": "plan", "action": "proposed", "verdict": "", "summary": "3 tasks", "artifact_ref": ""}

$ wc -l < runs/a6899357-f9ca-43a4-8c55-66a00b704179.jsonl
9

$ tail -1 runs/a6899357-f9ca-43a4-8c55-66a00b704179.jsonl
{"event_id": "0626cab8-a75e-4281-9c7a-76a3e08ee56c", ..., "actor": "system", "stage": "security", "action": "blocked", "verdict": "", "summary": "pipeline halted by block rule; block reason local://runs/offline-demo/NOTES.md", "artifact_ref": ""}
```

Full actor/stage/action sequence, decoded from the same nine events:

```
system     plan       opened     verdict=''
planner    plan       proposed   verdict=''
system     gate1      opened     verdict=''
human      gate1      approved   verdict='approved'  demo auto-approve
developer  develop    proposed   verdict=''          Adds a per-IP rate limit of five login attempts per minute.
reviewer   review     reviewed   verdict='approve'   reviewer approved the diff
system     develop    opened     verdict=''          PR local://agent-org/DEMO-POISON-6dab07b
security   security   blocked    verdict='block'     2 blocking
system     security   blocked    verdict=''          pipeline halted by block rule; block reason local://...
```

This is why the script says the last event's actor is `system`: the spec's
original Beat 5 expected the file to end with actor `security`, and it does not.

### Beat 6 — DORA batch and table, regenerated today

```
$ LLM_DISABLED=true python -m tests.dora_batch
wrote /Users/sorour/.../runs/dora_batch.json
mode      : FIXTURE-FALLBACK mode: no scanner binaries on PATH
            (10 scanner-fallback warnings aggregated; this is that mode's expected path)
agent_org : {'runs': 10, 'bad_changes_shipped': 0, 'blocked': 10, 'promoted': 0, 'avg_step_count': 9.0, 'avg_lead_time_s': 0.063282, 'checks_run': 6, 'provenance': 'fixture'}
baseline  : {'runs': 10, 'bad_changes_shipped': 10, 'blocked': 0, 'promoted': 10, 'avg_step_count': 2.0, 'avg_lead_time_s': 0.0001, 'checks_run': 0, 'provenance': 'n/a'}

real 0.887s

$ python -m tests.dora_table
| Metric | Baseline (no checks) | The Agent Org |
|---|---|---|
| Poisoned changes blocked | 0/10 | 10/10 |
| Bad changes shipped | 10/10 | 0/10 |
| Checks applied per change | 0 | 6 |
| Avg pipeline steps | 2.0 | 9.0 |
| Avg lead time (s) | 0.000100 | 0.063282 |

**Headline: The Agent Org blocks the poisoned change 10/10; the baseline ships it 10/10.**

_Measured in: FIXTURE-FALLBACK mode: no scanner binaries on PATH. Security verdict provenance: agent_org=fixture, baseline=n/a._

wrote /Users/sorour/.../runs/dora_table.md

real 0.033s
```

Lead time differs from the previous recorded run (`0.000099` / `0.060436`), which
is why the script tells the driver not to read those digits aloud. The counts and
step averages matched exactly.

### Beat 0 — the ordering trap, measured

```
$ SCANNERS_REQUIRED=true LLM_DISABLED=true python -m agentorg.graph    # knob ON, binaries ABSENT, CLEAN ticket
run_id=dcccbdc7-9464-47ba-8e15-14e7eea7e5f3
status=blocked
security verdict=block, blocking=3
```

The clean ticket blocks with three scanner-error findings. This is the first half
of the demo dying, and it is why steps 1–4 must precede step 5.

```
$ python scripts/scan_gate.py ; echo "exit=$?"
... FileNotFoundError: semgrep is not installed, so this change was NOT scanned by
it. Set SCANNERS_REQUIRED=true to make that a blocking finding instead of a
fixture fallback. Detail: the semgrep command could not be run (classified
'absent'); timeout was 120s
exit=1

$ grep -c "SCAN OK" <that output>
0
```

`scan_gate.py` does **not** print "SCAN OK" on this machine — it raises and exits
**1**. The Beat 0 gate is therefore written as "must exit 0", not "must print
SCAN OK".

### The import-path constraint

```
$ cd /tmp && python -c "from tests.provenance import describe_mode; print(describe_mode())"
    from tests.provenance import describe_mode; print(describe_mode())
    ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
ModuleNotFoundError: No module named 'tests'

$ cd <repo root> && python -c "from tests.provenance import describe_mode; print(describe_mode())"
FIXTURE-FALLBACK mode: no scanner binaries on PATH
```

### Provenance discriminator

`fixtures/security_result_block.json` — 3 findings, blocking 2:
`aws-access-key-id` at `app/auth.py` line **4**, `aws-secret-access-key` at line
**5**, plus one `low` semgrep finding at line 7 that does not block. Real gitleaks
8.21.2 reports the same two rules at lines **3** and **4**. Line numbers are the
only cheap way to tell the two modes apart from output alone.

### Not verified in this file

- **The 5–7 minute spoken duration.** Requires a human rehearsal — Task 12, Aug 24.
- **Real-scanner mode (narration A) has never been run** anywhere in this project.
  The three binaries are not installed on any machine here. Narration A is written
  so it is ready the moment someone provisions a machine and re-runs Beat 0 step 4;
  it is **not** a description of anything yet observed.
- **The backup video** does not exist. Task 10 step 3, owner Aya, due Aug 25.

---

## Re-verify sequence (run at freeze Aug 25, and again Aug 26–27 after any late fix)

Every command needs `cd <repo root>` first. Mode 1 was executed in full on Aug 20 and
the pasted results are below; Mode 2 has never been run anywhere in this project.

### Mode 1 — fixture fallback. What CI and every laptop here runs.

```
$ pytest -q
218 passed, 1 skipped

$ python -c "from tests.provenance import describe_mode; print(describe_mode())"
FIXTURE-FALLBACK mode: no scanner binaries on PATH

$ pytest -q tests/test_provenance.py
7 passed

$ LLM_DISABLED=true python -m tests.dora_batch && python -m tests.dora_table
agent_org : blocked=10  bad_changes_shipped=0  provenance=fixture
baseline  : blocked=0   bad_changes_shipped=10 provenance=n/a
```

### Mode 2 — real scanners. Requires the three binaries. NEVER RUN.

```
$ python scripts/scan_gate.py; echo "exit=$?"     # need exit=0
$ pytest -q                                        # expect the trivy skip to RUN, so 219 passed
$ LLM_DISABLED=true python -m tests.dora_batch && python -m tests.dora_table
```

**Verify the instrument before trusting a green run, every time.** `pytest -q
tests/test_provenance.py` must report:

| Machine | Correct result | What a wrong result means |
|---|---|---|
| No binaries (today) | `7 passed`, 0 skipped | — |
| All three installed | `4 passed, 3 skipped` | **`7 passed` here means the skips are broken** and the file is not measuring what it claims |

**Then confirm the two things that make the numbers meaningful:**

1. `agent_org` reports `blocked: 10, bad_changes_shipped: 0` **in both modes**.
2. The `provenance` field **differs** between them — `fixture` versus `real_scanners`. If it
   does not differ, the discriminator is broken and every number in the deck is unlabelled.
   *This comparison is impossible until Mode 2 runs once.*

If a late fix moves a number, update the deck the same day — and **re-paste it from
`runs/dora_table.md`, never re-type it.**

---

## Freeze checklist (Task 12 — needs a human)

- [ ] **Mon Aug 24** — one full timed run-through with whoever drives the terminal. Target
      5–7 minutes. Machine time is ~2 s total with `LLM_DISABLED=true`, so the pacing risk
      is entirely the talking.
- [ ] **Tue Aug 25, end of day** — Sorour's sign-off. Aya posts "metrics frozen" after a
      final green `pytest -q` and a final `python -m tests.dora_batch`. From here: wording
      polish only — no new commands, no new behaviour, no new tests.
- [ ] **Wed–Thu Aug 26–27** — two clean run-throughs, each under 7 minutes, in English.
- [ ] Confirm the fallback: play the video and narrate the six beats over it. **The video
      does not exist yet** — Task 10 step 3, owner Aya, due Aug 25.
- [ ] Re-run the sequence above after any late fix, and re-paste every number that moved.

**Freeze precondition, already met.** Tasks 1–8 were all required to land before Aug 25
because the freeze forbids new tests and new code paths. They landed Aug 20, five days
early, across 14 reviewed commits. Nothing from the contingency cut list was cut.
