"""Health, readiness, integration status and scoring-policy administration."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ai_engine.risk_model.policy import validate_policy
from app.core.config import settings
from app.core.database import SessionLocal, get_db
from app.core.deps import CurrentUser, client_ip, require_admin, require_any
from app.core.errors import ValidationFailed
from app.models import ScoringPolicy
from app.schemas.requests import PolicyIn
from app.services import audit
from app.services.analysis import active_policy
from app.services.providers import integration_status, live_check

router = APIRouter(tags=["system"])
health_router = APIRouter(tags=["health"])
VERSION = "1.0.0"


def health_payload() -> dict:
    return {"status": "ok", "service": settings.APP_NAME, "version": VERSION, "environment": settings.ENVIRONMENT}


def readiness() -> tuple[bool, dict]:
    checks: dict = {}
    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        checks["database"] = "ok"
        try:
            ver = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
            checks["migrations"] = ver or "missing"
        except Exception:  # noqa: BLE001
            db.rollback()
            checks["migrations"] = "not_applied" if not settings.is_sqlite else "n/a"
    except Exception as exc:  # noqa: BLE001
        checks["database"] = f"error: {type(exc).__name__}"
    finally:
        db.close()
    if settings.JOB_BACKEND == "celery":
        try:
            import redis

            redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=2).ping()
            checks["redis"] = "ok"
        except Exception as exc:  # noqa: BLE001
            checks["redis"] = f"error: {type(exc).__name__}"
    from app.core import storage

    try:
        root = storage.storage_root()
        probe = root / ".ready"
        probe.write_text("ok")
        probe.unlink()
        checks["storage"] = "ok"
    except OSError as exc:
        checks["storage"] = f"error: {exc.strerror}"
    ok = all(v == "ok" or k == "migrations" and v not in ("not_applied", "missing") for k, v in checks.items())
    return ok, checks


@health_router.get("/health")
def health():
    return health_payload()


@health_router.get("/ready")
def ready():
    ok, checks = readiness()
    return JSONResponse(status_code=200 if ok else 503, content={"status": "ready" if ok else "not_ready", "checks": checks})


@router.get("/system/integrations")
def integrations(current: CurrentUser = Depends(require_any), db: Session = Depends(get_db)):
    from app.services.providers import last_checks

    status = integration_status()
    checks = last_checks(db, current.org_id)
    for name, entry in status.items():
        entry["checks"] = checks.get(name)
    return status


@router.post("/system/integrations/{name}/test")
def test_integration(name: str, request: Request, current: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    from app.services.providers import CHECKABLE

    if name == "search":
        from app.core.config import settings as s

        name = s.SEARCH_PROVIDER
    if name not in CHECKABLE:
        raise ValidationFailed("Unknown integration.")
    res = live_check(name)
    audit.record(db, organization_id=current.org_id, user_id=current.id, action="integration.tested",
                 summary=f"Tested {name}: {res['status']}", ip_address=client_ip(request),
                 details={"integration": name, "status": res["status"], "detail": res["detail"], "latency_ms": res["latency_ms"]})
    db.commit()
    return res


@router.get("/system/models")
def models(current: CurrentUser = Depends(require_any)):
    """Challenger model artifact summary (global, non-tenant data)."""
    from app.services.challenger import model_summary

    return {"primary": {"name": "Policy scorecard", "status": "active", "role": "primary decision-support model"},
            "challenger": model_summary()}


@router.get("/policy")
def get_policy(current: CurrentUser = Depends(require_any), db: Session = Depends(get_db)):
    cfg, row = active_policy(db, current.org_id)
    versions = db.scalars(select(ScoringPolicy).where(ScoringPolicy.organization_id == current.org_id)
                          .order_by(ScoringPolicy.version.desc())).all()
    return {"active_version": row.version if row else None, "config": cfg,
            "versions": [{"version": v.version, "name": v.name, "is_active": v.is_active, "change_note": v.change_note,
                          "created_at": v.created_at.isoformat()} for v in versions]}


@router.put("/policy")
def update_policy(body: PolicyIn, request: Request, current: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    from ai_engine.risk_model.policy import merge_policy

    merged = merge_policy(body.config)
    problems = validate_policy(merged)
    if problems:
        raise ValidationFailed("Policy is invalid: " + "; ".join(problems), details={"problems": problems})
    for p in db.scalars(select(ScoringPolicy).where(ScoringPolicy.organization_id == current.org_id)).all():
        p.is_active = False
    ver = (db.scalar(select(func.max(ScoringPolicy.version)).where(ScoringPolicy.organization_id == current.org_id)) or 0) + 1
    db.add(ScoringPolicy(organization_id=current.org_id, name=body.name, version=ver, is_active=True, config=merged,
                         change_note=body.change_note, created_by_id=current.id))
    audit.record(db, organization_id=current.org_id, user_id=current.id, action="policy.updated",
                 summary=f"Scoring policy v{ver} activated: {body.change_note[:200]}", ip_address=client_ip(request))
    db.commit()
    return {"active_version": ver}
