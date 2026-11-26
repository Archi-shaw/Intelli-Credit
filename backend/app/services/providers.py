"""Factories for optional external providers plus an honest integration-status report.

Status values: ``configured`` means credentials are present (not that a call succeeded);
``verified`` / ``error`` are only reported after an explicit live check.
"""
from __future__ import annotations

import logging

from ai_engine.embeddings.providers import EmbeddingError, EmbeddingProvider, HashingEmbedder, get_embedder
from ai_engine.llm.provider import LLMError, LLMProvider, LLMUnavailable, get_llm
from ai_engine.ocr.engine import ocr_status
from ai_engine.research.providers import SearchError, SearchNotConfigured, SearchProvider, get_search_provider
from app.core.config import settings

log = logging.getLogger("intellicredit.providers")


def llm() -> LLMProvider:
    return get_llm(settings.LLM_PROVIDER, settings.LLM_API_KEY, settings.LLM_MODEL, settings.LLM_EFFORT,
                   settings.LLM_TIMEOUT_SECONDS)


def embedder() -> EmbeddingProvider:
    try:
        return get_embedder(settings.EMBEDDING_PROVIDER, settings.EMBEDDING_MODEL, settings.EMBEDDING_API_KEY)
    except Exception as exc:  # e.g. sentence-transformers not installed
        log.error("Embedding provider %s unavailable (%s); using local hashing embedder", settings.EMBEDDING_PROVIDER, exc)
        return HashingEmbedder(384)


def search() -> SearchProvider:
    return get_search_provider(settings.SEARCH_PROVIDER, settings.SEARCH_API_KEY)


def integration_status() -> dict:
    """Static configuration status. Never includes key material."""
    llm_conf = settings.LLM_PROVIDER != "none"
    emb = settings.EMBEDDING_PROVIDER
    return {
        "llm": {"provider": settings.LLM_PROVIDER, "model": settings.LLM_MODEL if llm_conf else None,
                "status": "configured" if llm_conf and (settings.LLM_API_KEY or settings.LLM_PROVIDER == "anthropic") else "not_configured",
                "api_key_present": bool(settings.LLM_API_KEY),
                "note": None if llm_conf else "Assistant answers and narratives use deterministic, extractive output."},
        "embeddings": {"provider": emb, "model": settings.EMBEDDING_MODEL,
                       "status": "local" if emb == "local-hash" else ("configured" if emb == "sentence-transformers" or settings.EMBEDDING_API_KEY else "not_configured"),
                       "semantic": emb != "local-hash"},
        "search": {"provider": settings.SEARCH_PROVIDER,
                   "status": "configured" if settings.SEARCH_PROVIDER != "none" and settings.SEARCH_API_KEY else "not_configured",
                   "note": None if settings.SEARCH_PROVIDER != "none" else
                   "External research is disabled. Synthetic demo borrowers use labelled synthetic research fixtures."},
        "ocr": ocr_status(settings.OCR_PROVIDER),
        "jobs": {"backend": settings.JOB_BACKEND},
        "databricks": {"status": "configured" if settings.DATABRICKS_HOST and settings.DATABRICKS_TOKEN else "not_configured"},
    }


def live_check(name: str) -> dict:
    """Make one minimal real call to a provider. Used by the admin 'Test connection' button."""
    try:
        if name == "llm":
            provider = llm()
            out = provider.generate(system="Reply with the single word OK.",
                                    messages=[{"role": "user", "content": "ping"}], max_tokens=64)
            return {"status": "verified", "detail": f"Model {out.model} responded."}
        if name == "search":
            results = search().search("Reserve Bank of India MSME", max_results=1)
            return {"status": "verified", "detail": f"{len(results)} result(s) returned."}
        if name == "embeddings":
            vec = embedder().embed(["test"])[0]
            return {"status": "verified", "detail": f"{len(vec)}-dimension vector returned."}
    except (LLMUnavailable, SearchNotConfigured) as exc:
        return {"status": "not_configured", "detail": str(exc)}
    except (LLMError, SearchError, EmbeddingError) as exc:
        return {"status": "error", "detail": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "detail": f"{type(exc).__name__}: {exc}"[:300]}
    return {"status": "unknown", "detail": f"Unknown integration '{name}'."}
