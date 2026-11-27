"""Reproducible training + evaluation pipeline for the LightGBM challenger.

Steps: validate data -> build features (shared with inference) -> borrower-level train/validation/test split ->
LightGBM with early stopping (validation) -> probability calibration chosen by cross-validation on the
validation split -> decision threshold chosen on validation -> single evaluation on the untouched test split ->
baselines (logistic regression, the production policy scorecard) -> TreeSHAP explanations -> error analysis ->
versioned artifact (model, calibrator, manifest, metrics, report, plots, test predictions).

The test split is used exactly once, after every choice has been made on train/validation data.
"""
from __future__ import annotations

import hashlib
import json
import math
import platform
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from ai_engine.risk_model.challenger import synthetic
from ai_engine.risk_model.challenger.features import FEATURE_NAMES, FEATURES, MONOTONE, compute_features

PIPELINE_VERSION = "1.0"
REQUIRED = ["borrower_id", "snapshot", "fiscal_year_end", "default_12m", "revenue_from_operations"]


class DataValidationError(Exception):
    pass


# ----------------------------------------------------------------------------------------------------- data

def validate(rows: list[dict]) -> dict:
    if not rows:
        raise DataValidationError("Dataset is empty.")
    missing = [c for c in REQUIRED if c not in rows[0]]
    if missing:
        raise DataValidationError(f"Missing required columns: {missing}")
    keys = set()
    for i, r in enumerate(rows):
        if r["default_12m"] not in (0, 1):
            raise DataValidationError(f"Row {i}: default_12m must be 0/1.")
        k = (r["borrower_id"], r["snapshot"])
        if k in keys:
            raise DataValidationError(f"Duplicate snapshot {k}.")
        keys.add(k)
    y = np.array([r["default_12m"] for r in rows])
    if min(y.sum(), len(y) - y.sum()) < 50:
        raise DataValidationError("Each class needs at least 50 rows for a meaningful evaluation.")
    origins = sorted({r.get("data_origin", "UNKNOWN") for r in rows})
    missing_rates = {c: float(np.mean([isinstance(r.get(c), float) and math.isnan(r[c]) for r in rows]))
                     for c in synthetic.METRIC_COLUMNS + synthetic.SIGNAL_COLUMNS if c in rows[0]}
    return {"rows": len(rows), "borrowers": len({r["borrower_id"] for r in rows}), "positives": int(y.sum()),
            "positive_rate": float(y.mean()), "data_origin": origins,
            "missing_rates": {k: round(v, 4) for k, v in missing_rates.items() if v > 0}}


def _metrics_dicts(r: dict) -> tuple[dict, dict | None]:
    cur = {k: {"value": r[k]} for k in synthetic.METRIC_COLUMNS
           if not k.startswith("prior_") and k in r and not (isinstance(r[k], float) and math.isnan(r[k]))}
    prior = None
    if not math.isnan(r.get("prior_revenue_from_operations", float("nan"))):
        prior = {"revenue_from_operations": {"value": r["prior_revenue_from_operations"]}}
        for k in ("ebitda", "pat"):
            v = r.get(f"prior_{k}", float("nan"))
            if not math.isnan(v):
                prior[k] = {"value": v}
    return cur, prior


def build_matrix(rows: list[dict]) -> np.ndarray:
    X = np.empty((len(rows), len(FEATURE_NAMES)))
    for i, r in enumerate(rows):
        cur, prior = _metrics_dicts(r)
        f = compute_features(cur, prior, {"gst_bank_variance_pct": r.get("gst_bank_variance_pct"),
                                          "circular": r.get("circular_flag"), "cheque_returns": r.get("cheque_returns")})
        X[i] = [f[n] for n in FEATURE_NAMES]
    return X


