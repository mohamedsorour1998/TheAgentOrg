"""Minimal approve/reject screen over gates.resume(). OWNER: Sorour.

    python -m agentorg.approve_server        # serves http://127.0.0.1:8000

Cut fallback, and the documented path for anything this screen refuses:

    python -m agentorg.gates_cli resume <run_id> --gate <g> \
        --decision approved --by <you>

=========================================================================
THERE IS NO AUTHENTICATION. THIS MUST NEVER BE EXPOSED OFF-HOST.
=========================================================================

This endpoint resumes a paused pipeline past a human gate -- including the
SECURITY gate -- and there is no auth seam anywhere in this codebase to hang a
credential on. Anyone who can reach this port can approve a gate. Three things
stand in for the authentication that does not exist, and none of them is a
substitute for it:

  * it binds 127.0.0.1 only, never 0.0.0.0, so it is not reachable off-host;
  * every mutation is a POST, so it cannot be triggered by a link, an <img>, or
    a prefetch;
  * a POST carrying a cross-site `Origin` is refused (see `_check_origin`),
    because localhost binding alone does NOT stop a page in the demo laptop's
    browser from posting here -- that is the hole loopback binding is most often
    assumed to close and does not.

`by` is recorded as "ui-reviewer" for every decision because with no
authentication the server genuinely does not know who clicked. That string is a
statement about what is knowable here, not a placeholder to fill in later.

RECOMMENDATION, deliberately not implemented: a real deployment needs an
operator identity on each decision. That is a credential scheme, it belongs in
the codebase's auth seam rather than invented here, and until it exists this
surface is localhost-only scaffolding for a demo.

=========================================================================
WHAT THIS REFUSES, AND WHY IT IS ONE PREDICATE RATHER THAN SEVERAL
=========================================================================

A decision is accepted only for a (run_id, gate) pair that `_awaiting` reports
is actually waiting for a human. That single check is the listing AND the guard,
which is the point: the screen cannot offer a button it would then refuse, and
it cannot refuse a button it offered.

It also subsumes, in one place, every way a decision can be phantom:

  * THE POST-REJECTION APPROVAL. `gates.resume` sets `status` only when the
    decision is "rejected" (gates.py:86-87) and never un-sets it, so approving
    a run the graph already rejected leaves `status="rejected"` -- correct --
    while still APPENDING the approval to `state.decisions` and still writing a
    `human / gate3 / approved` row to the log. Measured, and now visible:
    agentorg/timeline.py renders that row as `✓ gate3 human approved` AFTER the
    `✗ gate2 human rejected` row, so a rejected run displays a later approval on
    the timeline the judges read. `status` holding is not a guard; nothing in
    `gates.resume` refuses the attempt. This module refuses it at the boundary.
  * a decision on a run that is over some other way -- promoted, blocked or
    failed;
  * a second decision on a gate that has already been decided;
  * a decision on a gate this run never paused at;
  * a run_id that is not on disk at all -- including `../../etc/passwd`, which
    `gates._state_path` would happily resolve OUTSIDE runs/ (verified). Nothing
    here builds a path from request bytes: an accepted run_id came from
    `_RUNS.glob`, so traversal is impossible by construction rather than by
    pattern-matching.

The underlying `gates.resume` gap is NOT fixed here -- that file is shared and
owned elsewhere. Refusing at this boundary keeps the phantom off the screen; it
does not stop `python -m agentorg.gates_cli resume` from writing one.

THE COST OF THAT NARROWING, stated because it is real: an "overridden" decision
is refused by this screen entirely, and a blocked run cannot be overridden from
a browser, because a security block returns before gate2 ever opens (graph.py:
209-228) so no gate is ever awaiting on one. Overriding a security block is the
single most dangerous thing this vocabulary can express, and requiring shell
access for it -- `gates_cli resume ... --decision overridden` -- rather than an
unauthenticated click is the trade this module makes on purpose.
"""

import html
import logging
import pathlib
from collections.abc import Container
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import gates, log
from .state import HumanDecision, RunState

