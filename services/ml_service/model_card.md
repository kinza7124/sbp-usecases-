# Model Card — Cross-Border Exposure Classifier (UC11)

**Used by:** n8n node `Score Cross-Border Exposure (ML)`
**Regulatory basis:** Regulation 9 §2, Regulation 14 §1 (SBP AML/CFT/CPF Regulations)
**Purpose:** score INDIRECT jurisdiction exposure (correspondent/intermediary
chains) that a flat country-match rule on `jurisdiction_list` would miss.

## Algorithm
GradientBoostingClassifier (scikit-learn), 150 estimators, depth 3, in a
Pipeline with StandardScaler. Trained on 6000 synthetic corridor records.

## Features (in order)
- customer_high_risk
- customer_monitored
- counterparty_high_risk
- counterparty_monitored
- log_amount
- corridor_history
- hops

## Calibration method
The decision cutoff is NOT a hand-picked constant. It is the highest-precision
point on the test-set precision-recall curve subject to recall >= 0.85
(false negatives -- missing genuine high-risk exposure -- are treated as the
costlier error under Reg 14 §1's strict counter-measure obligation).

## Test-set metrics
- ROC-AUC: 0.9688
- calibrated_hold_cutoff: 0.7934
- Precision @ cutoff: 0.9516
- Recall @ cutoff: 0.8531

## Retraining
Re-run `train_exposure_model.py` whenever real sandbox `transactions` +
`jurisdiction_list` history is available in place of the synthetic generator,
and redeploy `exposure_model.joblib` + `cutoff.json` to the ml_service.
