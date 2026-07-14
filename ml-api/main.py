from fastapi import FastAPI
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer
import numpy as np

app = FastAPI(title="ML Semantic Title Matcher")

# Load the local embedding model at startup
# all-MiniLM-L6-v2 is extremely fast and very capable of semantic matching
print("Loading NLP Model... (this may take a few seconds)")
model = SentenceTransformer('all-MiniLM-L6-v2')
print("Model loaded successfully!")

class TitleCompareRequest(BaseModel):
    registered: str
    advertised: str

class TitleCompareResponse(BaseModel):
    registered: str
    advertised: str
    similarity_score: float
    is_match: bool

@app.post("/api/match-title", response_model=TitleCompareResponse)
def match_title(req: TitleCompareRequest):
    # Compute embeddings for both titles
    embeddings = model.encode([req.registered, req.advertised])
    
    # Calculate Cosine Similarity
    # embedding[0] dot embedding[1] (assuming normalized vectors by the model, but let's do safe cosine)
    vec1 = embeddings[0]
    vec2 = embeddings[1]
    
    cosine_sim = np.dot(vec1, vec2) / (np.linalg.norm(vec1) * np.linalg.norm(vec2))
    
    # Convert numpy float to standard float
    score = float(cosine_sim)
    
    # Define semantic threshold. 0.70 is generally a robust threshold for this model.
    # e.g., "Roshan Family Welfare Trust" vs "Roshan Fdn" -> High Score
    # "Roshan Foundation for Children" vs "Roshan Family Welfare Trust" -> Lower Score
    is_match = score >= 0.70
    
    return TitleCompareResponse(
        registered=req.registered,
        advertised=req.advertised,
        similarity_score=score,
        is_match=is_match
    )

@app.post("/api/vector-db/query")
def vector_db_query_mock(payload: dict):
    # Return a simulated RAG context response for the STR Narrative AI Agent
    return {
        "status": "success",
        "retrieved_context": [
            "Historical records indicate multiple instances of funding mismatch.",
            "The advertised operational address is not registered in the official database.",
            "Potential high-risk jurisdiction connections detected in previous wire transfers."
        ],
        "relevance_score": 0.88
    }
