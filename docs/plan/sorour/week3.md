# Sorour — Week 3 (Aug 22–27): timeline UI, approve/reject, deploy, rehearse

The pipeline is real end to end (planner, developer, reviewer, security are live;
the poisoned ticket blocks 10/10). This week you build the two things judges
actually watch — the **log timeline** and the **approve/reject screen** — then
co-deploy the agents to AgentCore with Mariam and rehearse.

**Feature freeze: Tuesday Aug 25** (end of day). After that: only dry runs and
fixing what they surface. Target ready date **Aug 27**.

The log you render is append-only JSONL, one `LogEvent` per line, at
`runs/<run_id>.jsonl`. Read it with `log.read(run_id) -> list[LogEvent]`. The
event shape (from `state.py`, exact fields):

```python
class LogEvent(BaseModel):
    event_id: str; ts: str            # both auto
    run_id: str; ticket_id: str
    actor: Actor                       # planner|developer|reviewer|security|sre|human|system
    stage: Stage                       # plan|gate1|develop|review|security|gate2|sre|gate3|promote
    action: Literal["opened","proposed","reviewed","blocked","passed",
                    "approved","rejected","overridden","merged","promoted"]
    verdict: str = ""; summary: str = ""; artifact_ref: str = ""
```

---

## Sat–Sun Aug 22–23 — log timeline (do NOT cut this)

**Task: render a run's log as a readable timeline.**

The timeline is the UX the judges score — never cut it (see the cut order). Build
it in two layers: a plain-text renderer usable from the CLI and in the demo, then
a one-file HTML view for the screen. Both read the same `log.read(run_id)`; no
new data source.

`agentorg/log.py` already gives you (do not rename):

```python
def read(run_id: str) -> list[LogEvent]:
    """Read the full ordered event history for a run."""
```

Create `agentorg/timeline.py`:

```python
"""Render a run's append-only log as a timeline. OWNER: Sorour.

    python -m agentorg.timeline <run_id>            # text timeline
    python -m agentorg.timeline <run_id> --html out.html
"""
import argparse
import html

from . import log
from .state import LogEvent

# One glyph per terminal action so a run reads at a glance.
_MARK = {
    "opened": "•", "proposed": "→", "reviewed": "✎", "passed": "✓",
    "blocked": "⛔", "approved": "✓", "rejected": "✗", "overridden": "!",
    "merged": "⇄", "promoted": "★",
}


def _line(e: LogEvent) -> str:
    mark = _MARK.get(e.action, "•")
    ts = e.ts[11:19]                       # HH:MM:SS from the iso timestamp
    verdict = f" [{e.verdict}]" if e.verdict else ""
    summary = f" — {e.summary}" if e.summary else ""
    return f"{ts} {mark} {e.stage:<8} {e.actor:<9} {e.action}{verdict}{summary}"


def render_text(run_id: str) -> str:
    events = log.read(run_id)
    if not events:
        return f"(no events for run {run_id})"
    header = f"Timeline for run {run_id} — ticket {events[0].ticket_id}"
    return header + "\n" + "\n".join(_line(e) for e in events)


def render_html(run_id: str) -> str:
    events = log.read(run_id)
    rows = "\n".join(
        f"<li><span class='ts'>{html.escape(e.ts[11:19])}</span>"
        f"<span class='mark'>{_MARK.get(e.action, '•')}</span>"
        f"<span class='stage'>{html.escape(e.stage)}</span>"
        f"<span class='actor'>{html.escape(e.actor)}</span>"
        f"<span class='act'>{html.escape(e.action)}"
        f"{(' [' + html.escape(e.verdict) + ']') if e.verdict else ''}</span>"
        f"<span class='sum'>{html.escape(e.summary)}</span></li>"
        for e in events)
    tid = html.escape(events[0].ticket_id) if events else run_id
    return f"""<!doctype html><meta charset=utf-8>
<title>Timeline {html.escape(run_id)}</title>
<style>
 body{{font:15px/1.5 system-ui;margin:2rem;background:#0d1117;color:#e6edf3}}
 h1{{font-size:1.1rem}} ul{{list-style:none;padding:0}}
 li{{display:grid;grid-template-columns:70px 24px 90px 90px 160px 1fr;
     gap:.5rem;padding:.35rem .5rem;border-left:2px solid #30363d}}
 li:hover{{background:#161b22}} .ts{{color:#8b949e}} .mark{{text-align:center}}
 .stage{{color:#58a6ff}} .actor{{color:#d2a8ff}} .sum{{color:#8b949e}}
</style>
<h1>Timeline for {html.escape(run_id)} — ticket {tid}</h1>
<ul>{rows}</ul>"""


def main() -> None:
    ap = argparse.ArgumentParser(prog="agentorg.timeline")
    ap.add_argument("run_id")
    ap.add_argument("--html", metavar="PATH", help="write an HTML view to PATH")
    args = ap.parse_args()
    if args.html:
        import pathlib
        pathlib.Path(args.html).write_text(render_html(args.run_id))
        print(f"wrote {args.html}")
    else:
        print(render_text(args.run_id))


if __name__ == "__main__":
    main()
```

