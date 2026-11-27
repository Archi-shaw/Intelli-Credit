"""LIVE provider integration tests — opt-in, real (billed) requests.

Run:  IC_LIVE_TESTS=1 ANTHROPIC_API_KEY=... VOYAGE_API_KEY=... TAVILY_API_KEY=... BRAVE_API_KEY=... SERPAPI_API_KEY=... \
      .venv/bin/python -m pytest -m live tests/test_live_providers.py -v
Each test is skipped (reported as "credentials missing") when its key is absent. Requests are minimal.
"""
from __future__ import annotations

import os

import pytest

from ai_engine.embeddings.providers import VoyageEmbedder, cosine
from ai_engine.llm.provider import AnthropicProvider, LLMError
from ai_engine.research.providers import BraveSearch, SerpAPISearch, TavilySearch

pytestmark = [pytest.mark.live, pytest.mark.skipif(os.environ.get("IC_LIVE_TESTS") != "1",
                                                   reason="live provider tests are opt-in (IC_LIVE_TESTS=1)")]
MODEL = os.environ.get("LLM_MODEL_LIVE", "claude-opus-5-5")


def key(*names: str) -> str:
    for n in names:
        if os.environ.get(n):
            return os.environ[n]
    pytest.skip(f"credentials missing: {' / '.join(names)}")


def test_claude_generate_and_structured_output():
    p = AnthropicProvider(key("ANTHROPIC_API_KEY", "LLM_API_KEY"), MODEL, "low", 60)
    out = p.generate(system="Reply with the single word OK.", messages=[{"role": "user", "content": "ping"}], max_tokens=200)
    assert out.text and out.model
    data, _ = p.generate_json(system="Answer as JSON.", messages=[{"role": "user", "content": "Is 2+2=4? Cite nothing."}],
                              schema={"type": "object", "properties": {"answer": {"type": "string"}, "ok": {"type": "boolean"}},
                                      "required": ["answer", "ok"], "additionalProperties": False}, max_tokens=500)
    assert isinstance(data["ok"], bool)


def test_claude_unknown_model_is_reported():
    with pytest.raises(LLMError, match="not found"):
        AnthropicProvider(key("ANTHROPIC_API_KEY", "LLM_API_KEY"), "claude-model-that-does-not-exist", "low", 30).generate(
            system="x", messages=[{"role": "user", "content": "x"}])


# Invalid-credential handling needs no real key: each service rejects a deliberately invalid key (not billed).
INVALID = "invalid-key-for-intellicredit-auth-test-0000"


def test_live_invalid_credentials_claude():
    with pytest.raises(LLMError, match="authentication failed"):
        AnthropicProvider("sk-ant-" + INVALID, MODEL, "low", 30).generate(system="x", messages=[{"role": "user", "content": "x"}])


def test_live_invalid_credentials_voyage():
    from ai_engine.embeddings.providers import EmbeddingError

    with pytest.raises(EmbeddingError, match="rejected the API key"):
        VoyageEmbedder("pa-" + INVALID).embed(["x"])


@pytest.mark.parametrize("cls", [TavilySearch, BraveSearch, SerpAPISearch])
def test_live_invalid_credentials_search(cls):
    from ai_engine.research.providers import SearchError

    with pytest.raises(SearchError) as ei:
        cls(INVALID).search("test", max_results=1)
    assert "rejected the API key" in str(ei.value) or "serpapi error" in str(ei.value), str(ei.value)


def test_voyage_embeddings_dimensions_and_retrieval_consistency():
    emb = VoyageEmbedder(key("VOYAGE_API_KEY", "EMBEDDING_API_KEY"), os.environ.get("EMBEDDING_MODEL_LIVE", "voyage-3.5"))
    docs = emb.embed(["The borrower's revenue from operations was 24.6 crore in FY2023-24.",
                      "The factory is located in Pune and employs 140 people."], input_type="document")
    q = emb.embed(["What was the revenue?"], input_type="query")[0]
    assert len({len(v) for v in docs + [q]}) == 1
    assert cosine(q, docs[0]) > cosine(q, docs[1])


@pytest.mark.parametrize("cls,envs", [(TavilySearch, ("TAVILY_API_KEY",)), (BraveSearch, ("BRAVE_API_KEY",)), (SerpAPISearch, ("SERPAPI_API_KEY",))])
def test_search_provider_returns_sourced_results(cls, envs):
    results = cls(key(*envs)).search("Reserve Bank of India MSME credit guarantee", max_results=3)
    assert results and all(r.url.startswith("http") and r.title for r in results)
