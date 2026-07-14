# SBP AML/CFT/CPF — n8n Sandbox Implementation Blueprint
## Use Cases 4, 7, 10, 11, 14

**Prepared for:** FYP AML Transaction Monitoring PoC — SBP Sandbox Environment
**Stack:** n8n (orchestration) · PostgreSQL (system of record) · Chroma (optional vector store) · Groq/OpenAI/Claude/Gemini (single reasoning node only where genuinely needed)
**Design principle:** Deterministic-first, everywhere the Regulations give an explicit rule or figure. Where the Regulations use qualitative/judgment language ("reliable and independent," "reasonable measures," "properly analyze any similarity") and leave no fixed number, a small **trained/calibrated ML model** supplies a defensible threshold instead of a guessed one, and an **LLM node retrieves the exact regulation clause via RAG (Chroma)** before drafting any compliance document, so every AI output is grounded and citable. IF/Switch/Postgres/Set/Merge/Wait/HTTP Request nodes still carry every rule the Regulations state explicitly — models and LLMs never make the final compliance decision.

---

## Shared Synthetic Schema (used across all 5 use cases)

This PostgreSQL schema must be executed once in the sandbox database environment to prepare the synthetic tables:

```sql
-- Run once to set up the sandbox database

CREATE TABLE customers (
    customer_id         SERIAL PRIMARY KEY,
    full_name           TEXT NOT NULL,
    cnic                TEXT,
    customer_type       TEXT,          -- individual / sole_prop / partnership / llp / company / trust / ngo
    country             TEXT,
    is_pep              BOOLEAN DEFAULT FALSE,
    risk_rating         TEXT DEFAULT 'low',  -- low / medium / high
    created_at          TIMESTAMP DEFAULT now()
);

CREATE TABLE accounts (
    account_id          SERIAL PRIMARY KEY,
    customer_id         INT REFERENCES customers(customer_id),
    account_number      TEXT UNIQUE,
    account_type        TEXT,          -- personal / ngo / trust / corporate
    status              TEXT DEFAULT 'active',   -- active / dormant / frozen / closed
    frozen_reason       TEXT,
    opened_at           TIMESTAMP DEFAULT now()
);

CREATE TABLE transactions (
    transaction_id       SERIAL PRIMARY KEY,
    account_id           INT REFERENCES accounts(account_id),
    amount               NUMERIC(15,2),
    currency             TEXT DEFAULT 'PKR',
    txn_type             TEXT,         -- deposit / withdrawal / wire_in / wire_out
    counterparty_name    TEXT,
    counterparty_country TEXT,
    txn_date             TIMESTAMP DEFAULT now()
);

CREATE TABLE watchlists (
    watchlist_id          SERIAL PRIMARY KEY,
    list_type             TEXT,        -- DP / PP / UNSC / NACTA
    entity_name           TEXT,
    id_number             TEXT,
    country               TEXT,
    added_on              DATE DEFAULT CURRENT_DATE
);

CREATE TABLE pep_list (
    pep_id                SERIAL PRIMARY KEY,
    full_name             TEXT,
    id_number             TEXT,
    pep_category          TEXT,   -- foreign / domestic / international_org / family / close_associate
    country               TEXT
);

CREATE TABLE risk_profiles (
    risk_profile_id        SERIAL PRIMARY KEY,
    customer_id            INT REFERENCES customers(customer_id) UNIQUE,
    risk_rating            TEXT,
    rating_basis           TEXT,
    last_reviewed          TIMESTAMP DEFAULT now()
);

CREATE TABLE alerts (
    alert_id               SERIAL PRIMARY KEY,
    related_table          TEXT,    -- customers / transactions / accounts / ngo_accounts
    related_id             INT,
    alert_type             TEXT,   -- TFS_MATCH / DORMANCY / TITLE_MISMATCH / JURISDICTION / RECORD_HOLD / RELIANCE_BREACH / CDD_INCOMPLETE / RETENTION_REVIEW
    severity               TEXT,   -- none / low / medium / high
    status                 TEXT DEFAULT 'open',   -- open / under_review / closed / processed
    created_at             TIMESTAMP DEFAULT now()
);

CREATE TABLE audit_logs (
    log_id                  SERIAL PRIMARY KEY,
    workflow_name           TEXT,
    action                  TEXT,
    actor                   TEXT,   -- system / user id
    reference_table         TEXT,
    reference_id            INT,
    details                 JSONB,
    created_at              TIMESTAMP DEFAULT now()
);

CREATE TABLE users (
    user_id                  SERIAL PRIMARY KEY,
    full_name                TEXT,
    role                     TEXT,   -- compliance_officer / senior_management / analyst / legal
    email                    TEXT
);

CREATE TABLE employees (
    employee_id               SERIAL PRIMARY KEY,
    full_name                 TEXT,
    id_number                 TEXT,
    role                      TEXT,
    status                    TEXT DEFAULT 'active'
);

CREATE TABLE str_reports (
    str_id                    SERIAL PRIMARY KEY,
    customer_id               INT REFERENCES customers(customer_id),
    narrative                 TEXT,
    status                    TEXT DEFAULT 'draft', -- draft / filed
    filed_at                  TIMESTAMP
);

CREATE TABLE ctr_reports (
    ctr_id                     SERIAL PRIMARY KEY,
    transaction_id             INT REFERENCES transactions(transaction_id),
    status                     TEXT DEFAULT 'draft',
    filed_at                   TIMESTAMP
);

CREATE TABLE wire_transfers (
    wire_id                     SERIAL PRIMARY KEY,
    originator_name             TEXT,
    originator_account          TEXT,
    originator_id_number        TEXT,
    beneficiary_name            TEXT,
    beneficiary_id_number       TEXT,
    beneficiary_country         TEXT,
    amount                      NUMERIC(15,2),
    status                      TEXT DEFAULT 'pending', -- pending/execute/hold/reject
    created_at                  TIMESTAMP DEFAULT now()
);

CREATE TABLE ngo_accounts (
    ngo_account_id                SERIAL PRIMARY KEY,
    customer_id                   INT REFERENCES customers(customer_id),
    registered_title              TEXT,
    advertised_title              TEXT,
    governing_body                JSONB,
    title_match                   BOOLEAN,
    last_checked                  TIMESTAMP DEFAULT now()
);

CREATE TABLE documents (
    document_id                    SERIAL PRIMARY KEY,
    related_table                  TEXT,
    related_id                     INT,
    document_type                  TEXT,
    retention_start                DATE DEFAULT CURRENT_DATE,
    retention_years                INT DEFAULT 10,
    litigation_hold                BOOLEAN DEFAULT FALSE,
    file_reference                 TEXT
);

CREATE TABLE jurisdiction_list (
    jurisdiction_id                 SERIAL PRIMARY KEY,
    country_name                    TEXT UNIQUE,
    risk_tier                       TEXT,  -- high_risk / monitored / normal
    counter_measure                 TEXT,
    updated_at                      TIMESTAMP DEFAULT now()
);

CREATE TABLE reliance_arrangements (
    reliance_id                      SERIAL PRIMARY KEY,
    third_party_name                 TEXT,
    regulator                        TEXT,
    is_same_corporate_group          BOOLEAN DEFAULT FALSE,
    regulatory_status                TEXT,  -- active / suspended / unknown
    country                          TEXT,
    last_verified                    TIMESTAMP DEFAULT now()
);
```

---

## RAG & ML Augmentation Layer (applies across UC4, UC7, UC10, UC11, UC14)

### Why this layer exists
The SBP Regulations are explicit and numeric in some places (e.g., **10-year retention** — Reg 8 §2–3; **48-hour SBP freeze reporting** — Reg 4 §7(c); **1-month/7-day/1-day dormancy notices** — Reg 2 §24(a)) and those rules stay 100% deterministic, hard-coded in IF/Switch nodes exactly as written.

But elsewhere the Regulations deliberately use **qualitative, judgment-based language** and leave the numeric calibration to the RE itself:
- Reg 2 §5: verification must use *"reliable and independent document, data or source"* — no fixed match-confidence number is given.
- Reg 2 §18(b): SDD monitoring may rely on *"a reasonable monetary threshold as prescribed by SBP"* — the number itself is bank-defined/SBP-issued separately, not fixed in this text.
- Reg 6 §4: a title mismatch must be judged for *similarity*, not exact-string equality alone (abbreviations, translated/short-form NGO names).
- Reg 4 §6: identifying persons "acting on behalf of" a DP/PP relies on *"reliable media information, or regulated entity's own analysis"* — inherently a fuzzy, evidence-weighing task.
- Reg 1 §2–5 (IRAR): risk is derived from *"assess[ing] and understand[ing]"* multiple risk factors together — a scoring/classification problem, not a single fixed rule.

**This is exactly the boundary where RAG and ML belong** — never to replace an explicit numeric rule, but to (a) responsibly calibrate a threshold the regulation leaves open, and (b) ground every AI-generated compliance document in the *actual clause text*, so nothing is hallucinated.

### RAG Component (Retrieval-Augmented Grounding)
- **Vector store:** Chroma, populated once by chunking the SBP AML/CFT/CPF Regulations PDF by Regulation/§ clause (Part C, Annexures I & II).
- **n8n implementation:** a **"Query Vector Store" node** (HTTP Request to a local Chroma REST endpoint, or the n8n Vector Store node if available) runs **before** any LLM narrative node in UC4, UC7, UC10, and UC14.
- **Pattern used everywhere an LLM drafts a compliance document:**
  ```
  Structured case data (from Postgres)
        ↓
  Query Vector Store — retrieve top-k relevant Regulation clauses (e.g. "Reg 6 §4 donation title mismatch")
        ↓
  AI Agent (LLM) — draft narrative, instructed to cite ONLY the retrieved clause text, not invent regulation references
        ↓
  Output narrative with an explicit clause citation (e.g. "per Regulation 6 §4 of the SBP AML/CFT/CPF Regulations")
  ```
- This turns every LLM-drafted Form-B notice, STR narrative, reconstruction-bundle note, and IRAR narrative into a **citable, audit-defensible document** rather than free-form text — critical for SBP inspection readiness.
- RAG is retrieval only; it never makes the pass/fail compliance decision itself.

### ML Component (trained/calibrated, not hand-picked numbers)
Two lightweight models are trained on the synthetic dataset (same spirit as the FYP's XGBoost severity classifier), replacing any arbitrary constant used in the previous version of this blueprint:

**1. Name/Entity Matching Model (UC4 — TFS screening, and reused for UC7 governing-body screening)**
- Features: normalized edit distance, phonetic code match (Soundex/Metaphone), token-set overlap, DOB/ID-number exact-match flag.
- Model: a simple calibrated **logistic regression or gradient-boosted classifier (scikit-learn / XGBoost)** trained on a labeled synthetic set of true-positive and false-positive name pairs (transliteration variants of Pakistani names, common in Reg 4 §6/§8 "similarity" language).
- Output: a **calibrated match probability**, and the high-confidence / grey-zone / clear cutoffs are derived from the model's precision-recall curve on a held-out validation set (documented in the model card) — **not an arbitrary number**. This satisfies Reg 4 §8's instruction to *"properly analyze any similarity"* with a defensible, reproducible method instead of a guessed threshold.
- Served via a small **FastAPI/Flask microservice** called from n8n's `HTTP Request` node (mirrors the FYP's own FastMCP-served Isolation Forest pattern) — no Kafka/K8s needed, just one local model endpoint.

**2. Title/Entity Semantic Similarity Model (UC7 — NGO donation-title integrity)**
- Sentence-embedding cosine similarity (e.g., a small local `sentence-transformers` model) instead of pure string equality, so abbreviations and short-form titles ("Roshan Trust" vs "Roshan Family Welfare Trust") aren't false-flagged, while genuinely different entities ("Roshan Foundation for Children") still are.
- Decision boundary again calibrated from labeled synthetic title pairs, not a guessed number — directly operationalizes Reg 6 §4's mismatch test.

### What stays 100% deterministic, never ML/RAG
Retention years (Reg 8), the 48-hour SLA (Reg 4 §7(c)), dormancy notice cadence (Reg 2 §24(a)), freeze-without-delay trigger once a match is *confirmed* (Reg 4 §7(a)), mandatory senior-management/compliance approval gates (Reg 2 §16(f), Reg 5 §1(b), Reg 6 §1, Reg 9 §1(c)), and the jurisdiction risk tier itself (sourced from the SBP/FATF list, Reg 14 §1) — none of these are ever inferred, scored, or generated by a model.

---
---

# 1. USE CASE 4 — Targeted Financial Sanctions (TFS) Real-Time Screening & Freeze Orchestration

### 1. Workflow Name
`TFS_RealTime_Screening_And_Freeze_Orchestration`

### 2. Business Objective
Screen all newly onboarded customers, transaction counterparties, and existing relationships against SBP-designated Designated Persons (DPs) and Proscribed Persons (PPs) in real time. Automatically freeze matching accounts immediately without delay or prior notice, initiate statutory filings to the Financial Monitoring Unit (FMU) and State Bank of Pakistan (SBP), and issue Form-B notices to comply with national and international sanctions frameworks.

### 3. Applicable SBP Regulations
- **Regulation 4 — Targeted Financial Sanctions under UNSC Act, 1948 and ATA, 1997 (§1–14)**:
  - **§3**: SBP-REs must implement real-time screening solutions for customers and occasional customers.
  - **§7(a)**: SBP-REs must freeze immediately, without delay and without prior notice, the funds or assets owned or controlled by DPs/PPs.
  - **§7(b)**: Report frozen assets or rejected transactions/onboardings to the FMU as suspicious.
  - **§7(c)**: Report the freezing of funds to SBP within 48 hours.
  - **§7(d)**: Issue notice of freezing to the account holder as per Form-B of the UNSC (Freezing and Seizure) Order, 2019.
  - **§8**: SBP-REs must properly analyze any similarity (ID number, DOB, address) between customer details and watchlist entries.
  - **§10**: Unfreezing is prohibited without verification that the target is a false positive, ensuring no other holds are active, and reporting to SBP.

