# ML challenger model (experimental)

The **policy scorecard remains the primary decision-support model.** The LightGBM challenger is computed alongside
it for comparison and research only: it never changes risk scores, recommendations, amounts, rates or tenures
(enforced by `tests/test_challenger.py::test_challenger_is_recorded_but_never_changes_the_recommendation`), and its
artifact is marked `approved_for_decisions: false`.

**It is trained on SYNTHETIC labels.** No real loan-performance data was available. The metrics below measure how
well models recover the synthetic generator's own assumptions; they are **not** evidence of real-world predictive
power and must not be quoted as such.

## Commands

```bash
cd backend && .venv/bin/python -m app.cli ml-generate-data
```

```bash
cd backend && .venv/bin/python -m app.cli ml-train
```

`ml-generate-data` writes `data/ml/synthetic_credit_v1.csv` and `data/ml/DATASET_CARD.md` (default seed
`20261127`, 4,000 borrowers). `ml-train` validates the data, trains, evaluates and writes a versioned artifact to
`backend/models/challenger/<version>/` and points `backend/models/challenger/LATEST` at it. Both are deterministic:
re-running produces the same dataset hash, the same version fingerprint and identical test metrics (verified).

Train on real, governed outcome data instead (same column schema as the synthetic CSV — statement metrics,
GST/bank signals, `borrower_id`, `snapshot`, `fiscal_year_end`, `default_12m`):

```bash
cd backend && .venv/bin/python -m app.cli ml-train --dataset /secure/path/labelled.csv --data-type real
```

A model trained on real data still ships with `approved_for_decisions: false`; independent model-risk validation
is required before any decision use.

## Dataset (`synthetic-credit-v1`)

- 7,630 annual snapshots from 4,000 synthetic borrowers; 591 synthetic defaults (7.7 %); SHA-256
  `2426bfbb38d40c616853713c11728948ddd3d2718776d30825f144c40dc2e85d`.
- Four profiles with different default rates (healthy 1.4 %, moderate 7.0 %, stressed 23.4 %, irregular 18.6 %) —
  class imbalance is deliberate.
- Controlled inconsistencies: irregular borrowers have bank receipts covering only 20–65 % of GST turnover,
  round-trip transactions and cheque returns.
- Missing values (operating cash flow 8 %, receivables 5 %, bank + GST absent for 12 % of borrowers, no prior period
  for ~30 % of first snapshots) are kept as missing — never zero-filled.
- Labels: `sigmoid(a0 + Σ coef·driver)` over hidden strength, an unobserved factor and named financial drivers; the
  full formula and coefficients are in `data/ml/DATASET_CARD.md`.
- Leakage controls: only application-time information; hidden generator variables are not in the file; `profile` is
  for error analysis only; no snapshots after a default; splits are by borrower.

## Features (shared with live scoring)

`ai_engine/risk_model/challenger/features.py` computes 16 features with the **production ratio engine**, so training
and live scoring cannot drift: log revenue, revenue growth, EBITDA and net margins, debt-to-equity (missing when
equity ≤ 0) plus a negative-net-worth flag, debt-to-revenue, current ratio, interest coverage, OCF margin, OCF-to-debt,
working capital to revenue, receivable days, GST-vs-bank variance, round-trip flag and cheque returns. Monotone
constraints encode the expected direction of each financial ratio.

## Pipeline

1. Validate (required columns, binary labels, unique borrower-snapshots, minimum class counts, data origin).
2. Build features; split by borrower 60 / 20 / 20 (train 4,565 rows, validation 1,536, test 1,529).
3. LightGBM 4.7.0 (15 leaves, learning rate 0.03, L2 2.0, bagging, monotone constraints), early stopping on
   validation AUC → best iteration 76.
4. Calibration chosen by 5-fold CV on the validation split: isotonic (Brier 0.05793) vs Platt (0.05800).
5. Decision threshold maximising F1 on validation: 0.2533.
6. One evaluation on the untouched test split; baselines: logistic regression (median imputation + missing
   indicators) and the **production policy scorecard** run on the same snapshots (risk = 100 − score, calibrated on
   validation).
7. TreeSHAP explanations (LightGBM built-in; additivity error 5 × 10⁻¹⁵), error analysis, calibration curves.
8. Artifact: `model.txt`, `calibrator.json`, `manifest.json`, `evaluation.md`, `calibration.png`,
   `feature_importance.png`, `test_predictions.csv`.

## Results on the synthetic test split (`challenger-lgbm-20261127-12af7e39`)

| Model | ROC-AUC | PR-AUC | Brier | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|---:|
| LightGBM challenger (calibrated) | 0.830 | 0.320 | 0.0569 | 0.315 | 0.593 | 0.411 |
| Logistic regression baseline | 0.824 | 0.352 | 0.0586 | 0.265 | 0.558 | 0.359 |
| Policy scorecard (production) | 0.814 | 0.334 | 0.0584 | 0.333 | 0.389 | 0.359 |

Bootstrap 95 % intervals overlap for all three (LightGBM ROC-AUC 0.788–0.868; scorecard 0.768–0.855). The
ROC-AUC difference LightGBM − scorecard is [−0.002, +0.033], i.e. **not significant**; the challenger's PR-AUC is
the lowest of the three. Top features by mean |SHAP|: net profit margin, interest coverage, debt-to-equity,
cheque returns, current ratio, EBITDA margin. Most missed defaults are healthy-looking borrowers — expected, because
the generator includes a factor no model can observe.

**Conclusion:** on this synthetic data the challenger is comparable to, not better than, the simpler baselines. That
is a reason to keep the scorecard primary and to re-run this pipeline on real outcome data before drawing any
conclusion. The full report is `backend/models/challenger/<version>/evaluation.md`.

## In the application

- Each risk assessment stores `challenger_output` (model version, probability, threshold, top SHAP contributions,
  missing inputs, dataset type, evaluation status). The Risk tab shows it in a separate, dashed "experimental" card
  labelled *Not used for decisions*; Settings → Scoring policy shows the model card and test metrics.
- `GET /api/v1/system/models` returns the primary/challenger summary.
- `CHALLENGER_ENABLED=false` turns it off; a missing or tampered artifact (feature-set mismatch) is reported as
  unavailable and never breaks an assessment.
