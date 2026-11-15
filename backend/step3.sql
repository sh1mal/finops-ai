ALTER TABLE invoices ALTER COLUMN verified_at DROP NOT NULL;
ALTER TABLE invoices ALTER COLUMN verified_at DROP DEFAULT;
ALTER TABLE invoices ALTER COLUMN checks SET DEFAULT '[]'::jsonb;
ALTER TABLE invoices DROP CONSTRAINT IF EXISTS invoices_status_check;
ALTER TABLE invoices ADD CONSTRAINT invoices_status_check
    CHECK (status IN ('Uploaded', 'Processing', 'Needs review', 'Awaiting approval', 'Approved', 'Rejected'));

ALTER TABLE decisions ADD COLUMN IF NOT EXISTS reviewer text;
ALTER TABLE decisions ADD COLUMN IF NOT EXISTS evidence jsonb;

CREATE TABLE IF NOT EXISTS sandbox_payment_requests (
    invoice_id text PRIMARY KEY REFERENCES decisions(invoice_id),
    reference text UNIQUE NOT NULL,
    vendor text NOT NULL,
    amount_cents bigint NOT NULL CHECK (amount_cents > 0),
    currency text NOT NULL CHECK (currency = 'USD'),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS reviewer_sessions (
    token_hash text PRIMARY KEY,
    reviewer text NOT NULL,
    expires_at timestamptz NOT NULL
);
