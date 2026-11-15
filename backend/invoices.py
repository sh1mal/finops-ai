INVOICE_QUERY = """
    SELECT i.id, coalesce(i.invoice_number, upper(left(i.id, 12))) AS number,
           coalesce(i.vendor_name, 'Unassigned vendor') AS vendor,
           coalesce(v.initials, '—') AS initials, coalesce(v.color, 'gray') AS color,
           i.amount_cents AS amount, i.received_at AS received, i.due_on AS due,
           i.issued_on AS issued, i.terms_days AS terms, i.po_number AS po,
           i.ai_extraction AS "aiExtraction", i.ai_investigation AS "aiInvestigation",
           i.manual_correction AS "manualCorrection",
           i.vendor_id AS "vendorId", i.filename, i.status, i.checks, i.exception, i.verified_at AS "verifiedAt",
           '/api/invoices/' || i.id || '/pdf' AS document,
           (d.invoice_id IS NOT NULL AND i.status = 'Awaiting approval') AS "decisionPending",
           CASE WHEN i.status IN ('Approved', 'Rejected') AND d.invoice_id IS NOT NULL THEN
               jsonb_build_object('status', d.status, 'note', d.note, 'at', d.decided_at,
                                  'reviewer', d.reviewer, 'evidence', d.evidence)
           END AS decision,
           CASE WHEN p.invoice_id IS NOT NULL THEN
               jsonb_build_object('reference', p.reference, 'amount', p.amount_cents,
                                  'currency', p.currency, 'at', p.created_at)
           END AS "paymentRequest"
    FROM invoices i
    LEFT JOIN vendors v ON i.vendor_id = v.id
    LEFT JOIN decisions d ON i.id = d.invoice_id
    LEFT JOIN sandbox_payment_requests p ON i.id = p.invoice_id
"""


def get_invoice(conn, invoice_id):
    return conn.execute(INVOICE_QUERY + " WHERE i.id = %s", (invoice_id,)).fetchone()


def store_invoice(conn, invoice_id, filename, pdf, *, received_at=None):
    conn.execute(
        """INSERT INTO invoices (id, filename, pdf, received_at, status)
           VALUES (%s, %s, %s, coalesce(%s, now()), 'Uploaded') ON CONFLICT (id) DO NOTHING""",
        (invoice_id, filename, pdf, received_at),
    )
