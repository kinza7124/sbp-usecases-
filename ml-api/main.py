import os
import json
import joblib
from fastapi import FastAPI
from pydantic import BaseModel, Field
from sentence_transformers import SentenceTransformer
import numpy as np
from typing import List, Optional

from rapidfuzz import fuzz
from sklearn.metrics.pairwise import cosine_similarity
from regulation_clauses import CLAUSES, keyword_hits

app = FastAPI(title="ML Semantic Title Matcher")

# Load the local embedding model at startup
print("Loading NLP Model... (this may take a few seconds)")
model = SentenceTransformer('all-MiniLM-L6-v2')
print("Model loaded successfully!")

HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
# Load trained models + their calibrated thresholds (produced by
# train_name_model.py / train_title_model.py -- see model cards in the
# accompanying *_meta.json files)
# ---------------------------------------------------------------------------
name_clf = joblib.load(os.path.join(HERE, "name_model.joblib"))
with open(os.path.join(HERE, "name_model_meta.json")) as f:
    name_meta = json.load(f)
NAME_HIGH_CONFIDENCE = name_meta["thresholds"]["high_confidence"]

title_bundle = joblib.load(os.path.join(HERE, "title_model.joblib"))
title_clf = title_bundle["clf"]
title_vectorizer = title_bundle["vectorizer"]
with open(os.path.join(HERE, "title_model_meta.json")) as f:
    title_meta = json.load(f)
TITLE_MATCH_THRESHOLD = title_meta["thresholds"]["match_threshold"]

# ---------------------------------------------------------------------------
# Synthetic in-memory fallback watchlist / PEP list
# ---------------------------------------------------------------------------
FALLBACK_CANDIDATES = [
    {"source": "pep_list", "entity_name": "Salman Qureshi", "id_number": "35202-1111111-1", "list_type": "PEP-Domestic"},
    {"source": "watchlists", "entity_name": "Ahmed Raza Baloch", "id_number": "42101-0000001-1", "list_type": "DP"},
    {"source": "watchlists", "entity_name": "Farhat ul Nisa", "id_number": "61101-0000002-2", "list_type": "PP"},
]


