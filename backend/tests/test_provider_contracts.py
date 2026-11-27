"""Contract tests for external providers against recorded/mocked HTTP responses (no network, no credentials).

These verify request construction and response/error handling. They are NOT live integration tests and never
mark a provider as verified (see tests/test_live_providers.py for the opt-in live checks).
"""
from __future__ import annotations

import json

import httpx
import pytest

from ai_engine.embeddings.providers import EmbeddingError, VoyageEmbedder
from ai_engine.research.agent import run_research
from ai_engine.research.providers import (
    NOT_CONFIGURED,
    BraveSearch,
    NullSearch,
    SearchError,
    SearchNotConfigured,
    SerpAPISearch,
    TavilySearch,
)


def transport(responses: list, seen: list | None = None):
    """MockTransport returning the given responses in order (an Exception instance is raised instead)."""
    it = iter(responses)

    def handler(request: httpx.Request):
        if seen is not None:
            seen.append(request)
        r = next(it)
        if isinstance(r, Exception):
            raise r
        status, body, headers = (r + ({},))[:3] if isinstance(r, tuple) else (200, r, {})
        content = body if isinstance(body, (bytes, str)) else json.dumps(body)
        return httpx.Response(status, content=content, headers=headers)

    return httpx.Client(transport=httpx.MockTransport(handler))


TAVILY_OK = {"results": [
    {"title": "Synthetic Co wins order", "url": "https://www.business-standard.com/a", "content": "Order win…", "published_date": "2026-10-03"},
    {"title": "no url"}, "not-a-dict", {"title": "bad scheme", "url": "javascript:alert(1)"}]}


def test_tavily_parses_results_and_sends_auth_header():
    seen = []
    p = TavilySearch("tvly-test", transport([TAVILY_OK], seen))
    res = p.search("Synthetic Co", max_results=3)
    assert [r.url for r in res] == ["https://www.business-standard.com/a"]
    assert res[0].source_name == "business-standard.com" and str(res[0].published) == "2026-10-03"
    assert seen[0].headers["authorization"] == "Bearer tvly-test" and json.loads(seen[0].content)["max_results"] == 3


def test_brave_strips_markup_and_parses_age():
    body = {"web": {"results": [{"title": "<b>Synthetic</b> Co", "url": "https://example.in/x", "description": "<p>Text</p>",
                                 "page_age": "2026-09-01T10:00:00", "profile": {"name": "Example News"}}]}}
    seen = []
    res = BraveSearch("brave-key", transport([body], seen)).search("q")
    assert res[0].title == "Synthetic Co" and res[0].snippet == "Text" and res[0].source_name == "Example News"
    assert str(res[0].published) == "2026-09-01" and seen[0].headers["x-subscription-token"] == "brave-key"


def test_serpapi_parses_dates_and_reports_api_errors():
    body = {"organic_results": [{"title": "T", "link": "https://serp.example/1", "snippet": "S", "date": "Nov 3, 2026", "source": "Mint"}]}
    res = SerpAPISearch("serp-key", transport([body])).search("q")
    assert res[0].url == "https://serp.example/1" and str(res[0].published) == "2026-11-03" and res[0].source_name == "Mint"
    with pytest.raises(SearchError, match="serpapi error"):
        SerpAPISearch("serp-key", transport([{"error": "Invalid API key."}])).search("q")


