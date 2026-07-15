# UC11 Supporting Microservices

`workflow.json` calls two local HTTP endpoints that n8n itself cannot provide.
Both are included here, fully working, tested against your actual SBP
AML/CFT/CPF Regulations PDF.

```
services/
├── requirements.txt
├── ml_service/
│   ├── train_exposure_model.py   # trains + calibrates the classifier
│   ├── ml_service.py             # FastAPI app -> port 5001
│   ├── exposure_model.joblib     # (generated) trained model
│   ├── cutoff.json               # (generated) calibrated_hold_cutoff + metrics
│   └── model_card.md             # (generated) calibration write-up
└── rag_service/
    ├── sbp_aml_regulations.pdf   # the source regulation document
    ├── load_regulations.py       # chunks the PDF into Chroma, once
    ├── rag_service.py            # FastAPI app -> port 8000
    ├── chroma_db/                # (generated) persistent vector store
    └── tfidf_vectorizer.joblib   # (generated) embedding model
```

## 1. Install once

```bash
cd services
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

## 2. Cross-Border Exposure classifier (port 5001)

Trains a calibrated GradientBoostingClassifier on synthetic corridor data and
derives `calibrated_hold_cutoff` from the precision-recall curve (see
`model_card.md` for the exact method — this is what "Score Cross-Border
Exposure (ML)" in the workflow calls).

```bash
cd ml_service
python3 train_exposure_model.py      # writes exposure_model.joblib + cutoff.json
uvicorn ml_service:app --port 5001 --reload
```

Test it:
```bash
curl -X POST http://localhost:5001/api/score-jurisdiction-exposure \
  -H "Content-Type: application/json" \
  -d '{"customer_country":"Pakistan","counterparty_country":"Country-X","amount":500000,"corridor_history":true,"hops":3}'
# -> {"exposure_score":0.96,"calibrated_hold_cutoff":0.7934,"hold_recommended":true}
```

**To use real data instead of the synthetic generator:** replace the
`generate_dataset()` function in `train_exposure_model.py` with a query
against your sandbox `transactions` + `jurisdiction_list` tables once you
have enough labeled history, keep `featurize()` unchanged in both the
training script and `ml_service.py`, and re-run.

## 3. SBP Regulations RAG service (port 8000)

Chunks the SBP AML/CFT/CPF Regulations PDF by Regulation/§ clause (268
chunks extracted from your PDF) and serves nearest-clause retrieval.

```bash
cd rag_service
python3 load_regulations.py          # run once, or whenever the PDF changes
uvicorn rag_service:app --port 8000 --reload
```

Test it:
```bash
curl -X POST http://localhost:8000/api/v1/collections/sbp_regulations/query \
  -H "Content-Type: application/json" \
  -d '{"query_texts":["high risk jurisdiction counter measures cross border EDD"],"n_results":3,"where":{"regulation":{"$in":["Reg 14","Reg 2","Reg 9"]}}}'
```

**Embedding note:** Chroma's default embedder downloads an ONNX model from
the internet on first use, which many locked-down sandbox/CI environments
block. To keep this fully offline and deterministic, both scripts instead
fit and share a local `TfidfVectorizer`. For stronger semantic recall in
production, swap `TfidfVectorizer` in `load_regulations.py` and
`rag_service.py` for `sentence-transformers` (`all-MiniLM-L6-v2`) or an
OpenAI/Groq embeddings endpoint — the Chroma collection schema and the
n8n-facing API contract don't need to change either way.

## 4. Point n8n at both services

Both ports match what's already configured in `workflow.json` — no node
edits needed if you run them locally as shown above:
- `Score Cross-Border Exposure (ML)` → `http://localhost:5001/api/score-jurisdiction-exposure`
- `Query Vector Store - Reg14` / `Query Vector Store - EDD Memo` → `http://localhost:8000/api/v1/collections/sbp_regulations/query`

If n8n itself runs inside Docker, replace `localhost` in those two node URLs
with `host.docker.internal` (or your Docker network's bridge IP) so the
container can reach the services running on your host.

## 5. Still needed to fully run the workflow

- **Postgres**: apply the shared schema from your blueprint `.md`, and seed
  `jurisdiction_list` / `customers` with the synthetic test data in the
  documentation.
- **Groq API key**: the two AI HTTP Request nodes (`AI Parse Regulatory
  Notice`, `AI Draft EDD Memo`) need a `groqApi` credential in n8n, or swap
  them for n8n's native LLM node pointed at any provider you prefer.
- **Slack / Email credentials**: only required if you want the notification
  nodes to actually fire; otherwise disable/delete them for a Postgres-only
  test run.
