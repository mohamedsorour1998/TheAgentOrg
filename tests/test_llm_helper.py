"""Unit tests for the shared LLM helper. Owner: Sorour.

Every test that drives the model path patches BOTH `llm.available` and
`llm._complete`. Patching only `_complete` makes the test depend on whether
the machine happens to have AWS credentials: `text()` short-circuits on
`available()` long before `_complete` is reached, so the test is green on a
laptop with ~/.aws/credentials and red on a CI runner without one. Tasks 3-6
patch the same pair for the same reason.
"""

import pytest

from agentorg.common import config, llm
from agentorg.state import PlanResult


def test_extract_json_from_fenced_block():
    raw = 'Sure!\n```json\n{"a": 1}\n```\nHope that helps.'
    assert llm.extract_json(raw) == '{"a": 1}'


def test_extract_json_from_unlabelled_fence():
    raw = '```\n{"a": 1}\n```'
    assert llm.extract_json(raw) == '{"a": 1}'


def test_extract_json_from_chatty_reply():
    raw = 'Here you go: {"a": 1} — let me know.'
    assert llm.extract_json(raw) == '{"a": 1}'


def test_extract_json_passes_through_bare_json():
    assert llm.extract_json('{"a": 1}') == '{"a": 1}'


def test_extract_json_from_a_fence_tagged_with_another_language():
    # Models fence JSON as ```python more often than you would like. Matching
    # only ```json returns 'python\n{...}', which never parses, and the agent
    # shows fixture data with nothing in the log to explain it.
    raw = '```python\n{"a": 1}\n```'
    assert llm.extract_json(raw) == '{"a": 1}'


def test_extract_json_from_an_uppercase_json_fence():
    raw = '```JSON\n{"a": 1}\n```'
    assert llm.extract_json(raw) == '{"a": 1}'


def test_extract_json_falls_through_a_fence_that_holds_no_object():
    # The first fence is a code block and the JSON follows it. Trusting the
    # first fence unconditionally returns the code, never parses, and puts
    # fixture data on screen while the run looks live.
    raw = 'Here is the code:\n```python\nx = 1\n```\nAnd the plan: {"a": 1}'
    assert llm.extract_json(raw) == '{"a": 1}'


def test_extract_json_from_an_unclosed_fence():
    # No closing fence, so the regex cannot match and the brace scan carries it.
    raw = '```json\n{"a": 1}'
    assert llm.extract_json(raw) == '{"a": 1}'


def test_available_is_false_when_disabled(monkeypatch):
    monkeypatch.setattr(llm.config, "LLM_DISABLED", True)
    assert llm.available() is False


def test_structured_returns_none_when_disabled(monkeypatch):
    monkeypatch.setattr(llm.config, "LLM_DISABLED", True)
    assert llm.structured(PlanResult, "sys", "user") is None


def test_text_returns_none_when_disabled(monkeypatch):
    monkeypatch.setattr(llm.config, "LLM_DISABLED", True)
    assert llm.text("sys", "user") is None


def test_structured_returns_none_on_unparseable_reply(monkeypatch):
    monkeypatch.setattr(llm.config, "LLM_DISABLED", False)
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "_complete", lambda s, u: "not json at all")
    assert llm.structured(PlanResult, "sys", "user") is None


def test_structured_parses_a_valid_reply(monkeypatch):
    monkeypatch.setattr(llm.config, "LLM_DISABLED", False)
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "_complete", lambda s, u: (
        '```json\n{"tasks": ["t"], "acceptance_criteria": ["c"], '
        '"target_files": ["f"], "notes": ""}\n```'
    ))
    result = llm.structured(PlanResult, "sys", "user")
    assert isinstance(result, PlanResult)
    assert result.tasks == ["t"]


def test_structured_returns_none_when_the_model_raises(monkeypatch):
    def boom(system_prompt, user_prompt):
        raise RuntimeError("bedrock exploded")

    monkeypatch.setattr(llm.config, "LLM_DISABLED", False)
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "_complete", boom)
    assert llm.structured(PlanResult, "sys", "user") is None


def test_structured_parses_a_reply_fenced_as_python(monkeypatch):
    # End-to-end version of the fence-tag case: the whole point is that this
    # reply reaches the agent as a PlanResult instead of silent fixture data.
    monkeypatch.setattr(llm.config, "LLM_DISABLED", False)
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "_complete", lambda s, u: (
        '```python\n{"tasks": ["t"], "acceptance_criteria": ["c"], '
        '"target_files": ["f"], "notes": ""}\n```'
    ))
    result = llm.structured(PlanResult, "sys", "user")
    assert isinstance(result, PlanResult)
    assert result.tasks == ["t"]


def test_text_returns_none_when_the_model_returns_none(monkeypatch):
    # Patching `_complete` to return None is the natural way to simulate "the
    # model gave nothing". It must degrade, not raise AttributeError.
    monkeypatch.setattr(llm.config, "LLM_DISABLED", False)
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "_complete", lambda s, u: None)
    assert llm.text("sys", "user") is None


def test_text_returns_none_when_the_model_returns_a_non_string(monkeypatch):
    monkeypatch.setattr(llm.config, "LLM_DISABLED", False)
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "_complete", lambda s, u: 42)
    assert llm.text("sys", "user") is None


def test_structured_returns_none_when_the_model_returns_a_non_string(monkeypatch):
    monkeypatch.setattr(llm.config, "LLM_DISABLED", False)
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "_complete", lambda s, u: None)
    assert llm.structured(PlanResult, "sys", "user") is None


