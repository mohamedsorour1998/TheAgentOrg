"""Which answered -- the model, or a fixture? The question scan_provenance answers.

MEASURED 2026-08-22: all four model-calling agents were serving fixtures in the
deployed pipeline because of an IAM denial, and NOTHING said so. The plan comment
on the target repo matched fixtures/plan_result.json byte for byte, every job was
green, and the only trace was a WARNING inside a container log nobody reads
during a demo.

`SecurityResult.scan_provenance` already prevents exactly this for the scanner
path, and it is the reason that path could be verified at all. This module pins
the equivalent for the model path.

WHAT THIS FILE DOES NOT COVER, stated because the gap is deliberate rather than
an oversight: `graph.py` and `scripts/run_stage.py` are the two places that copy
`llm.last_source()` onto `RunState.model_provenance` and render it in a comment,
and they belong to another lane. So the end-to-end label and the `_source:` line
on the pull request are pinned by that lane's tests, not here. Everything below
stops at the seam this lane owns: `llm`, and the four agents that fall back.
"""

import pytest

from agentorg.agents import developer, planner, reviewer, sre
from agentorg.common import config, llm
from agentorg.state import DevResult, PlanResult, RunState

_REPLY = ('{"tasks": ["a"], "acceptance_criteria": ["b"], "target_files": ["c"]}')


@pytest.fixture(autouse=True)
def _forget_which_path_answered():
    """Clear the module-level record on BOTH sides of every test in this file.

    `_LAST_SOURCE` is process-lifetime state, so without this a test inherits
    whichever path the previous one took -- and a leaked `"fixture"` cannot be
    overwritten by design, which would make a later `"model"` assertion fail for
    a reason that has nothing to do with the code under test. Same both-sides
    shape, and the same reasoning, as conftest's scanner-cache fixture: a stale
    value looks exactly like a real observation.
    """
    llm.reset_source()
    yield
    llm.reset_source()


def _model_answers(monkeypatch, reply):
    """Opt in to the model path, replacing ALL THREE layers conftest guards.

    Replacing only the policy knob and `available()` leaves `_complete` as the
    real, billable Bedrock call -- the exact bug conftest's raiser exists to
    catch. See tests/conftest.py.
    """
    monkeypatch.setattr(config, "LLM_DISABLED", False)
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "_complete", lambda s, u: reply)


def _state() -> RunState:
    return RunState(ticket_id="T-1", ticket_text="Add a per-IP login rate limit.")


# --------------------------------------------------------------------------
# The field, and the two answers
# --------------------------------------------------------------------------

def test_the_field_exists_and_defaults_to_unknown():
    """An optional ADDITION to the frozen contract, defaulting falsy."""
    assert _state().model_provenance == "", (
        "RunState.model_provenance must default to the empty string -- a run "
        "written before this field existed carries no provenance, and guessing "
        "one is what this field exists to prevent"
    )


def test_no_call_yet_is_None_not_a_guess():
    """The complement of both answers below.

    None means "nothing has asked a model since the reset". Defaulting to either
    real value would make a run that never reached an agent indistinguishable
    from one that did.
    """
    assert llm.last_source() is None, (
        f"llm.last_source() is {llm.last_source()!r} before any call; it must be "
        f"None, or a run that never called a model reads as one that did"
    )


def test_a_disabled_model_records_fixture_not_silence(monkeypatch):
    """The case that was live in the deployed pipeline for a week."""
    monkeypatch.setattr(config, "LLM_DISABLED", True)
    result = llm.structured(RunState, "sys", "user")
    assert result is None, "a disabled model must return None"
    assert llm.last_source() == "fixture", (
        f"llm.last_source() is {llm.last_source()!r} after a disabled-model "
        f"call; it must be 'fixture', because a caller that cannot tell the "
        f"model did not answer is the exact defect this field exists to surface"
    )


