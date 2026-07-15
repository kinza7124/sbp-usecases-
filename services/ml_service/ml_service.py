"""
Cross-Border Exposure scoring microservice for UC11.

Matches the n8n node "Score Cross-Border Exposure (ML)" exactly:
    POST http://localhost:5001/api/score-jurisdiction-exposure
    Body: {
        "customer_country": "...",
        "counterparty_country": "...",
        "amount": 123456.0,
        "corridor_history": true
    }
    Response: {
        "exposure_score": 0.83,
        "calibrated_hold_cutoff": 0.7934,
        "hold_recommended": true
    }

Run:
    pip install -r requirements.txt
    uvicorn ml_service:app --port 5001 --reload
"""
import json
import numpy as np
import joblib
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="UC11 Cross-Border Exposure Scoring Service")

MODEL = joblib.load("exposure_model.joblib")
with open("cutoff.json") as f:
    CUTOFF_INFO = json.load(f)
CUTOFF = CUTOFF_INFO["calibrated_hold_cutoff"]

HIGH_RISK_COUNTRIES = {"Country-X", "Country-Y", "North Korea", "Iran", "Myanmar"}
MONITORED_COUNTRIES = {"Country-Z", "Philippines", "Turkey", "South Africa"}


class ExposureRequest(BaseModel):
    customer_country: str = ""
    counterparty_country: str = ""
    amount: float = 0.0
    corridor_history: bool = False
    hops: int = 2  # optional: number of correspondent/intermediary institutions


def featurize(req: ExposureRequest) -> np.ndarray:
    return np.array([[
        int(req.customer_country in HIGH_RISK_COUNTRIES),
        int(req.customer_country in MONITORED_COUNTRIES),
        int(req.counterparty_country in HIGH_RISK_COUNTRIES),
        int(req.counterparty_country in MONITORED_COUNTRIES),
        np.log1p(max(req.amount, 0.0)),
        int(req.corridor_history),
        req.hops,
    ]])


@app.post("/api/score-jurisdiction-exposure")
def score_exposure(req: ExposureRequest):
    X = featurize(req)
    score = float(MODEL.predict_proba(X)[0, 1])
    return {
        "exposure_score": round(score, 4),
        "calibrated_hold_cutoff": CUTOFF,
        "hold_recommended": score >= CUTOFF,
    }


@app.get("/health")
def health():
    return {"status": "ok", "cutoff": CUTOFF, "model_metrics": CUTOFF_INFO}
