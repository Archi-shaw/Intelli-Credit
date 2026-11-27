"""Factories for optional external providers, an honest integration-status report and live connection checks.

Status vocabulary (never conflated):
* ``not_configured`` – no credentials; nothing was called.
* ``configured``     – credentials present but not yet connection-tested from this organisation.
* ``verified``       – a live request succeeded (timestamped).
* ``error``          – a live request failed (sanitised reason, timestamped).
Mocked/contract tests never produce ``verified``. Secret values are never returned or logged.
"""
from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_engine.embeddings.providers import EmbeddingError, EmbeddingProvider, HashingEmbedder, get_embedder
from ai_engine.llm.provider import LLMError, LLMProvider, LLMUnavailable, get_llm
from ai_engine.ocr.engine import ocr_status
from ai_engine.research.providers import SearchError, SearchNotConfigured, SearchProvider, get_search_provider
from app.core.config import settings

log = logging.getLogger("intellicredit.providers")
SEARCH_PROVIDERS = ("tavily", "brave", "serpapi")
CHECKABLE = ("llm", "embeddings", *SEARCH_PROVIDERS)
_SECRET_PATTERNS = [re.compile(p) for p in (r"sk-ant-[A-Za-z0-9_\-]{8,}", r"\bpa-[A-Za-z0-9_\-]{16,}", r"\btvly-[A-Za-z0-9_\-]{8,}",
                                            r"\b[A-Za-z0-9_\-]{32,}\b")]


def llm() -> LLMProvider:
    return get_llm(settings.LLM_PROVIDER, settings.LLM_API_KEY, settings.LLM_MODEL, settings.LLM_EFFORT,
                   settings.LLM_TIMEOUT_SECONDS)


def embedder() -> EmbeddingProvider:
    try:
        return get_embedder(settings.EMBEDDING_PROVIDER, settings.EMBEDDING_MODEL, settings.EMBEDDING_API_KEY)
    except Exception as exc:  # e.g. sentence-transformers not installed
        log.error("Embedding provider %s unavailable (%s); using local hashing embedder", settings.EMBEDDING_PROVIDER, exc)
        return HashingEmbedder(384)


def search_key(name: str) -> str:
    specific = {"tavily": settings.TAVILY_API_KEY, "brave": settings.BRAVE_API_KEY, "serpapi": settings.SERPAPI_API_KEY}.get(name, "")
    return specific or (settings.SEARCH_API_KEY if name == settings.SEARCH_PROVIDER else "")


def search(name: str | None = None) -> SearchProvider:
    name = name or settings.SEARCH_PROVIDER
    try:
        return get_search_provider(name, search_key(name))
    except SearchNotConfigured:
        return get_search_provider("none", "")


