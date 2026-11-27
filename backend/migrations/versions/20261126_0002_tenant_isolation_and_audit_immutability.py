"""Row-level security for tenant isolation; append-only audit and review tables; app role grants.

The API and worker should connect as the non-superuser role ``DB_APP_ROLE`` (created with
``python -m app.cli create-app-role``). Superusers and table owners bypass RLS by design, so
``FORCE ROW LEVEL SECURITY`` is set and the app must not use the owner account in production.

Each request sets ``app.current_org_id`` (transaction-local); background jobs set ``app.rls_bypass``.

Revision ID: 0002
Revises: 0001
"""
from __future__ import annotations

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

BYPASS = "current_setting('app.rls_bypass', true) = 'on'"
ORG = "NULLIF(current_setting('app.current_org_id', true), '')::int"

ORG_TABLES = ["companies", "credit_applications", "uploaded_documents", "processing_jobs", "audit_logs",
              "notifications", "scoring_policies", "model_versions"]
APPLICATION_TABLES = ["review_comments", "document_chunks", "extracted_financial_metrics", "document_signals",
                      "financial_statements", "financial_ratios", "bank_transactions", "gst_records", "reconciliations",
                      "research_queries", "research_findings", "primary_observations", "risk_assessments",
                      "loan_recommendations", "review_decisions", "cam_reports", "chat_sessions"]
PARENT_TABLES = {  # table: (fk column, parent table)
    "directors": ("company_id", "companies"), "promoters": ("company_id", "companies"),
    "shareholdings": ("company_id", "companies"), "document_extractions": ("document_id", "uploaded_documents"),
    "risk_dimensions": ("assessment_id", "risk_assessments"), "chat_messages": ("session_id", "chat_sessions"),
}
ALL = ORG_TABLES + APPLICATION_TABLES + list(PARENT_TABLES)
APPEND_ONLY = ["audit_logs", "review_decisions"]


def upgrade() -> None:
    for t in ORG_TABLES:
        expr = f"{BYPASS} OR organization_id = {ORG}"
        _policy(t, expr)
    for t in APPLICATION_TABLES:
        # The subquery is itself filtered by credit_applications' policy.
        _policy(t, f"{BYPASS} OR application_id IN (SELECT id FROM credit_applications)")
    for t, (col, parent) in PARENT_TABLES.items():
        _policy(t, f"{BYPASS} OR {col} IN (SELECT id FROM {parent})")

    op.execute("""
        CREATE OR REPLACE FUNCTION ic_reject_mutation() RETURNS trigger AS $$
        BEGIN
            IF current_setting('app.allow_record_maintenance', true) = 'on' THEN
                RETURN COALESCE(NEW, OLD);
            END IF;
            RAISE EXCEPTION '% on % is not permitted: records are append-only', TG_OP, TG_TABLE_NAME
                USING ERRCODE = 'insufficient_privilege';
        END $$ LANGUAGE plpgsql;
    """)
    for t in APPEND_ONLY:
        op.execute(f"CREATE TRIGGER {t}_append_only BEFORE UPDATE OR DELETE ON {t} "
                   f"FOR EACH ROW EXECUTE FUNCTION ic_reject_mutation()")


def _policy(table: str, expr: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON {table} USING ({expr}) WITH CHECK ({expr})")


def downgrade() -> None:
    for t in APPEND_ONLY:
        op.execute(f"DROP TRIGGER IF EXISTS {t}_append_only ON {t}")
    op.execute("DROP FUNCTION IF EXISTS ic_reject_mutation()")
    for t in ALL:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t}")
        op.execute(f"ALTER TABLE {t} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY")
