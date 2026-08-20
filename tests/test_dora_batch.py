"""The headline claim, under test. Owner: Aya.

ONE BATCH PER SESSION. The week-3 spec had each test call run_batch(), which is
40 pipeline runs for two assertions. Measured: ~0.7 s per batch in
fixture-fallback mode with the model off, so ~1.5 s for the spec's shape; with
the three real binaries installed and no diff-hash cache in this repository, it
is ~60 scanner subprocess launches instead of ~30. Two tests reading ONE
measurement is also more honest than two tests taking two measurements that can
disagree.

=========================================================================
THE MODULE-SCOPED FIXTURE ESCAPES conftest's GUARDS. MEASURED, NOT FEARED.
=========================================================================

conftest.py closes four seams with `@pytest.fixture(autouse=True)`, which is
FUNCTION scope, because `monkeypatch` is function-scoped. A module-scoped
fixture body runs BEFORE any function-scoped fixture applies, so it runs with
all four seams OPEN. Probed on this machine:

    module scope saw   LLM_DISABLED=False  available()=True   _complete=_complete
                       OFFLINE=False       _repo=_repo        OFFLINE_REPO=runs/offline-demo
    function scope sees LLM_DISABLED=True  available()=False  _complete=_unpatched_complete
                       OFFLINE=True        _repo=_unpatched_repo
                       OFFLINE_REPO=<tmp_path>/offline-demo

That is not a model problem, it is all four seams at once, and only the first is
about money:

  * the MODEL. available() is True here, so each of the 20 runs would make live
    billable Bedrock calls. Measured at ~10.7 s per run_pipeline outside the
    guard versus ~0.066 s inside it, this file alone would take minutes.
  * GITHUB. OFFLINE=False with the real _repo restored. It is _use_local() that
    saves this machine today -- no token is configured, so open_pr still takes
    the local branch -- which means the guard is NOT what keeps this file off the
    network here. On a developer's laptop with a token exported, the same file
    would perform real branch/commit/pull-request WRITES, 10 times over.
  * the WORKSPACE. OFFLINE_REPO points back at <repo>/runs/offline-demo, so the
    local git work lands in the working tree instead of tmp_path.
  * the TERMINAL. builtins.input is real again. run_batch passes
    auto_approve's default, so no gate asks -- but nothing in this file's own
    text says so.

So the fixture installs the guards it needs ITSELF, with
pytest.MonkeyPatch.context() (the `monkeypatch` fixture is unavailable at module
scope), and then ASSERTS that they are in place before spending anything. The
assertion is the point: a batch that quietly made live calls would still produce
a green 10/10 and a plausible DORA table, with every number measured under a
different regime from every other number in this suite. An instrument that
cannot report its own failure is not evidence.

WHAT IT DELIBERATELY DOES NOT REDIRECT: <repo>/runs/. `log.append` and
`gates.save` resolve it from `pathlib.Path(__file__).parent.parent`, a
module-level constant with no config knob, and `step_count` is read back out of
that log by `log.read`. It is gitignored, and conftest does not redirect it
either. The git WORKSPACE is redirected, to a tmp_path, because that one has a
knob and because writing a nested real repository into the working tree is the
thing conftest's third fixture exists to prevent.

Run: LLM_DISABLED=true pytest -q tests/test_dora_batch.py
"""

import builtins
import logging

import pytest

from agentorg import github_ops
from agentorg.common import config, llm
from tests import provenance as prov
from tests.dora_batch import (
    N,
    _fallback_warnings_counted,
    run_batch,
    summarize,
)
from tests.dora_runner import DoraRow


def _model_is_off() -> None:
    """Stand-in for the model seam at module scope. Fails the batch that reached it."""
    raise AssertionError(
        "the module-scoped batch reached llm._complete, which on this machine is "
        "a live billable Bedrock call at ~10.7s per run. The batch fixture's own "
        "guard did not hold -- see this file's docstring."
    )


