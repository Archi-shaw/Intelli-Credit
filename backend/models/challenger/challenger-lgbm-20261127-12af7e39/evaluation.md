# Challenger model evaluation — `challenger-lgbm-20261127-12af7e39`

> **Status: experimental. Evaluated on SYNTHETIC labels only — not validated on real loan outcomes. Must not be used for lending decisions.** The policy scorecard remains the primary decision-support model.

- Algorithm: LightGBM 4.7.0 gradient-boosted trees (binary), monotone constraints; best iteration 76; calibration: isotonic (5-fold CV Brier on validation: {'platt': 0.058, 'isotonic': 0.05793})
- Dataset: **SYNTHETIC** `/home/user/Documents/intelli/data/ml/synthetic_credit_v1.csv` (sha256 `2426bfbb38d40c61…`), 7630 snapshots / 4000 borrowers, positive rate 7.7%; generator `synthetic-credit-v1`
- Splits by borrower (seed 42): train 4565 rows / 2400 borrowers, 8.0% positive; validation 1536 rows / 800 borrowers, 7.5% positive; test 1529 rows / 800 borrowers, 7.4% positive
- Decision threshold 0.253 (max F1 on validation); TreeSHAP additivity max error 4.88e-15

## Test-split metrics (computed once, after all choices were made on train/validation)

| Model | ROC-AUC | PR-AUC | Brier | Precision | Recall | F1 | Threshold | TP/FP/FN/TN |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| LightGBM challenger (calibrated) | 0.830 | 0.320 | 0.0569 | 0.315 | 0.593 | 0.411 | 0.253 | 67/146/46/1270 |
| Logistic regression baseline | 0.824 | 0.352 | 0.0586 | 0.265 | 0.558 | 0.359 | 0.140 | 63/175/50/1241 |
| Policy scorecard (production, calibrated) | 0.814 | 0.334 | 0.0584 | 0.333 | 0.389 | 0.359 | 0.245 | 44/88/69/1328 |

Uncalibrated LightGBM Brier: 0.0570. PR-AUC of a random model equals the test positive rate (7.4%).

## Bootstrap 95 % intervals (test split, resampled rows)

| Model | ROC-AUC | PR-AUC |
|---|---|---|
| lightgbm | [0.7875, 0.8684] | [0.2504, 0.3925] |
| logistic_regression | [0.7806, 0.8647] | [0.2715, 0.4365] |
| policy_scorecard | [0.7676, 0.8551] | [0.2542, 0.4308] |

ROC-AUC difference (LightGBM − policy scorecard): [-0.0023, 0.0327]

## Feature importance (mean |SHAP|, log-odds) — see feature_importance.png

| Feature | Mean abs SHAP | Gain |
|---|---:|---:|
| net_profit_margin | 0.3478 | 3637 |
| interest_coverage | 0.2778 | 3578 |
| debt_to_equity | 0.1791 | 1354 |
| cheque_returns | 0.1316 | 1364 |
| current_ratio | 0.1093 | 783 |
| ebitda_margin | 0.0959 | 288 |
| working_capital_to_revenue | 0.0766 | 367 |
| gst_bank_variance_pct | 0.0500 | 281 |
| revenue_growth | 0.0464 | 541 |
| receivable_days | 0.0330 | 187 |
| circular_transactions_flag | 0.0311 | 277 |
| log_revenue | 0.0213 | 220 |
| debt_to_revenue | 0.0166 | 101 |
| ocf_to_debt | 0.0060 | 33 |
| ocf_margin | 0.0058 | 40 |
| net_worth_negative | 0.0050 | 75 |

## Error analysis at the chosen threshold (test split)

| Profile | Rows | Defaults | TP | FN | FP | Recall |
|---|---:|---:|---:|---:|---:|---:|
| healthy | 716 | 14 | 1 | 13 | 2 | 0.07 |
| irregular | 146 | 24 | 15 | 9 | 35 | 0.62 |
| moderate | 449 | 29 | 11 | 18 | 35 | 0.38 |
| stressed | 218 | 46 | 40 | 6 | 74 | 0.87 |

Lowest-probability missed defaults (top contributing features):

- SYN-00732 (moderate), p=0.0035: net_profit_margin=7.437 (-0.259), interest_coverage=8.76 (-0.224), debt_to_equity=0.455 (-0.219), current_ratio=1.743 (-0.142)
- SYN-00861 (healthy), p=0.0035: net_profit_margin=6.259 (-0.309), interest_coverage=6.371 (-0.226), debt_to_equity=0.4 (-0.208), ebitda_margin=13.41 (-0.093)
- SYN-01013 (healthy), p=0.0035: net_profit_margin=10.223 (-0.357), interest_coverage=8.366 (-0.245), debt_to_equity=0.467 (-0.206), ebitda_margin=18.727 (-0.119)
- SYN-02209 (healthy), p=0.0035: net_profit_margin=8.428 (-0.272), interest_coverage=4.521 (-0.213), current_ratio=1.841 (-0.157), debt_to_equity=0.766 (-0.115)
- SYN-00114 (healthy), p=0.0111: net_profit_margin=8.78 (-0.326), interest_coverage=7.596 (-0.228), debt_to_equity=0.38 (-0.217), ebitda_margin=15.115 (-0.120)

## Calibration — see calibration.png

- LightGBM (calibrated): predicted [0.0035, 0.0111, 0.0142, 0.0252, 0.0727, 0.0855, 0.2469, 0.4241] vs observed [0.0114, 0.0339, 0.0114, 0.0169, 0.0439, 0.1126, 0.2568, 0.3816]
- LightGBM (uncalibrated): predicted [0.0141, 0.0152, 0.0169, 0.0196, 0.0246, 0.0326, 0.0489, 0.085, 0.1549, 0.3668] vs observed [0.0065, 0.0196, 0.0261, 0.0196, 0.0131, 0.0066, 0.0458, 0.098, 0.1634, 0.3399]
- Logistic regression: predicted [0.0029, 0.007, 0.0113, 0.0168, 0.0244, 0.0347, 0.0519, 0.0829, 0.1521, 0.4213] vs observed [0.0131, 0.0131, 0.0327, 0.0196, 0.0065, 0.0197, 0.0392, 0.0915, 0.1699, 0.3333]
- Policy scorecard (calibrated): predicted [0.0092, 0.0126, 0.0157, 0.0198, 0.025, 0.0327, 0.0456, 0.0688, 0.1429, 0.348] vs observed [0.0128, 0.0197, 0.0128, 0.0196, 0.039, 0.0274, 0.038, 0.0743, 0.183, 0.3137]

## Interpretation and limitations
- The labels are generated from a known formula of financial drivers plus an unobserved factor; the scores above
  measure recovery of that formula. They are **not** evidence of real-world predictive power.
- The policy scorecard was not designed to rank this synthetic population; its score here is a reference point, not a
  verdict on either model. Re-run this pipeline on genuine, governed loan-outcome data before any use.
- Approved for decisions: **no**. The application stores challenger output alongside the scorecard for comparison only.
