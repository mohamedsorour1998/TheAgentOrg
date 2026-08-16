"""Suite-wide defaults. Owner: Sorour.

The suite is hermetic: no test reaches a live model unless it asks to.

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
"""

from typing import NoReturn

import pytest

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
