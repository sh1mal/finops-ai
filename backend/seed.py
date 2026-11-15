from pathlib import Path

from .database import connect
from .invoices import store_invoice
from .policies import backfill_held_citations, seed_policies
from .verification import normalize_number, normalize_vendor
from .workflow import process_invoice, saver

ROOT = Path(__file__).resolve().parent.parent
VENDORS = (
    ("V-101", "Northline Systems", "NS", "violet"),
    ("V-102", "Meridian Supply Co.", "MS", "teal"),
    ("V-103", "Cobalt Office Group", "CO", "blue"),
    ("V-104", "Harbor Freight Services", "HF", "blue"),
    ("V-105", "Juniper Facilities", "JF", "teal"),
    ("V-106", "Redwood IT Services", "RI", "violet"),
)
ORDERS = (
    ("PO-2081", "V-101", 485000, "Network installation and configuration"),
    ("PO-2074", "V-102", 1240000, "Warehouse safety equipment"),
    ("PO-2090", "V-101", 400000, "Laptop equipment"),
    ("PO-2091", "V-101", 400000, "Laptop equipment"),
    ("PO-2092", "V-101", 400000, "Laptop equipment"),
    ("PO-2100", "V-102", 1200000, "Warehouse equipment"),
    ("PO-2101", "V-102", 900000, "Warehouse equipment"),
    ("PO-2102", "V-103", 180000, "Office equipment"),
    ("PO-2069", "V-103", 127500, "Office equipment"),
    ("PO-2201", "V-104", 287500, "Regional freight"),
    ("PO-2202", "V-104", 146250, "Warehouse transfer"),
    ("PO-2203", "V-104", 95000, "Liftgate delivery"),
    ("PO-2204", "V-104", 320000, "Outbound freight"),
    ("PO-2211", "V-105", 234000, "Facilities maintenance"),
    ("PO-2212", "V-105", 182000, "Preventive maintenance"),
    ("PO-2213", "V-105", 460000, "HVAC service"),
    ("PO-2221", "V-106", 312000, "Network monitoring"),
    ("PO-2222", "V-106", 525000, "Workstation setup"),
    ("PO-2223", "V-106", 125000, "Endpoint maintenance"),
    ("PO-2224", "V-106", 272000, "Backup verification"),
)
ORDER_LINES = (
    ("PO-2090", 1, "Apple MacBook Pro 14-inch M5", 2, "each", 200000),
    ("PO-2091", 1, "Apple MacBook Pro 14-inch M5", 2, "each", 200000),
    ("PO-2092", 1, "Apple MacBook Pro 14-inch M5, silver", 1, "each", 200000),
    ("PO-2092", 2, "Apple MacBook Pro 14-inch M5, space black", 1, "each", 200000),
)
AGREEMENTS = (
    ("V-101", "Northline Vendor Agreement VA-011", 30),
    ("V-102", "Meridian Vendor Agreement VA-017", 30),
    ("V-103", "Cobalt Vendor Agreement VA-023", 30),
    ("V-104", "Harbor Vendor Agreement VA-031", 45),
    ("V-105", "Juniper Vendor Agreement VA-035", 30),
    ("V-106", "Redwood Vendor Agreement VA-042", 60),
)
AGREEMENT_START = "2026-01-01"
SAMPLES = (
    ("INV-1042", "2026-11-12T10:00:00+00:00"),
    ("INV-1041", "2026-11-10T10:00:00+00:00"),
    ("INV-1040", "2026-11-09T10:00:00+00:00"),
    *((f"INV-{number}", f"2026-11-{9 + (number - 1043) // 3:02}T{11 + (number - 1043) % 3}:00:00+00:00")
      for number in range(1043, 1055)),
)


def main():
    with connect() as conn:
        conn.execute((ROOT / "backend" / "schema.sql").read_text())
        conn.execute((ROOT / "backend" / "step3.sql").read_text())
        conn.execute((ROOT / "backend" / "step4.sql").read_text())
        conn.execute((ROOT / "backend" / "ai.sql").read_text())
        conn.execute((ROOT / "backend" / "line_matching.sql").read_text())
        conn.execute((ROOT / "backend" / "manual_review.sql").read_text())
        with conn.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO vendors VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                [(vendor_id, name, normalize_vendor(name), initials, color) for vendor_id, name, initials, color in VENDORS],
            )
            cursor.executemany(
                """INSERT INTO purchase_orders (number, vendor_id, amount_cents, description)
                   VALUES (%s, %s, %s, %s) ON CONFLICT (number) DO UPDATE
                   SET description = EXCLUDED.description WHERE purchase_orders.description IS NULL""", ORDERS,
            )
            cursor.executemany(
                """INSERT INTO purchase_order_lines (po_number, line_number, description, quantity, unit, unit_price_cents)
                   VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""", ORDER_LINES,
            )
            conn.execute(
                """UPDATE purchase_order_lines
                   SET variant_family = 'MacBook Pro M5 14', variant = CASE line_number
                       WHEN 1 THEN 'silver' WHEN 2 THEN 'space black' END
                   WHERE po_number = 'PO-2092' AND line_number IN (1, 2) AND variant_family IS NULL"""
            )
            cursor.executemany(
                "INSERT INTO vendor_agreements VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                [(vendor, f"{document} · § 4.2", AGREEMENT_START, terms) for vendor, document, terms in AGREEMENTS],
            )
        conn.execute(
            """INSERT INTO prior_invoices VALUES
               ('AP-0981', 'V-103', 'INV-1040', %s, 127500, '2026-11-04T10:00:00+00:00')
               ON CONFLICT DO NOTHING""",
            (normalize_number("INV-1040"),),
        )
        conn.execute(
            """INSERT INTO prior_invoices (id, vendor_id, invoice_number, normalized_number, amount_cents, received_at)
               VALUES ('AP-0960', 'V-102', 'MER-90', 'MER90', 820000, '2026-08-05'),
                      ('AP-0961', 'V-102', 'MER-91', 'MER91', 830000, '2026-09-05'),
                      ('AP-0962', 'V-102', 'MER-92', 'MER92', 850000, '2026-10-05')
               ON CONFLICT DO NOTHING"""
        )
        seed_policies(conn, [
            (document, "4.2", vendor, AGREEMENT_START,
             f"Payment is due {terms} calendar days from the invoice date (Net {terms}).")
            for vendor, document, terms in AGREEMENTS
        ])
        backfill_held_citations(conn)
        for invoice_id, received in SAMPLES:
            if conn.execute("SELECT 1 FROM invoices WHERE id = %s", (invoice_id,)).fetchone():
                continue
            filename = invoice_id + ".pdf"
            documents = ROOT / "public" / "invoices"
            path = documents / filename
            if not path.is_file():
                path = documents / "vendor-demo" / filename
            store_invoice(conn, invoice_id, filename, path.read_bytes(), received_at=received)
        pending = conn.execute(
            """SELECT id FROM invoices WHERE status IN ('Uploaded', 'Processing', 'Awaiting approval')
               ORDER BY received_at, id"""
        ).fetchall()
    with connect(autocommit=True) as conn:
        saver(conn).setup()
    for row in pending:
        process_invoice(row["id"])
    print("FinOps database and workflow checkpoints initialized.")


if __name__ == "__main__":
    main()