def split_by_borrower(groups: np.ndarray, seed: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    uniq = np.array(sorted(set(groups)))
    rng.shuffle(uniq)
    n = len(uniq)
    tr, va = set(uniq[: int(0.6 * n)]), set(uniq[int(0.6 * n): int(0.8 * n)])
    idx = np.arange(len(groups))
    is_tr = np.array([g in tr for g in groups])
    is_va = np.array([g in va for g in groups])
    return idx[is_tr], idx[is_va], idx[~is_tr & ~is_va]


# ----------------------------------------------------------------------------------------------------- baselines

def scorecard_risk(rows: list[dict]) -> np.ndarray:
    """Production policy scorecard (the primary model) evaluated on the same snapshots: risk = 100 - score."""
    from ai_engine.risk_model.policy import get_default_policy
    from ai_engine.risk_model.ratios import compute_ratios, derive_metrics
    from ai_engine.risk_model.scorecard import AssessmentInputs, run_scorecard

    policy = get_default_policy()
    out = np.empty(len(rows))
    for i, r in enumerate(rows):
        cur_raw, prior_raw = _metrics_dicts(r)
        cur = derive_metrics(cur_raw)
        pri = derive_metrics(prior_raw) if prior_raw else None
        fy_end = date.fromisoformat(r["fiscal_year_end"])
        ratios = {x.key: x for x in compute_ratios(cur, pri, period="FY", thresholds=policy["thresholds"])}
        has_bank = not math.isnan(r.get("gst_bank_variance_pct", float("nan")))
        docs = [{"category": "financial_statements", "extraction_status": "extracted"}]
        recons = []
        if has_bank:
            docs += [{"category": "bank_statements", "extraction_status": "extracted"},
                     {"category": "gst_returns", "extraction_status": "extracted"}]
            v = r["gst_bank_variance_pct"]
            st, sev = ("consistent", "info") if v <= 10 else (("potential_discrepancy", "medium") if v <= 25 else ("potential_discrepancy", "high"))
            recons.append({"id": None, "check_type": "gst_vs_bank", "metric": "GST vs bank receipts", "status": st,
                           "severity": sev, "description": f"variance {v:.1f}%", "confidence": 0.8})
            circ = r.get("circular_flag")
            recons.append({"id": None, "check_type": "circular_transactions", "metric": "Round-trip screening",
                           "status": "unusual_pattern" if circ == 1 else "consistent",
                           "severity": "high" if circ == 1 else "info", "description": "", "confidence": 0.7})
            cq = r.get("cheque_returns", 0) or 0
            if cq >= 1:
                recons.append({"id": None, "check_type": "cheque_returns", "metric": "Cheque returns",
                               "status": "unusual_pattern", "severity": "high" if cq >= 2 else "medium",
                               "description": "", "confidence": 0.8})
        rev = r["revenue_from_operations"]
        inp = AssessmentInputs(
            company={"name": r["borrower_id"], "industry": "Manufacturing"},
            application={"requested_amount": max(rev * 0.1, 1e5), "tenure_months": 36, "loan_type": "term_loan",
                         "collateral": [], "guarantors": [], "existing_borrowings": [], "moratorium_months": 0},
            latest_period="FY", latest_period_end=fy_end, current=cur, prior=pri, ratios=ratios, documents=docs,
            metric_confidences=[0.9] * len(cur_raw), verified_metric_share=0.0, signals=[], ratings=[],
            reconciliations=recons, research_mode="not_run", research_findings=[], observations=[], shareholding={},
            as_of=fy_end + timedelta(days=120))
        out[i] = 100.0 - run_scorecard(inp, policy).overall_score
    return out


def _logistic(Xtr, ytr):
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    m = make_pipeline(SimpleImputer(strategy="median", add_indicator=True), StandardScaler(),
                      LogisticRegression(C=0.5, max_iter=5000))
    return m.fit(_clip(Xtr), ytr)


def _clip(X):
    # Ratios can be extreme (tiny denominators); clip to robust bounds so the linear baseline is not dominated.
    return np.clip(X, -1e4, 1e4)


# ----------------------------------------------------------------------------------------------------- calibration

def _platt_fit(s, y):
    from sklearn.linear_model import LogisticRegression

    m = LogisticRegression(C=1e6, max_iter=5000).fit(s.reshape(-1, 1), y)
    return {"method": "platt", "a": float(m.coef_[0][0]), "b": float(m.intercept_[0])}


def _iso_fit(s, y):
    from sklearn.isotonic import IsotonicRegression

    m = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(s, y)
    return {"method": "isotonic", "x": [float(v) for v in m.X_thresholds_], "y": [float(v) for v in m.y_thresholds_]}


def apply_calibration(cal: dict, s: np.ndarray) -> np.ndarray:
    s = np.asarray(s, dtype=float)
    if cal["method"] == "platt":
        return 1.0 / (1.0 + np.exp(-(cal["a"] * s + cal["b"])))
    return np.interp(s, cal["x"], cal["y"])


def choose_calibration(s_val: np.ndarray, y_val: np.ndarray, seed: int) -> tuple[dict, dict]:
    """Pick Platt vs isotonic by 5-fold CV Brier on the validation split, then refit on all validation rows."""
    from sklearn.model_selection import StratifiedKFold

    briers = {"platt": [], "isotonic": []}
    for tr, te in StratifiedKFold(5, shuffle=True, random_state=seed).split(s_val, y_val):
        for name, fit in (("platt", _platt_fit), ("isotonic", _iso_fit)):
            c = fit(s_val[tr], y_val[tr])
            briers[name].append(float(np.mean((apply_calibration(c, s_val[te]) - y_val[te]) ** 2)))
    cv = {k: round(float(np.mean(v)), 5) for k, v in briers.items()}
    best = min(cv, key=cv.get)
    return (_platt_fit if best == "platt" else _iso_fit)(s_val, y_val), cv


# ----------------------------------------------------------------------------------------------------- metrics

def threshold_max_f1(y, p) -> float:
    from sklearn.metrics import precision_recall_curve

    pr, rc, th = precision_recall_curve(y, p)
    f1 = np.where(pr[:-1] + rc[:-1] > 0, 2 * pr[:-1] * rc[:-1] / (pr[:-1] + rc[:-1] + 1e-12), 0)
    return float(th[int(np.argmax(f1))])


def classification_metrics(y, p, threshold, prob=True) -> dict:
    from sklearn.metrics import average_precision_score, brier_score_loss, confusion_matrix, f1_score, precision_score, recall_score, roc_auc_score

    yhat = (p >= threshold).astype(int)
    tn, fp, fn, tp = confusion_matrix(y, yhat, labels=[0, 1]).ravel()
    out = {"roc_auc": float(roc_auc_score(y, p)), "pr_auc": float(average_precision_score(y, p)),
           "precision": float(precision_score(y, yhat, zero_division=0)), "recall": float(recall_score(y, yhat, zero_division=0)),
           "f1": float(f1_score(y, yhat, zero_division=0)), "threshold": float(threshold),
           "confusion_matrix": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)}}
    if prob:
        out["brier"] = float(brier_score_loss(y, p))
    return out


