"""Tests for agentorg/timeline.py — the run timeline the judges score.

OWNER: Sorour.

WHAT THESE ARE TESTED AGAINST, AND WHY IT IS NOT A FIXTURE

    Every assertion below runs `graph.run_pipeline` and renders the run_id it
    produced. `runs/` is gitignored, so a test that hardcoded one of the run_ids
    the controller produced by hand would pass on this machine and fail on every
    other one -- and a renderer that only works on synthetic input is not
    evidence, which is the point of the requirement. The conftest guards keep
    those runs offline and inside tmp_path; see tests/conftest.py.

    The pre-provenance case is the one exception that CANNOT come from a live
    run, because the whole claim is about rows written before the field existed.
    It is built by writing a LogEvent with `scan_provenance=""` -- which is the
    literal shape of every pre-week-3 row on disk -- through `log.append`, the
    same writer the graph uses. See `_legacy_run`.
"""

import json

import pytest

from agentorg import log, timeline
from agentorg.graph import run_pipeline
from agentorg.state import LogEvent

TICKET_TEXT = "Add a per-IP login rate limit."


def _blocked_run():
    state = run_pipeline("POISON-1", TICKET_TEXT, poisoned=True)
    assert state.status == "blocked", "the fixture for these tests must be a blocked run"
    return state


def _promoted_run():
    state = run_pipeline("CLEAN-1", TICKET_TEXT, poisoned=False)
    assert state.status == "promoted", "the fixture for these tests must be a promoted run"
    return state


# =========================================================================
# The spec's own contract: the CLI, the glyphs, the empty case. These are what
# the demo script and docs/plan/sorour/week3.md's done-when are written against.
# =========================================================================

def test_render_text_names_the_run_and_ticket():
    state = _promoted_run()
    out = timeline.render_text(state.run_id)
    assert f"Timeline for run {state.run_id}" in out
    assert "ticket CLEAN-1" in out


def test_an_unknown_run_id_renders_a_sentence_not_a_traceback():
    """A judged demo must never show a traceback, and log.read does not raise.

    The exact string is the spec's, because the demo script quotes it.
    """
    assert timeline.render_text("no-such-run") == "(no events for run no-such-run)"


def test_render_html_survives_an_unknown_run_id():
    """The HTML path has its own `events[0]` hazard the text path does not.

    render_text returns early on the empty case; render_html builds rows, a
    ticket id and a banner from a list that may be empty. Three chances to
    IndexError on the one input a judge is most likely to produce by mistyping.
    """
    out = timeline.render_html("no-such-run")
    assert "no-such-run" in out
    assert "<title>" in out


def test_every_action_the_log_can_write_has_a_glyph():
    """_MARK must cover LogEvent.action, or a stage renders as a bare bullet.

    Read off the Literal rather than listed by hand: a tenth action added to
    state.py would otherwise fall through `_MARK.get(..., "•")` silently and
    look exactly like `opened` on the projector.
    """
    from typing import get_args
    actions = set(get_args(LogEvent.model_fields["action"].annotation))
    assert actions <= set(timeline._MARK), (
        f"actions with no glyph: {sorted(actions - set(timeline._MARK))}"
    )


def test_the_specs_done_when_strings_render_verbatim():
    """The two lines docs/plan/sorour/week3.md's done-when names, exactly.

    Pinned because the demo script and the judges' expectations are built on
    them: a clean run "starting with a `system plan opened` line and ending with
    a `★ promote system promoted` line", and a poisoned run whose last line is
    `⛔ security ... blocked`.
    """
    clean = timeline.render_text(_promoted_run().run_id).splitlines()
    assert "• plan     system    opened" in clean[2], clean[2]
    assert "★ promote  system    promoted" in clean[-1], clean[-1]

    poisoned = [line for line in timeline.render_text(_blocked_run().run_id).splitlines()
                if not line.lstrip().startswith("↳")]
    assert "⛔ security" in poisoned[-1] and "blocked" in poisoned[-1], poisoned[-1]


# =========================================================================
# REQUIREMENT 1 -- blocked vs promoted, distinguishable AT A GLANCE.
#
# The spec's ⛔/★ glyphs are one character in a 24px column, on one row out of
# fourteen, in a list whose other rows also carry ✓ glyphs. So "at a glance"
# is tested as: the outcome is a WORD, near the TOP, and the two runs' banners
# are not equal.
# =========================================================================

