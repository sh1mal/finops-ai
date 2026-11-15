CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS policy_clauses (
    document text NOT NULL,
    section text NOT NULL,
    vendor_id text REFERENCES vendors(id),
    effective_from date NOT NULL,
    body text NOT NULL,
    embedding vector(256) NOT NULL,
    PRIMARY KEY (document, section, effective_from)
);
