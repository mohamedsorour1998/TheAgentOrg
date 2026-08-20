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

EVERY PIPELINE-DRIVEN TEST HERE DECLARES ITS SCANNER MODE. IT MUST.

    `run_pipeline`'s security stage fans out to real scanners when the three
    binaries are on PATH and falls back to the fixture verdict when they are
    not, and the two paths produce DIFFERENT provenance for the same headline
    verdict. A test that just calls the pipeline therefore reads whatever the
    laptop happens to have installed. Four tests in this file were written on a
    machine with no scanners, asserted the fixture wording, and began failing
    the day gitleaks, semgrep and trivy were brewed -- measured on this tree:
    with /opt/homebrew/bin on PATH `4 failed, 25 passed`, with it removed
    `29 passed`. The tests did not change; the machine did.

    So the mode is now an ARGUMENT, not an ambience: `_blocked_run` and
    `_promoted_run` require the `provenance` fixture (tests/conftest.py) and
    pin it through `Provenance.none_installed()`, and the three tests that
    reach the pipeline or the security agent directly pin it themselves. An
    ambient call is a TypeError rather than a test that passes on one laptop
    and fails on another.

    The tests that build LogEvents by hand and call `_delivery`/`_annotations`
    take no provenance argument, and that is not an oversight: they never run a
    scanner, so there is no mode for them to declare.
