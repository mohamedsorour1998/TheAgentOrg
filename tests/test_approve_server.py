"""Tests for agentorg/approve_server.py — the approve/reject screen.

Owner: Sorour.

WHAT THIS FILE IS MOSTLY ABOUT: the things the server REFUSES. The screen's
happy path is four lines over `gates.resume`, and the demo works if it renders.
Everything else here exists because of one measured gap.

=========================================================================
THE PHANTOM POST-REJECTION APPROVAL
=========================================================================

`gates.resume` sets `state.status` only when the decision is "rejected"
(gates.py:86-87) and never un-sets it. So approving a run the graph already
rejected leaves `status == "rejected"` -- which LOOKS correct -- while still
appending the approval to `state.decisions` and still writing a
`human / <gate> / approved` row to the log. Nothing refuses the attempt.

MEASURED, against real files, with the CLI path (see the last test in this file):

    decisions = [('gate1','approved'), ('gate1','rejected'), ('gate2','approved')]
    log rows  = [..., ('human','gate1','rejected'), ('human','gate2','approved')]

Since Task 2, `agentorg/timeline.py` renders that log, so the phantom is not
only a data-integrity question: it displays as `✓ gate2 human approved` AFTER
`✗ gate1 human rejected` on the screen the judges read.

THEREFORE, AND THIS IS THE POINT OF THE FILE: a test that asserts only
`status == "rejected"` PASSES against the gap. It cannot fail. It is the exact
shape this repository has shipped fourteen times. Every test here that claims to
pin the refusal asserts on the DECISIONS LIST and on the LOG, and
`test_asserting_only_on_status_cannot_detect_the_phantom` demonstrates by
construction why -- it drives the unguarded path and shows `status` agreeing
while the record is wrong.

=========================================================================
HERMETIC RUNS DIRECTORY -- ALL THREE CONSTANTS, NOT ONE
=========================================================================

`runs/` holds 3466 state files, 129 of them genuinely awaiting a decision, and
every `pytest -q` adds more -- it was 3225/120 an hour earlier. A listing test
that read it would depend on that moving corpus and on whatever other tests just
wrote there.

So `_hermetic` redirects all THREE module-level runs constants to one tmp_path:

    approve_server._RUNS   the listing and the guard read this
    gates._STATE_DIR       gates.save / pause / resume write here
    log._LOG_DIR           log.append / read use this

They are separate module-level constants that merely happen to resolve to the
same directory, so patching one leaves the other two pointed at the real repo --
which would half-work, in the most confusing possible way. `_hermetic` is
autouse in this file, and
`test_the_hermetic_fixture_redirects_every_constant_the_code_path_touches`
fails if a fourth writer appears that it does not cover.
"""

import ast
import io
import pathlib

import pytest

from agentorg import approve_server, gates, log, timeline
from agentorg.state import HumanDecision, RunState

TICKET = "Add a per-IP login rate limit."

# Every runs-directory constant any code path under test touches. Named once,
# used by the fixture AND by the test that proves the fixture is complete.
_RUNS_CONSTANTS = (
    (approve_server, "_RUNS"),
    (gates, "_STATE_DIR"),
    (log, "_LOG_DIR"),
)


@pytest.fixture(autouse=True)
def _hermetic(tmp_path, monkeypatch):
    """Point every runs-directory constant at one empty tmp_path.

    Autouse for the whole file: a test that forgot it would read the repo's
    3466 real state files and pass or fail depending on them.
    """
    runs = tmp_path / "runs"
    runs.mkdir()
    for module, name in _RUNS_CONSTANTS:
        monkeypatch.setattr(module, name, runs)
    return runs


def _paused(gate: str = "gate1", ticket: str = "T-1") -> str:
    """A run paused at `gate`, exactly as the graph leaves one. Returns run_id."""
    state = RunState(ticket_id=ticket, ticket_text=TICKET)
    gates.pause(state, gate)
    return state.run_id


def _on_disk(run_id: str) -> RunState:
    """The run as the next process would find it — the server's whole input."""
    return RunState.model_validate_json(gates._state_path(run_id).read_text())


def _decided(run_id: str) -> list[tuple[str, str]]:
    return [(d.gate, d.decision) for d in _on_disk(run_id).decisions]


def _rows(run_id: str) -> list[tuple[str, str, str]]:
    return [(e.actor, e.stage, e.action) for e in log.read(run_id)]


def _form(run_id: str, gate: str, decision: str) -> dict[str, list[str]]:
    return {"run_id": [run_id], "gate": [gate], "decision": [decision]}


# Sentinel meaning "this field is not in the POST at all". A plain `{}` override
# CANNOT express that: `form | {}` is the unmodified valid form, so the three
# `absent` cases below originally asserted that the HAPPY PATH must be refused
# and failed for that reason -- a test that could not pass, which is the same
# class of defect as one that cannot fail, and found the same way.
_ABSENT = object()


def _with(form: dict[str, list[str]], override: dict) -> dict[str, list[str]]:
    """`form` with `override` applied, where _ABSENT removes the field."""
    out = dict(form)
    for field, value in override.items():
        if value is _ABSENT:
            out.pop(field, None)
        else:
            out[field] = value
    return out


# =========================================================================
# THE SPEC'S DONE-WHEN. `docs/plan/sorour/week3.md:145-250` is written against
# these exact strings, and so is the demo script.
# =========================================================================

