"""Suite-wide defaults. Owner: Sorour.

The suite is hermetic on THREE external seams: no test reaches a live model, no
test reaches the GitHub API, and no test reaches the terminal, unless it asks
to. All three guards have the same shape -- force the offline path, then put a
loud raiser on the seam underneath it -- and all three exist because the failure
they prevent is silent, expensive, or both.

=========================================================================
SEAM 1 -- the model (Bedrock)
=========================================================================

`llm.available()` returns True on any machine with AWS credentials, and each
agent calls the model whenever it does. Without the fixture below, `pytest -q`
on a developer laptop makes a real Bedrock call for every agent in every
pipeline test -- slow, billable, and dependent on the network being up. CI never
caught it, because CI has no credentials and so silently took the fixture path.

A test that wants the model path opts in, in its own body, with all THREE lines:

    monkeypatch.setattr(config, "LLM_DISABLED", False)
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "_complete", lambda s, u: '{...}')

Fixtures finish before the test body starts, so those lines override the
defaults below rather than fighting them.

Two failure modes are covered, because both are silent and both cost money:

1. Forgetting to opt in at all -- `LLM_DISABLED` is True, so `available()` is
   False and no call is attempted.
2. Opting in with only the first two lines -- `available()` is now True, so the
   call proceeds and reaches the real `_complete`, which is a live billable
   request. `llm.text()` would absorb the resulting failure and hand back the
   fixture, leaving the test green and the charge invisible. So `_complete` is
   replaced with a raiser that fails the test by name instead.

The raiser uses `pytest.fail`, whose `Failed` derives from BaseException rather
than Exception. That is deliberate and load-bearing: `llm.text()` catches
`Exception`, so an ordinary raiser would be swallowed into the fixture branch
and the test would pass green -- exactly the bug this is meant to catch. The
Exception/BaseException split is itself pinned by
`test_keyboard_interrupt_is_not_swallowed` in test_llm_helper.py.

Note for anyone writing an agent: this works because `llm.py` reads
`config.LLM_DISABLED` as a module attribute at call time. An agent written as
`from ..common.config import LLM_DISABLED` would bind the value at import,
before any fixture runs, and silently fall outside this fixture's reach. Always
read it through the module: `config.LLM_DISABLED`.

Do not delete this fixture. Nothing turns red if you do -- the suite just goes
back to billing the team on every run.

=========================================================================
SEAM 2 -- GitHub (branch, commit, pull request)
=========================================================================

Same bug, different seam, and this one WRITES. `github_ops._use_local()` asks
only whether credentials exist -- `config.OFFLINE or not (GITHUB_TOKEN and
GITHUB_REPO)` -- never whether we are under test. So on any machine where a
token is exported, every test that drives `run_pipeline` walks into the online
branch of `open_pr` and performs real `create_git_ref` / `create_file` /
`create_pull` calls against DEMO_REPO. Not reads: writes, to a real repository,
several times per `pytest -q`.

Measured before this fixture existed, with a token in the environment and a
socket-level recorder: FOUR outbound connections to api.github.com per run --
three from tests/test_pipeline_smoke.py and one from
test_the_revision_loop_terminates. CI never caught it for exactly the reason CI
never caught the Bedrock bug: CI has no token, so `_use_local()` was True there
and the online branch was silently skipped.

The guard mirrors seam 1 deliberately, so there is one pattern to learn:

  * `config.OFFLINE = True` is the policy layer, the counterpart of
    `LLM_DISABLED`. `_use_local()` is its only reader and reads it through the
    module at call time, so the fixture reaches it whatever the credentials say.
  * `github_ops._repo` is the seam layer, the counterpart of `llm._complete`.
    It is the single point where a `Github` client is constructed, and both
    call sites (`open_pr`, `post_comment`) invoke it OUTSIDE their try blocks,
    so a raiser there escapes rather than being folded into a `GithubException`
    branch. It uses `pytest.fail` for the same BaseException reason as above.

A test that genuinely wants the online path opts in, in its own body. The
`_repo` line is the load-bearing one -- it is what keeps the test off the
network -- and the other three only matter on a machine whose environment does
not already supply them:

    monkeypatch.setattr(config, "OFFLINE", False)
    monkeypatch.setattr(config, "GITHUB_TOKEN", "t")
    monkeypatch.setattr(config, "GITHUB_REPO", "owner/name")
    monkeypatch.setattr(github_ops, "_repo", lambda: <a fake repo object>)

Both credentials are needed to leave local mode, since `_use_local()` reads
`not (GITHUB_TOKEN and GITHUB_REPO)`. Opting in WITHOUT the `_repo` line
reproduces the exact bug this fixture closes, so it fails the test by name
instead -- verified against both call sites, `open_pr` and `post_comment`.

Do not delete this fixture either. Nothing turns red if you do -- the suite
just goes back to opening pull requests on a real repository.

=========================================================================
SEAM 3 -- the terminal (stdin)
=========================================================================

Same shape again, and this one does not cost money -- it costs the whole run.
`graph._cli_gate` calls `input()` three times per pipeline, once per human gate.
A test that drives `run_pipeline(auto_approve=False)` without replacing `input`
therefore reads stdin. Under pytest's default capture that raises OSError, which
is survivable; under `pytest -s`, which is what anyone debugging a gate actually
types, it BLOCKS on the terminal forever. A hung suite reports no failing test
and names no file -- in CI it is a job timeout with nothing to read.

So the default is a raiser, and a test that wants the interactive path opts in
in its own body, exactly as with the other two seams:

    monkeypatch.setattr(builtins, "input", <a finite script of answers>)

FINITE is the load-bearing word. `lambda *a: "a"` answers every prompt forever,
so a gate that starts asking twice, or a fourth gate nobody meant to add, is
answered silently rather than caught -- the test passes while pinning less than
it claims. Scripts in tests/test_gates_cli.py hand back a fixed list of answers
and raise when it runs out, so "asked more often than expected" fails by name
instead of looping. Between the two, no code path can block the suite on stdin:
unpatched reads fail here, over-eager reads fail there.

One consequence worth knowing: `pytest --pdb` also wants stdin, so drop this
fixture for the run if you need the debugger inside a gate test.
"""

