"""Concurrent writers of an application's derived data (several documents finishing at once, API requests racing
background jobs, double-submitted runs).

Regression for a race seen in a Celery deployment and in an end-to-end run: two document jobs of the same
application consolidated its financial statements at the same moment, both inserts survived, and the duplicated
FY2023-24 statement was then read as the "prior" year (revenue growth 0 % instead of 9.15 %), lowering the risk
score and the recommended amount for identical documents.
"""
from __future__ import annotations

import threading

from sqlalchemy import delete, select

from app.core.database import system_session
from app.models import CreditApplication, FinancialStatement, RiskAssessment, UploadedDocument
from tests.conftest import make_assessment, make_borrower, upload

WAIT_S = 0.6


def _processed_application(org_a, synthetic_files) -> int:
    an = org_a["analyst"]
    ap = make_assessment(an, make_borrower(an)["id"])
    r = upload(an, ap["id"], synthetic_files["inconsistent"]["financial_statements"])
    assert r.status_code == 201, r.text
    return ap["id"]


def _periods(app_id: int) -> list[str]:
    with system_session() as s:
        return sorted(s.scalars(select(FinancialStatement.period_label)
                                .where(FinancialStatement.application_id == app_id)).all())


def _race(first_step, second_step):
    """Run first_step in an open transaction, start second_step concurrently, then commit the first.

    Returns (second_was_blocked, errors_from_second)."""
    first = system_session()
    first_step(first)
    done, errors = threading.Event(), []

    def second():
        s = system_session()
        try:
            second_step(s)
            s.commit()
        except Exception as exc:  # noqa: BLE001 - surfaced through the assertion
            s.rollback()
            errors.append(exc)
        finally:
            s.close()
            done.set()

    t = threading.Thread(target=second)
    t.start()
    blocked = not done.wait(WAIT_S)
    first.commit()
    first.close()
    t.join(30)
    assert not t.is_alive()
    return blocked, errors


def test_concurrent_consolidation_never_duplicates_statements(org_a, synthetic_files):
    from app.services.financials import consolidate_statements, latest_and_prior

    app_id = _processed_application(org_a, synthetic_files)
    expected = _periods(app_id)
    assert expected == ["FY2022-23", "FY2023-24"]
    with system_session() as s:  # the state just before the first document's consolidation commits
        s.execute(delete(FinancialStatement).where(FinancialStatement.application_id == app_id))
        s.commit()

    blocked, errors = _race(lambda s: consolidate_statements(s, app_id), lambda s: consolidate_statements(s, app_id))

    assert not errors, errors
    assert blocked, "a second consolidation must wait for the first to commit"
    assert _periods(app_id) == expected, "each period must be stored exactly once"
    with system_session() as s:
        latest, prior, _ = latest_and_prior(s, app_id)
        assert (latest.period_label, prior.period_label) == ("FY2023-24", "FY2022-23")


def test_statements_already_duplicated_are_read_once(org_a, synthetic_files):
    """Databases that hit the race before the fix keep working: a duplicated period is never read as the prior year."""
    from app.services.financials import latest_and_prior, statements_by_period

    app_id = _processed_application(org_a, synthetic_files)
    with system_session() as s:
        for st in s.scalars(select(FinancialStatement).where(FinancialStatement.application_id == app_id)).all():
            s.add(FinancialStatement(application_id=app_id, period_label=st.period_label, period_end=st.period_end,
                                     currency=st.currency, line_items=st.line_items,
                                     source_document_ids=st.source_document_ids, conflicts=st.conflicts))
        s.commit()
    assert _periods(app_id) == ["FY2022-23", "FY2022-23", "FY2023-24", "FY2023-24"]
    with system_session() as s:
        assert [st.period_label for st in statements_by_period(s, app_id)] == ["FY2022-23", "FY2023-24"]
        latest, prior, _ = latest_and_prior(s, app_id)
        assert (latest.period_label, prior.period_label) == ("FY2023-24", "FY2022-23")


def test_document_is_completed_only_together_with_its_statements(org_a, synthetic_files, monkeypatch):
    """Pollers must never see a document as completed before its consolidated statements are committed."""
    import app.services.financials as fin

    original, seen = fin.consolidate_statements, []

    def spy(db, application_id):
        with system_session() as other:  # what another transaction (a poller, another worker) can see now
            seen.extend(other.scalars(select(UploadedDocument.processing_status)
                                      .where(UploadedDocument.application_id == application_id)).all())
        return original(db, application_id)

    monkeypatch.setattr(fin, "consolidate_statements", spy)
    app_id = _processed_application(org_a, synthetic_files)
    assert seen == ["processing"]
    with system_session() as s:
        assert s.scalars(select(UploadedDocument.processing_status)
                         .where(UploadedDocument.application_id == app_id)).all() == ["completed"]


def test_concurrent_assessment_runs_get_consecutive_versions(org_a, synthetic_files):
    from app.services.analysis import run_assessment

    app_id = _processed_application(org_a, synthetic_files)

    def run(s):
        run_assessment(s, s.get(CreditApplication, app_id), None)

    blocked, errors = _race(run, run)

    assert not errors, errors
    assert blocked
    with system_session() as s:
        versions = s.scalars(select(RiskAssessment.version).where(RiskAssessment.application_id == app_id)).all()
    assert sorted(versions) == [1, 2]


def test_concurrent_reconciliation_runs_do_not_share_a_run_number(org_a, synthetic_files):
    from app.models import Reconciliation
    from app.services.analysis import run_reconciliation_for

    app_id = _processed_application(org_a, synthetic_files)

    def run(s):
        run_reconciliation_for(s, s.get(CreditApplication, app_id), None)

    blocked, errors = _race(run, run)

    assert not errors, errors
    assert blocked
    with system_session() as s:
        runs = s.scalars(select(Reconciliation.run_number).where(Reconciliation.application_id == app_id)).all()
    assert sorted(set(runs)) == [1, 2] and runs.count(1) == runs.count(2)
