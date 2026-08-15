"""Unit tests for the shared LLM helper. Owner: Sorour.

Every test that drives the model path patches BOTH `llm.available` and
`llm._complete`. Patching only `_complete` makes the test depend on whether
the machine happens to have AWS credentials: `text()` short-circuits on
`available()` long before `_complete` is reached, so the test is green on a
laptop with ~/.aws/credentials and red on a CI runner without one. Tasks 3-6
patch the same pair for the same reason.
"""

from agentorg.common import llm
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
