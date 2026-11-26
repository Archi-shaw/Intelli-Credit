"""Request dependencies: authentication, role checks and organisation-scoped lookups."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import jwt
from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import apply_tenant, get_db
from app.core.errors import ForbiddenError, NotFoundError, UnauthorizedError
from app.core.security import decode_access_token
from app.models import (
    CAMReport,
    Company,
    CreditApplication,
    Membership,
    Organization,
    Reconciliation,
    RevokedToken,
    UploadedDocument,
    User,
)

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@dataclass
class CurrentUser:
    user: User
    organization: Organization
    role: str
    token_jti: str
    token_exp: datetime

    @property
    def id(self) -> int:
        return self.user.id

    @property
    def org_id(self) -> int:
        return self.organization.id


def _extract_token(request: Request) -> tuple[str | None, bool]:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip(), False
    cookie = request.cookies.get(settings.COOKIE_NAME)
    if cookie:
        return cookie, True
    return None, False


def get_current_user(request: Request, db: Session = Depends(get_db)) -> CurrentUser:
    token, via_cookie = _extract_token(request)
    if not token:
        raise UnauthorizedError("Authentication required.")
    # CSRF defence for cookie sessions: browsers cannot attach custom headers cross-site without CORS approval.
    if via_cookie and request.method not in SAFE_METHODS and request.headers.get("x-ic-client") != "web":
        raise ForbiddenError("Missing client header for cookie-authenticated request.", code="csrf_check_failed")
    try:
        payload = decode_access_token(token)
    except jwt.ExpiredSignatureError as exc:
        raise UnauthorizedError("Session expired. Please sign in again.", code="session_expired") from exc
    except jwt.PyJWTError as exc:
        raise UnauthorizedError("Invalid session token.") from exc

    jti = payload.get("jti", "")
    if db.scalar(select(RevokedToken.id).where(RevokedToken.jti == jti)):
        raise UnauthorizedError("Session has been signed out.", code="session_revoked")

    user = db.get(User, int(payload["sub"]))
    if not user or not user.is_active:
        raise UnauthorizedError("Account is inactive or no longer exists.")
    membership = db.scalar(
        select(Membership).where(Membership.user_id == user.id, Membership.organization_id == int(payload["org"]))
    )
    if not membership:
        raise UnauthorizedError("Organisation membership not found.")
    org = db.get(Organization, membership.organization_id)
    apply_tenant(db, org.id)
    exp = datetime.fromtimestamp(int(payload["exp"]), tz=timezone.utc)
    return CurrentUser(user=user, organization=org, role=membership.role, token_jti=jti, token_exp=exp)


def require_roles(*roles: str):
    def _checker(current: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if current.role not in roles:
            raise ForbiddenError(f"This action requires one of the roles: {', '.join(roles)}.")
        return current

    return _checker


# Common role groups
require_admin = require_roles("admin")
require_analyst = require_roles("admin", "analyst")
require_reviewer = require_roles("admin", "reviewer")
require_any = require_roles("admin", "analyst", "reviewer")
require_contributor = require_roles("admin", "analyst", "reviewer")  # notes / comments


def get_company_for_org(db: Session, company_id: int, current: CurrentUser) -> Company:
    company = db.get(Company, company_id)
    if not company or company.organization_id != current.org_id:
        raise NotFoundError("Company not found.")
    return company


def get_application_for_org(db: Session, application_id: int, current: CurrentUser) -> CreditApplication:
    app_ = db.get(CreditApplication, application_id)
    if not app_ or app_.organization_id != current.org_id:
        raise NotFoundError("Credit application not found.")
    return app_


def get_document_for_org(db: Session, document_id: int, current: CurrentUser) -> UploadedDocument:
    doc = db.get(UploadedDocument, document_id)
    if not doc or doc.organization_id != current.org_id:
        raise NotFoundError("Document not found.")
    return doc


def get_cam_for_org(db: Session, report_id: int, current: CurrentUser) -> CAMReport:
    rep = db.get(CAMReport, report_id)
    if not rep:
        raise NotFoundError("Report not found.")
    get_application_for_org(db, rep.application_id, current)
    return rep


def get_reconciliation_for_org(db: Session, recon_id: int, current: CurrentUser) -> Reconciliation:
    r = db.get(Reconciliation, recon_id)
    if not r:
        raise NotFoundError("Reconciliation finding not found.")
    get_application_for_org(db, r.application_id, current)
    return r


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None
