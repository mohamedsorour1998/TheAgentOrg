"""The cloud path must be able to load a run on either state backend.

PROVED, before the fix:

    $ STATE_BACKEND=dynamodb .venv-main/bin/python -c \
        "from agentorg import gates; gates._state_path('x')"
    RuntimeError: there is no state FILE on the 'dynamodb' backend; the run's
    state is an item in 'theagentorg-runs'. Read it with gates.load(run_id) ...

`run_stage._load` called `gates._state_path`, which refuses on that backend BY
DESIGN, so every cloud stage after `plan` raised. CLAUDE.md carried it as KNOWN
DEBT; `gates.load` already handled both backends correctly, so the fix is three
lines rather than the rewrite the old docstring implied.

THE REFUSAL FOR AN ABSENT RUN IS KEPT, and that is the half that matters.
`gates.load` raises `FileNotFoundError` on BOTH backends for a run that does not
exist, deliberately the same exception. `_load` turns it into the named
`SystemExit` about a broken artifact handoff. It must NOT be softened into a
fresh `RunState` -- that would start a new run and report success for work it
invented, which is the same defect as a check that did not run.
"""

import importlib.util
from pathlib import Path

import pytest

from agentorg import gates
from agentorg.common import config
from agentorg.state import RunState

REPO_ROOT = Path(__file__).resolve().parent.parent
STAGE_SCRIPT = REPO_ROOT / "scripts" / "run_stage.py"


def _stage_module():
    spec = importlib.util.spec_from_file_location("run_stage_backend_test", STAGE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_load_reads_through_gates_load_and_never_touches_the_path_helper(monkeypatch):
    """THE SEAM TEST. `_state_path` must not be on this code path at all.

    Asserted by making `gates._state_path` raise `AssertionError` if it is
    called, rather than by checking the happy path still works: reading through
    `gates.load` on the LOCAL backend produces an identical result either way,
    because `gates.load` calls `_state_path` itself. So a test that only checked
    the returned state would pass against both the fix and the bug -- the exact
    shape of confidence that cannot be falsified.
    """
    module = _stage_module()
    original = RunState(ticket_id="CLOUD-1", ticket_text="x")

    def _forbidden(run_id):
        raise AssertionError(
            f"run_stage._load called gates._state_path({run_id!r}). That helper "
            f"refuses on the dynamodb backend BY DESIGN, so every cloud stage "
            f"after `plan` raises there. Read through gates.load instead, which "
            f"already handles both backends."
        )

    monkeypatch.setattr(module.gates, "load", lambda run_id: original)
    monkeypatch.setattr(module.gates, "_state_path", _forbidden)

    loaded = module._load(original.run_id)
    assert loaded.run_id == original.run_id


def test_load_works_on_the_DYNAMODB_backend(monkeypatch, tmp_path):
    """The measured defect, driven on the backend that exhibited it.

    `log.write_state` / `read_state` are the dynamodb seam `gates.load` uses;
    stubbed with an in-memory dict so this needs no AWS. The point is that
    `_load` reaches the state at all -- before the fix this raised RuntimeError
    from `_state_path` before any read was attempted.
    """
    module = _stage_module()
    monkeypatch.setattr(config, "STATE_BACKEND", "dynamodb")

    stored: dict[str, str] = {}
    monkeypatch.setattr(module.log, "write_state",
                        lambda run_id, doc: stored.__setitem__(run_id, doc))
    monkeypatch.setattr(module.log, "read_state", lambda run_id: stored.get(run_id))

    original = RunState(ticket_id="CLOUD-1", ticket_text="x")
    gates.save(original)
    assert stored, "the save did not reach the stubbed dynamodb seam"

    loaded = module._load(original.run_id)
    assert loaded.run_id == original.run_id, (
        "run_stage._load could not read a run back on the dynamodb backend"
    )
    assert loaded.ticket_id == "CLOUD-1"


def test_an_absent_run_is_a_NAMED_SystemExit_not_a_fresh_RunState(monkeypatch,
                                                                 tmp_path):
    """The refusal that must survive the change.

    Softening this into a fresh `RunState` would start a new run and report
    success for work it invented -- every approval already recorded silently
    discarded, with the job still green. `gates.load` raises FileNotFoundError
    on both backends for exactly this, and `_load` names the likely cause.
    """
    module = _stage_module()
    monkeypatch.setattr(module.gates, "_STATE_DIR", tmp_path)

    with pytest.raises(SystemExit) as caught:
        module._load("00000000-0000-4000-8000-000000000000")

    message = str(caught.value)
    assert "no state file" in message, (
        f"the refusal does not say what was missing. "
        f"`tests/test_run_pipeline_workflow.py:1381` asserts on this exact "
        f"phrase, so it is a cross-file dependency rather than a wording "
        f"preference: {message!r}"
    )
    assert "artifact" in message, (
        f"the refusal does not name the likely cause -- a broken artifact "
        f"handoff -- so an operator reading it has nowhere to start: {message!r}"
    )
    assert "new run" in message or "cannot start" in message, (
        f"the refusal does not say that starting a fresh run is what it is "
        f"refusing to do: {message!r}"
    )


def test_the_absent_run_refusal_also_holds_on_the_dynamodb_backend(monkeypatch):
    """Both backends raise FileNotFoundError for an absent run; both must exit.

    A fix that caught `FileNotFoundError` only where the local backend raises it
    would leave the cloud path returning something for a run that does not
    exist.
    """
    module = _stage_module()
    monkeypatch.setattr(config, "STATE_BACKEND", "dynamodb")
    monkeypatch.setattr(module.log, "read_state", lambda run_id: None)

    with pytest.raises(SystemExit):
        module._load("00000000-0000-4000-8000-000000000000")


def test_the_docstring_no_longer_claims_this_is_broken():
    """The prose is part of the fix, because it is what the next reader trusts.

    The old docstring said this function RAISES on the dynamodb backend and
    called it KNOWN DEBT deliberately not fixed. Leaving that would send the next
    person to re-derive a limitation that no longer exists.

    Asserted on the CLAIM, not on the words: CLAUDE.md's standing rule is that a
    corrected claim is stated rather than quietly deleted, so the docstring
    legitimately still contains the phrase "KNOWN DEBT" while recording what that
    claim used to say. A substring check would therefore forbid the honest form
    and reward silent deletion. What must be gone is the assertion that the fix
    has NOT been made.
    """
    module = _stage_module()
    doc = module._load.__doc__ or ""
    assert "gates.load" in doc, (
        "the docstring does not say what this function now reads through"
    )
    assert "deliberately not fixed" not in doc, (
        "the docstring still says the dynamodb path is deliberately unfixed"
    )
    assert "It is fixed now" in doc or "is fixed now" in doc, (
        "the docstring records the old limitation without stating that it has "
        "been fixed, so a reader cannot tell which claim is current"
    )