def _fetch_candidates() -> List[dict]:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        return FALLBACK_CANDIDATES
    try:
        import psycopg2
        import psycopg2.extras
        conn = psycopg2.connect(db_url, connect_timeout=2)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT 'watchlists' AS source, entity_name, id_number, list_type
            FROM watchlists
            UNION ALL
            SELECT 'pep_list' AS source, full_name AS entity_name, id_number, pep_category AS list_type
            FROM pep_list
            """
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [dict(r) for r in rows] if rows else FALLBACK_CANDIDATES
    except Exception:
        return FALLBACK_CANDIDATES


def _name_features(name_a: str, name_b: str, id_a, id_b) -> np.ndarray:
    import jellyfish
    a, b = name_a.lower().strip(), name_b.lower().strip()
    token_sort = fuzz.token_sort_ratio(a, b) / 100.0
    token_set = fuzz.token_set_ratio(a, b) / 100.0
    partial = fuzz.partial_ratio(a, b) / 100.0
    try:
        soundex_match = 1.0 if jellyfish.soundex(a.split()[0]) == jellyfish.soundex(b.split()[0]) else 0.0
    except IndexError:
        soundex_match = 0.0
    id_exact = 1.0 if (id_a and id_b and str(id_a).strip() == str(id_b).strip()) else 0.0
    return np.array([[token_sort, token_set, partial, soundex_match, id_exact]])


def _title_features(reg_title: str, adv_title: str) -> np.ndarray:
    a, b = reg_title.lower().strip(), adv_title.lower().strip()
    token_set = fuzz.token_set_ratio(a, b) / 100.0
    partial = fuzz.partial_ratio(a, b) / 100.0
    a_tokens, b_tokens = set(a.split()), set(b.split())
    jaccard = len(a_tokens & b_tokens) / max(len(a_tokens | b_tokens), 1)
    
    try:
        tfidf = title_vectorizer.transform([a, b])
    except Exception:
        # Fallback if the loaded vectorizer was saved before being fitted
        from sklearn.feature_extraction.text import TfidfVectorizer
        try:
            temp_vec = TfidfVectorizer(**title_vectorizer.get_params()).fit([a, b])
            tfidf = temp_vec.transform([a, b])
        except ValueError:
            # Occurs if vocabulary is entirely empty (e.g. stop words only)
            tfidf_cos = 0.0
            return np.array([[token_set, partial, jaccard, tfidf_cos]])
            
    tfidf_cos = float(cosine_similarity(tfidf[0], tfidf[1])[0][0])
    return np.array([[token_set, partial, jaccard, tfidf_cos]])


# ---------------------------------------------------------------------------
# Regulation 3 (SBP AML/CFT) knowledge base
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
# Reg 8 (SBP AML/CFT) knowledge base
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
# Unified RAG index — Reg 3 + Reg 8 + Reg 6 clauses merged into one searchable index
# ---------------------------------------------------------------------------
# Format the CLAUSES from regulation_clauses.py to match the {"id", "clause_ref", "text"} format
_reg6_clauses = [{"id": c["id"], "clause_ref": c["citation"], "text": c["text"]} for c in CLAUSES]
ALL_CLAUSES = REG3_CLAUSES + REG8_CLAUSES + _reg6_clauses

# Pre-compute embeddings for all clauses once at startup
_clause_texts = [c["text"] for c in ALL_CLAUSES]
_clause_embeddings = model.encode(_clause_texts, convert_to_numpy=True)
print(f"RAG index built: {len(ALL_CLAUSES)} clauses in total")

class LeaBundleRequest(BaseModel):
    account_number: str = None
    start_date: str = None
    end_date: str = None
    cover_note: str = None
    records: str = None


@app.post("/mock-lea/receive-bundle")
def mock_lea_receive_bundle(payload: LeaBundleRequest):
    import uuid
    receipt = f"LEA-RECEIPT-{uuid.uuid4().hex[:12].upper()}"
    return {
        "status": "received",
        "receipt": receipt,
        "message": "Bundle received by LEA intake system. Reference number issued.",
        "account_number": payload.account_number,
    }


# ---------------------------------------------------------------------------
# UC7: Match Name endpoint
# ---------------------------------------------------------------------------
class MatchNameRequest(BaseModel):
    name: str
    cnic: Optional[str] = Field(default=None)


class MatchNameResponse(BaseModel):
    match_probability: float
    matched_entity: Optional[str] = None
    matched_list_type: Optional[str] = None
    verdict: str
    threshold_used: float


@app.post("/api/match-name", response_model=MatchNameResponse)
def match_name(req: MatchNameRequest):
    candidates = _fetch_candidates()
    best_prob = 0.0
    best_candidate = None
    for cand in candidates:
        feats = _name_features(req.name, cand["entity_name"], req.cnic, cand.get("id_number"))
        prob = float(name_clf.predict_proba(feats)[0][1])
        if prob > best_prob:
            best_prob = prob
            best_candidate = cand

    verdict = "alert" if best_prob >= NAME_HIGH_CONFIDENCE else "clear"
    return MatchNameResponse(
        match_probability=round(best_prob, 4),
        matched_entity=best_candidate["entity_name"] if best_candidate and verdict == "alert" else None,
        matched_list_type=best_candidate["list_type"] if best_candidate and verdict == "alert" else None,
        verdict=verdict,
        threshold_used=NAME_HIGH_CONFIDENCE,
    )


# ---------------------------------------------------------------------------
# UC7: Match Title endpoint (ML version)
# ---------------------------------------------------------------------------
class TitleCompareRequest(BaseModel):
    registered: str
    advertised: str

class TitleCompareResponse(BaseModel):
    registered: str
    advertised: str
    similarity_score: float
    is_match: bool
    verdict: str
    threshold_used: float

@app.post("/api/match-title", response_model=TitleCompareResponse)
def match_title(req: TitleCompareRequest):
    feats = _title_features(req.registered, req.advertised)
    score = float(title_clf.predict_proba(feats)[0][1])
    is_match = score >= TITLE_MATCH_THRESHOLD
    return TitleCompareResponse(
        registered=req.registered,
        advertised=req.advertised,
        similarity_score=round(score, 4),
        is_match=is_match,
        verdict="match" if is_match else "mismatch",
        threshold_used=TITLE_MATCH_THRESHOLD,
    )


class VectorQueryRequest(BaseModel):
    query: str
    top_k: Optional[int] = 3


@app.post("/api/vector-db/query")
def vector_db_query(req: VectorQueryRequest):
    top_k = max(1, min(req.top_k or 3, len(ALL_CLAUSES)))
    query_embedding = model.encode([req.query], convert_to_numpy=True)[0]

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
# Mock Registry API
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
    record = REGISTRY_DATA.get(name, DEFAULT_REGISTRY_RESPONSE)
    return {
        "entity_name": name,
        "country": country,
        "disclosure_text": record["disclosure_text"],
        "raw_status_hint": record["status"],
    }


class ClassifyRegistryRequest(BaseModel):
    third_party_name: str
    disclosure_text: str
    sbp_clause: str = ""


@app.post("/llm/classify-registry-status")
def classify_registry_status(req: ClassifyRegistryRequest):
    text_lower = req.disclosure_text.lower()

    if any(kw in text_lower for kw in ["suspended", "revoked", "enforcement", "sanctions", "restricted"]):
        status = "suspended"
        reasoning = (
            f"Disclosure text contains indicators of regulatory action. Classification: SUSPENDED."
        )
        confidence = 0.95
    elif any(kw in text_lower for kw in ["active", "current", "no sanctions", "passed", "compliant", "registered"]):
        status = "active"
        reasoning = (
            f"Disclosure text confirms active license. Classification: ACTIVE."
        )
        confidence = 0.92
    else:
        status = "unknown"
        reasoning = (
            "Disclosure text is ambiguous. Classification: UNKNOWN."
        )
        confidence = 0.60

    return {
        "regulatory_status": status,
        "reasoning": reasoning,
        "confidence": confidence,
        "third_party_name": req.third_party_name,
    }