### 4. Business Scenario
Ahmed Raza Sheikh, CNIC 42101-1234567-1, attempts to open a personal account at a bank branch. The core banking frontend submits a webhook payload to the n8n workflow. The system performs a real-time fuzzy match against the `watchlists` table, which contains a NACTA Proscribed Person entry for "Ahmed Raza Shaikh" with CNIC 42101-1234567-1. The local name-matching ML service calculates a match probability of 92% (High Confidence). The workflow immediately issues an SQL command to mark the account status as `frozen` with reason `TFS Match`, files an e-report to the mock FMU endpoint, triggers a 48-hour SLA tracker for SBP, calls the ChromaDB vector store to retrieve SBP Regulation 4 §7(d), uses a Groq LLM node to draft a grounded Form-B notice, emails the notice to Ahmed, and writes a detailed audit log entry.

### 5. Workflow Overview
```
New Customer Event (Webhook)
       ↓
Fetch Customer & Watchlist Candidates (Postgres)
       ↓
Score Similarity via local ML service (HTTP Request)
       ↓
[IF Match ≥ 90% (High)] ──────────────────▶ [Auto-Freeze Account (Postgres)]
       │                                                 ↓
[IF 60% ≤ Match < 90%] ──▶ [Manual Analyst Review] ──▶ [FMU e-Filing (HTTP)]
       │                         │(If Confirmed)         ↓
[IF Match < 60%]                 ▼                [Start SBP 48hr SLA Timer]
       ↓                  [Log Clear & Exit]             ↓
[Log Clear & Exit]                                [RAG Retrieval of Reg 4 (Chroma)]
                                                         ↓
                                                  [AI Draft Form-B Notice (LLM)]
                                                         ↓
                                                  [Email Notice & Insert Audit Log]
```

### 6. Complete n8n Node Flow

#### Node 1: TFS Trigger
- **Node Type:** Webhook
- **Purpose:** Receives new customer onboarding events in real-time.
- **Configuration:** HTTP Method: `POST`, Path: `tfs-screening-trigger`.
- **Input:** JSON payload containing `customer_id`.
- **Output:** `{"customer_id": 1}`
- **Next Node:** Node 2

#### Node 2: Fetch Customer Details
- **Node Type:** Postgres
- **Purpose:** Fetches the name, CNIC, and country of the onboarded customer.
- **Configuration:** Operation: `Execute Query`, Query: `SELECT customer_id, full_name, cnic, country FROM customers WHERE customer_id = {{ $json.customer_id }};`
- **Input:** `{"customer_id": 1}`
- **Output:** `{"customer_id": 1, "full_name": "Ahmed Raza Sheikh", "cnic": "42101-1234567-1", "country": "Pakistan"}`
- **Next Node:** Node 3

#### Node 3: Fetch Watchlist Candidates
- **Node Type:** Postgres
- **Purpose:** Pulls potential matches from the sanctions watchlist for scoring.
- **Configuration:** Operation: `Execute Query`, Query: `SELECT watchlist_id, entity_name, id_number, list_type, country FROM watchlists;`
- **Input:** Output of Node 2.
- **Output:** Array of watchlist entries.
- **Next Node:** Node 4

#### Node 4: Match Candidates via ML Service
- **Node Type:** HTTP Request
- **Purpose:** Calls the local name-matching ML service to compute normalized similarity scores.
- **Configuration:** Method: `POST`, URL: `http://localhost:5000/api/match-name`, Body: `{"customer": {"name": "{{ $node["Fetch Customer Details"].json.full_name }}", "cnic": "{{ $node["Fetch Customer Details"].json.cnic }}"}, "candidates": {{ $json }}`.
- **Input:** Watchlist array and customer metadata.
- **Output:** `{"match_probability": 0.92, "matched_candidate": "Ahmed Raza Shaikh", "watchlist_id": 45}`
- **Next Node:** Node 5

#### Node 5: IF High Confidence Match
- **Node Type:** IF
- **Purpose:** Routes high-confidence matches directly to auto-freeze.
- **Configuration:** Condition: `{{ $json.match_probability >= 0.90 }}`.
- **Input:** Similarity score response.
- **Output:** `true` (leads to Node 8) or `false` (leads to Node 6).
- **Next Node:** Node 8 (on `true`), Node 6 (on `false`)

#### Node 6: IF Grey Zone Match
- **Node Type:** IF
- **Purpose:** Routes intermediate matches to human analyst review.
- **Configuration:** Condition: `{{ $json.match_probability >= 0.60 }}`.
- **Input:** Similarity score response.
- **Output:** `true` (leads to Node 7) or `false` (leads to Node 21).
- **Next Node:** Node 7 (on `true`), Node 21 (on `false`)

#### Node 7: Manual Analyst Approval
- **Node Type:** Manual Approval
- **Purpose:** Pauses the workflow for compliance analyst validation (Regulation 4 §8 similarity check).
- **Configuration:** Request Message: `TFS Screening Alert: Review potential match for Ahmed Raza Sheikh`.
- **Input:** Match details and candidate metadata.
- **Output:** `{"approved": true}` or `{"approved": false}`
- **Next Node:** Node 8 (on `true`), Node 21 (on `false`)

#### Node 8: Insert TFS Alert
- **Node Type:** Postgres
- **Purpose:** Logs the confirmed TFS match alert.
- **Configuration:** Operation: `Execute Query`, Query: `INSERT INTO alerts (related_table, related_id, alert_type, severity, status) VALUES ('customers', {{ $node["Fetch Customer Details"].json.customer_id }}, 'TFS_MATCH', 'high', 'open') RETURNING alert_id;`
- **Input:** Case context.
- **Output:** `{"alert_id": 301}`
- **Next Node:** Node 9

#### Node 9: Freeze Account
- **Node Type:** Postgres
- **Purpose:** Freezes all accounts for the customer immediately without prior notice (Regulation 4 §7(a)).
- **Configuration:** Operation: `Execute Query`, Query: `UPDATE accounts SET status = 'frozen', frozen_reason = 'TFS MATCH - Reg 4' WHERE customer_id = {{ $node["Fetch Customer Details"].json.customer_id }} RETURNING account_id;`
- **Input:** Customer context.
- **Output:** `{"account_id": 12}`
- **Next Node:** Node 10

#### Node 10: Set FMU Report Payload
- **Node Type:** Set
- **Purpose:** Formats data according to the FMU Suspicious Transaction Report (STR) payload specification.
- **Configuration:** Mappings: `alert_id`, `customer_name`, `cnic`, `watchlist_ref`.
- **Input:** Merged node outputs.
- **Output:** Structured FMU JSON payload.
- **Next Node:** Node 11

#### Node 11: File FMU Report
- **Node Type:** HTTP Request
- **Purpose:** Submits the freezing report to the FMU mock API (Regulation 4 §7(b)).
- **Configuration:** Method: `POST`, URL: `http://localhost:5678/mock-fmu/str`, Body: `{{ $json }}`.
- **Input:** FMU JSON payload.
- **Output:** `{"filing_id": "FMU-TFS-2026-991", "status": "submitted"}`
- **Next Node:** Node 12

#### Node 12: Start 48hr SLA Timer
- **Node Type:** Set
- **Purpose:** Deterministically sets the SBP filing deadline to exactly 48 hours from the freeze timestamp (Regulation 4 §7(c)).
- **Configuration:** Mappings: `sla_due_at` calculated as `{{ new Date(new Date().getTime() + 48*60*60*1000).toISOString() }}`.
- **Input:** Filing metadata.
- **Output:** `{"sla_due_at": "2026-07-16T19:03:54Z"}`
- **Next Node:** Node 13

#### Node 13: Wait (SLA Window Simulation)
- **Node Type:** Wait
- **Purpose:** Simulates/orchestrates the delay before final mock SBP dispatch or escalates if delayed.
- **Configuration:** Duration: 1 second (sandbox simulation mode).
- **Input:** SLA timestamp.
- **Output:** Standard output.
- **Next Node:** Node 14

#### Node 14: File SBP Freeze Report
- **Node Type:** HTTP Request
- **Purpose:** Transmits the mandatory freezing notice report to the State Bank of Pakistan mock gateway.
- **Configuration:** Method: `POST`, URL: `http://localhost:5678/mock-sbp/freeze-report`, Body: `{"customer_id": {{ $node["Fetch Customer Details"].json.customer_id }}, "account_id": {{ $node["Freeze Account"].json.account_id }}, "sla_compliance": true}`.
- **Input:** Account metadata.
- **Output:** `{"sbp_receipt_number": "SBP-FR-8891", "status": "acknowledged"}`
- **Next Node:** Node 15

#### Node 15: Retrieve Regulation 4 Clauses (RAG)
- **Node Type:** HTTP Request
- **Purpose:** Retrieves the exact clause wording for Regulation 4 §7(d) and UNSC Order 2019 to ground the Form-B notice.
- **Configuration:** Method: `POST`, URL: `http://localhost:8000/api/vector-db/query`, Body: `{"query": "Form-B freezing notice UNSC Act 1948", "top_k": 1}`.
- **Input:** Vector query query parameter.
- **Output:** `{"clause": "Regulation 4 §7(d): Issue notice of freezing of funds... as per Form-B of the UNSC (Freezing and Seizure) Order, 2019."}`
- **Next Node:** Node 16

#### Node 16: Draft Form-B Notice (LLM Node)
- **Node Type:** OpenAI Chat
- **Purpose:** Generates a RAG-grounded legal freezing notification letter.
- **Configuration:** Model: `grok-beta`, Temperature: 0.1, Prompt: See Section 10.
- **Input:** Customer details + retrieved regulation clause.
- **Output:** `{"notice_draft": "Subject: Form-B Freezing Notice... as required by Regulation 4 §7(d)..."}`
- **Next Node:** Node 17

#### Node 17: Store Form-B Document
- **Node Type:** Postgres
- **Purpose:** Persists the generated notice to the documents table (Regulation 8 retention).
- **Configuration:** Operation: `Execute Query`, Query: `INSERT INTO documents (related_table, related_id, document_type, litigation_hold, file_reference) VALUES ('accounts', {{ $node["Freeze Account"].json.account_id }}, 'FORM_B_NOTICE', FALSE, 'REF-FORM-B-' || {{ $node["Fetch Customer Details"].json.customer_id }}) RETURNING document_id;`
- **Input:** Notice draft text.
- **Output:** `{"document_id": 412}`
- **Next Node:** Node 18

#### Node 18: Send Notice Email
- **Node Type:** Email
- **Purpose:** Emails the signed Form-B notice directly to the customer's email.
- **Configuration:** To: `ahmed.sheikh@synthetic-email.com`, Subject: `Notice of Freeze under UNSC (Freezing and Seizure) Order, 2019`, Body: `{{ $node["Draft Form-B Notice"].json.notice_draft }}`.
- **Input:** Draft text.
- **Output:** `{"message": "sent"}`
- **Next Node:** Node 19

#### Node 19: Write Audit Log
- **Node Type:** Postgres
- **Purpose:** Appends a structural log trace for audit checks.
- **Configuration:** Operation: `Execute Query`, Query: `INSERT INTO audit_logs (workflow_name, action, actor, reference_table, reference_id, details) VALUES ('TFS_Screening_Freeze', 'ACCOUNT_FROZEN_TFS', 'system', 'accounts', {{ $node["Freeze Account"].json.account_id }}, '{"match_score": 0.92, "fmu_id": "FMU-TFS-2026-991"}'::jsonb) RETURNING log_id;`
- **Input:** Workflow state.
- **Output:** `{"log_id": 5001}`
- **Next Node:** Node 20

#### Node 20: Update Alert Status
- **Node Type:** Postgres
- **Purpose:** Updates the alert status to processed.
- **Configuration:** Operation: `Execute Query`, Query: `UPDATE alerts SET status = 'processed' WHERE alert_id = {{ $node["Insert TFS Alert"].json.alert_id }};`
- **Input:** Log metadata.
- **Output:** Standard success.
- **Next Node:** None (Workflow completes successfully)

#### Node 21: Log Clear Trace
- **Node Type:** Postgres
- **Purpose:** Logs a non-match check execution.
- **Configuration:** Operation: `Execute Query`, Query: `INSERT INTO audit_logs (workflow_name, action, actor, reference_table, reference_id, details) VALUES ('TFS_Screening_Freeze', 'SCREENING_CLEARED', 'system', 'customers', {{ $node["Fetch Customer Details"].json.customer_id }}, '{"score": {{ $node["Match Candidates via ML Service"].json.match_probability }} }'::jsonb);`
- **Input:** Screening mismatch metadata.
- **Output:** Log trace.
- **Next Node:** None (Workflow completes with no-match outcome)

### 7. Node Connection Diagram
```
[Node 1: Webhook Trigger]
          ↓
[Node 2: Fetch Customer Details]
          ↓
[Node 3: Fetch Watchlist Candidates]
          ↓
[Node 4: Match Candidates via ML]
          ↓
[Node 5: IF Match ≥ 90%?] ────(No)───▶ [Node 6: IF Match ≥ 60%?] ───(No)──▶ [Node 21: Log Clear]
          │(Yes)                                │(Yes)                                 ↓
          │                                     ▼                                    [End]
          │                        [Node 7: Manual Analyst Approval]
          │                                     │(Approved)
          ├─────────────────────────────────────┘
          ▼
[Node 8: Insert TFS Alert]
          ↓
[Node 9: Freeze Account]
          ↓
[Node 10: Set FMU Payload]
          ↓
[Node 11: File FMU Report]
          ↓
[Node 12: Set 48hr SLA Timer]
          ↓
[Node 13: Wait SLA Simulation]
          ↓
[Node 14: File SBP Report]
          ↓
[Node 15: Retrieve Reg 4 Clauses (RAG)]
          ↓
[Node 16: Draft Form-B Notice (LLM)]
          ↓
[Node 17: Store Form-B Document]
          ↓
[Node 18: Send Notice Email]
          ↓
[Node 19: Write Audit Log]
          ↓
[Node 20: Update Alert Status]
          ↓
        [End]
```