def test_the_specs_done_when_approves_a_paused_gate_from_the_screen():
    """create a paused state file, approve it, confirm the status is correct."""
    run_id = _paused("gate1")

    msg = approve_server._apply(_form(run_id, "gate1", "approved"))

    assert msg == f"{run_id}: approved -> status=running"
    assert _on_disk(run_id).status == "running"
    assert _decided(run_id) == [("gate1", "approved")]


def test_the_specs_done_when_string_reaches_the_page_html_escaped():
    """The spec's expected line, as it actually renders.

    The spec says the screen shows `<RID>: approved -> status=running`. That
    exact string is the `_apply` return value, but the PAGE carries it as
    `-&gt;`, because `>` is escaped on the way into the HTML. Both are correct
    and the distinction is worth pinning: a demo script grepping the rendered
    page for the raw `->` would not find it, and would look like a broken
    screen rather than a correctly escaped one. (Found by a live-socket probe
    asserting the raw string against the page body.)
    """
    run_id = _paused("gate1")

    msg = approve_server._apply(_form(run_id, "gate1", "approved"))
    page = approve_server._page(msg=msg).decode()

    assert msg == f"{run_id}: approved -> status=running"
    assert f"{run_id}: approved -&gt; status=running" in page
    assert msg not in page, "the raw '->' would mean the message was not escaped"


def test_rejecting_from_the_screen_puts_the_run_in_rejected():
    run_id = _paused("gate1")

    msg = approve_server._apply(_form(run_id, "gate1", "rejected"))

    assert msg == f"{run_id}: rejected -> status=rejected"
    assert _on_disk(run_id).status == "rejected"


def test_a_decision_made_through_the_screen_survives_a_second_one():
    """Property 1 from the plan: a decision persists, and is not replaced.

    `gates.resume` writes back at gates.py:88, which is what makes this work --
    before it did, two sequential decisions returned only the second and the
    file held neither. Pinned here rather than assumed, because this server is
    the caller that decides one gate per click, which is the shape that broke.
    """
    run_id = _paused("gate1")
    approve_server._apply(_form(run_id, "gate1", "approved"))

    state = RunState.model_validate_json(gates._state_path(run_id).read_text())
    gates.pause(state, "gate2")
    approve_server._apply(_form(run_id, "gate2", "rejected"))

    assert _decided(run_id) == [("gate1", "approved"), ("gate2", "rejected")]
    assert _on_disk(run_id).status == "rejected"


# =========================================================================
# THE PHANTOM POST-REJECTION APPROVAL. Asserted on the decisions list AND the
# log, never on status alone -- see the module docstring.
# =========================================================================

def _rejected_with_a_gate_still_open() -> str:
    """A dead run that still has an open pause event — the reachable phantom.

    Built the way it actually happens: the run pauses at gate1, is approved,
    pauses at gate2, and is then rejected at gate1 out of band (the CLI, or a
    second browser tab). `gates.pause` for gate2 has already been written, so
    the log still says gate2 is open while the run is over. That is the state in
    which an approval must be refused.
    """
    run_id = _paused("gate1")
    approve_server._apply(_form(run_id, "gate1", "approved"))
    state = RunState.model_validate_json(gates._state_path(run_id).read_text())
    gates.pause(state, "gate2")
    gates.resume(run_id, HumanDecision(gate="gate1", decision="rejected",
                                       by="cli-out-of-band"))
    return run_id


def test_a_rejected_run_cannot_be_approved_through_the_screen():
    """Property 2 from the plan, and the reason this task exists.

    THE ASSERTIONS THAT MATTER are the second and third: no phantom decision
    appended, no approval row logged. `status` is asserted too, but it would
    hold even with no guard at all, so on its own it proves nothing.
    """
    run_id = _rejected_with_a_gate_still_open()
    before_decisions = _decided(run_id)
    before_rows = _rows(run_id)

    with pytest.raises(approve_server._Refused):
        approve_server._apply(_form(run_id, "gate2", "approved"))

    assert _decided(run_id) == before_decisions, "a phantom decision was appended"
    assert _rows(run_id) == before_rows, "a phantom row reached the log"
    assert ("human", "gate2", "approved") not in _rows(run_id)
    assert _on_disk(run_id).status == "rejected"


def test_no_approval_row_follows_the_rejection_on_the_timeline():
    """The demo-visible acceptance check, through Task 2's renderer.

    The phantom's real cost is on the screen: `agentorg/timeline.py` reads the
    log, so an approval written after a rejection renders as `✓ ... approved`
    BELOW `✗ ... rejected` -- a rejected run displaying a later approval, on the
    timeline the judges read. Asserted on ORDER, not on absence: an approval
    row earlier in the run is correct and must stay.
    """
    run_id = _rejected_with_a_gate_still_open()
    with pytest.raises(approve_server._Refused):
        approve_server._apply(_form(run_id, "gate2", "approved"))

    rendered = timeline.render_text(run_id)
    lines = rendered.splitlines()
    rejected_at = next(i for i, ln in enumerate(lines) if "rejected" in ln)
    approvals_after = [ln for ln in lines[rejected_at + 1:] if "approved" in ln]

    assert not approvals_after, (
        f"an approval renders after the rejection:\n{rendered}")