def test_a_real_reply_records_model(monkeypatch):
    """The complement. Without this the field could be hardcoded to 'fixture'."""
    _model_answers(monkeypatch, _REPLY)
    assert llm.structured(PlanResult, "sys", "user") is not None, (
        "the stubbed model reply should have parsed; this test would pin nothing"
    )
    assert llm.last_source() == "model", (
        f"llm.last_source() is {llm.last_source()!r} after a successful model "
        f"call; it must be 'model', or the discriminator cannot distinguish the "
        f"two paths and is worthless"
    )


# --------------------------------------------------------------------------
# Every path in text() that returns None is a fixture path
# --------------------------------------------------------------------------

def test_a_raising_model_records_fixture(monkeypatch):
    """The IAM denial's own shape: the call was made and refused."""
    def _boom(system_prompt, user_prompt):
        raise RuntimeError("AccessDeniedException: not authorized to InvokeModel")

    _model_answers(monkeypatch, _REPLY)
    monkeypatch.setattr(llm, "_complete", _boom)
    assert llm.text("sys", "user") is None
    assert llm.last_source() == "fixture", (
        f"a model call that RAISED recorded {llm.last_source()!r}. This is the "
        f"measured production case -- bedrock:InvokeModel was implicitDeny and "
        f"the caller fell back -- so it must read 'fixture'"
    )


def test_a_non_string_reply_records_fixture(monkeypatch):
    """`_complete` is the substituted seam, so it can hand back anything."""
    _model_answers(monkeypatch, None)
    assert llm.text("sys", "user") is None
    assert llm.last_source() == "fixture", (
        f"a non-string reply recorded {llm.last_source()!r}; the caller falls "
        f"back to its fixture, so that is what the field must say"
    )


def test_an_empty_reply_records_fixture(monkeypatch):
    """Whitespace only. The model spoke and said nothing usable.

    This is the path that is easiest to miss, because `return reply or None`
    reaches it and the success case through the SAME line -- so a single
    `_record("model")` above that line would label an empty answer a model run.
    """
    _model_answers(monkeypatch, "   \n\t  ")
    assert llm.text("sys", "user") is None
    assert llm.last_source() == "fixture", (
        f"an empty-after-strip reply recorded {llm.last_source()!r}. The caller "
        f"gets None and loads its fixture, so 'model' would be a false claim"
    )


def test_an_unparseable_reply_records_fixture_because_the_caller_falls_back(monkeypatch):
    """A model that answered garbage is a fixture run from the caller's view.

    `structured()` returns None, so the agent loads its fixture. Recording
    'model' here would claim the run used model output when it did not -- and
    note `text()` legitimately recorded 'model' on the way through, so this
    pins that `structured` OVERWRITES it rather than leaving it.
    """
    _model_answers(monkeypatch, "not json at all")
    assert llm.structured(PlanResult, "sys", "user") is None
    assert llm.last_source() == "fixture", (
        f"an unparseable reply recorded {llm.last_source()!r}. text() saw a "
        f"usable string and recorded 'model'; structured() must overwrite that, "
        f"because claiming 'model' would assert the run used model output"
    )


def test_a_reply_that_fails_validation_records_fixture(monkeypatch):
    """Well-formed JSON, wrong shape. Same outcome, a different branch.

    `model_validate_json` raises ValidationError here rather than ValueError, so
    a fix that recorded only on the parse branch would miss this one.
    """
    _model_answers(monkeypatch, '{"wrong": "shape"}')
    assert llm.structured(PlanResult, "sys", "user") is None
    assert llm.last_source() == "fixture", (
        f"a reply that parsed but failed validation recorded "
        f"{llm.last_source()!r}; the caller still falls back to its fixture"
    )


# --------------------------------------------------------------------------
# The asymmetry: fixture never downgrades to model
# --------------------------------------------------------------------------

