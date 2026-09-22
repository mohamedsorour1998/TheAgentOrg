import pytest

from agentorg.agents import developer, planner, reviewer, security, sre
from agentorg.state import RunState

TICKET_TEXT = "Add a per-IP login rate limit."


def _shape(model):
    """Field-name -> type-name fingerprint of a pydantic model."""
    return {k: type(v).__name__ for k, v in model.model_dump().items()}


def _fresh_state():
    return RunState(ticket_id="STAB-1", ticket_text=TICKET_TEXT)


def _populate(state):
    # Give each downstream agent the fields it may read.
    state.plan = planner.run(state)
    state.dev = developer.run(state, poisoned=False)
    state.review = reviewer.run(state)
    state.security = security.run(state)
    return state


@pytest.mark.parametrize("agent_name", ["planner", "developer", "reviewer", "security", "sre"])
def test_agent_output_shape_is_stable_over_10_runs(agent_name):
    calls = {
        "planner":   lambda s: planner.run(s),
        "developer": lambda s: developer.run(s, poisoned=False),
        "reviewer":  lambda s: reviewer.run(s),
        "security":  lambda s: security.run(s),
        "sre":       lambda s: sre.run(s),
    }
    run_agent = calls[agent_name]

    shapes = []
    for _ in range(10):
        state = _populate(_fresh_state())
        result = run_agent(state)
        shapes.append(_shape(result))

    first = shapes[0]
    for i, shape in enumerate(shapes):
        assert shape == first, (
            f"{agent_name} run {i} drifted in shape:\n  first={first}\n  got  ={shape}"
        )


def test_shapes_match_the_declared_types():
    # Presence + type sanity against the frozen contract.
    state = _populate(_fresh_state())
    assert set(_shape(state.plan)) == {"tasks", "acceptance_criteria", "target_files", "notes"}
    assert set(_shape(state.dev)) == {"branch", "diff", "summary", "files_changed", "pr_url"}
    assert set(_shape(state.review)) == {"verdict", "comments", "must_fix"}
    # scan_provenance added in week 3 for the timeline UI: "blocked" proves two
    # different things depending on whether real scanners ran, and this is the
    # field that tells them apart. See state.ScanProvenance.
    assert set(_shape(state.security)) == {
        # `scoring` added by the final phase's Phase 0 contract batch -- the per-finding
        # go/no-go transparency artifact. Listed here because this assertion is a
        # FINGERPRINT of the contract, deliberately: a field arriving on SecurityResult
        # without anyone noticing is exactly what it exists to catch, and it did catch
        # this one. Updating it is the intended workflow; deleting it is not.
        "verdict", "findings", "blocking", "explanation", "scan_provenance", "scoring",
    }
    assert state.review.verdict in ("approve", "changes_requested")
    assert state.security.verdict in ("pass", "block")
