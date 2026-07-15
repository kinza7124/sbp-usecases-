"""
RAG microservice for UC11 -- wraps the local Chroma collection with the
exact request/response shape the n8n HTTP Request nodes
("Query Vector Store - Reg14" and "Query Vector Store - EDD Memo") expect.

    POST http://localhost:8000/api/v1/collections/sbp_regulations/query
    Body: {
        "query_texts": ["high risk jurisdiction counter measures cross border EDD"],
        "n_results": 3,
        "where": {"regulation": {"$in": ["Reg 14", "Reg 2", "Reg 9"]}}
    }
    Response: {
        "documents": [["<clause text 1>", "<clause text 2>", "<clause text 3>"]],
        "metadatas": [[{"regulation": "Reg 14"}, ...]],
        "ids": [["reg14_clause0_133", ...]]
    }

Run (after load_regulations.py has been run at least once):
    pip install -r requirements.txt
    uvicorn rag_service:app --port 8000 --reload
"""
import joblib
import numpy as np
import chromadb
from fastapi import FastAPI
from pydantic import BaseModel
from typing import Any, Dict, List, Optional

app = FastAPI(title="UC11 SBP Regulations RAG Service")

CHROMA_PATH = "./chroma_db"
VECTORIZER_PATH = "./tfidf_vectorizer.joblib"

client = chromadb.PersistentClient(path=CHROMA_PATH)
collection = client.get_collection("sbp_regulations")
vectorizer = joblib.load(VECTORIZER_PATH)


class QueryRequest(BaseModel):
    query_texts: List[str]
    n_results: int = 3
    where: Optional[Dict[str, Any]] = None


def embed(texts: List[str]) -> np.ndarray:
    X = vectorizer.transform(texts).toarray().astype(np.float32)
    norms = np.linalg.norm(X, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return X / norms


@app.post("/api/v1/collections/sbp_regulations/query")
def query_regulations(req: QueryRequest):
    query_embeddings = embed(req.query_texts).tolist()
    result = collection.query(
        query_embeddings=query_embeddings,
        n_results=req.n_results,
        where=req.where,
    )
    return {
        "documents": result["documents"],
        "metadatas": result["metadatas"],
        "ids": result["ids"],
    }


@app.get("/health")
def health():
    return {"status": "ok", "chunks_loaded": collection.count()}
