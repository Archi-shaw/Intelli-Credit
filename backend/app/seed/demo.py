"""Seed demonstration organisations, users and three SYNTHETIC borrower cases.

Documents are pushed through the real pipeline (upload → background job → extraction →
reconciliation → research fixtures → observations → assessment); nothing is hardcoded.
"""
from __future__ import annotations

import logging
import os
from datetime import date

from sqlalchemy import select, text

from ai_engine.risk_model.policy import get_default_policy
from app.core.config import settings
from app.core.database import system_session
from app.core.security import hash_password
from app.models import Company, CreditApplication, Director, Membership, Organization, Promoter, ScoringPolicy, User
from app.seed import synthetic
from app.services import analysis, cam, diligence, documents, jobs

log = logging.getLogger("intellicredit.seed")

DEMO_ORG = {"name": "Meridian Demo Bank (Synthetic)", "slug": "meridian-demo"}
OTHER_ORG = {"name": "Northwind Credit Union (Synthetic)", "slug": "northwind-demo"}
USERS = [  # (email, name, role, org slug)
    ("admin@meridian.example", "Demo Administrator", "admin", "meridian-demo"),
    ("analyst@meridian.example", "Demo Credit Analyst", "analyst", "meridian-demo"),
    ("reviewer@meridian.example", "Demo Credit Reviewer", "reviewer", "meridian-demo"),
    ("admin@northwind.example", "Northwind Administrator", "admin", "northwind-demo"),
]


def demo_password() -> str:
    pw = os.environ.get("DEMO_PASSWORD", "")
    if not pw:
        if settings.ENVIRONMENT == "production":
            raise RuntimeError("Set DEMO_PASSWORD explicitly to seed demo users in a production environment.")
        pw = "IntelliDemo2026!"
    return pw


def reset_demo() -> None:
    db = system_session()
    try:
        if db.get_bind().dialect.name == "postgresql":
            db.execute(text("SELECT set_config('app.allow_record_maintenance', 'on', true)"))
        for slug in (DEMO_ORG["slug"], OTHER_ORG["slug"]):
            org = db.scalar(select(Organization).where(Organization.slug == slug))
            if org:
                db.delete(org)
        for email, *_ in USERS:
            u = db.scalar(select(User).where(User.email == email))
            if u:
                db.delete(u)
        db.commit()
    finally:
        db.close()


def seed(*, reset: bool = False, analyse: bool = True) -> dict:
    if reset:
        reset_demo()
    manifest = synthetic.generate(settings.SYNTHETIC_DATA_DIR)
    db = system_session()
    created: dict = {"organizations": [], "users": [], "assessments": []}
    try:
        if db.scalar(select(Organization.id).where(Organization.slug == DEMO_ORG["slug"])):
            log.info("demo data already present; use --reset to recreate")
            return {"skipped": True}
        orgs = {}
        for o in (DEMO_ORG, OTHER_ORG):
            org = Organization(name=o["name"], slug=o["slug"], is_demo=True)
            db.add(org)
            db.flush()
            db.add(ScoringPolicy(organization_id=org.id, name="Default prototype policy", version=1, is_active=True,
                                 config=get_default_policy(), change_note="Seeded default policy"))
            orgs[o["slug"]] = org
            created["organizations"].append(o["name"])
        users = {}
        pw_hash = hash_password(demo_password())
        for email, name, role, slug in USERS:
            u = User(email=email, full_name=name, hashed_password=pw_hash, title=role.capitalize())
            db.add(u)
            db.flush()
            db.add(Membership(user_id=u.id, organization_id=orgs[slug].id, role=role, is_default=True))
            users[email] = u
            created["users"].append({"email": email, "role": role, "organization": orgs[slug].name})
        db.commit()

        org = orgs[DEMO_ORG["slug"]]
        analyst = users["analyst@meridian.example"]
        for key, entry in manifest.items():
            case: synthetic.Case = entry["case"]
            c = dict(case.company)
            directors, promoters = c.pop("directors"), c.pop("promoters")
            company = Company(organization_id=org.id, created_by_id=analyst.id, is_synthetic=True, demo_scenario=key, **c)
            company.directors = [Director(name=n, din=din, designation=des) for n, din, des in directors]
            company.promoters = [Promoter(name=n, shareholding_pct=p, experience_years=y) for n, p, y in promoters]
            db.add(company)
            db.flush()
            a = CreditApplication(reference=f"IC-DEMO-{key.upper()[:4]}", organization_id=org.id, company_id=company.id,
                                  application_date=date(2024, 10, 15), status="documents_pending", current_step=3,
                                  created_by_id=analyst.id, repayment_frequency="monthly", moratorium_months=0,
                                  guarantors=[], **case.application)
            db.add(a)
            db.flush()
            job_ids = []
            for path, category in entry["files"]:
                _doc, job = documents.upload_document(db, application=a, filename=path.name, data=path.read_bytes(),
                                                      category=category, user_id=analyst.id, is_synthetic=True)
                job_ids.append(job.id)
            db.commit()
            for jid in job_ids:
                jobs.run_job(jid)  # inline, through the same job runner the workers use
            created["assessments"].append({"reference": a.reference, "company": company.name, "scenario": key})
            if not analyse:
                continue
            db.expire_all()
            a = db.get(CreditApplication, a.id)
            analysis.run_reconciliation_for(db, a, analyst.id)
            diligence.run_research_for(db, a, analyst.id)
            for o in case.observations:
                diligence.add_observation(db, a, user_id=analyst.id, observed_on=date(2024, 10, 20), **o)
            db.commit()
            analysis.run_assessment(db, a, analyst.id)
            db.commit()
            if key == "healthy":
                _rep, job = cam.request_cam(db, a, analyst.id)
                db.commit()
                jobs.run_job(job.id)
        return created
    finally:
        db.close()