def _llm_key_present() -> bool:
    return bool(settings.LLM_API_KEY or os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def sanitize(text: str) -> str:
    """Remove configured secret values and key-shaped tokens from provider error text before storing/returning it."""
    out = str(text)
    for v in (settings.LLM_API_KEY, settings.EMBEDDING_API_KEY, settings.SEARCH_API_KEY, settings.TAVILY_API_KEY,
              settings.BRAVE_API_KEY, settings.SERPAPI_API_KEY, os.environ.get("ANTHROPIC_API_KEY", "")):
        if v and len(v) >= 6:
            out = out.replace(v, "[redacted]")
    for p in _SECRET_PATTERNS:
        out = p.sub("[redacted]", out)
    return out[:300]


def integration_status() -> dict:
    """Static configuration status (no network calls). Never includes key material."""
    llm_on = settings.LLM_PROVIDER == "anthropic"
    emb = settings.EMBEDDING_PROVIDER
    status = {
        "llm": {"label": "Anthropic Claude", "provider": settings.LLM_PROVIDER, "model": settings.LLM_MODEL if llm_on else None,
                "configured": llm_on and _llm_key_present(), "api_key_present": bool(settings.LLM_API_KEY),
                "status": "configured" if llm_on and _llm_key_present() else "not_configured",
                "note": None if llm_on else "Assistant answers use extractive, cited output (no language model)."},
        "embeddings": {"label": "Embeddings", "provider": emb, "model": settings.EMBEDDING_MODEL,
                       "configured": emb == "local-hash" or emb == "sentence-transformers" or bool(settings.EMBEDDING_API_KEY),
                       "status": "local" if emb == "local-hash" else ("configured" if emb == "sentence-transformers" or settings.EMBEDDING_API_KEY else "not_configured"),
                       "semantic": emb != "local-hash",
                       "note": "Local hashing embedder (no network)." if emb == "local-hash" else None},
        "ocr": {"label": "OCR", **ocr_status(settings.OCR_PROVIDER)},
        "jobs": {"label": "Background jobs", "backend": settings.JOB_BACKEND},
        "rate_limiter": {"label": "Rate limiter", "backend": settings.RATE_LIMIT_BACKEND, "enabled": settings.RATE_LIMIT_ENABLED,
                         "fail_mode": settings.RATE_LIMIT_FAIL_MODE},
        "databricks": {"label": "Databricks", "status": "configured" if settings.DATABRICKS_HOST and settings.DATABRICKS_TOKEN else "not_configured"},
    }
    for name in SEARCH_PROVIDERS:
        present = bool(search_key(name))
        status[name] = {"label": {"tavily": "Tavily", "brave": "Brave Search", "serpapi": "SerpAPI"}[name], "provider": name,
                        "configured": present, "active": settings.SEARCH_PROVIDER == name,
                        "status": "configured" if present else "not_configured",
                        "note": None if present else "No verified external research available: provider not configured."}
    status["search"] = {"label": "Research provider in use", "provider": settings.SEARCH_PROVIDER,
                        "status": status.get(settings.SEARCH_PROVIDER, {}).get("status", "not_configured")}
    return status


def live_check(name: str) -> dict:
    """One minimal real request. Returns status verified / error / not_configured with a sanitised detail."""
    started = time.perf_counter()
    checked_at = datetime.now(timezone.utc).isoformat()

    def done(status: str, detail: str) -> dict:
        return {"integration": name, "status": status, "detail": sanitize(detail), "checked_at": checked_at,
                "latency_ms": round((time.perf_counter() - started) * 1000)}

    try:
        if name == "llm":
            if settings.LLM_PROVIDER != "anthropic" or not _llm_key_present():
                return done("not_configured", "LLM_PROVIDER=anthropic and an API key are required.")
            out = llm().generate(system="Reply with the single word OK.", messages=[{"role": "user", "content": "ping"}], max_tokens=64)
            return done("verified", f"Model {out.model} responded.")
        if name == "embeddings":
            if settings.EMBEDDING_PROVIDER == "voyage" and not settings.EMBEDDING_API_KEY:
                return done("not_configured", "EMBEDDING_API_KEY is required for Voyage.")
            vecs = embedder().embed(["credit appraisal", "working capital"], input_type="document")
            kind = "local (no network)" if settings.EMBEDDING_PROVIDER == "local-hash" else "live"
            return done("verified" if kind == "live" else "local", f"{len(vecs)} vectors × {len(vecs[0])} dimensions ({kind}).")
        if name in SEARCH_PROVIDERS:
            if not search_key(name):
                return done("not_configured", "No verified external research available: provider not configured.")
            results = get_search_provider(name, search_key(name)).search("Reserve Bank of India MSME credit", max_results=2)
            urls = sum(1 for r in results if r.url)
            return done("verified", f"{len(results)} result(s), {urls} with source URLs.")
    except (LLMUnavailable, SearchNotConfigured) as exc:
        return done("not_configured", str(exc))
    except (LLMError, SearchError, EmbeddingError) as exc:
        return done("error", str(exc))
    except Exception as exc:  # noqa: BLE001
        return done("error", f"{type(exc).__name__}: {exc}")
    return done("unknown", f"Unknown integration '{name}'.")


def last_checks(db: Session, organization_id: int) -> dict:
    """Latest connection-test result, last success and last failure per integration (this organisation's tests)."""
    from app.models import AuditLog

    rows = db.scalars(select(AuditLog).where(AuditLog.organization_id == organization_id, AuditLog.action == "integration.tested")
                      .order_by(AuditLog.id.desc()).limit(200)).all()
    out: dict[str, dict] = {}
    for r in rows:
        d = r.details or {}
        name = d.get("integration")
        if not name:
            continue
        entry = out.setdefault(name, {"last": None, "last_success": None, "last_failure": None})
        item = {"status": d.get("status"), "detail": d.get("detail"), "at": r.created_at.isoformat(), "latency_ms": d.get("latency_ms")}
        if entry["last"] is None:
            entry["last"] = item
        if d.get("status") in ("verified", "local") and entry["last_success"] is None:
            entry["last_success"] = item
        if d.get("status") == "error" and entry["last_failure"] is None:
            entry["last_failure"] = item
    return out
