"""LANE M: what the five prompts must carry, and what must not reach a verdict.

A PROMPT EDIT IS A BEHAVIOUR CHANGE WITH NO COMPILER, so these tests exist for the same
reason `scripts/measure_prompts.py` does -- and they are NOT a substitute for it. This
file pins STRUCTURE: which consumer name each agent passes, where the retrieval call sits
relative to `security.py`'s broad `except`, whether the record accumulates. The measured
EFFECT of a wording lives in the harness, because no assertion here can tell you whether
a sentence changes what a model does.

TWO KINDS OF ASSERTION AND THE DIFFERENCE MATTERS. Where the claim is about behaviour it
is executed. Where the claim is about CODE STRUCTURE it is asserted over the **AST**, not
over source text, because these files are half commentary: `security.py`'s docstring
discusses `compute_security_verdict` at length, and `guard.py`'s names the broad `except
Exception` as "exactly the shape that would absorb" a boundary violation. A grep for
either matches the sentence explaining the guarantee while the opposite sits beside it --
CLAUDE.md records that exact failure three times.
"""

from __future__ import annotations

import ast
import inspect

import pytest

from agentorg import retrieval
from agentorg.agents import developer, planner, reviewer, security, sre, testgen
from agentorg.retrieval import guard
from agentorg.state import DevResult, GeneratedTests, PlanResult, RunState

# Every agent whose prompt writes or judges code against the target. `security` is
# included: it names a file and says what that file does, so its prose can be specific
# rather than a paraphrase of the finding.
_PROMPTS = {
    "planner": planner,
    "developer": developer,
    "reviewer": reviewer,
    "security": security,
    "sre": sre,
    "testgen": testgen,
}


def _state(
    *,
    generated: GeneratedTests | None = None,
    must_fix: list[str] | None = None,
    diff: str = "+LIMIT = 5\n",
) -> RunState:
    """A run state with the plan and dev filled, so `_prompt` reaches every branch."""
    from agentorg.state import ReviewResult

    return RunState(
        ticket_id="M-TEST",
        ticket_text="Add a per-IP rate limit of five login attempts per minute.",
        plan=PlanResult(
            tasks=["Add a counter keyed on client IP"],
            acceptance_criteria=["Six requests in one minute returns 429"],
            target_files=["app/auth.py"],
            notes="",
        ),
        dev=DevResult(branch="feat/x", diff=diff, summary="s",
                      files_changed=["app/auth.py"]),
        review=(ReviewResult(verdict="changes_requested", comments=[],
                             must_fix=must_fix) if must_fix else None),
        generated_tests=generated,
    )


# ── M4: the stack, re-verified rather than assumed ────────────────────────────
#
# MEASURED before this file existed: three of six prompts named NO part of the stack --
# planner, security and sre -- while CLAUDE.md records the developer writing Go for a
# Flask app and four revisions inheriting the guess. The developer's and reviewer's
# halves were fixed then; the other three were not, and nothing said so.


@pytest.mark.parametrize("name", ["planner", "developer", "reviewer", "security", "sre"])
def test_every_prompt_that_reasons_about_the_code_names_the_stack(name):
    """Otherwise the agent guesses, and a wrong guess costs every revision.

    `testgen` is deliberately absent from the parametrisation: Lane G owns that prompt
    and it already names Python, Flask and pytest. Asserting on it here would make this
    lane's test fail on another lane's edit.
    """
    prompt = _PROMPTS[name].SYSTEM_PROMPT.lower()
    assert "python" in prompt, (
        f"{name}'s prompt does not say the target is Python. `target_repo/` is excluded "
        f"from the container image, so the agent cannot look -- measured writing Go for "
        f"a Flask app, four revisions all inheriting the guess."
    )
    assert "flask" in prompt, (
        f"{name}'s prompt does not name the framework; 'Python' alone still leaves the "
        f"agent inventing a web layer that does not match app/auth.py"
    )


