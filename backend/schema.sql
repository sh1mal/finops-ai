CREATE TABLE IF NOT EXISTS vendors (
    id text PRIMARY KEY,
    name text NOT NULL,
    normalized_name text NOT NULL UNIQUE,
    initials text NOT NULL,
    color text NOT NULL CHECK (color IN ('violet', 'teal', 'blue'))
);

CREATE TABLE IF NOT EXISTS purchase_orders (
    number text PRIMARY KEY,
    vendor_id text NOT NULL REFERENCES vendors(id),
    amount_cents bigint NOT NULL CHECK (amount_cents > 0)
);

CREATE TABLE IF NOT EXISTS vendor_agreements (
    vendor_id text NOT NULL REFERENCES vendors(id),
    reference text NOT NULL,
    effective_from date NOT NULL,
    terms_days integer NOT NULL CHECK (terms_days BETWEEN 1 AND 365),
    PRIMARY KEY (vendor_id, effective_from)
);

CREATE TABLE IF NOT EXISTS prior_invoices (
    id text PRIMARY KEY,
    vendor_id text NOT NULL REFERENCES vendors(id),
    invoice_number text NOT NULL,
    normalized_number text NOT NULL,
    amount_cents bigint NOT NULL CHECK (amount_cents > 0),
    received_at timestamptz NOT NULL
);

CREATE INDEX IF NOT EXISTS prior_invoice_lookup ON prior_invoices (vendor_id, normalized_number);

CREATE TABLE IF NOT EXISTS invoices (
    id text PRIMARY KEY,
    invoice_number text,
    normalized_number text,
    vendor_name text,
    vendor_id text REFERENCES vendors(id),
    amount_cents bigint CHECK (amount_cents > 0),
    issued_on date,
    due_on date,
    terms_days integer CHECK (terms_days BETWEEN 1 AND 365),
    po_number text,
    received_at timestamptz NOT NULL DEFAULT now(),
    verified_at timestamptz,
    filename text NOT NULL,
    pdf bytea NOT NULL,
    status text NOT NULL,
    checks jsonb NOT NULL DEFAULT '[]'::jsonb,
    exception jsonb
);

CREATE INDEX IF NOT EXISTS invoice_duplicate_lookup ON invoices (vendor_id, normalized_number);

CREATE TABLE IF NOT EXISTS decisions (
    invoice_id text PRIMARY KEY REFERENCES invoices(id),
    status text NOT NULL CHECK (status IN ('Approved', 'Rejected')),
    note text NOT NULL CHECK (char_length(note) BETWEEN 1 AND 1000),
    decided_at timestamptz NOT NULL DEFAULT now()
);
