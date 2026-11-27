"""Load a versioned challenger artifact and score one borrower with TreeSHAP explanations."""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from ai_engine.risk_model.challenger.features import FEATURE_NAMES, FEATURES
from ai_engine.risk_model.challenger.pipeline import apply_calibration

DESCRIPTIONS = {n: d for n, d, _ in FEATURES}


class ArtifactError(Exception):
    pass


class ChallengerModel:
    def __init__(self, model_dir: str | Path):
        import lightgbm as lgb

        self.dir = Path(model_dir)
        try:
            self.manifest = json.loads((self.dir / "manifest.json").read_text())
            self.calibrator = json.loads((self.dir / "calibrator.json").read_text())
            self.booster = lgb.Booster(model_file=str(self.dir / "model.txt"))
        except (OSError, ValueError, lgb.basic.LightGBMError) as exc:
            raise ArtifactError(f"Challenger artifact at {self.dir} is unreadable: {exc}") from exc
        names = [f["name"] for f in self.manifest.get("features", [])]
        if names != FEATURE_NAMES or self.booster.feature_name() != FEATURE_NAMES:
            raise ArtifactError("Challenger artifact was trained with a different feature set; retrain it with the current code.")

    @property
    def version(self) -> str:
        return self.manifest["version"]

    def predict(self, features: dict[str, float], top_k: int = 6) -> dict:
        x = np.array([[float(features.get(n, math.nan)) for n in FEATURE_NAMES]])
        raw = float(self.booster.predict(x, raw_score=True)[0])
        contrib = self.booster.predict(x, pred_contrib=True)[0]
        p = float(apply_calibration(self.calibrator, np.array([raw]))[0])
        order = sorted(range(len(FEATURE_NAMES)), key=lambda i: -abs(contrib[i]))[:top_k]
        mt = self.manifest.get("metrics_test", {})
        return {
            "status": "ok", "model_version": self.version, "algorithm": self.manifest.get("algorithm"),
            "dataset_type": self.manifest.get("dataset", {}).get("type"),
            "evaluation_status": self.manifest.get("evaluation_status"),
            "approved_for_decisions": bool(self.manifest.get("approved_for_decisions")),
            "probability": round(p, 4),
            "probability_label": "Model-estimated probability of default within 12 months"
                                 + (" (calibrated on SYNTHETIC labels — illustrative only)" if self.manifest.get("dataset", {}).get("type") == "synthetic" else ""),
            "decision_threshold": self.manifest.get("decision_threshold"),
            "above_threshold": p >= float(self.manifest.get("decision_threshold") or 1.0),
            "raw_log_odds": round(raw, 4), "base_log_odds": round(float(contrib[-1]), 4),
            "top_contributions": [{
                "feature": FEATURE_NAMES[i], "description": DESCRIPTIONS[FEATURE_NAMES[i]],
                "value": None if math.isnan(x[0][i]) else round(float(x[0][i]), 4),
                "shap_log_odds": round(float(contrib[i]), 4),
                "direction": "raises risk" if contrib[i] > 0 else "lowers risk"} for i in order],
            "missing_features": [n for n, v in zip(FEATURE_NAMES, x[0]) if math.isnan(v)],
            "features": {n: None if math.isnan(v) else round(float(v), 4) for n, v in zip(FEATURE_NAMES, x[0])},
            "test_metrics": {k: {m: round(v, 4) for m, v in d.items() if m in ("roc_auc", "pr_auc", "brier")}
                             for k, d in mt.items() if k in ("lightgbm_calibrated", "policy_scorecard")},
        }
