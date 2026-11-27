"""Celery worker module: import-path robustness inside forked pool processes."""
import importlib
import os
import sys
from pathlib import Path

BACKEND = str(Path(__file__).resolve().parents[1])


def test_worker_keeps_backend_importable_after_celery_path_cleanup(monkeypatch):
    from celery.utils.imports import cwd_in_path

    saved = list(sys.path)
    try:
        sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != BACKEND]
        monkeypatch.chdir(BACKEND)
        sys.modules.pop("app.workers.celery_app", None)
        with cwd_in_path():  # exactly what `celery -A app.workers.celery_app worker` does
            importlib.import_module("app.workers.celery_app")
        assert BACKEND in [os.path.abspath(p) for p in sys.path if p], "backend dir must survive Celery's cleanup"
        for mod in [m for m in sys.modules if m == "reporting" or m.startswith("reporting.")]:
            sys.modules.pop(mod)
        importlib.import_module("reporting.generators.pdf_report")  # lazily imported by the CAM job
    finally:
        sys.path[:] = saved


def test_worker_registers_job_handlers():
    from app.services import jobs
    from app.workers.celery_app import celery_app, run_job_task

    assert {"process_document", "generate_cam"} <= set(jobs._HANDLERS)
    assert run_job_task.name == "intellicredit.run_job"
    assert celery_app.conf.task_acks_late and celery_app.conf.task_reject_on_worker_lost