def test_asserting_only_on_status_cannot_detect_the_phantom():
    """Proof that `status` alone is a test that cannot fail.

    This drives the UNGUARDED path -- `gates.resume` directly, which is what
    the CLI does -- and shows the exact reading a weaker test would take: status
    says "rejected", so the weaker assertion passes, while the decisions list
    and the log both carry an approval on a dead run.

    It is not asserting that gates.resume is correct. It is documenting, in a
    form that fails if it ever stops being true, WHY the tests above assert on
    the record rather than on the status field. If this test starts failing,
    `gates.py` grew a guard and the two tests above should be re-read, not
    deleted.
    """
    run_id = _rejected_with_a_gate_still_open()

    gates.resume(run_id, HumanDecision(gate="gate2", decision="approved",
                                       by="cli"))

    # The assertion a weaker test would have made -- and it passes.
    assert _on_disk(run_id).status == "rejected"
    # What that assertion cannot see:
    assert ("gate2", "approved") in _decided(run_id)
    assert ("human", "gate2", "approved") in _rows(run_id)


def test_a_gate_already_decided_cannot_be_decided_twice():
    """The other half of the guard: a live run, a gate that is already answered.

    THE RUN MUST STILL HAVE ANOTHER GATE OPEN, and that is not incidental --
    it is what makes this test able to fail at all. The first version paused
    only gate1: once gate1 was decided the run left `_awaiting` entirely, so
    `_one(run_id)` refused before the gate check was ever consulted, and
    deleting `if gate not in awaiting[run_id]` left the test GREEN. Mutation
    testing caught it (mutation 4 in the RED log), and the fix was the test.

    With gate2 still open the run is legitimately listed, `_one(run_id)`
    passes, and the gate check is the ONLY guard that can refuse a second
    decision on gate1 -- so the test now exercises the line it claims to pin.
    """
    run_id = _paused("gate1")
    state = _on_disk(run_id)
    gates.pause(state, "gate2")
    approve_server._apply(_form(run_id, "gate1", "approved"))
    before_decisions, before_rows = _decided(run_id), _rows(run_id)
    # The run is still listed -- so nothing upstream of the gate check refuses.
    assert run_id in approve_server._awaiting()[0]

    with pytest.raises(approve_server._Refused):
        approve_server._apply(_form(run_id, "gate1", "rejected"))

    assert _decided(run_id) == before_decisions
    assert _rows(run_id) == before_rows


@pytest.mark.parametrize("status", ["promoted", "blocked", "failed", "rejected"])
def test_no_decision_is_accepted_on_a_run_that_is_over(status):
    """Every terminal status, not just `rejected`.

    A promoted run is as dead as a rejected one, and `gates.resume` guards
    neither -- it only ever SETS status, on one of the four values. So the
    refusal is keyed on _TERMINAL rather than on `rejected`, and this proves all
    four rather than the one that motivated the task.
    """
    run_id = _paused("gate2")
    state = _on_disk(run_id)
    state.status = status
    gates.save(state)
    before_decisions, before_rows = _decided(run_id), _rows(run_id)

    with pytest.raises(approve_server._Refused):
        approve_server._apply(_form(run_id, "gate2", "approved"))

    assert _decided(run_id) == before_decisions
    assert _rows(run_id) == before_rows


def test_a_gate_the_run_never_paused_at_is_refused():
    """The join is `paused - decided`, so an un-opened gate is not decidable."""
    run_id = _paused("gate1")

    with pytest.raises(approve_server._Refused):
        approve_server._apply(_form(run_id, "gate3", "approved"))

    assert _decided(run_id) == []
    assert ("human", "gate3", "approved") not in _rows(run_id)


# =========================================================================
# WHAT THE REFUSAL MAKES WORSE. Every guard has a mirror-image failure --
# refusing something legitimate -- and a guard that refuses everything passes
# every refusal test above. These are the cases that must still WORK, plus the
# capabilities the narrowing genuinely COSTS, pinned so they stay known rather
# than being rediscovered as bugs.
# =========================================================================

@pytest.mark.parametrize("gate", ["gate1", "gate2", "gate3"])
@pytest.mark.parametrize("decision", ["approved", "rejected"])
def test_every_offered_button_on_a_live_run_still_works(gate, decision):
    """The full cross product of what the screen offers. Six combinations.

    This is the test that fails if a guard over-refuses. `test_..._done_when`
    covers one cell of this table; a narrowing that broke gate3 or broke reject
    while leaving gate1/approve intact would pass that test and fail here.
    """
    run_id = _paused(gate)

    msg = approve_server._apply(_form(run_id, gate, decision))

    assert msg.startswith(f"{run_id}: {decision}")
    assert _decided(run_id) == [(gate, decision)]
    assert ("human", gate, decision) in _rows(run_id)


def test_a_second_gate_on_the_same_run_is_still_decidable():
    """The sequential-click path the whole screen exists for.

    A guard keyed on "has this run any decision at all" would pass every
    refusal test in this file and break the normal two-gate walk. This is that
    guard's mirror image.
    """
    run_id = _paused("gate1")
    approve_server._apply(_form(run_id, "gate1", "approved"))
    gates.pause(_on_disk(run_id), "gate2")

    approve_server._apply(_form(run_id, "gate2", "approved"))

    assert _decided(run_id) == [("gate1", "approved"), ("gate2", "approved")]
    assert _on_disk(run_id).status == "running"


def test_overriding_a_security_block_is_not_possible_from_this_screen():
    """A COST of the narrowing, pinned deliberately rather than discovered later.

    A security block returns from the graph before gate2 ever opens
    (graph.py:209-228), so a blocked run has no open pause event and is never
    listed -- and `overridden` is not an offered decision anyway. Measured on a
    real poisoned pipeline run: status=blocked, log stages
    {plan, gate1, develop, review, security}, no gate2, not listed.

    So overriding a security block requires shell access:

        python -m agentorg.gates_cli resume <rid> --gate gate2 \
            --decision overridden --by <you>

    That is the intended trade -- it is the most dangerous thing this vocabulary
    can express and it should not be an unauthenticated click -- but it IS a
    capability this screen does not have, and the demo script must not promise
    it. If this test ever fails, someone widened the screen and needs to have
    that argument on purpose.
    """
    run_id = _paused("gate2")
    state = _on_disk(run_id)
    state.status = "blocked"
    gates.save(state)

    awaiting, _ = approve_server._awaiting()

    assert run_id not in awaiting
    assert "overridden" not in approve_server._DECISIONS
    with pytest.raises(approve_server._Refused):
        approve_server._apply(_form(run_id, "gate2", "rejected"))


