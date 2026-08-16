import pytest

from agentorg.state import RunState
from agentorg.agents import planner, developer, reviewer, security, sre

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
    assert set(_shape(state.security)) == {"verdict", "findings", "blocking", "explanation"}
    assert state.review.verdict in ("approve", "changes_requested")
    assert state.security.verdict in ("pass", "block")
