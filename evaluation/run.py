"""Generate labeled synthetic PDFs and replay them through the FastAPI upload endpoint."""
import argparse
import json
import os
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from io import BytesIO
from math import ceil
from pathlib import Path
from statistics import median
from time import perf_counter
from unittest.mock import patch
from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import make_conninfo
from reportlab.pdfgen.canvas import Canvas

from backend.extraction import read_invoice
from backend.main import app
from backend.seed import main as seed

CITATION = {
    "document": "Meridian Vendor Agreement VA-017",
    "section": "4.2",
    "effectiveFrom": "2026-01-01",
    "quote": "Payment is due 30 calendar days from the invoice date (Net 30).",
}
SCENARIOS = {
    "clean": None,
    "duplicate": "Possible duplicate",
    "terms": "Payment terms mismatch",
    "missing_po": "Purchase order mismatch",
    "wrong_po_vendor": "Purchase order mismatch",
    "total": "Invoice total mismatch",
    "unknown_vendor": "Vendor not found",
    "missing_terms": "Manual extraction needed",
    "blank": "Manual extraction needed",
}


@dataclass(frozen=True)
class Case:
    group: str
    expected_fields: dict | None
    pdf: bytes


def render_pdf(fields, group):
    output = BytesIO()
    page = Canvas(output, pagesize=(612, 792), invariant=True)
    if group != "blank":
        page.setFont("Helvetica-Bold", 22)
        page.drawString(48, 730, fields["vendor"])
        page.setFont("Helvetica", 12)
        page.drawString(48, 700, f'Invoice {fields["number"]}')
        labels = [
            ("Purchase order", fields["po"]),
            ("Issued", fields["issued"].strftime("%b %d, %Y")),
            ("Due", fields["due"].strftime("%b %d, %Y")),
        ]
        if group != "missing_terms":
            labels.append(("Terms", f'Net {fields["terms"]}'))
        for index, (label, value) in enumerate(labels):
            y = 625 - index * 24
            page.drawString(330, y, label)
            page.drawRightString(555, y, value)
        page.drawString(58, 486, "Description")
        page.drawRightString(555, 486, "Amount (USD)")
        line = fields["line_total"]
        items = (line // 3, line - line // 3)
        for index, amount in enumerate(items):
            y = 453 - index * 24
            page.drawString(58, y, f"Service {index + 1}")
            page.drawRightString(555, y, f"${amount / 100:,.2f}")
        page.drawString(330, 390, "Total due")
        page.drawRightString(555, 390, f'${fields["amount"] / 100:,.2f}')
        page.drawString(48, 55, "Synthetic evaluation invoice")
    page.showPage()
    page.save()
    return output.getvalue()


def dataset():
    for index in range(12):
        issued = date(2026, 10, 1) + timedelta(days=index)
        for group in SCENARIOS:
            terms = 90 if group == "terms" else 30
            vendor = {"terms": "Meridian Supply Co.", "unknown_vendor": "Unlisted Supplies"}.get(group, "Northline Systems")
            po = {"terms": "PO-2074", "missing_po": "PO-9999", "wrong_po_vendor": "PO-2074"}.get(group, "PO-2081")
            amount = 1240000 if group == "terms" else 485000
            number = f"EVAL-{group.replace('_', '-')}-{index:02}"
            if group == "clean":
                number = f"EVAL-PAIR-{index:02}"
            if group == "duplicate":
                number = f"eval pair {index:02}"
            fields = dict(vendor=vendor, number=number, po=po, issued=issued,
                          due=issued + timedelta(days=terms), terms=terms, amount=amount,
                          line_total=amount - (100 if group == "total" else 0))
            expected = None if group in {"blank", "missing_terms"} else fields
            yield Case(group, expected, render_pdf(fields, group))


def evaluate():
    rows = []
    with TestClient(app) as client:
        for index, case in enumerate(dataset()):
            expected = case.expected_fields
            extracted = read_invoice(case.pdf)
            actual = vars(extracted) if extracted else {}
            field_matches = sum(actual.get(key) == value for key, value in (expected or {}).items())
            started = perf_counter()
            response = client.post("/invoices", files={"file": (f"evaluation-{index:03}.pdf", case.pdf, "application/pdf")},
                                   headers={"X-FinOps-Request": "upload"})
            elapsed = round((perf_counter() - started) * 1000, 2)
            response.raise_for_status()
            result = response.json()
            exception = result["exception"] or {}
            expected_exception = SCENARIOS[case.group]
            expected_status = "Needs review" if expected_exception else "Awaiting approval"
            rows.append({
                "case": index, "group": case.group, "expected_status": expected_status,
                "actual_status": result["status"], "expected_exception": expected_exception,
                "actual_exception": exception.get("title"), "citation": exception.get("citation"),
                "fields_matched": field_matches, "fields_expected": len(expected or {}),
                "manual_extraction": expected is None and extracted is None,
                "processing_ms": elapsed,
            })
    return rows


def summarize(rows):
    counts = Counter(row["group"] for row in rows)
    latency = sorted(row["processing_ms"] for row in rows)
    duplicate = "Possible duplicate"
    true_positive = sum(row["expected_exception"] == row["actual_exception"] == duplicate for row in rows)
    return {
        "cases": len(rows), "cases_by_group": dict(counts),
        "fields_matched": sum(row["fields_matched"] for row in rows),
        "fields_expected": sum(row["fields_expected"] for row in rows),
        "manual_extraction_count": sum(row["manual_extraction"] for row in rows),
        "status_matches": sum(row["expected_status"] == row["actual_status"] for row in rows),
        "exception_matches": sum(row["expected_exception"] == row["actual_exception"] for row in rows),
        "citation_matches": sum(row["citation"] == CITATION for row in rows if row["group"] == "terms"),
        "citations_expected": counts["terms"],
        "unexpected_citations": sum(row["citation"] is not None for row in rows if row["group"] != "terms"),
        "duplicate_true_positives": true_positive,
        "duplicate_flags": sum(row["actual_exception"] == duplicate for row in rows),
        "duplicates_expected": counts["duplicate"],
        "processing_p50_ms": median(latency),
        "processing_p95_ms": latency[ceil(0.95 * len(latency)) - 1],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    dsn = os.environ.get("FINOPS_TEST_DATABASE_URL")
    if not dsn:
        parser.error("Set FINOPS_TEST_DATABASE_URL to a PostgreSQL database with pgvector available.")
    schema = sql.Identifier("finops_eval_" + uuid4().hex)
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(schema))
        try:
            isolated_dsn = make_conninfo(dsn, options=f"-c search_path={schema.as_string()},public")
            with patch.dict(os.environ, {"FINOPS_DATABASE_URL": isolated_dsn, "FINOPS_OLLAMA_MODEL": ""}):
                seed()
                rows = evaluate()
        finally:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(schema))
    summary = summarize(rows)
    args.output.write_text(json.dumps({"summary": summary, "cases": rows}, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
