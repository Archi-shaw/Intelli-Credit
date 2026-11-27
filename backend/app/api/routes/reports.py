"""CAM generation, preview, history and authorised downloads."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import serializers as S
from app.core import storage
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, get_application_for_org, get_cam_for_org, require_any
from app.core.errors import NotFoundError, PreconditionFailed
from app.models import CAMReport, ProcessingJob
from app.services import audit, cam, jobs

router = APIRouter(tags=["cam"])


@router.post("/assessments/{application_id}/cam", status_code=202)
def generate(application_id: int, current: CurrentUser = Depends(require_any), db: Session = Depends(get_db)):
    a = get_application_for_org(db, application_id, current)
    if current.role == "reviewer" and a.status not in ("approved", "rejected", "awaiting_review"):
        raise PreconditionFailed("Reviewers can generate a CAM once a recommendation is awaiting review.")
    report, job = cam.request_cam(db, a, current.id)
    db.commit()
    jobs.dispatch(job.id)
    db.refresh(report)
    return {"report": S.cam(report), "job": S.job(db.get(ProcessingJob, job.id))}


@router.get("/assessments/{application_id}/cam")
def history(application_id: int, current: CurrentUser = Depends(require_any), db: Session = Depends(get_db)):
    a = get_application_for_org(db, application_id, current)
    rows = db.scalars(select(CAMReport).where(CAMReport.application_id == a.id).order_by(CAMReport.version.desc())).all()
    return {"items": [S.cam(r) for r in rows]}


@router.get("/cam/{report_id}")
def preview(report_id: int, current: CurrentUser = Depends(require_any), db: Session = Depends(get_db)):
    return S.cam(get_cam_for_org(db, report_id, current), include_content=True)


def _download(report_id: int, kind: str, request: Request, current: CurrentUser, db: Session, inline: bool = False):
    r = get_cam_for_org(db, report_id, current)
    path = r.pdf_path if kind == "pdf" else r.docx_path
    if r.status != "completed" or not path:
        raise NotFoundError("This report file is not available yet.")
    data = storage.read_bytes(path)
    a = get_application_for_org(db, r.application_id, current)
    audit.record(db, organization_id=current.org_id, user_id=current.id, action="cam.downloaded",
                 summary=f"Downloaded CAM v{r.version} ({kind.upper()})", entity_type="cam_report", entity_id=r.id,
                 application_id=r.application_id, ip_address=client_ip(request))
    db.commit()
    media = "application/pdf" if kind == "pdf" else \
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    name = f"CAM_{a.reference}_v{r.version}.{kind}"
    disp = "inline" if inline and kind == "pdf" else "attachment"
    return Response(content=data, media_type=media, headers={"content-disposition": f'{disp}; filename="{name}"',
                                                             "cache-control": "private, no-store"})


@router.get("/cam/{report_id}/pdf")
def download_pdf(report_id: int, request: Request, inline: bool = False, current: CurrentUser = Depends(require_any),
                 db: Session = Depends(get_db)):
    return _download(report_id, "pdf", request, current, db, inline)


@router.get("/cam/{report_id}/docx")
def download_docx(report_id: int, request: Request, current: CurrentUser = Depends(require_any), db: Session = Depends(get_db)):
    return _download(report_id, "docx", request, current, db)
