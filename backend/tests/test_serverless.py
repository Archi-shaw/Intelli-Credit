"""Serverless mode (Vercel): request-driven jobs, database file storage, PostgreSQL rate limits and lockout.

Runs the real processing pipeline (extraction, reconciliation, risk assessment, CAM rendering) with
JOB_BACKEND=request, STORAGE_BACKEND=database and RATE_LIMIT_BACKEND=postgres — no worker, no Redis, no disk.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

import pytest

from tests.conftest import PASSWORD, make_assessment, make_borrower, upload


@pytest.fixture
def serverless(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "JOB_BACKEND", "request")
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "database")
    return settings


def _drain(api, app_id: int, limit: int = 20) -> list[dict]:
    ran = []
    for _ in range(limit):
        r = api.post(f"/api/v1/assessments/{app_id}/jobs/run-next")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["mode"] == "request"
        if body["ran"]:
            ran.append(body["ran"])
        if body["pending"] == 0:
            return ran
    raise AssertionError("jobs still pending after draining")


def _db():
    from app.core.database import system_session

    return system_session()


def test_full_workflow_runs_inside_requests_with_database_storage(org_a, org_b, synthetic_files, serverless):
    from sqlalchemy import func, select

    from app.core.config import settings
    from app.models import ProcessingJob, StoredFile, UploadedDocument

    api = org_a["analyst"]
    a = make_assessment(api, make_borrower(api)["id"])
    files = synthetic_files["healthy"]
    for cat in ("financial_statements", "gst_returns", "bank_statements"):
        r = upload(api, a["id"], files[cat])
        assert r.status_code == 201, r.text
        assert r.json()["job"]["status"] == "queued"  # nothing ran outside a request

    db = _db()
    try:
        assert db.scalar(select(func.count(ProcessingJob.id)).where(ProcessingJob.status == "queued")) == 3
        assert db.scalar(select(func.count(StoredFile.path))) == 3  # bytes in PostgreSQL ...
        paths = db.scalars(select(UploadedDocument.stored_path).where(UploadedDocument.application_id == a["id"])).all()
    finally:
        db.close()
    assert len(paths) == 3 and not any((Path(settings.STORAGE_DIR) / p).exists() for p in paths)  # ... not on disk

    # Another organisation can neither run nor see the jobs.
    other = org_b["admin"]
    assert other.post(f"/api/v1/assessments/{a['id']}/jobs/run-next").status_code == 404
    db = _db()
    try:
        job_id = db.scalar(select(ProcessingJob.id).where(ProcessingJob.application_id == a["id"]).limit(1))
    finally:
        db.close()
    assert other.post(f"/api/v1/jobs/{job_id}/run").status_code == 404
    assert other.get(f"/api/v1/jobs/{job_id}").status_code == 404

    ran = _drain(api, a["id"])
    assert len(ran) == 3 and all(j["status"] == "completed" and j["attempts"] == 1 for j in ran)
    docs = api.get(f"/api/v1/assessments/{a['id']}/documents").json()["items"]
    assert {d["processing_status"] for d in docs} == {"completed"}
    fs = next(d for d in docs if d["category"] == "financial_statements")
    original = files["financial_statements"].read_bytes()
    assert api.get(f"/api/v1/documents/{fs['id']}/file").content == original
    assert len(api.get(f"/api/v1/documents/{fs['id']}/extraction").json()["metrics"]) > 5

    assert api.post(f"/api/v1/assessments/{a['id']}/reconciliation").status_code == 200
    risk = api.post(f"/api/v1/assessments/{a['id']}/risk-assessment")
    assert risk.status_code in (200, 201), risk.text
    assert set(risk.json()["assessment"]["five_cs"]) >= {"character", "capacity", "capital", "collateral", "conditions"}

    gen = api.post(f"/api/v1/assessments/{a['id']}/cam")
    assert gen.status_code == 202 and gen.json()["job"]["status"] == "queued"
    ran = _drain(api, a["id"])
    assert [j["job_type"] for j in ran] == ["generate_cam"]
    report = api.get(f"/api/v1/assessments/{a['id']}/cam").json()["items"][0]
    assert report["status"] == "completed"
    assert api.get(f"/api/v1/cam/{report['id']}/pdf").content[:4] == b"%PDF"
    assert api.get(f"/api/v1/cam/{report['id']}/docx").content[:2] == b"PK"

    # Nothing left: repeated calls are harmless no-ops.
    again = api.post(f"/api/v1/assessments/{a['id']}/jobs/run-next").json()
    assert again == {"mode": "request", "pending": 0, "ran": None}

    # Deleting a document (allowed before analysis) removes its stored bytes.
    b = make_assessment(api, make_borrower(api, name="Second Traders Pvt Ltd", pan="AAACS9999Q",
                                              gstin="27AAACS9999Q1Z1")["id"])
    doc = upload(api, b["id"], files["gst_returns"]).json()["document"]
    db = _db()
    try:
        path = db.scalar(select(UploadedDocument.stored_path).where(UploadedDocument.id == doc["id"]))
    finally:
        db.close()
    assert api.delete(f"/api/v1/documents/{doc['id']}").status_code in (200, 204)
    db = _db()
    try:
        assert db.scalar(select(func.count(StoredFile.path)).where(StoredFile.path == path)) == 0
    finally:
        db.close()


def test_concurrent_requests_never_run_a_job_twice(org_a, synthetic_files, serverless, monkeypatch):
    from app.models import ProcessingJob
    from app.services import documents, jobs

    api = org_a["analyst"]
    a = make_assessment(api, make_borrower(api)["id"])
    job_id = upload(api, a["id"], synthetic_files["healthy"]["gst_returns"]).json()["job"]["id"]
    calls, lock = [], threading.Lock()

    def counting(db, job):
        with lock:
            calls.append(job.id)
        return documents.process_document(db, job)

    monkeypatch.setitem(jobs._HANDLERS, "process_document", counting)
    with ThreadPoolExecutor(6) as pool:
        list(pool.map(lambda _: jobs.run_job_now(job_id), range(6)))
    assert calls == [job_id]
    db = _db()
    try:
        job = db.get(ProcessingJob, job_id)
        assert job.status == "completed" and job.attempts == 1
    finally:
        db.close()


def test_one_attempt_per_request_then_failed_and_stale_jobs_are_reclaimed(org_a, serverless, monkeypatch):
    from app.core.database import utcnow
    from app.models import ProcessingJob
    from app.services import jobs

    org_id = org_a["admin"].data["organization"]["id"]
    monkeypatch.setitem(jobs._HANDLERS, "test_flaky", lambda db, job: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setitem(jobs._HANDLERS, "test_ok", lambda db, job: {"ok": True})
    db = _db()
    try:
        flaky = jobs.create_job(db, job_type="test_flaky", organization_id=org_id)
        stale = jobs.create_job(db, job_type="test_ok", organization_id=org_id)
        stale.status, stale.attempts, stale.started_at = "running", 1, utcnow() - timedelta(minutes=120)
        fresh = jobs.create_job(db, job_type="test_ok", organization_id=org_id)
        fresh.status, fresh.attempts, fresh.started_at = "running", 1, utcnow()
        db.commit()
        ids = flaky.id, stale.id, fresh.id
    finally:
        db.close()

    from app.core.config import settings

    for n in range(1, settings.JOB_MAX_ATTEMPTS + 1):
        jobs.run_job_now(ids[0])
        db = _db()
        try:
            j = db.get(ProcessingJob, ids[0])
            assert j.attempts == n and "boom" in j.error
            assert j.status == ("failed" if n == settings.JOB_MAX_ATTEMPTS else "retrying")
        finally:
            db.close()
    jobs.run_job_now(ids[0])  # no attempts left: nothing happens
    jobs.run_job_now(ids[1])  # runner vanished two hours ago -> reclaimed
    jobs.run_job_now(ids[2])  # still owned by a live request -> untouched
    db = _db()
    try:
        assert db.get(ProcessingJob, ids[0]).attempts == settings.JOB_MAX_ATTEMPTS
        assert (db.get(ProcessingJob, ids[1]).status, db.get(ProcessingJob, ids[1]).attempts) == ("completed", 2)
        assert db.get(ProcessingJob, ids[2]).status == "running"
    finally:
        db.close()


def test_worker_backends_report_mode_without_running(org_a, synthetic_files, monkeypatch):
    from app.core.config import settings

    api = org_a["analyst"]
    a = make_assessment(api, make_borrower(api)["id"])
    upload(api, a["id"], synthetic_files["healthy"]["gst_returns"])  # sync backend: already processed
    body = api.post(f"/api/v1/assessments/{a['id']}/jobs/run-next").json()
    assert body == {"mode": settings.JOB_BACKEND, "pending": 0, "ran": None}


def test_postgres_rate_limit_is_atomic_and_all_or_nothing(db_clean):
    from app.core.ratelimit import Bucket, PostgresBackend, parse_limit

    pg = PostgresBackend()
    bucket = Bucket("t:atomic", parse_limit("20/minute"), "test")
    with ThreadPoolExecutor(16) as pool:
        decisions = list(pool.map(lambda _: pg.check_sync([bucket]), range(80)))
    assert sum(d.allowed for d in decisions) == 20
    denied = next(d for d in decisions if not d.allowed)
    assert denied.retry_after_s >= 1 and denied.scope == "test"

    a, b = Bucket("t:a", parse_limit("1/minute"), "a"), Bucket("t:b", parse_limit("5/minute"), "b")
    assert pg.check_sync([a, b]).allowed
    d = pg.check_sync([a, b])
    assert not d.allowed and d.scope == "a"
    assert pg.check_sync([b]).remaining == 3  # the denied request consumed nothing from b


def test_rate_limits_enforced_through_postgres(client, org_a, monkeypatch):
    from app.core.config import settings
    from app.core.ratelimit import limiter

    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(settings, "RATE_LIMIT_BACKEND", "postgres")
    monkeypatch.setattr(settings, "RATE_LIMIT_USER", "5/minute")
    monkeypatch.setattr(limiter, "redis", None)
    limiter.configure()
    try:
        codes = [org_a["analyst"].get("/api/v1/borrowers").status_code for _ in range(7)]
        assert codes[:5] == [200] * 5 and codes[5:] == [429, 429]
        assert type(limiter.redis).__name__ == "PostgresBackend"
    finally:
        limiter.redis = None
        limiter.configure()


def test_login_lockout_shared_through_postgres(client, org_a, monkeypatch):
    from sqlalchemy import func, select

    from app.core.config import settings
    from app.core.security import login_throttle
    from app.models import LoginFailure

    monkeypatch.setattr(settings, "RATE_LIMIT_BACKEND", "postgres")
    for _ in range(5):
        r = client.post("/api/v1/auth/login", json={"email": "analyst@alpha.example", "password": "wrong-password-1"})
        assert r.status_code == 401
    assert login_throttle._failures == {}  # nothing tracked in process memory
    db = _db()
    try:
        assert db.scalar(select(func.count(LoginFailure.id))) == 5
    finally:
        db.close()
    locked = client.post("/api/v1/auth/login", json={"email": "analyst@alpha.example", "password": PASSWORD})
    assert locked.status_code == 401 and locked.json()["error"]["code"] == "login_locked"


def test_vercel_refuses_disk_storage_and_background_threads(monkeypatch):
    from app.core.config import Settings

    monkeypatch.setenv("VERCEL", "1")
    bad = Settings(JOB_BACKEND="thread", STORAGE_BACKEND="local", RATE_LIMIT_BACKEND="redis")
    problems = " ".join(bad.production_problems())
    assert "STORAGE_BACKEND" in problems and "JOB_BACKEND" in problems and "RATE_LIMIT_BACKEND" in problems
    good = Settings(JOB_BACKEND="request", STORAGE_BACKEND="database", RATE_LIMIT_BACKEND="postgres")
    assert good.production_problems() == []
