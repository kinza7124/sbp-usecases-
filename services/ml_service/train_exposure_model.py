"""
Trains the calibrated Cross-Border Exposure classifier used by
Node "Score Cross-Border Exposure (ML)" in UC11.

Regulatory basis: Regulation 9 §2 (EDD for correspondent relationships with
high-risk-jurisdiction respondent banks) and Regulation 14 §1 (counter-measures
for high-risk jurisdictions) give no fixed numeric threshold for indirect /
correspondent-chain exposure -- this script derives one from data instead of
hand-picking a constant, and writes the derivation into model_card.md so the
cutoff is defensible/reproducible for SBP inspection purposes.

Run:
    python train_exposure_model.py

Produces:
    exposure_model.joblib   - trained scikit-learn Pipeline (scaler + classifier)
    cutoff.json             - {"calibrated_hold_cutoff": <float>, "metrics": {...}}
    model_card.md           - human-readable documentation of the calibration
"""
import json
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import train_test_split
from sklearn.metrics import precision_recall_curve, roc_auc_score, classification_report
import joblib

RNG = np.random.default_rng(42)

HIGH_RISK_COUNTRIES = {"Country-X", "Country-Y", "North Korea", "Iran", "Myanmar"}
MONITORED_COUNTRIES = {"Country-Z", "Philippines", "Turkey", "South Africa"}

# ---------------------------------------------------------------------------
# 1. Synthetic corridor / transaction dataset
#    (mirrors the FYP's existing synthetic-data-generation approach; swap this
#    block for a query against the real `transactions` + `jurisdiction_list`
#    tables once historical sandbox data exists)
# ---------------------------------------------------------------------------
def synth_country(rng, pool):
    return rng.choice(list(pool))

def generate_dataset(n=6000):
    rows = []
    all_countries = list(HIGH_RISK_COUNTRIES | MONITORED_COUNTRIES) + \
        ["Pakistan", "UAE", "UK", "USA", "Singapore", "China"]
    for _ in range(n):
        customer_country = RNG.choice(all_countries, p=_weights(all_countries))
        counterparty_country = RNG.choice(all_countries, p=_weights(all_countries))
        amount = float(np.round(np.exp(RNG.normal(10, 1.6)), 2))  # heavy-tailed PKR amounts
        corridor_history = bool(RNG.random() < 0.35)  # prior transactions on this corridor
        hops = RNG.integers(1, 5)  # number of intermediary/correspondent institutions

        cc_is_high = customer_country in HIGH_RISK_COUNTRIES
        cp_is_high = counterparty_country in HIGH_RISK_COUNTRIES
        cc_is_mon = customer_country in MONITORED_COUNTRIES
        cp_is_mon = counterparty_country in MONITORED_COUNTRIES

        # latent "true" indirect-exposure risk used only to LABEL the synthetic
        # data -- the model must learn this relationship from features alone,
        # it is never hard-coded into the serving logic.
        latent = (
            3.0 * (cc_is_high or cp_is_high)
            + 1.2 * (cc_is_mon or cp_is_mon)
            + 0.35 * hops
            + 0.6 * corridor_history * (cc_is_high or cp_is_high)
            + 0.4 * np.log1p(amount) / 10
            + RNG.normal(0, 0.8)
        )
        label = int(latent > 3.2)

        rows.append({
            "customer_country": customer_country,
            "counterparty_country": counterparty_country,
            "amount": amount,
            "corridor_history": corridor_history,
            "hops": hops,
            "label": label,
        })
    return pd.DataFrame(rows)

def _weights(countries):
    # skew slightly toward normal-risk corridors, as in real transaction mix
    w = np.array([2.0 if c in HIGH_RISK_COUNTRIES else
                   1.3 if c in MONITORED_COUNTRIES else 3.0
                   for c in countries], dtype=float)
    return w / w.sum()