# This module's own runs directory, in the style of log._LOG_DIR,
# gates._STATE_DIR and gates_cli._RUNS -- all four resolve to <repo>/runs. Its
# own rather than a reach into another module's underscore, and module-level so
# tests have one seam to patch. Patching this one does NOT redirect gates or
# log; a hermetic test patches all three (see tests/test_approve_server.py).
_RUNS = pathlib.Path(__file__).resolve().parent.parent / "runs"

# The gates a decision can name. Hardcoded for readability; kept honest by
# test_the_gates_this_screen_offers_are_exactly_the_ones_the_contract_allows,
# which derives the same tuple from HumanDecision's Literal and compares.
_GATES = ("gate1", "gate2", "gate3")

# What this screen will act on. NOT the full HumanDecision vocabulary: see "THE
# COST OF THAT NARROWING" in the module docstring for why "overridden" is not
# here. Exact words, no prefixes, no case folding, no stripping -- the same
# fail-closed rule graph.APPROVAL_WORDS states, for the same reason: on the
# three prompts in this system where being misread is most expensive, anything
# that is not an explicit exact decision must not become one.
_DECISIONS = ("approved", "rejected")

# A run in one of these is over. `gates.pause` can only have been called before
# the run reached its ending, so a terminal run's open pause events are history,
# not an invitation -- which is exactly the post-rejection case above.
_TERMINAL = frozenset({"rejected", "promoted", "blocked", "failed"})

# The sentence gates.pause writes at every gate (gates.py:61). Read rather than
# assumed: test_a_real_gates_pause_is_what_the_listing_finds calls the real
# gates.pause, so a change to that wording fails a test here instead of quietly
# emptying this screen.
_PAUSE_MARKER = "awaiting human decision"

# Loopback hosts. A cross-site POST carries the attacker's Origin, which is
# never one of these; a same-origin POST from the browser carries whichever of
# these the human typed. Matched on HOST alone, not on the full origin string,
# so serving on another port cannot turn a legitimate click into a refusal.
_LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})

_MAX_BODY = 64 * 1024

# The logger is fetched inline at each call site below rather than bound to a
# module-level `_LOG`. This is not style: BLE001 wants each `except Exception`
# handler to hold a logging call it can STATICALLY resolve to the logging
# module and that carries the traceback, and a module-level alias defeats that
# resolution -- `ruff check agentorg` goes red with three BLE001s and there is
# no noqa to spend. Measured on this file, and already documented at
# agentorg/agents/security.py:19-24 for the same reason.


class _Refused(Exception):
    """A request this server will not act on, carrying the sentence to show.

    Everything refused -- malformed field, unknown word, wrong-typed value,
    unknown run, decided gate, dead run, cross-site origin -- raises this, and
    `Handler.do_POST` catches it in exactly one place. One type and one catch
    rather than a branch per case, because the failure that loses a security
    gate is not the explicit reject; it is one branch out of several falling
    through to approval.
    """


def _awaiting() -> tuple[dict[str, list[str]], int]:
    """Which runs want a human, and how many state files could not be read.

    Returns ({run_id: [gates awaiting]}, unreadable_count).

    A run awaits a decision iff it has an open pause event for a gate that has
    no decision recorded, and is not already over:

        paused - decided, where paused comes from log.read(run_id)

    THE OBVIOUS FILTER IS WRONG, measured on this machine's 3466 state files:
    215 runs read `status == "running"` but only 129 are genuinely awaiting a
    decision -- status alone over-counts by 86, a 67% INFLATION, and shows
    abandoned runs as actionable. THE RATIO IS THE DURABLE PART: `runs/` is
    gitignored scratch that every `pytest -q` grows, and an earlier measurement
    over 3225 files read 200/120/80 -- the same 67%. So re-measure rather than
    trusting the integers. `gates_cli._list` filters nothing at all and would
    print one line per file.

    The unreadable count is RETURNED rather than swallowed. A truncated state
    file and a run with nothing open are both "not listed", and rendering them
    identically is the same silent conflation this codebase keeps paying for --
    so `_page` says how many were skipped whenever any were.
    """
    awaiting: dict[str, list[str]] = {}
    unreadable = 0
    for path in sorted(_RUNS.glob("*.state.json")):
        run_id = path.name.removesuffix(".state.json")
        try:
            state = RunState.model_validate_json(path.read_text())
        except Exception:
            # Truncated, mid-write, or written by an older contract. Broad on
            # purpose: one bad file must not blank the whole screen.
            logging.getLogger(__name__).warning(
                "could not read state file %s", path.name, exc_info=True)
            unreadable += 1
            continue
        if state.status in _TERMINAL:
            # Cheap first, so the log read below happens only for the runs
            # that could still be pending: measured, 215 log reads rather than
            # 3466 -- 16x less I/O for the same answer.
            continue
        try:
            decided = {d.gate for d in state.decisions}
            paused = {e.stage for e in log.read(run_id)
                      if e.action == "opened" and _PAUSE_MARKER in e.summary}
        except Exception:
            logging.getLogger(__name__).warning(
                "could not read log for %s", run_id, exc_info=True)
            unreadable += 1
            continue
        open_gates = sorted(paused - decided)
        if open_gates:
            awaiting[run_id] = open_gates
    return awaiting, unreadable


