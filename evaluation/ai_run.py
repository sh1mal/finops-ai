"""Replay a small labeled vision-model corpus against the real upload and policy endpoints."""
import argparse
import base64
import json
import os
from dataclasses import dataclass
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from statistics import median
from time import perf_counter
from unittest.mock import patch
from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import make_conninfo
from PIL import Image, ImageEnhance, ImageFilter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

from backend.main import app
from backend.model import page_images
from backend.seed import main as seed
from evaluation.run import CITATION

EXPECTED_TOOLS = {
    "original Meridian": {"get_purchase_order", "get_vendor_history"},
    "alternate layout with terms mismatch": {"get_purchase_order", "get_vendor_history"},
}


@dataclass(frozen=True)
class Case:
    name: str
    pdf: bytes
    fields: dict
    exception: str | None


def fixture(number, *, layout, amount=485000, tax=0, terms=30, omit_total=False, vendor="Northline Systems", po="PO-2081"):
    output = BytesIO()
    page = Canvas(output, pagesize=(612, 792), invariant=True)
    def label(x, y, value, size=13):
        page.setFont("Helvetica", size)
        page.drawString(x, y, value)

    columns = layout == "columns"
    label(52, 740, "INVOICE", 27)
    label(52, 702, vendor, 20)
    label(52, 660, f"Invoice number: {number}")
    x = 345 if columns else 52
    label(x, 624, f"Purchase order: {po}")
    label(x, 594, "Issued: November 12, 2026")
    due = "February 10, 2027" if terms == 90 else "December 12, 2026"
    label(x, 564, f"Payment due: {due}")
    label(x, 534, f"Payment terms: Net {terms}")
    label(52, 470, "Description and line amount (USD)", 15)
    label(52, 430, f"Equipment and service                         ${(amount-tax)/100:,.2f}")
    if layout == "two-page":
        label(52, 90, "Continued on page 2")
        page.showPage()
        label(52, 740, f"Invoice {number} - page 2", 18)
    if tax:
        label(x, 360, f"Sales tax: ${tax/100:,.2f}")
    if not omit_total:
        label(x, 318, f"Amount due: USD {amount/100:,.2f}", 15)
    page.showPage()
    page.save()
    pdf = output.getvalue()
    if layout == "scan":
        original = Image.open(BytesIO(base64.b64decode(page_images(pdf)[0]))).convert("L")
        softened = ImageEnhance.Contrast(original.filter(ImageFilter.GaussianBlur(0.65))).enhance(0.82)
        output = BytesIO()
        page = Canvas(output, pagesize=(612, 792), invariant=True)
        page.drawImage(ImageReader(softened), 0, 0, width=612, height=792)
        page.showPage()
        page.save()
        pdf = output.getvalue()
    return pdf


def fields(number, vendor="Northline Systems", po="PO-2081", amount=485000, terms=30,
           issued="2026-11-12", due="2026-12-12"):
    return dict(number=number, vendor=vendor, po=po, amount=amount, terms=terms, issued=issued, due=due)


def dataset():
    samples = Path("public/invoices")
    yield Case("original Cobalt", (samples / "INV-1040.pdf").read_bytes(),
               fields("INV-1040", "Cobalt Office Group", "PO-2069", 127500, 30, "2026-11-09", "2026-12-09"),
               "Possible duplicate")
    yield Case("original Meridian", (samples / "INV-1041.pdf").read_bytes(),
               fields("INV-1041", "Meridian Supply Co.", "PO-2074", 1240000, 90, "2026-11-10", "2027-02-08"),
               "Payment terms mismatch")
    for number, layout, tax in (("QA-101", "stacked", 0), ("QA-102", "columns", 35000),
                                ("QA-103", "two-page", 35000), ("QA-104", "scan", 0)):
        yield Case(layout, fixture(number, layout=layout, tax=tax), fields(number), None)
    yield Case("missing declared total", fixture("QA-105", layout="columns", omit_total=True), {},
               "Manual extraction needed")
    yield Case("alternate layout with terms mismatch",
               fixture("QA-106", layout="columns", vendor="Meridian Supply Co.", po="PO-2074", amount=1240000, terms=90),
               fields("QA-106", "Meridian Supply Co.", "PO-2074", 1240000, 90, due="2027-02-10"),
               "Payment terms mismatch")