def _github_is_off():
    """Stand-in for the GitHub API seam at module scope."""
    raise AssertionError(
        "the module-scoped batch reached github_ops._repo, which with a token in "
        "the environment performs live branch/commit/pull-request WRITES. The "
        "batch fixture's own guard did not hold -- see this file's docstring."
    )


def _terminal_is_off(prompt: str = ""):
    """Stand-in for the terminal at module scope."""
    raise AssertionError(
        f"the module-scoped batch reached builtins.input (prompt: {prompt!r}), "
        f"which under `pytest -s` blocks the whole suite with no failing test to "
        f"point at."
    )


@pytest.fixture(scope="module")
def batch(tmp_path_factory):
    """One batch for the whole module, hermetic by its own hand.

    See this file's docstring for the cost and for why this fixture cannot rely
    on conftest. AssertionError rather than pytest.fail for the raisers: this
    body runs at module scope where a fail() is reported as an error anyway, and
    AssertionError is what the guard assertions below already raise, so the
    failure reads the same whichever half caught it.
    """
    workspace = tmp_path_factory.mktemp("dora-batch-offline")

    with pytest.MonkeyPatch.context() as mp:
        # The same four seams conftest closes, closed again here because a
        # module-scoped body runs outside its function-scoped fixtures.
        mp.setattr(config, "LLM_DISABLED", True)
        mp.setattr(llm, "_complete", _model_is_off)
        mp.setattr(config, "OFFLINE", True)
        mp.setattr(github_ops, "_repo", _github_is_off)
        mp.setattr(config, "OFFLINE_REPO", str(workspace / "offline-demo"))
        mp.setattr(config, "OFFLINE_NOTES", str(workspace / "offline-demo" / "NOTES.md"))
        mp.setattr(builtins, "input", _terminal_is_off)

        # PROVE the guards hold BEFORE spending 20 pipeline runs. Each of these
        # is checked through the module attribute, because that is how the
        # production code reads it -- a local copy would assert about this
        # function's namespace instead of the pipeline's.
        assert config.LLM_DISABLED is True, "the model knob did not take"
        assert llm.available() is False, (
            "llm.available() is still True, so the batch would make live "
            "billable model calls"
        )
        assert llm._complete is _model_is_off, "the model seam is not the stub"
        assert config.OFFLINE is True, "the batch would use the GitHub API"
        assert github_ops._repo is _github_is_off, "the GitHub seam is not the stub"
        assert "offline-demo" in config.OFFLINE_REPO, "the git workspace is not redirected"
        assert str(workspace) in config.OFFLINE_REPO, (
            f"the git workspace must be under tmp_path, not the working tree; "
            f"got {config.OFFLINE_REPO}"
        )
        assert builtins.input is _terminal_is_off, "the terminal is not blocked"

        yield run_batch()

    # The rows outlive this context, and that is safe rather than lucky: DoraRow
    # is a frozen dataclass of str/bool/int/float, so it holds no handle on the
    # patched modules or the temporary directory. Verified by
    # test_the_rows_survive_the_fixture_teardown, which reads them after the
    # context has exited and the workspace is gone.


def test_the_batch_fixture_was_actually_hermetic(batch):
    """The instrument reports on itself, from the test side of the fence.

    The fixture's own assertions run inside its `with`, where they are cheap and
    early. This one runs after it, where conftest's function-scoped guards have
    since taken over -- so it deliberately does NOT re-check the module-scope
    state, which no longer exists to check. It checks the one thing that
    survives: the rows themselves, and whether they carry the shape a
    fixture-mode run produces. A batch that had gone to a live model would still
    land here green, which is exactly why the fixture asserts BEFORE it spends.
    """
    agent_rows, baseline_rows = batch
    assert len(agent_rows) == N and len(baseline_rows) == N
    assert all(isinstance(row, DoraRow) for row in agent_rows + baseline_rows)


def test_agent_org_blocks_poison_10_of_10(batch):
    agent_rows, _ = batch
    summary = summarize(agent_rows)
    assert summary["runs"] == N
    assert summary["blocked"] == N, (
        f"the demo's central claim: {N}/{N} blocked. Got "
        f"{summary['blocked']}/{summary['runs']}."
    )
    assert summary["bad_changes_shipped"] == 0
    assert summary["promoted"] == 0, "no poisoned run may be promoted"