def _one(form: dict[str, list[str]], field: str,
         allowed: Container[str], expected: str) -> str:
    """The single allowed value of `field`, or refuse. `allowed` is a container.

    Absent, empty, repeated, and unknown-word all arrive here and leave the
    same way. Two of those collapse before this function even runs: `parse_qs`
    without keep_blank_values drops `field=` entirely, so "absent" and "empty"
    are one shape rather than two (verified: parse_qs("decision=") == {}).

    `expected` is prose rather than the container's contents, because the
    container for run_id is every pending run on disk -- 129 of them here -- and
    a refusal page is not a place to print them. It also keeps the message from
    echoing the offending value back: that is attacker-controlled text on a page
    rendered to a human, and the human does not need it to fix a mis-clicked
    form.
    """
    values = form.get(field) or []
    if len(values) != 1 or values[0] not in allowed:
        raise _Refused(f"{field} must be exactly one {expected}. "
                       f"Nothing else is read as a decision, and nothing "
                       f"was recorded.")
    return values[0]


def _check_origin(origin: str | None) -> None:
    """Refuse a POST that came from another site's page.

    Absent is allowed: curl and the CLI send no Origin, and the documented
    fallback path must keep working. Present must be loopback -- a cross-site
    form POST carries the attacker's origin, and localhost binding does not stop
    the browser on this laptop from making one.
    """
    if origin is None:
        return
    if urlparse(origin).hostname not in _LOOPBACK:
        raise _Refused(
            "this request came from another site's page and was not acted on; "
            "open http://127.0.0.1:8000 directly to decide a gate"
        )


def _apply(form: dict[str, list[str]], origin: str | None = None) -> str:
    """Record one decision, or raise _Refused. The only caller of gates.resume.

    Every guard is above the single `gates.resume` call, so there is no path to
    it that has not passed all of them -- pinned structurally by
    test_gates_resume_is_reached_from_exactly_one_place.

    `_awaiting()` is called ONCE and both run_id and gate are checked against
    that one snapshot. Calling it per field would scan 3466 state files twice
    per click, and worse, would check the two halves of one (run, gate) pair
    against two different readings of a directory other processes write to.
    """
    _check_origin(origin)
    gate = _one(form, "gate", _GATES, f"of {', '.join(_GATES)}")
    decision = _one(form, "decision", _DECISIONS, f"of {', '.join(_DECISIONS)}")
    awaiting, _ = _awaiting()
    run_id = _one(form, "run_id", awaiting,
                  "run id that is currently awaiting a decision")
    if gate not in awaiting[run_id]:
        raise _Refused(
            f"{run_id} is not awaiting a decision at {gate} — it is already "
            f"decided there, or the run is over. Nothing was recorded."
        )
    state = gates.resume(run_id, HumanDecision(gate=gate, decision=decision,
                                               by="ui-reviewer"))
    return f"{run_id}: {decision} -> status={state.status}"