"""

import json

import pytest

from agentorg import log, timeline
from agentorg.graph import run_pipeline
from agentorg.state import LogEvent

TICKET_TEXT = "Add a per-IP login rate limit."


def _blocked_run(provenance):
    """A blocked run in a DECLARED scanner mode, never the machine's.

    `provenance` is required rather than defaulted on purpose. The whole failure
    this closes is a helper that could be called with no mode at all, so the
    only way to keep it closed is to make the omission unspellable: every caller
    names the fixture, and a future test that forgets gets a TypeError at
    collection instead of a verdict from whatever is on PATH.

    `none_installed()` REMOVES the directories holding the three scanners from
    PATH and asserts none is reachable while `git` still is. It is the fixture's
    job and not this file's: replacing PATH wholesale would kill the real `git`
    that `github_ops.open_pr` shells out to, and run_pipeline would die with
    `FileNotFoundError: 'git'` before the security stage ran at all. See
    tests/provenance.py, which is owned by another lane and consumed here.

    Idempotent, so a test may build both runs: the second call finds nothing
    left to scrub.
    """
    provenance.none_installed()
    state = run_pipeline("POISON-1", TICKET_TEXT, poisoned=True)
    assert state.status == "blocked", "the fixture for these tests must be a blocked run"
    return state


def _promoted_run(provenance):
    """A promoted run in the same DECLARED mode. See `_blocked_run`."""
    provenance.none_installed()
    state = run_pipeline("CLEAN-1", TICKET_TEXT, poisoned=False)
    assert state.status == "promoted", "the fixture for these tests must be a promoted run"
    return state


# =========================================================================
# The spec's own contract: the CLI, the glyphs, the empty case. These are what
# the demo script and docs/plan/sorour/week3.md's done-when are written against.
# =========================================================================

def test_render_text_names_the_run_and_ticket(provenance):
    state = _promoted_run(provenance)
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


# =========================================================================
# HTML ESCAPING.
#
# Three layers, because no one of them is sufficient and the gap between them is
# where the original defect lived:
#
#   * the DATA tests below drive markup through every LogEvent field that can
#     hold it, on BOTH the empty-events and non-empty branches. They cannot
#     reach `stage`, `actor` or `action` -- those are Literals, so pydantic
#     rejects markup before the renderer ever sees it (verified: constructing a
#     LogEvent with stage="<img ...>" raises ValidationError). An escape removed
#     from one of those three sites is therefore INVISIBLE to any data-driven
#     test, however thorough.
#   * so the STRUCTURAL test walks render_html's AST and requires every
#     interpolation to be escaped or provably renderer-owned. That is what makes
#     "every interpolated field is escaped" enforceable at all 12 sites instead
#     of only the reachable ones.
#
# The whole set exists because stripping all 12 `html.escape(` calls from
# timeline.py left the suite green at 247 passed -- a defended design decision
# with nothing behind it, which is this repository's signature defect.
# =========================================================================

MARKUP = "<img src=x onerror=alert(1)>"
ESCAPED_MARKUP = "&lt;img src=x onerror=alert(1)&gt;"


def _assert_markup_is_inert(out: str, where: str):
    """No live tag anywhere, and the payload present only in escaped form."""
    assert "<img" not in out, f"{where}: raw <img survived into the HTML"
    assert "onerror=alert(1)>" not in out, f"{where}: a live onerror attribute survived"
    assert ESCAPED_MARKUP in out, f"{where}: the payload is not present in escaped form"


def test_markup_in_every_injectable_log_field_is_escaped_in_the_html():
    """Every free-str field a LogEvent can carry, one at a time.

    One field per run_id so a single unescaped site cannot be masked by another
    field's escaping happening to contain the same bytes.

    `ts` gets a DIFFERENT payload, and that is not a shortcut. Both renderers
    slice it as `e.ts[11:19]` to pull HH:MM:SS out of an ISO timestamp, so the
    full `<img ...>` string never reaches the escape call -- it arrives as the
    8-character fragment "onerror=", which proves nothing about escaping. The
    padded payload puts a real `<b>` tag inside that window instead, so the
    assertion is about what the renderer actually interpolates.
    """
    sliced_field_markup = "0123456789T<b>x</b>"          # [11:19] == "<b>x</b>"
    assert sliced_field_markup[11:19] == "<b>x</b>", "the ts payload must survive slicing"

    for field in ("verdict", "summary", "ticket_id"):
        run_id = f"escape-check-{field}"
        kwargs = {"run_id": run_id, "ticket_id": "ESC-1", "actor": "system",
                  "stage": "security", "action": "blocked", field: MARKUP}
        log.append(LogEvent(**kwargs))
        _assert_markup_is_inert(timeline.render_html(run_id), f"field {field}")

    # `artifact_ref` is deliberately NOT in that loop. Neither renderer
    # interpolates it: `_delivery` reads only its SCHEME and emits a fixed phrase
    # from `_DELIVERY`, so the field's bytes never reach the page. Asserting the
    # escaped payload appears would be asserting a requirement that does not
    # exist -- and it fails, which is how this was found. What must hold is that
    # nothing live escapes, and that an unrecognised scheme still says so.
    log.append(LogEvent(run_id="escape-check-artifact_ref", ticket_id="ESC-1",
                        actor="system", stage="security", action="blocked",
                        artifact_ref=MARKUP))
    out = timeline.render_html("escape-check-artifact_ref")
    assert "<img" not in out, "artifact_ref: raw <img reached the HTML"
    assert "onerror=alert(1)>" not in out, "artifact_ref: a live onerror attribute reached the HTML"
    assert "UNRECOGNISED" in out, "an unparseable ref must still be reported, not dropped"

    log.append(LogEvent(run_id="escape-check-ts", ticket_id="ESC-1", actor="system",
                        stage="security", action="blocked", ts=sliced_field_markup))
    out = timeline.render_html("escape-check-ts")
    assert "<b>x</b>" not in out, "field ts: a raw <b> tag survived into the HTML"
    assert "&lt;b&gt;x&lt;/b&gt;" in out, "field ts: not present in escaped form"


def test_markup_in_the_run_id_is_escaped_on_the_empty_events_branch():
    """The branch the original bug lived on, reachable from the documented CLI.

    `python -m agentorg.timeline '<img ...>' --html out.html` is an unknown run
    id, so `log.read` returns [] and render_html takes its empty-events path.
    That path once interpolated the id into the <h1> RAW while escaping the same
    value in the <title> two lines above -- the same bytes escaped in one
    position and live in another.
    """
    out = timeline.render_html(MARKUP)
    _assert_markup_is_inert(out, "empty-events run_id")
    # Both positions, named separately: the <title> was already correct, and a
    # fix that only repaired the <h1> would leave the pair asymmetric again.
    assert f"<title>Timeline {ESCAPED_MARKUP}" in out
    assert f"ticket {ESCAPED_MARKUP}" in out


def test_markup_in_an_annotation_is_escaped():
    """Annotations are renderer-built, but they INTERPOLATE the row's own data.

    `_delivery` reads the ref out of `artifact_ref`/`summary`, so an annotation
    string can carry attacker bytes even though this module composed the sentence
    around them.
    """
    run_id = "escape-check-annotation"
    log.append(LogEvent(
        run_id=run_id, ticket_id="ESC-1", actor="system", stage="security",
        action="blocked", artifact_ref=f"comment://{MARKUP}",
        summary=f"pipeline halted by block rule; block reason comment://{MARKUP}"))
    _assert_markup_is_inert(timeline.render_html(run_id), "annotation")


def _render_html_interpolations():
    """Every f-string interpolation inside render_html, as source text."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(timeline))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "render_html")
    return [ast.unparse(n.value) for n in ast.walk(fn)
            if isinstance(n, ast.FormattedValue)]