def bootstrap(y, scores: dict[str, np.ndarray], n: int, seed: int) -> dict:
    from sklearn.metrics import average_precision_score, roc_auc_score

    rng = np.random.default_rng(seed)
    res = {k: {"roc_auc": [], "pr_auc": []} for k in scores}
    diffs = []
    keys = list(scores)
    for _ in range(n):
        idx = rng.integers(0, len(y), len(y))
        if y[idx].min() == y[idx].max():
            continue
        aucs = {}
        for k in keys:
            aucs[k] = roc_auc_score(y[idx], scores[k][idx])
            res[k]["roc_auc"].append(aucs[k])
            res[k]["pr_auc"].append(average_precision_score(y[idx], scores[k][idx]))
        diffs.append(aucs[keys[0]] - aucs[keys[-1]])

    def ci(v):
        return [round(float(np.percentile(v, 2.5)), 4), round(float(np.percentile(v, 97.5)), 4)]

    out = {k: {m: ci(v) for m, v in d.items()} for k, d in res.items()}
    out["roc_auc_difference_" + keys[0] + "_minus_" + keys[-1]] = ci(diffs)
    return out


# ----------------------------------------------------------------------------------------------------- run

def train_and_evaluate(dataset_path: str | Path, out_root: str | Path, *, seed: int = 42, data_type: str = "synthetic",
                       bootstrap_rounds: int = 500) -> dict:
    import lightgbm as lgb

    dataset_path, out_root = Path(dataset_path), Path(out_root)
    rows = synthetic.read_csv(dataset_path)
    report_data = validate(rows)
    if data_type == "synthetic" and report_data["data_origin"] != ["SYNTHETIC"]:
        raise DataValidationError("data_type=synthetic but rows are not all marked SYNTHETIC.")
    ds_sha = hashlib.sha256(dataset_path.read_bytes()).hexdigest()
    y = np.array([r["default_12m"] for r in rows])
    groups = np.array([r["borrower_id"] for r in rows])
    profiles = np.array([r.get("profile", "") for r in rows])
    X = build_matrix(rows)
    tr, va, te = split_by_borrower(groups, seed)
    assert not (set(groups[tr]) & set(groups[te]) or set(groups[tr]) & set(groups[va]) or set(groups[va]) & set(groups[te]))

    params = dict(objective="binary", metric=["auc", "binary_logloss"], learning_rate=0.03, num_leaves=15,
                  min_data_in_leaf=40, feature_fraction=0.85, bagging_fraction=0.85, bagging_freq=1, lambda_l2=2.0,
                  monotone_constraints=MONOTONE, monotone_constraints_method="advanced", seed=seed, deterministic=True,
                  force_row_wise=True, num_threads=1, verbose=-1)
    dtrain = lgb.Dataset(X[tr], y[tr], feature_name=FEATURE_NAMES, free_raw_data=False)
    dval = lgb.Dataset(X[va], y[va], reference=dtrain)
    booster = lgb.train(params, dtrain, num_boost_round=3000, valid_sets=[dval],
                        callbacks=[lgb.early_stopping(150, first_metric_only=True, verbose=False)])

    raw = {k: booster.predict(X[idx], raw_score=True, num_iteration=booster.best_iteration) for k, idx in (("val", va), ("test", te))}
    cal, cal_cv = choose_calibration(raw["val"], y[va], seed)
    p_val, p_test = apply_calibration(cal, raw["val"]), apply_calibration(cal, raw["test"])
    p_test_uncal = 1 / (1 + np.exp(-raw["test"]))
    thr = threshold_max_f1(y[va], p_val)

    logit = _logistic(X[tr], y[tr])
    lp_val, lp_test = logit.predict_proba(_clip(X[va]))[:, 1], logit.predict_proba(_clip(X[te]))[:, 1]
    sc = scorecard_risk(rows)
    sc_cal, _ = choose_calibration(sc[va], y[va], seed)
    sp_val, sp_test = apply_calibration(sc_cal, sc[va]), apply_calibration(sc_cal, sc[te])

    metrics = {
        "lightgbm_calibrated": classification_metrics(y[te], p_test, thr),
        "lightgbm_uncalibrated": {"brier": float(np.mean((p_test_uncal - y[te]) ** 2))},
        "logistic_regression": classification_metrics(y[te], lp_test, threshold_max_f1(y[va], lp_val)),
        "policy_scorecard": classification_metrics(y[te], sp_test, threshold_max_f1(y[va], sp_val)),
    }
    metrics["policy_scorecard"]["note"] = "risk = 100 - scorecard score, mapped to a probability by calibration on the validation split"
    ci = bootstrap(y[te], {"lightgbm": p_test, "logistic_regression": lp_test, "policy_scorecard": sp_test}, bootstrap_rounds, seed)

    contrib = booster.predict(X[te], pred_contrib=True, num_iteration=booster.best_iteration)
    additivity = float(np.max(np.abs(contrib.sum(axis=1) - raw["test"])))
    mean_abs = np.abs(contrib[:, :-1]).mean(axis=0)
    gain = booster.feature_importance(importance_type="gain", iteration=booster.best_iteration)
    importance = sorted(({"feature": f, "mean_abs_shap": round(float(m), 5), "gain": round(float(g), 2)}
                         for f, m, g in zip(FEATURE_NAMES, mean_abs, gain)), key=lambda d: -d["mean_abs_shap"])

    yhat = (p_test >= thr).astype(int)
    by_profile = {}
    for prof in sorted(set(profiles[te])):
        m = profiles[te] == prof
        yy, hh = y[te][m], yhat[m]
        by_profile[prof] = {"rows": int(m.sum()), "defaults": int(yy.sum()), "tp": int(((hh == 1) & (yy == 1)).sum()),
                            "fn": int(((hh == 0) & (yy == 1)).sum()), "fp": int(((hh == 1) & (yy == 0)).sum()),
                            "recall": round(float(((hh == 1) & (yy == 1)).sum() / max(1, yy.sum())), 3)}
    fn_idx = np.where((yhat == 0) & (y[te] == 1))[0]
    worst_fn = sorted(fn_idx, key=lambda i: p_test[i])[:5]
    error_examples = [{"borrower_id": groups[te][i], "profile": profiles[te][i], "probability": round(float(p_test[i]), 4),
                       "top_features": _top_contrib(contrib[i], X[te][i], 4)} for i in worst_fn]

    from sklearn.calibration import calibration_curve

    curves = {}
    for name, p in (("LightGBM (calibrated)", p_test), ("LightGBM (uncalibrated)", p_test_uncal),
                    ("Logistic regression", lp_test), ("Policy scorecard (calibrated)", sp_test)):
        fr, mp = calibration_curve(y[te], p, n_bins=10, strategy="quantile")
        curves[name] = {"mean_predicted": [round(float(v), 4) for v in mp], "observed_rate": [round(float(v), 4) for v in fr]}

    fingerprint = hashlib.sha256(json.dumps({"ds": ds_sha, "params": params, "features": FEATURE_NAMES,
                                             "pipeline": PIPELINE_VERSION, "lgb": lgb.__version__}, sort_keys=True,
                                            default=str).encode()).hexdigest()[:8]
    version = f"challenger-lgbm-{datetime.now(timezone.utc):%Y%m%d}-{fingerprint}"
    out = out_root / version
    out.mkdir(parents=True, exist_ok=True)
    booster.save_model(str(out / "model.txt"), num_iteration=booster.best_iteration)
    (out / "calibrator.json").write_text(json.dumps(cal))
    split_stats = {k: {"rows": int(len(idx)), "borrowers": int(len(set(groups[idx]))), "positives": int(y[idx].sum()),
                       "positive_rate": round(float(y[idx].mean()), 4)} for k, idx in (("train", tr), ("validation", va), ("test", te))}
    manifest = {
        "name": "intelli-credit-challenger", "version": version, "created_at": datetime.now(timezone.utc).isoformat(),
        "algorithm": f"LightGBM {lgb.__version__} gradient-boosted trees (binary), monotone constraints",
        "pipeline_version": PIPELINE_VERSION, "python": platform.python_version(),
        "dataset": {"type": data_type, "path": str(dataset_path), "sha256": ds_sha, **report_data,
                    "generator": synthetic.GENERATOR_VERSION if data_type == "synthetic" else None, "split_seed": seed},
        "splits": split_stats, "features": [{"name": n, "description": d, "monotone": m} for n, d, m in FEATURES],
        "params": params, "best_iteration": int(booster.best_iteration), "calibration": {**{k: v for k, v in cal.items() if k == "method"}, "cv_brier": cal_cv},
        "decision_threshold": thr, "metrics_test": metrics, "bootstrap_ci_test": ci, "shap_additivity_max_error": additivity,
        "feature_importance": importance, "error_analysis": {"by_profile": by_profile, "worst_false_negatives": error_examples},
        "calibration_curves_test": curves,
        "status": "experimental", "approved_for_decisions": False,
        "evaluation_status": ("Evaluated on SYNTHETIC labels only — not validated on real loan outcomes. Must not be used "
                              "for lending decisions.") if data_type == "synthetic" else "Evaluated on supplied labelled data; requires model-risk validation before use.",
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str))
    with open(out / "test_predictions.csv", "w") as fh:
        fh.write("borrower_id,snapshot,profile,default_12m,lgbm_probability,logistic_probability,scorecard_probability\n")
        for j, i in enumerate(te):
            fh.write(f"{groups[i]},{rows[i]['snapshot']},{profiles[i]},{y[i]},{p_test[j]:.6f},{lp_test[j]:.6f},{sp_test[j]:.6f}\n")
    _plot_calibration(curves, out / "calibration.png")
    _plot_importance(importance, out / "feature_importance.png")
    (out / "evaluation.md").write_text(render_report(manifest))
    (out_root / "LATEST").write_text(version + "\n")
    return manifest