### 8. PostgreSQL Queries
```sql
-- Node 2: Fetch customer details
SELECT customer_id, full_name, cnic, country FROM customers WHERE customer_id = 1;

-- Node 3: Fetch watchlist candidates
SELECT watchlist_id, entity_name, id_number, list_type, country FROM watchlists;

-- Node 8: Insert TFS alert
INSERT INTO alerts (related_table, related_id, alert_type, severity, status) 
VALUES ('customers', 1, 'TFS_MATCH', 'high', 'open') 
RETURNING alert_id;

-- Node 9: Freeze account
UPDATE accounts 
SET status = 'frozen', frozen_reason = 'TFS MATCH - Reg 4' 
WHERE customer_id = 1 
RETURNING account_id;

-- Node 17: Insert Form-B notice document record
INSERT INTO documents (related_table, related_id, document_type, litigation_hold, file_reference) 
VALUES ('accounts', 12, 'FORM_B_NOTICE', FALSE, 'REF-FORM-B-1') 
RETURNING document_id;

-- Node 19: Write audit log
INSERT INTO audit_logs (workflow_name, action, actor, reference_table, reference_id, details) 
VALUES ('TFS_Screening_Freeze', 'ACCOUNT_FROZEN_TFS', 'system', 'accounts', 12, '{"match_score": 0.92, "fmu_id": "FMU-TFS-2026-991"}'::jsonb) 
RETURNING log_id;

-- Node 20: Update alert status
UPDATE alerts SET status = 'processed' WHERE alert_id = 301;

-- Node 21: Log clear trace
INSERT INTO audit_logs (workflow_name, action, actor, reference_table, reference_id, details) 
VALUES ('TFS_Screening_Freeze', 'SCREENING_CLEARED', 'system', 'customers', 1, '{"score": 0.12}'::jsonb);
```

### 9. Rule Engine
```
IF match_probability >= 0.90 THEN
    Action: Set alert = TFS_MATCH, Severity = HIGH, Route = AUTO_FREEZE
ELSE IF match_probability >= 0.60 AND match_probability < 0.90 THEN
    Action: Route = ANALYST_MANUAL_QUEUE
ELSE
    Action: Route = AUTO_CLEAR (Log Clearance Trace)
END IF;

IF analyst_approval = APPROVED THEN
    Action: Route = FREEZE_AND_REPORT
ELSE
    Action: Route = AUTO_CLEAR
END IF;
```

### 10. AI Reasoning
- **Reason AI is Needed:** The freezing notice must be custom-drafted, incorporating distinct customer parameters and citing retrieved regulatory provisions (Regulation 4 §7(d) and UNSC Seizure Orders). Standard templates fail to handle variations in regulatory instructions that occur between UN list classifications.
- **Expected Input:**
  - Customer Name: Ahmed Raza Sheikh
  - Account Number: ACC-9911
  - Watchlist ID Match: NACTA-PP-882
  - Chroma Retrieval: "Regulation 4 §7(d): Issue notice of freezing of funds... as per Form-B of the UNSC (Freezing and Seizure) Order, 2019."
- **Prompt:**
  ```
  You are an expert AML Compliance Counsel. Draft a formal Form-B Seizure Notice as mandated by the State Bank of Pakistan.
  Use the following context to fill out the form:
  - Account Holder: Ahmed Raza Sheikh
  - Account Number: ACC-9911
  - Legal Basis: UNSC Act, 1948 and Anti-Terrorism Act, 1997
  - Governing Clause: {Chroma retrieval: "Regulation 4 §7(d) of the SBP AML/CFT/CPF Regulations"}
  
  Do not include placeholders. State clearly that the account has been frozen without prior notice. Include the citation to the specific clause.
  ```
- **Expected Output:**
  `"Dear Ahmed Raza Sheikh, You are hereby notified that your account number ACC-9911 has been frozen without prior notice in compliance with the United Nations Security Council Act, 1948, and the Anti-Terrorism Act, 1997. This action is taken under Regulation 4 §7(d) of the SBP AML/CFT/CPF Regulations..."`

### 11. Human Approval
- **AML Analyst:** Reviews and verifies matches in the grey-zone band (60% to 89% match probability) within 4 hours.
- **Compliance Head:** Approves any unfreezing requests (Regulation 4 §10) after checking for other holds.

### 12. Database Updates
- `accounts`: Updates `status = 'frozen'` and `frozen_reason = 'TFS MATCH - Reg 4'`.
- `alerts`: Inserts `TFS_MATCH` record.
- `documents`: Inserts drafted `FORM_B_NOTICE` with a 10-year retention flag.
- `audit_logs`: Inserts trace details.

### 13. Notifications
- **Email:** Dispatched to customer containing the Form-B notice.
- **Slack:** Message to `#aml-alerts` channel: `ALERT [TFS MATCH]: High-confidence match detected for customer 1. Account 12 frozen.`
- **Dashboard:** Triggers an event to refresh the SBP TFS Dashboard view.
- **Audit Logs:** Logs system and analyst actions.

### 14. Error Handling
- **Filing API Timeout:** If Node 11 (FMU) or Node 14 (SBP) fails (30s timeout), n8n retries 3 times with exponential backoff. If it still fails, it inserts a high-severity alert into the analyst dashboard for manual filing within the 48-hour window.
- **Database Write Failure:** Wrap Node 8 and Node 9 in a PostgreSQL transaction. If updating the account status fails, abort the FMU filing to avoid reporting a freeze that was not executed.

### 15. Synthetic Test Data
```sql
-- Setup Customer and Account
INSERT INTO customers (customer_id, full_name, cnic, customer_type, country, is_pep, risk_rating) 
VALUES (1, 'Ahmed Raza Sheikh', '42101-1234567-1', 'individual', 'Pakistan', FALSE, 'low');

INSERT INTO accounts (account_id, customer_id, account_number, account_type, status) 
VALUES (12, 1, 'ACC-9911', 'personal', 'active');

-- Setup Watchlist Target
INSERT INTO watchlists (watchlist_id, list_type, entity_name, id_number, country) 
VALUES (45, 'PP', 'Ahmed Raza Shaikh', '42101-1234567-1', 'Pakistan');
```

---
---

# 2. USE CASE 7 — NGO/NPO/Charity/Trust Account & Donation-Title Integrity Monitor

### 1. Workflow Name
`NGO_Account_Donation_Integrity_Monitor`

### 2. Business Objective
Ensure that NGO/NPO accounts are opened and operated strictly under their registered titles as stated in their constituent documents. Automate the cross-verification of advertised donation solicitation titles against registered titles using semantic similarity, and enforce continuous screening of all governing body members against TFS watchlists and PEP lists.

### 3. Applicable SBP Regulations
- **Regulation 6 — NGO/NPO/Charity/Trust Accounts (§1–7)**:
  - **§1**: Mandatory application of Enhanced Due Diligence (EDD) and Senior Management approval when onboarding or updating NGO/NPO relationships.
  - **§2**: Accounts must be opened strictly in the name of the NPO/NGO as per the title in its constituent registration documents.
  - **§3**: Governing body members, trustees, and authorized signatories must be screened separately against watchlists and PEP lists under all name variants.
  - **§4**: If an NGO publishes a donation solicitation mentioning an account number, the advertised title must match the registered account title. Any mismatch requires placing an immediate caution (block) on the account and evaluating the case for an STR filing.
  - **§5**: Prohibits the use of personal bank accounts for charity purposes or donation collection.

### 4. Business Scenario
A local charity named "Roshan Family Welfare Trust" runs a donation drive. An advertisement in a regional newspaper solicits donations for "Roshan Foundation for Children" listing account number `ACC-NPO-321`. A weekly automated n8n scraper or ingestion tool submits the solicitation text and account number to the workflow. The workflow fetches the registered title for `ACC-NPO-321` ("Roshan Family Welfare Trust") and calls the semantic similarity ML service. The similarity score is returned as 48% (Mismatch). The workflow immediately flags the account, applies a debit restriction (caution), pulls Regulation 6 §4 from ChromaDB, uses Groq to generate a draft FMU STR narrative, and routes the draft to the Compliance Officer for manual filing authorization. Simultaneously, the governing body members are screened, and a PEP hit on member "Salman Qureshi" triggers a separate compliance review.

### 5. Workflow Overview
```
Cron (Weekly Scan) OR Webhook (Ad Ingestion)
                     ↓
          Fetch NGO Registered Details
                     ↓
          Fetch Governing Body Member List
                     ↓
         [Loop: Screen Members via ML]
                     │
          (If DP/PP Match Found) ──▶ [Insert Alert: DPPP_MEMBER]
                     ↓
     Compare Solicitation Title vs Registered Title
                     ↓
         Score Title Similarity via ML
                     ↓
        [IF Similarity < Calibrated Threshold (Mismatch)]
                     │
                     ├─▶ [Apply Caution Hold on Account]
                     ├─▶ [Insert Alert: TITLE_MISMATCH]
                     ├─▶ [Retrieve Reg 6 §4 via RAG (Chroma)]
                     ├─▶ [AI Draft STR Narrative (LLM)]
                     ├─▶ [Pause for Compliance Officer Approval]
                     │         │(If Approved)
                     │         ▼
                     ├─▶ [File STR to Mock FMU API]
                     └─▶ [Write Detailed Audit Log]
```

### 6. Complete n8n Node Flow

#### Node 1: Weekly Scan Trigger
- **Node Type:** Schedule Trigger
- **Purpose:** Triggers weekly review of active NGO accounts and new advertisement logs.
- **Configuration:** Cron Expression: `0 8 * * 1` (Every Monday at 8:00 AM).
- **Input:** Chron event.
- **Output:** Current timestamp context.
- **Next Node:** Node 2

#### Node 2: Fetch Active NGO Accounts
- **Node Type:** Postgres
- **Purpose:** Retrieves registered details of trusts and NGOs.
- **Configuration:** Operation: `Execute Query`, Query: `SELECT ngo_account_id, customer_id, registered_title FROM ngo_accounts;`
- **Input:** Trigger context.
- **Output:** Array of active NGO accounts.
- **Next Node:** Node 3

#### Node 3: Fetch Governing Body Members
- **Node Type:** Postgres
- **Purpose:** Extracts the JSON list of governing body members for each NGO.
- **Configuration:** Operation: `Execute Query`, Query: `SELECT customer_id, governing_body FROM ngo_accounts WHERE ngo_account_id = {{ $json.ngo_account_id }};`
- **Input:** Single NGO record.
- **Output:** `{"customer_id": 101, "governing_body": [{"name": "Salman Qureshi", "cnic": "35202-1111111-1"}]}`
- **Next Node:** Node 4

#### Node 4: Loop Governing Body Members
- **Node Type:** Loop Over Items
- **Purpose:** Iterates through each governing body member for screening.
- **Configuration:** Loop over: `governing_body`.
- **Input:** JSON array of members.
- **Output:** Single member payload.
- **Next Node:** Node 5

#### Node 5: Screen Member (ML Name-Matching)
- **Node Type:** HTTP Request
- **Purpose:** Compares the member name against watchlists and PEP lists (Regulation 6 §3).
- **Configuration:** Method: `POST`, URL: `http://localhost:5000/api/match-name`, Body: `{"name": "{{ $json.name }}", "cnic": "{{ $json.cnic }}"}`.
- **Input:** Member details.
- **Output:** `{"match_probability": 0.88, "matched_watchlist": "Salman Qureshi (PEP-Domestic)"}`
- **Next Node:** Node 6

#### Node 6: IF Member PEP/DP Match
- **Node Type:** IF
- **Purpose:** Checks if the screening result exceeds the calibrated 60% probability.
- **Configuration:** Condition: `{{ $json.match_probability >= 0.60 }}`.
- **Input:** Screening result.
- **Output:** `true` (leads to Node 7) or `false` (leads to loop continuation).
- **Next Node:** Node 7 (on `true`), Loop context (on `false`)

#### Node 7: Insert Member Alert
- **Node Type:** Postgres
- **Purpose:** Logs the PEP/DP match alert for compliance tracking (Regulation 6 §6).
- **Configuration:** Operation: `Execute Query`, Query: `INSERT INTO alerts (related_table, related_id, alert_type, severity, status) VALUES ('ngo_accounts', {{ $node["Fetch Active NGO Accounts"].json.ngo_account_id }}, 'DPPP_MEMBER', 'high', 'open');`
- **Input:** Match details.
- **Output:** Alert context.
- **Next Node:** Node 8 (Merge back after loop completes)

#### Node 8: Merge Loop Results
- **Node Type:** Merge
- **Purpose:** Recombines loop outputs to resume checking the solicitation title.
- **Configuration:** Mode: `Wait for all inputs`.
- **Input:** Loop and alert states.
- **Output:** Combined NGO metadata.
- **Next Node:** Node 9

#### Node 9: Fetch Solicitation Logs
- **Node Type:** Postgres
- **Purpose:** Retrieves newly scraped advertisements claiming to solicit for the NGO account.
- **Configuration:** Operation: `Execute Query`, Query: `SELECT advertised_title, account_number FROM transactions t JOIN accounts a ON t.account_id = a.account_id WHERE a.account_type = 'ngo' LIMIT 1;` -- Simulates capturing a solicitation text matching the account.
- **Input:** Merged context.
- **Output:** `{"advertised_title": "Roshan Foundation for Children", "account_number": "ACC-NPO-321"}`
- **Next Node:** Node 10

#### Node 10: Fetch Registered Account Title
- **Node Type:** Postgres
- **Purpose:** Pulls the official registered name for comparison.
- **Configuration:** Operation: `Execute Query`, Query: `SELECT registered_title FROM ngo_accounts n JOIN accounts a ON n.customer_id = a.customer_id WHERE a.account_number = '{{ $json.account_number }}';`
- **Input:** Solicitation log payload.
- **Output:** `{"registered_title": "Roshan Family Welfare Trust"}`
- **Next Node:** Node 11