# ---------------------------------------------------------------------------
# 2. Feature engineering (must match ml_service.py exactly)
# ---------------------------------------------------------------------------
def featurize(df: pd.DataFrame) -> pd.DataFrame:
    feats = pd.DataFrame()
    feats["customer_high_risk"] = df["customer_country"].isin(HIGH_RISK_COUNTRIES).astype(int)
    feats["customer_monitored"] = df["customer_country"].isin(MONITORED_COUNTRIES).astype(int)
    feats["counterparty_high_risk"] = df["counterparty_country"].isin(HIGH_RISK_COUNTRIES).astype(int)
    feats["counterparty_monitored"] = df["counterparty_country"].isin(MONITORED_COUNTRIES).astype(int)
    feats["log_amount"] = np.log1p(df["amount"])
    feats["corridor_history"] = df["corridor_history"].astype(int)
    feats["hops"] = df["hops"]
    return feats

# ---------------------------------------------------------------------------
# 3. Train + calibrate cutoff from the precision-recall curve
# ---------------------------------------------------------------------------
def main():
    df = generate_dataset()
    X = featurize(df)
    y = df["label"]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.25, random_state=42, stratify=y)

    pipe = Pipeline([
        ("scaler", StandardScaler()),
        ("clf", GradientBoostingClassifier(
            n_estimators=150, max_depth=3, learning_rate=0.08, random_state=42)),
    ])
    pipe.fit(X_train, y_train)

    proba_test = pipe.predict_proba(X_test)[:, 1]
    auc = roc_auc_score(y_test, proba_test)

    precision, recall, thresholds = precision_recall_curve(y_test, proba_test)
    # Target: highest-precision cutoff that still keeps recall >= 0.85, i.e. we
    # accept some manual-review overhead (false positives) rather than missing
    # genuine high-risk-jurisdiction exposure (Reg 14 §1 is a strict-liability
    # style obligation -- false negatives are the costlier error).
    min_recall = 0.85
    eligible = [(p, r, t) for p, r, t in zip(precision[:-1], recall[:-1], thresholds) if r >= min_recall]
    if eligible:
        best = max(eligible, key=lambda x: x[0])
        cutoff = float(best[2])
    else:
        cutoff = float(np.median(thresholds))

    preds = (proba_test >= cutoff).astype(int)
    report = classification_report(y_test, preds, output_dict=True)

    joblib.dump(pipe, "exposure_model.joblib")
    with open("cutoff.json", "w") as f:
        json.dump({
            "calibrated_hold_cutoff": round(cutoff, 4),
            "target_min_recall": min_recall,
            "test_auc": round(float(auc), 4),
            "precision_at_cutoff": round(report["1"]["precision"], 4),
            "recall_at_cutoff": round(report["1"]["recall"], 4),
            "feature_order": list(X.columns),
        }, f, indent=2)

    with open("model_card.md", "w") as f:
        f.write(f"""# Model Card — Cross-Border Exposure Classifier (UC11)

**Used by:** n8n node `Score Cross-Border Exposure (ML)`
**Regulatory basis:** Regulation 9 §2, Regulation 14 §1 (SBP AML/CFT/CPF Regulations)
**Purpose:** score INDIRECT jurisdiction exposure (correspondent/intermediary
chains) that a flat country-match rule on `jurisdiction_list` would miss.

## Algorithm
GradientBoostingClassifier (scikit-learn), 150 estimators, depth 3, in a
Pipeline with StandardScaler. Trained on {len(df)} synthetic corridor records.

## Features (in order)
{chr(10).join(f"- {c}" for c in X.columns)}

## Calibration method
The decision cutoff is NOT a hand-picked constant. It is the highest-precision
point on the test-set precision-recall curve subject to recall >= {min_recall}
(false negatives -- missing genuine high-risk exposure -- are treated as the
costlier error under Reg 14 §1's strict counter-measure obligation).

## Test-set metrics
- ROC-AUC: {auc:.4f}
- calibrated_hold_cutoff: {cutoff:.4f}
- Precision @ cutoff: {report['1']['precision']:.4f}
- Recall @ cutoff: {report['1']['recall']:.4f}

## Retraining
Re-run `train_exposure_model.py` whenever real sandbox `transactions` +
`jurisdiction_list` history is available in place of the synthetic generator,
and redeploy `exposure_model.joblib` + `cutoff.json` to the ml_service.
""")

    print(f"AUC={auc:.4f}  cutoff={cutoff:.4f}  "
          f"precision={report['1']['precision']:.4f}  recall={report['1']['recall']:.4f}")

if __name__ == "__main__":
    main()
