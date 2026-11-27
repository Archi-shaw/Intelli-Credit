"""Authentication: registration, login (httpOnly cookie + bearer token), logout, session."""
from __future__ import annotations

import re
import secrets

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import serializers as S
from app.core.config import settings
from app.core.database import apply_tenant, get_db, utcnow
from app.core.deps import CurrentUser, client_ip, get_current_user
from app.core.errors import ConflictError, ForbiddenError, UnauthorizedError
from app.core.security import create_access_token, hash_password, login_throttle, verify_password
from app.models import Membership, Organization, RevokedToken, ScoringPolicy, User
from app.schemas.requests import LoginIn, RegisterIn
from app.services import audit
from ai_engine.risk_model.policy import get_default_policy

router = APIRouter(prefix="/auth", tags=["auth"])

# Equalise timing for unknown e-mails so responses do not reveal which accounts exist.
_DUMMY_HASH = hash_password(secrets.token_hex(8) + "a1")


def _session_payload(user: User, org: Organization, role: str, token: str, expires) -> dict:
    return {"user": S.user(user, role), "organization": {"id": org.id, "name": org.name, "slug": org.slug, "is_demo": org.is_demo},
            "role": role, "access_token": token, "token_type": "bearer", "expires_at": expires.isoformat()}


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(settings.COOKIE_NAME, token, httponly=True, secure=settings.COOKIE_SECURE, samesite="lax",
                        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60, path="/")


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:80] or "org"


@router.post("/register", status_code=201)
def register(body: RegisterIn, request: Request, response: Response, db: Session = Depends(get_db)):
    if not settings.ALLOW_REGISTRATION:
        raise ForbiddenError("Self-service registration is disabled. Ask an administrator for an account.",
                             code="registration_disabled")
    email = body.email.lower()
    if db.scalar(select(User.id).where(func.lower(User.email) == email)):
        raise ConflictError("An account with this e-mail already exists.", code="email_taken")
    slug = _slugify(body.organization_name)
    if db.scalar(select(Organization.id).where(Organization.slug == slug)):
        slug = f"{slug}-{secrets.token_hex(3)}"
    org = Organization(name=body.organization_name, slug=slug, is_demo=False)
    user = User(email=email, full_name=body.full_name, hashed_password=hash_password(body.password), last_login_at=utcnow())
    db.add_all([org, user])
    db.flush()
    apply_tenant(db, org.id)
    db.add(Membership(user_id=user.id, organization_id=org.id, role="admin", is_default=True))
    db.add(ScoringPolicy(organization_id=org.id, name="Default prototype policy", version=1, is_active=True,
                         config=get_default_policy(), change_note="Initial default policy", created_by_id=user.id))
    token, _jti, exp = create_access_token(user.id, org.id, "admin")
    audit.record(db, organization_id=org.id, user_id=user.id, action="auth.registered",
                 summary=f"Organisation '{org.name}' registered by {email}", entity_type="organization", entity_id=org.id,
                 ip_address=client_ip(request))
    db.commit()
    _set_cookie(response, token)
    return _session_payload(user, org, "admin", token, exp)


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    email = body.email.lower()
    key = f"{email}|{client_ip(request)}"
    if login_throttle.is_locked(key):
        raise UnauthorizedError("Too many failed sign-in attempts. Try again in 15 minutes.", code="login_locked")
    user = db.scalar(select(User).where(func.lower(User.email) == email))
    ok = verify_password(body.password, user.hashed_password if user else _DUMMY_HASH)
    if not user or not ok or not user.is_active:
        login_throttle.record_failure(key)
        raise UnauthorizedError("Invalid e-mail or password.", code="invalid_credentials")
    membership = db.scalar(select(Membership).where(Membership.user_id == user.id)
                           .order_by(Membership.is_default.desc(), Membership.id))
    if not membership:
        raise UnauthorizedError("This account is not a member of any organisation.")
    login_throttle.reset(key)
    org = db.get(Organization, membership.organization_id)
    apply_tenant(db, org.id)
    user.last_login_at = utcnow()
    token, _jti, exp = create_access_token(user.id, org.id, membership.role)
    audit.record(db, organization_id=org.id, user_id=user.id, action="auth.login", summary=f"{email} signed in",
                 entity_type="user", entity_id=user.id, ip_address=client_ip(request))
    db.commit()
    _set_cookie(response, token)
    return _session_payload(user, org, membership.role, token, exp)


@router.post("/logout")
def logout(request: Request, response: Response, current: CurrentUser = Depends(get_current_user),
           db: Session = Depends(get_db)):
    db.add(RevokedToken(jti=current.token_jti, expires_at=current.token_exp))
    audit.record(db, organization_id=current.org_id, user_id=current.id, action="auth.logout",
                 summary=f"{current.user.email} signed out", entity_type="user", entity_id=current.id,
                 ip_address=client_ip(request))
    db.commit()
    response.delete_cookie(settings.COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
def me(current: CurrentUser = Depends(get_current_user)):
    o = current.organization
    return {"user": S.user(current.user, current.role), "role": current.role,
            "organization": {"id": o.id, "name": o.name, "slug": o.slug, "is_demo": o.is_demo},
            "permissions": PERMISSIONS[current.role]}


@router.get("/session")
def session(current: CurrentUser = Depends(get_current_user)):
    return {"valid": True, "expires_at": current.token_exp.isoformat(), "role": current.role}


PERMISSIONS = {
    "admin": ["borrowers:write", "assessments:write", "documents:write", "documents:delete", "analysis:run",
              "review:decide", "comments:write", "users:manage", "org:manage", "audit:read", "policy:manage"],
    "analyst": ["borrowers:write", "assessments:write", "documents:write", "documents:delete", "analysis:run",
                "comments:write", "audit:read"],
    "reviewer": ["review:decide", "comments:write", "audit:read"],
}
