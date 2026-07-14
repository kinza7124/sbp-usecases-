INSERT INTO customers (customer_id, full_name, cnic, customer_type, country, is_pep, risk_rating)
VALUES (1, 'Ahmed Raza Sheikh', '42101-1234567-1', 'individual', 'Pakistan', FALSE, 'low');

INSERT INTO accounts (account_id, customer_id, account_number, account_type, status)
VALUES (12, 1, 'ACC-9911', 'personal', 'active');

INSERT INTO watchlists (watchlist_id, list_type, entity_name, id_number, country)
VALUES (45, 'PP', 'Ahmed Raza Shaikh', '42101-1234567-1', 'Pakistan');