def test_no_unescaped_interpolation_reaches_the_html():
    """STRUCTURAL: every f-string field in render_html is escaped or ours.

    This is the test that actually pins the requirement. The data tests above
    cannot reach `stage`, `actor` or `action` -- pydantic Literals make markup in
    those fields unconstructable -- so removing `html.escape` from one of them is
    caught by nothing else. Walking the AST covers all 12 sites uniformly.

    The allowlist is values this module BUILT, never log data: the CSS class from
    `_row_class`/`label`, the glyph from `_MARK`, and the `rows`/`banner`/`tid`
    fragments assembled above (each escaped at its own point of use). Anything
    else interpolated raw is a finding -- if you add an interpolation and this
    fails, escape it rather than extending the list.
    """
    renderer_owned = {
        "label.split()[0].lower()", "glyph", "tid", "banner", "rows",
        "_row_class(e)", "_MARK.get(e.action, '\u2022')",
    }
    unescaped = [expr for expr in _render_html_interpolations()
                 if "html.escape(" not in expr and expr not in renderer_owned]
    assert not unescaped, (
        "render_html interpolates these without html.escape and they are not "
        f"renderer-owned constants: {unescaped}"
    )


def test_the_structural_escape_check_covers_every_escape_site():
    """Guard on the guard: the AST walk must actually SEE all 12 escape sites.

    Without this, a structural test whose allowlist quietly grew, or whose walk
    stopped finding nodes, would pass by inspecting nothing -- the failure mode
    of every test that asserts on an empty collection.
    """
    escaped = [e for e in _render_html_interpolations() if "html.escape(" in e]
    assert len(escaped) >= 10, (
        f"the AST walk found only {len(escaped)} escaped interpolations in "
        "render_html; it is no longer inspecting what it claims to"
    )


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


