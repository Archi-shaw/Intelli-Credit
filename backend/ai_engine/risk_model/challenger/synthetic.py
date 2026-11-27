"""Reproducible SYNTHETIC labelled dataset for controlled engineering experiments.

THIS IS NOT REAL LOAN-PERFORMANCE DATA. No historical default is represented. Labels are drawn from an explicit,
documented formula so the training/evaluation pipeline can be exercised end to end; any metric measured on this
data only says how well a model recovers the generator's own assumptions.

Structure
* Borrowers belong to one of four profiles (healthy, moderate, stressed, irregular) and have a hidden financial
  strength ``q`` and a hidden, never-observed factor ``u`` (e.g. management quality, market shocks).
* Each borrower has 1–3 consecutive annual snapshots (statement metrics + GST/bank signals as of the application).
* Label ``default_12m`` = synthetic default within 12 months after the snapshot. After a synthetic default no
  further snapshots are generated for that borrower (no post-default observations).
* ``q`` and ``u`` are NOT written to the dataset (they would be leakage); ``profile`` is written only for error
  analysis and is excluded from model features.
"""
from __future__ import annotations

import csv
import hashlib
import math
from pathlib import Path

import numpy as np

GENERATOR_VERSION = "synthetic-credit-v1"
PROFILES = ["healthy", "moderate", "stressed", "irregular"]
PROFILE_P = [0.45, 0.30, 0.15, 0.10]
PROFILE_MU = {"healthy": 0.9, "moderate": 0.1, "stressed": -1.0, "irregular": -0.1}
TARGET_DEFAULT_RATE = 0.09

# Label formula (documented in the dataset card): logit = a0 + sum(coef * driver), a0 solved for the target rate.
LABEL_COEFS = {
    "q_strength": -1.35,          # hidden financial strength (better -> fewer defaults)
    "u_unobserved": 0.65,         # hidden factor the model cannot see (irreducible noise)
    "negative_net_worth": 1.6,
    "interest_cover_below_1_25": 0.9,
    "debt_to_revenue_above_0_9": 0.8,
    "irregular_and_circular": 0.9,
    "bank_coverage_below_0_6": 0.6,
    "cheque_returns_capped_5": 0.2,
}

METRIC_COLUMNS = [
    "revenue_from_operations", "prior_revenue_from_operations", "ebitda", "prior_ebitda", "depreciation",
    "finance_costs", "pbt", "tax_expense", "pat", "prior_pat", "total_debt", "long_term_borrowings",
    "short_term_borrowings", "shareholders_equity", "current_assets", "current_liabilities", "inventory",
    "trade_receivables", "cash_and_equivalents", "total_assets", "operating_cash_flow",
]
SIGNAL_COLUMNS = ["gst_turnover", "bank_business_receipts", "gst_bank_variance_pct", "circular_flag", "cheque_returns"]
COLUMNS = ["borrower_id", "snapshot", "fiscal_year_end", "profile", "data_origin"] + METRIC_COLUMNS + SIGNAL_COLUMNS + ["default_12m"]


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


