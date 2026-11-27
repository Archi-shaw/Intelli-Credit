"""Management commands.

  python -m app.cli seed [--reset] [--no-analysis]   Seed demo orgs, users and synthetic borrower cases
  python -m app.cli generate-synthetic               Write synthetic documents to SYNTHETIC_DATA_DIR
  python -m app.cli create-app-role --password ...   Create/refresh the non-superuser DB role used by the API
  python -m app.cli recover-jobs                     Re-queue jobs orphaned by a crashed worker
  python -m app.cli ml-generate-data [--borrowers N] [--seed S]   Write the SYNTHETIC labelled dataset + card
  python -m app.cli ml-train [--dataset PATH] [--data-type synthetic|real] [--seed S]
                                                     Train/evaluate the LightGBM challenger; writes a versioned artifact
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from app.core.config import settings
from app.core.logging import configure_logging

ML_DATA = Path(__file__).resolve().parents[2] / "data" / "ml" / "synthetic_credit_v1.csv"


def create_app_role(password: str) -> None:
    from sqlalchemy import create_engine, text

    role = settings.DB_APP_ROLE
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise SystemExit("DB_APP_ROLE must be a simple lower-case identifier.")
    eng = create_engine(settings.migration_url, future=True)
    with eng.begin() as conn:
        dbname = conn.execute(text("SELECT current_database()")).scalar()
        exists = conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role}).scalar()
        pw = password.replace("'", "''")
        verb = "ALTER" if exists else "CREATE"
        conn.execute(text(f"{verb} ROLE {role} WITH LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE PASSWORD '{pw}'"))
        conn.execute(text(f'GRANT CONNECT ON DATABASE "{dbname}" TO {role}'))
        conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
        conn.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}"))
        conn.execute(text(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role}"))
        conn.execute(text(f"REVOKE ALL ON alembic_version FROM {role}"))
        conn.execute(text(f"GRANT SELECT ON alembic_version TO {role}"))
        conn.execute(text(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {role}"))
        conn.execute(text(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {role}"))
    print(f"Role '{role}' {'updated' if exists else 'created'} with DML privileges (row-level security enforced).")


def main(argv: list[str] | None = None) -> int:
    configure_logging(settings.LOG_LEVEL, "text")
    p = argparse.ArgumentParser(prog="python -m app.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seed")
    s.add_argument("--reset", action="store_true", help="delete and recreate the demo organisations")
    s.add_argument("--no-analysis", action="store_true", help="only upload and process documents")
    sub.add_parser("generate-synthetic")
    r = sub.add_parser("create-app-role")
    r.add_argument("--password", default=os.environ.get("APP_DB_PASSWORD") or settings.APP_DB_PASSWORD)
    sub.add_parser("recover-jobs")
    g = sub.add_parser("ml-generate-data")
    g.add_argument("--borrowers", type=int, default=4000)
    g.add_argument("--seed", type=int, default=20261127)
    g.add_argument("--out", default=str(ML_DATA))
    t = sub.add_parser("ml-train")
    t.add_argument("--dataset", default=str(ML_DATA))
    t.add_argument("--data-type", choices=["synthetic", "real"], default="synthetic")
    t.add_argument("--seed", type=int, default=42)
    t.add_argument("--out", default=settings.CHALLENGER_MODEL_DIR)
    args = p.parse_args(argv)

    if args.cmd == "seed":
        os.environ.setdefault("JOB_BACKEND", "sync")
        from app.seed.demo import demo_password, seed

        out = seed(reset=args.reset, analyse=not args.no_analysis)
        print(json.dumps(out, indent=2, default=str))
        if not out.get("skipped"):
            print("\nDemo sign-in password for all demo users:", "(from DEMO_PASSWORD)" if os.environ.get("DEMO_PASSWORD")
                  else demo_password())
    elif args.cmd == "generate-synthetic":
        from app.seed.synthetic import generate

        for k, v in generate(settings.SYNTHETIC_DATA_DIR).items():
            print(k, *[str(path) for path, _ in v["files"]], sep="\n  ")
    elif args.cmd == "create-app-role":
        if len(args.password) < 12:
            print("Provide --password (or APP_DB_PASSWORD) of at least 12 characters.", file=sys.stderr)
            return 2
        create_app_role(args.password)
    elif args.cmd == "ml-generate-data":
        from ai_engine.risk_model.challenger.pipeline import generate_dataset

        print(json.dumps(generate_dataset(args.out, n_borrowers=args.borrowers, seed=args.seed), indent=2))
    elif args.cmd == "ml-train":
        from ai_engine.risk_model.challenger.pipeline import train_and_evaluate

        m = train_and_evaluate(args.dataset, args.out, seed=args.seed, data_type=args.data_type)
        mt = m["metrics_test"]
        print(json.dumps({"version": m["version"], "artifact_dir": str(Path(args.out) / m["version"]),
                          "dataset_type": m["dataset"]["type"], "best_iteration": m["best_iteration"],
                          "calibration": m["calibration"], "test": {k: {x: round(v, 4) for x, v in d.items() if isinstance(v, float)}
                                                                    for k, d in mt.items()},
                          "status": m["status"], "approved_for_decisions": m["approved_for_decisions"]}, indent=2))
    elif args.cmd == "recover-jobs":
        from app.services.jobs import recover_jobs

        print(f"{recover_jobs()} job(s) re-queued")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
