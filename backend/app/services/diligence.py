"""External research and primary due-diligence observations.

Research is only ever recorded with the provider that actually produced it. If no search
provider is configured, queries are stored as ``not_configured`` with no findings — unless the
borrower is a synthetic demonstration case, in which case clearly labelled synthetic fixtures
are used (``source_tier='synthetic'``, no URLs).
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ai_engine.nlp.observations import interpret_observation
from ai_engine.research.agent import run_research, synthetic_outcomes
from app.core.config import settings
from app.core.database import utcnow
from app.models import CreditApplication, PrimaryObservation, ResearchFinding, ResearchQuery
from app.services import audit
from app.services.providers import llm, search


def _fixtures(scenario: str | None) -> list[dict]:
    if not scenario:
        return []
    path = Path(settings.SYNTHETIC_DATA_DIR) / "research" / f"{scenario}.json"
    if not path.is_file():
        return []
    return json.loads(path.read_text())


def run_research_for(db: Session, app_: CreditApplication, user_id: int | None) -> dict:
    company = app_.company
    cdict = {"name": company.name, "industry": company.industry}
    provider_name = settings.SEARCH_PROVIDER
    if company.is_synthetic and (provider_name == "none" or not settings.SEARCH_API_KEY):
        outcomes = synthetic_outcomes(cdict, _fixtures(company.demo_scenario))
        provider_name = "synthetic_fixture"
    else:
        outcomes = run_research(cdict, [p.name for p in company.promoters], [d.name for d in company.directors],
                                search(), llm(), settings.SEARCH_MAX_RESULTS)
    run = (db.scalar(select(func.max(ResearchQuery.run_number)).where(ResearchQuery.application_id == app_.id)) or 0) + 1
    now = utcnow()
    counts = {"queries": 0, "findings": 0}
    for oc in outcomes:
        q = ResearchQuery(application_id=app_.id, run_number=run, query_text=oc.query.text[:500], category=oc.query.category,
                          entity=oc.query.entity[:200], provider=provider_name, status=oc.status,
                          result_count=len(oc.findings), message=oc.message, executed_at=now, created_by_id=user_id)
        db.add(q)
        db.flush()
        counts["queries"] += 1
        for f in oc.findings:
            db.add(ResearchFinding(
                application_id=app_.id, query_id=q.id, title=f.title, source_name=f.source_name, source_url=f.source_url,
                source_tier=f.source_tier, published_at=f.published, retrieved_at=now, entity=f.entity[:200],
                category=f.category, severity=f.severity, sentiment=f.sentiment, summary=f.summary,
                summary_method=f.summary_method, evidence_excerpt=f.evidence_excerpt, verification_status=f.verification_status,
                relevance_score=f.relevance, recency_score=f.recency, matched_keywords=f.matched_keywords,
                is_synthetic=f.is_synthetic))
            counts["findings"] += 1
    statuses = sorted({oc.status for oc in outcomes})
    audit.record(db, organization_id=app_.organization_id, user_id=user_id, action="research.run",
                 summary=f"Research run #{run} via {provider_name}: {counts['findings']} finding(s) from {counts['queries']} queries",
                 entity_type="application", entity_id=app_.id, application_id=app_.id,
                 details={"provider": provider_name, "statuses": statuses})
    return {"run": run, "provider": provider_name, "statuses": statuses, **counts}


def add_observation(db: Session, app_: CreditApplication, *, user_id: int, category: str, sub_category: str, title: str,
                    notes: str, rating: int | None, observed_on: date, numeric_value: float | None = None,
                    severity_override: str | None = None, thresholds: dict | None = None) -> PrimaryObservation:
    interp = interpret_observation(category, sub_category, title, notes, rating, numeric_value, thresholds)
    obs = PrimaryObservation(
        application_id=app_.id, category=category, sub_category=sub_category, title=title, notes=notes, rating=rating,
        numeric_value=interp.numeric_value, numeric_unit="%" if interp.numeric_value is not None else None,
        observed_on=observed_on, author_id=user_id, severity=severity_override or interp.severity, severity_source="analyst" if severity_override else "system",
        risk_impact=interp.risk_impact, interpretation=interp.interpretation, signals=list(interp.signals),
        reviewer_status="pending", is_synthetic=app_.company.is_synthetic,
    )
    db.add(obs)
    db.flush()
    audit.record(db, organization_id=app_.organization_id, user_id=user_id, action="observation.added",
                 summary=f"Due-diligence observation: {title[:120]} ({interp.severity})", entity_type="observation",
                 entity_id=obs.id, application_id=app_.id)
    return obs