def test_the_specs_done_when_strings_render_verbatim(provenance):
    """The two lines docs/plan/sorour/week3.md's done-when names, exactly.

    Pinned because the demo script and the judges' expectations are built on
    them: a clean run "starting with a `system plan opened` line and ending with
    a `★ promote system promoted` line", and a poisoned run whose last line is
    `⛔ security ... blocked`.
    """
    clean = timeline.render_text(_promoted_run(provenance).run_id).splitlines()
    assert "• plan     system    opened" in clean[2], clean[2]
    assert "★ promote  system    promoted" in clean[-1], clean[-1]

    poisoned = [line for line in timeline.render_text(_blocked_run(provenance).run_id).splitlines()
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

def test_a_blocked_run_and_a_promoted_run_are_distinguishable_at_a_glance(provenance):
    """One screen, one word, no prose. The two runs must not read alike.

    Asserts on the SECOND line of each -- above the rows -- because an outcome
    only findable by reading to the end of the list is the thing this replaces.
    Both halves are asserted, not just the block: a renderer that stamped
    "BLOCKED" on everything would pass a blocked-only test.
    """
    blocked = timeline.render_text(_blocked_run(provenance).run_id).splitlines()
    promoted = timeline.render_text(_promoted_run(provenance).run_id).splitlines()

    assert "BLOCKED" in blocked[1], blocked[1]
    assert "PROMOTED" in promoted[1], promoted[1]
    assert blocked[1] != promoted[1]
    # And the words must not both appear on either screen, or "which happened?"
    # is back to being a reading exercise.
    assert "PROMOTED" not in blocked[1]
    assert "BLOCKED" not in promoted[1]


def test_the_html_outcome_is_not_signalled_by_colour_alone(provenance):
    """A washed-out projector and a colour-blind judge must both still read it.

    The banner carries the word and the glyph as TEXT, so the CSS class is an
    enhancement rather than the signal.
    """
    html_out = timeline.render_html(_blocked_run(provenance).run_id)
    assert "BLOCKED" in html_out
    assert "⛔" in html_out
    assert "banner blocked" in html_out


def test_an_unfinished_run_is_not_reported_as_either_outcome(provenance):
    """A run with no ending must not be given one.

    `_outcome` reads the last event's action. A run abandoned at a gate ends on
    `opened`, and forcing that into PROMOTED or BLOCKED would be the renderer
    inventing an ending the pipeline never wrote -- the same class of error as
    guessing provenance.

    Pins the mode even though the assertion is about the BANNER and not about
    provenance: this test reaches the pipeline, so leaving it ambient would let
    a scanner fault on a provisioned machine change the run's status out from
    under it. Declaring costs one line; the standing rule is that no test in
    this file reads the machine's mode.
    """
    provenance.none_installed()
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


def test_the_offline_delivery_is_rendered_as_a_success_on_a_real_run(provenance):
    """local:// is a DELIVERY. This is the case the demo actually produces.

    github_ops returns `local://` only after the bytes reach disk, so calling it
    undelivered would mark a working offline demo as a failure on a projector.
    Run through the real pipeline, so the ref is the one github_ops produced.
    """
    state = _blocked_run(provenance)
    out = timeline.render_text(state.run_id)
    assert "delivery: reported" in out, out
    assert "offline notes file" in out
    assert "NOT REPORTED" not in out


def test_an_undelivered_block_reason_is_rendered_as_not_reported(monkeypatch, provenance):
    """A block nobody was told about is a different outcome. Say so.

    The failure is injected at `github_ops.post_comment`'s return value rather
    than by breaking the NOTES path, because the claim is about how the RENDERER
    reads a `comment://` ref -- not about how one gets produced, which
    tests/test_offline_mode.py already pins.
    """
    from agentorg import github_ops
    monkeypatch.setattr(github_ops, "post_comment",
                        lambda state, body, finding=None: f"comment://{state.run_id}")
    state = _blocked_run(provenance)
    out = timeline.render_text(state.run_id)
    assert "delivery: NOT REPORTED" in out, out
    assert "reached nobody" in out


def test_a_promoted_run_claims_no_delivery_at_all(provenance):
    """The PR row's summary contains `local://` too -- and it is NOT a delivery.

    `PR local://agent-org/CLEAN-1-6361c5b` is a branch. A renderer keying on the
    scheme anywhere in a row would announce "block reason written to the offline
    notes file" on every clean run that never had a block reason. This is the
    case that must still work, and it is the mirror image of the bug that
    rendering three states invites.
    """
    out = timeline.render_text(_promoted_run(provenance).run_id)
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
# the log row was identical either way.
#
# NO TEST BELOW RELIES ON WHAT IS INSTALLED. This block used to end "on this
# machine gitleaks, semgrep and trivy are all absent from PATH, so the fixture
# fallback is the DEFAULT" -- an ambient fact, true when written and false the
# day the three were brewed. Each test now pins its own mode through the
# `provenance` fixture, so the pair below tests BOTH directions on ANY machine:
# `none_installed()` for the fixture path, a returning fan-out for the real one.
# =========================================================================

def test_a_fixture_verdict_is_not_rendered_as_a_real_scan(provenance):
    """The gap this closes: a fixture block must not look like a scanned block.

    DECLARES the fallback mode rather than assuming it. The docstring used to
    open "No scanners are installed here, so the pipeline takes the fallback" --
    which was an observation about the laptop, not a property of the test, and it
    stopped being true. `none_installed()` makes the sentence true by
    construction, so the row must say so in words rather than only carrying a
    blocking COUNT -- the count is produced identically by both paths.
    """
    out = timeline.render_text(_blocked_run(provenance).run_id)
    assert "scan: FIXTURE verdict" in out, out
    assert "scanners did not run" in out
    assert "real scanners ran" not in out


def test_a_real_scan_is_rendered_as_a_real_scan(monkeypatch, provenance):
    """The case that must still WORK -- the mirror of the test above.

    Without this, a renderer hardcoded to say "FIXTURE" would pass every other
    provenance assertion in this file. Drives the real scanner path by replacing
    the fan-out with one that RETURNS rather than raises, which is the only path
    on which compute_security_verdict actually runs.

    Takes `provenance` and scrubs PATH FIRST, which looks backwards for the
    real-scanner case and is the point: replacing `run_all_scanners` is what
    makes this the real path, so the binaries are irrelevant to the outcome --
    and pinning proves that. Without it this test passes on a provisioned
    machine for two possible reasons and the test cannot say which; with it, the
    only thing on the real path is the stubbed fan-out. So this half is
    mode-independent BY CONSTRUCTION rather than by luck, exactly like its
    mirror above.
    """
    from agentorg.agents import security
    provenance.none_installed()
    monkeypatch.setattr(security, "run_all_scanners", lambda dev: [])
    state = run_pipeline("CLEAN-1", TICKET_TEXT)
    out = timeline.render_text(state.run_id)
    assert "scan: real scanners ran" in out, out
    assert "FIXTURE" not in out


def test_the_stub_path_is_told_apart_from_the_fallback_path(provenance):
    """Two fixture paths, two different meanings, and they must not collapse.

    `use_real_scanners=False` is a CHOICE nobody asked to scan for; a scanner
    raise is a FAULT. Collapsing them would hide a broken gate behind a
    deliberate demo setting.

    This one calls `security.run` directly rather than the pipeline, so it needs
    the mode for the SECOND assertion: with binaries on PATH the knob-on call
    reaches a real fan-out and returns "scanners", and the test read the laptop
    instead of the fault path it names. `none_installed()` makes the raise the
    reason the fallback happens, which is the distinction being drawn.
    """
    from agentorg import fixtures_loader
    from agentorg.agents import security
    from agentorg.state import RunState

    provenance.none_installed()
    state = RunState(ticket_id="STUB-1", ticket_text=TICKET_TEXT)
    state.dev = fixtures_loader.dev(poisoned=True)
    assert security.run(state, use_real_scanners=False).scan_provenance == "fixture-stub"
    assert security.run(state).scan_provenance == "fixture-fallback"
    assert (timeline._PROVENANCE["fixture-stub"]
            != timeline._PROVENANCE["fixture-fallback"])


def test_a_passed_row_names_the_cause_and_a_blocked_row_keeps_its_wording(provenance):
    """Same fact, two phrasings, keyed on the row's action.

    On a blocked row "FIXTURE verdict -- scanners did not run" is the caveat a
    judge needs. On the green run that same phrasing reads as a shortfall in the
    run rather than in the machine, so the passed row names the CAUSE instead.

    Both halves are asserted, and the blocked half matters most: the demo script
    and every provenance assertion in this file are written against those exact
    words, so the passed-row rewording must not have touched them.
    """
    clean = timeline.render_text(_promoted_run(provenance).run_id)
    blocked = timeline.render_text(_blocked_run(provenance).run_id)

    assert "scan: no scanners installed — verdict from the built-in fixture rules" in clean
    assert "scanners did not run" not in clean, (
        "the green run must not be described in terms of what did not happen"
    )

    assert "scan: FIXTURE verdict — scanners did not run" in blocked, (
        "the blocked row's wording is quoted by the demo script and must not drift"
    )

    # And the two must not collapse into one string, or the distinction is gone.
    assert timeline._PROVENANCE["fixture-fallback"] != \
        timeline._PROVENANCE_PASSED["fixture-fallback"]


def test_every_provenance_value_the_log_can_carry_has_words():
    """_PROVENANCE must cover ScanProvenance, mirroring _MARK's guard.

    Without this, a fourth ScanProvenance value falls through
    `_PROVENANCE.get(..., _PROVENANCE[""])` and renders "provenance unknown --
    logged before this was recorded", which would be FALSE: the row does record
    its provenance, this renderer just has no words for it. That is the exact
    conflation this field exists to end, inverted -- and it is the worst shape of
    all, because it is a confident wrong answer rather than a missing one.

    Read off the Literal via get_args rather than listed by hand, for the same
    reason test_every_action_the_log_can_write_has_a_glyph does.
    """
    from typing import get_args

    from agentorg.state import ScanProvenance

    values = set(get_args(ScanProvenance))
    # BOTH tables, because _annotations picks between them by the row's action --
    # a value covered by only one renders correctly on blocked rows and falsely
    # on passed ones, which is the harder half of the bug to notice.
    for name, table in (("_PROVENANCE", timeline._PROVENANCE),
                        ("_PROVENANCE_PASSED", timeline._PROVENANCE_PASSED)):
        assert values <= set(table), (
            f"{name}: provenance values with no words: {sorted(values - set(table))}"
        )
        # And the unknown-row key must remain, since it is the default the .get
        # falls back to and the commonest case in the existing corpus.
        assert "" in table, f"{name}: the pre-provenance row case must keep its words"


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


def test_provenance_reaches_the_log_on_disk_not_only_the_runstate(provenance):
    """The timeline may read nothing but log.read, so the log must carry it.

    Read back off the raw JSONL rather than through log.read: the claim is about
    the bytes in runs/<run_id>.jsonl, and a field that existed only on the
    in-memory model would satisfy every renderer assertion above while leaving
    the artifact the judges are handed unchanged.
    """
    state = _blocked_run(provenance)
    rows = [json.loads(line) for line
            in log._path(state.run_id).read_text().splitlines()]
    security_rows = [r for r in rows if (r["actor"], r["stage"]) == ("security", "security")]
    assert security_rows, "the security verdict row must exist"
    assert security_rows[0]["scan_provenance"] == "fixture-fallback", security_rows[0]

    halt = [r for r in rows if (r["actor"], r["stage"], r["action"])
            == ("system", "security", "blocked")]
    assert len(halt) == 1
    assert halt[0]["artifact_ref"].startswith("local://"), halt[0]
# =========================================================================
# WHAT PINNING THE MODE MAKES WORSE, AND THE TWO GUARDS FOR IT.
#
# Declaring the mode fixes four tests and costs two things.
#
#   1. THE RULE IS NOW CONVENTION. Nothing stopped the old tests from reading
#      the machine, and nothing stops a new one either: a test added next week
#      that calls `run_pipeline` directly, without the fixture, is back to
#      passing on one laptop and failing on another -- and it will LOOK right,
#      because it matches what the four broken tests looked like for weeks. The
#      structural guard below is the only thing that makes the rule enforceable
#      rather than remembered.
#
#   2. THE PIN COULD BECOME A NO-OP. Both helpers rely on
#      `Provenance.none_installed()` actually scrubbing PATH. If that call were
#      dropped in a refactor, or the fixture's removal stopped working on some
#      future machine, every test here would go back to reading the ambient mode
#      while still LOOKING declared -- the fixture requested, the mode named, and
#      no effect. That is strictly worse than the bug being fixed, because the
#      declaration reads as evidence that the mode was controlled.
# =========================================================================

_PIPELINE_ENTRY_POINTS = ("run_pipeline", "_blocked_run", "_promoted_run")


def test_every_pipeline_driven_test_declares_its_scanner_mode():
    """STRUCTURAL: reaching a scanner without naming a mode is a finding.

    Walks this module's own AST and requires every `test_*` that reaches the
    pipeline or the security agent to take `provenance`. That is what turns "no
    test reads the ambient mode" from a habit into a checked property -- the same
    reason `test_no_unescaped_interpolation_reaches_the_html` walks the AST
    instead of trusting a reviewer to spot a missing `html.escape`.

    The four tests this file was fixed for were not wrong in a way review
    catches. They read correctly, asserted the right words, and passed for weeks;
    what was missing was invisible in the source. This test makes it visible.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(inspect.getmodule(test_render_text_names_the_run_and_ticket)))
    offenders = []
    for fn in (n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")):
        calls = {ast.unparse(n.func) for n in ast.walk(fn) if isinstance(n, ast.Call)}
        reaches = calls & set(_PIPELINE_ENTRY_POINTS) or {
            c for c in calls if c.endswith("security.run")
        }
        if reaches and "provenance" not in {a.arg for a in fn.args.args}:
            offenders.append(f"{fn.name} (calls {sorted(reaches)})")
    assert not offenders, (
        "these tests reach a scanner but do not request the `provenance` "
        "fixture, so their result depends on what is installed on the machine "
        f"running them: {offenders}"
    )


def test_the_structural_mode_check_is_actually_inspecting_tests():
    """Guard on the guard: the walk must SEE the pipeline-driven tests.

    Without this, a walk that stopped matching -- a renamed helper, an AST shape
    it no longer unparses the same way -- would pass by inspecting nothing, the
    failure mode of every assertion on an empty collection. Mirrors
    `test_the_structural_escape_check_covers_every_escape_site`.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(inspect.getmodule(test_render_text_names_the_run_and_ticket)))
    declared = [
        fn.name for fn in ast.walk(tree)
        if isinstance(fn, ast.FunctionDef) and fn.name.startswith("test_")
        and {ast.unparse(n.func) for n in ast.walk(fn) if isinstance(n, ast.Call)}
        & set(_PIPELINE_ENTRY_POINTS)
    ]
    assert len(declared) >= 10, (
        f"the AST walk found only {len(declared)} pipeline-driven tests in this "
        "module; it is no longer inspecting what it claims to"
    )


