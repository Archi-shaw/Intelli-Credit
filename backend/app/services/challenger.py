"""Optional challenger-model scoring for assessments (display and comparison only).

The policy scorecard and the deterministic decision engine remain the only inputs to recommendations. The
challenger's output is stored on the risk assessment for transparency; it never changes scores, amounts or rates.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ai_engine.risk_model.challenger.features import compute_features, recon_signals
from app.core.config import settings
from app.models import BankTransaction

log = logging.getLogger("intellicredit.challenger")
_lock = threading.Lock()
_cache: dict[tuple, object] = {}

NOTE = ("Experimental challenger model shown for comparison only. It does not influence the risk score, the "
        "recommendation, amounts or rates.")


def model_dir() -> Path | None:
    root = Path(settings.CHALLENGER_MODEL_DIR)
    version = settings.CHALLENGER_MODEL_VERSION.strip()
    if not version and (root / "LATEST").is_file():
        version = (root / "LATEST").read_text().strip()
    return root / version if version else None


def load():
    """Return the configured ChallengerModel (cached per artifact) or raise ArtifactError."""
    from ai_engine.risk_model.challenger.inference import ArtifactError, ChallengerModel

    d = model_dir()
    if d is None or not (d / "manifest.json").is_file():
        raise ArtifactError("No challenger model artifact is installed (run `python -m app.cli ml-train`).")
    key = (str(d), (d / "model.txt").stat().st_mtime)
    with _lock:
        if key not in _cache:
            _cache.clear()
            _cache[key] = ChallengerModel(d)
        return _cache[key]


def score(db: Session, application_id: int, current: dict, prior: dict | None, recon_items: list[dict],
          document_categories: set[str]) -> dict | None:
    if not settings.CHALLENGER_ENABLED:
        return None
    try:
        model = load()
    except Exception as exc:  # noqa: BLE001 - an unavailable challenger must never break an assessment
        return {"status": "unavailable", "detail": str(exc)[:300], "note": NOTE}
    try:
        has_bank = "bank_statements" in document_categories
        cheques = db.scalar(select(func.count(BankTransaction.id)).where(
            BankTransaction.application_id == application_id, BankTransaction.category == "cheque_return")) if has_bank else None
        feats = compute_features(current, prior, recon_signals(recon_items, has_bank, cheques))
        out = model.predict(feats)
        out["note"] = NOTE
        return out
    except Exception as exc:  # noqa: BLE001
        log.exception("challenger scoring failed")
        return {"status": "error", "detail": f"{type(exc).__name__}: {exc}"[:300], "note": NOTE}


def model_summary() -> dict:
    try:
        m = load().manifest
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "enabled": settings.CHALLENGER_ENABLED, "detail": str(exc)[:300]}
    return {"available": True, "enabled": settings.CHALLENGER_ENABLED, "version": m["version"], "algorithm": m["algorithm"],
            "created_at": m["created_at"], "dataset": {k: m["dataset"].get(k) for k in ("type", "rows", "borrowers", "positive_rate", "generator", "sha256")},
            "status": m["status"], "approved_for_decisions": m["approved_for_decisions"],
            "evaluation_status": m["evaluation_status"], "metrics_test": m["metrics_test"],
            "bootstrap_ci_test": m["bootstrap_ci_test"], "feature_importance": m["feature_importance"][:8],
            "calibration": m["calibration"], "decision_threshold": m["decision_threshold"]}