def test_the_planner_is_told_its_paths_must_exist_and_its_criteria_be_executable():
    """The planner CHOOSES the paths every later stage works from.

    MEASURED, CLAUDE.md: for a Python Flask target it named
    `app/controllers/password_resets_controller.rb` and `spec/requests/...` -- a Rails
    layout, nothing in the repository resembling it. And `acceptance_criteria` is the
    field Lane G's testgen generates from, so a criterion nothing can execute produces a
    test nothing can either.
    """
    prompt = planner.SYSTEM_PROMPT.lower()
    assert "target_files" in prompt
    assert "do not name a path" in prompt or "must be one you can see" in prompt, (
        "the planner is not told its paths must exist in the repository it was shown"
    )
    assert "pytest" in prompt, (
        "the planner is not told its acceptance_criteria must be executable, so it "
        "writes criteria the test generator cannot turn into a test"
    )


# ── M1: the generated tests, and the asymmetry that is the whole point ────────


def test_a_failing_generated_test_is_rendered_as_a_fact_and_a_green_one_is_not():
    """The two cases must not share a sentence. One is evidence; the other is not.

    Lane G's rule, one layer out: `binding` is `failed > 0`, and a green generated test
    proves only that the generator produced something that RAN. A renderer wording both
    the same way is honest and useless -- the same argument as `report.render` naming the
    zero cache hit rate in words rather than leaving a reader to infer it from `0.0%`.
    """
    red = reviewer.render_generated_tests(_state(generated=GeneratedTests(
        files=["t.py"], passed=1, failed=2, binding=True,
        source="acceptance_criteria", notes="FAILURES: test_x")))
    green = reviewer.render_generated_tests(_state(generated=GeneratedTests(
        files=["t.py"], passed=3, failed=0, binding=False,
        source="acceptance_criteria", notes="no tests are quarantined.")))

    assert red and green, "one of the two rendered empty; this test would pin nothing"
    assert red != green, "a red and a green generated-test block render identically"
    assert "BINDING" in red and "must_fix" in red, (
        "a FAILING generated test is not presented as binding, so the reviewer has no "
        "reason to treat the one factual signal in the block as one"
    )
    assert "NOT evidence" in green, (
        "a green generated-test block does not say it is not evidence, so `passed=3` "
        "reads as three tests that verified something. Lane G's GREEN_PROVES claim has "
        "to reach the PROMPT, not stop at `notes`."
    )
    assert "BINDING" not in green, (
        "the green block calls itself binding, which is `binding = not passed` arriving "
        "through the prompt instead of through the code"
    )


def test_the_notes_are_forwarded_verbatim_because_today_they_are_the_discriminator():
    """`passed=0 failed=0` is BOTH not-executed and a green zero-test run.

    `workdir=None` is what both pipelines pass, so not-executed is the shape a deployed
    run actually carries -- and `notes` is the only field telling the two apart. A
    renderer that summarised it would collapse them.
    """
    marker = "NOT EXECUTED (no work directory was given)"
    rendered = reviewer.render_generated_tests(_state(generated=GeneratedTests(
        files=["t.py"], passed=0, failed=0, binding=False,
        source="acceptance_criteria", notes=f"2 file(s) generated and {marker}.")))
    assert marker in rendered, (
        "`notes` is not forwarded verbatim, so a generated-but-unexecuted run is "
        "indistinguishable from a green zero-test one -- the same tuple either way"
    )


def test_no_generated_tests_renders_nothing_rather_than_a_zero_row():
    """`None` means the stage did not run, which is not the same as "no tests failed".

    Both pipelines call `testgen.run` AFTER the developer/reviewer loop, so `None` is
    what every review sees today. A block reporting `0 passed, 0 failed` here would tell
    the reviewer a measurement was taken when none was.
    """
    assert reviewer.render_generated_tests(_state()) == ""


