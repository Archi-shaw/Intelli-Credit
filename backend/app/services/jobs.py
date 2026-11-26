"""Background job orchestration.

Every unit of background work is a ``ProcessingJob`` row, so status, attempts and errors are
visible through the API regardless of which backend executes it:

* ``celery`` — a Redis-backed Celery worker (``app.workers.celery_app``) runs ``run_job``.
* ``thread`` — an in-process thread pool (default for local development).
* ``sync``   — executed inline after commit (tests).

Handlers must be idempotent: a retried or recovered job re-derives its outputs from scratch.
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal, utcnow
from app.models import ProcessingJob

log = logging.getLogger("intellicredit.jobs")

JobHandler = Callable[[Session, ProcessingJob], dict | None]
_HANDLERS: dict[str, JobHandler] = {}
_executor: ThreadPoolExecutor | None = None
_executor_lock = threading.Lock()


class PermanentJobError(Exception):
    """Raised by a handler when retrying cannot help (corrupt file, unsupported type...)."""


def register(job_type: str):
    def deco(fn: JobHandler) -> JobHandler:
        _HANDLERS[job_type] = fn
        return fn

    return deco


def _pool() -> ThreadPoolExecutor:
    global _executor
    with _executor_lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=max(1, settings.JOB_THREAD_WORKERS), thread_name_prefix="ic-job")
        return _executor


def create_job(db: Session, *, job_type: str, organization_id: int, application_id: int | None = None,
               document_id: int | None = None, params: dict | None = None, created_by_id: int | None = None) -> ProcessingJob:
    from app.services import handlers  # noqa: F401  (registers handlers)

    if job_type not in _HANDLERS:
        raise ValueError(f"Unknown job type {job_type}")
    job = ProcessingJob(job_type=job_type, organization_id=organization_id, application_id=application_id,
                        document_id=document_id, params=params or {}, created_by_id=created_by_id, status="queued",
                        message="Queued")
    db.add(job)
    db.flush()
    return job


def dispatch(job_id: int) -> None:
    """Hand a committed job to the configured backend."""
    backend = settings.JOB_BACKEND
    if backend == "sync":
        run_job(job_id)
    elif backend == "celery":
        try:
            from app.workers.celery_app import run_job_task

            run_job_task.delay(job_id)
        except Exception as exc:  # broker unavailable -> fall back so work is never silently lost
            log.error("Celery dispatch failed (%s); running job %s in-process", exc, job_id)
            _pool().submit(run_job, job_id)
    else:
        _pool().submit(run_job, job_id)


def run_job(job_id: int) -> None:
    """Execute a job with retries. Safe to call more than once for the same job."""
    from app.services import handlers  # noqa: F401  (registers handlers)

    for attempt in range(settings.JOB_MAX_ATTEMPTS):
        db = SessionLocal()
        db.info["rls_bypass"] = True
        try:
            job = db.get(ProcessingJob, job_id)
            if job is None:
                log.warning("Job %s no longer exists", job_id)
                return
            # Atomic claim: only one worker may move a job from queued/retrying to running.
            claimed = db.execute(
                update(ProcessingJob)
                .where(ProcessingJob.id == job_id, ProcessingJob.status.in_(("queued", "retrying")),
                       ProcessingJob.attempts < settings.JOB_MAX_ATTEMPTS)
                .values(status="running", attempts=ProcessingJob.attempts + 1, started_at=utcnow(), error=None,
                        message="Running")
            ).rowcount
            db.commit()
            if not claimed:
                return  # already completed, failed permanently, or being run by another worker
            db.refresh(job)
            handler = _HANDLERS[job.job_type]
            result = handler(db, job)
            job.status = "completed"
            job.progress = 100
            job.result = result or {}
            job.message = "Completed"
            job.finished_at = utcnow()
            db.commit()
            log.info("job completed", extra={"ctx": {"job_id": job_id, "job_type": job.job_type}})
            return
        except PermanentJobError as exc:
            db.rollback()
            _mark_failed(job_id, str(exc), final=True)
            return
        except Exception as exc:  # noqa: BLE001 - every failure must be recorded on the job row
            db.rollback()
            final = attempt + 1 >= settings.JOB_MAX_ATTEMPTS
            log.exception("job attempt failed", extra={"ctx": {"job_id": job_id, "attempt": attempt + 1}})
            _mark_failed(job_id, f"{type(exc).__name__}: {exc}"[:2000], final=final)
            if final:
                return
            time.sleep(min(2 ** attempt, 8) if settings.JOB_BACKEND != "sync" else 0)
        finally:
            db.close()


def _mark_failed(job_id: int, error: str, *, final: bool) -> None:
    from app.services import handlers

    db = SessionLocal()
    db.info["rls_bypass"] = True
    try:
        job = db.get(ProcessingJob, job_id)
        if not job:
            return
        job.error = error
        job.status = "failed" if final else "retrying"
        job.message = "Failed" if final else "Retrying after error"
        if final:
            job.finished_at = utcnow()
            on_fail = getattr(handlers, f"on_failure_{job.job_type}", None)
            if on_fail:
                on_fail(db, job, error)
        db.commit()
    finally:
        db.close()


def recover_jobs() -> int:
    """Re-dispatch jobs left queued/running by a crashed or restarted worker."""
    db = SessionLocal()
    db.info["rls_bypass"] = True
    try:
        stale_before = utcnow() - timedelta(minutes=settings.JOB_STALE_MINUTES)
        jobs = db.scalars(select(ProcessingJob).where(or_(
            ProcessingJob.status.in_(("queued", "retrying")),
            (ProcessingJob.status == "running") & (ProcessingJob.started_at < stale_before),
        ))).all()
        ids = []
        for job in jobs:
            if job.attempts >= settings.JOB_MAX_ATTEMPTS:
                job.status, job.message = "failed", "Abandoned after maximum attempts"
                job.finished_at = utcnow()
            else:
                job.status, job.message = "queued", "Re-queued after worker restart"
                ids.append(job.id)
        db.commit()
    finally:
        db.close()
    for jid in ids:
        dispatch(jid)
    if ids:
        log.info("recovered %d background job(s)", len(ids))
    return len(ids)


def shutdown() -> None:
    global _executor
    with _executor_lock:
        if _executor is not None:
            _executor.shutdown(wait=False, cancel_futures=False)
            _executor = None
