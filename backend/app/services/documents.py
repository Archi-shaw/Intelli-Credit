"""Document upload validation and the extraction pipeline.

upload → validate (size, extension, magic bytes) → private storage → DB record → background job →
parse (text/tables, OCR fallback) → classify → extract metrics/signals → parse bank & GST tables →
persist with page-level evidence → chunk + embed for RAG → status update → consolidate statements.
"""
from __future__ import annotations

import logging

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ai_engine.embeddings.providers import EmbeddingError
from ai_engine.extraction.classifier import classify
from ai_engine.extraction.financial_extractor import extract_document
from ai_engine.extraction.parsers import DocumentParseError, detect_kind, parse_document
from ai_engine.extraction.tabular import parse_tables
from ai_engine.ocr.engine import get_ocr_engine
from ai_engine.rag.chunker import chunk_pages
from app.core import storage
from app.core.config import settings
from app.core.database import utcnow
from app.core.errors import ConflictError, ValidationFailed
from app.models import (
    BankTransaction,
    CreditApplication,
    DocumentChunk,
    DocumentExtraction,
    DocumentSignal,
    ExtractedFinancialMetric,
    GSTRecord,
    ProcessingJob,
    Shareholding,
    UploadedDocument,
)
from app.models.document import DOCUMENT_CATEGORIES
from app.services import audit, jobs
from app.services.providers import embedder

log = logging.getLogger("intellicredit.documents")

CONTENT_TYPES = {"pdf": "application/pdf", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                 "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "csv": "text/csv",
                 "image": "image/*"}


def validate_upload(filename: str, data: bytes) -> str:
    if len(data) > settings.max_upload_bytes:
        raise ValidationFailed(f"File exceeds the {settings.MAX_UPLOAD_MB} MB upload limit.", code="file_too_large")
    try:
        return detect_kind(data, filename)
    except DocumentParseError as exc:
        raise ValidationFailed(exc.message, code=exc.code) from exc


def upload_document(db: Session, *, application: CreditApplication, filename: str, data: bytes, category: str | None,
                    user_id: int, ip: str | None = None, is_synthetic: bool = False) -> tuple[UploadedDocument, ProcessingJob]:
    safe_name = storage.sanitize_filename(filename)
    kind = validate_upload(safe_name, data)
    if category and category not in DOCUMENT_CATEGORIES and category != "auto":
        raise ValidationFailed(f"Unknown document category '{category}'.")
    digest = storage.sha256_bytes(data)
    dup = db.scalar(select(UploadedDocument.id).where(UploadedDocument.application_id == application.id,
                                                      UploadedDocument.sha256 == digest))
    if dup:
        raise ConflictError("This exact file has already been uploaded to this assessment.", code="duplicate_document",
                            details={"document_id": dup})
    ext = safe_name.rsplit(".", 1)[-1]
    rel = storage.save_bytes(f"org_{application.organization_id}/app_{application.id}", data, ext)
    try:
        doc = UploadedDocument(
            organization_id=application.organization_id, application_id=application.id,
            category=category if category and category != "auto" else "other",
            suggested_category=None, original_filename=safe_name, stored_path=rel,
            content_type=CONTENT_TYPES.get(kind), file_kind=kind, file_size=len(data), sha256=digest,
            processing_status="queued", is_synthetic=is_synthetic or application.company.is_synthetic,
            uploaded_by_id=user_id, warnings=[], detected_periods=[],
        )
        db.add(doc)
        db.flush()
        job = jobs.create_job(db, job_type="process_document", organization_id=application.organization_id,
                              application_id=application.id, document_id=doc.id, created_by_id=user_id,
                              params={"auto_classify": not category or category == "auto"})
        if application.status in ("draft", "documents_pending"):
            application.status = "processing"
        audit.record(db, organization_id=application.organization_id, user_id=user_id, action="document.uploaded",
                     summary=f"Uploaded {safe_name} ({kind}, {len(data):,} bytes)", entity_type="document",
                     entity_id=doc.id, application_id=application.id, ip_address=ip,
                     details={"sha256": digest, "category": doc.category})
    except Exception:
        storage.delete(rel)  # do not leave orphaned files behind
        raise
    return doc, job


def reprocess(db: Session, doc: UploadedDocument, user_id: int) -> ProcessingJob:
    doc.processing_status = "queued"
    doc.processing_progress = 0
    doc.error_message = None
    return jobs.create_job(db, job_type="process_document", organization_id=doc.organization_id,
                           application_id=doc.application_id, document_id=doc.id, created_by_id=user_id,
                           params={"auto_classify": False})