def test_fixture_then_model_stays_fixture(monkeypatch):
    """A run where ANY agent fell back is not a model run.

    THE ASYMMETRY IS THE WHOLE MECHANISM. Five agents share one record, so
    without it the LAST agent to answer decides the label -- and one successful
    call would paper over four denials, which is the shape of the defect this
    field exists to surface. A partial outage must not read as no outage.
    """
    monkeypatch.setattr(config, "LLM_DISABLED", True)
    assert llm.text("sys", "user") is None
    assert llm.last_source() == "fixture", "the first call must record fixture"

    _model_answers(monkeypatch, "a real answer")
    assert llm.text("sys", "user") == "a real answer", (
        "the second call must genuinely succeed, or this test proves nothing "
        "about the guard -- it would just be observing another failure"
    )
    assert llm.last_source() == "fixture", (
        "a successful model call OVERWROTE an earlier fixture fallback. One "
        "agent reaching the model does not make a run where another fell back "
        "into a model run; letting the optimistic value win is the defect."
    )


def test_model_then_fixture_becomes_fixture(monkeypatch):
    """The other direction MUST move, or the field would freeze on its first value.

    The complement of the test above, and it is what proves that one is a
    directional guard rather than a write-once latch.

    Written as one `_complete` that answers and then raises, rather than by
    re-patching `LLM_DISABLED` between the calls: `_model_answers` stubs
    `available()`, so flipping the knob underneath it would change nothing and
    the test would pass on a stale first answer. This shape is also the measured
    production one -- a model that worked and then started refusing.
    """
    answers = ["a real answer"]

    def _once_then_denied(system_prompt, user_prompt):
        if answers:
            return answers.pop()
        raise RuntimeError("AccessDeniedException: not authorized to InvokeModel")

    _model_answers(monkeypatch, None)
    monkeypatch.setattr(llm, "_complete", _once_then_denied)

    assert llm.text("sys", "user") == "a real answer"
    assert llm.last_source() == "model", "the first call must record model"

    assert llm.text("sys", "user") is None, (
        "the second call must genuinely fail, or this test observes nothing"
    )
    assert llm.last_source() == "fixture", (
        "a fixture fallback AFTER a successful call left the record at 'model'. "
        "The guard is one-directional -- fixture is sticky, model is not -- and "
        "a write-once latch would report the first agent's luck for the run."
    )


def test_reset_source_actually_forgets(monkeypatch):
    """Without this, `reset_source` could be a no-op and every test above still pass.

    A sticky 'fixture' that cannot be cleared would make the SECOND run in a
    process report the first run's provenance -- and `agents/server.py` serves
    many runs from one process.
    """
    monkeypatch.setattr(config, "LLM_DISABLED", True)
    assert llm.text("sys", "user") is None
    assert llm.last_source() == "fixture"

    llm.reset_source()
    assert llm.last_source() is None, (
        f"after reset_source() the record is {llm.last_source()!r}. A sticky "
        f"value that cannot be cleared makes every later run in the same "
        f"process report the first one's provenance -- agents/server.py serves "
        f"many runs from one process."
    )


# --------------------------------------------------------------------------
# The agents: the fallback is theirs, so the record must survive their seam
# --------------------------------------------------------------------------

def test_each_agent_records_fixture_when_structured_returns_none(monkeypatch):
    """Every agent that falls back must say so, at ITS OWN fallback site.

    NOT redundant with the llm tests above, and the reason is the seam tests
    actually replace. Most of this suite monkeypatches `llm.structured`, not
    `llm._complete` -- so `llm`'s internal recording is BYPASSED entirely and
    nothing would be recorded at all. The agent knows it is loading a fixture;
    that is the one place the fact cannot be stubbed away.

    Parametrised over all four, because one agent stamping is not evidence the
    others do -- and the measured defect hit every one of them at once.
    """
    monkeypatch.setattr(llm, "structured", lambda *a, **k: None)
    calls = {
        "planner": lambda: planner.run(_state()),
        "developer": lambda: developer.run(_state(), poisoned=False),
        "reviewer": lambda: reviewer.run(_state()),
        "sre": lambda: sre.run(_state()),
    }
    for name, call in calls.items():
        llm.reset_source()
        result = call()
        assert result is not None, f"{name} returned nothing; this pins nothing"
        assert llm.last_source() == "fixture", (
            f"{name}.run fell back to its fixture and recorded "
            f"{llm.last_source()!r}. This suite stubs llm.structured rather "
            f"than llm._complete, so llm's own recording never runs -- the "
            f"agent must stamp its own fallback or the provenance is silent on "
            f"the path every test and every offline run takes."
        )


