"""
Chunks the SBP AML/CFT/CPF Regulations PDF by Regulation/§ clause and loads
it into a persistent Chroma collection "sbp_regulations", tagged with
metadata {"regulation": "Reg <n>"} so the n8n RAG nodes can filter by
regulation number (see workflow.json "where": {"regulation": {"$in": [...]}}).

Run once (or whenever the PDF is updated):
    pip install -r requirements.txt
    python load_regulations.py
"""
import re
import joblib
import numpy as np
import chromadb
from pypdf import PdfReader
from sklearn.feature_extraction.text import TfidfVectorizer

PDF_PATH = "sbp_aml_regulations.pdf"
CHROMA_PATH = "./chroma_db"
VECTORIZER_PATH = "./tfidf_vectorizer.joblib"

# NOTE: Chroma's default embedding function downloads an ONNX model from the
# internet on first use. In network-restricted environments (or simply to
# keep this fully offline/deterministic for the FYP sandbox) we instead fit a
# TF-IDF vectorizer once over the regulation corpus and store embeddings
# directly. For production-grade semantic recall, swap this for
# `sentence-transformers` (all-MiniLM-L6-v2) or an OpenAI/Groq embeddings
# endpoint -- both rag_service.py and this script would need the same swap.

REG_HEADER_RE = re.compile(r"REGULATION\s*[–-]\s*(\d+)", re.IGNORECASE)


def extract_text(pdf_path: str) -> str:
    reader = PdfReader(pdf_path)
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(pages)


def chunk_by_regulation(full_text: str):
    """
    Splits the full regulation text into per-Regulation chunks, then further
    splits each Regulation into numbered-clause sub-chunks (1), 2), 3) ...)
    so retrieval returns tightly-scoped clause text rather than an entire
    Regulation.
    """
    matches = list(REG_HEADER_RE.finditer(full_text))
    chunks = []
    global_counter = 0
    for i, m in enumerate(matches):
        reg_num = m.group(1)
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(full_text)
        body = full_text[start:end].strip()

        # split on top-level numbered clauses "1)", "2)", ... at line starts
        clause_splits = re.split(r"\n(?=\d{1,2}\)\s)", body)
        for j, clause in enumerate(clause_splits):
            clause = clause.strip()
            if len(clause) < 40:
                continue
            global_counter += 1
            chunks.append({
                "id": f"reg{reg_num}_clause{j}_{global_counter}",
                "text": clause[:3000],  # cap chunk size
                "metadata": {"regulation": f"Reg {reg_num}"},
            })
    return chunks


def main():
    full_text = extract_text(PDF_PATH)
    chunks = chunk_by_regulation(full_text)
    print(f"Extracted {len(chunks)} regulation clause chunks from {PDF_PATH}")

    texts = [c["text"] for c in chunks]
    vectorizer = TfidfVectorizer(max_features=512, stop_words="english")
    embeddings = vectorizer.fit_transform(texts).toarray().astype(np.float32)
    # L2-normalize so cosine similarity <-> Chroma's default L2/IP distance behaves sensibly
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    embeddings = embeddings / norms
    joblib.dump(vectorizer, VECTORIZER_PATH)

    client = chromadb.PersistentClient(path=CHROMA_PATH)
    # fresh load each run
    try:
        client.delete_collection("sbp_regulations")
    except Exception:
        pass
    collection = client.create_collection(
        name="sbp_regulations",
        metadata={"description": "SBP AML/CFT/CPF Regulations, chunked by Regulation/clause",
                  "hnsw:space": "cosine"},
    )

    collection.add(
        ids=[c["id"] for c in chunks],
        documents=texts,
        metadatas=[c["metadata"] for c in chunks],
        embeddings=embeddings.tolist(),
    )
    print(f"Loaded {collection.count()} chunks into Chroma collection 'sbp_regulations' at {CHROMA_PATH}")
    print(f"TF-IDF vectorizer saved to {VECTORIZER_PATH}")


if __name__ == "__main__":
    main()
