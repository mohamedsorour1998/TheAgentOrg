"""G2 — separation of authority, asserted STRUCTURALLY.

The claim: the test-generation agent reads the ticket's acceptance criteria and the
repository, and NOT the change under review. If it read the diff, the generated test
would be a restatement of the change's own assumptions and would pass whether or not
the change is correct -- the same argument that keeps `compute_security_verdict` free
of any model, one layer out.

WHY THESE TESTS WALK THE AST AND DO NOT GREP. `agentorg/agents/testgen.py` is roughly
40% commentary, and its module docstring says "is NOT given `state.dev`" in those
words. So `assert "state.dev" not in source` would fail on the sentence explaining the
guarantee, and `assert "acceptance_criteria" in source` is satisfied by any comment
mentioning it. CLAUDE.md records this exact failure twice in one lane -- a test
satisfied by the comment explaining the thing it was checking -- and both fixes have
the shape used here: assert over the AST, and add a guard that the parse is not
vacuous.

A later "improvement" that hands the diff to the generator would look like
helpfulness. These tests are what makes it fail by name.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from agentorg import repo_snapshot
from agentorg.agents import reviewer, testgen
from agentorg.state import GeneratedTests, PlanResult, RunState

TESTGEN_SOURCE = Path(inspect.getfile(testgen))


def _tree() -> ast.Module:
    return ast.parse(TESTGEN_SOURCE.read_text())


def _function(name: str) -> ast.FunctionDef:
    """The named top-level function's AST node, or fail loudly.

    Failing rather than skipping is the point: a renamed `_prompt` must break these
    tests instead of quietly deselecting them. A test that can delete itself when the
    mechanism moves is the shape CLAUDE.md calls "a skip whose condition depends on
    the thing under test".
    """
    for node in _tree().body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    pytest.fail(
        f"testgen.py declares no top-level function {name!r}; this test would pin "
        f"nothing. If it was renamed, rename it here too."
    )


def _attribute_chains(node: ast.AST) -> set[str]:
    """Every dotted attribute access under `node`, as dotted strings.

    `state.dev.diff` yields {"state.dev", "state.dev.diff"}, so a test can ask about
    the receiver without knowing which field is read off it.
    """
    found: set[str] = set()
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Attribute):
            continue
        parts: list[str] = []
        cursor: ast.AST = sub
        while isinstance(cursor, ast.Attribute):
            parts.append(cursor.attr)
            cursor = cursor.value
        if isinstance(cursor, ast.Name):
            parts.append(cursor.id)
            found.add(".".join(reversed(parts)))
    return found


# ── the parse is not vacuous ───────────────────────────────────────────────────

def test_the_ast_walk_actually_sees_the_prompt_builder():
    """THE GUARD ON EVERY TEST BELOW. Without it they could all pass on an empty parse.

    CLAUDE.md: any test whose matcher can match nothing must assert that it matched.
    `_attribute_chains` over a function that failed to parse returns an empty set, and
    "no forbidden access" is trivially true of nothing.
    """
    chains = _attribute_chains(_function("_prompt"))
    assert chains, "no attribute access found in testgen._prompt; every test in this file would pin nothing"
    assert any(c.startswith("state.") for c in chains), (
        f"testgen._prompt reads nothing off `state`. Found: {sorted(chains)}"
    )


# ── G2: the generator does not read the change ─────────────────────────────────

def test_the_generator_never_reads_the_diff_it_is_meant_to_be_independent_of():
    """`_prompt` must not touch `state.dev` in any form. Asserted over the AST.

    Over the AST because the module docstring says these words in prose. A substring
    check passes on the explanation while a real `state.dev.diff` sits in the body,
    which is this repository's most-repeated test defect.
    """
    chains = _attribute_chains(_function("_prompt"))
    forbidden = sorted(c for c in chains if c.startswith("state.dev"))
    assert not forbidden, (
        f"testgen._prompt reads {forbidden}. The generator must be independent of the "
        f"change under review -- a test written from the diff restates the change's "
        f"own assumptions and passes whether or not the change is correct."
    )


def test_the_generator_reads_the_acceptance_criteria():
    """The positive half. Independence is worthless if it reads nothing instead.

    Both halves are needed: a `_prompt` returning the empty string would satisfy the
    test above perfectly.
    """
    chains = _attribute_chains(_function("_prompt"))
    assert "state.plan.acceptance_criteria" in chains, (
        f"testgen._prompt does not read state.plan.acceptance_criteria, so it is not "
        f"generating from the ticket's criteria at all. Found: {sorted(chains)}"
    )


def test_the_whole_module_reads_no_field_of_the_developers_result():
    """Wider than `_prompt`: NO function in testgen.py may read `state.dev`.

    `_prompt` is where a diff would arrive today, but a helper added later could feed
    it in from anywhere in the file and `_prompt` itself would stay clean.
    """
    offenders = sorted(c for c in _attribute_chains(_tree()) if c.startswith("state.dev"))
    assert not offenders, (
        f"testgen.py reads {offenders} somewhere. The independence claim is about the "
        f"module, not one function."
    )


def test_the_snapshot_is_the_BEFORE_view_and_the_reviewer_proves_the_switch_is_real():
    """No `diff=` on testgen's `render` call -- and the reviewer's call HAS one.

    THE SECOND HALF IS WHAT MAKES THIS TEST MEAN ANYTHING. "testgen passes no diff="
    would also be true if `render` had no such parameter, or if nobody in the codebase
    used it, or if the after-view had been deleted. Asserting the reviewer still passes
    it proves the keyword is a live switch this agent is deliberately not throwing.
    """
    signature = inspect.signature(repo_snapshot.render)
    assert "diff" in signature.parameters, (
        "repo_snapshot.render has no `diff` parameter, so 'testgen omits it' is "
        "vacuous. The before/after switch has moved; re-derive this test."
    )

    def render_calls(node: ast.AST) -> list[ast.Call]:
        return [
            sub for sub in ast.walk(node)
            if isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Attribute)
            and sub.func.attr == "render"
        ]

    testgen_calls = render_calls(_tree())
    assert testgen_calls, "testgen.py calls repo_snapshot.render nowhere; G1 is unmet and this test pins nothing"
    for call in testgen_calls:
        keywords = [kw.arg for kw in call.keywords]
        assert "diff" not in keywords, (
            f"testgen.py passes diff= to render at line {call.lineno}, which renders "
            f"the repository AS THE CHANGE WOULD LEAVE IT. That is the reviewer's "
            f"view, and it destroys this agent's independence."
        )

    reviewer_calls = render_calls(ast.parse(Path(inspect.getfile(reviewer)).read_text()))
    assert any("diff" in [kw.arg for kw in c.keywords] for c in reviewer_calls), (
        "reviewer.py no longer passes diff= to render, so 'testgen omits it' is not a "
        "deliberate distinction any more -- nobody uses the after-view. Both halves of "
        "this test must hold for either to mean anything."
    )


# ── G1: it can see the repository ──────────────────────────────────────────────

def test_the_generator_is_given_the_repository_it_is_asked_to_test(monkeypatch):
    """G1, executed rather than read. An agent that cannot see the file invents imports.

    CLAUDE.md, "THE DEVELOPER WAS WRITING GO FOR A FLASK APP": the clean run failed
    twice at the revision cap with the scanners reporting PASS, because the prompt
    named target FILES and never their contents. The snapshot is the fix, and this
    asserts the file's real text reaches the prompt.
    """
    monkeypatch.setattr(
        repo_snapshot,
        "snapshot",
        lambda: {"app/auth.py": "def authenticate(username, password):\n    return False\n"},
    )
    state = RunState(
        run_id="g1",
        ticket_id="1",
        ticket_text="Rate-limit login",
        plan=PlanResult(
            tasks=["t"],
            acceptance_criteria=["Six requests in one minute returns 429"],
            target_files=["app/auth.py"],
        ),
    )
    prompt = testgen._prompt(state)

    assert "def authenticate" in prompt, (
        "the target file's CONTENTS are not in the prompt. An agent asked to test a "
        "file it cannot read invents imports -- measured on this repository once."
    )
    assert "Six requests in one minute returns 429" in prompt, (
        "the acceptance criterion is not in the prompt"
    )


def test_a_diff_on_the_state_does_not_reach_the_prompt(monkeypatch):
    """The executed twin of the AST test. Put a distinctive string in the diff; it must not appear.

    The AST test forbids the ACCESS; this forbids the RESULT. Either alone could be
    satisfied by a route the other does not see -- a diff arriving through a helper
    that takes it as a plain argument, for instance.
    """
    from agentorg.state import DevResult

    monkeypatch.setattr(repo_snapshot, "snapshot", lambda: {"app/auth.py": "pass\n"})
    marker = "SENTINEL_FROM_THE_DEVELOPERS_DIFF"
    state = RunState(
        run_id="g2",
        ticket_id="1",
        ticket_text="Rate-limit login",
        plan=PlanResult(tasks=["t"], acceptance_criteria=["429 past the limit"],
                        target_files=["app/auth.py"]),
        dev=DevResult(branch="feat/x", diff=f"+++ b/app/auth.py\n+{marker}\n",
                      summary="s", files_changed=["app/auth.py"]),
    )

    assert marker not in testgen._prompt(state), (
        "the developer's diff reached the generator's prompt. The generated test would "
        "then be written from the change rather than from the ticket."
    )


def test_the_result_never_claims_the_diff_as_its_source(monkeypatch):
    """`GeneratedTests.source` must never be "diff" from this agent.

    The contract's vocabulary admits it, which is why the refusal needs asserting: a
    run whose tests were derived from the change would be honestly LABELLED, and the
    label is what an auditor reads. This agent's job is to make that label unreachable.
    """
    monkeypatch.setattr(repo_snapshot, "snapshot", lambda: {"app/auth.py": "pass\n"})
    monkeypatch.setattr(
        testgen.llm, "structured",
        lambda model_cls, system, user: testgen.TestPlan(
            files=[testgen.GeneratedFile(path="tests/test_x.py", content="def test_x():\n    assert True\n")],
            notes="n",
        ),
    )
    state = RunState(
        run_id="g3", ticket_id="1", ticket_text="t",
        plan=PlanResult(tasks=["t"], acceptance_criteria=["c"], target_files=["app/auth.py"]),
    )

    result = testgen.run(state)
    assert isinstance(result, GeneratedTests)
    assert result.source != testgen.SOURCE_FORBIDDEN, (
        f"testgen reported source={result.source!r}. The generator does not read the "
        f"diff, so it must never claim to have."
    )
    assert result.source == testgen.SOURCE_ACCEPTANCE
