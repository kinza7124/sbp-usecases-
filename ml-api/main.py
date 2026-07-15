from fastapi import FastAPI
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer
import numpy as np
from typing import List, Optional

app = FastAPI(title="ML Semantic Title Matcher")

# Load the local embedding model at startup
# all-MiniLM-L6-v2 is extremely fast and very capable of semantic matching
print("Loading NLP Model... (this may take a few seconds)")
model = SentenceTransformer('all-MiniLM-L6-v2')
print("Model loaded successfully!")

# ---------------------------------------------------------------------------
# Regulation 3 (SBP AML/CFT) knowledge base — Third-Party CDD Reliance
# Verbatim clause text used for RAG in the ThirdParty_CDD_Reliance workflow
# ---------------------------------------------------------------------------
REG3_CLAUSES = [
    {
        "id": "r3_s1",
        "clause_ref": "Regulation 3, §1",
        "text": (
            "SBP-Regulated Entities (REs) may rely on a third party financial institution to "
            "perform CDD measures on their behalf, provided the requirements of this Regulation "
            "are met. Such reliance does not exempt the RE from its own AML/CFT/CPF obligations."
        ),
    },
    {
        "id": "r3_s2",
        "clause_ref": "Regulation 3, §2",
        "text": (
            "The ultimate responsibility for CDD measures remains with the relying SBP-RE. "
            "Reliance on a third party does not transfer or dilute the RE's obligations under "
            "the Anti-Money Laundering Act, 2010 and associated regulations. The RE shall ensure "
            "that any deficiency in the third party's CDD is remediated without delay."
        ),
    },
    {
        "id": "r3_s3",
        "clause_ref": "Regulation 3, §3",
        "text": (
            "SBP-REs must remain responsible for their overall AML/CFT/CPF obligations, including "
            "ongoing monitoring of business relationships, transaction monitoring, and filing of "
            "Suspicious Transaction Reports (STRs) and Currency Transaction Reports (CTRs). These "
            "duties cannot be delegated to the third party under any reliance arrangement."
        ),
    },
    {
        "id": "r3_s4",
        "clause_ref": "Regulation 3, §4",
        "text": (
            "SBP-REs must immediately obtain from the third party all customer identity documents "
            "and CDD files upon request or upon commencement of the business relationship. The "
            "third party must make these records available promptly and the RE must retain them "
            "in accordance with Regulation 8 (Record Keeping) for the prescribed retention period."
        ),
    },
    {
        "id": "r3_s5",
        "clause_ref": "Regulation 3, §5",
        "text": (
            "SBP-REs shall satisfy themselves that the third party financial institution is "
            "regulated, supervised or monitored for AML/CFT/CPF compliance, and that the third "
            "party has measures in place for CDD compliance, record-keeping, and security of "
            "customer information. The RE must document its assessment of the third party's "
            "compliance status and review it at least annually or whenever a material change occurs."
        ),
    },
    {
        "id": "r3_s6",
        "clause_ref": "Regulation 3, §6",
        "text": (
            "SBP-REs must verify compliance with Regulation 14 (Counter Measures for High Risk "
            "Jurisdictions) when the third party is located in or operates from a country "
            "designated as high-risk or under enhanced monitoring by FATF or SBP. In such cases, "
            "Enhanced Due Diligence measures must be applied to the reliance arrangement itself, "
            "including senior management approval and more frequent verification reviews."
        ),
    },
    {
        "id": "r3_s7",
        "clause_ref": "Regulation 3, §7",
        "text": (
            "Where the third party is part of the same corporate group as the relying SBP-RE, "
            "group-wide AML/CFT/CPF compliance rules apply. The RE must verify that the third "
            "party implements group-level AML policies and procedures that are equivalent to or "
            "stricter than the requirements of the SBP AML/CFT/CPF Regulations. A copy of the "
            "group AML policy must be retained on file by the relying RE."
        ),
    },
]

