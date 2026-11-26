"""Registers background-job handlers (imported by the job runner and the Celery worker)."""
from __future__ import annotations

from app.services import cam, documents, jobs

jobs.register("process_document")(documents.process_document)
jobs.register("generate_cam")(cam.generate_cam)

on_failure_process_document = documents.on_failure
on_failure_generate_cam = cam.on_failure
