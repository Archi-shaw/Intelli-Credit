"""Celery worker for background jobs (used when JOB_BACKEND=celery).

Run: celery -A app.workers.celery_app worker --loglevel=INFO --concurrency=2
The worker executes the same ``run_job`` as the in-process backend, so retries, idempotency and
status tracking behave identically. On startup it re-queues jobs orphaned by a crashed worker.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Celery puts the working directory on sys.path only while it imports this module and removes that entry
# afterwards (first occurrence). Always append our own copy so lazily imported packages (ai_engine, reporting)
# still resolve inside pool processes after Celery's cleanup.
_BACKEND_DIR = str(Path(__file__).resolve().parents[2])
sys.path.append(_BACKEND_DIR)

from celery import Celery  # noqa: E402
from celery.signals import worker_ready  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.logging import configure_logging  # noqa: E402
from app.services import handlers  # noqa: E402,F401  (register job handlers before forking)
from reporting.generators import docx_report, pdf_report  # noqa: E402,F401  (CAM renderers used by jobs)

configure_logging(settings.LOG_LEVEL, settings.LOG_FORMAT)

celery_app = Celery("intellicredit", broker=settings.REDIS_URL, backend=None)
celery_app.conf.update(
    task_acks_late=True,  # a job is only acknowledged after it finishes -> survives worker crashes
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_serializer="json",
    accept_content=["json"],
    broker_connection_retry_on_startup=True,
    task_default_queue="intellicredit",
)


@celery_app.task(name="intellicredit.run_job")
def run_job_task(job_id: int) -> None:
    from app.services.jobs import run_job

    run_job(job_id)


@worker_ready.connect
def _recover(**_):
    from app.services.jobs import recover_jobs

    recover_jobs()