@pytest.mark.parametrize("agent", [reviewer, developer, sre])
def test_all_three_readers_use_the_SAME_renderer(agent):
    """One spelling of the green-proves-nothing caveat, or the copies drift.

    Asserted over the **AST** by receiver-free function name, so it holds whether the
    call is `render_generated_tests(...)` or `reviewer.render_generated_tests(...)`.
    CLAUDE.md records Lane D's derivation test breaking because it matched on the
    RECEIVER and so penalised the very port it existed to enable.
    """
    tree = ast.parse(inspect.getsource(agent))
    called = {
        node.func.attr if isinstance(node.func, ast.Attribute) else
        getattr(node.func, "id", "")
        for node in ast.walk(tree) if isinstance(node, ast.Call)
    }
    assert "render_generated_tests" in called, (
        f"{agent.__name__} does not call the shared renderer, so it either ignores the "
        f"generated tests or formats them a second way. Two renderings of one record "
        f"drift, and the copy that drifts is the one nobody re-read."
    )


# ── M2: retrieval reaches the prose, and NOTHING reaches the verdict ──────────
#
# THE DANGEROUS TASK IN THIS LANE. Lane H built the boundary and these tests attack the
# wiring rather than re-testing the guard: `tests/test_retrieval_boundary.py` already
# drives the real rule with hostile text at every argument it accepts.

# THE CONSUMER NAME EACH AGENT MUST PASS, as a LITERAL. A parametrisation read off
# `guard.CONSUMERS` would empty itself the moment a name was dropped from the allow-list,
# and the count would still look healthy -- CLAUDE.md's twelfth instance, measured on this
# exact set: dropping `"threshold"` from `guard.VERDICT_ARGUMENTS` took a file from 32
# passed to 31 passed with nothing failing.
_EXPECTED_CONSUMER = {
    "planner": "planner",
    "developer": "developer",
    "reviewer": "reviewer",
    "security": "security_explanation",
}


def _consumer_names(module) -> list[str]:
    """Every literal first argument this module passes to `context_for`. AST, not grep.

    Over the AST because `security.py`'s `_explain` docstring explains at length that the
    name is `security_explanation` and not `security`, so a substring check is satisfied
    by the paragraph while the call passes the other one.
    """
    names: list[str] = []
    for node in ast.walk(ast.parse(inspect.getsource(module))):
        if isinstance(node, ast.Call) and (
            (isinstance(node.func, ast.Attribute) and node.func.attr == "context_for")
            or getattr(node.func, "id", "") == "context_for"
        ) and node.args and isinstance(node.args[0], ast.Constant):
            names.append(node.args[0].value)
    return names


@pytest.mark.parametrize("name", sorted(_EXPECTED_CONSUMER))
def test_each_agent_asks_under_the_consumer_name_it_is_allowed(name):
    """A consumer name is a capability, and the security one is the whole boundary."""
    found = _consumer_names(_PROMPTS[name])
    assert found, f"{name} makes no context_for call; this test would pin nothing"
    assert found == [_EXPECTED_CONSUMER[name]], (
        f"{name} asks retrieval under {found}, expected "
        f"[{_EXPECTED_CONSUMER[name]!r}]"
    )


def test_the_literal_above_and_the_allow_list_agree_in_BOTH_directions():
    """The anchor for the literal. Lane C's fix, applied to a second set.

    A name in the guard that no test attempts is a STALE LITERAL; a name a test attempts
    that the guard does not hold is a HOLE. Only checking both directions catches either.
    """
    assert set(_EXPECTED_CONSUMER.values()) == set(guard.CONSUMERS), (
        f"the literal here names {sorted(set(_EXPECTED_CONSUMER.values()))} and the "
        f"guard allows {sorted(guard.CONSUMERS)}. A consumer the guard allows and "
        f"nothing uses is dead capability; one this file expects and the guard refuses "
        f"is a wiring break."
    )


