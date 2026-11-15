from datetime import date
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .database import connect
from .duplicate_numbers import likely_typo
from .verification import normalize_number, normalize_vendor

server = FastMCP("FinOps records", log_level="WARNING")
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
LookupText = Annotated[str, Field(min_length=1, max_length=200)]


def read_record(query, params):
    with connect() as conn:
        conn.read_only = True
        return conn.execute(query, params).fetchone()


@server.tool(annotations=READ_ONLY, structured_output=True)
def get_vendor(name: LookupText) -> dict[str, dict[str, str | int | None] | None]:
    """Find a vendor by its normalized name."""
    return {"record": read_record(
        "SELECT id, name FROM vendors WHERE normalized_name = %s", (normalize_vendor(name),),
    )}


@server.tool(annotations=READ_ONLY, structured_output=True)
def get_purchase_order(number: LookupText) -> dict[str, dict[str, str | int | None] | None]:
    """Find a purchase order and its vendor and total in cents."""
    return {"record": read_record(
        "SELECT number, vendor_id, amount_cents, description FROM purchase_orders WHERE number = %s", (number,),
    )}


@server.tool(annotations=READ_ONLY, structured_output=True)
def find_prior_invoice(vendor_id: LookupText, number: LookupText, exclude_id: LookupText) -> dict[str, dict[str, str | int | None] | None]:
    """Find the earliest matching invoice by vendor and normalized number, excluding the current invoice."""
    return {"record": read_record(
        """SELECT id, invoice_number, amount_cents FROM (
               SELECT id, invoice_number, amount_cents, received_at FROM prior_invoices
               WHERE vendor_id = %(vendor)s AND normalized_number = %(number)s
               UNION ALL
               SELECT id, invoice_number, amount_cents, received_at FROM invoices
               WHERE vendor_id = %(vendor)s AND normalized_number = %(number)s AND id <> %(id)s
           ) AS matches ORDER BY received_at, id LIMIT 1""",
        {"vendor": vendor_id, "number": normalize_number(number), "id": exclude_id},
    )}


@server.tool(annotations=READ_ONLY, structured_output=True)
def find_near_duplicate(
    vendor_id: LookupText, number: LookupText, exclude_id: LookupText, amount_cents: int, issued_on: date,
) -> dict[str, dict[str, str | int] | None]:
    """Find a likely number typo with the same vendor, amount, and a date within seven days."""
    with connect() as conn:
        conn.read_only = True
        rows = conn.execute(
            """SELECT id, invoice_number, normalized_number, candidate_date::text AS date, date_source
               FROM (
                   SELECT id, invoice_number, normalized_number,
                          (received_at AT TIME ZONE 'UTC')::date AS candidate_date,
                          'received' AS date_source
                   FROM prior_invoices WHERE vendor_id = %(vendor)s AND amount_cents = %(amount)s
                   UNION ALL
                   SELECT id, invoice_number, normalized_number, issued_on, 'issued'
                   FROM invoices WHERE vendor_id = %(vendor)s AND amount_cents = %(amount)s
                       AND id <> %(id)s
               ) AS candidates
               WHERE candidate_date BETWEEN %(issued)s - 7 AND %(issued)s + 7
               ORDER BY abs(candidate_date - %(issued)s), id""",
            {"vendor": vendor_id, "amount": amount_cents, "id": exclude_id, "issued": issued_on},
        )
        normalized = normalize_number(number)
        for row in rows:
            if likely_typo(normalized, row.pop("normalized_number")):
                return {"record": row}
    return {"record": None}


@server.tool(annotations=READ_ONLY, structured_output=True)
def get_vendor_agreement(vendor_id: LookupText, issued_on: date) -> dict[str, dict[str, str | int | None] | None]:
    """Find the agreement in effect for the vendor on the invoice date."""
    return {"record": read_record(
        """SELECT reference, terms_days, effective_from::text FROM vendor_agreements
           WHERE vendor_id = %s AND effective_from <= %s
           ORDER BY effective_from DESC LIMIT 1""",
        (vendor_id, issued_on),
    )}


@server.tool(annotations=READ_ONLY, structured_output=True)
def get_vendor_history(vendor_id: LookupText, before: date) -> dict[str, dict[str, int | None] | None]:
    """Summarize historical USD totals before the invoice date."""
    return {"record": read_record(
        """SELECT count(*) AS count, round(avg(amount_cents))::bigint AS mean_cents,
                  coalesce(sum(amount_cents), 0) AS total_cents
           FROM prior_invoices WHERE vendor_id = %s AND received_at < (%s::date::timestamp AT TIME ZONE 'UTC')""",
        (vendor_id, before),
    )}


if __name__ == "__main__":
    server.run(transport="stdio")