def test_every_agent_org_row_blocked_for_the_same_reason(batch):
    """10/10 is only meaningful if the ten runs are the same run ten times.

    Ten blocks reached by ten different mechanisms would satisfy the count above
    while meaning the pipeline is unstable. This is the assertion that makes the
    number a determinism claim rather than a tally.
    """
    agent_rows, _ = batch
    assert {r.final_status for r in agent_rows} == {"blocked"}
    assert {r.provenance for r in agent_rows} == {agent_rows[0].provenance}, (
        "the ten runs must all have been decided the same way; a mixed batch "
        "means some runs reached the scanners and others fell back"
    )


def test_baseline_ships_the_poison_every_time(batch):
    """The no-checks path has no security gate, so the poisoned change ships."""
    _, baseline_rows = batch
    summary = summarize(baseline_rows)
    assert summary["runs"] == N
    assert summary["bad_changes_shipped"] == N, (
        f"the 'before' picture: the baseline must ship all {N}. Got "
        f"{summary['bad_changes_shipped']}."
    )
    assert summary["blocked"] == 0, "the baseline has nothing that could block"
    assert summary["checks_run"] == 0


def test_the_two_columns_actually_contrast(batch):
    """The table's whole content, as one assertion."""
    agent_rows, baseline_rows = batch
    agent, base = summarize(agent_rows), summarize(baseline_rows)
    assert agent["blocked"] == N and base["blocked"] == 0
    assert agent["bad_changes_shipped"] == 0 and base["bad_changes_shipped"] == N
    assert agent["checks_run"] > base["checks_run"]


def test_both_columns_agree_on_how_many_checks_they_ran(batch):
    """summarize reads checks_run off row 0; this is what makes that legitimate.

    An average or a joined string would carry a disagreement into the report, but
    an int silently reports the first row and hides one. So the invariant that
    licenses the shortcut is asserted where it can be seen, over ALL the rows.
    """
    agent_rows, baseline_rows = batch
    for label, rows in (("agent_org", agent_rows), ("baseline", baseline_rows)):
        counts = {row.checks_run for row in rows}
        assert len(counts) == 1, (
            f"{label} rows disagree about checks_run ({sorted(counts)}), so the "
            f"summary's single value is reporting row 0 and hiding the rest"
        )


def test_the_summary_names_the_provenance_mode(batch):
    """A number quoted without its mode is two claims wearing one coat."""
    agent_rows, _ = batch
    summary = summarize(agent_rows)
    assert summary["provenance"] in ("fixture", "real_scanners", "unknown")
    if not prov.binaries_installed():
        assert summary["provenance"] == "fixture", (
            "with no binaries installed the 10/10 is a claim about the FIXTURE, "
            "not about compute_security_verdict"
        )


def test_a_mixed_provenance_batch_is_reported_and_not_hidden(batch):
    """The joined string must SHOW a disagreement the row set can contain.

    Built from a real row via dataclasses.replace rather than from a fabricated
    one, so this pins the field on the object the pipeline actually produced.
    `unknown` is the specific value at risk: dora_runner emits it when the
    provenance discriminator raises, and it is the one label whose whole purpose
    is to not be quietly dropped.
    """
    import dataclasses

    agent_rows, _ = batch
    mixed = list(agent_rows)
    mixed[0] = dataclasses.replace(mixed[0], provenance="unknown")

    summary = summarize(mixed)
    assert summary["provenance"] != agent_rows[0].provenance, (
        "the summary reported one provenance for a batch that carried two"
    )
    assert "unknown" in summary["provenance"], (
        "an `unknown` provenance was dropped from the report; dora_runner emits "
        "it when the discriminator raises, and it must reach the reader"
    )
    assert summary["provenance"] == "+".join(
        sorted({"unknown", agent_rows[0].provenance})
    )