def generate(n_borrowers: int = 4000, seed: int = 20261127) -> list[dict]:
    rng = np.random.default_rng(seed)
    nan = float("nan")
    draft = []  # rows before labels: (row, drivers, uniform, borrower_index)
    for b in range(n_borrowers):
        profile = PROFILES[int(rng.choice(4, p=PROFILE_P))]
        q = rng.normal(PROFILE_MU[profile], 0.6)
        u = rng.normal(0.0, 1.0)
        rev = float(np.clip(math.exp(rng.normal(math.log(8e7), 0.9)), 5e6, 3e9))
        n_snap = int(rng.choice([1, 2, 3], p=[0.3, 0.4, 0.3]))
        first_fy = int(rng.integers(2020, 2024))
        has_prior = rng.random() < 0.7
        prior = None
        if has_prior:
            g0 = rng.normal(0.07 + 0.06 * q, 0.10)
            prior = {"revenue": rev / (1 + g0), "ebitda": None, "pat": None}
        docs_missing = rng.random() < 0.12  # bank + GST not provided for this borrower
        for s in range(n_snap):
            qt = q + rng.normal(0, 0.25)
            if s > 0:
                rev = rev * (1 + rng.normal(0.07 + 0.06 * qt, 0.10))
            em = float(np.clip(rng.normal(0.11 + 0.035 * qt, 0.035), -0.30, 0.40))
            ebitda = rev * em
            dep = rev * rng.uniform(0.015, 0.04)
            dtr = float(np.clip(rng.normal(0.38 - 0.11 * qt, 0.14), 0.0, 2.5))
            debt = rev * dtr
            lt = debt * rng.uniform(0.3, 0.8)
            rate = float(np.clip(rng.normal(0.105 - 0.006 * qt, 0.012), 0.07, 0.19))
            fin = debt * rate
            pbt = ebitda - dep - fin
            tax = max(pbt, 0.0) * 0.25
            pat = pbt - tax
            equity = rev * float(np.clip(rng.normal(0.33 + 0.13 * qt, 0.12), -0.5, 1.5))
            cl = rev * rng.uniform(0.10, 0.24)
            ca = cl * float(np.clip(rng.normal(1.35 + 0.22 * qt, 0.25), 0.25, 3.5))
            inv = ca * rng.uniform(0.25, 0.45)
            recv = rev * float(np.clip(rng.normal(68 - 11 * qt, 18), 10, 220)) / 365.0
            cash = ca * rng.uniform(0.03, 0.12)
            ta = ca + rev * rng.uniform(0.2, 0.6)
            ocf = ebitda * rng.uniform(0.55, 0.95) - tax + rev * rng.normal(0, 0.025) - (rev * 0.02 if qt < -0.8 else 0.0)
            if profile == "irregular":
                coverage, circular, cheques = rng.uniform(0.2, 0.65), float(rng.random() < 0.55), int(rng.poisson(1.5))
            else:
                coverage = float(np.clip(rng.normal(0.97, 0.04), 0.7, 1.1))
                circular, cheques = float(rng.random() < 0.02), int(rng.poisson(0.15 + 0.9 * max(0.0, -qt)))
            gst = rev * rng.normal(1.0, 0.025)
            bank = gst * coverage
            icov = (pbt + fin) / fin if fin > 0 else nan
            row = {
                "borrower_id": f"SYN-{b:05d}", "snapshot": s + 1, "fiscal_year_end": f"{first_fy + s}-03-31",
                "profile": profile, "data_origin": "SYNTHETIC",
                "revenue_from_operations": rev, "prior_revenue_from_operations": prior["revenue"] if prior else nan,
                "ebitda": ebitda, "prior_ebitda": prior["ebitda"] if prior and prior["ebitda"] is not None else nan,
                "depreciation": dep, "finance_costs": fin, "pbt": pbt, "tax_expense": tax, "pat": pat,
                "prior_pat": prior["pat"] if prior and prior["pat"] is not None else nan,
                "total_debt": debt, "long_term_borrowings": lt, "short_term_borrowings": debt - lt,
                "shareholders_equity": equity, "current_assets": ca, "current_liabilities": cl, "inventory": inv,
                "trade_receivables": recv if rng.random() > 0.05 else nan, "cash_and_equivalents": cash,
                "total_assets": ta, "operating_cash_flow": ocf if rng.random() > 0.08 else nan,
                "gst_turnover": nan if docs_missing else gst, "bank_business_receipts": nan if docs_missing else bank,
                "gst_bank_variance_pct": nan if docs_missing else abs(bank - gst) / gst * 100.0,
                "circular_flag": nan if docs_missing else circular, "cheque_returns": nan if docs_missing else float(cheques),
            }
            drivers = np.array([qt, u, float(equity <= 0), float(not math.isnan(icov) and icov < 1.25), float(dtr > 0.9),
                                float(profile == "irregular" and circular == 1.0), float(coverage < 0.6), min(cheques, 5)])
            draft.append((row, drivers, rng.random(), b))
            prior = {"revenue": rev, "ebitda": ebitda, "pat": pat}

    coefs = np.array(list(LABEL_COEFS.values()))
    lin = np.array([d @ coefs for _, d, _, _ in draft])
    lo, hi = -15.0, 5.0  # solve a0 so the expected default rate equals the target (deterministic bisection)
    for _ in range(80):
        mid = (lo + hi) / 2
        if _sigmoid(mid + lin).mean() > TARGET_DEFAULT_RATE:
            hi = mid
        else:
            lo = mid
    a0 = (lo + hi) / 2
    rows, defaulted = [], set()
    for (row, _, r, b), z in zip(draft, lin):
        if b in defaulted:
            continue  # no observations after a synthetic default
        row["default_12m"] = int(r < _sigmoid(a0 + z))
        if row["default_12m"]:
            defaulted.add(b)
        rows.append(row)
    for row in rows:
        row["_intercept"] = a0
    return rows


