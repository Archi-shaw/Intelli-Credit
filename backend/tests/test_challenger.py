"""LightGBM challenger: synthetic data hygiene, reproducible training/evaluation, inference and isolation from decisions."""
from __future__ import annotations

import json
import math
import shutil

import numpy as np
import pytest

from ai_engine.risk_model.challenger import synthetic
from ai_engine.risk_model.challenger.features import FEATURE_NAMES, compute_features
from ai_engine.risk_model.challenger.inference import ArtifactError, ChallengerModel
from ai_engine.risk_model.challenger.pipeline import split_by_borrower, train_and_evaluate
from tests.conftest import TMP, make_assessment, make_borrower, upload


def test_synthetic_dataset_is_reproducible_labelled_and_leakage_free():
    a, b = synthetic.generate(300, seed=7), synthetic.generate(300, seed=7)
    assert [r["default_12m"] for r in a] == [r["default_12m"] for r in b]
    assert {r["data_origin"] for r in a} == {"SYNTHETIC"}
    assert not {"q", "u", "q_strength", "u_unobserved"} & set(synthetic.COLUMNS), "hidden generator variables must not be columns"
    assert "profile" not in FEATURE_NAMES and "default_12m" not in FEATURE_NAMES
    rate = np.mean([r["default_12m"] for r in synthetic.generate(1500, seed=11)])
    assert 0.04 < rate < 0.14
    # No observations after a synthetic default.
    by_b: dict[str, list] = {}
    for r in a:
        by_b.setdefault(r["borrower_id"], []).append(r)
    for snaps in by_b.values():
        flags = [s["default_12m"] for s in sorted(snaps, key=lambda s: s["snapshot"])]
        assert 1 not in flags[:-1]
    # Missing values stay missing (NaN), never zero-filled.
    assert any(math.isnan(r["operating_cash_flow"]) for r in a)


def test_features_keep_missing_values_missing():
    f = compute_features({"revenue_from_operations": {"value": 1e8}}, None, None)
    assert f["log_revenue"] == 8.0
    assert all(math.isnan(f[k]) for k in FEATURE_NAMES if k != "log_revenue")


def test_borrower_level_split_has_no_overlap():
    groups = np.array([f"B{i // 3}" for i in range(900)])
    tr, va, te = split_by_borrower(groups, seed=1)
    assert not (set(groups[tr]) & set(groups[va]) or set(groups[tr]) & set(groups[te]) or set(groups[va]) & set(groups[te]))
    assert len(tr) + len(va) + len(te) == 900


@pytest.fixture(scope="module")
def small_model():
    out = TMP / "ml"
    shutil.rmtree(out, ignore_errors=True)
    csv = out / "data.csv"
    synthetic.write_csv(synthetic.generate(1200, seed=5), csv)
    m = train_and_evaluate(csv, out / "models", seed=3, bootstrap_rounds=60)
    return m, out / "models" / m["version"]


def test_pipeline_writes_a_complete_versioned_artifact(small_model):
    m, d = small_model
    for f in ("model.txt", "calibrator.json", "manifest.json", "evaluation.md", "calibration.png", "feature_importance.png", "test_predictions.csv"):
        assert (d / f).is_file(), f
    assert (d.parent / "LATEST").read_text().strip() == m["version"]
    assert m["dataset"]["type"] == "synthetic" and m["approved_for_decisions"] is False and "SYNTHETIC" in m["evaluation_status"]
    for model in ("lightgbm_calibrated", "logistic_regression", "policy_scorecard"):
        mt = m["metrics_test"][model]
        assert 0 <= mt["roc_auc"] <= 1 and 0 <= mt["pr_auc"] <= 1 and 0 <= mt["brier"] <= 1
        assert sum(mt["confusion_matrix"].values()) == m["splits"]["test"]["rows"]
    assert m["shap_additivity_max_error"] < 1e-6
    assert m["calibration"]["method"] in ("platt", "isotonic")
    assert "Evaluated on SYNTHETIC labels only" in (d / "evaluation.md").read_text()


def test_inference_returns_probability_and_shap_contributions(small_model):
    _, d = small_model
    model = ChallengerModel(d)
    stressed = {**{n: math.nan for n in FEATURE_NAMES}, "log_revenue": 7.5, "net_worth_negative": 1.0, "interest_coverage": 0.6,
                "debt_to_revenue": 1.4, "ebitda_margin": -4.0, "net_profit_margin": -12.0, "gst_bank_variance_pct": 60.0,
                "circular_transactions_flag": 1.0, "cheque_returns": 4.0}
    healthy = {**stressed, "net_worth_negative": 0.0, "interest_coverage": 9.0, "debt_to_revenue": 0.2, "ebitda_margin": 18.0,
               "net_profit_margin": 9.0, "gst_bank_variance_pct": 2.0, "circular_transactions_flag": 0.0, "cheque_returns": 0.0,
               "debt_to_equity": 0.4, "current_ratio": 1.9}
    hs, st = model.predict(healthy), model.predict(stressed)
    assert 0 <= hs["probability"] <= 1 and 0 <= st["probability"] <= 1
    assert st["probability"] > hs["probability"]
    assert st["top_contributions"] and all(c["direction"] in ("raises risk", "lowers risk") for c in st["top_contributions"])
    assert st["dataset_type"] == "synthetic" and not st["approved_for_decisions"]
    assert "debt_to_equity" in st["missing_features"]


def test_tampered_or_mismatched_artifact_is_rejected(small_model, tmp_path):
    _, d = small_model
    bad = tmp_path / "bad"
    shutil.copytree(d, bad)
    man = json.loads((bad / "manifest.json").read_text())
    man["features"] = man["features"][:-1]
    (bad / "manifest.json").write_text(json.dumps(man))
    with pytest.raises(ArtifactError):
        ChallengerModel(bad)


