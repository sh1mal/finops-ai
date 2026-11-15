from contextlib import contextmanager
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from hashlib import sha256
from typing import Literal, TypedDict

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from psycopg.types.json import Jsonb

from .database import connect
from .extraction import ExtractedInvoice, read_invoice
from .invoices import get_invoice
from .line_matching import reconcile
from .investigation import investigate
from .model import configured_model, extract_with_model, extraction_model, gemini_verification_model
from .verification import normalize_number, verify


class InvoiceNotFound(Exception):
    pass


class DecisionConflict(Exception):
    pass


class WorkflowState(TypedDict, total=False):
    invoice_id: str
    fields: dict | None
    ai_extraction: dict | None
    manual_correction: dict
    evidence: dict
    decision: dict


def verification_model(trace):
    if model := gemini_verification_model():
        return model
    if trace.get("provider") == "gemini":
        return configured_model()
    return trace.get("model")


def extract(state: WorkflowState):
    with connect() as conn:
        row = conn.execute("SELECT pdf, verified_at, manual_correction, ai_extraction FROM invoices WHERE id = %s", (state["invoice_id"],)).fetchone()
    if row["verified_at"] is not None:
        return {"fields": None}
    if correction := row["manual_correction"]:
        return {
            "fields": correction["fields"], "ai_extraction": row["ai_extraction"],
            "manual_correction": correction,
        }
    model = extraction_model() if state["invoice_id"].startswith("UPL-") else ""
    fields, trace = extract_with_model(row["pdf"], model) if model else (read_invoice(row["pdf"]), None)
    result = {"fields": None}
    if trace is not None:
        result["ai_extraction"] = trace
    if fields is not None:
        result["fields"] = {**asdict(fields), "issued": fields.issued.isoformat(), "due": fields.due.isoformat()}
    return result


def validate(state: WorkflowState):
    fields = state["fields"]
    if fields is None or not normalize_number(fields["number"]):
        return {"fields": None}
    return {"fields": fields}


def verify_invoice(state: WorkflowState):
    invoice_id = state["invoice_id"]
    with connect() as conn:
        row = conn.execute("SELECT pdf, verified_at FROM invoices WHERE id = %s FOR UPDATE", (invoice_id,)).fetchone()
        if row["verified_at"] is None:
            fields = None
            if data := state["fields"]:
                fields = ExtractedInvoice(**{
                    **data, "issued": date.fromisoformat(data["issued"]), "due": date.fromisoformat(data["due"]),
                })
            vendor_id, checks, exception = verify(conn, fields, invoice_id)
            trace = state.get("ai_extraction")
            if fields is not None and vendor_id is not None:
                po_lines = conn.execute(
                    """SELECT line_number, l.description, quantity, unit, unit_price_cents, variant_family, variant
                       FROM purchase_order_lines l JOIN purchase_orders p ON p.number = l.po_number
                       WHERE p.number = %s AND p.vendor_id = %s ORDER BY line_number""",
                    (fields.po, vendor_id),
                ).fetchall()
                if po_lines:
                    extraction = trace or {}
                    pairs, issues = reconcile(extraction.get("items"), po_lines, verification_model(extraction))
                    if state.get("manual_correction") and fields.line_total != state["manual_correction"].get("extracted_line_total"):
                        issues.append("Confirm the line items against the corrected line total.")
                    check = {
                        "title": "PO lines need review" if issues else "PO lines reconciled",
                        "passed": not issues, "source": fields.po, "linePairs": pairs,
                        "detail": " ".join(issues) if issues else "All PO lines matched on quantity, unit, and unit price.",
                    }
                    checks.append(check)
                    if issues and exception is None:
                        exception = {key: check[key] for key in ("title", "detail", "source")}
            if fields is None and trace:
                exception["detail"] = "The model could not extract all required USD invoice fields from the PDF."
            conn.execute(
                """UPDATE invoices SET invoice_number = %s, normalized_number = %s, vendor_name = %s,
                       vendor_id = %s, po_number = %s, issued_on = %s, due_on = %s, terms_days = %s,
                       amount_cents = %s, checks = %s, exception = %s, ai_extraction = %s, verified_at = now()
                   WHERE id = %s""",
                (
                    fields.number if fields else None, normalize_number(fields.number) if fields else None,
                    fields.vendor if fields else None, vendor_id, fields.po if fields else None,
                    fields.issued if fields else None, fields.due if fields else None,
                    fields.terms if fields else None, fields.amount if fields else None,
                    Jsonb(checks), Jsonb(exception) if exception else None, Jsonb(trace) if trace else None, invoice_id,
                ),
            )
        invoice = get_invoice(conn, invoice_id)
        evidence = {key: invoice[key] for key in ("id", "number", "vendor", "amount", "terms", "po", "filename", "checks", "exception")}
        for key in ("issued", "due", "verifiedAt"):
            evidence[key] = invoice[key].isoformat() if invoice[key] else None
        evidence["pdfSha256"] = sha256(row["pdf"]).hexdigest()
        evidence["currency"] = "USD"
        if invoice["manualCorrection"]:
            evidence["manualCorrection"] = invoice["manualCorrection"]
    return {"evidence": evidence}