def test_structured_returns_none_for_an_empty_fence(monkeypatch):
    # A fence with nothing in it must still degrade, not become a parse attempt
    # on the raw reply that somehow succeeds.
    monkeypatch.setattr(llm.config, "LLM_DISABLED", False)
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "_complete", lambda s, u: "```\n\n```")
    assert llm.structured(PlanResult, "sys", "user") is None


def test_keyboard_interrupt_is_not_swallowed(monkeypatch):
    # The fallback rule covers Exception, never BaseException: a Ctrl-C or a
    # SystemExit must still stop the run. Pinned because widening the catch to
    # BaseException is an easy and very costly "fix".
    def interrupt(system_prompt, user_prompt):
        raise KeyboardInterrupt

    monkeypatch.setattr(llm.config, "LLM_DISABLED", False)
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "_complete", interrupt)
    with pytest.raises(KeyboardInterrupt):
        llm.structured(PlanResult, "sys", "user")


# ── A LOCAL GATEWAY NEEDS NO KEY — the config that silently served fixtures ────
#
# MEASURED 2026-08-28 by Lane F, trying to reproduce the documented self-hosted path on
# a clean host. With only `LLM_BASE_URL=http://127.0.0.1:11434/v1` set, `available()`
# returned False, so every agent served its fixture and the whole run was green:
#
#     LLM_BASE_URL   = 'http://127.0.0.1:11434/v1'
#     LLM_API_KEY    = 'not-needed'      <- the default
#     available()    = False
#
# A local gateway authenticates nobody, so it ignores the key and nothing complains.
# The naive self-hosted configuration was a fixture run wearing a model run's output --
# this repository's signature defect, in the one place the whole self-hosted claim rests.
#
# The brief given to that lane asserted the opposite ("`not-needed` is what a local
# gateway wants"), which is why the lane needed its own `--require-model` guard to catch
# it. That guard is what a test should have been.

@pytest.mark.parametrize("base_url", [
    "http://127.0.0.1:11434/v1",
    "http://localhost:11434/v1",
    "http://[::1]:8000/v1",
])
def test_a_loopback_gateway_is_available_without_a_key(base_url, monkeypatch):
    """The self-hosted path must work with the default key. This is F1's premise."""
    monkeypatch.setattr(config, "LLM_DISABLED", False)
    monkeypatch.setattr(config, "LLM_BASE_URL", base_url)
    monkeypatch.setattr(config, "LLM_API_KEY", "not-needed")

    assert llm.available() is True, (
        f"{base_url} with the default key reports unavailable, so every agent serves "
        f"a fixture and the self-hosted run is green while calling no model"
    )


def test_a_remote_gateway_without_a_key_refuses_AND_SAYS_SO(monkeypatch, caplog):
    """Still refused — but the WARNING is the load-bearing half.

    The refusal was always correct for a remote gateway: `not-needed` there means
    nobody configured it. What was missing is that it happened in silence, so a run
    that called no model was indistinguishable from one that did. The previous version
    returned False with no log line at all.
    """
    monkeypatch.setattr(config, "LLM_DISABLED", False)
    monkeypatch.setattr(config, "LLM_BASE_URL", "https://gateway.example.com/v1")
    monkeypatch.setattr(config, "LLM_API_KEY", "not-needed")

    with caplog.at_level("WARNING"):
        assert llm.available() is False

    assert caplog.records, (
        "a remote gateway with no key refused SILENTLY. That silence is the defect: "
        "the run then serves fixtures everywhere and reports success"
    )
    assert "LLM_API_KEY" in caplog.text


def test_a_hostname_that_merely_CONTAINS_the_loopback_literal_is_remote(monkeypatch):
    """`http://127.0.0.1.evil.com/` is a remote host, and a substring check ships a key.

    The loopback test parses the URL rather than matching a pattern. Without that, an
    operator's key would be sent to somebody else's machine while the code reported the
    model as locally served — and the run would look completely normal.
    """
    monkeypatch.setattr(config, "LLM_DISABLED", False)
    monkeypatch.setattr(config, "LLM_BASE_URL", "http://127.0.0.1.evil.com/v1")
    monkeypatch.setattr(config, "LLM_API_KEY", "not-needed")

    assert llm.available() is False, (
        "a host merely containing '127.0.0.1' was treated as loopback; a credential-"
        "free request would go to a remote machine believing it stayed local"
    )


@pytest.mark.parametrize("host", ["10.0.0.5", "192.168.1.20", "172.16.0.9"])
def test_a_private_but_not_local_address_still_needs_a_key(host, monkeypatch):
    """Private is not local. A gateway on the LAN is somebody else's machine.

    Deliberately narrow: widening the loopback set to "any private address" is how a
    key-free request reaches a host the operator did not think about. Pinned per range
    so a future "convenience" widening fails here rather than in an incident.
    """
    monkeypatch.setattr(config, "LLM_DISABLED", False)
    monkeypatch.setattr(config, "LLM_BASE_URL", f"http://{host}:11434/v1")
    monkeypatch.setattr(config, "LLM_API_KEY", "not-needed")

    assert llm.available() is False, (
        f"{host} was treated as loopback. It is private but not local -- the request "
        f"crosses a wire, so it needs a key like any other remote gateway"
    )


def test_LLM_DISABLED_still_wins_over_a_loopback_gateway(monkeypatch):
    """The kill switch outranks the new branch, or conftest guard 1 stops working.

    Guard 1 sets `config.LLM_DISABLED = True` for the whole suite. If the loopback
    branch were checked first, a test that set a local base URL would make live calls
    with the guard in place and nothing would report it.
    """
    monkeypatch.setattr(config, "LLM_DISABLED", True)
    monkeypatch.setattr(config, "LLM_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setattr(config, "LLM_API_KEY", "not-needed")

    assert llm.available() is False, "LLM_DISABLED was overridden by the loopback branch"