def test_challenger_is_recorded_but_never_changes_the_recommendation(org_a, synthetic_files, monkeypatch):
    from app.core.config import settings

    api = org_a["analyst"]
    case = synthetic_files["inconsistent"]["case"]
    comp = {k: v for k, v in case.company.items() if k not in ("directors", "promoters")}
    comp["incorporation_date"] = comp["incorporation_date"].isoformat()
    a = make_assessment(api, make_borrower(api, **comp)["id"], **case.application)
    for cat in ("financial_statements", "bank_statements", "gst_returns"):
        upload(api, a["id"], synthetic_files["inconsistent"][cat], cat)
    api.post(f"/api/v1/assessments/{a['id']}/reconciliation")

    with_model = api.post(f"/api/v1/assessments/{a['id']}/risk-assessment").json()
    ch = with_model["assessment"]["challenger"]
    assert ch["status"] == "ok" and ch["model_version"].startswith("challenger-lgbm-")
    assert 0 <= ch["probability"] <= 1 and ch["dataset_type"] == "synthetic" and ch["top_contributions"]
    assert ch["features"]["gst_bank_variance_pct"] > 50  # the GST-vs-bank discrepancy reaches the model

    monkeypatch.setattr(settings, "CHALLENGER_ENABLED", False)
    without = api.post(f"/api/v1/assessments/{a['id']}/risk-assessment").json()
    assert without["assessment"]["challenger"] is None
    for k in ("outcome", "recommended_amount", "indicative_rate", "recommended_tenure_months"):
        assert with_model["recommendation"][k] == without["recommendation"][k], k
    assert with_model["assessment"]["overall_score"] == without["assessment"]["overall_score"]

    summary = api.get("/api/v1/system/models").json()
    assert summary["primary"]["status"] == "active" and summary["challenger"]["available"]
    assert summary["challenger"]["approved_for_decisions"] is False


def test_missing_artifact_degrades_gracefully(org_a, monkeypatch, tmp_path):
    from app.core.config import settings
    from app.services import challenger

    monkeypatch.setattr(settings, "CHALLENGER_MODEL_DIR", str(tmp_path / "none"))
    out = challenger.score(None, 0, {}, None, [], set())
    assert out["status"] == "unavailable" and "ml-train" in out["detail"]


def _feature_matrix(booster, n: int = 400, seed: int = 7) -> np.ndarray:
    """Random rows across each feature's training range, with missing values, zeros and exact split thresholds."""
    rng = np.random.default_rng(seed)
    import re
    from pathlib import Path

    from app.core.config import settings

    text = (Path(settings.CHALLENGER_MODEL_DIR) / (Path(settings.CHALLENGER_MODEL_DIR) / "LATEST").read_text().strip()
            / "model.txt").read_text()
    ranges = [tuple(map(float, r.split(":"))) for r in re.search(r"feature_infos=(.*)", text).group(1).replace("[", "")
              .replace("]", "").split()]
    X = np.array([[rng.uniform(lo - 0.1 * (hi - lo), hi + 0.1 * (hi - lo)) for lo, hi in ranges] for _ in range(n)])
    X[rng.random(X.shape) < 0.15] = np.nan
    X[rng.random(X.shape) < 0.03] = 0.0
    thresholds = [(t.split_feature[i], t.threshold[i]) for t in booster.trees for i in range(len(t.split_feature))]
    for row in range(0, n, 4):  # exact thresholds exercise the `<=` boundary
        f, thr = thresholds[row % len(thresholds)]
        X[row, f] = thr
    return X


def test_numpy_scorer_matches_lightgbm_scores_and_treeshap():
    """The serverless scorer (no LightGBM/SciPy) must reproduce LightGBM exactly on the shipped artifact."""
    lgb = pytest.importorskip("lightgbm")
    from pathlib import Path

    from ai_engine.risk_model.challenger.treemodel import TextModelBooster
    from app.core.config import settings

    root = Path(settings.CHALLENGER_MODEL_DIR)
    model = root / (root / "LATEST").read_text().strip() / "model.txt"
    ours, ref = TextModelBooster(model), lgb.Booster(model_file=str(model))
    assert ours.feature_name() == ref.feature_name() == FEATURE_NAMES
    X = _feature_matrix(ours)
    np.testing.assert_allclose(ours.predict(X, raw_score=True), ref.predict(X, raw_score=True), rtol=0, atol=1e-10)
    np.testing.assert_allclose(ours.predict(X, pred_contrib=True), ref.predict(X, pred_contrib=True), rtol=0, atol=1e-10)
    np.testing.assert_allclose(ours.predict(X), ref.predict(X), rtol=0, atol=1e-12)


def test_challenger_uses_numpy_scorer_without_lightgbm(monkeypatch):
    import builtins
    from pathlib import Path

    from app.core.config import settings

    real_import = builtins.__import__

    def no_lightgbm(name, *a, **kw):
        if name == "lightgbm" or name.startswith("lightgbm."):
            raise ImportError("lightgbm not installed")
        return real_import(name, *a, **kw)

    root = Path(settings.CHALLENGER_MODEL_DIR)
    art = root / (root / "LATEST").read_text().strip()
    features = {n: v for n, v in zip(FEATURE_NAMES, [8.5, 9.1, 12.0, 6.0, 1.2, 0, 0.4, 1.4, 3.1, 8.0, 0.3, 10.0,
                                                     60.0, math.nan, 0, 1])}
    with_lgb = ChallengerModel(art).predict(features)
    monkeypatch.setattr(builtins, "__import__", no_lightgbm)
    slim = ChallengerModel(art)
    assert slim.engine == "treemodel"
    assert slim.predict(features) == with_lgb  # identical output, including rounded SHAP contributions