# ---------------------------------------------------------------------------
# Reg 8 (SBP AML/CFT) knowledge base — verbatim clause text used for RAG
# Each entry: { "id", "clause_ref", "text" }
# ---------------------------------------------------------------------------
REG8_CLAUSES = [
    {
        "id": "r8_s2",
        "clause_ref": "Regulation 8, §2",
        "text": (
            "Every Reporting Entity shall maintain all records of transactions for a minimum period "
            "of ten (10) years from the date of transaction. Records must be sufficient to permit "
            "reconstruction of individual transactions so as to provide, if necessary, evidence for "
            "prosecution of criminal activity."
        ),
    },
    {
        "id": "r8_s3",
        "clause_ref": "Regulation 8, §3",
        "text": (
            "The records required to be maintained under §2 shall include: (a) the date of the "
            "transaction; (b) the amount and currency of the transaction; (c) the type/nature of "
            "the transaction (deposit, withdrawal, wire transfer, etc.); and (d) the account "
            "identifier (account number or unique reference) of the parties involved."
        ),
    },
    {
        "id": "r8_s4",
        "clause_ref": "Regulation 8, §4",
        "text": (
            "Upon receipt of a written request from a Law Enforcement Agency (LEA) or competent "
            "authority, the Reporting Entity shall compile and provide a transaction reconstruction "
            "bundle within the timeframe stipulated in the request. The bundle must be certified by "
            "the General Counsel or Compliance Officer and shall reference the specific regulatory "
            "authority under which records are disclosed."
        ),
    },
    {
        "id": "r8_s5",
        "clause_ref": "Regulation 8, §5",
        "text": (
            "Where records are subject to litigation, a court order, or an ongoing regulatory "
            "investigation, the Reporting Entity shall impose a Litigation Hold on those records, "
            "suspending any automatic destruction or archival scheduled under the standard "
            "retention policy. The Hold shall remain in effect until the matter is formally resolved "
            "and the Hold is released in writing by the General Counsel."
        ),
    },
    {
        "id": "r8_s6",
        "clause_ref": "Regulation 8, §6",
        "text": (
            "No records subject to a transaction reconstruction request or litigation hold shall be "
            "disclosed to an external authority without prior written approval from senior management "
            "(General Counsel or Chief Compliance Officer). All disclosures shall be logged in the "
            "audit trail with the name of the approving officer, the recipient authority, and the "
            "date of disclosure."
        ),
    },
    {
        "id": "r8_s7",
        "clause_ref": "Regulation 8, §7",
        "text": (
            "Customer identification records, account opening documents, and business correspondence "
            "related to a customer relationship shall also be retained for ten (10) years from the "
            "date on which the business relationship is ended. These records are subject to the same "
            "litigation hold provisions as transaction records."
        ),
    },
]

# ---------------------------------------------------------------------------
# Unified RAG index — Reg 3 + Reg 8 clauses merged into one searchable index
# The /api/vector-db/query endpoint searches across both regulation sets so
# both UC10 (ThirdParty_CDD_Reliance) and UC14 (Record Retention) workflows
# get the most relevant clause for their query from a single endpoint.
# ---------------------------------------------------------------------------
ALL_CLAUSES = REG3_CLAUSES + REG8_CLAUSES

# Pre-compute embeddings for all clauses once at startup
_clause_texts = [c["text"] for c in ALL_CLAUSES]
_clause_embeddings = model.encode(_clause_texts, convert_to_numpy=True)
print(f"RAG index built: {len(REG3_CLAUSES)} Reg 3 clauses + {len(REG8_CLAUSES)} Reg 8 clauses")

class LeaBundleRequest(BaseModel):
    account_number: str = None
    start_date: str = None
    end_date: str = None
    cover_note: str = None
    records: str = None


@app.post("/mock-lea/receive-bundle")
def mock_lea_receive_bundle(payload: LeaBundleRequest):
    """
    Mock LEA (Law Enforcement Agency) intake endpoint.
    Simulates the FIA/LEA system acknowledging receipt of a transaction
    reconstruction bundle. Returns a receipt reference number.
    In production this would be replaced with the real LEA intake API.
    """
    import uuid
    receipt = f"LEA-RECEIPT-{uuid.uuid4().hex[:12].upper()}"
    return {
        "status": "received",
        "receipt": receipt,
        "message": "Bundle received by LEA intake system. Reference number issued.",
        "account_number": payload.account_number,
    }


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

class VectorQueryRequest(BaseModel):
    query: str
    top_k: Optional[int] = 3


@app.post("/api/vector-db/query")
def vector_db_query(req: VectorQueryRequest):
    """
    Unified semantic RAG endpoint — searches across Reg 3 AND Reg 8 clause
    knowledge bases. Returns the top-k most relevant clauses for the query.

    Used by:
    - UC10 ThirdParty_CDD_Reliance node '3b': query about Reg 3 §5 supervision
    - UC14 Record Retention node 'Retrieve Reg8 Clause': query about Reg 8 §4
    The top-level 'clause' field is what n8n expressions read as $json.clause.
    """
    top_k = max(1, min(req.top_k or 3, len(ALL_CLAUSES)))

    query_embedding = model.encode([req.query], convert_to_numpy=True)[0]

    # Cosine similarity against every clause in the unified index
    scores = []
    for i, clause_emb in enumerate(_clause_embeddings):
        sim = float(
            np.dot(query_embedding, clause_emb)
            / (np.linalg.norm(query_embedding) * np.linalg.norm(clause_emb) + 1e-9)
        )
        scores.append((sim, i))

    scores.sort(key=lambda x: x[0], reverse=True)
    top_results = scores[:top_k]

    results = []
    for sim, idx in top_results:
        c = ALL_CLAUSES[idx]
        results.append(
            {
                "clause_ref": c["clause_ref"],
                "clause": c["text"],
                "relevance_score": round(sim, 4),
            }
        )

    return {
        "status": "success",
        "clause": results[0]["clause"],
        "clause_ref": results[0]["clause_ref"],
        "relevance_score": results[0]["relevance_score"],
        "all_results": results,
    }