WORKFLOW_CASES = (
    ("line-matching/matched", "LINE-101", "PO lines reconciled", True, None),
    ("line-matching/quantity-price-mismatch", "LINE-102", "PO lines need review", False, "PO lines need review"),
    ("line-matching/ambiguous", "LINE-103", "PO lines need review", False, "PO lines need review"),
    ("risk/unusual", "RISK-101", "Unusual invoice amount", False, "Unusual invoice amount"),
    ("risk/normal", "RISK-102", "Invoice amount within history threshold", True, None),
    ("risk/insufficient-history", "RISK-103", "Insufficient vendor history", True, None),
)


@dataclass(frozen=True)
class BoundaryCase:
    number: str
    category: str
    label: str
    order: str
    vendor: str
    total: str
    items: tuple[tuple[str, int, str], ...]
    expected_hold: bool


def boundary_cases():
    northline = "Northline Systems"
    meridian = "Meridian Supply Co."
    yield BoundaryCase("QA-L201", "line", "reordered descriptions with explicit variants", "PO-2092",
                       northline, "4,000.00", (("Space black - Apple MBP M5 14 inch", 1, "2,000.00"),
                                             ("Silver - Apple MBP M5 14 inch", 1, "2,000.00")), False)
    yield BoundaryCase("QA-L202", "line", "abbreviated equivalent item", "PO-2090",
                       northline, "4,000.00", (("Apple MBP M5 14-inch notebook", 2, "2,000.00"),), False)
    yield BoundaryCase("QA-L203", "line", "same total with different quantity and price", "PO-2090",
                       northline, "4,000.00", (("Apple MacBook Pro 14-inch M5", 4, "1,000.00"),), True)
    yield BoundaryCase("QA-L204", "line", "missing variant with two plausible PO lines", "PO-2092",
                       northline, "4,000.00", (("Apple MacBook Pro 14-inch M5", 1, "2,000.00"),
                                             ("Apple MacBook Pro 14-inch M5", 1, "2,000.00")), True)
    for number, order, amount, label, hold in (
        ("QA-R201", "PO-2190", "11,666.66", "last cent below threshold", False),
        ("QA-R202", "PO-2191", "11,666.67", "first cent at or above threshold", True),
        ("QA-R203", "PO-2192", "11,666.68", "one cent above first flagged amount", True),
    ):
        yield BoundaryCase(number, "risk", label, order, meridian, amount,
                           (("Warehouse equipment", 1, amount),), hold)


def statement_pdf(case):
    output = BytesIO()
    page = Canvas(output, pagesize=(612, 792), invariant=True)
    page.setFont("Helvetica-Bold", 22)
    page.drawString(44, 735, case.vendor)
    page.drawRightString(568, 735, "TAX INVOICE")
    page.setFont("Helvetica", 12)
    page.drawString(44, 681, f"Purchase order: {case.order}")
    page.drawString(44, 655, "Payment terms: Net 30")
    page.drawRightString(568, 681, f"Invoice number: {case.number}")
    page.drawRightString(568, 655, "Invoice date: November 12, 2026")
    page.drawRightString(568, 629, "Due date: December 12, 2026")
    page.line(44, 600, 568, 600)
    for x, label in ((44, "Description"), (365, "Qty"), (399, "Unit"), (439, "Unit price"), (522, "Amount")):
        page.drawString(x, 577, label)
    for index, (description, quantity, unit_price) in enumerate(case.items):
        y = 543 - index * 30
        page.drawString(44, y, description)
        page.drawString(369, y, str(quantity))
        page.drawString(399, y, "each")
        page.drawRightString(493, y, f"${unit_price}")
        line_total = quantity * Decimal(unit_price.replace(",", ""))
        page.drawRightString(568, y, f"${line_total:,.2f}")
    page.line(44, 475, 568, 475)
    page.setFont("Helvetica-Bold", 15)
    page.drawRightString(568, 443, f"Invoice total: USD {case.total}")
    page.setFont("Helvetica", 10)
    page.drawString(44, 364, "Remittance reference")
    page.drawString(44, 347, case.number)
    page.showPage()
    page.save()
    return output.getvalue()


