"""Background job orchestration.

Every unit of background work is a ``ProcessingJob`` row, so status, attempts and errors are
visible through the API regardless of which backend executes it:

* ``celery`` — a Redis-backed Celery worker (``app.workers.celery_app``) runs ``run_job``.
* ``thread`` — an in-process thread pool (default for local development).
* ``sync``   — executed inline after commit (tests).
* ``request`` — serverless hosts (Vercel): nothing runs outside a request. Jobs stay ``queued`` in PostgreSQL until
  an authenticated, organisation-scoped API call (``POST /assessments/{id}/jobs/run-next`` or ``/jobs/{id}/run``)
  claims one and executes a single attempt inside that request. The web app calls it while it shows queued work,
  so processing needs no worker, no broker and no fire-and-forget threads that a frozen function would lose.

Handlers must be idempotent: a retried or recovered job re-derives its outputs from scratch.
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from sqlalchemy import and_, func, or_, select, update
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
    if backend == "request":
        return  # stays queued; run by run_next_for_application / run_job_now inside an authenticated request
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


def _claimable(job_id: int):
    """Queued/retrying jobs with attempts left, or 'running' jobs whose runner vanished (stale)."""
    stale_before = utcnow() - timedelta(minutes=settings.JOB_STALE_MINUTES)
    return and_(ProcessingJob.id == job_id, ProcessingJob.attempts < settings.JOB_MAX_ATTEMPTS, or_(
        ProcessingJob.status.in_(("queued", "retrying")),
        and_(ProcessingJob.status == "running", ProcessingJob.started_at < stale_before)))


def run_job(job_id: int, *, max_attempts: int | None = None) -> None:
    """Execute a job with retries. Safe to call more than once for the same job.

    ``max_attempts`` limits the attempts made by this call (the request backend makes one per request and leaves
    the job ``retrying`` for the next request, so a single HTTP call never sleeps through back-offs).
    """
    from app.services import handlers  # noqa: F401  (registers handlers)

    attempts_this_call = max_attempts or settings.JOB_MAX_ATTEMPTS
    for attempt in range(attempts_this_call):
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
                .where(_claimable(job_id))
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
            used = db.scalar(select(ProcessingJob.attempts).where(ProcessingJob.id == job_id)) or attempt + 1
            final = used >= settings.JOB_MAX_ATTEMPTS
            log.exception("job attempt failed", extra={"ctx": {"job_id": job_id, "attempt": used}})
            _mark_failed(job_id, f"{type(exc).__name__}: {exc}"[:2000], final=final)
            if final or attempt + 1 >= attempts_this_call:
                return
            time.sleep(min(2 ** attempt, 8) if settings.JOB_BACKEND != "sync" else 0)
        finally:
            db.close()


RUNNABLE = ("queued", "retrying")


def _pending_query(application_id: int, organization_id: int):
    stale_before = utcnow() - timedelta(minutes=settings.JOB_STALE_MINUTES)
    return (select(ProcessingJob.id).where(
        ProcessingJob.application_id == application_id, ProcessingJob.organization_id == organization_id,
        ProcessingJob.attempts < settings.JOB_MAX_ATTEMPTS,
        or_(ProcessingJob.status.in_(RUNNABLE),
            and_(ProcessingJob.status == "running", ProcessingJob.started_at < stale_before))))


def _count_active(db: Session, application_id: int, organization_id: int) -> tuple[int, int]:
    """(runnable now, currently running) for one application."""
    runnable = db.scalar(select(func.count()).select_from(_pending_query(application_id, organization_id).subquery()))
    running = db.scalar(select(func.count(ProcessingJob.id)).where(
        ProcessingJob.application_id == application_id, ProcessingJob.organization_id == organization_id,
        ProcessingJob.status == "running"))
    return int(runnable or 0), int(running or 0)


def run_job_now(job_id: int) -> None:
    """Request backend: one attempt of one job, inside the calling request. Idempotent (atomic claim)."""
    run_job(job_id, max_attempts=1)


def run_next_for_application(db: Session, application_id: int, organization_id: int) -> dict:
    """Request backend: claim and run the oldest runnable job of an application (organisation-scoped).

    ``db`` is the caller's tenant-scoped session; it is only used to find the job. Returns what was run and how
    much work remains, so the client knows whether to call again. Other backends never run anything here.
    """
    if settings.JOB_BACKEND != "request":
        runnable, running = _count_active(db, application_id, organization_id)
        return {"mode": settings.JOB_BACKEND, "ran": None, "pending": runnable + running}
    ran = None
    for _ in range(5):  # another request may claim the candidate first; try the next one
        job_id = db.scalar(_pending_query(application_id, organization_id).order_by(ProcessingJob.id).limit(1))
        if job_id is None:
            break
        run_job_now(job_id)
        db.expire_all()
        job = db.get(ProcessingJob, job_id)
        if job is not None and job.status != "queued":
            ran = job
            break
    db.expire_all()
    runnable, running = _count_active(db, application_id, organization_id)
    return {"mode": "request", "ran": ran, "pending": runnable + running}


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