def write_csv(rows: list[dict], path: str | Path) -> str:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(COLUMNS)
        for r in rows:
            w.writerow([_fmt(r[c]) for c in COLUMNS])
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fmt(v):
    if isinstance(v, float):
        return "" if math.isnan(v) else f"{v:.6g}"
    return v


def read_csv(path: str | Path) -> list[dict]:
    out = []
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            row = {}
            for k, v in r.items():
                if k in ("borrower_id", "fiscal_year_end", "profile", "data_origin"):
                    row[k] = v
                elif k in ("snapshot", "default_12m"):
                    row[k] = int(v)
                else:
                    row[k] = float(v) if v not in ("", None) else float("nan")
            out.append(row)
    return out


def dataset_card(rows: list[dict], sha256: str, seed: int, n_borrowers: int) -> str:
    n = len(rows)
    pos = sum(r["default_12m"] for r in rows)
    by_profile = {p: [r for r in rows if r["profile"] == p] for p in PROFILES}
    lines = [
        "# Dataset card — SYNTHETIC credit default dataset (engineering use only)",
        "",
        "> **Synthetic.** Generated by `ai_engine.risk_model.challenger.synthetic`. It contains no real borrower, no real",
        "> financial statement and no historical loan outcome. Labels come from the formula below. Metrics computed on",
        "> this data measure how well a model recovers the generator's assumptions — **not** real-world lending performance.",
        "",
        f"- Generator: `{GENERATOR_VERSION}`, seed `{seed}`, {n_borrowers} borrowers → {n} annual snapshots",
        f"- SHA-256: `{sha256}`",
        f"- Label `default_12m` (synthetic default within 12 months of the snapshot): {pos} positives ({pos / n:.1%})",
        "",
        "| Profile | Snapshots | Synthetic default rate |",
        "|---|---:|---:|",
        *[f"| {p} | {len(v)} | {(sum(r['default_12m'] for r in v) / len(v)):.1%} |" for p, v in by_profile.items() if v],
        "",
        "## Generation assumptions",
        "- Profiles (prior probability): healthy 45 %, moderate 30 %, stressed 15 %, irregular 10 %.",
        "- Hidden strength `q ~ N(mu_profile, 0.6)` with yearly shocks `N(0, 0.25)`; hidden factor `u ~ N(0, 1)` is never observed.",
        "- Statements: revenue lognormal around ₹8 Cr; growth, EBITDA margin, leverage, interest rate, equity, liquidity,",
        "  receivable days and operating cash flow are noisy functions of `q`. Accounting relations hold for PBT, tax and PAT.",
        "- Irregular profile: bank receipts cover only 20–65 % of GST turnover, 55 % show round-trip transactions, more cheque returns.",
        "- Missingness (never zero-filled): operating cash flow 8 %, receivables 5 %, bank + GST documents absent for 12 % of borrowers;",
        "  growth needs a prior period (absent for ~30 % of first snapshots).",
        "- Survivorship: after a synthetic default the borrower produces no further snapshots.",
        "",
        "## Label formula",
        f"`P(default_12m) = sigmoid(a0 + Σ coef·driver)`, with `a0 = {rows[0]['_intercept']:.4f}` solved for a {TARGET_DEFAULT_RATE:.0%} expected rate over all drafted snapshots (the realised rate is lower because no snapshots follow a default):",
        "",
        "| Driver | Coefficient |",
        "|---|---:|",
        *[f"| {k} | {v:+.2f} |" for k, v in LABEL_COEFS.items()],
        "",
        "## Leakage controls",
        "- Only information available at application time is stored; no post-outcome fields (arrears, restructuring, recoveries).",
        "- Hidden generator variables (`q`, `u`) are not in the file; `profile` is for error analysis only and is excluded from features.",
        "- Train / validation / test splits are made by `borrower_id`, so no borrower appears in more than one split.",
    ]
    return "\n".join(lines) + "\n"