# ---------------------------------------------------------------------------
# Mock Registry API — simulates a central bank / regulator registry lookup
# Called by node '3. Query Registry API (mock)' in ThirdParty_CDD_Reliance
# ---------------------------------------------------------------------------
REGISTRY_DATA = {
    "Gulf Trust Bank": {
        "disclosure_text": (
            "Gulf Trust Bank is registered under UAE Central Bank license no. CB-UAE-2019-881. "
            "Current status: License is ACTIVE with no regulatory sanctions or restrictions. "
            "Last annual AML/CFT review passed on 2025-11-15."
        ),
        "status": "active",
    },
    "Offshore Credit Co": {
        "disclosure_text": (
            "Offshore Credit Co was registered under the Central Bank of Country-X. "
            "NOTICE: License SUSPENDED effective 2024-06-01 due to failure to comply with "
            "AML/CFT obligations. Regulatory authority has initiated enforcement proceedings."
        ),
        "status": "suspended",
    },
}

DEFAULT_REGISTRY_RESPONSE = {
    "disclosure_text": (
        "Registry lookup returned no record for the specified entity. "
        "Status cannot be confirmed. Classification defaults to UNKNOWN per SBP Reg 3 §5 "
        "error-handling policy."
    ),
    "status": "unknown",
}


@app.get("/mock-registry/status")
def mock_registry_status(name: str = "", country: str = ""):
    """
    Mock central bank / regulator registry endpoint.
    Called by n8n node '3. Query Registry API (mock)' with query params:
      ?name=Gulf+Trust+Bank&country=UAE
    Returns an unstructured disclosure_text that node '4. AI Interpret Status'
    will classify into active / suspended / unknown.
    """
    record = REGISTRY_DATA.get(name, DEFAULT_REGISTRY_RESPONSE)
    return {
        "entity_name": name,
        "country": country,
        "disclosure_text": record["disclosure_text"],
        "raw_status_hint": record["status"],  # present for transparency; AI node ignores this
    }


# ---------------------------------------------------------------------------
# LLM classify-registry-status — replaces the localhost:8001 endpoint
# Called by node '4. AI Interpret Status (LLM)' in ThirdParty_CDD_Reliance
# Uses the retrieved Reg 3 §5 clause as grounding (no hallucination).
# ---------------------------------------------------------------------------
class ClassifyRegistryRequest(BaseModel):
    third_party_name: str
    disclosure_text: str
    sbp_clause: str = ""


@app.post("/llm/classify-registry-status")
def classify_registry_status(req: ClassifyRegistryRequest):
    """
    Deterministic + semantic classifier for third-party regulatory status.
    Simulates what an LLM node would do: reads the disclosure_text and the
    retrieved Reg 3 §5 clause, then classifies into active / suspended / unknown.

    Uses keyword-based rules grounded in Reg 3 §5 language
    ('regulated, supervised or monitored') — deterministic, auditable, no model
    hallucination risk. Returns structured JSON matching the n8n Set node mapping.
    """
    text_lower = req.disclosure_text.lower()

    if any(kw in text_lower for kw in ["suspended", "revoked", "enforcement", "sanctions", "restricted"]):
        status = "suspended"
        reasoning = (
            f"Disclosure text contains indicators of regulatory action (suspended/revoked/enforcement). "
            f"Per {req.sbp_clause[:80]}... the third party cannot be considered 'regulated, supervised "
            f"or monitored' for AML/CFT compliance. Classification: SUSPENDED."
        )
        confidence = 0.95
    elif any(kw in text_lower for kw in ["active", "current", "no sanctions", "passed", "compliant", "registered"]):
        status = "active"
        reasoning = (
            f"Disclosure text confirms active license with no adverse regulatory findings. "
            f"Per {req.sbp_clause[:80]}... the third party satisfies the 'regulated, supervised "
            f"or monitored' standard under Regulation 3 §5. Classification: ACTIVE."
        )
        confidence = 0.92
    else:
        status = "unknown"
        reasoning = (
            "Disclosure text is ambiguous or does not contain sufficient indicators to confirm "
            "regulatory standing. Per Reg 3 §5, the RE cannot satisfy itself of the third party's "
            "compliance status. Defaulting to UNKNOWN — RELIANCE_BREACH evaluation will apply."
        )
        confidence = 0.60

    return {
        "regulatory_status": status,
        "reasoning": reasoning,
        "confidence": confidence,
        "third_party_name": req.third_party_name,
    }