def test_a_blocked_run_and_a_promoted_run_are_distinguishable_at_a_glance():
    """One screen, one word, no prose. The two runs must not read alike.

    Asserts on the SECOND line of each -- above the rows -- because an outcome
    only findable by reading to the end of the list is the thing this replaces.
    Both halves are asserted, not just the block: a renderer that stamped
    "BLOCKED" on everything would pass a blocked-only test.
    """
    blocked = timeline.render_text(_blocked_run().run_id).splitlines()
    promoted = timeline.render_text(_promoted_run().run_id).splitlines()

    assert "BLOCKED" in blocked[1], blocked[1]
    assert "PROMOTED" in promoted[1], promoted[1]
    assert blocked[1] != promoted[1]
    # And the words must not both appear on either screen, or "which happened?"
    # is back to being a reading exercise.
    assert "PROMOTED" not in blocked[1]
    assert "BLOCKED" not in promoted[1]


def test_the_html_outcome_is_not_signalled_by_colour_alone():
    """A washed-out projector and a colour-blind judge must both still read it.

    The banner carries the word and the glyph as TEXT, so the CSS class is an
    enhancement rather than the signal.
    """
    html_out = timeline.render_html(_blocked_run().run_id)
    assert "BLOCKED" in html_out
    assert "⛔" in html_out
    assert "banner blocked" in html_out


def test_an_unfinished_run_is_not_reported_as_either_outcome():
    """A run with no ending must not be given one.

    `_outcome` reads the last event's action. A run abandoned at a gate ends on
    `opened`, and forcing that into PROMOTED or BLOCKED would be the renderer
    inventing an ending the pipeline never wrote -- the same class of error as
    guessing provenance.
    """
    state = run_pipeline("CLEAN-1", TICKET_TEXT)
    log.append(LogEvent(run_id=state.run_id, ticket_id=state.ticket_id,
                        actor="system", stage="gate2", action="opened",
                        summary="paused at gate2 awaiting human decision"))
    banner = timeline.render_text(state.run_id).splitlines()[1]
    assert "INCOMPLETE" in banner, banner
    assert "PROMOTED" not in banner and "BLOCKED" not in banner


# =========================================================================
# REQUIREMENT 2 -- three delivery states, not two.
#
# MEASURED over every runs/*.jsonl on this machine, counting blocked rows that
# carry a delivery ref: local:// 828, comment:// 62, https:// 28 = 918 rows, so
# local:// is 828/918 = 90%. The RATIO is the durable finding; the absolutes are
# gitignored scratch that every `pytest -q` adds to, so re-measure rather than
# trusting the integers. Rendering "not https" as undelivered would misreport
# ~90% of real blocked runs -- including the offline path the demo actually runs
# on -- as "nobody was told".
# =========================================================================

@pytest.mark.parametrize("ref,expect_reported,expect_phrase", [
    ("https://github.com/o/r/pull/41#issuecomment-9001", True, "posted to the PR"),
    ("local://runs/offline-demo/NOTES.md", True, "offline notes file"),
    ("comment://a-run-id", False, "reached nobody"),
])
def test_each_of_the_three_delivery_refs_renders_distinctly(ref, expect_reported,
                                                            expect_phrase):
    """All three of post_comment's return shapes, told apart.

    Parametrised over the SCHEMES rather than asserting only on the two the
    plan's text named, because github_ops returns three and the middle one is
    90% of the corpus.
    """
    event = LogEvent(run_id="r", ticket_id="t", actor="system", stage="security",
                     action="blocked", artifact_ref=ref,
                     summary=f"pipeline halted by block rule; block reason {ref}")
    state, detail = timeline._delivery(event)
    assert (state == "reported") is expect_reported, (ref, state)
    assert expect_phrase in detail


def test_the_offline_delivery_is_rendered_as_a_success_on_a_real_run():
    """local:// is a DELIVERY. This is the case the demo actually produces.

    github_ops returns `local://` only after the bytes reach disk, so calling it
    undelivered would mark a working offline demo as a failure on a projector.
    Run through the real pipeline, so the ref is the one github_ops produced.
    """
    state = _blocked_run()
    out = timeline.render_text(state.run_id)
    assert "delivery: reported" in out, out
    assert "offline notes file" in out
    assert "NOT REPORTED" not in out


