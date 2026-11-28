"""Document upload, listing, extraction results, secure download and deletion."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import serializers as S
from app.core import storage
from app.core.config import settings
from app.core.database import get_db, lock_application, utcnow
from app.core.deps import (
    CurrentUser,
    client_ip,
    get_application_for_org,
    get_document_for_org,
    require_analyst,
    require_any,
)
from app.core.errors import NotFoundError, PreconditionFailed, ValidationFailed
from app.models import (
    BankTransaction,
    DocumentExtraction,
    DocumentSignal,
    ExtractedFinancialMetric,
    GSTRecord,
    ProcessingJob,
    UploadedDocument,
)
from app.schemas.requests import MetricCorrectionIn
from app.services import audit, documents, jobs
from app.services.financials import consolidate_statements

router = APIRouter(tags=["documents"])

PREVIEW_TYPES = {"pdf": "application/pdf", "image": None, "csv": "text/csv; charset=utf-8"}


async def _read_limited(upload: UploadFile) -> bytes:
    limit = settings.max_upload_bytes
    chunks, size = [], 0
    while True:
        chunk = await upload.read(1024 * 1024)
        if not chunk:
            break
        size += len(chunk)
        if size > limit:
            raise ValidationFailed(f"File exceeds the {settings.MAX_UPLOAD_MB} MB upload limit.", code="file_too_large")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/assessments/{application_id}/documents", status_code=201)
async def upload(application_id: int, request: Request, file: UploadFile = File(...), category: str = Form(default="auto"),
                 current: CurrentUser = Depends(require_analyst), db: Session = Depends(get_db)):
    a = get_application_for_org(db, application_id, current)
    if a.status in ("approved", "rejected"):
        raise PreconditionFailed("This assessment is closed; documents can no longer be added.")
    data = await _read_limited(file)
    doc, job = documents.upload_document(db, application=a, filename=file.filename or "upload", data=data,
                                         category=category, user_id=current.id, ip=client_ip(request))
    a.current_step = max(a.current_step, 4)
    db.commit()
    jobs.dispatch(job.id)
    db.refresh(doc)
    return {"document": S.document(doc), "job": S.job(db.get(ProcessingJob, job.id))}


def _doc_stats(db: Session, doc_ids: list[int]) -> dict[int, tuple[int, float | None]]:
    if not doc_ids:
        return {}
    rows = db.execute(select(ExtractedFinancialMetric.document_id, func.count(ExtractedFinancialMetric.id),
                             func.avg(ExtractedFinancialMetric.confidence))
                      .where(ExtractedFinancialMetric.document_id.in_(doc_ids))
                      .group_by(ExtractedFinancialMetric.document_id)).all()
    return {d: (n, round(float(avg), 3) if avg is not None else None) for d, n, avg in rows}


@router.get("/assessments/{application_id}/documents")
def list_documents(application_id: int, current: CurrentUser = Depends(require_any), db: Session = Depends(get_db)):
    a = get_application_for_org(db, application_id, current)
    docs = db.scalars(select(UploadedDocument).where(UploadedDocument.application_id == a.id).order_by(UploadedDocument.id)).all()
    stats = _doc_stats(db, [d.id for d in docs])
    return {"items": [S.document(d, metric_count=stats.get(d.id, (0, None))[0], avg_confidence=stats.get(d.id, (0, None))[1])
                      for d in docs]}


@router.get("/documents/{document_id}")
def get_document(document_id: int, current: CurrentUser = Depends(require_any), db: Session = Depends(get_db)):
    d = get_document_for_org(db, document_id, current)
    n, avg = _doc_stats(db, [d.id]).get(d.id, (0, None))
    job = db.scalar(select(ProcessingJob).where(ProcessingJob.document_id == d.id).order_by(ProcessingJob.id.desc()))
    out = S.document(d, metric_count=n, avg_confidence=avg)
    out["latest_job"] = S.job(job) if job else None
    out["previewable"] = d.file_kind in ("pdf", "image", "csv")
    return out


@router.get("/documents/{document_id}/status")
def document_status(document_id: int, current: CurrentUser = Depends(require_any), db: Session = Depends(get_db)):
    d = get_document_for_org(db, document_id, current)
    job = db.scalar(select(ProcessingJob).where(ProcessingJob.document_id == d.id).order_by(ProcessingJob.id.desc()))
    return {"processing_status": d.processing_status, "extraction_status": d.extraction_status,
            "progress": d.processing_progress, "error": d.error_message, "job": S.job(job) if job else None}


@router.get("/documents/{document_id}/extraction")
def document_extraction(document_id: int, current: CurrentUser = Depends(require_any), db: Session = Depends(get_db),
                        include_text: bool = False):
    d = get_document_for_org(db, document_id, current)
    metrics = db.scalars(select(ExtractedFinancialMetric).where(ExtractedFinancialMetric.document_id == d.id)
                         .order_by(ExtractedFinancialMetric.period_label, ExtractedFinancialMetric.page_number)).all()
    signals = db.scalars(select(DocumentSignal).where(DocumentSignal.document_id == d.id)).all()
    pages = db.scalars(select(DocumentExtraction).where(DocumentExtraction.document_id == d.id)
                       .order_by(DocumentExtraction.page_number)).all()
    txn_count = db.scalar(select(func.count(BankTransaction.id)).where(BankTransaction.document_id == d.id))
    gst = db.scalars(select(GSTRecord).where(GSTRecord.document_id == d.id).order_by(GSTRecord.period)).all()
    txns = db.scalars(select(BankTransaction).where(BankTransaction.document_id == d.id)
                      .order_by(BankTransaction.txn_date).limit(500)).all()
    return {
        "document": S.document(d), "metrics": [S.metric(m) for m in metrics], "signals": [S.signal(s) for s in signals],
        "pages": [{"page_number": p.page_number, "label": p.page_label, "method": p.method, "char_count": p.char_count,
                   "ocr_confidence": p.ocr_confidence, "table_count": len(p.tables or []),
                   **({"text": p.text[:20000]} if include_text else {})} for p in pages],
        "bank_transactions": {"total": txn_count, "items": [S.bank_txn(t) for t in txns]},
        "gst_records": [S.gst_row(g) for g in gst],
    }


@router.get("/documents/{document_id}/file")
def download_document(document_id: int, request: Request, inline: bool = False,
                      current: CurrentUser = Depends(require_any), db: Session = Depends(get_db)):
    d = get_document_for_org(db, document_id, current)
    try:
        data = storage.read_bytes(d.stored_path)
    except (ValueError, OSError) as exc:
        raise NotFoundError("Stored file is unavailable.") from exc
    media = d.content_type or "application/octet-stream"
    if d.file_kind == "image":
        media = "image/png" if data[:4] == b"\x89PNG" else "image/jpeg"
    disposition = "inline" if inline and d.file_kind in PREVIEW_TYPES else "attachment"
    audit.record(db, organization_id=current.org_id, user_id=current.id, action="document.downloaded",
                 summary=f"{'Viewed' if disposition == 'inline' else 'Downloaded'} {d.original_filename}",
                 entity_type="document", entity_id=d.id, application_id=d.application_id, ip_address=client_ip(request))
    db.commit()
    safe = d.original_filename.replace('"', "")
    return Response(content=data, media_type=media, headers={
        "content-disposition": f'{disposition}; filename="{safe}"', "cache-control": "private, no-store",
        "x-content-type-options": "nosniff", "content-security-policy": "default-src 'none'; sandbox"})


@router.post("/documents/{document_id}/reprocess")
def reprocess_document(document_id: int, request: Request, current: CurrentUser = Depends(require_analyst),
                       db: Session = Depends(get_db)):
    d = get_document_for_org(db, document_id, current)
    if d.processing_status in ("queued", "processing"):
        raise PreconditionFailed("The document is already being processed.")
    job = documents.reprocess(db, d, current.id)
    audit.record(db, organization_id=current.org_id, user_id=current.id, action="document.reprocess",
                 summary=f"Re-processing {d.original_filename}", entity_type="document", entity_id=d.id,
                 application_id=d.application_id, ip_address=client_ip(request))
    db.commit()
    jobs.dispatch(job.id)
    return {"job": S.job(job)}


@router.delete("/documents/{document_id}")
def delete_document(document_id: int, request: Request, current: CurrentUser = Depends(require_analyst),
                    db: Session = Depends(get_db)):
    d = get_document_for_org(db, document_id, current)
    if d.application.status in ("approved", "rejected", "awaiting_review"):
        raise PreconditionFailed("Documents cannot be deleted once an assessment is under review or decided.")
    app_id, name = d.application_id, d.original_filename
    lock_application(db, app_id)  # before any write: the delete and the re-consolidation are one serialised step
    documents.delete_document(db, d)
    consolidate_statements(db, app_id)
    audit.record(db, organization_id=current.org_id, user_id=current.id, action="document.deleted",
                 summary=f"Deleted {name}", entity_type="document", entity_id=document_id, application_id=app_id,
                 ip_address=client_ip(request))
    db.commit()
    return {"ok": True}


@router.patch("/metrics/{metric_id}")
def correct_metric(metric_id: int, body: MetricCorrectionIn, request: Request, current: CurrentUser = Depends(require_analyst),
                   db: Session = Depends(get_db)):
    m = db.get(ExtractedFinancialMetric, metric_id)
    if not m:
        raise NotFoundError("Metric not found.")
    a = get_application_for_org(db, m.application_id, current)
    if a.status in ("approved", "rejected"):
        raise PreconditionFailed("This assessment is closed.")
    if body.verification_status == "corrected" and body.corrected_value is None:
        raise ValidationFailed("A corrected value is required when marking a figure as corrected.")
    old = m.effective_value
    m.verification_status = "verified" if body.verification_status == "corrected" else body.verification_status
    m.corrected_value = body.corrected_value if body.verification_status == "corrected" else None
    m.correction_note = body.correction_note
    m.corrected_by_id = current.id
    m.corrected_at = utcnow()
    consolidate_statements(db, a.id)
    audit.record(db, organization_id=current.org_id, user_id=current.id, action="metric.reviewed",
                 summary=f"{m.label} ({m.period_label}): {body.verification_status}"
                         + (f" {old} → {body.corrected_value}" if body.verification_status == "corrected" else ""),
                 entity_type="metric", entity_id=m.id, application_id=a.id, ip_address=client_ip(request),
                 details={"old_value": old, "new_value": m.effective_value, "note": body.correction_note})
    db.commit()
    return S.metric(m)


@router.get("/jobs/{job_id}")
def get_job(job_id: int, current: CurrentUser = Depends(require_any), db: Session = Depends(get_db)):
    j = db.get(ProcessingJob, job_id)
    if not j or j.organization_id != current.org_id:
        raise NotFoundError("Job not found.")
    return S.job(j)


@router.post("/assessments/{application_id}/jobs/run-next")
def run_next_job(application_id: int, current: CurrentUser = Depends(require_any), db: Session = Depends(get_db)):
    """Serverless processing (JOB_BACKEND=request): run the application's oldest queued job inside this request.

    The web app calls this while it shows queued or processing work and polls the usual status endpoints.
    Organisation-scoped (other organisations get 404) and idempotent: a job is claimed atomically, so concurrent
    calls never run it twice. With worker backends (celery, thread) it only reports the pending count.
    """
    a = get_application_for_org(db, application_id, current)
    out = jobs.run_next_for_application(db, a.id, current.org_id)
    return {"mode": out["mode"], "pending": out["pending"], "ran": S.job(out["ran"]) if out["ran"] else None}


@router.post("/jobs/{job_id}/run")
def run_job_request(job_id: int, current: CurrentUser = Depends(require_any), db: Session = Depends(get_db)):
    """Serverless processing: run one attempt of a specific job now (no-op if it already ran or is running)."""
    j = db.get(ProcessingJob, job_id)
    if not j or j.organization_id != current.org_id:
        raise NotFoundError("Job not found.")
    if settings.JOB_BACKEND == "request":
        jobs.run_job_now(j.id)
        db.expire_all()
        j = db.get(ProcessingJob, job_id)
    return {"mode": settings.JOB_BACKEND, "job": S.job(j)}
