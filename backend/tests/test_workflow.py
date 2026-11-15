import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from hashlib import sha256
from unittest.mock import patch
from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb

from backend.database import connect
from backend.main import app
from backend.mcp_client import LookupUnavailable
from backend.seed import ROOT, main as seed
from backend.workflow import locked_workflow

TEST_DATABASE = os.getenv("FINOPS_TEST_DATABASE_URL")
PASSWORD = "integration-reviewer-password"
REVIEWER = "Finance test reviewer"


@unittest.skipUnless(TEST_DATABASE, "Set FINOPS_TEST_DATABASE_URL to run PostgreSQL integration tests")
class WorkflowTests(unittest.TestCase):
    def setUp(self):
        schema = "finops_test_" + uuid4().hex
        with psycopg.connect(TEST_DATABASE, autocommit=True) as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        self.addCleanup(self.drop_schema, schema)
        self.enterContext(patch.dict(os.environ, {
            "FINOPS_DATABASE_URL": make_conninfo(TEST_DATABASE, options=f"-c search_path={schema},public"),
            "FINOPS_REVIEWER_PASSWORD": PASSWORD,
            "FINOPS_REVIEWER_NAME": REVIEWER,
        }))
        seed()

    def drop_schema(self, schema):
        with psycopg.connect(TEST_DATABASE, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))

    def sign_in(self, client):
        response = client.post("/session", json={"password": PASSWORD})
        self.assertEqual(response.status_code, 200, response.text)

    def test_checkpoint_reopens_and_concurrent_approval_creates_one_request(self):
        with TestClient(app) as first:
            rows = {row["id"]: row for row in first.get("/invoices").json()}
            self.assertEqual(rows["INV-1042"]["status"], "Awaiting approval")
            mismatch = rows["INV-1041"]["exception"]
            self.assertEqual(mismatch["title"], "Payment terms mismatch")
            self.assertIn("Net 90", mismatch["detail"])
            self.assertEqual(mismatch["citation"]["section"], "4.2")
            self.assertIn("Net 30", mismatch["citation"]["quote"])
            self.assertEqual({check.get("lookup") for check in rows["INV-1042"]["checks"] if check.get("lookup")}, {
                "get_vendor", "get_purchase_order", "get_vendor_agreement", "find_prior_invoice", "find_near_duplicate", "get_vendor_history",
            })
            self.assertEqual(rows["INV-1040"]["exception"]["title"], "Possible duplicate")
            self.assertEqual(first.post("/invoices/INV-1042/decision", json={
                "status": "Approved", "note": "Attempt as a guest",
            }).status_code, 403)

        # This graph and its PostgreSQL connection are new, with state loaded by invoice ID.
        with locked_workflow("INV-1042") as (_, graph, config):
            snapshot = graph.get_state(config)
            self.assertEqual(snapshot.next, ("review",))
            self.assertEqual(snapshot.interrupts[0].value, snapshot.values["evidence"])

        def approve():
            with TestClient(app) as client:
                self.sign_in(client)
                return client.post("/invoices/INV-1042/decision", json={
                    "status": "Approved", "note": "PO and terms verified",
                })

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: approve(), range(2)))
        for response in responses:
            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()
            self.assertEqual(result["decision"]["reviewer"], REVIEWER)
            self.assertEqual(result["decision"]["evidence"], snapshot.values["evidence"])
            self.assertEqual(result["paymentRequest"]["amount"], 485000)
            self.assertEqual(result["paymentRequest"]["reference"], "PAY-INV-1042")

        with connect() as conn:
            self.assertEqual(conn.execute("SELECT count(*) AS n FROM sandbox_payment_requests").fetchone()["n"], 1)
        with TestClient(app) as client:
            self.sign_in(client)
            for invoice_id in ("INV-1041", "INV-1042"):
                self.assertEqual(client.post(f"/invoices/{invoice_id}/decision", json={
                    "status": "Rejected", "note": "Conflicting decision",
                }).status_code, 409)
            self.assertEqual(client.delete("/session").status_code, 200)
            self.assertIsNone(client.get("/session").json()["reviewer"])

    def test_processing_retry_recovers_a_recorded_decision_after_interruption(self):
        with locked_workflow("INV-1042") as (conn, graph, config):
            snapshot = graph.get_state(config)
            conn.execute(
                "INSERT INTO decisions (invoice_id, status, note, reviewer, evidence) VALUES (%s, %s, %s, %s, %s)",
                ("INV-1042", "Rejected", "Hold this payment", REVIEWER, Jsonb(snapshot.values["evidence"])),
            )
        with TestClient(app) as client:
            self.assertTrue(client.get("/invoices/INV-1042").json()["decisionPending"])
            response = client.post("/invoices/INV-1042/process", headers={"X-FinOps-Request": "process"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["status"], "Rejected")
            self.assertIsNone(response.json()["paymentRequest"])
            self.assertFalse(response.json()["decisionPending"])

    def test_upload_retry_reuses_the_same_invoice(self):
        pdf = (ROOT / "public" / "invoices" / "INV-1042.pdf").read_bytes()
        headers = {"X-FinOps-Request": "upload", "Idempotency-Key": str(uuid4())}
        with TestClient(app) as client:
            existing = {row["id"] for row in client.get("/invoices").json()}
            for _ in range(2):
                response = client.post("/invoices", files={"file": ("copy.pdf", pdf, "application/pdf")}, headers=headers)
                self.assertEqual(response.status_code, 201, response.text)
                self.assertEqual(response.json()["exception"]["title"], "Possible duplicate")
            invoice_id = response.json()["id"]
            self.assertEqual({row["id"] for row in client.get("/invoices").json()}, existing | {invoice_id})
            document = client.get(f"/invoices/{invoice_id}/pdf").content
            self.assertEqual(sha256(document).digest(), sha256(pdf).digest())
            response = client.post("/invoices", files={"file": ("different.pdf", pdf, "application/pdf")}, headers=headers)
            self.assertEqual(response.status_code, 409)

    def test_unavailable_lookup_leaves_invoice_retryable(self):
        pdf = (ROOT / "public" / "invoices" / "INV-1042.pdf").read_bytes()
        key = uuid4()
        headers = {"X-FinOps-Request": "upload", "Idempotency-Key": str(key)}
        with TestClient(app) as client:
            with patch("backend.verification.lookup_records", side_effect=LookupUnavailable("Unavailable")):
                response = client.post("/invoices", files={"file": ("copy.pdf", pdf)}, headers=headers)
            self.assertEqual(response.status_code, 503)
            invoice_id = f"UPL-{key.hex}"
            self.assertEqual(client.get(f"/invoices/{invoice_id}").json()["status"], "Processing")
            response = client.post(f"/invoices/{invoice_id}/process", headers={"X-FinOps-Request": "process"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["exception"]["title"], "Possible duplicate")

    def test_concurrent_duplicate_lookup_and_verification_are_serialized(self):
        pdf = (ROOT / "public" / "invoices" / "INV-1042.pdf").read_bytes()
        with connect() as conn:
            conn.execute("DELETE FROM invoices WHERE id = 'INV-1042'")

        def submit(_):
            with TestClient(app) as client:
                response = client.post("/invoices", files={"file": ("invoice.pdf", pdf)},
                                       headers={"X-FinOps-Request": "upload"})
                self.assertEqual(response.status_code, 201, response.text)
                return response.json()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(submit, range(2)))
        by_status = {invoice["status"]: invoice for invoice in results}
        self.assertEqual(set(by_status), {"Awaiting approval", "Needs review"})
        self.assertEqual(by_status["Needs review"]["exception"]["title"], "Possible duplicate")
        self.assertIn(by_status["Awaiting approval"]["id"], by_status["Needs review"]["exception"]["source"])

    def test_reviewer_correction_rechecks_invoice_and_preserves_model_fields(self):
        pdf = (ROOT / "public" / "invoices" / "INV-1040.pdf").read_bytes()
        trace = {"state": "manual review", "model": "test-model",
                 "readFields": {"vendor": "Cobalt Office Group", "amount": None}}
        fields = {"vendor": "Cobalt Office Group", "number": "INV-104O", "po": "PO-2069",
                  "issued": "2026-11-09", "due": "2026-12-09", "terms": 30,
                  "amount": "1275.00", "line_total": "1275.00"}
        with TestClient(app) as client:
            with patch.dict(os.environ, {"FINOPS_OLLAMA_MODEL": "test-model"}), patch(
                "backend.workflow.extract_with_model", return_value=(None, trace),
            ):
                response = client.post("/invoices", files={"file": ("incomplete.pdf", pdf)},
                                       headers={"X-FinOps-Request": "upload"})
            self.assertEqual(response.status_code, 201)
            invoice = response.json()
            self.assertEqual(invoice["aiExtraction"]["readFields"], trace["readFields"])
            path = f'/invoices/{invoice["id"]}/correction'
            self.assertEqual(client.post(path, json=fields).status_code, 403)
            self.sign_in(client)
            with patch("backend.workflow.investigate", return_value={"state": "investigated"}):
                response = client.post(path, json=fields)
            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()
            self.assertEqual(result["exception"]["title"], "Possible near duplicate")
            self.assertEqual(result["exception"]["source"], "Prior invoice INV-1040 · INV-1040")
            self.assertEqual(result["manualCorrection"]["reviewer"], REVIEWER)
            self.assertEqual(result["aiExtraction"], trace)
            self.assertEqual(client.post(path, json=fields).status_code, 409)

    def test_near_duplicate_requires_typo_amount_and_date(self):
        from backend.mcp_server import find_near_duplicate

        for number, amount, issued, expected in (
            ("INV-104O", 127500, "2026-11-09", "INV-1040"),
            ("INV-104O", 127500, "2026-11-04", "AP-0981"),
            ("INV-1041", 127500, "2026-11-09", None),
            ("INV-104O", 127501, "2026-11-09", None),
            ("INV-104O", 127500, "2026-11-20", None),
        ):
            result = find_near_duplicate("V-103", number, "new", amount, date.fromisoformat(issued))["record"]
            self.assertEqual(result["id"] if result else None, expected)
