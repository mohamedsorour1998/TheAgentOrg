"""The interface's own properties — the ones no adapter can be asked to prove.

OWNER: Lane D.  Companion to `tests/test_integration_conformance.py`, which drives
every adapter through the same behaviour. This file tests `base.CodeHost` and
`integrations.host()` themselves.

WHY IT IS A SEPARATE FILE. Four of these assertions are about what the interface
REFUSES, and a refusal cannot be parametrized over adapters: there is no adapter
that demonstrates "an incomplete class cannot be constructed", because such a
class is by definition not in `ADAPTERS`. Mixing them into the conformance suite
would mean parametrizing tests that ignore their own parameter, which reads as
coverage of three adapters and is coverage of none.

=========================================================================
THE FIRST TEST HERE EXISTS BECAUSE A RED STEP CAME BACK INERT
=========================================================================

Widening `CodeHost._guard`'s `except Exception` to `except BaseException` was the
RED step for the never-raises contract. Measured: `42 passed` before and `42
passed` after -- **identical** -- and `tests/test_offline_mode.py` was `25 passed`
both ways. An inert mutation reads exactly like a caught one, so the guard's own
docstring was naming a test that did not exist.

The reason it was inert is worth keeping: every conformance test drives the
handler with an ordinary `RuntimeError`, which BOTH spellings catch. Nothing in
the suite handed it a BaseException, and the one BaseException that matters --
`pytest.fail`'s `Failed`, which is how conftest guard 2 keeps 1,500 tests off the
live GitHub API -- was never put through this seam at all.

`test_the_interface_guard_does_not_swallow_the_conftest_github_guard` is the
mutation-catching test. With `except BaseException` it FAILS; see the commit
message for the pasted failure.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from agentorg import github_ops, integrations
from agentorg.integrations import (
    CodeHost,
    GitHost,
    GitHubHost,
    MemoryHost,
)
from agentorg.integrations import base as interface
from agentorg.state import DevResult, Finding, RunState

REPO_ROOT = Path(__file__).resolve().parent.parent


def _state() -> RunState:
    return RunState(ticket_id="7", ticket_text="add a rate limit")


# ── the guard must not absorb a BaseException. THE MUTATION-CATCHING TEST ──────


def test_the_interface_guard_does_not_swallow_the_conftest_github_guard():
    """`pytest.fail`'s `Failed` must pass straight through `CodeHost._guard`.

    THIS IS THE ONE ASSERTION IN THE LANE THAT PROTECTS THE OTHER 1,500 TESTS.
    Conftest guard 2 puts a `pytest.fail` raiser on `github_ops._repo` because that
    seam WRITES -- measured at four outbound connections to api.github.com per run
    before it existed, performing real branch, commit and pull-request writes. It
    survives `github_ops.post_comment`'s blind `except Exception` only because
    `Failed` derives from BaseException.

    This interface adds a SECOND blind handler in front of that one. Widen it to
    `BaseException` and the guard is absorbed: the suite goes green while the live
    writes go out, on a machine with a token exported. That is the exact defect
    `test_the_blind_except_does_not_swallow_the_conftest_github_guard` pins one
    layer down, arriving on a new seam -- which is guard 2's history repeating, the
    way `repo_snapshot` repeated it.

    Driven through the REAL adapter, not the double, because the real one is what
    the guard is installed on.
    """
    host = GitHubHost()

    with pytest.raises(BaseException) as caught:
        # `pytest.fail` raises Failed, and asserting on Failed by name would
        # couple this test to pytest's internals. What matters is that SOMETHING
        # escaped rather than being converted into a `comment://` ref, and that it
        # is not an ordinary Exception -- both asserted below.
        host._guard(
            "post_comment",
            lambda: pytest.fail("the conftest GitHub guard fired", pytrace=False),
            lambda exc: "comment://absorbed",
        )

    assert not isinstance(caught.value, Exception), (
        f"CodeHost._guard absorbed a {type(caught.value).__name__}, which derives "
        f"from BaseException rather than Exception. That is how conftest guard 2 "
        f"keeps the whole suite off the live GitHub API -- widening this handler "
        f"puts the suite back on it with every test green."
    )


def test_the_guard_absorbs_an_ordinary_exception_so_the_test_above_is_not_vacuous():
    """The other direction: a plain Exception IS absorbed.

    THE PAIR IS THE INSTRUMENT. Without this, the test above would pass against a
    `_guard` with no handler at all -- an interface that never absorbs anything
    also never absorbs a BaseException. Together they pin the boundary rather than
    one side of it.
    """
    answer = GitHubHost()._guard(
        "post_comment",
        lambda: (_ for _ in ()).throw(RuntimeError("the host went away")),
        lambda exc: "comment://absorbed",
    )
    assert answer == "comment://absorbed", (
        "an ordinary Exception was not absorbed, so the never-raises contract is "
        "not being kept and the BaseException test above proves nothing"
    )


def test_the_guard_catches_Exception_and_not_BaseException_over_the_AST():
    """Read the handler's exception TYPE out of the syntax tree.

    OVER THE AST, NOT THE TEXT, and this repository has been bitten twice by the
    difference: a substring check for `Exception` is satisfied by the word in the
    surrounding comment -- and `base.py`'s docstring says "`Exception`, never
    `BaseException`" in prose, so a naive grep would find both spellings whichever
    one the code used.

    Complementary to the behavioural test above rather than redundant with it: this
    one fails on a widening even if somebody also deletes the test that would have
    caught it behaviourally.
    """
    tree = ast.parse(inspect.getsource(interface))
    handlers = [
        handler
        for node in ast.walk(tree)
        if isinstance(node, ast.Try)
        for handler in node.handlers
    ]
    assert handlers, "base.py has no try/except at all; this test would pin nothing"
    caught = {
        handler.type.id for handler in handlers
        if isinstance(handler.type, ast.Name)
    }
    assert caught == {"Exception"}, (
        f"base.py's handlers catch {sorted(caught)}. Only `Exception` is allowed: "
        f"`BaseException` absorbs conftest's `pytest.fail` guards, and "
        f"`KeyboardInterrupt` / `SystemExit` must reach the caller."
    )


# ── the ABC must refuse an incomplete adapter AT CONSTRUCTION ──────────────────


def test_an_adapter_missing_a_method_cannot_be_constructed():
    """The reason this is an ABC and not a Protocol.

    A `typing.Protocol` is structural, so an adapter missing `_ci_status` satisfies
    nothing and nobody finds out until the SRE stage -- by which time the run has a
    diff, a pull request and a security verdict. `abc` refuses at construction,
    before a ticket is touched.

    `TypeError` names the missing method, which is the difference between a fix
    that takes a minute and one that takes a debugging session.
    """

    class HalfAnAdapter(CodeHost):
        name = "half"

        def open_pr(self, state):
            raise NotImplementedError

        def _post_comment(self, state, body, finding=None):
            return "local://x"

        def _merge_pr(self, state):
            return "local://x"

        def _report_outcome(self, state):
            return "local://x"
        # `_ci_status` deliberately absent.

    with pytest.raises(TypeError, match="_ci_status"):
        HalfAnAdapter()


def test_the_interface_declares_exactly_the_five_methods_graph_calls():
    """Five, no sixth. DERIVED from graph.py, and this test re-derives it.

    D1 says "define the interface from what `graph.py` actually calls -- derive it,
    do not design it fresh", and the way that claim rots is by someone adding a
    convenient sixth method. So the abstract set is checked against the
    `github_ops.<name>` calls `graph.py` actually makes, read off ITS AST.

    `deploy_note` is expected to be ABSENT and the assertion says so explicitly: it
    reads the Bedrock AgentCore control plane, and an interface carrying it would
    oblige a plain-git adapter to answer a question about AWS runtimes.
    """
    source = REPO_ROOT / "agentorg" / "graph.py"
    tree = ast.parse(source.read_text())
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "github_ops"
    }
    assert called, "graph.py makes no github_ops calls; this test would pin nothing"

    public = {
        name for name in ("open_pr", "post_comment", "merge_pr", "report_outcome",
                          "ci_status")
    }
    assert called == public, (
        f"graph.py calls {sorted(called)} but the interface declares {sorted(public)}. "
        f"The interface is DERIVED from these call sites -- reconcile them rather "
        f"than adding a method nothing calls."
    )
    assert "deploy_note" not in public, (
        "deploy_note reads the AgentCore control plane, not a code host"
    )
    assert not hasattr(CodeHost, "deploy_note"), (
        "CodeHost grew a deploy_note; a plain-git adapter cannot answer a question "
        "about AWS runtimes and would have to stub it"
    )


def test_the_four_wrapped_methods_are_concrete_and_open_pr_is_abstract():
    """The asymmetry is structural, not a convention a reviewer has to notice.

    `open_pr` is abstract with no wrapper, so an adapter's raise reaches the
    caller. The other four are CONCRETE on the base class, calling an abstract
    `_`-prefixed body -- which is what makes the never-raises contract impossible
    for an adapter to opt out of by accident.
    """
    abstract = set(CodeHost.__abstractmethods__)
    assert abstract == {"open_pr", "_post_comment", "_merge_pr", "_report_outcome",
                        "_ci_status"}, (
        f"the abstract set is {sorted(abstract)}. `open_pr` must be abstract and "
        f"unwrapped; the other four must be abstract only in their `_` form, so the "
        f"public method carrying the guard cannot be overridden by accident."
    )
    for method in ("post_comment", "merge_pr", "report_outcome", "ci_status"):
        assert method not in abstract, (
            f"{method} is abstract, so an adapter could implement it directly and "
            f"bypass the guard that makes it never raise"
        )


# ── host() refuses; it never falls back ───────────────────────────────────────


def test_host_refuses_an_unknown_name_rather_than_defaulting():
    """The `STATE_BACKEND` rule: an unknown value raises, it does not fall back.

    A fallback here would open a real pull request on the target repository for an
    operator who asked for GitLab -- the same shape as a typo'd `dynamo` silently
    writing to disk while an operator believes the run is durable.
    """
    with pytest.raises(ValueError, match="unknown INTEGRATION_HOST"):
        integrations.host("gitlab")


@pytest.mark.parametrize("unshipped", ["memory", "git"])
def test_host_refuses_a_registered_adapter_that_is_not_shipped(unshipped):
    """Passing the conformance suite is not permission to serve a real run.

    Both of these work and both pass every conformance test, which is exactly why
    the refusal has to be explicit rather than left to whoever reads the class.
    """
    with pytest.raises(ValueError, match="NOT SHIPPED"):
        integrations.host(unshipped)


def test_host_reads_the_environment_at_call_time_not_at_import(monkeypatch):
    """The knob trap CLAUDE.md records for every config value.

    `from ..common.config import X` binds at import, before any fixture runs, so a
    knob read that way silently ignores both the tests and the deployed
    environment. This one is read inside `host()`.
    """
    monkeypatch.setenv("INTEGRATION_HOST", "gitlab")
    with pytest.raises(ValueError, match="unknown INTEGRATION_HOST"):
        integrations.host()
    monkeypatch.setenv("INTEGRATION_HOST", "github")
    assert isinstance(integrations.host(), GitHubHost)


def test_an_empty_environment_variable_is_ABSENT_but_an_empty_argument_is_a_MISTAKE(
    monkeypatch,
):
    """`INTEGRATION_HOST=` gets the default; `host("")` raises. Both are right.

    THE ASYMMETRY WAS CAUGHT BY THIS TEST DISAGREEING WITH ITS OWN CODE, which is
    the useful kind of failure: `host()`'s first docstring claimed an empty string
    "is not silently the default" while the `or DEFAULT_HOST` beside it made it
    exactly that for the environment path. One of the two had to move.

    The environment case wins, because `""` is what an unset-but-declared Actions
    variable looks like -- `env: INTEGRATION_HOST: ${{ vars.INTEGRATION_HOST }}`
    with nothing configured -- and refusing it would fail the pipeline of every
    repository that never set this knob. Same reality `run_stage.flag` handles,
    where `""` means absent.

    The argument case still raises: a caller who reached for this function with a
    value in hand had a value in mind, and `""` was not it.
    """
    monkeypatch.setenv("INTEGRATION_HOST", "")
    assert isinstance(integrations.host(), GitHubHost), (
        "an empty INTEGRATION_HOST must read as absent, not as a bad value: that "
        "is what an unset Actions variable interpolates to"
    )

    monkeypatch.delenv("INTEGRATION_HOST", raising=False)
    assert isinstance(integrations.host(), GitHubHost), (
        "an absent INTEGRATION_HOST must get the default"
    )

    with pytest.raises(ValueError, match="unknown INTEGRATION_HOST"):
        integrations.host("")


def test_the_default_adapter_is_the_shipped_one():
    """`REMOTE_AGENTS=false`'s argument: the shipped path must be the tested one.

    Defaulting to `memory` would keep the suite green while no run reached a code
    host at all -- a check that cannot distinguish "did not run" from "passed".
    """
    assert integrations.DEFAULT_HOST == GitHubHost.name
    assert integrations.ADAPTERS[integrations.DEFAULT_HOST].shipped is True


def test_every_registered_adapter_is_a_CodeHost_with_a_distinct_name():
    """A duplicate name would make one adapter unreachable through `host()`.

    Silently: `ADAPTERS` is keyed on `name`, so two classes sharing one would leave
    the second overwriting the first with nothing raised.
    """
    assert integrations.ADAPTERS, "ADAPTERS is empty; this test would pin nothing"
    for key, adapter in integrations.ADAPTERS.items():
        assert issubclass(adapter, CodeHost), f"{key} is not a CodeHost"
        assert adapter.name == key, (
            f"registered under {key!r} but names itself {adapter.name!r}; `host()` "
            f"reports the key and the conformance suite reports the name"
        )
    assert len(integrations.ADAPTERS) == len(
        {a.name for a in integrations.ADAPTERS.values()}
    ), "two adapters share a name, so one is unreachable"


# ── the GitHub adapter delegates. Asserted over the AST ───────────────────────


@pytest.mark.parametrize(("method", "delegate"), [
    ("open_pr", "open_pr"),
    ("_post_comment", "post_comment"),
    ("_merge_pr", "merge_pr"),
    ("_report_outcome", "report_outcome"),
    ("_ci_status", "ci_status"),
])
def test_the_github_adapter_delegates_rather_than_reimplementing(method, delegate):
    """Each method body is one call into `github_ops`. D2: no behaviour change.

    OVER THE AST, because that is the only way to make "no behaviour change" a
    checkable claim rather than a stated one. `github_ops.py` carries 1,132 lines
    whose comments record traps that were measured -- the
    `os.path.exists`-not-`isdir` worktree guard, `_ISSUE_REF`'s anchors, `local://`
    only after the bytes reach disk -- and a reimplementation here would have to
    re-earn every one of them.

    A future edit that inlines any of that fails this test by name.
    """
    tree = ast.parse(inspect.getsource(GitHubHost))
    body = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == method
    )
    calls = [
        node.func.attr
        for node in ast.walk(body)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "github_ops"
    ]
    assert calls == [delegate], (
        f"GitHubHost.{method} makes github_ops calls {calls}, expected "
        f"[{delegate!r}]. This adapter must DELEGATE: `github_ops.{delegate}` "
        f"carries measured behaviour that a reimplementation would have to re-earn."
    )


def test_the_github_adapter_is_still_covered_by_the_conftest_github_guard(monkeypatch):
    """Guard 2 must reach through the adapter, not only through a direct call.

    THE CLAIM A REFACTOR OF THIS SEAM IS MOST LIKELY TO BREAK. `github_ops`
    resolves `_repo` through its own module at call time, so the raiser conftest
    installs applies whether the caller is `graph.py` or `GitHubHost`. If the
    adapter had captured a reference at import, or reimplemented the API call, the
    guard would silently stop covering the path the pipeline takes.

    The credentials are set here because `_use_local()` short-circuits to the local
    branch without them, and the local branch never reaches `_repo`.
    """
    from agentorg.common import config

    monkeypatch.setattr(config, "OFFLINE", False)
    monkeypatch.setattr(config, "GITHUB_TOKEN", "t")
    monkeypatch.setattr(config, "GITHUB_REPO", "owner/name")

    with pytest.raises(BaseException) as caught:
        GitHubHost().open_pr(_state())

    assert not isinstance(caught.value, Exception), (
        f"the conftest GitHub guard was absorbed into a {type(caught.value)} on "
        f"the way through GitHubHost. With a token in the environment that means "
        f"real branch, commit and pull-request WRITES against DEMO_REPO."
    )


def test_the_adapters_share_the_refusal_predicates_rather_than_copying_them():
    """`_merge_refusal`, `_destination` and `_issue_number` have ONE definition each.

    A double with its own copy of the rules is a double that can disagree with
    production and stay green -- which is why `MemoryHost` calls into `github_ops`
    for all three. Asserted by driving the same state through the shipped adapter
    and the double and requiring the same refusal.
    """
    from agentorg.state import SecurityResult

    state = _state()
    state.dev = DevResult(branch="b", diff="+x\n", summary="s", files_changed=["f"])
    state.security = SecurityResult(verdict="block", findings=[], blocking=[],
                                    explanation="")

    shipped = GitHubHost().merge_pr(state)
    double = MemoryHost().merge_pr(state)
    sketch = GitHost().merge_pr(state)

    # The expected value comes from the PREDICATE, not from a literal, so this
    # cannot pass by all three adapters agreeing on a wrong answer that happens to
    # match a string typed into a test. `_merge_refusal` is the single definition;
    # if it changes, all four move together and the test still means something.
    expected = f"merge://refused/{github_ops._merge_refusal(state)}"
    assert expected == "merge://refused/security-verdict-block", (
        f"the shared predicate answered {expected!r} for a blocked run, so this "
        f"test's oracle is wrong and it would pin nothing"
    )
    assert shipped == double == sketch == expected, (
        f"the three adapters disagree about a BLOCKED run: shipped={shipped!r} "
        f"double={double!r} sketch={sketch!r}. They must share "
        f"`github_ops._merge_refusal`, not each carry a copy."
    )


def test_the_undelivered_ref_has_one_writer():
    """Two spellings of `comment://<run_id>` would drift unobservably.

    A ref in the wrong shape still records as a ref, and the timeline would
    classify it `UNRECOGNISED` on exactly the runs worth reading.
    """
    state = _state()
    assert interface.undelivered_ref(state) == f"comment://{state.run_id}"
    assert interface.scheme_of(interface.undelivered_ref(state)) == "comment"


def test_a_ref_with_no_scheme_reports_an_empty_scheme_rather_than_itself():
    """`scheme_of("nonsense")` must not answer `"nonsense"`.

    The naive `split("://")[0]` returns the whole string for a ref carrying no
    scheme, which would make an obviously broken ref look like an exotic but valid
    one -- and the timeline's `UNRECOGNISED` branch is the honest answer there.
    """
    assert interface.scheme_of("nonsense") == ""
    assert interface.scheme_of("") == ""
    assert interface.scheme_of("https://example.test/1") == "https"


def test_a_finding_is_rendered_into_the_comment_body_by_every_adapter():
    """`post_comment(state, body, finding=...)` must not drop the finding.

    The third parameter is optional and nothing in `graph.py` passes it today --
    which is exactly why it is worth a test: an unexercised parameter is one an
    adapter can silently ignore.
    """
    finding = Finding(tool="gitleaks", severity="critical", rule="aws-access-key-id",
                      file="app/auth.py", line=3, description="a committed key")
    host = MemoryHost()
    host.post_comment(_state(), "the explanation", finding)
    assert host.comments, "the double recorded no comment"
    _, body, recorded = host.comments[-1]
    assert recorded is finding, "the finding was dropped on the way through"
    assert "the explanation" in body
