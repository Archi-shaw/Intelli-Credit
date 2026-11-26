# Intelli-Credit — build status (paused)

Work paused when the session's usage limit was reached. This file lists what exists and what's still to do.

## Done (backend only, under `backend/`)
- **Settings and infrastructure:** `app/core/` holds the env-based config, the SQLAlchemy setup (pgvector on Postgres, with a JSON fallback so tests can use SQLite), bcrypt + JWT security, role and organization dependencies (cookie sessions with a CSRF header check), JSON error handlers and private file storage.
- **ORM models:** `app/models/` has all 35 tables from the spec, plus `document_signals`, `review_comments`, `processing_jobs` and `revoked_tokens`.
- **AI engine:** `ai_engine/` contains pure, deterministic modules, each tested by hand:
  - `extraction/`: Indian number, unit and period parsing; line and table extraction with confidence scores; sanction-letter and credit-rating parsing; qualitative signals; parsers for bank statements, GST and shareholding; PDF, DOCX, XLSX, CSV and image parsers; a document classifier.
  - `ocr/`: Tesseract when it is installed, otherwise RapidOCR. RapidOCR is verified working locally at about 98% confidence.
  - `reconciliation/`: 12 cross-source checks (GST vs bank, GSTR-2A vs 3B, filing pattern, GST vs financial statements, financial statements vs bank, cash-flow support, debt vs declared, interest vs debt, loan servicing, circular transactions, cheque returns, cash deposits, balance vs financial statements).
  - `risk_model/`: the policy, ratios, scorecard (9 dimensions, 12 risk categories, Five Cs, red flags, confidence) and an ML training/challenger interface.
  - `decision_engine/`: amount constraints, the rate build-up, tenure, outcome rules and explanations.
  - `research/`: Tavily, Brave and SerpAPI providers plus the research agent, with a synthetic-fixture mode for demos.
  - Also: `embeddings/` (local hashing, Voyage, sentence-transformers), `llm/` (Anthropic `claude-opus-5-5`), `rag/chunker.py` and `nlp/observations.py`.
- **Tests:** `tests/test_ai_engine_core.py`.

## Not started yet
1. Services in `app/services/`: document pipeline, background jobs (thread/celery/sync), financial statements and ratios, reconciliation, research, observations, assessment, recommendation, review, CAM, chat (RAG), dashboard and search. Only `audit.py` exists.
2. REST routes in `app/api/routes/` and `app/main.py`; Celery worker in `app/workers/`.
3. Alembic migrations (`migrations/`), seed users/org/policy, synthetic document generator → `data/synthetic/`.
4. CAM generators in `reporting/generators/` (ReportLab PDF and python-docx).
5. The whole frontend. Dependencies are installed in `frontend/node_modules`, but there is no source code yet.
6. Docker/Compose, `.env.example`, README and docs, frontend tests, end-to-end test.

## Local dev environment used
- Postgres + pgvector container `intellicredit-dev-db` on `127.0.0.1:5433` (db/user `intellicredit`/`intelli`, password `intelli_dev_pw`, dev only).
- Redis container `intellicredit-dev-redis` on `127.0.0.1:6380`.
- Backend venv at `backend/.venv` (`pip install -r requirements.txt`). Run the tests with `cd backend && .venv/bin/python -m pytest`.