def route(state: WorkflowState) -> Command[Literal["review", "__end__"]]:
    evidence = state["evidence"]
    exception = evidence["exception"]
    trace = state.get("ai_extraction")
    if trace and evidence["checks"] and (model := verification_model(trace)):
        with connect() as conn:
            invoice = get_invoice(conn, state["invoice_id"])
        if invoice["aiInvestigation"] is None:
            report = investigate({
                **evidence, "vendorId": invoice["vendorId"], "items": trace.get("items", []),
            }, model)
            with connect() as conn:
                conn.execute("UPDATE invoices SET ai_investigation = %s WHERE id = %s",
                             (Jsonb(report), state["invoice_id"]))
    status = "Needs review" if exception else "Awaiting approval"
    with connect() as conn:
        conn.execute("UPDATE invoices SET status = %s WHERE id = %s AND status = 'Processing'",
                     (status, state["invoice_id"]))
    return Command(goto=END if exception else "review")


def review(state: WorkflowState):
    decision = interrupt(state["evidence"])
    return {"decision": decision}


def finalize(state: WorkflowState):
    invoice_id = state["invoice_id"]
    with connect() as conn:
        row = conn.execute("SELECT status FROM invoices WHERE id = %s FOR UPDATE", (invoice_id,)).fetchone()
        decision = conn.execute("SELECT status, note, reviewer, evidence FROM decisions WHERE invoice_id = %s",
                                (invoice_id,)).fetchone()
        if not decision or decision != {**state["decision"], "evidence": state["evidence"]}:
            raise RuntimeError("The recorded decision does not match this workflow")
        if row["status"] == decision["status"]:
            return {}
        if row["status"] != "Awaiting approval":
            raise RuntimeError("The invoice is outside the approval queue")
        if decision["status"] == "Approved":
            evidence = decision["evidence"]
            conn.execute(
                """INSERT INTO sandbox_payment_requests (invoice_id, reference, vendor, amount_cents, currency)
                   VALUES (%s, %s, %s, %s, %s)""",
                (invoice_id, f"PAY-{invoice_id}", evidence["vendor"], evidence["amount"], evidence["currency"]),
            )
        conn.execute("UPDATE invoices SET status = %s WHERE id = %s", (decision["status"], invoice_id))
    return {}


builder = StateGraph(WorkflowState)
builder.add_node("extract", extract)
builder.add_node("validate", validate)
builder.add_node("verify", verify_invoice)
builder.add_node("route", route)
builder.add_node("review", review)
builder.add_node("finalize", finalize)
builder.add_edge(START, "extract")
builder.add_edge("extract", "validate")
builder.add_edge("validate", "verify")
builder.add_edge("verify", "route")
builder.add_edge("review", "finalize")
builder.add_edge("finalize", END)


def saver(conn):
    return PostgresSaver(conn, serde=JsonPlusSerializer(allowed_msgpack_modules=[]))


@contextmanager
def locked_workflow(invoice_id):
    with connect(autocommit=True) as conn:
        # Serializes graph invocations across API workers. Closing this connection releases the lock.
        conn.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (f"workflow:{invoice_id}",))
        graph = builder.compile(checkpointer=saver(conn))
        yield conn, graph, {"configurable": {"thread_id": invoice_id}}