@pytest.mark.parametrize("responses,match,rate_limited", [
    ([(401, {"detail": "unauthorized"})], "rejected the API key", False),
    ([(429, {"detail": "slow down"})], "rate limit", True),
    ([(200, "<html>not json</html>")], "invalid", False),
    ([(200, ["unexpected", "list"])], "unexpected response shape", False),
    ([(400, {})], "HTTP 400", False),
    ([httpx.ReadTimeout("t"), httpx.ReadTimeout("t")], "timed out", False),
    ([(503, {}), (502, {})], "HTTP 502", False),
])
def test_search_error_handling(responses, match, rate_limited, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    with pytest.raises(SearchError, match=match) as ei:
        TavilySearch("k", transport(responses)).search("q")
    assert ei.value.rate_limited == rate_limited


def test_search_retries_transient_failure_once(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    res = TavilySearch("k", transport([(503, {}), TAVILY_OK])).search("q")
    assert len(res) == 1


def test_missing_keys_and_null_provider_report_honest_status():
    with pytest.raises(SearchNotConfigured, match="provider not configured"):
        TavilySearch("", None)
    outcomes = run_research({"name": "Synthetic Co Pvt Ltd", "industry": "Textiles"}, [], [], NullSearch(), None)
    assert outcomes and all(o.status == "not_configured" and o.message == NOT_CONFIGURED and not o.findings for o in outcomes)


def test_rate_limited_provider_stops_issuing_queries():
    seen = []
    outcomes = run_research({"name": "Synthetic Co", "industry": "Textiles"}, [], [],
                            TavilySearch("k", transport([(429, {})] * 20, seen)), None)
    assert len(seen) == 1 and outcomes[0].status == "failed"


# ---------------------------------------------------------------------------------------------- Voyage

def _vec_body(n, dim=4, start=0):
    return {"data": [{"index": i, "embedding": [0.1 * (start + i)] * dim} for i in range(n)], "model": "voyage-3.5"}


def test_voyage_batches_and_forwards_input_type():
    seen = []
    emb = VoyageEmbedder("pa-test-key", client=transport([_vec_body(128), _vec_body(2)], seen))
    vecs = emb.embed([f"text {i}" for i in range(130)], input_type="document")
    assert len(vecs) == 130 and {len(v) for v in vecs} == {4}
    bodies = [json.loads(r.content) for r in seen]
    assert [len(b["input"]) for b in bodies] == [128, 2] and all(b["input_type"] == "document" for b in bodies)
    assert emb.model_id == "voyage:voyage-3.5"


def test_voyage_retries_rate_limit_then_succeeds(monkeypatch):
    slept = []
    monkeypatch.setattr("time.sleep", slept.append)
    vecs = VoyageEmbedder("pa-k", client=transport([(429, {}, {"retry-after": "2"}), _vec_body(1)])).embed(["x"])
    assert len(vecs) == 1 and slept and slept[0] >= 2


@pytest.mark.parametrize("responses,match", [
    ([(401, {})], "rejected the API key"),
    ([(200, {"oops": 1})], "unexpected response"),
    ([(200, _vec_body(2))], "2 vectors for 1 inputs"),
    ([(400, {})], "API error 400"),
])
def test_voyage_errors(responses, match):
    with pytest.raises(EmbeddingError, match=match):
        VoyageEmbedder("pa-k", client=transport(responses)).embed(["x"])


def test_voyage_rejects_mixed_dimensions():
    body = {"data": [{"index": 0, "embedding": [0.1] * 4}, {"index": 1, "embedding": [0.1] * 8}]}
    with pytest.raises(EmbeddingError, match="different dimensions"):
        VoyageEmbedder("pa-k", client=transport([body])).embed(["a", "b"])


def test_retrieval_never_mixes_embedding_models(org_a, synthetic_files):
    """Chunks indexed by another embedding model are excluded from semantic scoring (lexical match only)."""
    from sqlalchemy import update

    from app.core.database import system_session
    from app.models import DocumentChunk
    from tests.conftest import make_assessment, make_borrower, upload

    api = org_a["analyst"]
    a = make_assessment(api, make_borrower(api)["id"])
    upload(api, a["id"], synthetic_files["healthy"]["financial_statements"], "financial_statements")
    db = system_session()
    try:
        db.execute(update(DocumentChunk).where(DocumentChunk.application_id == a["id"]).values(embedding=[1.0, 2.0], embedding_model="voyage:other-model"))
        db.commit()
    finally:
        db.close()
    r = api.post(f"/api/v1/assessments/{a['id']}/chat", json={"question": "What was the revenue in FY 2023-24?"})
    assert r.status_code == 200  # a 2-d vector from another model must not reach the cosine scorer


# ---------------------------------------------------------------------------------------------- status / secrets

def test_status_and_checks_without_credentials_are_honest(org_a):
    admin = org_a["admin"]
    st = admin.get("/api/v1/system/integrations").json()
    for name in ("tavily", "brave", "serpapi"):
        assert st[name]["status"] == "not_configured" and st[name]["configured"] is False
    res = admin.post("/api/v1/system/integrations/brave/test").json()
    assert res["status"] == "not_configured" and "provider not configured" in res["detail"]
    st2 = admin.get("/api/v1/system/integrations").json()
    assert st2["brave"]["checks"]["last"]["status"] == "not_configured"
    assert org_a["analyst"].post("/api/v1/system/integrations/brave/test").status_code == 403


def test_error_details_are_sanitised(monkeypatch):
    from app.core.config import settings
    from app.services.providers import sanitize

    monkeypatch.setattr(settings, "TAVILY_API_KEY", "tvly-SECRETVALUE123456")
    text = sanitize("401 for key tvly-SECRETVALUE123456 and sk-ant-api03-abcdefghijklmnop and pa-ABCDEFGHIJKLMNOPQRST")
    assert "SECRETVALUE" not in text and "sk-ant-api03" not in text and "pa-ABCDEFGHIJ" not in text


def test_brave_invalid_token_422_is_reported_as_auth_error():
    # Recorded from the live Brave API (2026-11-27) for a deliberately invalid subscription token.
    body = {"error": {"code": "SUBSCRIPTION_TOKEN_INVALID", "detail": "The provided subscription token is invalid.",
                      "meta": {"component": "authentication"}, "status": 422}, "type": "ErrorResponse"}
    with pytest.raises(SearchError, match="rejected the API key \\(HTTP 422\\)"):
        BraveSearch("bad", transport([(422, body)])).search("q")