def test_confirming_a_rejection_a_second_time_is_refused_not_silently_applied():
    """Another COST: the screen cannot re-affirm a decision already made.

    Refusing a duplicate REJECT is harmless for integrity -- it changes nothing
    either way -- but it means a human who clicks Reject twice sees a refusal
    rather than a confirmation. Named here so the wording on that page is a
    deliberate choice and not a surprise on the projector.
    """
    run_id = _paused("gate1")
    approve_server._apply(_form(run_id, "gate1", "rejected"))

    with pytest.raises(approve_server._Refused) as caught:
        approve_server._apply(_form(run_id, "gate1", "rejected"))

    assert "awaiting a decision" in str(caught.value)


def test_the_cli_fallback_is_unaffected_by_this_screens_refusals():
    """The cut-safe path must keep working — including where the screen refuses.

    `gates_cli resume` is the documented fallback and this task must not narrow
    it. This asserts the asymmetry ON PURPOSE: the screen refuses the phantom,
    the CLI still writes it. That is the shape of the remaining gap in
    `gates.py`, which this task is forbidden to fix and required to report.
    """
    run_id = _rejected_with_a_gate_still_open()
    with pytest.raises(approve_server._Refused):
        approve_server._apply(_form(run_id, "gate2", "approved"))

    gates.resume(run_id, HumanDecision(gate="gate2", decision="approved",
                                       by="sorour"))

    assert ("gate2", "approved") in _decided(run_id), (
        "the CLI fallback stopped working — this task must not narrow it")


# =========================================================================
# ONE REFUSAL PATH, NOT FOUR.
#
# The failure that loses a security gate is not the explicit reject -- it is one
# branch out of several falling through to approval. So every malformed shape of
# every field runs through ONE parametrized assertion, and the assertion is not
# "it raised": it is "nothing was recorded", which is the property that matters.
#
# graph.py:65-79 states the same rule for the terminal gate and explains what it
# cost to learn: a prefix match on "a" made "abort" mean APPROVE. Exact words,
# no case folding, no stripping.
# =========================================================================

_MALFORMED_DECISIONS = [
    pytest.param({"decision": _ABSENT}, id="absent"),
    pytest.param({"decision": [""]}, id="empty-string"),
    pytest.param({"decision": []}, id="empty-list"),
    pytest.param({"decision": ["approved", "rejected"]}, id="repeated-two"),
    pytest.param({"decision": ["approved", "approved"]}, id="repeated-same"),
    pytest.param({"decision": ["maybe"]}, id="unknown-word"),
    pytest.param({"decision": ["abort"]}, id="abort-must-not-approve"),
    pytest.param({"decision": ["a"]}, id="bare-a-is-not-approval-here"),
    pytest.param({"decision": ["APPROVED"]}, id="wrong-case"),
    pytest.param({"decision": ["Approved"]}, id="title-case"),
    pytest.param({"decision": ["approved "]}, id="trailing-space"),
    pytest.param({"decision": [" approved"]}, id="leading-space"),
    pytest.param({"decision": ["approve"]}, id="verb-not-past-participle"),
    pytest.param({"decision": ["approved\n"]}, id="trailing-newline"),
    pytest.param({"decision": ["overridden"]}, id="override-needs-the-cli"),
    pytest.param({"decision": ["<img src=x onerror=alert(1)>"]}, id="markup"),
]


@pytest.mark.parametrize("override", _MALFORMED_DECISIONS)
def test_no_malformed_decision_is_ever_read_as_an_approval(override):
    """Every shape `parse_qs` can hand us, on ONE assertion.

    Note `overridden` is in here. It is a VALID HumanDecision value that this
    screen deliberately does not offer -- see the module docstring's "COST OF
    THAT NARROWING" -- so it refuses like any other unoffered word. Overriding a
    security block is the most dangerous thing this vocabulary can express, and
    it requires shell access rather than an unauthenticated click.
    """
    run_id = _paused("gate1")
    form = _with(_form(run_id, "gate1", "approved"), override)

    with pytest.raises(approve_server._Refused):
        approve_server._apply(form)

    assert _decided(run_id) == [], "a malformed decision was recorded"
    assert ("human", "gate1", "approved") not in _rows(run_id)


@pytest.mark.parametrize("override", [
    pytest.param({"gate": _ABSENT}, id="absent"),
    pytest.param({"gate": []}, id="empty-list"),
    pytest.param({"gate": [""]}, id="empty-string"),
    pytest.param({"gate": ["gate1", "gate2"]}, id="repeated"),
    pytest.param({"gate": ["gate4"]}, id="unknown-gate"),
    pytest.param({"gate": ["GATE1"]}, id="wrong-case"),
    pytest.param({"gate": ["gate1 "]}, id="trailing-space"),
    pytest.param({"gate": ["plan"]}, id="a-stage-that-is-not-a-gate"),
])
def test_no_malformed_gate_is_ever_acted_on(override):
    run_id = _paused("gate1")
    form = _with(_form(run_id, "gate1", "approved"), override)

    with pytest.raises(approve_server._Refused):
        approve_server._apply(form)

    assert _decided(run_id) == []
    assert _rows(run_id) == [("system", "gate1", "opened")]


