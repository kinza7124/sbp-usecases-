-- Run once to set up the sandbox database

CREATE EXTENSION IF NOT EXISTS pg_trgm;

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