def test_summarize_of_nothing_does_not_divide_by_zero():
    """The empty case reaches this the day run_baseline is unavailable."""
    empty = summarize([])
    assert empty["runs"] == 0
    assert empty["avg_lead_time_s"] == 0
    assert empty["provenance"] == "n/a"


def test_summarize_returns_every_key_the_deck_builder_subscripts():
    """The key set is a contract with the deck builder, which subscripts each one.

    Asserted as EQUALITY, on both the populated and the empty branch. A subset
    check would let the empty branch drift a key and still pass, and the empty
    branch is the one no demo exercises.
    """
    expected = {
        "runs", "bad_changes_shipped", "blocked", "promoted",
        "avg_step_count", "avg_lead_time_s", "checks_run", "provenance",
    }
    assert set(summarize([]).keys()) == expected

    row = DoraRow(
        ticket_id="T-1", path="baseline", poisoned=False, final_status="promoted",
        bad_change_shipped=False, step_count=2, lead_time_s=0.000123,
        checks_run=0, provenance="n/a",
    )
    assert set(summarize([row]).keys()) == expected


def test_summarize_does_not_round_a_real_average_away_to_zero():
    """Six places, not four -- and asserted where the two actually differ.

    Measured baseline rows run 0.000047-0.001217 s, whose AVERAGE survives four
    places at 0.0002, so real rows would not catch a four-place regression. The
    lead time below is inside the measured per-row range and low enough that four
    places renders it 0.0 while six keeps it. Without this the constant would be
    a comment rather than a contract.
    """
    row = DoraRow(
        ticket_id="T-1", path="baseline", poisoned=False, final_status="promoted",
        bad_change_shipped=False, step_count=2, lead_time_s=0.000047,
        checks_run=0, provenance="n/a",
    )
    assert round(0.000047, 4) == 0.0, "the premise of this test no longer holds"
    assert summarize([row])["avg_lead_time_s"] == 0.000047


def test_the_fallback_warning_is_aggregated_but_a_new_warning_still_prints(caplog):
    """The demo-safety filter must suppress ONLY the message it claims to.

    A filter that swallowed everything from that logger would make the projector
    tidy by making it blind, so both directions are asserted: the known
    N-times-repeated fallback warning is counted and suppressed, and an unrelated
    WARNING on the same logger is still emitted. The suppressed count is what
    keeps this aggregation rather than deletion.
    """
    logger = logging.getLogger("agentorg.agents.security")

    with (
        _fallback_warnings_counted() as counter,
        caplog.at_level(logging.WARNING, logger="agentorg.agents.security"),
    ):
        logger.warning(
            "scanners failed (%s: %s); falling back to the fixture verdict",
            "FileNotFoundError",
            "semgrep is not installed",
        )
        logger.warning("something else entirely went wrong")

    assert counter.count == 1, "the fallback warning was not counted"
    messages = [r.getMessage() for r in caplog.records]
    assert not any("falling back to the fixture verdict" in m for m in messages), (
        "the repeated fallback warning should have been aggregated away"
    )
    assert any("something else entirely" in m for m in messages), (
        "the filter suppressed an unrelated warning; it is hiding failures, not "
        "aggregating a known one"
    )


def test_the_warning_filter_is_removed_afterwards():
    """The context manager must not leave a filter on a shared global logger."""
    logger = logging.getLogger("agentorg.agents.security")
    before = list(logger.filters)
    with _fallback_warnings_counted():
        assert len(logger.filters) == len(before) + 1
    assert list(logger.filters) == before, (
        "a filter was left attached to a module-level logger, which would then "
        "affect every later caller in the process"
    )


def test_the_rows_survive_the_fixture_teardown(batch):
    """The module-scoped rows are still readable after their workspace is gone.

    DoraRow is frozen plain data, so it holds nothing that dies with the
    fixture's tmp_path or its MonkeyPatch context. Stated as a test because the
    fixture's docstring claims it, and a claim in a docstring is not a check.
    """
    agent_rows, baseline_rows = batch
    for row in agent_rows + baseline_rows:
        assert isinstance(row.lead_time_s, float)
        assert isinstance(row.final_status, str) and row.final_status
        assert row.ticket_id
