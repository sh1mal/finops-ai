import re
import unicodedata
from datetime import timedelta
from decimal import Decimal

from .mcp_client import lookup_records
from .policies import payment_terms_citation


def normalize_vendor(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def normalize_number(value):
    return re.sub(r"[\s/._-]+", "", unicodedata.normalize("NFKC", value).upper())


def money(cents):
    dollars, remainder = divmod(cents, 100)
    return f"${dollars:,}.{remainder:02d}"


def vendor_amount_check(amount, history, issued):
    count = history["count"]
    total = history["total_cents"]
    source = f"Vendor invoice history before {issued} UTC"
    if count < 3:
        return {
            "title": "Insufficient vendor history", "passed": True, "skipped": True,
            "detail": f"Found {count} prior invoices. The amount check requires at least 3.",
            "source": source, "lookup": "get_vendor_history",
        }

    # Compare against the exact mean. Round only for display.
    flagged = amount * count * 100 >= total * 140
    difference = (Decimal(amount * count - total) * 100) / total
    direction = "above" if difference >= 0 else "below"
    return {
        "title": "Unusual invoice amount" if flagged else "Invoice amount within history threshold",
        "passed": not flagged, "source": source, "lookup": "get_vendor_history",
        "detail": (
            f"{money(amount)} is {abs(difference):.2f}% {direction} the average of "
            f"{money(history['mean_cents'])} across {count} prior invoices. "
            "Review threshold: 40% or more above the average."
        ),
    }


def verify(conn, fields, invoice_id):
    if fields is None:
        return None, [], {
            "title": "Manual extraction needed",
            "detail": "The invoice fields could not be read using the supported one-page USD text layout.",
            "source": "Uploaded PDF",
        }

    checks = []
    exception = None

    def check(label, passed, title, detail, source, *, lookup=None, citation=None):
        nonlocal exception
        checks.append({"title": label if passed else title, "passed": passed, "detail": detail, "source": source})
        if lookup:
            checks[-1]["lookup"] = lookup
        if citation:
            checks[-1]["citation"] = citation
        if not passed and exception is None:
            exception = {"title": title, "detail": detail, "source": source}
            if citation:
                exception["citation"] = citation

    # Hold this across the MCP history read and the caller's write of the verified invoice.
    identity = f"verify-vendor:{normalize_vendor(fields.vendor)}"
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (identity,))
    records = lookup_records(fields, invoice_id)
    vendor = records["vendor"]
    vendor_id = vendor["id"] if vendor else None
    check("Vendor verified", vendor is not None, "Vendor not found",
          f"{fields.vendor} matches vendor {vendor_id}." if vendor else f"No vendor record matches {fields.vendor}.",
          "Vendor register", lookup="get_vendor")

    po = records["order"]
    po_matches = po is not None and vendor_id == po["vendor_id"]
    if po_matches:
        po_detail = f"{fields.po} belongs to {fields.vendor}."
    elif po:
        po_detail = f"{fields.po} is assigned to vendor {po['vendor_id']}. The invoice vendor could not be matched to that record."
    else:
        po_detail = f"Purchase order {fields.po} was not found."
    check("Purchase order matched", po_matches, "Purchase order mismatch", po_detail, fields.po, lookup="get_purchase_order")

    total_matches = fields.amount == fields.line_total
    check("Invoice total verified", total_matches, "Invoice total mismatch",
          f"Invoice total {money(fields.amount)}. Sum of line items {money(fields.line_total)}.",
          "Invoice PDF")
    if po_matches:
        check("Amount verified", fields.amount == po["amount_cents"], "Purchase order amount mismatch",
              f"Invoice total {money(fields.amount)}. Purchase order total {money(po['amount_cents'])}.",
              fields.po)

    if vendor:
        agreement = records["agreement"]
        if agreement:
            terms = agreement["terms_days"]
            citation = payment_terms_citation(conn, vendor_id, agreement, terms) if fields.terms != terms else None
            check("Payment terms matched", fields.terms == terms, "Payment terms mismatch",
                  f"The invoice requests Net {fields.terms}. The vendor agreement specifies Net {terms}.",
                  agreement["reference"], lookup="get_vendor_agreement", citation=citation)
        else:
            check("", False, "Vendor agreement not found",
                  f"No agreement covers the invoice date {fields.issued}.", "Vendor agreements", lookup="get_vendor_agreement")

    due = fields.issued + timedelta(days=fields.terms) if fields.issued.year < 9999 else None
    check("Due date verified", fields.due == due, "Due date mismatch",
          f"Invoice due date {fields.due}. Net {fields.terms} from the issue date gives {due or 'an invalid date'}.",
          "Invoice PDF")

    if vendor:
        duplicate = records["prior"]
        detail = "No prior invoice matches this vendor and invoice number."
        source = "Invoice history"
        if duplicate:
            detail = f"{duplicate['id']} has the same vendor and normalized invoice number."
            source = f"Prior invoice {duplicate['id']} · {duplicate['invoice_number']} · {money(duplicate['amount_cents'])}"
        check("No duplicate found", duplicate is None, "Possible duplicate", detail, source, lookup="find_prior_invoice")

        if duplicate is None:
            near = records["near"]
            detail = "No likely invoice number typo matched the vendor, amount, and date window."
            source = "Invoice history"
            if near:
                detail = (
                    f"{fields.number} resembles {near['invoice_number']}. Both invoices have the same vendor "
                    f"and total of {money(fields.amount)}. This invoice was issued on {fields.issued}. "
                    f"The matching record was {near['date_source']} on {near['date']}."
                )
                source = f"Prior invoice {near['id']} · {near['invoice_number']}"
            check("No near duplicate found", near is None, "Possible near duplicate",
                  detail, source, lookup="find_near_duplicate")

        risk = vendor_amount_check(fields.amount, records["history"], fields.issued)
        checks.append(risk)
        if not risk["passed"] and exception is None:
            exception = {key: risk[key] for key in ("title", "detail", "source")}

    return vendor_id, checks, exception