def delete_document(db: Session, doc: UploadedDocument) -> None:
    path = doc.stored_path
    db.delete(doc)
    db.flush()
    storage.delete(path)


def _clear_outputs(db: Session, doc_id: int) -> None:
    for model in (DocumentExtraction, DocumentChunk, ExtractedFinancialMetric, DocumentSignal, BankTransaction, GSTRecord):
        db.execute(delete(model).where(model.document_id == doc_id))


def process_document(db: Session, job: ProcessingJob) -> dict:
    doc = db.get(UploadedDocument, job.document_id)
    if doc is None:
        raise jobs.PermanentJobError("Document was deleted before processing.")
    app_ = db.get(CreditApplication, doc.application_id)

    def progress(pct: int, msg: str):
        doc.processing_progress = pct
        job.progress = pct
        job.message = msg
        db.commit()

    doc.processing_status = "processing"
    doc.extraction_status = "pending"
    progress(5, "Reading file")
    _clear_outputs(db, doc.id)  # idempotency: a retry rebuilds everything for this document

    data = storage.read_bytes(doc.stored_path)
    try:
        parsed = parse_document(data, doc.file_kind, get_ocr_engine(settings.OCR_PROVIDER, settings.OCR_LANG))
    except DocumentParseError as exc:
        raise jobs.PermanentJobError(exc.message) from exc
    doc.page_count = parsed.page_count
    doc.is_scanned = parsed.is_scanned
    doc.ocr_used = parsed.ocr_used
    doc.ocr_confidence = parsed.ocr_confidence
    progress(35, "Text recovered" + (" with OCR" if parsed.ocr_used else ""))

    full_text = "\n".join(p.text for p in parsed.pages[:5])
    cat, conf, _scores = classify(doc.original_filename, full_text)
    doc.suggested_category = cat
    doc.classification_confidence = conf
    if job.params.get("auto_classify") or doc.category == "other":
        doc.category = cat
    for p in parsed.pages:
        db.add(DocumentExtraction(document_id=doc.id, page_number=p.page_number, page_label=p.label, method=p.method,
                                  text=p.text[:200_000], tables=p.tables[:20], ocr_confidence=p.ocr_confidence,
                                  char_count=len(p.text)))
    progress(50, f"Classified as {doc.category.replace('_', ' ')}")

    result = extract_document(parsed.pages, doc.category)
    for m in result.metrics:
        db.add(ExtractedFinancialMetric(
            application_id=doc.application_id, document_id=doc.id, metric_key=m.metric_key, label=m.label[:255],
            period_label=m.period_label, period_end=m.period_end, value=m.value, text_value=m.text_value,
            raw_text=(m.raw_text or "")[:255], unit=m.unit, unit_multiplier=m.unit_multiplier, currency=m.currency,
            page_number=m.page_number, confidence=m.confidence, extraction_method=m.method[:30],
            source_excerpt=(m.excerpt or "")[:2000], flags=m.flags,
        ))
    for s in result.signals:
        db.add(DocumentSignal(application_id=doc.application_id, document_id=doc.id, signal_key=s.signal_key,
                              label=s.label[:255], area=s.area, severity=s.severity, excerpt=(s.excerpt or "")[:2000],
                              page_number=s.page_number, amount=s.amount))
    progress(65, f"{len(result.metrics)} financial value(s) extracted")

    tables = parse_tables(parsed.structured_tables, doc.category)
    for t in tables.bank_transactions:
        db.add(BankTransaction(application_id=doc.application_id, document_id=doc.id, txn_date=t.txn_date,
                               description=t.description, debit=t.debit, credit=t.credit, balance=t.balance,
                               counterparty=t.counterparty, category=t.category, row_ref=t.row_ref,
                               is_flagged=t.is_flagged, flag_reason=t.flag_reason))
    for g in tables.gst_rows:
        db.add(GSTRecord(application_id=doc.application_id, document_id=doc.id, gstin=g.gstin, return_type=g.return_type,
                         period=g.period, taxable_value=g.taxable_value, igst=g.igst, cgst=g.cgst, sgst=g.sgst,
                         cess=g.cess, total_tax=g.total_tax, itc_claimed=g.itc_claimed, itc_available=g.itc_available,
                         filing_date=g.filing_date, row_ref=g.row_ref))
    if tables.shareholding and app_ is not None:
        company = app_.company
        company.shareholdings.clear()
        for sh in tables.shareholding:
            company.shareholdings.append(Shareholding(holder_name=sh.holder_name, holder_type=sh.holder_type,
                                                      percentage=sh.percentage, shares=sh.shares,
                                                      pledged_pct=sh.pledged_pct))
    progress(80, f"{len(tables.bank_transactions)} bank transaction(s), {len(tables.gst_rows)} GST row(s) parsed")

    # RAG index. Embedding failure degrades search, it must not fail extraction.
    chunk_warning = None
    chunks = chunk_pages([(p.page_number, p.text) for p in parsed.pages])
    if chunks:
        emb = embedder()
        try:
            vectors = emb.embed([c.text for c in chunks])
            model_id = emb.model_id
        except EmbeddingError as exc:
            vectors, model_id = [None] * len(chunks), "none"
            chunk_warning = f"Semantic index unavailable ({exc}); assistant will use keyword retrieval."
        for c, v in zip(chunks, vectors):
            db.add(DocumentChunk(document_id=doc.id, application_id=doc.application_id, chunk_index=c.index,
                                 page_number=c.page_number, text=c.text, embedding=v, embedding_model=model_id))

    warnings = list(dict.fromkeys(parsed.warnings + result.warnings + tables.warnings + ([chunk_warning] if chunk_warning else [])))
    has_data = bool(result.metrics or tables.bank_transactions or tables.gst_rows or tables.shareholding)
    doc.warnings = warnings[:50]
    doc.detected_unit = result.unit.unit if result.unit and result.unit.explicit else None
    doc.detected_currency = result.unit.currency if result.unit else None
    doc.detected_periods = result.periods
    doc.extraction_status = "extracted" if has_data and not warnings else ("partial" if has_data else "no_data")
    doc.validation_status = "needs_review" if warnings or not has_data else "pending"
    doc.processing_status = "completed"
    doc.processed_at = utcnow()
    doc.error_message = None
    progress(95, "Persisting results")

    from app.services.financials import consolidate_statements

    consolidate_statements(db, doc.application_id)
    _advance_application(db, doc.application_id)
    audit.record(db, organization_id=doc.organization_id, user_id=None, action="document.processed",
                 summary=f"Processed {doc.original_filename}: {len(result.metrics)} metrics, "
                         f"{len(tables.bank_transactions)} bank txns, {len(tables.gst_rows)} GST rows",
                 entity_type="document", entity_id=doc.id, application_id=doc.application_id,
                 details={"category": doc.category, "extraction_status": doc.extraction_status})
    if app_ is not None and app_.created_by_id:
        audit.notify(db, organization_id=doc.organization_id, user_id=app_.created_by_id,
                     title="Document processed", message=f"{doc.original_filename} — {doc.extraction_status.replace('_', ' ')}",
                     level="success" if doc.extraction_status == "extracted" else "warning",
                     link=f"/assessments/{doc.application_id}?tab=documents")
    return {"metrics": len(result.metrics), "signals": len(result.signals), "bank_transactions": len(tables.bank_transactions),
            "gst_rows": len(tables.gst_rows), "pages": parsed.page_count, "category": doc.category}


def on_failure(db: Session, job: ProcessingJob, error: str) -> None:
    doc = db.get(UploadedDocument, job.document_id) if job.document_id else None
    if doc:
        doc.processing_status = "failed"
        doc.extraction_status = "failed"
        doc.error_message = error[:1000]
        audit.record(db, organization_id=doc.organization_id, user_id=None, action="document.failed",
                     summary=f"Processing failed for {doc.original_filename}", entity_type="document", entity_id=doc.id,
                     application_id=doc.application_id, details={"error": error[:300]})
        _advance_application(db, doc.application_id)


def _advance_application(db: Session, application_id: int) -> None:
    """When no document is still pending, move the assessment to the analysis stage."""
    db.flush()
    pending = db.scalar(select(func.count(UploadedDocument.id)).where(
        UploadedDocument.application_id == application_id,
        UploadedDocument.processing_status.in_(("queued", "processing"))))
    app_ = db.get(CreditApplication, application_id)
    if app_ and not pending and app_.status in ("draft", "documents_pending", "processing"):
        app_.status = "under_analysis"
        app_.current_step = max(app_.current_step, 5)