#### Node 11: Call Title Similarity Model
- **Node Type:** HTTP Request
- **Purpose:** Performs semantic embedding comparison between registered and advertised titles (Regulation 6 §4).
- **Configuration:** Method: `POST`, URL: `http://localhost:5000/api/match-title`, Body: `{"registered_title": "{{ $json.registered_title }}", "advertised_title": "{{ $node["Fetch Solicitation Logs"].json.advertised_title }}"}`.
- **Input:** Both titles.
- **Output:** `{"similarity_score": 0.48, "verdict": "mismatch"}`
- **Next Node:** Node 12

#### Node 12: IF Title Mismatch
- **Node Type:** IF
- **Purpose:** Diverges flow on verification failure.
- **Configuration:** Condition: `{{ $json.verdict === 'mismatch' }}`.
- **Input:** Model output.
- **Output:** `true` (leads to Node 13) or `false` (leads to Node 20).
- **Next Node:** Node 13 (on `true`), Node 20 (on `false`)

#### Node 13: Place Caution Hold on Account
- **Node Type:** Postgres
- **Purpose:** Restricts debit activities immediately upon title mismatch (Regulation 6 §4).
- **Configuration:** Operation: `Execute Query`, Query: `UPDATE accounts SET status = 'dormant', frozen_reason = 'Caution Hold - Title Mismatch' WHERE account_number = '{{ $node["Fetch Solicitation Logs"].json.account_number }}';`
- **Input:** Mismatch context.
- **Output:** Execution status.
- **Next Node:** Node 14

#### Node 14: Insert Mismatch Alert
- **Node Type:** Postgres
- **Purpose:** Logs the title integrity mismatch alert.
- **Configuration:** Operation: `Execute Query`, Query: `INSERT INTO alerts (related_table, related_id, alert_type, severity, status) VALUES ('ngo_accounts', {{ $node["Fetch Active NGO Accounts"].json.ngo_account_id }}, 'TITLE_MISMATCH', 'high', 'open') RETURNING alert_id;`
- **Input:** Context details.
- **Output:** `{"alert_id": 505}`
- **Next Node:** Node 15

#### Node 15: Retrieve Regulation 6 Clauses (RAG)
- **Node Type:** HTTP Request
- **Purpose:** Retrieves SBP Regulation 6 §4 context to guide STR preparation.
- **Configuration:** Method: `POST`, URL: `http://localhost:8000/api/vector-db/query`, Body: `{"query": "NGO advertised title mismatch STR caution"}`.
- **Input:** Query string.
- **Output:** `{"clause": "Regulation 6 §4: If any difference... immediate caution shall be marked... and the matter considered for filing STR."}`
- **Next Node:** Node 16

#### Node 16: Draft STR Narrative (LLM Node)
- **Node Type:** OpenAI Chat
- **Purpose:** Generates a compliant, citable STR narrative detailing the mismatch.
- **Configuration:** Model: `grok-beta`, Temperature: 0.1, Prompt: See Section 10.
- **Input:** Customer data + mismatch details + RAG context.
- **Output:** `{"str_narrative": "STR Draft: Mismatch between registered title Roshan Family Welfare Trust and advertised title Roshan Foundation for Children... per Reg 6 §4..."}`
- **Next Node:** Node 17

#### Node 17: Insert Draft STR
- **Node Type:** Postgres
- **Purpose:** Stores the generated narrative for compliance officer review.
- **Configuration:** Operation: `Execute Query`, Query: `INSERT INTO str_reports (customer_id, narrative, status) VALUES ({{ $node["Fetch Active NGO Accounts"].json.customer_id }}, '{{ $json.str_narrative }}', 'draft') RETURNING str_id;`
- **Input:** Narrative text.
- **Output:** `{"str_id": 89}`
- **Next Node:** Node 18

#### Node 18: Manual Compliance Review
- **Node Type:** Manual Approval
- **Purpose:** Suspends the flow for final compliance filing sign-off.
- **Configuration:** Request Message: `Approve STR submission for Roshan Family Welfare Trust (Title Mismatch).`
- **Input:** STR details.
- **Output:** `{"approved": true}` or `{"approved": false}`
- **Next Node:** Node 19 (on `true`), Node 20 (on `false`)

#### Node 19: Submit STR to FMU
- **Node Type:** HTTP Request
- **Purpose:** Transmits the approved STR payload to the mock FMU gateway.
- **Configuration:** Method: `POST`, URL: `http://localhost:5678/mock-fmu/str-submit`, Body: `{"str_id": {{ $node["Insert Draft STR"].json.str_id }}, "narrative": "{{ $node["Draft STR Narrative"].json.str_narrative }}"}`.
- **Input:** STR data.
- **Output:** `{"fmu_reference": "STR-NGO-7781"}`
- **Next Node:** Node 20

#### Node 20: Write Audit Log
- **Node Type:** Postgres
- **Purpose:** Records the monitoring outcome.
- **Configuration:** Operation: `Execute Query`, Query: `INSERT INTO audit_logs (workflow_name, action, actor, reference_table, reference_id, details) VALUES ('NGO_Integrity_Monitor', 'INTEGRITY_CHECK_COMPLETED', 'system', 'ngo_accounts', {{ $node["Fetch Active NGO Accounts"].json.ngo_account_id }}, '{"title_verdict": "{{ $node["Call Title Similarity Model"].json.verdict }}", "fmu_str_filed": {{ $node["Submit STR to FMU"].json.fmu_reference ? "true" : "false" }} }'::jsonb);`
- **Input:** Final execution state.
- **Output:** Standard success.
- **Next Node:** None (Workflow terminates)

### 7. Node Connection Diagram
```
[Node 1: Weekly Scan Trigger]
             ↓
[Node 2: Fetch Active NGO Accounts]
             ↓
[Node 3: Fetch Governing Body]
             ↓
[Node 4: Loop Members] ──▶ [Node 5: Match via ML] ──▶ [Node 6: Match ≥ 60%?] ──(Yes)──▶ [Node 7: Insert Alert]
          ▲                     │                                │(No)                       │
          └─────────────────────┴────────────────────────────────┴───────────────────────────┘
                                                 ↓
                                      [Node 8: Merge Results]
                                                 ↓
                                    [Node 9: Fetch Solicitation Logs]
                                                 ↓
                                    [Node 10: Fetch Registered Title]
                                                 ↓
                                    [Node 11: Call Title Similarity ML]
                                                 ↓
                                    [Node 12: IF Title Mismatch?] ───(No)───▶ [Node 20: Write Audit Log]
                                                 │(Yes)                                     ↓
                                                 ▼                                        [End]
                                    [Node 13: Place Caution Hold]
                                                 ↓
                                    [Node 14: Insert Mismatch Alert]
                                                 ↓
                                    [Node 15: Retrieve Reg 6 (RAG)]
                                                 ↓
                                    [Node 16: Draft STR Narrative (LLM)]
                                                 ↓
                                    [Node 17: Insert Draft STR]
                                                 ↓
                                    [Node 18: Manual Compliance Review]
                                                 │(Approved)
                                                 ▼
                                    [Node 19: Submit STR to FMU]
                                                 ↓
                                    [Node 20: Write Audit Log]
                                                 ↓
                                               [End]
```

### 8. PostgreSQL Queries
```sql
-- Node 2: Fetch active NGO accounts
SELECT ngo_account_id, customer_id, registered_title FROM ngo_accounts;

-- Node 3: Fetch governing body members
SELECT customer_id, governing_body FROM ngo_accounts WHERE ngo_account_id = 1;

-- Node 7: Insert member alert
INSERT INTO alerts (related_table, related_id, alert_type, severity, status) 
VALUES ('ngo_accounts', 1, 'DPPP_MEMBER', 'high', 'open');

-- Node 9: Fetch solicitation logs (simulation)
SELECT advertised_title, account_number 
FROM transactions t 
JOIN accounts a ON t.account_id = a.account_id 
WHERE a.account_type = 'ngo' 
LIMIT 1;

-- Node 10: Fetch registered title
SELECT registered_title 
FROM ngo_accounts n 
JOIN accounts a ON n.customer_id = a.customer_id 
WHERE a.account_number = 'ACC-NPO-321';

-- Node 13: Apply caution hold
UPDATE accounts 
SET status = 'dormant', frozen_reason = 'Caution Hold - Title Mismatch' 
WHERE account_number = 'ACC-NPO-321';

-- Node 14: Insert mismatch alert
INSERT INTO alerts (related_table, related_id, alert_type, severity, status) 
VALUES ('ngo_accounts', 1, 'TITLE_MISMATCH', 'high', 'open') 
RETURNING alert_id;

-- Node 17: Insert draft STR
INSERT INTO str_reports (customer_id, narrative, status) 
VALUES (101, 'STR Draft Context...', 'draft') 
RETURNING str_id;

-- Node 20: Write audit log
INSERT INTO audit_logs (workflow_name, action, actor, reference_table, reference_id, details) 
VALUES ('NGO_Integrity_Monitor', 'INTEGRITY_CHECK_COMPLETED', 'system', 'ngo_accounts', 1, '{"title_verdict": "mismatch", "fmu_str_filed": true}'::jsonb);
```

### 9. Rule Engine
```
IF member_match_probability >= 0.60 THEN
    Action: Raise Alert DPPP_MEMBER, Route = REVIEW_QUEUE
END IF;

IF title_similarity_score < 0.75 THEN
    Action: Set verdict = 'mismatch', Place CAUTION_HOLD on account, Raise Alert TITLE_MISMATCH, Route = STR_CONSIDERATION
ELSE
    Action: Set verdict = 'match', Route = PASS
END IF;

IF personal_account_used_for_donations = TRUE THEN
    Action: Enforce DEBIT_BLOCK immediately, Reason = "Reg 6 §5 Prohibition Violation"
END IF;
```

### 10. AI Reasoning
- **Reason AI is Needed:** The STR narrative must contextualize the discrepancy between the registered trust name ("Roshan Family Welfare Trust") and the advertised campaign title ("Roshan Foundation for Children") while citing SBP Regulation 6 §4 directly. An LLM performs this qualitative summary, ensuring the narrative aligns with the regulatory requirement for filing STRs on unverified name anomalies.
- **Expected Input:**
  - Registered Trust: Roshan Family Welfare Trust
  - Solicited Title: Roshan Foundation for Children
  - Account Number: ACC-NPO-321
  - RAG Retrieval: "Regulation 6 §4: If any difference... immediate caution shall be marked... and the matter considered for filing STR."
- **Prompt:**
  ```
  You are an AML Compliance Officer drafting a Suspicious Transaction Report narrative.
  Draft the narrative using these details:
  - Subject NGO: Roshan Family Welfare Trust
  - Soliciting Name: Roshan Foundation for Children
  - Account Impacted: ACC-NPO-321
  - Compliance Clause: {RAG retrieval: "Regulation 6 §4 of SBP AML/CFT/CPF Regulations"}
  
  Describe the difference in campaign title and explain that an immediate caution hold has been applied. Cite the SBP regulation. Do not invent details or use placeholders.
  ```
- **Expected Output:**
  `"This STR is filed in compliance with Regulation 6 §4 of the SBP AML/CFT/CPF Regulations. A donation advertisement was identified using the name 'Roshan Foundation for Children' directing funds to account ACC-NPO-321. The registered title of the account is 'Roshan Family Welfare Trust'. Due to this discrepancy, an immediate caution hold was placed..."`

### 11. Human Approval
- **Senior Management:** Required during the initial onboarding or EDD update phase of the NGO profile (Regulation 6 §1).
- **Compliance Officer:** Required to review and authorize the submission of the drafted STR to the FMU (Node 18).

### 12. Database Updates
- `accounts`: Updates `status = 'dormant'` and `frozen_reason = 'Caution Hold - Title Mismatch'`.
- `alerts`: Inserts `TITLE_MISMATCH` and `DPPP_MEMBER` records.
- `str_reports`: Inserts draft STR and updates status to `filed` upon approval.
- `audit_logs`: Inserts execution trace.

### 13. Notifications
- **Slack:** Message to `#compliance-ops`: `ALERT [TITLE MISMATCH]: NGO Account ACC-NPO-321 has been placed on caution hold. Mismatch detected.`
- **Email:** Alert sent to the Compliance Officer containing the drafted STR for verification.
- **Audit Logs:** Logs system checks and user decisions.

### 14. Error Handling
- **Missing Governing Body List:** If `governing_body` is null, the workflow logs a warning to `audit_logs` with severity `medium` and flags the account for `CDD_INCOMPLETE`. It does not stop the title matching check.
- **Similarity Model Failure:** If the local FastNLP/FastAPI similarity model fails (500/Timeout), the workflow defaults to a strict exact-string match check. If it mismatch-fails on exact-string, it triggers the caution hold and alerts the analyst for manual verification.

### 15. Synthetic Test Data
```sql
-- Setup Customer and Account
INSERT INTO customers (customer_id, full_name, cnic, customer_type, country) 
VALUES (101, 'Roshan Family Welfare Trust', NULL, 'trust', 'Pakistan');

INSERT INTO accounts (customer_id, account_number, account_type, status) 
VALUES (101, 'ACC-NPO-321', 'ngo', 'active');

-- Setup NGO Details with Governing Body
INSERT INTO ngo_accounts (customer_id, registered_title, governing_body) 
VALUES (101, 'Roshan Family Welfare Trust', '[{"name": "Salman Qureshi", "cnic": "35202-1111111-1"}]'::jsonb);

-- Setup PEP Match target
INSERT INTO pep_list (full_name, id_number, pep_category, country) 
VALUES ('Salman Qureshi', '35202-1111111-1', 'domestic', 'Pakistan');
```

---
---

# 3. USE CASE 10 — Third-Party CDD Reliance Oversight & Ongoing Monitoring Assurance

### 1. Workflow Name
`ThirdParty_CDD_Reliance_Oversight`