def test_an_undelivered_block_reason_is_rendered_as_not_reported(monkeypatch):
    """A block nobody was told about is a different outcome. Say so.

    The failure is injected at `github_ops.post_comment`'s return value rather
    than by breaking the NOTES path, because the claim is about how the RENDERER
    reads a `comment://` ref -- not about how one gets produced, which
    tests/test_offline_mode.py already pins.
    """
    from agentorg import github_ops
    monkeypatch.setattr(github_ops, "post_comment",
                        lambda state, body, finding=None: f"comment://{state.run_id}")
    state = _blocked_run()
    out = timeline.render_text(state.run_id)
    assert "delivery: NOT REPORTED" in out, out
    assert "reached nobody" in out


def test_a_promoted_run_claims_no_delivery_at_all():
    """The PR row's summary contains `local://` too -- and it is NOT a delivery.

    `PR local://agent-org/CLEAN-1-6361c5b` is a branch. A renderer keying on the
    scheme anywhere in a row would announce "block reason written to the offline
    notes file" on every clean run that never had a block reason. This is the
    case that must still work, and it is the mirror image of the bug that
    rendering three states invites.
    """
    out = timeline.render_text(_promoted_run().run_id)
    assert "local://agent-org/" in out, "the PR row must still be rendered"
    assert "delivery:" not in out, out
    assert "block reason" not in out


def test_a_legacy_row_carrying_the_ref_only_in_its_summary_still_classifies():
    """Every row already on disk has NO artifact_ref. They must still read.

    This is the case that populating `artifact_ref` at the call site could have
    quietly broken: every one of those rows carries the ref only inside
    "pipeline halted by block rule; block reason <ref>", so a renderer that read
    the new field alone would go blind on the entire existing corpus while every
    test driven by a fresh pipeline run stayed green.
    """
    for ref, expected in [
        ("local://runs/offline-demo/NOTES.md", "reported"),
        ("comment://abc", "NOT REPORTED"),
        ("https://gh/x#c1", "reported"),
    ]:
        event = LogEvent(run_id="r", ticket_id="t", actor="system", stage="security",
                         action="blocked",
                         summary=f"pipeline halted by block rule; block reason {ref}")
        assert event.artifact_ref == "", "this case is about rows with no artifact_ref"
        classified = timeline._delivery(event)
        assert classified is not None, (
            f"a legacy row carrying {ref} only in its summary was not classified at "
            "all -- the ref is read from artifact_ref alone, so every run already "
            "on disk renders with no delivery state"
        )
        assert classified[0] == expected, ref


def test_a_blocked_row_with_no_ref_at_all_claims_nothing():
    """No ref recorded means no claim, not a guess in either direction."""
    event = LogEvent(run_id="r", ticket_id="t", actor="system", stage="security",
                     action="blocked", summary="pipeline halted by block rule")
    assert timeline._delivery(event) is None
    assert timeline._annotations(event) == []


def test_an_unrecognised_ref_scheme_is_reported_as_unrecognised():
    """A fourth ref shape must not render as silence.

    Rendering nothing would be indistinguishable from a blocked row that carried
    no ref at all -- the same silent conflation this module exists to end. Found
    by asking what this change makes worse rather than by a failing demo.
    """
    event = LogEvent(run_id="r", ticket_id="t", actor="system", stage="security",
                     action="blocked", artifact_ref="slack://channel/C123",
                     summary="pipeline halted by block rule; block reason slack://channel/C123")
    classified = timeline._delivery(event)
    assert classified is not None, (
        "an unrecognised delivery ref rendered as silence, which is "
        "indistinguishable from a blocked row that carried no ref at all"
    )
    state, detail = classified
    assert state == "UNRECOGNISED", state
    assert "not understood" in detail
    # And it must not be mistaken for either a success or a delivery failure.
    assert state != "reported"
    assert state != "NOT REPORTED"


# =========================================================================
# REQUIREMENT 3 -- scan provenance.
#
# `agents/security.run` answers a scanner raise with the FIXTURE verdict, which
# still blocks a poisoned diff. So "blocked" proves two different things, and
# the log row was identical either way. On this machine gitleaks, semgrep and
# trivy are all absent from PATH, so the fixture fallback is the DEFAULT.
# =========================================================================