def test_the_helpers_pin_the_mode_rather_than_only_claiming_to(provenance):
    """The pin must have an EFFECT, not just a name.

    Asserts the provenance of the runs the two helpers return. On a machine with
    the three binaries installed this passes only if `none_installed()` really
    scrubbed PATH; on a bare machine it passes trivially. Same assertion, sound
    either way -- which is the property the whole change is about.

    This is the guard for the worse-thing: a dropped `none_installed()` call
    leaves every test in this file looking declared while reading the ambient
    mode again, and a declaration that reads as evidence without being evidence
    is the exact failure this file exists to prevent, one level up.
    """
    from tests.provenance import answered_from_fixture

    blocked = _blocked_run(provenance)
    assert blocked.security is not None, "the blocked run must carry a verdict"
    assert blocked.security.scan_provenance == "fixture-fallback", (
        "_blocked_run did not end up in the fallback mode it declares; "
        "none_installed() had no effect"
    )
    # And cross-checked against the line-number discriminator, which is derived
    # from the FINDINGS rather than from the field the renderer reads -- so a
    # wrongly-stamped provenance cannot satisfy both.
    assert answered_from_fixture(blocked), (
        "the blocked run's findings do not carry the fixture's line numbers, so "
        "the real scanners answered despite the declared fallback mode"
    )

    promoted = _promoted_run(provenance)
    assert promoted.security is not None, "the promoted run must carry a verdict"
    assert promoted.security.scan_provenance == "fixture-fallback", (
        "_promoted_run did not end up in the fallback mode it declares"
    )
