"""Interchangeable credit-model interface and supervised training pipeline.

The active decision model is the transparent policy scorecard. A machine-learning model can be
trained only from a genuine labelled historical dataset supplied by the institution (CSV with
feature columns and a binary default label). No training data is bundled or invented and no
accuracy metrics exist until such a dataset is used. Trained models are registered as
*challengers*; their output is displayed alongside — never instead of — the scorecard.
"""
from __future__ import annotations

import csv
import io
import math
from dataclasses import dataclass, field

import numpy as np

# Features the platform can compute for every assessment (ratio keys + qualitative factor scores).
FEATURE_SPEC = [
    "current_ratio", "quick_ratio", "debt_to_equity", "debt_to_assets", "debt_to_ebitda", "interest_coverage",
    "net_profit_margin", "ebitda_margin", "roa", "roe", "receivable_days", "ocf_to_debt", "cash_conversion",
    "revenue_growth", "pat_growth", "capacity_utilisation", "promoter_holding", "promoter_pledge",
]
MIN_ROWS = 200
MIN_PER_CLASS = 20
SUPPORTED_MODELS = ("logistic_regression", "random_forest", "gradient_boosting")


class TrainingDataError(Exception):
    pass


@dataclass
class ModelOutput:
    model_type: str
    probability_of_default: float | None
    contributions: list[dict] = field(default_factory=list)
    note: str = ""


def load_labelled_csv(data: bytes, label_column: str = "default_flag") -> tuple[np.ndarray, np.ndarray, list[str]]:
    text = data.decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise TrainingDataError("The dataset is empty.")
    cols = rows[0].keys()
    if label_column not in cols:
        raise TrainingDataError(f"Label column '{label_column}' not found. Expected a 0/1 column named '{label_column}'.")
    features = [f for f in FEATURE_SPEC if f in cols]
    if len(features) < 3:
        raise TrainingDataError(f"At least three supported feature columns are required. Supported: {', '.join(FEATURE_SPEC)}.")
    X, y = [], []
    for i, r in enumerate(rows, start=2):
        lab = str(r[label_column]).strip()
        if lab not in ("0", "1"):
            raise TrainingDataError(f"Row {i}: label must be 0 or 1 (got '{lab}').")
        y.append(int(lab))
        vals = []
        for f in features:
            v = str(r.get(f, "")).strip()
            try:
                vals.append(float(v) if v not in ("", "NA", "nan", "None") else math.nan)
            except ValueError as exc:
                raise TrainingDataError(f"Row {i}: feature '{f}' is not numeric ('{v}').") from exc
        X.append(vals)
    Xa, ya = np.array(X, dtype=float), np.array(y, dtype=int)
    if len(ya) < MIN_ROWS:
        raise TrainingDataError(f"At least {MIN_ROWS} labelled rows are required for a meaningful model (got {len(ya)}).")
    if min((ya == 0).sum(), (ya == 1).sum()) < MIN_PER_CLASS:
        raise TrainingDataError(f"Each class needs at least {MIN_PER_CLASS} examples (defaults: {(ya == 1).sum()}).")
    return Xa, ya, features


def _build(model_type: str):
    from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    if model_type == "logistic_regression":
        return Pipeline([("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler()),
                         ("model", LogisticRegression(max_iter=2000, class_weight="balanced"))])
    if model_type == "random_forest":
        return Pipeline([("impute", SimpleImputer(strategy="median")),
                         ("model", RandomForestClassifier(n_estimators=300, min_samples_leaf=5, class_weight="balanced", random_state=7))])
    if model_type == "gradient_boosting":
        return Pipeline([("model", HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, random_state=7))])
    raise TrainingDataError(f"Unsupported model type '{model_type}'. Choose one of: {', '.join(SUPPORTED_MODELS)}.")


def train(X: np.ndarray, y: np.ndarray, features: list[str], model_type: str) -> dict:
    """Fit, calibrate and evaluate on a stratified hold-out split. Returns artefact + honest metrics."""
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.inspection import permutation_importance
    from sklearn.metrics import accuracy_score, brier_score_loss, log_loss, roc_auc_score, roc_curve
    from sklearn.model_selection import train_test_split

    X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.25, stratify=y, random_state=7)
    base = _build(model_type)
    method = "isotonic" if len(y_tr) >= 1000 else "sigmoid"
    model = CalibratedClassifierCV(base, method=method, cv=3)
    model.fit(X_tr, y_tr)
    p = model.predict_proba(X_te)[:, 1]
    fpr, tpr, _ = roc_curve(y_te, p)
    metrics = {
        "roc_auc": round(float(roc_auc_score(y_te, p)), 4),
        "ks_statistic": round(float(np.max(tpr - fpr)), 4),
        "brier_score": round(float(brier_score_loss(y_te, p)), 4),
        "log_loss": round(float(log_loss(y_te, p, labels=[0, 1])), 4),
        "accuracy_at_0_5": round(float(accuracy_score(y_te, (p >= 0.5).astype(int))), 4),
        "train_rows": int(len(y_tr)), "test_rows": int(len(y_te)), "default_rate": round(float(y.mean()), 4),
        "calibration": method, "evaluation": "stratified 25% hold-out",
    }
    imp = permutation_importance(model, X_te, y_te, scoring="roc_auc", n_repeats=5, random_state=7)
    importance = {f: round(float(v), 5) for f, v in sorted(zip(features, imp.importances_mean), key=lambda t: -t[1])}
    # Refit the linear explainer on all data for contribution-based (linear SHAP) explanations.
    explainer = None
    if model_type == "logistic_regression":
        lin = _build(model_type).fit(X, y)
        imputer, scaler, lr = lin.named_steps["impute"], lin.named_steps["scale"], lin.named_steps["model"]
        explainer = {"medians": imputer.statistics_.tolist(), "means": scaler.mean_.tolist(), "scales": scaler.scale_.tolist(),
                     "coef": lr.coef_[0].tolist(), "intercept": float(lr.intercept_[0])}
    return {"model": model, "features": features, "metrics": metrics, "feature_importance": importance, "explainer": explainer,
            "model_type": model_type}


def predict(artifact: dict, feature_values: dict) -> ModelOutput:
    feats = artifact["features"]
    x = np.array([[float(feature_values.get(f)) if feature_values.get(f) is not None else math.nan for f in feats]])
    pd_ = float(artifact["model"].predict_proba(x)[0, 1])
    contributions: list[dict] = []
    exp = artifact.get("explainer")
    if exp:
        # Exact linear-model SHAP values (log-odds space) relative to the training mean, assuming feature independence.
        for i, f in enumerate(feats):
            raw = x[0, i] if not math.isnan(x[0, i]) else exp["medians"][i]
            z = (raw - exp["means"][i]) / (exp["scales"][i] or 1.0)
            contributions.append({"feature": f, "value": None if math.isnan(x[0, i]) else float(x[0, i]),
                                  "contribution_log_odds": round(exp["coef"][i] * z, 4)})
        contributions.sort(key=lambda c: -abs(c["contribution_log_odds"]))
        note = "Contributions are exact linear SHAP values in log-odds space."
    else:
        note = "Tree-model explanations use global permutation importance (see model registry)."
    return ModelOutput(model_type=artifact["model_type"], probability_of_default=round(pd_, 4), contributions=contributions,
                       note=note)