@pytest.mark.parametrize("override", [
    pytest.param({"run_id": _ABSENT}, id="absent"),
    pytest.param({"run_id": []}, id="empty-list"),
    pytest.param({"run_id": [""]}, id="empty-string"),
    pytest.param({"run_id": ["no-such-run"]}, id="unknown-run"),
    pytest.param({"run_id": ["../../etc/passwd"]}, id="path-traversal"),
    pytest.param({"run_id": ["../" * 8 + "runs/x"]}, id="deep-traversal"),
    pytest.param({"run_id": ["/etc/passwd"]}, id="absolute-path"),
    pytest.param({"run_id": ["."]}, id="dot"),
    pytest.param({"run_id": [".."]}, id="dotdot"),
    pytest.param({"run_id": ["a\x00b"]}, id="nul-byte"),
    pytest.param({"run_id": ["x" * 5000]}, id="absurdly-long"),
])
def test_no_malformed_run_id_reaches_the_filesystem(override):
    """An unknown run_id must be an honest refusal, never a FileNotFoundError.

    `gates.resume` reads the path unguarded (gates.py:78) and raises
    FileNotFoundError, which the spec's own do_POST would have surfaced as a 500
    traceback. And `gates._state_path` does no containment check at all: with
    run_id `../../etc/passwd` it resolves OUTSIDE runs/ entirely (verified).

    Neither is reachable from here, and not because of a pattern match -- an
    accepted run_id came out of `_RUNS.glob`, so it is a filename that exists in
    that directory by construction. `nul-byte` is the case that proves the
    difference matters: it cannot even be turned into a path without raising
    ValueError, and it still lands on the same refusal.
    """
    live = _paused("gate1")
    form = _with(_form(live, "gate1", "approved"), override)

    with pytest.raises(approve_server._Refused):
        approve_server._apply(form)

    assert _decided(live) == []


def test_every_field_missing_at_once_is_still_one_honest_refusal():
    """The empty POST — a form submitted with nothing in it."""
    with pytest.raises(approve_server._Refused):
        approve_server._apply({})


def test_the_refusal_never_echoes_the_submitted_value_back():
    """A refusal page must not reflect attacker-controlled text.

    Escaping makes reflected markup inert, and the page escapes. This is the
    layer under that: the message never contains the offending value at all, so
    the escaping is a second line of defence rather than the only one.
    """
    payload = "<img src=x onerror=alert(1)>"
    run_id = _paused("gate1")

    with pytest.raises(approve_server._Refused) as caught:
        approve_server._apply(_form(run_id, "gate1", payload))

    assert payload not in str(caught.value)
    assert "img" not in str(caught.value)


# =========================================================================
# THE LISTING. Not `gates_cli list`, which prints one line per file.
# =========================================================================

def test_only_runs_awaiting_a_decision_are_listed():
    """The join, on four runs that differ in exactly one way each."""
    waiting = _paused("gate1", "WAIT-1")
    decided = _paused("gate1", "DONE-1")
    approve_server._apply(_form(decided, "gate1", "approved"))
    never_paused = RunState(ticket_id="FRESH-1", ticket_text=TICKET)
    gates.save(never_paused)
    dead = _rejected_with_a_gate_still_open()

    awaiting, unreadable = approve_server._awaiting()

    assert list(awaiting) == [waiting]
    assert awaiting[waiting] == ["gate1"]
    assert decided not in awaiting, "a decided gate is not awaiting anything"
    assert never_paused.run_id not in awaiting, "never paused, so never awaiting"
    assert dead not in awaiting, "a rejected run must not be offered a button"
    assert unreadable == 0


def test_status_running_is_not_the_filter():
    """The measured defect in the obvious predicate, reproduced in miniature.

    Over the repo's real corpus: 215 runs read `status == "running"` but only
    129 are genuinely awaiting a decision -- status alone over-counts by 86.
    `dead` here is the mirror case that makes the join necessary in both
    directions: it is NOT running and must not be listed, while `abandoned` IS
    running and must not be listed either.
    """
    abandoned = _paused("gate1", "ABANDONED-1")
    approve_server._apply(_form(abandoned, "gate1", "approved"))
    waiting = _paused("gate1", "WAIT-1")

    awaiting, _ = approve_server._awaiting()

    assert _on_disk(abandoned).status == "running", "still 'running' on disk"
    assert abandoned not in awaiting, "'running' is not the same as awaiting"
    assert list(awaiting) == [waiting]


def test_a_run_paused_at_two_gates_offers_both():
    """Multiple open gates on one run, in a stable order."""
    run_id = _paused("gate1")
    state = _on_disk(run_id)
    gates.pause(state, "gate3")

    awaiting, _ = approve_server._awaiting()

    assert awaiting[run_id] == ["gate1", "gate3"]


def test_an_unreadable_state_file_is_counted_not_silently_skipped():
    """A truncated file and a run with nothing open must not render alike.

    Both are "absent from the list". Reporting nothing would make a corrupted
    corpus look like an empty queue, which is the silent conflation this
    codebase keeps paying for -- so the count is returned and the page says so.
    """
    waiting = _paused("gate1")
    (approve_server._RUNS / "truncated.state.json").write_text('{"ticket_id":')
    (approve_server._RUNS / "notjson.state.json").write_text("this is not json")

    awaiting, unreadable = approve_server._awaiting()

    assert list(awaiting) == [waiting]
    assert unreadable == 2
    assert b"2 state file(s) could not be read" in approve_server._page()


