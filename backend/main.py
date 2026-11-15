from datetime import date
from decimal import Decimal
from typing import Annotated, Literal
from urllib.parse import quote
from uuid import UUID, uuid4

import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from .auth import require_reviewer, router as session_router
from .database import connect
from .extraction import MAX_PDF_BYTES
from .invoices import INVOICE_QUERY, get_invoice, store_invoice
from .mcp_client import LookupUnavailable
from .model import Amount, InvoiceNumber, ModelUnavailable, PurchaseOrder, Vendor, configured_model, gemini_verification_model
from .policies import policy_answer
from .workflow import DecisionConflict, InvoiceNotFound, correct_invoice, decide_invoice, process_invoice

app = FastAPI(title="FinOps AI")
app.include_router(session_router)


class DecisionInput(BaseModel):
    status: Literal["Approved", "Rejected"]
    note: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


@app.exception_handler(psycopg.OperationalError)
async def database_unavailable(_request, _error):
    return JSONResponse({"detail": "The invoice database is unavailable. Try again shortly."}, status_code=503)


@app.exception_handler(ModelUnavailable)
@app.exception_handler(LookupUnavailable)
async def lookup_unavailable(_request, error):
    return JSONResponse({"detail": str(error)}, status_code=503)


@app.get("/health")
def health():
    with connect() as conn:
        conn.execute("SELECT 1 FROM invoices LIMIT 1")
    return {"status": "ok"}


@app.get("/invoices")
def list_invoices():
    with connect() as conn:
        return conn.execute(INVOICE_QUERY + " ORDER BY i.received_at DESC, i.id").fetchall()


@app.get("/vendors")
def list_vendors():
    with connect() as conn:
        return conn.execute(
            """SELECT v.id, v.name, v.initials, v.color, a.reference, a.terms_days AS terms,
                      (SELECT count(*) FROM purchase_orders p WHERE p.vendor_id = v.id) AS "purchaseOrderCount"
               FROM vendors v
               LEFT JOIN LATERAL (
                   SELECT reference, terms_days FROM vendor_agreements
                   WHERE vendor_id = v.id AND effective_from <= (now() AT TIME ZONE 'UTC')::date
                   ORDER BY effective_from DESC LIMIT 1
               ) a ON true
               ORDER BY v.name, v.id"""
        ).fetchall()


@app.post("/invoices", status_code=201)
def upload_invoice(
    file: UploadFile,
    x_finops_request: Annotated[Literal["upload"], Header()],
    idempotency_key: Annotated[UUID | None, Header()] = None,
):
    pdf = file.file.read(MAX_PDF_BYTES + 1)
    if len(pdf) > MAX_PDF_BYTES:
        raise HTTPException(413, "Choose a PDF of 5 MB or less.")
    if not pdf.startswith(b"%PDF-"):
        raise HTTPException(415, "Choose a valid PDF.")
    filename = (file.filename or "invoice.pdf").replace("\\", "/").rsplit("/", 1)[-1][:200] or "invoice.pdf"
    invoice_id = f"UPL-{(idempotency_key or uuid4()).hex}"
    with connect() as conn:
        store_invoice(conn, invoice_id, filename, pdf)
        original = conn.execute("SELECT pdf, filename FROM invoices WHERE id = %s", (invoice_id,)).fetchone()
        if original["pdf"] != pdf or original["filename"] != filename:
            raise HTTPException(409, "This upload key is already used by a different document.")
    return process_invoice(invoice_id)


@app.post("/invoices/{invoice_id}/process")
def retry_processing(invoice_id: str, x_finops_request: Annotated[Literal["process", "retry-extraction"], Header()]):
    try:
        return process_invoice(invoice_id, retry_extraction=x_finops_request == "retry-extraction")
    except InvoiceNotFound as error:
        raise HTTPException(404, "Invoice not found.") from error
    except DecisionConflict as error:
        raise HTTPException(409, str(error)) from error


class InvoiceCorrection(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    vendor: Vendor
    number: InvoiceNumber
    po: PurchaseOrder
    issued: date
    due: date
    terms: Annotated[int, Field(strict=True, ge=1, le=365)]
    amount: Amount
    line_total: Amount


@app.post("/invoices/{invoice_id}/correction")
def correct(invoice_id: str, data: InvoiceCorrection, reviewer: Annotated[str, Depends(require_reviewer)]):
    fields = data.model_dump(mode="json")
    fields["po"] = data.po.upper()
    fields["amount"] = int(Decimal(data.amount) * 100)
    fields["line_total"] = int(Decimal(data.line_total) * 100)
    if fields["amount"] <= 0:
        raise HTTPException(422, "Enter a positive invoice total.")
    try:
        return correct_invoice(invoice_id, fields, reviewer)
    except InvoiceNotFound as error:
        raise HTTPException(404, "Invoice not found.") from error
    except DecisionConflict as error:
        raise HTTPException(409, str(error)) from error


@app.get("/invoices/{invoice_id}")
def invoice_detail(invoice_id: str):
    with connect() as conn:
        invoice = get_invoice(conn, invoice_id)
    if not invoice:
        raise HTTPException(404, "Invoice not found.")
    return invoice


@app.get("/invoices/{invoice_id}/pdf")
def invoice_pdf(invoice_id: str):
    with connect() as conn:
        document = conn.execute("SELECT filename, pdf FROM invoices WHERE id = %s", (invoice_id,)).fetchone()
    if not document:
        raise HTTPException(404, "Invoice PDF not found.")
    return Response(
        document["pdf"], media_type="application/pdf",
        headers={
            "Content-Disposition": "inline; filename*=UTF-8''" + quote(document["filename"], safe=""),
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


class PolicyQuestion(BaseModel):
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


@app.post("/invoices/{invoice_id}/policy-question")
def ask_policy(invoice_id: str, data: PolicyQuestion):
    model = gemini_verification_model() or configured_model()
    if not model:
        raise HTTPException(503, "The policy model is unavailable.")
    answer = policy_answer(invoice_id, data.question, model)
    if answer is None:
        raise HTTPException(404, "No payment terms clause was found for this invoice.")
    return answer


@app.post("/invoices/{invoice_id}/decision")
def decide(invoice_id: str, decision: DecisionInput, reviewer: Annotated[str, Depends(require_reviewer)]):
    try:
        return decide_invoice(invoice_id, status=decision.status, note=decision.note, reviewer=reviewer)
    except InvoiceNotFound as error:
        raise HTTPException(404, "Invoice not found.") from error
    except DecisionConflict as error:
        raise HTTPException(409, str(error)) from error