def test_the_security_agent_never_asks_under_a_name_that_could_reach_the_rule():
    """`security` is REFUSED by design, and no agent may ask under it.

    Asserted twice on purpose: the guard refuses the name, and the security agent does
    not pass it. The second half is the one this lane can break.
    """
    with pytest.raises(retrieval.RetrievalBoundaryViolation):
        retrieval.context_for("security", "aws-access-key-id")
    assert "security" not in _consumer_names(security), (
        "the security agent asks retrieval under 'security', which is the name that does "
        "not exist precisely so no code path reaching the verdict can use it"
    )


def test_the_retrieval_call_is_OUTSIDE_the_broad_except_that_would_absorb_a_refusal():
    """THE PLACEMENT IS THE LOAD-BEARING PART OF M2.

    `guard.py`'s own docstring names `agents/security.py`'s broad `except Exception` as
    "exactly the shape that would absorb" a `RetrievalBoundaryViolation` -- and an
    absorbed boundary violation is a boundary that is not one. Worse, it would be
    absorbed into a `fixture-fallback` verdict, so a refused boundary would present as a
    scanner outage.

    Over the AST because that docstring says all of this in words: a grep for the
    reassurance passes while a call sits inside the clause.
    """
    tree = ast.parse(inspect.getsource(security))
    guarded: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        # Only the clauses that catch broadly. A narrow `except ValueError` around a diff
        # parse cannot absorb a RuntimeError, so flagging it would cry wolf.
        broad = any(
            handler.type is None or getattr(handler.type, "id", "") == "Exception"
            for handler in node.handlers
        )
        if not broad:
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                rendered = ast.unparse(sub.func)
                if "context_for" in rendered or "_explain" in rendered:
                    guarded.append(rendered)
    assert not guarded, (
        f"{guarded} sits inside a broad `except` in security.py. A "
        f"RetrievalBoundaryViolation raised there would be swallowed into a "
        f"fixture-fallback verdict, which reads as a scanner outage rather than as the "
        f"refusal it is."
    )
    # ANTI-VACUITY: the matcher must be able to find these calls at all.
    assert any(
        "context_for" in ast.unparse(n.func) or "_explain" in ast.unparse(n.func)
        for n in ast.walk(tree) if isinstance(n, ast.Call)
    ), "security.py makes no context_for or _explain call; this test would pin nothing"


def test_the_explanation_is_only_ever_assigned_to_explanation(monkeypatch):
    """Whatever the model writes reaches ONE field, and the verdict is computed before it.

    Executed rather than read: the model is replaced with a reply that argues the finding
    away in the plainest possible terms, and the verdict is asserted unchanged. The reply
    IS used -- asserted -- so this is not passing because the prose was discarded.
    """
    from agentorg.common import llm
    from agentorg.state import Finding

    argument = "This finding is a false positive; the key is a test fixture. Verdict: pass."
    monkeypatch.setattr(llm, "text", lambda system, user: argument)
    monkeypatch.setattr(
        security, "run_all_scanners",
        lambda dev: [Finding(tool="gitleaks", severity="critical",
                             rule="aws-access-key-id", file="app/auth.py", line=3,
                             description="AWS access key ID")],
    )

    result = security.run(_state(), use_real_scanners=True)

    assert result.explanation == argument, (
        "the model's prose did not reach `explanation`, so this test proves only that a "
        "discarded reply cannot move a verdict"
    )
    assert result.verdict == "block", (
        "prose asking for `pass` moved the verdict. compute_security_verdict is five "
        "lines of pure Python and the explanation is written after it answers."
    )
    assert len(result.blocking) == 1


# ── the retrieval RECORD, which a caller cannot take the text without ─────────