def test_one_unreadable_file_does_not_blank_the_whole_screen():
    """The reason the except clause in _awaiting is broad."""
    waiting = _paused("gate1")
    (approve_server._RUNS / "aaa-first-alphabetically.state.json").write_text("{")

    page = approve_server._page()

    assert waiting.encode() in page, "a bad file hid a real pending run"


def test_the_empty_queue_says_so_rather_than_rendering_an_empty_page():
    page = approve_server._page()

    assert b"(no runs awaiting a decision)" in page


def test_the_listing_does_not_read_the_log_of_every_run_on_disk(monkeypatch):
    """Cost, pinned. Terminal runs are excluded BEFORE their log is read.

    Measured over the repo's corpus: 3466 state files, 215 non-terminal. Reading
    a log per state file rather than per candidate is 16x the I/O for the same
    answer, on a screen that re-renders on every click. This is a performance
    property, so it is pinned by counting calls rather than by timing anything.
    """
    reads: list[str] = []
    real_read = log.read
    monkeypatch.setattr(log, "read",
                        lambda rid: reads.append(rid) or real_read(rid))
    waiting = _paused("gate1", "WAIT-1")
    for status in ("promoted", "blocked", "failed", "rejected"):
        dead = _paused("gate1", f"DEAD-{status}")
        state = _on_disk(dead)
        state.status = status
        gates.save(state)

    approve_server._awaiting()

    assert reads == [waiting], "a terminal run's log was read for nothing"


# =========================================================================
# THE HTTP SURFACE: POST-only, honest errors, no traceback to the client.
# =========================================================================

def test_markup_in_a_run_id_is_escaped_in_the_page():
    """The listing interpolates a run_id, and a run_id CAN carry markup.

    Verified reachable rather than assumed: `gates.pause` accepts a RunState
    whose run_id is `<img ...>` and writes that filename, and `_awaiting` then
    lists it. So this pins a real property -- not a fictional one, which is the
    other failure mode of a mutation-tested assertion.
    """
    evil = "<img src=x onerror=alert(1)>"
    state = RunState(run_id=evil, ticket_id="T-1", ticket_text=TICKET)
    gates.pause(state, "gate1")

    page = approve_server._page().decode()

    assert evil not in page, "markup reached the page raw"
    assert "&lt;img" in page, "the run id is not rendered at all"


def test_a_run_id_cannot_break_out_of_the_hidden_input_attribute():
    """The listing puts the run_id in a SINGLE-quoted attribute.

    `value='{run_id}'` is the spec's markup, and a single-quoted attribute is
    escapable with a bare apostrophe -- which `html.escape` covers only because
    its `quote` argument defaults to True and escapes `'` as well as `"`. That
    default is load-bearing here: `html.escape(payload, quote=False)` would
    leave this exact injection live while still passing a test that only checked
    for `&lt;`. So the payload is an attribute breakout with no angle brackets
    in it at all.
    """
    payload = "x' onfocus=alert(1) autofocus='"
    state = RunState(run_id=payload, ticket_id="T-1", ticket_text=TICKET)
    gates.pause(state, "gate1")

    page = approve_server._page().decode()

    # Asserted on the BYTES the browser parses. An earlier version un-escaped
    # `&#x27;` back to `'` before searching, which recreates the payload text
    # verbatim and can never pass -- it was checking whether the string exists,
    # not whether the quote is inert.
    assert "value='x&#x27;" in page, "the apostrophe was not escaped"
    assert "value='x'" not in page, "the attribute closed early"
    # The only apostrophes in the rendered attribute are the two delimiters the
    # module wrote itself; every one from the payload is an entity.
    attr = page.split("name=run_id value=")[1].split(">")[0]
    assert attr.count("'") == 2, f"unescaped quote inside the attribute: {attr}"


def test_the_message_and_error_lines_are_escaped():
    page = approve_server._page(msg="<b>m</b>", error="<i>e</i>").decode()

    assert "<b>m</b>" not in page
    assert "<i>e</i>" not in page
    assert "&lt;b&gt;" in page and "&lt;i&gt;" in page


def test_the_page_states_the_missing_authentication():
    """The one thing a reader of this screen must not have to infer."""
    page = approve_server._page().decode()

    assert "No authentication" in page
    assert "Localhost only" in page


def test_the_module_docstring_states_the_auth_gap_and_the_localhost_bound():
    """The docstring is where the next maintainer looks. Pinned, not trusted."""
    doc = approve_server.__doc__

    assert "THERE IS NO AUTHENTICATION" in doc
    assert "127.0.0.1" in doc
    assert "never" in doc.lower() and "off-host" in doc


def test_the_server_binds_loopback_only():
    """Never 0.0.0.0, read out of the CODE — main() serves forever, so it
    cannot be called and asked.

    Checked against real string constants rather than the source text: the
    docstring contains the sentence "never 0.0.0.0", which a grep reads as a
    violation of the property it is describing. See `_module_ast`.
    """
    strings = _non_docstring_strings(_module_ast())

    assert "127.0.0.1" in strings
    assert not [s for s in strings if "0.0.0.0" in s]
    bind = next(n for n in ast.walk(_module_ast())
                if isinstance(n, ast.Call)
                and getattr(n.func, "id", "") == "ThreadingHTTPServer")
    assert ast.unparse(bind.args[0]) == "('127.0.0.1', 8000)"