def test_an_agent_that_reached_the_model_does_not_record_fixture(monkeypatch):
    """The anti-vacuity check for the test above.

    An agent that called `record_fixture_fallback()` unconditionally would pass
    every assertion above while making the field a constant.
    """
    monkeypatch.setattr(
        llm, "structured",
        lambda *a, **k: PlanResult(tasks=["t"], acceptance_criteria=["a"],
                                   target_files=["f"]),
    )
    llm.reset_source()
    result = planner.run(_state())
    assert result.tasks == ["t"], "the stub's plan was not used; this pins nothing"
    assert llm.last_source() != "fixture", (
        "the planner used the model's plan and still recorded 'fixture'. The "
        "stamp is unconditional, so the field is a constant and distinguishes "
        "nothing."
    )


def test_the_developers_poisoned_safety_net_is_not_a_fixture_fallback(monkeypatch):
    """A rescued diff is still a model run, and the distinction is documented.

    The safety net swaps `diff` and `files_changed` for the reference fixture's
    while KEEPING the model's summary -- CLAUDE.md records that asymmetry as the
    only observable difference between the rescue path and a wholesale fallback.
    So it must not be labelled `fixture`: the model answered, and a separate
    demo mechanism edited one field of its answer. Collapsing the two would make
    every poisoned run look like a model outage.
    """
    clean = DevResult(branch="b", diff="--- a/x\n+++ b/x\n@@ -1 +1 @@\n+safe\n",
                      summary="model wrote this", files_changed=["x"])
    monkeypatch.setattr(llm, "structured", lambda *a, **k: clean)
    llm.reset_source()
    dev = developer.run(_state(), poisoned=True)
    assert "AKIA" in dev.diff, (
        "the safety net did not substitute the reference diff, so this test is "
        "not on the path it claims to describe"
    )
    assert dev.summary == "model wrote this", (
        "the summary was rewritten, so this run is indistinguishable from a "
        "wholesale fixture fallback -- see developer.py"
    )
    assert llm.last_source() != "fixture", (
        f"the poisoned safety net recorded {llm.last_source()!r}. The model "
        f"answered and one field of its answer was replaced by a demo "
        f"mechanism; labelling that a fixture run would make every poisoned "
        f"demo look like a model outage."
    )


# ── the provenance must cross the REMOTE seam, which is where it failed ────────
#
# MEASURED 2026-08-22 on the deployed pipeline. The plan job printed
# `_source=none` while the plan comment on the target repo carried six tasks
# naming files no fixture contains -- so the model had plainly answered and the
# provenance feature reported nothing, on precisely the path it exists to
# describe.
#
# The cause: under REMOTE_AGENTS=true the model call happens INSIDE the container,
# and `llm.last_source()` on the runner never sees it. Same shape as
# RunState.poisoned -- a fact the container must communicate rather than one the
# caller can observe.


