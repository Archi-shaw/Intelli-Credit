"""Provider integrations without network access: request shape, error mapping and graceful fallback.

No real credentials are used. The Anthropic SDK client is replaced by a stub so the exact request the
provider would send is asserted; the assistant's LLM branch is exercised with a fake provider.
"""
from __future__ import annotations

from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from ai_engine.llm.provider import AnthropicProvider, LLMError, LLMResult, NullProvider, get_llm
from ai_engine.research.providers import SearchNotConfigured, get_search_provider
from tests.conftest import make_assessment, make_borrower, upload


class _StubMessages:
    def __init__(self, response=None, exc=None):
        self.calls, self.response, self.exc = [], response, exc

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc:
            raise self.exc
        return self.response


def _provider(stub: _StubMessages) -> AnthropicProvider:
    p = AnthropicProvider(api_key="test-key-not-real", model="claude-opus-5-5", effort="medium", timeout=5)
    p._client = SimpleNamespace(beta=SimpleNamespace(messages=stub))
    return p


def _response(text="Revenue was ₹9.85 crore [S1].", stop="end_turn"):
    return SimpleNamespace(content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
                           stop_reason=stop, model="claude-opus-5-5")


def test_anthropic_request_shape_uses_effort_and_server_side_fallback():
    stub = _StubMessages(response=_response())
    out = _provider(stub).generate(system="sys", messages=[{"role": "user", "content": "q"}], max_tokens=500)
    assert out == LLMResult(text="Revenue was ₹9.85 crore [S1].", model="claude-opus-5-5", stop_reason="end_turn", provider="anthropic")
    call = stub.calls[0]
    assert call["model"] == "claude-opus-5-5" and call["output_config"] == {"effort": "medium"}
    assert call["betas"] == ["server-side-fallback-2026-07-01"] and call["fallbacks"] == "default"
    assert "thinking" not in call and "temperature" not in call  # not accepted by this model


def _status_error(cls, status):
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("error", response=httpx2.Response(status, request=req), body=None)


@pytest.mark.parametrize("cls,status,needle,retryable", [
    (anthropic.AuthenticationError, 401, "authentication failed", False),
    (anthropic.NotFoundError, 404, "not found", False),
    (anthropic.RateLimitError, 429, "rate limit", True),
    (anthropic.InternalServerError, 500, "service error", True),
])
def test_anthropic_errors_are_mapped_to_clear_messages(cls, status, needle, retryable):
    with pytest.raises(LLMError) as ei:
        _provider(_StubMessages(exc=_status_error(cls, status))).generate(system="s", messages=[{"role": "user", "content": "q"}])
    assert needle in str(ei.value).lower() and ei.value.retryable == retryable


def test_refusal_and_empty_responses_are_errors():
    with pytest.raises(LLMError, match="declined"):
        _provider(_StubMessages(response=_response(stop="refusal"))).generate(system="s", messages=[{"role": "user", "content": "q"}])
    with pytest.raises(LLMError, match="empty"):
        _provider(_StubMessages(response=_response(text="  "))).generate(system="s", messages=[{"role": "user", "content": "q"}])


def test_unconfigured_providers_degrade_gracefully():
    assert not get_llm("none", "", "claude-opus-5-5").available
    assert isinstance(get_llm("none", "", "x"), NullProvider)
    with pytest.raises(SearchNotConfigured):
        get_search_provider("none", "").search("anything")
    with pytest.raises(SearchNotConfigured):
        get_search_provider("tavily", "").search("anything")


class FakeLLM:
    available = True
    name = "fake"

    def __init__(self, text):
        self.text, self.prompts = text, []

    def generate(self, *, system, messages, max_tokens=4000):
        self.prompts.append(messages[0]["content"])
        return LLMResult(text=self.text, model="fake-model", stop_reason="end_turn", provider="fake")


def test_assistant_llm_answers_keep_only_cited_sources(org_a, synthetic_files, monkeypatch):
    api = org_a["analyst"]
    a = make_assessment(api, make_borrower(api)["id"])
    upload(api, a["id"], synthetic_files["healthy"]["financial_statements"], "financial_statements")
    fake = FakeLLM("Revenue from operations for FY2023-24 was ₹24.60 crore [S1].")
    monkeypatch.setattr("app.services.assistant.llm", lambda: fake)
    ans = api.post(f"/api/v1/assessments/{a['id']}/chat", json={"question": "What was the revenue in FY 2023-24?"}).json()["answer"]
    assert ans["method"] == "llm" and ans["grounded"]
    assert [c["ref"] for c in ans["citations"]] == ["S1"] and ans["citations"][0]["page_number"] == 1
    assert "<sources>" in fake.prompts[0] and "untrusted" not in fake.prompts[0]  # system prompt carries the rule

    fake.text = "The company is doing great."  # uncited claim → flagged as not source-supported
    ans2 = api.post(f"/api/v1/assessments/{a['id']}/chat", json={"question": "What was the revenue in FY 2023-24?"}).json()["answer"]
    assert not ans2["grounded"] and ans2["citations"] == []


class FakeStructuredLLM(FakeLLM):
    supports_structured = True

    def __init__(self, data):
        super().__init__("")
        self.data = data

    def generate_json(self, *, system, messages, schema, max_tokens=4000):
        assert schema["required"] == ["answer", "cited_sources", "verifiable"] and "JSON" in system
        self.prompts.append(messages[0]["content"])
        return self.data, LLMResult(text="{}", model="fake-model", stop_reason="end_turn", provider="fake")


def test_assistant_structured_output_validates_citations(org_a, synthetic_files, monkeypatch):
    api = org_a["analyst"]
    a = make_assessment(api, make_borrower(api)["id"])
    upload(api, a["id"], synthetic_files["healthy"]["financial_statements"], "financial_statements")
    fake = FakeStructuredLLM({"answer": "Revenue was ₹24.60 crore [S1] (also see [S99]).", "cited_sources": ["S1", "S99"],
                              "verifiable": True})
    monkeypatch.setattr("app.services.assistant.llm", lambda: fake)
    ans = api.post(f"/api/v1/assessments/{a['id']}/chat", json={"question": "What was the revenue in FY 2023-24?"}).json()["answer"]
    assert ans["grounded"] and [c["ref"] for c in ans["citations"]] == ["S1"]  # invented S99 dropped

    fake.data = {"answer": "The documents do not say.", "cited_sources": [], "verifiable": False}
    unv = api.post(f"/api/v1/assessments/{a['id']}/chat", json={"question": "What was the revenue in FY 2023-24?"}).json()["answer"]
    assert not unv["grounded"] and unv["content"].startswith("This could not be verified")


def test_anthropic_structured_request_shape_and_timeout():
    stub = _StubMessages(response=_response(text='{"answer": "x", "cited_sources": [], "verifiable": false}'))
    data, _ = _provider(stub).generate_json(system="s", messages=[{"role": "user", "content": "q"}],
                                            schema={"type": "object"}, max_tokens=300)
    assert data["verifiable"] is False
    oc = stub.calls[0]["output_config"]
    assert oc == {"effort": "medium", "format": {"type": "json_schema", "schema": {"type": "object"}}}
    assert stub.calls[0]["fallbacks"] == "default"
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    with pytest.raises(LLMError, match="timed out") as ei:
        _provider(_StubMessages(exc=anthropic.APITimeoutError(request=req))).generate(system="s", messages=[{"role": "user", "content": "q"}])
    assert ei.value.retryable