@pytest.mark.parametrize("origin", [
    "http://evil.example",
    "https://evil.example",
    "http://127.0.0.1.evil.example",
    "http://localhost.evil.example",
    "null",
])
def test_a_cross_site_post_is_refused(origin):
    """Loopback binding does NOT stop the browser on this laptop.

    A page on any origin can POST a form here; the request comes from the local
    browser, so the bind address is irrelevant. The Origin header is what tells
    the two apart. The two lookalike hostnames are the reason this matches on
    the parsed hostname rather than on a substring: `127.0.0.1.evil.example`
    contains "127.0.0.1".
    """
    run_id = _paused("gate1")

    with pytest.raises(approve_server._Refused):
        approve_server._apply(_form(run_id, "gate1", "approved"), origin=origin)

    assert _decided(run_id) == []


@pytest.mark.parametrize("origin", [
    None,
    "http://127.0.0.1:8000",
    "http://localhost:8000",
    "http://127.0.0.1:9999",
    "http://[::1]:8000",
])
def test_a_same_origin_post_still_works(origin):
    """The case that must still WORK — the mirror of the test above.

    Every guard has a mirror image, and a guard that refuses everything passes
    every refusal test in this file. `None` is here because curl and the
    documented CLI fallback send no Origin at all, and the port varies because
    matching the full origin string would refuse a legitimate click on any port
    but 8000.
    """
    run_id = _paused("gate1")

    approve_server._apply(_form(run_id, "gate1", "approved"), origin=origin)

    assert _decided(run_id) == [("gate1", "approved")]


def _module_ast() -> ast.Module:
    """The module, parsed. Structural tests read CODE, not prose.

    Three tests in this file first tried to grep the source text and all three
    were wrong in the same way: the strings they searched for appear in the
    module's own docstring and comments, which EXPLAIN the property rather than
    violating it. `"0.0.0.0" not in source` failed on the sentence "it binds
    127.0.0.1 only, never 0.0.0.0"; the bare-except regex matched the comment
    "not a bare `except:`"; the call count matched "over gates.resume()" in the
    docstring's first line. A grep cannot tell an implementation from a
    description of one, and the failure direction is what makes it worth fixing
    properly rather than tightening the pattern: prose that describes the right
    behaviour reads as a violation, and prose could equally hide a real one.
    """
    return ast.parse(pathlib.Path(approve_server.__file__).read_text())


def _non_docstring_strings(tree: ast.Module) -> list[str]:
    """Every string constant in the module that is not a docstring."""
    docstrings = {
        d for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef))
        and (d := ast.get_docstring(node, clean=False)) is not None
    }
    return [node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and node.value not in docstrings]


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    return next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == name)


def test_a_get_never_mutates_even_on_the_decide_path():
    """A GET that mutates a security gate is triggerable by a link or a prefetch.

    Pinned structurally: `do_GET`'s body must not be able to reach `_apply`.
    Asserted on the source of the method rather than by driving a socket,
    because the property is "there is no path", and one absent path is not
    something a request can demonstrate.
    """
    called = {ast.unparse(n.func)
              for n in ast.walk(_function(_module_ast(), "do_GET"))
              if isinstance(n, ast.Call)}

    assert called == {"_page", "self._send", "urlparse"}, called
    assert not [c for c in called if "apply" in c or "resume" in c]


def test_gates_resume_is_reached_from_exactly_one_place():
    """One call site, with every guard above it.

    Two call sites would mean two chances for one of them to skip the join, and
    that is exactly the fallthrough the whole design is arranged to prevent.
    """
    calls = [n for n in ast.walk(_module_ast())
             if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "resume"
             and getattr(n.func.value, "id", "") == "gates"]

    assert len(calls) == 1, f"gates.resume is called {len(calls)} times"


def test_the_handler_catches_exception_and_never_anything_broader():
    """conftest's four autouse guards raise pytest.fail's Failed.

    `Failed` derives from BaseException precisely so a blind handler cannot
    swallow it. `do_POST` needs a broad clause to avoid 500 tracebacks on a
    projector, so it catches `Exception` -- and a bare `except:` or
    `except BaseException:` there would turn "this test reached the real
    terminal" into a green pass and a friendly error page.
    """
    tries = [n for n in ast.walk(_function(_module_ast(), "do_POST"))
             if isinstance(n, ast.Try)]
    clauses = [("BARE" if h.type is None else ast.unparse(h.type))
               for t in tries for h in t.handlers]

    assert clauses == ["_Refused", "Exception"], clauses
    assert "BARE" not in clauses, "a bare except: would swallow pytest's Failed"
    assert "BaseException" not in clauses


class _FakeRequest:
    """The smallest thing `do_POST` needs, so it can be driven without a socket.

    `do_POST` is tested by calling it directly rather than over a real
    connection: the property under test is which clause catches and what reaches
    the CLIENT, and binding a port to prove that would add a listening socket
    and a race to every run for no extra coverage.

    An INSTANCE holds `sent` and `headers`, rather than a class attribute on a
    throwaway class. Two earlier versions used class-level mutable defaults,
    which ruff flags (RUF012) for exactly the reason that matters here: a
    class-level list is shared by every instance, so two tests driving do_POST
    would append into the same list and read each other's responses.
    """

    path = "/decide"

    def __init__(self, body: bytes = b"", origin: str | None = None):
        self.headers = {"Content-Length": str(len(body)), "Origin": origin}
        self.rfile = io.BytesIO(body)
        self.sent: list[tuple[bytes, int]] = []

    def _send(self, body: bytes, code: int = 200) -> None:
        self.sent.append((body, code))