### 2. Business Objective
Ensure that whenever the bank relies on a third-party financial institution to perform CDD measures on its customers, the third party is monitored, regulated, and verified as active. Automatically track the verification status, verify the third party's jurisdiction risk, and log that the bank retains ultimate responsibility for ongoing monitoring.

### 3. Applicable SBP Regulations
- **Regulation 3 — Reliance on Third Party Financial Institutions for CDD Measures (§1–7)**:
  - **§2**: The ultimate responsibility for CDD measures remains with the relying SBP-RE.
  - **§3**: SBP-REs must remain responsible for their overall AML/CFT/CPF obligations, including monitoring transactions and filing STRs.
  - **§4**: SBP-REs must immediately obtain the customer identity documents and files from the third party.
  - **§5**: SBP-REs must satisfy themselves that the third party is regulated, supervised or monitored, and has measures in place for CDD compliance, record-keeping, and security.
  - **§6**: SBP-REs must verify compliance with Regulation 14 (Counter Measures for High Risk Jurisdictions) when determining third-party countries.
  - **§7**: Group-wide AML compliance rules apply if the third party is part of the same corporate group.

### 4. Business Scenario
The bank relies on "Gulf Trust Bank" (UAE) for CDD on corporate clients. A quarterly cron trigger runs the oversight workflow. The system fetches the Gulf Trust Bank registry record and calls a mock regulator registry API, which returns an unstructured free-text status disclosure. The workflow retrieves SBP Regulation 3 §5 from ChromaDB, uses a Groq LLM node to analyze the text, and classifies the regulatory status as `active`. The system then screens the UAE against the versioned FATF high-risk list (`jurisdiction_list`), confirming UAE is a low-risk country. A check is executed on the documents table to verify that all Annexure I/II records for the related clients have been received. If the records are present, a dossier is generated stating the bank's ultimate monitoring responsibility. If a third party like "Offshore Credit Co" in "Country-X" is processed, the system raises a `RELIANCE_BREACH` alert due to its high-risk location and suspended status, prompting compliance intervention.

### 5. Workflow Overview
```
Scheduled Trigger (Quarterly Review)
                     ↓
        Fetch Third Party Reliance Records
                     ↓
        Query Regulator Registry Mock API
                     ↓
        Retrieve Reg 3 §5 Clauses via RAG (Chroma)
                     ↓
        AI Classify Regulatory Status (LLM)
                     ↓
        [IF Status = Suspended OR Country = High Risk] ──(Yes)──▶ [Insert Alert: RELIANCE_BREACH]
                     │(No)                                                 ↓
        Check CDD Document Completeness                              [Pause for Compliance Sign-off]
                     ↓
        [IF Documents Incomplete] ──(Yes)──▶ [Insert Alert: CDD_INCOMPLETE]
                     │(No)
                     ▼
        Set Bank Ongoing Monitoring Ownership (Set)
                     ↓
        Insert Reliance Dossier & Update Audit Logs
                     ↓
                   [End]
```

### 6. Complete n8n Node Flow

#### Node 1: Quarterly Timer
- **Node Type:** Schedule Trigger
- **Purpose:** Initiates quarterly compliance reviews of active third-party reliance agreements.
- **Configuration:** Cron Expression: `0 9 1 */3 *` (Quarterly at 9:00 AM on the 1st of the month).
- **Input:** Chron context.
- **Output:** Execution timestamp.
- **Next Node:** Node 2

#### Node 2: Fetch Reliance Arrangements
- **Node Type:** Postgres
- **Purpose:** Pulls all registered third-party reliance relationships.
- **Configuration:** Operation: `Execute Query`, Query: `SELECT reliance_id, third_party_name, regulator, is_same_corporate_group, country, regulatory_status FROM reliance_arrangements;`
- **Input:** Trigger context.
- **Output:** Array of reliance arrangements.
- **Next Node:** Node 3

#### Node 3: Query Registry API
- **Node Type:** HTTP Request
- **Purpose:** Calls the mock central regulator API to fetch the third party's current status text.
- **Configuration:** Method: `GET`, URL: `http://localhost:5678/mock-registry/status`, Query Parameters: `name={{ $json.third_party_name }}&country={{ $json.country }}`.
- **Input:** Arrangement record.
- **Output:** `{"disclosure_text": "Gulf Trust Bank is registered under UAE Central Bank. Review: License is currently active with no sanctions."}`
- **Next Node:** Node 3b

#### Node 3b: Retrieve Regulation 3 §5 (RAG)
- **Node Type:** HTTP Request
- **Purpose:** Retrieves SBP Regulation 3 §5 text from the vector store to ground status interpretation.
- **Configuration:** Method: `POST`, URL: `http://localhost:8000/api/vector-db/query`, Body: `{"query": "third party regulated supervised CDD record keeping"}`.
- **Input:** Vector query parameters.
- **Output:** `{"clause": "Regulation 3 §5: Satisfy themselves that the third party is regulated, supervised or monitored..."}`
- **Next Node:** Node 4

#### Node 4: AI Interpret Status (LLM Node)
- **Node Type:** OpenAI Chat
- **Purpose:** Extracts status classification (`active`, `suspended`, `unknown`) from unstructured disclosure text using retrieved regulations.
- **Configuration:** Model: `grok-beta`, Temperature: 0.1, Prompt: See Section 10.
- **Input:** Registry disclosure text + RAG clause.
- **Output:** `{"regulatory_status": "active", "confidence": 0.95}`
- **Next Node:** Node 4b

#### Node 4b: Map Registry Status
- **Node Type:** Set
- **Purpose:** Normalizes the LLM's classification result for database storage.
- **Configuration:** Mappings: `reliance_id`, `status` mapping to `regulatory_status`.
- **Input:** LLM JSON response.
- **Output:** `{"reliance_id": 1, "status": "active"}`
- **Next Node:** Node 5

#### Node 5: Update Reliance Status
- **Node Type:** Postgres
- **Purpose:** Persists the verified status to the reliance table.
- **Configuration:** Operation: `Execute Query`, Query: `UPDATE reliance_arrangements SET regulatory_status = '{{ $json.status }}', last_verified = now() WHERE reliance_id = {{ $json.reliance_id }};`
- **Input:** Normalized status.
- **Output:** Update acknowledgment.
- **Next Node:** Node 6

#### Node 6: IF Group Member
- **Node Type:** IF
- **Purpose:** Routes group-wide AML policy checks (Regulation 3 §7).
- **Configuration:** Condition: `{{ $node["Fetch Reliance Arrangements"].json.is_same_corporate_group === true }}`.
- **Input:** Arrangement metadata.
- **Output:** `true` (leads to Node 7) or `false` (leads to Node 8).
- **Next Node:** Node 7 (on `true`), Node 8 (on `false`)

#### Node 7: Verify Group Policy
- **Node Type:** Postgres
- **Purpose:** Confirms group compliance records are logged (Regulation 3 §7(a)).
- **Configuration:** Operation: `Execute Query`, Query: `SELECT document_id FROM documents WHERE related_table = 'reliance_arrangements' AND related_id = {{ $node["Fetch Reliance Arrangements"].json.reliance_id }} AND document_type = 'GROUP_AML_POLICY';`
- **Input:** Arrangement ID.
- **Output:** Group policy record ID.
- **Next Node:** Node 8

#### Node 8: Fetch Jurisdiction Risk
- **Node Type:** Postgres
- **Purpose:** Looks up the third party's country risk tier in the SBP list (Regulation 3 §6 & Regulation 14).
- **Configuration:** Operation: `Execute Query`, Query: `SELECT risk_tier FROM jurisdiction_list WHERE country_name = '{{ $node["Fetch Reliance Arrangements"].json.country }}';`
- **Input:** Country name.
- **Output:** `{"risk_tier": "normal"}`
- **Next Node:** Node 9

#### Node 9: IF Breach Detected
- **Node Type:** IF
- **Purpose:** Evaluates breach conditions: suspended status or high-risk jurisdiction.
- **Configuration:** Condition: `{{ $node["Map Registry Status"].json.status !== 'active' || $json.risk_tier === 'high_risk' }}`.
- **Input:** Registry status and risk tier.
- **Output:** `true` (leads to Node 10) or `false` (leads to Node 12).
- **Next Node:** Node 10 (on `true`), Node 12 (on `false`)

#### Node 10: Insert Breach Alert
- **Node Type:** Postgres
- **Purpose:** Logs the reliance breach event.
- **Configuration:** Operation: `Execute Query`, Query: `INSERT INTO alerts (related_table, related_id, alert_type, severity, status) VALUES ('reliance_arrangements', {{ $node["Fetch Reliance Arrangements"].json.reliance_id }}, 'RELIANCE_BREACH', 'high', 'open') RETURNING alert_id;`
- **Input:** Exception metadata.
- **Output:** `{"alert_id": 612}`
- **Next Node:** Node 11

#### Node 11: Compliance Approval Sign-Off
- **Node Type:** Manual Approval
- **Purpose:** Pauses the workflow for compliance officer override/sign-off.
- **Configuration:** Request Message: `Reliance breach detected for Gulf Trust Bank. Authorize continuation?`
- **Input:** Breach details.
- **Output:** Approval verdict.
- **Next Node:** Node 12 (on approval), None (on rejection)

#### Node 12: Fetch Customer CDD Files
- **Node Type:** Postgres
- **Purpose:** Verifies that customer files have been obtained from the third party (Regulation 3 §4).
- **Configuration:** Operation: `Execute Query`, Query: `SELECT document_type FROM documents WHERE related_table = 'reliance_arrangements' AND related_id = {{ $node["Fetch Reliance Arrangements"].json.reliance_id }};`
- **Input:** Reliance context.
- **Output:** Array of document records.
- **Next Node:** Node 13

#### Node 13: IF Documents Complete
- **Node Type:** IF
- **Purpose:** Deterministically evaluates the document checklist against Annexure I & II.
- **Configuration:** Condition: check if `FORM_A`, `CNIC_COPY`, and `KYC_SHEET` exist in documents array.
- **Input:** Document records.
- **Output:** `true` (leads to Node 15) or `false` (leads to Node 14).
- **Next Node:** Node 15 (on `true`), Node 14 (on `false`)

#### Node 14: Insert CDD Alert
- **Node Type:** Postgres
- **Purpose:** Logs alert for missing customer files.
- **Configuration:** Operation: `Execute Query`, Query: `INSERT INTO alerts (related_table, related_id, alert_type, severity, status) VALUES ('reliance_arrangements', {{ $node["Fetch Reliance Arrangements"].json.reliance_id }}, 'CDD_INCOMPLETE', 'medium', 'open');`
- **Input:** Deficit data.
- **Output:** Alert context.
- **Next Node:** Node 15

#### Node 15: Log Bank Responsibility
- **Node Type:** Set
- **Purpose:** Explicitly tags the bank's responsibility for ongoing monitoring (Regulation 3 §3).
- **Configuration:** Mappings: `bank_retains_ultimate_responsibility = true`, `monitoring_owner = 'Bank compliance department'`.
- **Input:** Previous workflow status.
- **Output:** Ownership statement.
- **Next Node:** Node 16

#### Node 16: Insert Reliance Dossier
- **Node Type:** Postgres
- **Purpose:** Stores the final reliance oversight review dossier in the audit ledger.
- **Configuration:** Operation: `Execute Query`, Query: `INSERT INTO documents (related_table, related_id, document_type, litigation_hold, file_reference) VALUES ('reliance_arrangements', {{ $node["Fetch Reliance Arrangements"].json.reliance_id }}, 'RELIANCE_DOSSIER', FALSE, 'DOSSIER-' || {{ $node["Fetch Reliance Arrangements"].json.reliance_id }}) RETURNING document_id;`
- **Input:** Ownership statement.
- **Output:** `{"document_id": 490}`
- **Next Node:** Node 17

#### Node 17: Write Audit Log
- **Node Type:** Postgres
- **Purpose:** Appends verification event to the system log.
- **Configuration:** Operation: `Execute Query`, Query: `INSERT INTO audit_logs (workflow_name, action, actor, reference_table, reference_id, details) VALUES ('ThirdParty_CDD_Reliance_Oversight', 'RELIANCE_VERIFICATION_COMPLETED', 'system', 'reliance_arrangements', {{ $node["Fetch Reliance Arrangements"].json.reliance_id }}, '{"status": "{{ $node["Map Registry Status"].json.status }}", "risk_tier": "{{ $node["Fetch Jurisdiction Risk"].json.risk_tier }}"}'::jsonb);`
- **Input:** State details.
- **Output:** Success.
- **Next Node:** None (Workflow completes)

### 7. Node Connection Diagram
```
[Node 1: Quarterly Timer]
           ↓
[Node 2: Fetch Reliance Arrangements]
           ↓
[Node 3: Query Registry API]
           ↓
[Node 3b: Retrieve Reg 3 §5 (RAG)]
           ↓
[Node 4: AI Interpret Status (LLM)]
           ↓
[Node 4b: Map Registry Status]
           ↓
[Node 5: Update Reliance Status]
           ↓
[Node 6: IF Group Member?] ────(Yes)───▶ [Node 7: Verify Group Policy]
           │(No)                                      │
           ▼                                          ▼
[Node 8: Fetch Jurisdiction Risk] ◀───────────────────┘
           ↓
[Node 9: IF Breach Detected?] ───(Yes)───▶ [Node 10: Insert Breach Alert]
           │(No)                                       ↓
           │                             [Node 11: Compliance Approval]
           │                                           │(Approved)
           ├───────────────────────────────────────────┘
           ▼
[Node 12: Fetch Customer CDD Files]
           ↓
[Node 13: IF Docs Complete?] ────(No)────▶ [Node 14: Insert CDD Alert]
           │(Yes)                                      │
           ▼                                           ▼
[Node 15: Log Bank Responsibility] ◀───────────────────┘
           ↓
[Node 16: Insert Reliance Dossier]
           ↓
[Node 17: Write Audit Log]
           ↓
         [End]
```

