INSERT INTO customers (customer_id, full_name, cnic, customer_type, country, is_pep, risk_rating)
VALUES (1, 'Ahmed Raza Sheikh', '42101-1234567-1', 'individual', 'Pakistan', FALSE, 'low')
ON CONFLICT (customer_id) DO NOTHING;

INSERT INTO accounts (account_id, customer_id, account_number, account_type, status)
VALUES (12, 1, 'ACC-9911', 'personal', 'active')
ON CONFLICT (account_id) DO NOTHING;

INSERT INTO watchlists (watchlist_id, list_type, entity_name, id_number, country)
VALUES (45, 'PP', 'Ahmed Raza Shaikh', '42101-1234567-1', 'Pakistan')
ON CONFLICT (watchlist_id) DO NOTHING;

-- UC11 Test Data Setup
INSERT INTO jurisdiction_list (country_name, risk_tier, counter_measure)
VALUES ('Country-X', 'normal', NULL)
ON CONFLICT (country_name) DO UPDATE SET risk_tier = 'normal', counter_measure = NULL;

-- 1. Customer
INSERT INTO customers (customer_id, full_name, cnic, customer_type, country)
VALUES (401, 'Bilal Exports (Pvt) Ltd', '42101-9999999-1', 'company', 'Pakistan')
ON CONFLICT (customer_id) DO NOTHING;

-- 2. Account linked to that customer
INSERT INTO accounts (account_id, customer_id, account_number, status)
VALUES (5001, 401, 'PK00SAND0000005001', 'active')
ON CONFLICT (account_id) DO NOTHING;

-- 3. The transaction itself — counterparty in a jurisdiction we're about to mark high-risk
INSERT INTO transactions (transaction_id, account_id, counterparty_name, counterparty_country, amount, txn_date)
VALUES (9001, 5001, 'Overseas Trading Co', 'Country-X', 750000.00, now())
ON CONFLICT (transaction_id) DO NOTHING;

-- 4. Jurisdiction list — this is what the Switch Risk Tier node reads
INSERT INTO jurisdiction_list (country_name, risk_tier, counter_measure)
VALUES ('Country-X', 'high_risk', 'EDD Required')
ON CONFLICT (country_name) DO UPDATE SET risk_tier = 'high_risk', counter_measure = 'EDD Required';

-- 5. wire_transfers row — needed by "Update Transaction - Hold/Reject" (wire_id must equal transaction_id)
INSERT INTO wire_transfers (wire_id, status)
VALUES (9001, 'pending')
ON CONFLICT (wire_id) DO NOTHING;

-- 6. Customer actually located in the high-risk country to trigger the "Fetch Impacted Customers" node
INSERT INTO customers (customer_id, full_name, cnic, customer_type, country)
VALUES (402, 'Country-X Local Branch', '99999-0000000-1', 'company', 'Country-X')
ON CONFLICT (customer_id) DO NOTHING;

-- =============================================================
-- UC7 Synthetic seed data — the exact "Roshan" scenario from the workflow
-- =============================================================

INSERT INTO customers (customer_id, full_name, cnic, customer_type, country)
VALUES (101, 'Roshan Family Welfare Trust', NULL, 'trust', 'Pakistan')
ON CONFLICT (customer_id) DO NOTHING;

INSERT INTO accounts (account_id, customer_id, account_number, account_type, status)
VALUES (1001, 101, 'ACC-NPO-321', 'ngo', 'active')
ON CONFLICT (account_number) DO NOTHING;

INSERT INTO ngo_accounts (customer_id, registered_title, advertised_title, governing_body)
VALUES (
    101,
    'Roshan Family Welfare Trust',
    'Roshan Charity Foundation',
    '[{"name": "Salman Qureshi", "cnic": "35202-1111111-1"}, {"name": "Nadia Hameed", "cnic": "42101-5555555-5"}]'::jsonb
)
ON CONFLICT DO NOTHING;

INSERT INTO pep_list (full_name, id_number, pep_category, country)
VALUES ('Salman Qureshi', '35202-1111111-1', 'domestic', 'Pakistan')
ON CONFLICT DO NOTHING;

-- A second, clean NGO for contrast/testing the "no mismatch" path
INSERT INTO customers (customer_id, full_name, cnic, customer_type, country)
VALUES (102, 'Al-Khidmat Foundation Pakistan', NULL, 'ngo', 'Pakistan')
ON CONFLICT (customer_id) DO NOTHING;

INSERT INTO accounts (account_id, customer_id, account_number, account_type, status)
VALUES (1002, 102, 'ACC-NPO-450', 'ngo', 'active')
ON CONFLICT (account_number) DO NOTHING;

INSERT INTO ngo_accounts (customer_id, registered_title, advertised_title, governing_body)
VALUES (
    102,
    'Al-Khidmat Foundation Pakistan',
    NULL,
    '[{"name": "Bilal Ahmed Sheikh", "cnic": "42101-9999999-9"}]'::jsonb
)
ON CONFLICT DO NOTHING;

-- =============================================================
-- UC10 Synthetic seed data
-- =============================================================

INSERT INTO reliance_arrangements (reliance_id, third_party_name, regulator, is_same_corporate_group, regulatory_status, country)
VALUES (1, 'Gulf Trust Bank', 'UAE Central Bank', TRUE, 'unknown', 'UAE')
ON CONFLICT (reliance_id) DO NOTHING;

-- Insert document if it doesn't exist
INSERT INTO documents (related_table, related_id, document_type, litigation_hold, file_reference)
SELECT 'reliance_arrangements', 1, 'GROUP_AML_POLICY', FALSE, 'DOC-GROUP-AML-1'
WHERE NOT EXISTS (
    SELECT 1 FROM documents WHERE related_table = 'reliance_arrangements' AND related_id = 1 AND document_type = 'GROUP_AML_POLICY'
);

INSERT INTO reliance_arrangements (reliance_id, third_party_name, regulator, is_same_corporate_group, regulatory_status, country)
VALUES (2, 'Offshore Credit Co', 'Central Bank of Country-X', FALSE, 'unknown', 'Country-X')
ON CONFLICT (reliance_id) DO NOTHING;