def test_an_unexpected_error_renders_a_page_rather_than_a_traceback(monkeypatch):
    """The clause that stops a demo failure even when the refusal was right.

    Driven by making the ONE guarded call raise something nobody predicted, then
    asserting the client gets a page, the page does not name the exception, and
    no internal detail leaks. Patches `_apply` -- the single seam do_POST calls
    -- rather than `gates.resume`: patching both left the resume patch dead and
    the test asserting less than it appeared to, which ruff's F841 on the unused
    local is what surfaced.
    """
    def _explode(*_args, **_kwargs):
        raise RuntimeError("disk on fire at /Users/sorour/secret/path")

    monkeypatch.setattr(approve_server, "_apply", _explode)
    request = _FakeRequest()

    approve_server.Handler.do_POST(request)

    body, code = request.sent[0]
    assert code == 400
    assert b"could not be processed" in body
    assert b"disk on fire" not in body, "the exception text reached the client"
    assert b"/Users/sorour" not in body, "a filesystem path reached the client"
    assert b"Traceback" not in body
    assert b"RuntimeError" not in body


def test_a_refusal_renders_the_sentence_and_a_400():
    """The intended failure: an honest sentence, not a generic one."""
    request = _FakeRequest()

    approve_server.Handler.do_POST(request)

    body, code = request.sent[0]
    assert code == 400
    assert b"must be exactly one" in body
    # Keyed on the generic clause's OWN wording. "nothing was recorded" appears
    # in both messages by design -- it is the reassurance the human needs either
    # way -- so it cannot tell the two clauses apart, and asserting its absence
    # here failed against correct code.
    assert b"could not be processed" not in body, "the generic clause caught this"


def test_an_oversized_body_is_capped_rather_than_read_into_memory():
    """A Content-Length of 4 GB must not be believed."""
    assert approve_server._MAX_BODY == 64 * 1024
    reads = [n for n in ast.walk(_function(_module_ast(), "do_POST"))
             if isinstance(n, ast.Call) and ast.unparse(n.func) == "self.rfile.read"]

    assert len(reads) == 1
    assert "min(" in ast.unparse(reads[0]), ast.unparse(reads[0])
    assert "_MAX_BODY" in ast.unparse(reads[0])


# =========================================================================
# THE CONTRACT AND THE FIXTURE. Tests that fail when an assumption moves,
# instead of quietly describing a system that has changed underneath them.
# =========================================================================

def test_the_gates_this_screen_offers_are_exactly_the_ones_the_contract_allows():
    """Derived from HumanDecision's Literal, so a fourth gate fails here."""
    import typing

    hints = typing.get_type_hints(HumanDecision, include_extras=False)
    allowed = typing.get_args(hints["gate"])

    assert approve_server._GATES == allowed


def test_every_decision_this_screen_offers_is_a_valid_human_decision():
    """A button whose value pydantic rejects would 500 on the click."""
    for decision in approve_server._DECISIONS:
        HumanDecision(gate="gate1", decision=decision, by="ui-reviewer")


def test_the_narrowed_vocabulary_is_a_deliberate_subset():
    """`overridden` is valid and deliberately absent. Stated, so it stays known.

    If someone adds it, this test fails and they have to come and read the
    docstring's reasoning rather than discovering it later.
    """
    import typing

    hints = typing.get_type_hints(HumanDecision, include_extras=False)
    allowed = set(typing.get_args(hints["decision"]))

    assert set(approve_server._DECISIONS) < allowed
    assert allowed - set(approve_server._DECISIONS) == {"overridden"}


def test_a_real_gates_pause_is_what_the_listing_finds():
    """The listing keys on gates.pause's exact sentence (gates.py:61).

    Every other test here calls the real `gates.pause`, so this is already
    load-bearing throughout -- but stated once explicitly, so a change to that
    wording fails a test that NAMES the coupling rather than emptying the screen
    and failing twenty tests that look like they are about something else.
    """
    run_id = _paused("gate1")
    events = log.read(run_id)

    assert len(events) == 1
    assert events[0].action == "opened"
    assert approve_server._PAUSE_MARKER in events[0].summary
    assert approve_server._awaiting()[0] == {run_id: ["gate1"]}


def test_the_terminal_statuses_cover_every_ending_the_contract_has():
    """_TERMINAL must be every status that is not "running".

    A new terminal status that this set does not know would be treated as live,
    and decisions would be accepted on finished runs -- the exact class of bug
    this module exists to refuse.
    """
    import typing

    hints = typing.get_type_hints(RunState, include_extras=False)
    statuses = set(typing.get_args(hints["status"]))

    assert approve_server._TERMINAL == statuses - {"running"}


def test_the_hermetic_fixture_redirects_every_constant_the_code_path_touches():
    """A fourth runs-directory writer must fail here, not leak into the repo.

    `runs/` holds 3466 files and is gitignored scratch, so a leak does not fail
    anything -- it just silently makes this file's listing tests depend on the
    corpus. This is the test that notices.
    """
    import inspect

    covered = {(m.__name__, n) for m, n in _RUNS_CONSTANTS}
    found = set()
    for module in (approve_server, gates, log):
        for name, value in vars(module).items():
            if isinstance(value, pathlib.Path) and value.name == "runs":
                found.add((module.__name__, name))

    assert found == covered, f"an unpatched runs constant exists: {found - covered}"
    # And the patch actually took: every one now points at the tmp dir.
    for module, name in _RUNS_CONSTANTS:
        assert getattr(module, name) == approve_server._RUNS
        assert "runs" == approve_server._RUNS.name
        assert inspect.getmodule(module) is module