def _top_contrib(row_contrib, row_x, k):
    pairs = sorted(zip(FEATURE_NAMES, row_contrib[:-1], row_x), key=lambda t: -abs(t[1]))[:k]
    return [{"feature": f, "value": None if math.isnan(x) else round(float(x), 3), "shap_log_odds": round(float(c), 4)} for f, c, x in pairs]


def _plot_calibration(curves, path):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(6, 5), dpi=120)
    ax.plot([0, 1], [0, 1], color="#9aa3ad", lw=1, ls="--", label="Perfect calibration")
    for (name, c), color in zip(curves.items(), ["#2a78d6", "#93b9e8", "#eb6834", "#1baf7a"]):
        ax.plot(c["mean_predicted"], c["observed_rate"], marker="o", ms=4, lw=2, color=color, label=name)
    ax.set_xlabel("Mean predicted probability (test, quantile bins)")
    ax.set_ylabel("Observed synthetic default rate")
    ax.set_title("Calibration on the held-out test split (synthetic labels)", fontsize=10)
    ax.grid(color="#e6e9ee", lw=0.8)
    ax.legend(fontsize=8, frameon=False)
    m = max(max(max(c["mean_predicted"]), max(c["observed_rate"])) for c in curves.values())
    ax.set_xlim(0, min(1, m * 1.1))
    ax.set_ylim(0, min(1, m * 1.1))
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def _plot_importance(importance, path):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    items = list(reversed(importance))
    fig, ax = plt.subplots(figsize=(6, 5), dpi=120)
    ax.barh([d["feature"] for d in items], [d["mean_abs_shap"] for d in items], color="#2a78d6", height=0.6)
    ax.set_xlabel("Mean |SHAP| on test (log-odds)")
    ax.set_title("Feature importance (TreeSHAP)", fontsize=10)
    ax.grid(axis="x", color="#e6e9ee", lw=0.8)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def render_report(m: dict) -> str:
    mt, ci = m["metrics_test"], m["bootstrap_ci_test"]

    def row(name, k):
        d = mt[k]
        cm = d.get("confusion_matrix", {})
        return (f"| {name} | {d['roc_auc']:.3f} | {d['pr_auc']:.3f} | {d.get('brier', float('nan')):.4f} | {d['precision']:.3f} | "
                f"{d['recall']:.3f} | {d['f1']:.3f} | {d['threshold']:.3f} | {cm.get('tp')}/{cm.get('fp')}/{cm.get('fn')}/{cm.get('tn')} |")

    ds = m["dataset"]
    lines = [
        f"# Challenger model evaluation — `{m['version']}`",
        "",
        f"> **Status: {m['status']}. {m['evaluation_status']}** The policy scorecard remains the primary decision-support model.",
        "",
        f"- Algorithm: {m['algorithm']}; best iteration {m['best_iteration']}; calibration: {m['calibration']['method']} "
        f"(5-fold CV Brier on validation: {m['calibration']['cv_brier']})",
        f"- Dataset: **{ds['type'].upper()}** `{ds['path']}` (sha256 `{ds['sha256'][:16]}…`), {ds['rows']} snapshots / "
        f"{ds['borrowers']} borrowers, positive rate {ds['positive_rate']:.1%}; generator `{ds.get('generator')}`",
        f"- Splits by borrower (seed {ds['split_seed']}): " + "; ".join(f"{k} {v['rows']} rows / {v['borrowers']} borrowers, "
                                                                        f"{v['positive_rate']:.1%} positive" for k, v in m["splits"].items()),
        f"- Decision threshold {m['decision_threshold']:.3f} (max F1 on validation); TreeSHAP additivity max error {m['shap_additivity_max_error']:.2e}",
        "",
        "## Test-split metrics (computed once, after all choices were made on train/validation)",
        "",
        "| Model | ROC-AUC | PR-AUC | Brier | Precision | Recall | F1 | Threshold | TP/FP/FN/TN |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
        row("LightGBM challenger (calibrated)", "lightgbm_calibrated"),
        row("Logistic regression baseline", "logistic_regression"),
        row("Policy scorecard (production, calibrated)", "policy_scorecard"),
        "",
        f"Uncalibrated LightGBM Brier: {mt['lightgbm_uncalibrated']['brier']:.4f}. PR-AUC of a random model equals the "
        f"test positive rate ({m['splits']['test']['positive_rate']:.1%}).",
        "",
        "## Bootstrap 95 % intervals (test split, " + "resampled rows)",
        "",
        "| Model | ROC-AUC | PR-AUC |",
        "|---|---|---|",
        *[f"| {k} | {v['roc_auc']} | {v['pr_auc']} |" for k, v in ci.items() if isinstance(v, dict)],
        *[f"\nROC-AUC difference (LightGBM − policy scorecard): {v}" for k, v in ci.items() if k.startswith("roc_auc_difference")],
        "",
        "## Feature importance (mean |SHAP|, log-odds) — see feature_importance.png",
        "",
        "| Feature | Mean abs SHAP | Gain |",
        "|---|---:|---:|",
        *[f"| {d['feature']} | {d['mean_abs_shap']:.4f} | {d['gain']:.0f} |" for d in m["feature_importance"]],
        "",
        "## Error analysis at the chosen threshold (test split)",
        "",
        "| Profile | Rows | Defaults | TP | FN | FP | Recall |",
        "|---|---:|---:|---:|---:|---:|---:|",
        *[f"| {p} | {d['rows']} | {d['defaults']} | {d['tp']} | {d['fn']} | {d['fp']} | {d['recall']:.2f} |"
          for p, d in m["error_analysis"]["by_profile"].items()],
        "",
        "Lowest-probability missed defaults (top contributing features):",
        "",
        *[f"- {e['borrower_id']} ({e['profile']}), p={e['probability']}: " +
          ", ".join(f"{t['feature']}={t['value']} ({t['shap_log_odds']:+.3f})" for t in e["top_features"])
          for e in m["error_analysis"]["worst_false_negatives"]],
        "",
        "## Calibration — see calibration.png",
        "",
        *[f"- {name}: predicted {c['mean_predicted']} vs observed {c['observed_rate']}" for name, c in m["calibration_curves_test"].items()],
        "",
        "## Interpretation and limitations",
        "- The labels are generated from a known formula of financial drivers plus an unobserved factor; the scores above",
        "  measure recovery of that formula. They are **not** evidence of real-world predictive power.",
        "- The policy scorecard was not designed to rank this synthetic population; its score here is a reference point, not a",
        "  verdict on either model. Re-run this pipeline on genuine, governed loan-outcome data before any use.",
        "- Approved for decisions: **no**. The application stores challenger output alongside the scorecard for comparison only.",
    ]
    return "\n".join(lines) + "\n"


def generate_dataset(out_csv: str | Path, *, n_borrowers: int = 4000, seed: int = 20261127) -> dict:
    rows = synthetic.generate(n_borrowers, seed)
    sha = synthetic.write_csv(rows, out_csv)
    card = Path(out_csv).with_name("DATASET_CARD.md")
    card.write_text(synthetic.dataset_card(rows, sha, seed, n_borrowers))
    return {"path": str(out_csv), "rows": len(rows), "sha256": sha, "card": str(card)}