**Done when:** a full run's history renders on one screen — opened → proposed →
reviewed → passed/blocked → promoted:

```bash
# produce a run, capture its run_id, render the timeline
RID=$(python -c "
from agentorg import graph
print(graph.run_pipeline('CLEAN-1','Add a per-IP login rate limit.').run_id)")
python -m agentorg.timeline "$RID"
python -m agentorg.timeline "$RID" --html /tmp/timeline.html && echo wrote html
```
Expected: a timeline starting with a `system plan opened` line and ending with a
`★ promote system promoted` line (for the clean run), and `wrote html`. For a
poisoned run the last line is `⛔ security ... blocked`.

**You're unblocked because:** the log has been written by every graph stage since
week 1 — you're only reading it.

---

## Mon Aug 24 — approve/reject screen (over gates.resume)

**Task: a screen that approves/rejects a paused gate — CLI-backed, cut-safe.**

This is second on the cut list: if time is tight, the week-2
`python -m agentorg.gates_cli resume ...` command IS the fallback and is already
good enough for the demo. Build the screen as a thin layer over the exact same
call so nothing new can break the block:

```python
# gates.resume is already frozen — do NOT rename:
def resume(run_id: str, decision: HumanDecision) -> RunState:
    """Reload paused state, append the decision, hand back to the graph."""
```

Add a tiny local HTTP screen `agentorg/approve_server.py` (stdlib only — no
Flask, no new dependency) that lists paused runs and posts a decision:

```python
"""Minimal approve/reject screen over gates.resume(). OWNER: Sorour.

    python -m agentorg.approve_server        # serves http://127.0.0.1:8000
Cut fallback: python -m agentorg.gates_cli resume <run_id> --gate <g> \
    --decision approved --by <you>
"""
import html
import pathlib
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from .state import HumanDecision
from . import gates

_RUNS = pathlib.Path(__file__).resolve().parent.parent / "runs"


def _paused_run_ids() -> list[str]:
    return [p.stem.replace(".state", "") for p in sorted(_RUNS.glob("*.state.json"))]


def _page(msg: str = "") -> bytes:
    items = "".join(
        f"<form method=post action=/decide>"
        f"<input type=hidden name=run_id value='{html.escape(rid)}'>"
        f"<code>{html.escape(rid)}</code> "
        f"gate <select name=gate><option>gate1</option><option>gate2</option>"
        f"<option>gate3</option></select> "
        f"<button name=decision value=approved>Approve</button> "
        f"<button name=decision value=rejected>Reject</button></form>"
        for rid in _paused_run_ids())
    body = f"""<!doctype html><meta charset=utf-8><title>Approve / Reject</title>
<style>body{{font:15px system-ui;margin:2rem}}form{{margin:.4rem 0}}
.msg{{color:#238636}}</style><h1>Paused runs</h1>
<p class=msg>{html.escape(msg)}</p>{items or '<p>(no paused runs)</p>'}"""
    return body.encode()


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if urlparse(self.path).path == "/":
            self._send(_page())
        else:
            self._send(_page())

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        form = parse_qs(self.rfile.read(length).decode())
        run_id = form["run_id"][0]
        decision = HumanDecision(gate=form["gate"][0], decision=form["decision"][0],
                                 by="ui-reviewer")
        state = gates.resume(run_id, decision)
        self._send(_page(f"{run_id}: {form['decision'][0]} -> status={state.status}"))


def main() -> None:
    server = HTTPServer(("127.0.0.1", 8000), Handler)
    print("approve/reject screen on http://127.0.0.1:8000  (Ctrl-C to stop)")
    server.serve_forever()


if __name__ == "__main__":
    main()
```

**Done when:** a human approves/rejects a paused gate from the screen and the
resulting `status` is correct — or the CLI fallback is confirmed good enough.

```bash
# 1. create a paused state file (a gate writes runs/<run_id>.state.json)
RID=$(python -c "
from agentorg.state import RunState
from agentorg import gates
st = RunState(ticket_id='CLEAN-1', ticket_text='Add a per-IP login rate limit.')
gates.pause(st, 'gate1'); print(st.run_id)")

# 2a. UI path: start the screen, click Approve on that run in the browser
python -m agentorg.approve_server    # visit http://127.0.0.1:8000 -> Approve

# 2b. CLI fallback (cut-safe, identical call):
python -m agentorg.gates_cli resume "$RID" --gate gate1 --decision approved --by sorour
# expected: run_id=<RID> gate=gate1 decision=approved status=running
```
Expected: the screen shows `<RID>: approved -> status=running` (or `rejected ->
status=rejected`), or the CLI prints the same status. Both go through
`gates.resume` — the block path is untouched.

---

## Tue Aug 25 — AgentCore deploy (with Mariam) + FREEZE

**Task: deploy the 5 agents to Bedrock AgentCore. You own IAM/ECR; Mariam drives
the CLI.**