def test_the_container_reports_which_path_answered():
    """server.py must put `source` on the 200 envelope.

    Without it the runner has no way to know, and the field it fills is a
    confident-looking empty string.
    """
    import inspect

    from agentorg.agents import server

    source = inspect.getsource(server.Handler.do_POST)
    assert '"source"' in source, (
        "the 200 envelope carries no `source` key. Under REMOTE_AGENTS=true the "
        "model call happens in this container and llm.last_source() on the runner "
        "is always None, so the provenance field records nothing on the deployed "
        "path -- measured as `_source=none` beside a plan comment that was "
        "unmistakably model-written."
    )
    assert "reset_source" in source, (
        "the handler does not reset the source before running the agent. "
        "AgentCore reuses warm containers, so one early model success would label "
        "every later fixture answer a model answer."
    )


def test_the_client_records_the_container_reported_source(monkeypatch):
    """And the runner must read it back, or the round trip is decorative."""
    from agentorg.common import agent_client, config
    from agentorg.state import PlanResult, RunState

    plan = PlanResult(tasks=["t"], acceptance_criteria=["a"], target_files=["f"])
    envelope = {
        "agent": "planner",
        "result": plan.model_dump(mode="json"),
        "source": "model",
    }

    monkeypatch.setattr(config, "REMOTE_AGENTS", True)
    monkeypatch.setattr(agent_client, "_remote_state", lambda r, s, k: s)
    monkeypatch.setattr(agent_client, "_invoke", lambda role, state: envelope)

    llm.reset_source()
    result = agent_client.call_agent(
        "planner", RunState(ticket_id="T-1", ticket_text="x")
    )
    assert isinstance(result, PlanResult)
    assert llm.last_source() == "model", (
        f"the container reported source='model' and the runner recorded "
        f"{llm.last_source()!r}. The fact crossed the wire and was dropped, which "
        f"leaves RunState.model_provenance empty on every remote run."
    )


def test_an_older_container_without_the_key_is_unknown_not_a_model_run(monkeypatch):
    """Backward compatibility, and it must fail toward unknown.

    A container deployed before this change omits `source`. Reading that absence
    as a model run would be the exact false claim this whole feature exists to
    prevent -- and it is the more flattering of the two possible guesses, which is
    why it needs its own test.
    """
    from agentorg.common import agent_client, config
    from agentorg.state import PlanResult, RunState

    plan = PlanResult(tasks=["t"], acceptance_criteria=["a"], target_files=["f"])
    envelope = {"agent": "planner", "result": plan.model_dump(mode="json")}

    monkeypatch.setattr(config, "REMOTE_AGENTS", True)
    monkeypatch.setattr(agent_client, "_remote_state", lambda r, s, k: s)
    monkeypatch.setattr(agent_client, "_invoke", lambda role, state: envelope)

    llm.reset_source()
    agent_client.call_agent("planner", RunState(ticket_id="T-1", ticket_text="x"))
    assert llm.last_source() is None, (
        f"an envelope with no `source` key recorded {llm.last_source()!r}. An "
        f"older container cannot report its provenance, and the honest answer is "
        f"unknown -- never 'model'."
    )


def test_a_garbage_source_value_is_ignored_rather_than_recorded(monkeypatch):
    """The envelope is remote input. An unrecognised value is not a provenance."""
    from agentorg.common import agent_client, config
    from agentorg.state import PlanResult, RunState

    plan = PlanResult(tasks=["t"], acceptance_criteria=["a"], target_files=["f"])
    envelope = {
        "agent": "planner",
        "result": plan.model_dump(mode="json"),
        "source": "definitely-a-model-trust-me",
    }

    monkeypatch.setattr(config, "REMOTE_AGENTS", True)
    monkeypatch.setattr(agent_client, "_remote_state", lambda r, s, k: s)
    monkeypatch.setattr(agent_client, "_invoke", lambda role, state: envelope)

    llm.reset_source()
    agent_client.call_agent("planner", RunState(ticket_id="T-1", ticket_text="x"))
    assert llm.last_source() is None, (
        f"an unrecognised source value was recorded as {llm.last_source()!r}. Only "
        f"the two known values may be, or a container can assert any provenance it "
        f"likes into the run's record."
    )