def test_a_fixture_verdict_is_not_rendered_as_a_real_scan():
    """The gap this closes: a fixture block must not look like a scanned block.

    No scanners are installed here, so the pipeline takes the fallback and the
    row must say so in words rather than only carrying a blocking COUNT -- the
    count is produced identically by both paths.
    """
    out = timeline.render_text(_blocked_run().run_id)
    assert "scan: FIXTURE verdict" in out, out
    assert "scanners did not run" in out
    assert "real scanners ran" not in out


def test_a_real_scan_is_rendered_as_a_real_scan(monkeypatch):
    """The case that must still WORK -- the mirror of the test above.

    Without this, a renderer hardcoded to say "FIXTURE" would pass every other
    provenance assertion in this file. Drives the real scanner path by replacing
    the fan-out with one that RETURNS rather than raises, which is the only path
    on which compute_security_verdict actually runs.
    """
    from agentorg.agents import security
    monkeypatch.setattr(security, "run_all_scanners", lambda dev: [])
    state = run_pipeline("CLEAN-1", TICKET_TEXT)
    out = timeline.render_text(state.run_id)
    assert "scan: real scanners ran" in out, out
    assert "FIXTURE" not in out


def test_the_stub_path_is_told_apart_from_the_fallback_path():
    """Two fixture paths, two different meanings, and they must not collapse.

    `use_real_scanners=False` is a CHOICE nobody asked to scan for; a scanner
    raise is a FAULT. Collapsing them would hide a broken gate behind a
    deliberate demo setting.
    """
    from agentorg import fixtures_loader
    from agentorg.agents import security
    from agentorg.state import RunState

    state = RunState(ticket_id="STUB-1", ticket_text=TICKET_TEXT)
    state.dev = fixtures_loader.dev(poisoned=True)
    assert security.run(state, use_real_scanners=False).scan_provenance == "fixture-stub"
    assert security.run(state).scan_provenance == "fixture-fallback"
    assert (timeline._PROVENANCE["fixture-stub"]
            != timeline._PROVENANCE["fixture-fallback"])


def _legacy_run(tmp_run_id: str) -> str:
    """One run logged in the PRE-PROVENANCE shape: scan_provenance is "".

    This is what every pre-week-3 row in runs/ looks like, and the only case
    that cannot be produced by running the pipeline -- the field exists now, so
    a live run always fills it. Written through `log.append`, the same writer
    the graph uses, so the bytes on disk are the real shape.
    """
    for actor, action, summary in [
        ("system", "opened", "run started for LEGACY-1"),
        ("security", "blocked", "2 blocking"),
    ]:
        log.append(LogEvent(
            run_id=tmp_run_id, ticket_id="LEGACY-1", actor=actor,
            stage="plan" if action == "opened" else "security",
            action=action, verdict="block" if action == "blocked" else "",
            summary=summary,
        ))
    return tmp_run_id


def test_a_run_logged_before_provenance_existed_is_rendered_as_unknown():
    """Do not guess, and do not imply a scan. Say the record does not say.

    The runs already on disk carry no provenance and none can be recovered:
    the fixture's explanation names a real file and a real remediation and is
    indistinguishable from real gitleaks output, so pattern-matching it would be
    a guess dressed as evidence. Rendering blank would read identically to a real
    scan, which is the conflation this field exists to end.
    """
    run_id = _legacy_run("legacy-timeline-test-run")
    out = timeline.render_text(run_id)
    assert "provenance unknown" in out, out
    assert "real scanners ran" not in out
    assert "FIXTURE" not in out


def test_provenance_reaches_the_log_on_disk_not_only_the_runstate():
    """The timeline may read nothing but log.read, so the log must carry it.

    Read back off the raw JSONL rather than through log.read: the claim is about
    the bytes in runs/<run_id>.jsonl, and a field that existed only on the
    in-memory model would satisfy every renderer assertion above while leaving
    the artifact the judges are handed unchanged.
    """
    state = _blocked_run()
    rows = [json.loads(line) for line
            in log._path(state.run_id).read_text().splitlines()]
    security_rows = [r for r in rows if (r["actor"], r["stage"]) == ("security", "security")]
    assert security_rows, "the security verdict row must exist"
    assert security_rows[0]["scan_provenance"] == "fixture-fallback", security_rows[0]

    halt = [r for r in rows if (r["actor"], r["stage"], r["action"])
            == ("system", "security", "blocked")]
    assert len(halt) == 1
    assert halt[0]["artifact_ref"].startswith("local://"), halt[0]
