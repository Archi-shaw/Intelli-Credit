"""Organisation, user and role management (admin), notifications and audit log."""
from __future__ import annotations

import re
import secrets

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import serializers as S
from app.api.routes.auth import PERMISSIONS
from app.core.database import apply_tenant, get_db
from app.core.deps import CurrentUser, client_ip, get_current_user, require_admin, require_any
from app.core.errors import ConflictError, NotFoundError, ValidationFailed
from app.core.security import hash_password
from app.models import AuditLog, Membership, Notification, Organization, ScoringPolicy, User
from app.schemas.requests import OrganizationIn, UserCreateIn, UserUpdateIn
from app.services import audit
from ai_engine.risk_model.policy import get_default_policy

router = APIRouter(tags=["organization"])


@router.get("/organization")
def get_org(current: CurrentUser = Depends(require_any), db: Session = Depends(get_db)):
    o = current.organization
    members = db.scalar(select(func.count(Membership.id)).where(Membership.organization_id == o.id))
    return {"id": o.id, "name": o.name, "slug": o.slug, "is_demo": o.is_demo, "member_count": members,
            "created_at": o.created_at.isoformat()}


@router.patch("/organization")
def update_org(body: OrganizationIn, request: Request, current: CurrentUser = Depends(require_admin),
               db: Session = Depends(get_db)):
    o = current.organization
    old = o.name
    o.name = body.name
    audit.record(db, organization_id=o.id, user_id=current.id, action="organization.updated",
                 summary=f"Organisation renamed from '{old}' to '{o.name}'", entity_type="organization", entity_id=o.id,
                 ip_address=client_ip(request))
    db.commit()
    return {"id": o.id, "name": o.name, "slug": o.slug}


@router.post("/organizations", status_code=201)
def create_org(body: OrganizationIn, request: Request, current: CurrentUser = Depends(require_admin),
               db: Session = Depends(get_db)):
    """Create an additional organisation; the caller becomes its administrator."""
    slug = re.sub(r"[^a-z0-9]+", "-", body.name.lower()).strip("-")[:80] or "org"
    if db.scalar(select(Organization.id).where(Organization.slug == slug)):
        slug = f"{slug}-{secrets.token_hex(3)}"
    org = Organization(name=body.name, slug=slug)
    db.add(org)
    db.flush()
    db.add(Membership(user_id=current.id, organization_id=org.id, role="admin", is_default=False))
    apply_tenant(db, org.id)  # row-level security: the new org's policy row belongs to the new org
    db.add(ScoringPolicy(organization_id=org.id, name="Default prototype policy", version=1, is_active=True,
                         config=get_default_policy(), change_note="Initial default policy", created_by_id=current.id))
    db.flush()
    apply_tenant(db, current.org_id)
    audit.record(db, organization_id=current.org_id, user_id=current.id, action="organization.created",
                 summary=f"Created organisation '{org.name}'", entity_type="organization", entity_id=org.id,
                 ip_address=client_ip(request))
    db.commit()
    return {"id": org.id, "name": org.name, "slug": org.slug}


@router.get("/roles")
def roles(current: CurrentUser = Depends(require_any)):
    return {"roles": [{"role": r, "permissions": p,
                       "description": {"admin": "Full access including users, policy and approvals.",
                                       "analyst": "Creates borrowers and assessments, uploads documents, runs analysis.",
                                       "reviewer": "Reviews recommendations and records approval decisions."}[r]}
                      for r, p in PERMISSIONS.items()]}


@router.get("/users")
def list_users(current: CurrentUser = Depends(require_any), db: Session = Depends(get_db)):
    rows = db.execute(select(User, Membership.role).join(Membership, Membership.user_id == User.id)
                      .where(Membership.organization_id == current.org_id).order_by(User.full_name)).all()
    return {"items": [S.user(u, role) for u, role in rows]}