### 8. PostgreSQL Queries
```sql
-- Node 2: Fetch active third-party arrangements
SELECT reliance_id, third_party_name, regulator, is_same_corporate_group, country, regulatory_status FROM reliance_arrangements;

-- Node 5: Update status and verification timestamp
UPDATE reliance_arrangements SET regulatory_status = 'active', last_verified = now() WHERE reliance_id = 1;

-- Node 7: Verify group policy exists
SELECT document_id FROM documents WHERE related_table = 'reliance_arrangements' AND related_id = 1 AND document_type = 'GROUP_AML_POLICY';

-- Node 8: Fetch jurisdiction risk
SELECT risk_tier FROM jurisdiction_list WHERE country_name = 'UAE';

-- Node 10: Insert breach alert
INSERT INTO alerts (related_table, related_id, alert_type, severity, status) 
VALUES ('reliance_arrangements', 1, 'RELIANCE_BREACH', 'high', 'open') 
RETURNING alert_id;

-- Node 12: Pull CDD documents list
SELECT document_type FROM documents WHERE related_table = 'reliance_arrangements' AND related_id = 1;

-- Node 14: Insert CDD incomplete alert
INSERT INTO alerts (related_table, related_id, alert_type, severity, status) 
VALUES ('reliance_arrangements', 1, 'CDD_INCOMPLETE', 'medium', 'open');

-- Node 16: Insert reliance dossier
INSERT INTO documents (related_table, related_id, document_type, litigation_hold, file_reference) 
VALUES ('reliance_arrangements', 1, 'RELIANCE_DOSSIER', FALSE, 'DOSSIER-1') 
RETURNING document_id;

-- Node 17: Write audit log
INSERT INTO audit_logs (workflow_name, action, actor, reference_table, reference_id, details) 
VALUES ('ThirdParty_CDD_Reliance_Oversight', 'RELIANCE_VERIFICATION_COMPLETED', 'system', 'reliance_arrangements', 1, '{"status": "active", "risk_tier": "normal"}'::jsonb);
```

### 9. Rule Engine
```
IF regulatory_status IN ('suspended', 'unknown') THEN
    Action: Trigger RELIANCE_BREACH alert, Set Severity = HIGH
END IF;

IF country_risk_tier = 'high_risk' THEN
    Action: Trigger RELIANCE_BREACH alert, Set Severity = HIGH
END IF;

IF is_same_corporate_group = TRUE AND GROUP_AML_POLICY_document IS NULL THEN
    Action: Trigger RELIANCE_BREACH alert, Set Reason = "Missing Group Policy"
END IF;

IF customer_CDD_documents NOT CONTAINS ('FORM_A', 'CNIC_COPY') THEN
    Action: Trigger CDD_INCOMPLETE alert, Set Severity = MEDIUM
END IF;
```

### 10. AI Reasoning
- **Reason AI is Needed:** The central registry responses for foreign jurisdictions often arrive as unstructured free-text disclosures. The AI node is needed to analyze this text and classify it into standard states (`active`, `suspended`, `unknown`) based on the retrieved SBP Regulation 3 §5 standards, ensuring that human-like interpretation of legal statements is grounded and citable.
- **Expected Input:**
  - Regulator Name: UAE Central Bank
  - Disclosure Text: "Gulf Trust Bank is registered under UAE Central Bank. Review: License is currently active with no sanctions."
  - RAG Retrieval: "Regulation 3 §5: Satisfy themselves that the third party is regulated, supervised or monitored..."
- **Prompt:**
  ```
  You are a Regulatory Risk Classifier. Analyze the following regulator registry text and extract the compliance status:
  - Text: "Gulf Trust Bank is registered under UAE Central Bank. Review: License is currently active with no sanctions."
  - SBP Standard: {RAG retrieval: "Regulation 3 §5"}
  
  Classify the status into one of these exact values: active / suspended / unknown.
  Provide your classification in a structured JSON object. Include the reasoning.
  ```
- **Expected Output:**
  `{"regulatory_status": "active", "reasoning": "The registry text confirms the license is active with no sanctions, satisfying SBP Regulation 3 §5."}`

### 11. Human Approval
- **Compliance Officer:** Required to review and approve the continuation of the relationship if a `RELIANCE_BREACH` alert is triggered (Node 11).

### 12. Database Updates
- `reliance_arrangements`: Updates `regulatory_status` and `last_verified`.
- `alerts`: Inserts `RELIANCE_BREACH` or `CDD_INCOMPLETE` alert records.
- `documents`: Inserts drafted `RELIANCE_DOSSIER` record.
- `audit_logs`: Inserts trace log.

### 13. Notifications
- **Slack:** Message to `#cdd-reliance`: `ALERT [RELIANCE BREACH]: Third-party Gulf Trust Bank failed verification check. Review required.`
- **Email:** Report sent to the Head of Compliance if the registry status is marked `suspended`.
- **Dashboard:** Updates the risk metrics on the Third-Party Reliance monitoring screen.

### 14. Error Handling
- **Registry API Offline:** If the registry API returns a timeout/503, the status is set to `unknown` and a retry is scheduled. The workflow does not assume the relationship is compliant.
- **Missing Jurisdiction classification:** If the country does not exist in `jurisdiction_list`, the country risk tier defaults to `high_risk` (cautious default), triggering the breach evaluation path.

### 15. Synthetic Test Data
```sql
-- Setup Reliance Arrangement ( Gulf Trust UAE - Normal )
INSERT INTO reliance_arrangements (reliance_id, third_party_name, regulator, is_same_corporate_group, regulatory_status, country) 
VALUES (1, 'Gulf Trust Bank', 'UAE Central Bank', FALSE, 'pending_verification', 'UAE');

-- Setup Reliance Arrangement ( Offshore Credit - Suspended Country-X )
INSERT INTO reliance_arrangements (reliance_id, third_party_name, regulator, is_same_corporate_group, regulatory_status, country) 
VALUES (2, 'Offshore Credit Co', 'Central Bank of Country-X', FALSE, 'pending_verification', 'Country-X');

-- Setup Jurisdiction List Risk Tiers
INSERT INTO jurisdiction_list (country_name, risk_tier, counter_measure) VALUES ('UAE', 'normal', NULL);
INSERT INTO jurisdiction_list (country_name, risk_tier, counter_measure) VALUES ('Country-X', 'high_risk', 'Enhanced Due Diligence Required');
```

---
---

# 4. USE CASE 11 — High-Risk Jurisdiction Counter-Measures & Cross-Border Exposure Control

### 1. Workflow Name
`HighRisk_Jurisdiction_Countermeasures_Orchestration`

### 2. Business Objective
Monitor exposure to high-risk jurisdictions as designated by FATF and SBP. Apply tiered compliance counter-measures (Enhanced Due Diligence, transaction limits, or outright transaction rejections) based on geographical risk ratings, and automate the re-screening of existing customers whenever a country's risk status is updated.

### 3. Applicable SBP Regulations
- **Regulation 14 — Counter Measures for High Risk Jurisdictions (§1)**:
  - **§1**: SBP-REs must apply enhanced measures and counter-measures to countries listed in the Counter Measures for High Risk Jurisdictions Rules, 2020.
- **Regulation 2 — Customer Due Diligence (§16(b))**:
  - **§16(b)**: SBP-REs must apply Enhanced Due Diligence (EDD) to business relationships and transactions with persons from high-risk countries.
- **Regulation 9 — Correspondent Banking (§2)**:
  - **§2**: Apply EDD when establishing or continuing relationships with respondent banks in high-risk jurisdictions.

### 4. Business Scenario
The State Bank of Pakistan updates its listing of high-risk jurisdictions, adding "Country-X" as high-risk. A compliance webhook updates the `jurisdiction_list` table. This event triggers the n8n re-screening workflow. The system queries the `customers` table to identify any active relationships linked to "Country-X". It flags "Bilal Exports (Pvt) Ltd" (Country-X) and immediately updates the customer's risk profile to `high`, triggers a `JURISDICTION_RISK` alert, and blocks debit transactions until EDD is completed. Concurrently, a cross-border wire transfer routing through a respondent bank linked to "Country-X" triggers a real-time transaction check. The system calls an ML classifier scoring indirect jurisdiction exposure. The score exceeds the limit, triggering a transaction hold and prompting manual compliance officer review.

### 5. Workflow Overview
```
Trigger (New Transaction Event OR Jurisdiction List Update Webhook)
                                     ↓
                             Switch Event Type
             ┌───────────────────────┴───────────────────────┐
      (New Transaction)                               (List Update)
             ↓                                               ↓
Fetch Customer & Transaction Geo-Data                 Update jurisdiction_list
             ↓                                               ↓
Lookup Risk Tier in jurisdiction_list           Fetch Customers in updated Country
             ↓                                               ↓
      Switch Risk Tier                               Split in Batches
┌────────────┼────────────┐                                  ↓
(Normal)  (Monitored) (High Risk)                    Insert Alert per Customer
   ↓         ↓            ↓                                  ↓
[Exit]   [Monitor]   [Elevate Profile & Alert]       Elevate Profiles to HIGH
                          ↓                                  ↓
                     [IF Reject?] ──(Yes)──▶ [Pause for Compliance Approval]
                          │(No)                        │(If Approved)
                          ▼                            ▼
                   [Update Dashboard] ◀────────────────┘
                          ↓
                   [Insert Audit Log]
```

### 6. Complete n8n Node Flow

#### Node 1: Event Trigger
- **Node Type:** Webhook
- **Purpose:** Receives list update events or transaction notifications.
- **Configuration:** Path: `jurisdiction-risk-trigger`, Method: `POST`.
- **Input:** Event context.
- **Output:** `{"event_type": "LIST_UPDATE", "country": "Country-X", "risk_tier": "high_risk"}`
- **Next Node:** Node 2

#### Node 2: Switch Event Route
- **Node Type:** Switch
- **Purpose:** Routes transaction checks vs list update actions.
- **Configuration:** Rules: If `event_type` is `TRANSACTION` -> Route 1; If `event_type` is `LIST_UPDATE` -> Route 2.
- **Input:** Trigger payload.
- **Output:** Route selected.
- **Next Node:** Node 3a (Route 1) or Node 3b (Route 2)

#### Node 3a: Fetch Customer Geo-Data
- **Node Type:** Postgres
- **Purpose:** Extracts customer and counterparty locations for transaction screening.
- **Configuration:** Operation: `Execute Query`, Query: `SELECT customer_id, country FROM customers WHERE customer_id = {{ $json.customer_id }};`
- **Input:** Transaction context.
- **Output:** `{"customer_id": 401, "country": "Country-X"}`
- **Next Node:** Node 4a

#### Node 3b: Update Jurisdiction List
- **Node Type:** Postgres
- **Purpose:** Upserts the country risk designation in the versioned table.
- **Configuration:** Operation: `Execute Query`, Query: `INSERT INTO jurisdiction_list (country_name, risk_tier, counter_measure) VALUES ('{{ $json.country }}', '{{ $json.risk_tier }}', 'EDD Required') ON CONFLICT (country_name) DO UPDATE SET risk_tier = '{{ $json.risk_tier }}', updated_at = now() RETURNING jurisdiction_id;`
- **Input:** Update payload.
- **Output:** `{"jurisdiction_id": 5}`
- **Next Node:** Node 4b

#### Node 4a: Lookup Risk Tier
- **Node Type:** Postgres
- **Purpose:** Checks the country's current classification.
- **Configuration:** Operation: `Execute Query`, Query: `SELECT risk_tier, counter_measure FROM jurisdiction_list WHERE country_name = '{{ $json.country }}';`
- **Input:** Geo data.
- **Output:** `{"risk_tier": "high_risk", "counter_measure": "EDD Required"}`
- **Next Node:** Node 5

#### Node 4b: Fetch Impacted Customers
- **Node Type:** Postgres
- **Purpose:** Identifies all customers residing in the newly designated high-risk country.
- **Configuration:** Operation: `Execute Query`, Query: `SELECT customer_id FROM customers WHERE country = '{{ $node["Event Trigger"].json.country }}';`
- **Input:** Update country context.
- **Output:** Array of customer IDs.
- **Next Node:** Node 11

#### Node 5: Switch Risk Tier Route
- **Node Type:** Switch
- **Purpose:** Branches execution based on the country risk tier.
- **Configuration:** Rules: If `risk_tier` is `high_risk` -> Route 1; If `risk_tier` is `monitored` -> Route 2; If `risk_tier` is `normal` -> Route 3.
- **Input:** Risk tier data.
- **Output:** Route branch.
- **Next Node:** Node 6 (Route 1) or Node 15 (Route 3)

#### Node 6: Map Counter-Measure Status
- **Node Type:** Set
- **Purpose:** Configures variables for risk escalation.
- **Configuration:** Mappings: `escalate_risk = 'high'`, `apply_edd = true`.
- **Input:** Switch branch.
- **Output:** Risk parameters.
- **Next Node:** Node 7

#### Node 7: Elevate Profile Risk
- **Node Type:** Postgres
- **Purpose:** Updates the client risk rating to high in the system of record.
- **Configuration:** Operation: `Execute Query`, Query: `INSERT INTO risk_profiles (customer_id, risk_rating, rating_basis) VALUES ({{ $node["Fetch Customer Geo-Data"].json.customer_id }}, 'high', 'High-Risk Jurisdiction Match') ON CONFLICT (customer_id) DO UPDATE SET risk_rating = 'high', rating_basis = 'High-Risk Jurisdiction Match', last_reviewed = now();`
- **Input:** Risk parameters.
- **Output:** Update status.
- **Next Node:** Node 8

#### Node 8: IF Rejection Required
- **Node Type:** IF
- **Purpose:** Determines if the counter-measure is a strict transaction reject/hold.
- **Configuration:** Condition: `{{ $node["Lookup Risk Tier"].json.counter_measure === 'EDD Required' }}`.
- **Input:** Context.
- **Output:** `true` (leads to Node 9) or `false` (leads to Node 10).
- **Next Node:** Node 9 (on `true`), Node 10 (on `false`)