import builtins
from typing import NoReturn

import pytest

from agentorg import github_ops
from agentorg.common import config, llm


def _unpatched_complete(system_prompt: str, user_prompt: str) -> NoReturn:
    """Stand-in for the real model seam. Fails the test that reached it."""
    pytest.fail(
        "This test reached the real llm._complete, which on a machine with AWS "
        "credentials is a live, billable Bedrock call. It patched "
        "config.LLM_DISABLED and/or llm.available but NOT llm._complete. A test "
        "that wants the model path must patch all three -- see tests/conftest.py.",
        pytrace=False,
    )


@pytest.fixture(autouse=True)
def _model_disabled_by_default(monkeypatch):
    """Start every test with the model off and the network seam blocked."""
    monkeypatch.setattr(config, "LLM_DISABLED", True)
    monkeypatch.setattr(llm, "_complete", _unpatched_complete)


def _unpatched_repo() -> NoReturn:
    """Stand-in for the GitHub API seam. Fails the test that reached it."""
    pytest.fail(
        "This test reached the real github_ops._repo, which with a token in "
        "the environment performs live branch, commit and pull-request WRITES "
        "against DEMO_REPO. It patched config.OFFLINE and/or the credentials "
        "but NOT github_ops._repo. A test that wants the online path must "
        "replace all three -- see tests/conftest.py.",
        pytrace=False,
    )


@pytest.fixture(autouse=True)
def _github_offline_by_default(monkeypatch):
    """Start every test in local mode with the GitHub API seam blocked."""
    monkeypatch.setattr(config, "OFFLINE", True)
    monkeypatch.setattr(github_ops, "_repo", _unpatched_repo)


def _unpatched_input(prompt: str = "") -> NoReturn:
    """Stand-in for the terminal. Fails the test that reached it."""
    pytest.fail(
        f"This test reached the real builtins.input (prompt: {prompt!r}), which "
        "reads stdin. Under `pytest -s` that blocks the whole suite with no "
        "failing test to point at. A test that wants the interactive gate must "
        "replace builtins.input with a finite script of answers -- see "
        "tests/conftest.py.",
        pytrace=False,
    )


@pytest.fixture(autouse=True)
def _terminal_blocked_by_default(monkeypatch):
    """Start every test with the terminal blocked, so nothing can hang."""
    monkeypatch.setattr(builtins, "input", _unpatched_input)