def run_boundary_cases(dsn):
    cases = list(boundary_cases())
    with psycopg.connect(dsn) as conn, conn.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO purchase_orders (number, vendor_id, amount_cents, description) VALUES (%s, 'V-102', %s, 'Boundary evaluation')",
            [(case.order, int(case.total.replace(",", "").replace(".", ""))) for case in cases if case.category == "risk"],
        )
    rows = []
    with TestClient(app) as client:
        for case in cases:
            started = perf_counter()
            response = client.post("/invoices", files={"file": (f"{case.number}.pdf", statement_pdf(case), "application/pdf")},
                                   headers={"X-FinOps-Request": "upload"})
            response.raise_for_status()
            invoice = response.json()
            titles = ("PO lines reconciled", "PO lines need review") if case.category == "line" else (
                "Invoice amount within history threshold", "Unusual invoice amount",
            )
            check = next((item for item in invoice["checks"] if item["title"] in titles), None)
            expected_exception = titles[1] if case.expected_hold else None
            expected_status = "Needs review" if case.expected_hold else "Awaiting approval"
            rows.append({
                "invoice": case.number, "category": case.category, "label": case.label,
                "expected_hold": case.expected_hold, "observed_hold": None if check is None else not check["passed"],
                "expected_exception": expected_exception, "exception": (invoice["exception"] or {}).get("title"),
                "expected_status": expected_status, "status": invoice["status"], "check": check,
                "seconds": round(perf_counter() - started, 2),
            })
            print(case.number, case.label, invoice["status"], (check or {}).get("title"), flush=True)
    return rows


def summarize_boundaries(cases):
    counts = {}
    for category in ("line", "risk"):
        rows = [row for row in cases if row["category"] == category]
        counts[category] = {
            "true_holds": sum(row["expected_hold"] and row["observed_hold"] is True for row in rows),
            "true_passes": sum(not row["expected_hold"] and row["observed_hold"] is False for row in rows),
            "false_holds": sum(not row["expected_hold"] and row["observed_hold"] is True for row in rows),
            "missed_holds": sum(row["expected_hold"] and row["observed_hold"] is False for row in rows),
            "checks_not_run": sum(row["observed_hold"] is None for row in rows),
        }
    return {
        "documents": len(cases), "routes_matched": sum(row["status"] == row["expected_status"] for row in cases),
        "exceptions_matched": sum(row["exception"] == row["expected_exception"] for row in cases),
        "checks": counts,
    }


def run_workflow_cases():
    rows = []
    with TestClient(app) as client:
        for file, number, title, passed, exception in WORKFLOW_CASES:
            path = Path("public/invoices") / f"{file}.pdf"
            started = perf_counter()
            response = client.post("/invoices", files={"file": (path.name, path.read_bytes(), "application/pdf")},
                                   headers={"X-FinOps-Request": "upload"})
            response.raise_for_status()
            invoice = response.json()
            expected_status = "Needs review" if exception else "Awaiting approval"
            check = next((item for item in invoice["checks"] if item["title"] == title), None)
            rows.append({
                "invoice": number, "actual_number": invoice["number"],
                "expected_status": expected_status, "status": invoice["status"],
                "expected_exception": exception, "exception": (invoice["exception"] or {}).get("title"),
                "expected_check": title, "expected_check_passed": passed, "check": check,
                "seconds": round(perf_counter() - started, 2),
            })
            print(number, invoice["status"], bool(check), flush=True)
    return rows