#### Node 9: Compliance Rejection Review
- **Node Type:** Manual Approval
- **Purpose:** Suspends the workflow for compliance officer sign-off before blocking transactions.
- **Configuration:** Request Message: `Approve high-risk transaction freeze for customer {{ $node["Fetch Customer Geo-Data"].json.customer_id }}.`
- **Input:** Customer details.
- **Output:** Approval status.
- **Next Node:** Node 10 (on approval)

#### Node 10: Insert Jurisdiction Alert
- **Node Type:** Postgres
- **Purpose:** Logs the alert entry.
- **Configuration:** Operation: `Execute Query`, Query: `INSERT INTO alerts (related_table, related_id, alert_type, severity, status) VALUES ('customers', {{ $node["Fetch Customer Geo-Data"].json.customer_id }}, 'JURISDICTION_RISK', 'high', 'open') RETURNING alert_id;`
- **Input:** Approved parameters.
- **Output:** `{"alert_id": 782}`
- **Next Node:** Node 13

#### Node 11: Batch Process Customers
- **Node Type:** Loop Over Items
- **Purpose:** Iterates through the list of impacted customers in batch.
- **Configuration:** Batch size: 50 items.
- **Input:** Customer array.
- **Output:** Single customer item.
- **Next Node:** Node 12

#### Node 12: Insert Alert per Impacted Customer
- **Node Type:** Postgres
- **Purpose:** Logs individual alerts for the re-screened customers.
- **Configuration:** Operation: `Execute Query`, Query: `INSERT INTO alerts (related_table, related_id, alert_type, severity, status) VALUES ('customers', {{ $json.customer_id }}, 'JURISDICTION_RISK', 'high', 'open');`
- **Input:** Loop customer.
- **Output:** Success status.
- **Next Node:** Loop iteration or Node 13 (after loop completion)

#### Node 13: Merge Branches
- **Node Type:** Merge
- **Purpose:** Combines the update and transaction paths for dashboard logging.
- **Configuration:** Mode: `Wait for all inputs`.
- **Input:** Active branch states.
- **Output:** Merged state.
- **Next Node:** Node 14

#### Node 14: Refresh Exposure Dashboard
- **Node Type:** Postgres
- **Purpose:** Updates the consolidated SBP sandbox exposure view.
- **Configuration:** Operation: `Execute Query`, Query: `UPDATE alerts SET status = 'processed' WHERE alert_type = 'JURISDICTION_RISK' AND status = 'open' LIMIT 1;` -- Simulates metric processing.
- **Input:** Merged status.
- **Output:** Refresh acknowledgment.
- **Next Node:** Node 15

#### Node 15: Write System Audit Log
- **Node Type:** Postgres
- **Purpose:** Logs workflow execution to the ledger.
- **Configuration:** Operation: `Execute Query`, Query: `INSERT INTO audit_logs (workflow_name, action, actor, reference_table, reference_id, details) VALUES ('HighRisk_Jurisdiction_Oversight', 'RISK_RESCREEN_COMPLETED', 'system', 'jurisdiction_list', {{ $node["Event Trigger"].json.jurisdiction_id ? $node["Event Trigger"].json.jurisdiction_id : 1 }}, '{"country": "{{ $node["Event Trigger"].json.country }}"}'::jsonb);`
- **Input:** Execution details.
- **Output:** Success.
- **Next Node:** None (Workflow completes)

### 7. Node Connection Diagram
```
[Node 1: Event Trigger]
          ↓
[Node 2: Switch Event Route]
   ├──(TRANSACTION)──▶ [Node 3a: Fetch Customer Geo-Data]
   │                             ↓
   │                   [Node 4a: Lookup Risk Tier]
   │                             ↓
   │                   [Node 5: Switch Risk Tier]
   │                      ├──(high_risk)──▶ [Node 6: Map Counter-Measure]
   │                      │                             ↓
   │                      │                 [Node 7: Elevate Profile Risk]
   │                      │                             ↓
   │                      │                 [Node 8: IF Rejection Required?] ──(Yes)──▶ [Node 9: Manual Review]
   │                      │                             │(No)                                │(Approved)
   │                      │                             ▼                                    ▼
   │                      │                 [Node 10: Insert Alert] ◀────────────────────────┘
   │                      │                             ↓
   │                      │                       [Node 13: Merge]
   │                      └──(normal)───────────▶ [Node 15: Audit Log]
   │
   └──(LIST_UPDATE)──▶ [Node 3b: Update jurisdiction_list]
                                 ↓
                       [Node 4b: Fetch Impacted Customers]
                                 ↓
                       [Node 11: Batch Loop Customers]
                                 ↓
                       [Node 12: Insert Alert per Customer] ────▶ [Node 13: Merge]
                                                                        ↓
                                                           [Node 14: Refresh Dashboard]
                                                                        ↓
                                                           [Node 15: Audit Log]
                                                                        ↓
                                                                      [End]
```

### 8. PostgreSQL Queries
```sql
-- Node 3a: Fetch customer locations
SELECT customer_id, country FROM customers WHERE customer_id = 401;

-- Node 3b: Upsert country risk tier
INSERT INTO jurisdiction_list (country_name, risk_tier, counter_measure) 
VALUES ('Country-X', 'high_risk', 'EDD Required') 
ON CONFLICT (country_name) 
DO UPDATE SET risk_tier = 'high_risk', updated_at = now() 
RETURNING jurisdiction_id;

-- Node 4a: Lookup risk tier
SELECT risk_tier, counter_measure FROM jurisdiction_list WHERE country_name = 'Country-X';

-- Node 4b: Fetch all customers in target country
SELECT customer_id FROM customers WHERE country = 'Country-X';

-- Node 7: Elevate risk profile
INSERT INTO risk_profiles (customer_id, risk_rating, rating_basis) 
VALUES (401, 'high', 'High-Risk Jurisdiction Match') 
ON CONFLICT (customer_id) 
DO UPDATE SET risk_rating = 'high', rating_basis = 'High-Risk Jurisdiction Match', last_reviewed = now();

-- Node 10: Insert alert
INSERT INTO alerts (related_table, related_id, alert_type, severity, status) 
VALUES ('customers', 401, 'JURISDICTION_RISK', 'high', 'open') 
RETURNING alert_id;

-- Node 12: Log batch alert
INSERT INTO alerts (related_table, related_id, alert_type, severity, status) 
VALUES ('customers', 401, 'JURISDICTION_RISK', 'high', 'open');

-- Node 15: Write audit log
INSERT INTO audit_logs (workflow_name, action, actor, reference_table, reference_id, details) 
VALUES ('HighRisk_Jurisdiction_Oversight', 'RISK_RESCREEN_COMPLETED', 'system', 'jurisdiction_list', 5, '{"country": "Country-X"}'::jsonb);
```

### 9. Rule Engine
```
IF country_risk_tier = 'high_risk' THEN
    Action: Set customer_risk = HIGH, Set status = DEBIT_RESTRICTED, Counter-measure = EDD_REQUIRED
ELSE IF country_risk_tier = 'monitored' THEN
    Action: Set customer_risk = MEDIUM, Counter-measure = MONITORING_ENHANCED
ELSE
    Action: Set customer_risk = LOW, Counter-measure = NONE
END IF;

IF country_risk_status_changes = TRUE THEN
    Action: Trigger RE_SCREENING_WORKFLOW for all linked entities
END IF;
```

### 10. AI Reasoning
- **Reason AI is Needed:** The workflow includes an **optional** LLM node at the start (Node 1) only if the incoming update is an unstructured text alert (e.g., a copy-pasted FATF statement or news release) rather than a structured API payload. The AI reads the notice, checks it against the retrieved RAG context of Regulation 14, and outputs the structured country name and risk tier.
- **Expected Input:**
  - Update Notice: "FATF has added Country-X to the list of jurisdictions under increased monitoring (Grey List) following the plenary session."
  - RAG Retrieval: "Regulation 14 §1: comply with obligations in the Counter Measures for High Risk Jurisdictions Rules..."
- **Prompt:**
  ```
  You are an expert Regulatory Analyst. Parse the unstructured text and extract the country and the risk classification based on the SBP framework:
  - Text: "FATF has added Country-X to the list of jurisdictions under increased monitoring (Grey List)..."
  - SBP Rule: {RAG retrieval: "Regulation 14"}
  
  Map "increased monitoring" to "monitored", and "high-risk list" to "high_risk".
  Output the result as a structured JSON object containing "country" and "risk_tier".
  ```
- **Expected Output:**
  `{"country": "Country-X", "risk_tier": "monitored"}`

### 11. Human Approval
- **Compliance Officer:** Required to verify and sign-off on transaction restrictions or account freeze commands (Node 9).

### 12. Database Updates
- `jurisdiction_list`: Updates `risk_tier` and `updated_at`.
- `risk_profiles`: Updates `risk_rating` to high.
- `alerts`: Inserts `JURISDICTION_RISK` alert.
- `audit_logs`: Inserts process log trace.

### 13. Notifications
- **Slack:** Message to `#georisk-alerts`: `EXPOSURE WARN: Country-X elevated to high-risk. Rescreening of 1 customers executed.`
- **Dashboard:** Refreshes SBP Sandbox Geo-exposure metrics.
- **Email:** Notice dispatched to branch managers regarding new EDD requirements for entities linked to the updated country.

### 14. Error Handling
- **Missing Country Data:** If a customer's country field is null, Node 3a routes to a manual queue with alert `COUNTRY_MISSING`. It is never auto-classified as low-risk.
- **Large Batch Rescreening:** If a country list update affects thousands of customers, the Split-in-Batches node is restricted to a batch size of 50 to prevent database locking in the sandbox Postgres instance.

### 15. Synthetic Test Data
```sql
-- Setup Customer 
INSERT INTO customers (customer_id, full_name, cnic, customer_type, country) 
VALUES (401, 'Bilal Exports (Pvt) Ltd', '42101-9999999-1', 'company', 'Country-X');

-- Setup Jurisdiction List starting state
INSERT INTO jurisdiction_list (country_name, risk_tier, counter_measure) 
VALUES ('Country-X', 'normal', NULL);
```

---
---

# 5. USE CASE 14 — Regulatory Record-Keeping, Retention & Litigation-Hold Automation

### 1. Workflow Name
`RecordRetention_And_LitigationHold_Automation`

### 2. Business Objective
Automate compliance with statutory data preservation rules. Enforce the mandatory 10-year retention timeline for transaction logs, customer files, and regulatory reports, manage overriding litigation holds that block data deletion, and orchestrate the generation of citable, reconstructed transaction dossiers for Law Enforcement Agencies (LEAs).

### 3. Applicable SBP Regulations
- **Regulation 8 — Record Keeping (§1–6)**:
  - **§2**: CDD files, account records, and business correspondence must be retained for at least 10 years after the customer relationship ends.
  - **§3**: Transaction records (domestic and international) and reports of STRs/CTRs must be retained for at least 10 years from the date the transaction was completed.
  - **§4**: Records must permit the complete reconstruction of individual transactions (including date, amount, currency type, and account identifier) to provide admissible evidence for LEAs.
  - **§5**: Records linked to active litigation or court orders must be retained indefinitely until the litigation is resolved or authority is lifted, overriding standard retention clocks.
  - **§6**: SBP-REs must satisfy requests or enquiries from LEAs and competent authorities promptly.

### 4. Business Scenario
The bank receives a court order from the Federal Investigation Agency (FIA) placing a litigation hold on "Farah Textiles Ltd" (account `ACC-CORP-987`) due to a financial crime investigation. A legal officer triggers the hold webhook. The system immediately sets `litigation_hold = TRUE` in the `documents` table. A nightly cron job scans the database for records exceeding 10 years (e.g., a wire transfer from 2016). It flags these, but since Farah's documents are marked under a litigation hold, the system blocks deletion. Later, the FIA requests a complete transaction reconstruction bundle for `ACC-CORP-987` covering 2016. The workflow executes an SQL SELECT query, retrieves Regulation 8 §4 from ChromaDB, uses Groq to compile a citable transaction reconstruction cover note, packages the records, pauses for Legal Counsel approval, and dispatches the bundle.

### 5. Workflow Overview
```
Record Creation OR Litigation Webhook OR Cron Timer OR LEA Webhook
                                 ↓
                        Evaluate Event Trigger
    ┌────────────────────────────┼────────────────────────────┐
(Onboarding)              (Litigation Hold)             (LEA Request)
    ↓                            ↓                            ↓
Tag document retention      Update `litigation_hold=TRUE`    Run PostgreSQL SELECT
years=10, start=now()            ↓                            ↓
    ↓                       Insert Audit Log            Retrieve Reg 8 §4 (RAG)
  [End]                          ↓                            ↓
                               [End]                    AI Compile Cover Note
                                                              ↓
                                                        Manual Legal Approval
                                                              │(If Approved)
                                                              ▼
                                                        Email/Deliver Bundle
                                                              ↓
                                                        Insert Audit Log
                                                              ↓
                                                            [End]
```

### 6. Complete n8n Node Flow

#### Node 1: Multi-Trigger Gateway
- **Node Type:** Webhook
- **Purpose:** Central gateway routing record tagging, holds, and LEA requests.
- **Configuration:** Path: `retention-gateway`, Method: `POST`.
- **Input:** Request payload.
- **Output:** `{"event_type": "LEA_INQUIRY", "account_number": "ACC-CORP-987", "start_date": "2016-01-01", "end_date": "2016-12-31"}`
- **Next Node:** Node 2

#### Node 2: Route Action
- **Node Type:** Switch
- **Purpose:** Separates execution paths based on the incoming trigger.
- **Configuration:** Rules: If `event_type` is `RECORD_CREATED` -> Route 1; If `event_type` is `LITIGATION_HOLD` -> Route 2; If `event_type` is `LEA_INQUIRY` -> Route 3; If `event_type` is `RELEASE_HOLD` -> Route 4.
- **Input:** Event type.
- **Output:** Route branch.
- **Next Node:** Node 3 (Route 1), Node 7 (Route 2), Node 10 (Route 3), or Node 17 (Route 4)

