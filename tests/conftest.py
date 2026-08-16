"""Suite-wide defaults. Owner: Sorour.

The suite is hermetic: no test reaches a live model unless it asks to.

`llm.available()` returns True on any machine with AWS credentials, and each
agent calls the model whenever it does. Without the fixture below, `pytest -q`
on a developer laptop makes a real Bedrock call for every agent in every
pipeline test -- slow, billable, and dependent on the network being up. CI never
caught it, because CI has no credentials and so silently took the fixture path.

A test that wants the model path opts in, in its own body:

    monkeypatch.setattr(config, "LLM_DISABLED", False)
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "_complete", lambda s, u: '{...}')

Fixtures finish before the test body starts, so those three lines override this
default rather than fighting it. Patch `available` as well as `LLM_DISABLED`:
`available()` is the only reader of `LLM_DISABLED`, and patching it wholesale is
what makes an opt-in test behave the same with or without credentials present.

Do not delete this fixture. Nothing turns red if you do -- the suite just goes
back to billing the team on every run.
"""

import pytest

from agentorg.common import config


@pytest.fixture(autouse=True)
def _model_disabled_by_default(monkeypatch):
    """Start every test with the model switched off. Opt in to override."""
    monkeypatch.setattr(config, "LLM_DISABLED", True)
