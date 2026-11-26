"""Audit trail and in-app notifications."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditLog, Membership, Notification


def record(
    db: Session,
    *,
    organization_id: int | None,
    user_id: int | None,
    action: str,
    summary: str,
    entity_type: str | None = None,
    entity_id: int | None = None,
    application_id: int | None = None,
    details: dict | None = None,
    ip_address: str | None = None,
) -> AuditLog:
    entry = AuditLog(
        organization_id=organization_id,
        user_id=user_id,
        action=action,
        summary=summary[:500],
        entity_type=entity_type,
        entity_id=entity_id,
        application_id=application_id,
        details=details or {},
        ip_address=ip_address,
    )
    db.add(entry)
    return entry


def notify(
    db: Session,
    *,
    organization_id: int,
    user_id: int,
    title: str,
    message: str,
    level: str = "info",
    link: str | None = None,
) -> Notification:
    n = Notification(
        organization_id=organization_id, user_id=user_id, title=title, message=message, level=level, link=link
    )
    db.add(n)
    return n


def notify_role(
    db: Session, *, organization_id: int, roles: tuple[str, ...], title: str, message: str, level: str = "info",
    link: str | None = None, exclude_user_id: int | None = None,
) -> None:
    user_ids = db.scalars(
        select(Membership.user_id).where(Membership.organization_id == organization_id, Membership.role.in_(roles))
    ).all()
    for uid in user_ids:
        if uid != exclude_user_id:
            notify(db, organization_id=organization_id, user_id=uid, title=title, message=message, level=level, link=link)