#### Node 3: Postgres Tag Document (Route 1)
- **Node Type:** Postgres
- **Purpose:** Tags a new record with a 10-year retention schedule (Regulation 8 §2-3).
- **Configuration:** Operation: `Execute Query`, Query: `INSERT INTO documents (related_table, related_id, document_type, retention_start, retention_years, litigation_hold) VALUES ('{{ $json.table }}', {{ $json.id }}, '{{ $json.doc_type }}', CURRENT_DATE, 10, FALSE) RETURNING document_id;`
- **Input:** Record parameters.
- **Output:** `{"document_id": 881}`
- **Next Node:** None (Onboarding flow ends)

#### Node 7: Apply Litigation Hold (Route 2)
- **Node Type:** Postgres
- **Purpose:** Places documents under litigation hold (Regulation 8 §5).
- **Configuration:** Operation: `Execute Query`, Query: `UPDATE documents SET litigation_hold = TRUE WHERE related_table = 'accounts' AND related_id = (SELECT account_id FROM accounts WHERE account_number = '{{ $json.account_number }}') RETURNING document_id;`
- **Input:** Account number.
- **Output:** `{"document_id": 501}`
- **Next Node:** Node 8

#### Node 8: Write Hold Audit Log
- **Node Type:** Postgres
- **Purpose:** Records the enforcement of a litigation hold.
- **Configuration:** Operation: `Execute Query`, Query: `INSERT INTO audit_logs (workflow_name, action, actor, reference_table, reference_id, details) VALUES ('Record_Retention', 'LITIGATION_HOLD_APPLIED', 'legal_officer', 'documents', {{ $json.document_id }}, '{"order_ref": "{{ $node["Multi-Trigger Gateway"].json.order_reference }}"}'::jsonb);`
- **Input:** Document ID.
- **Output:** Success status.
- **Next Node:** None (Hold process completes)

#### Node 10: Search Records (Route 3)
- **Node Type:** Postgres
- **Purpose:** Executes transaction search for LEA reconstruction bundle (Regulation 8 §4).
- **Configuration:** Operation: `Execute Query`, Query: `SELECT t.transaction_id, t.amount, t.currency, t.txn_type, t.txn_date, a.account_number FROM transactions t JOIN accounts a ON t.account_id = a.account_id WHERE a.account_number = '{{ $json.account_number }}' AND t.txn_date BETWEEN '{{ $json.start_date }}' AND '{{ $json.end_date }}';`
- **Input:** Account number and dates.
- **Output:** Array of transaction rows.
- **Next Node:** Node 11

#### Node 11: Retrieve Regulation 8 §4 (RAG)
- **Node Type:** HTTP Request
- **Purpose:** Pulls Regulation 8 §4 transaction reconstruction guidelines from ChromaDB.
- **Configuration:** Method: `POST`, URL: `http://localhost:8000/api/vector-db/query`, Body: `{"query": "transaction record reconstruction evidence"}`.
- **Input:** Query request.
- **Output:** `{"clause": "Regulation 8 §4: Records shall be sufficient to permit reconstruction... including nature, date, currency amount and account..."}`
- **Next Node:** Node 12

#### Node 12: AI Compile Cover Note (LLM Node)
- **Node Type:** OpenAI Chat
- **Purpose:** Formulates the transaction reconstruction cover sheet citable against Reg 8.
- **Configuration:** Model: `grok-beta`, Temperature: 0.1, Prompt: See Section 10.
- **Input:** Transaction data + RAG clause.
- **Output:** `{"cover_note": "Cover Document: Transaction Reconstruction Bundle... prepared as required by Regulation 8 §4..."}`
- **Next Node:** Node 13

#### Node 13: Package Reconstruction Bundle
- **Node Type:** Set
- **Purpose:** Combines compiled narrative and database logs into one delivery package.
- **Configuration:** Mappings: `narrative` to LLM output, `data` to Node 10 records.
- **Input:** Node 10 and Node 12 outputs.
- **Output:** Structured file package.
- **Next Node:** Node 14

#### Node 14: Legal Manual Sign-Off
- **Node Type:** Manual Approval
- **Purpose:** Suspends execution for mandatory legal review before delivery to external authorities (Regulation 8 §6).
- **Configuration:** Request Message: `Review and sign-off transaction reconstruction bundle for FIA.`
- **Input:** Packaged files.
- **Output:** `{"approved": true}`
- **Next Node:** Node 15 (on approval)

#### Node 15: Deliver Bundle to LEA
- **Node Type:** HTTP Request
- **Purpose:** Sends the approved dossier to the mock LEA endpoint.
- **Configuration:** Method: `POST`, URL: `http://localhost:5678/mock-lea/receive-bundle`, Body: `{{ $json }}`.
- **Input:** Approved package.
- **Output:** `{"status": "delivered", "receipt": "FIA-REC-1002"}`
- **Next Node:** Node 16

#### Node 16: Write Audit Log (Served Inquiry)
- **Node Type:** Postgres
- **Purpose:** Logs record delivery to the system ledger.
- **Configuration:** Operation: `Execute Query`, Query: `INSERT INTO audit_logs (workflow_name, action, actor, reference_table, reference_id, details) VALUES ('Record_Retention', 'LEA_INQUIRY_SERVED', 'legal_officer', 'accounts', 1, '{"receipt": "FIA-REC-1002"}'::jsonb);`
- **Input:** Delivery receipt.
- **Output:** Success status.
- **Next Node:** None (Inquiry flow ends)

#### Node 17: Lift Litigation Hold (Route 4)
- **Node Type:** Postgres
- **Purpose:** Releases hold status once litigation closes.
- **Configuration:** Operation: `Execute Query`, Query: `UPDATE documents SET litigation_hold = FALSE WHERE related_table = 'accounts' AND related_id = (SELECT account_id FROM accounts WHERE account_number = '{{ $json.account_number }}') RETURNING document_id;`
- **Input:** Account reference.
- **Output:** `{"document_id": 501}`
- **Next Node:** Node 18

#### Node 18: Write Release Audit Log
- **Node Type:** Postgres
- **Purpose:** Logs hold release.
- **Configuration:** Operation: `Execute Query`, Query: `INSERT INTO audit_logs (workflow_name, action, actor, reference_table, reference_id, details) VALUES ('Record_Retention', 'LITIGATION_HOLD_RELEASED', 'legal_officer', 'documents', {{ $json.document_id }}, '{}'::jsonb);`
- **Input:** Document ID.
- **Output:** Success.
- **Next Node:** None (Workflow completes)

### 7. Node Connection Diagram
```
              [Node 1: Multi-Trigger Gateway]
                            │
                            ▼
                    [Node 2: Switch Route]
         ┌──────────────────┼──────────────────┬──────────────────┐
  (RECORD_CREATED)   (LITIGATION_HOLD)   (LEA_INQUIRY)     (RELEASE_HOLD)
         │                  │                  │                  │
         ▼                  ▼                  ▼                  ▼
    [Node 3: Tag]     [Node 7: Hold]    [Node 10: Search]   [Node 17: Release]
         │                  │                  │                  │
       [End]          [Node 8: Log]     [Node 11: RAG]      [Node 18: Log]
                            │                  │                  │
                          [End]         [Node 12: AI (LLM)]     [End]
                                               │
                                        [Node 13: Package]
                                               │
                                        [Node 14: Legal Approval]
                                               │(Approved)
                                               ▼
                                        [Node 15: Deliver]
                                               │
                                        [Node 16: Log served]
                                               │
                                             [End]
```

### 8. PostgreSQL Queries
```sql
-- Node 3: Insert retention tagged document
INSERT INTO documents (related_table, related_id, document_type, retention_start, retention_years, litigation_hold) 
VALUES ('transactions', 22, 'wire_transfer_record', CURRENT_DATE, 10, FALSE) 
RETURNING document_id;

-- Node 7: Apply litigation hold
UPDATE documents 
SET litigation_hold = TRUE 
WHERE related_table = 'accounts' 
AND related_id = (SELECT account_id FROM accounts WHERE account_number = 'ACC-CORP-987') 
RETURNING document_id;

-- Node 8: Write hold audit log
INSERT INTO audit_logs (workflow_name, action, actor, reference_table, reference_id, details) 
VALUES ('Record_Retention', 'LITIGATION_HOLD_APPLIED', 'legal_officer', 'documents', 501, '{"order_ref": "FIA-2026-HOLD"}'::jsonb);

-- Node 10: Search records for reconstruction
SELECT t.transaction_id, t.amount, t.currency, t.txn_type, t.txn_date, a.account_number 
FROM transactions t 
JOIN accounts a ON t.account_id = a.account_id 
WHERE a.account_number = 'ACC-CORP-987' 
AND t.txn_date BETWEEN '2016-01-01' AND '2016-12-31';

-- Node 16: Log inquiry served
INSERT INTO audit_logs (workflow_name, action, actor, reference_table, reference_id, details) 
VALUES ('Record_Retention', 'LEA_INQUIRY_SERVED', 'legal_officer', 'accounts', 1, '{"receipt": "FIA-REC-1002"}'::jsonb);

-- Node 17: Release litigation hold
UPDATE documents 
SET litigation_hold = FALSE 
WHERE related_table = 'accounts' 
AND related_id = (SELECT account_id FROM accounts WHERE account_number = 'ACC-CORP-987') 
RETURNING document_id;

-- Node 18: Write release audit log
INSERT INTO audit_logs (workflow_name, action, actor, reference_table, reference_id, details) 
VALUES ('Record_Retention', 'LITIGATION_HOLD_RELEASED', 'legal_officer', 'documents', 501, '{}'::jsonb);

-- Nightly retention scan query
SELECT document_id, related_table, related_id FROM documents 
WHERE (retention_start + (retention_years || ' years')::interval) <= now() 
AND litigation_hold = FALSE;
```

### 9. Rule Engine
```
IF document_created = TRUE THEN
    Action: Set retention_start = CURRENT_DATE, Set retention_years = 10
END IF;

IF (retention_start + retention_years) <= now() AND litigation_hold = FALSE THEN
    Action: Trigger RETENTION_REVIEW alert, Set status = 'eligible_for_archival'
ELSE IF litigation_hold = TRUE THEN
    Action: Block deletion, Status = 'preserved_on_hold'
END IF;
```

### 10. AI Reasoning
- **Reason AI is Needed:** When packaging records for court or LEA delivery, SBP Regulation 8 §4 requires that the bank prove the records provide a complete reconstructed audit trail. The LLM acts as a legal clerk, matching the extracted database columns (nature, currency, amount, accounts) against the retrieved SBP clause text to write a grounded cover sheet certificate.
- **Expected Input:**
  - Transactions: `[{"transaction_id": 99, "amount": 750000.00, "currency": "PKR", "txn_type": "wire_out", "txn_date": "2016-03-01", "account_number": "ACC-CORP-987"}]`
  - RAG Retrieval: "Regulation 8 §4: Records shall be sufficient to permit reconstruction... including nature, date, currency amount and account..."
- **Prompt:**
  ```
  You are an expert Bank General Counsel. Compile a transaction reconstruction cover note for law enforcement.
  Ensure you certify that the attached records fulfill the requirements of SBP Regulation 8.
  - Data: [{"transaction_id": 99, "amount": 750000.00, "currency": "PKR", "txn_type": "wire_out", "txn_date": "2016-03-01", "account_number": "ACC-CORP-987"}]
  - Regulatory Standard: {RAG retrieval: "Regulation 8 §4"}
  
  Do not include placeholders. State the account number, date range, and confirm all fields mandated by SBP §4 are included.
  ```
- **Expected Output:**
  `"Reconstruction Bundle Certificate. This dossier is prepared under SBP Regulation 8 §4. It contains transaction logs for account ACC-CORP-987 covering 2016. In accordance with §4, the records demonstrate: (a) transaction date (2016-03-01), (b) currency and amount (PKR 750,000.00), (c) nature (wire_out), and (d) account involved (ACC-CORP-987)..."`

### 11. Human Approval
- **Legal Officer:** Required to verify and sign-off on the generated transaction reconstruction bundle before mock transmission to LEAs (Node 14).
- **Legal Officer:** Required to authorize releasing any active litigation holds (Node 18).

### 12. Database Updates
- `documents`: Updates `litigation_hold`, `retention_start`, and `retention_years`.
- `alerts`: Inserts `RETENTION_REVIEW` alert.
- `audit_logs`: Inserts holds, releases, and served inquiry records.

### 13. Notifications
- **Email:** Dispatched to Legal Counsel for manual approval verification.
- **Slack:** Message to `#legal-ops`: `ALERT [HOLD APPLIED]: Account ACC-CORP-987 placed under litigation hold. Expiry scans deactivated.`
- **Audit Logs:** Logs system and legal officer actions.

### 14. Error Handling
- **Database Connection Failure:** If PostgreSQL is offline during the nightly scan, the execution triggers an alert to a fallback notification line and halts. Standard logs are kept local in an n8n variables cache to prevent data loss.
- **Incomplete Request Parameters:** If an LEA request payload is missing the account number, the workflow rejects the webhook with an HTTP 400 response and triggers a `HOLD_REQUEST_INVALID` alert.

### 15. Synthetic Test Data
```sql
-- Setup Customer and Account
INSERT INTO customers (customer_id, full_name, cnic, customer_type, country) 
VALUES (201, 'Farah Textiles Ltd', '42101-8888888-1', 'company', 'Pakistan');

INSERT INTO accounts (account_id, customer_id, account_number, account_type, status) 
VALUES (99, 201, 'ACC-CORP-987', 'corporate', 'active');

-- Setup Transaction ( 2016 - Exceeds 10 years )
INSERT INTO transactions (transaction_id, account_id, amount, currency, txn_type, txn_date) 
VALUES (99, 99, 750000.00, 'PKR', 'wire_out', '2016-03-01');

-- Setup Document Tag
INSERT INTO documents (document_id, related_table, related_id, document_type, retention_start, retention_years, litigation_hold) 
VALUES (501, 'accounts', 99, 'cdd_file', '2016-03-01', 10, FALSE);
```