def _page(msg: str = "", error: str = "") -> bytes:
    """The whole screen. Everything interpolated is escaped right here.

    Escaped at the point of USE, once, adjacent to the interpolation, so the
    invariant is local and readable: nothing reaches this HTML unescaped, and
    nothing is escaped twice on the way.
    """
    awaiting, unreadable = _awaiting()
    items = "".join(
        "<form method=post action=/decide>"
        f"<input type=hidden name=run_id value='{html.escape(rid)}'>"
        f"<code>{html.escape(rid)}</code> "
        "gate <select name=gate>"
        + "".join(f"<option>{html.escape(g)}</option>" for g in gates_open)
        + "</select> "
        '<button name=decision value=approved>Approve</button> '
        '<button name=decision value=rejected>Reject</button></form>'
        for rid, gates_open in awaiting.items())
    # Said rather than omitted: a file that could not be read is not the same
    # fact as a run with nothing open, and the two are indistinguishable in a
    # list that shows neither.
    skipped = (f"<p class=warn>{unreadable} state file(s) could not be read and "
               f"are not listed — see the server log.</p>") if unreadable else ""
    body = f"""<!doctype html><meta charset=utf-8><title>Approve / Reject</title>
<style>body{{font:15px system-ui;margin:2rem}}form{{margin:.4rem 0}}
.msg{{color:#238636}}.err{{color:#cf222e;font-weight:600}}.warn{{color:#9a6700}}
.auth{{color:#57606a;font-size:.85em;margin-top:2rem}}</style>
<h1>Paused runs</h1>
<p class=msg>{html.escape(msg)}</p>
<p class=err>{html.escape(error)}</p>
{skipped}{items or '<p>(no runs awaiting a decision)</p>'}
<p class=auth>No authentication. Localhost only — never expose this off-host.</p>"""
    return body.encode()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
        """Request log to `logging`, not to stderr.

        The base class writes every request line to stderr, which under pytest
        is noise and on a projector is a scrolling wall next to the screen the
        judges are meant to read.
        """
        logging.getLogger(__name__).info("%s - %s", self.address_string(),
                                        fmt % args)

    def _send(self, body: bytes, code: int = 200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        """Render. NEVER mutate -- that is what makes POST-only meaningful.

        /decide is reachable by GET (a browser back-button, a bookmark, a
        prefetch) and must be inert when it is, so it renders the list like any
        other path rather than replaying a decision.
        """
        if urlparse(self.path).path in ("/", "/decide"):
            self._send(_page())
        else:
            self._send(_page(error=f"no such page: {self.path}"), code=404)

    def do_POST(self) -> None:
        """One decision, or one honest page. Never a traceback to the client.

        THREE clauses, in this order, and the order is the whole design:

          * _Refused first -- the intended failure, with a sentence written for
            the human who caused it.
          * Exception second -- anything unforeseen. Without it, an unhandled
            error escapes into socketserver, which prints the traceback to
            stderr and drops the connection: a demo failure on a projector even
            when the refusal underneath it was correct.
          * nothing broader. `Exception`, not a bare `except:` and not
            `BaseException`: tests/conftest.py's four autouse guards raise
            pytest.fail's `Failed`, which derives from BaseException precisely
            so a blind handler like this one cannot swallow them. Widening this
            clause would turn "this test reached the real terminal" into a
            green pass and an error page.
        """
        try:
            body = self.rfile.read(min(int(self.headers.get("Content-Length") or 0),
                                       _MAX_BODY))
            form = parse_qs(body.decode())
            self._send(_page(msg=_apply(form, self.headers.get("Origin"))))
        except _Refused as refusal:
            self._send(_page(error=str(refusal)), code=400)
        except Exception:
            # The message is generic on purpose: the detail goes to the server
            # log, not to the client, so nothing about this machine's paths or
            # internals renders on a page anyone can reach.
            logging.getLogger(__name__).exception(
                "unhandled error serving POST %s", self.path)
            self._send(_page(error="that request could not be processed; "
                                   "nothing was recorded"), code=400)


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 8000), Handler)
    print("approve/reject screen on http://127.0.0.1:8000  (Ctrl-C to stop)")
    print("NO AUTHENTICATION — localhost only. Do not expose this off-host.")
    server.serve_forever()


if __name__ == "__main__":
    main()