Your Terraform from week 1 already laid the ground: 5 ECR repos
`theagentorg-shared-{planner,developer,reviewer,security,sre}-agent` and the
runtime role `theagentorg-shared-agentcore-runtime-role` (trusts
`bedrock-agentcore.amazonaws.com`). Hand Mariam the two values she needs — pull
them from your Terraform outputs:

```bash
cd infra/Terraform/environments/shared
terraform output -raw agentcore_runtime_role_arn      # -> arn:aws:iam::339712964409:role/theagentorg-shared-agentcore-runtime-role
terraform output ecr_repository_urls                   # -> 5 repo URLs
terraform output github_actions_role_arns              # -> existing github-actions-role ARN, for her deploy workflow's role-to-assume
```

Confirm the ground is there before she deploys:

```bash
aws ecr describe-repositories --region us-east-1 \
  --query "repositories[?contains(repositoryName,'theagentorg-shared')].repositoryName"
aws iam get-role --role-name theagentorg-shared-agentcore-runtime-role \
  --query 'Role.Arn' --output text
```
Expected: the 5 repo names listed, and the runtime role ARN printed.

Mariam then runs, per agent (planner shown — repeat for developer, reviewer,
security, sre), from `agentorg/agents/`:

```bash
ROLE=$(terraform -chdir=infra/Terraform/environments/shared output -raw agentcore_runtime_role_arn)
agentcore configure -e planner.py -n theagentorg_planner -er "$ROLE" \
    -rf requirements.txt -r us-east-1 -ni
agentcore launch --auto-update-on-conflict --env BEDROCK_MODEL=us.amazon.nova-2-lite-v1:0
agentcore status
agentcore invoke '{"task":"Add a per-IP login rate limit."}'
```

You verify the runtime can pull the image and invoke Bedrock (that's the role
you own):

```bash
agentcore status                              # runtime shows READY
agentcore invoke '{"task":"say hi"}'          # returns a real completion, not an auth error
```
If `invoke` returns `AccessDenied` on Bedrock or ECR pull, the fix is on your
side — the runtime role's Bedrock-invoke / ECR-pull policy — not Mariam's CLI.

**Done when:** `agentcore status` is READY for all 5 runtimes and
`agentcore invoke` returns a real completion (the graph can run against hosted
agents, not local).

**Task: FREEZE at end of day.** From here: only dry runs and fixing what they
surface. No new features. Announce the freeze to the team.

**Cut order if behind:** SRE agent first — keep `agentorg/agents/sre.py` on its
fixture (it's `# TODO(Sorour, wk3)`, first on the cut list); then the
approve/reject screen — fall back to `python -m agentorg.gates_cli`. Never cut
the security block or the timeline.

---

## Wed–Thu Aug 26–27 — dry runs (online + offline) + ready

**Task: dry-run the full demo twice — once online, once offline — and confirm
they behave identically.**

Offline mode is Mariam's build (`config.OFFLINE=true` makes `github_ops` use
plain local git and `pr_url=f"local://{dev.branch}"` instead of the GitHub API),
but you run the rehearsal with her. The security block must be identical in both
modes — it never touches the network.

```bash
# ONLINE: clean promotes, poisoned blocks, timeline renders
python -m agentorg.graph                       # status=promoted
python -m agentorg.graph --poisoned            # status=blocked, blocking=2
RID=$(python -c "from agentorg import graph;print(graph.run_pipeline('POISON-1','Add a per-IP login rate limit.',poisoned=True).run_id)")
python -m agentorg.timeline "$RID"             # ends with ⛔ ... blocked

# OFFLINE: same behaviour, no network
OFFLINE=true python -m agentorg.graph          # status=promoted
OFFLINE=true python -m agentorg.graph --poisoned   # status=blocked, blocking=2

# poisoned determinism holds in both modes
for i in $(seq 1 10); do OFFLINE=true python -m agentorg.graph --poisoned | grep status; done
```
**Done when:** online and offline both show clean → `promoted` and poisoned →
`blocked` (`blocking=2`), the timeline renders both runs, and the offline
poisoned loop is 10/10 `status=blocked`. Ready date **Aug 27** is hit.

**Task: final green check before ready.**

```bash
pip install -e ".[dev]" && python make_fixtures.py && pytest -q
```
**Done when:** `pytest -q` is green and both dry runs behaved identically.

---

## End of week 3 — done when

- `agentorg/timeline.py` renders a full run history on one screen (text + HTML),
  reading only `log.read(run_id)`.
- Approve/reject works: `agentorg/approve_server.py` over `gates.resume`, or the
  confirmed `python -m agentorg.gates_cli` fallback.
- All 5 agents deploy to AgentCore (`agentcore status` READY, `agentcore invoke`
  returns a real completion); Mariam has `agentcore_runtime_role_arn`,
  `ecr_repository_urls`, and `github_actions_role_arns` from your Terraform.
- Two clean dry runs — online and offline — behave identically by **Aug 27**;
  poisoned blocks 10/10 in both modes.
- Feature freeze held on **Tue Aug 25**.

**Cut order if behind:** SRE agent first (keep it a stub reading CI), then the
approve/reject UI (use the CLI). Never cut the security block or the log
timeline — the block **is** the demo, the timeline is the UX the judges score.