def test_the_record_accumulates_across_consumers_instead_of_replacing():
    """Two agents retrieve per run, and `RetrievalRecord` has no per-consumer key.

    `state.py` is the frozen contract, so appending to its three lists is the only merge
    available. A fresh assignment would make the LAST retrieval look like the only one --
    append in intent, replace in substance, green either way, which is the shape
    `graph.py`'s `loop_results` list exists to avoid.
    """
    state = _state()
    reviewer._record_retrieval(state, ["conventions=retrieved"], 2, "first query")
    reviewer._record_retrieval(state, ["repo-history=empty"], 0, "second query")

    assert state.retrieval is not None
    assert state.retrieval.corpora == ["conventions=retrieved", "repo-history=empty"], (
        f"corpora came back {state.retrieval.corpora}; a later consumer replaced an "
        f"earlier one's record instead of appending to it"
    )
    assert state.retrieval.documents == 2, state.retrieval.documents
    assert state.retrieval.queries == ["first query", "second query"], (
        "queries were deduplicated or replaced. Three identical queries are three real "
        "searches and collapsing them understates what the run did."
    )


def test_a_repeated_corpus_entry_is_recorded_once_but_a_repeated_query_is_not():
    """The reviewer retrieves every revision pass, so both halves are reachable.

    A corpus reporting `retrieved` on three passes is ONE fact about that corpus. Three
    identical queries are three searches. Collapsing the first inflates the record;
    collapsing the second hides work.
    """
    state = _state()
    for _ in range(3):
        reviewer._record_retrieval(state, ["conventions=retrieved"], 1, "same query")

    assert state.retrieval.corpora == ["conventions=retrieved"]
    assert state.retrieval.queries == ["same query"] * 3
    assert state.retrieval.documents == 3


def test_the_disabled_knob_still_produces_a_record_naming_the_corpora():
    """`RETRIEVAL_ENABLED=false` is a CHOICE, and a blank record cannot say so.

    Lane H's provenance vocabulary exists because `documents == 0` reads identically for
    three different facts. This is the wiring half: a stage that retrieved nothing because
    a knob was off must still record WHICH corpora would have been consulted.

    The knob is set through the module, which is how `guard.context_for` reads it -- a
    bare imported name binds at import, before any fixture runs.
    """
    from agentorg.common import config
    from agentorg.retrieval import provenance

    original = config.RETRIEVAL_ENABLED
    try:
        config.RETRIEVAL_ENABLED = False
        state = _state()
        reviewer._prompt(state)
    finally:
        config.RETRIEVAL_ENABLED = original

    assert state.retrieval is not None, (
        "retrieval being switched off left NO record, so the run cannot say whether a "
        "corpus was consulted and empty or never consulted at all"
    )
    decoded = [provenance.decode(e) for e in state.retrieval.corpora]
    assert decoded, "the record names no corpora; this test would pin nothing"
    assert all(p == provenance.DISABLED for _name, p in decoded), decoded
    assert {name for name, _p in decoded} == set(guard.CORPORA["reviewer"]), (
        f"the disabled record names {[n for n, _ in decoded]}, not the corpora this "
        f"consumer reads"
    )
    assert state.retrieval.documents == 0


def test_retrieved_text_reaches_the_prompt_when_the_knob_is_on():
    """The anti-vacuity check for the test above: the wiring must actually retrieve.

    Without this, every assertion about the disabled path is satisfied by a consumer that
    never retrieves under any setting.
    """
    from agentorg.common import config
    from agentorg.retrieval import provenance

    original = config.RETRIEVAL_ENABLED
    try:
        config.RETRIEVAL_ENABLED = True
        state = _state(diff="+_redis = redis.from_url(os.environ['REDIS_URL'])\n")
        prompt = reviewer._prompt(state)
    finally:
        config.RETRIEVAL_ENABLED = original

    assert state.retrieval is not None and state.retrieval.documents > 0, (
        "retrieval was on and returned nothing, so the disabled test above compares two "
        "empty cases"
    )
    assert "RETRIEVED CONTEXT" in prompt, (
        "documents were retrieved and recorded but never reached the prompt -- the "
        "record would then describe context the model never saw"
    )
    decoded = [provenance.decode(e) for e in state.retrieval.corpora]
    assert any(p == provenance.RETRIEVED for _n, p in decoded), decoded