@router.post("/users", status_code=201)
def create_user(body: UserCreateIn, request: Request, current: CurrentUser = Depends(require_admin),
                db: Session = Depends(get_db)):
    email = body.email.lower()
    existing = db.scalar(select(User).where(func.lower(User.email) == email))
    if existing:
        if db.scalar(select(Membership.id).where(Membership.user_id == existing.id, Membership.organization_id == current.org_id)):
            raise ConflictError("This user is already a member of the organisation.")
        raise ConflictError("An account with this e-mail already exists.", code="email_taken")
    u = User(email=email, full_name=body.full_name, title=body.title, hashed_password=hash_password(body.password))
    db.add(u)
    db.flush()
    db.add(Membership(user_id=u.id, organization_id=current.org_id, role=body.role, is_default=True))
    audit.record(db, organization_id=current.org_id, user_id=current.id, action="user.created",
                 summary=f"Added user {email} as {body.role}", entity_type="user", entity_id=u.id, ip_address=client_ip(request))
    db.commit()
    return S.user(u, body.role)


@router.patch("/users/{user_id}")
def update_user(user_id: int, body: UserUpdateIn, request: Request, current: CurrentUser = Depends(require_admin),
                db: Session = Depends(get_db)):
    m = db.scalar(select(Membership).where(Membership.user_id == user_id, Membership.organization_id == current.org_id))
    if not m:
        raise NotFoundError("User not found in this organisation.")
    u = db.get(User, user_id)
    changes = body.model_dump(exclude_none=True)
    if user_id == current.id and (changes.get("role", "admin") != "admin" or changes.get("is_active") is False):
        raise ValidationFailed("You cannot remove your own administrator access or deactivate yourself.")
    if "role" in changes:
        if m.role == "admin" and changes["role"] != "admin":
            admins = db.scalar(select(func.count(Membership.id)).where(Membership.organization_id == current.org_id,
                                                                       Membership.role == "admin"))
            if admins <= 1:
                raise ValidationFailed("The organisation must keep at least one administrator.")
        m.role = changes["role"]
    for f in ("is_active", "full_name", "title"):
        if f in changes:
            setattr(u, f, changes[f])
    audit.record(db, organization_id=current.org_id, user_id=current.id, action="user.updated",
                 summary=f"Updated {u.email}: {', '.join(f'{k}={v}' for k, v in changes.items())}", entity_type="user",
                 entity_id=u.id, ip_address=client_ip(request))
    db.commit()
    return S.user(u, m.role)


@router.get("/audit-logs")
def audit_logs(current: CurrentUser = Depends(require_any), db: Session = Depends(get_db),
               application_id: int | None = None, action: str | None = Query(default=None, max_length=80),
               limit: int = Query(default=100, ge=1, le=500), offset: int = Query(default=0, ge=0)):
    q = select(AuditLog).where(AuditLog.organization_id == current.org_id)
    if application_id:
        q = q.where(AuditLog.application_id == application_id)
    if action:
        q = q.where(AuditLog.action.startswith(action))
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.scalars(q.order_by(AuditLog.id.desc()).limit(limit).offset(offset)).all()
    names = {u.id: u.full_name for u in db.scalars(select(User).where(User.id.in_({r.user_id for r in rows if r.user_id}))).all()}
    return {"items": [S.audit_log(r, names) for r in rows], "total": total}


@router.get("/notifications")
def notifications(current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db),
                  unread_only: bool = False):
    q = select(Notification).where(Notification.user_id == current.id, Notification.organization_id == current.org_id)
    if unread_only:
        q = q.where(Notification.is_read.is_(False))
    rows = db.scalars(q.order_by(Notification.id.desc()).limit(50)).all()
    unread = db.scalar(select(func.count(Notification.id)).where(Notification.user_id == current.id,
                                                                 Notification.organization_id == current.org_id,
                                                                 Notification.is_read.is_(False)))
    return {"items": [S.notification(n) for n in rows], "unread": unread}


@router.post("/notifications/{notification_id}/read")
def mark_read(notification_id: int, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    n = db.get(Notification, notification_id)
    if not n or n.user_id != current.id:
        raise NotFoundError("Notification not found.")
    n.is_read = True
    db.commit()
    return {"ok": True}


@router.post("/notifications/read-all")
def mark_all_read(current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    for n in db.scalars(select(Notification).where(Notification.user_id == current.id, Notification.is_read.is_(False))).all():
        n.is_read = True
    db.commit()
    return {"ok": True}