def run_processing(conn, graph, config, invoice):
    snapshot = graph.get_state(config)
    if not snapshot.values:
        if invoice["status"] in ("Needs review", "Approved", "Rejected"):
            return
        conn.execute("UPDATE invoices SET status = 'Processing' WHERE id = %s AND status = 'Uploaded'",
                     (invoice["id"],))
        graph.invoke({"invoice_id": invoice["id"]}, config, durability="sync")
    elif snapshot.interrupts:
        recorded = conn.execute("SELECT status, note, reviewer FROM decisions WHERE invoice_id = %s",
                                (invoice["id"],)).fetchone()
        if recorded:
            graph.invoke(Command(resume=recorded), config, durability="sync")
    elif snapshot.next:
        graph.invoke(None, config, durability="sync")


def process_invoice(invoice_id, *, retry_extraction=False):
    with locked_workflow(invoice_id) as (conn, graph, config):
        invoice = get_invoice(conn, invoice_id)
        if invoice is None:
            raise InvoiceNotFound
        if retry_extraction and invoice["status"] != "Processing":
            if (
                invoice["status"] != "Needs review"
                or (invoice["exception"] or {}).get("title") != "Manual extraction needed"
                or invoice["manualCorrection"] is not None
            ):
                raise DecisionConflict("Only invoices awaiting manual extraction can be re-extracted.")
            with conn.transaction():
                saver(conn).delete_thread(invoice_id)
                conn.execute(
                    """UPDATE invoices SET status = 'Processing', verified_at = NULL,
                           checks = '[]'::jsonb, exception = NULL, ai_extraction = NULL,
                           ai_investigation = NULL WHERE id = %s""",
                    (invoice_id,),
                )
            invoice = get_invoice(conn, invoice_id)
        run_processing(conn, graph, config, invoice)
        return get_invoice(conn, invoice_id)


def correct_invoice(invoice_id, fields, reviewer):
    with locked_workflow(invoice_id) as (conn, graph, config):
        invoice = get_invoice(conn, invoice_id)
        if invoice is None:
            raise InvoiceNotFound
        if invoice["status"] != "Needs review" or (invoice["exception"] or {}).get("title") not in {
            "Manual extraction needed", "Vendor not found",
        }:
            raise DecisionConflict("Only invoices held for extraction or vendor matching can be corrected.")
        extraction = invoice["aiExtraction"]
        correction = {
            "fields": fields, "reviewer": reviewer,
            "at": conn.execute("SELECT now()::text AS at").fetchone()["at"],
        }
        if extraction:
            items = extraction.get("items", [])
            if items:
                total = sum(Decimal(item["amount"]) for item in items) + Decimal(extraction.get("tax") or "0.00")
                correction["extracted_line_total"] = int(total * 100)
        # Retain the original extraction on the invoice for audit. Manual input is separate.
        with conn.transaction():
            saver(conn).delete_thread(invoice_id)
            conn.execute(
                """UPDATE invoices SET status = 'Processing', verified_at = NULL,
                       checks = '[]'::jsonb, exception = NULL, ai_investigation = NULL,
                       manual_correction = %s WHERE id = %s""",
                (Jsonb(correction), invoice_id),
            )
        run_processing(conn, graph, config, get_invoice(conn, invoice_id))
        return get_invoice(conn, invoice_id)


def decide_invoice(invoice_id, *, status, note, reviewer):
    proposed = {"status": status, "note": note, "reviewer": reviewer}
    with locked_workflow(invoice_id) as (conn, graph, config):
        invoice = get_invoice(conn, invoice_id)
        if invoice is None:
            raise InvoiceNotFound
        previous = conn.execute("SELECT status, note, reviewer FROM decisions WHERE invoice_id = %s",
                                (invoice_id,)).fetchone()
        if previous and previous != proposed:
            raise DecisionConflict("A different decision has already been recorded.")
        if not previous:
            if invoice["status"] != "Awaiting approval":
                raise DecisionConflict("This invoice is not awaiting approval.")
            run_processing(conn, graph, config, invoice)
        snapshot = graph.get_state(config)
        if not previous:
            if not snapshot.interrupts or snapshot.next != ("review",):
                raise DecisionConflict("This invoice has no pending approval checkpoint.")
            # Persist the authorized input before resuming. A retry must use that same decision.
            conn.execute(
                """INSERT INTO decisions (invoice_id, status, note, reviewer, evidence)
                   VALUES (%s, %s, %s, %s, %s)""",
                (invoice_id, status, note, reviewer, Jsonb(snapshot.values["evidence"])),
            )
        if snapshot.interrupts:
            graph.invoke(Command(resume=proposed), config, durability="sync")
        elif snapshot.next:
            graph.invoke(None, config, durability="sync")
        return get_invoice(conn, invoice_id)