def run():
    results = []
    questions = []
    with TestClient(app) as client:
        for index, case in enumerate(dataset()):
            start = perf_counter()
            response = client.post("/invoices", files={"file": (f"ai-evaluation-{index}.pdf", case.pdf, "application/pdf")},
                                   headers={"X-FinOps-Request": "upload"})
            elapsed = round(perf_counter() - start, 2)
            row = response.json()
            exception = row.get("exception") or {}
            investigation = row.get("aiInvestigation") or {}
            expected_status = "Needs review" if case.exception else "Awaiting approval"
            result = {
                "case": case.name, "seconds": elapsed, "http_status": response.status_code,
                "expected_status": expected_status, "status": row.get("status"),
                "expected_exception": case.exception, "exception": exception.get("title"),
                "fields": {key: {"expected": value, "actual": row.get(key)} for key, value in case.fields.items()},
                "citation_expected": case.exception == "Payment terms mismatch",
                "citation_match": exception.get("citation") == CITATION,
                "model_selected_tools": [call["tool"] for call in investigation.get("calls", [])],
                "investigation_state": investigation.get("state"),
                "extraction": row.get("aiExtraction"),
                "findings": investigation.get("findings", []),
                "investigation_usage": investigation.get("usage"),
            }
            results.append(result)
            print(case.name, elapsed, result["status"], result["exception"], result["model_selected_tools"], flush=True)
            if case.name == "original Meridian" and response.is_success:
                for question, expect_citation in [
                    ("hello", False), ("What is the capital of France?", False),
                    ("Can this invoice use Net 90 terms?", True),
                ]:
                    answer = client.post(f'/invoices/{row["id"]}/policy-question', json={"question": question})
                    data = answer.json()
                    questions.append({"question": question, "expected_citation": expect_citation,
                                      "http_status": answer.status_code, "response": data,
                                      "passed": answer.is_success and (data.get("citation") == CITATION if expect_citation
                                                                    else data.get("citation") is None)})
    return results, questions


def summarize(cases, questions):
    compared = [field for row in cases for field in row["fields"].values()]
    expected_calls = [(row, EXPECTED_TOOLS[row["case"]]) for row in cases if row["case"] in EXPECTED_TOOLS]
    return {
        "documents": len(cases), "fields_matched": sum(field["actual"] == field["expected"] for field in compared),
        "fields_expected": len(compared), "routes_matched": sum(row["status"] == row["expected_status"] for row in cases),
        "exceptions_matched": sum(row["exception"] == row["expected_exception"] for row in cases),
        "citations_matched": sum(row["citation_match"] for row in cases if row["citation_expected"]),
        "citations_expected": sum(row["citation_expected"] for row in cases),
        "expected_tool_calls_observed": sum(len(names.intersection(row["model_selected_tools"])) for row, names in expected_calls),
        "expected_tool_calls": sum(len(names) for _, names in expected_calls),
        "questions_passed": sum(row["passed"] for row in questions), "questions_tested": len(questions),
        "median_seconds": round(median(row["seconds"] for row in cases), 2),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    suite = parser.add_mutually_exclusive_group()
    suite.add_argument("--workflow", action="store_true", help="Replay the six line-matching and vendor-risk demo PDFs")
    suite.add_argument("--boundaries", action="store_true", help="Generate fresh line-matching and threshold fixtures")
    args = parser.parse_args()
    dsn = os.environ.get("FINOPS_TEST_DATABASE_URL")
    model = os.environ.get("FINOPS_OLLAMA_MODEL")
    if not dsn or not model:
        parser.error("Set FINOPS_TEST_DATABASE_URL and FINOPS_OLLAMA_MODEL.")
    schema = sql.Identifier("finops_ai_eval_" + uuid4().hex)
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(schema))
        try:
            isolated = make_conninfo(dsn, options=f"-c search_path={schema.as_string()},public")
            with patch.dict(os.environ, {"FINOPS_DATABASE_URL": isolated}):
                seed()
                if args.boundaries:
                    cases, questions = run_boundary_cases(isolated), []
                elif args.workflow:
                    cases, questions = run_workflow_cases(), []
                else:
                    cases, questions = run()
        finally:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(schema))
    if args.boundaries:
        summary = summarize_boundaries(cases)
    elif args.workflow:
        summary = {
            "documents": len(cases),
            "routes_matched": sum(row["status"] == row["expected_status"] for row in cases),
            "exceptions_matched": sum(row["exception"] == row["expected_exception"] for row in cases),
            "checks_matched": sum(row["check"] is not None and row["check"]["passed"] == row["expected_check_passed"] for row in cases),
            "median_seconds": round(median(row["seconds"] for row in cases), 2),
        }
    else:
        summary = summarize(cases, questions)
    args.output.write_text(json.dumps({"model": model, "summary": summary, "cases": cases, "questions": questions}, indent=2) + "\n")
    print(summary)


if __name__ == "__main__":
    main()
