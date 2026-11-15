# FinOps AI

An accounts payable demo for investigating invoice exceptions and reviewing invoices before payment approval.

## Run locally

Requires Node.js 20.9+, Python 3.11+, and a running PostgreSQL instance with the pgvector extension available. For a new local database, run `createdb finops_ai`. From the project folder:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
python -m backend.seed
```

Set `FINOPS_DATABASE_URL` before seeding and starting the API if you use a custom PostgreSQL connection string. The default is `dbname=finops_ai`. For a database from Step 2, rerun `python -m backend.seed` to apply the schema changes and initialize checkpoints for pending approvals. Historical decisions keep their original notes and timestamps. Rerun the same seed command for Step 4 to install the policy tables and add verified citations to existing payment-terms holds.

To enable reviewer sign-in, set `FINOPS_REVIEWER_PASSWORD` to a password of at least 16 characters in the API environment. Set `FINOPS_REVIEWER_NAME` to the name that should appear in the audit history. For example, add the following to the Git-ignored `.env.reviewer` file and replace the password:

```sh
export FINOPS_REVIEWER_PASSWORD='replace-with-your-own-local-password'
export FINOPS_REVIEWER_NAME='Finance reviewer'
```

Then start the API:

```sh
source .env.reviewer
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

In a second terminal:

```sh
npm ci
npm run dev -- --hostname 127.0.0.1
```

Open [localhost:3000](http://localhost:3000). Next.js forwards `/api` to FastAPI at `http://127.0.0.1:8000`. Set `FINOPS_API_ORIGIN` before starting development or building to use another backend address. API documentation is at [localhost:8000/docs](http://localhost:8000/docs).

## Walk through the demo

1. Open **Northline Systems** in **Approvals**. Inspect the invoice fields, PDF, and verification checks.
2. Use **Reviewer sign-in** and enter the configured password. Record an approval with a note.
3. In **Activity**, expand **Evidence at decision**. The entry includes the reviewer, timestamp, note, checked fields, and document hash. Approval creates a single USD sandbox payment request stored in PostgreSQL. Rejection records the decision with no payment request.
4. Open **Meridian Supply Co.** to see the Net 90 versus Net 30 exception and the text of its agreement clause. New verification runs show the MCP lookups under **Activity**. Open **Cobalt Office Group** to see the matching historical invoice.
5. Upload a copy of a sample PDF. A fresh upload with the same vendor and normalized invoice number goes to **Needs review**. Retrying the same upload request with its idempotency key returns the original invoice.

## Workflow and recovery

LangGraph runs extraction, validation, verification, and routing. Clean invoices pause at a reviewer interrupt under their invoice ID. PostgreSQL checkpoints allow the next API process to resume the same workflow after a restart. Guests can view and upload demo invoices. The API requires an expiring reviewer session to record an approval or rejection.

The API records the authorized decision and evidence from the checkpoint before resuming the graph. The final node updates the invoice and, on approval, inserts the sandbox request in a single transaction. A PostgreSQL advisory lock serializes retries for the same invoice, and the invoice ID is the payment request's primary key. Repeating the same decision and note as the same reviewer returns the saved outcome. A conflicting decision receives HTTP 409.

Use **Resume processing** for an interrupted upload, or **Finish decision** if an authorized decision was recorded before processing stopped. These actions resume persisted work. A reviewer decision can only be recorded through the authenticated decision endpoint.

Extraction supports the bundled one-page, text-based USD layout. Scans, encrypted PDFs, and unsupported layouts go to manual review. Uploads are limited to 5 MB. This local demo uses one configured reviewer identity, synthetic data, and sandbox records with no external payment integration. Keep the servers on loopback.

## Verify

```sh
npm run build
FINOPS_TEST_DATABASE_URL='dbname=finops_ai' python -m unittest discover -s backend/tests -v
```

The integration tests create and remove their own PostgreSQL schemas. They cover the approval checkpoint, authorization, concurrent approval retries, recovery after a recorded decision, and upload idempotency.

## MCP and policy citations

The verifier launches `python -m backend.mcp_server` over stdio using the MCP Python SDK. The server reads vendor, purchase order, agreement, and invoice history records in read-only PostgreSQL transactions. The verifier holds the duplicate lock through the history read and database write. A failed MCP lookup leaves the invoice in **Processing** so it can be resumed.

The synthetic clauses use local bag-of-words vectors ranked by cosine distance in pgvector. Retrieval filters to the vendor and effective agreement version and checks the selected clause against the recorded payment terms. The explanation comes from the verification rule and the retrieved quote. The baseline uses this retrieval and the rule-generated explanation. The model-enabled upload path also uses the retrieved clause for investigation and policy questions.

## Run the local AI workflow

Install [Ollama](https://ollama.com/) and run it on loopback. Download the vision model once:

```sh
ollama pull qwen3-vl:4b-instruct
python -m pip install -r backend/requirements.txt
python -m backend.seed
source .env.reviewer
FINOPS_OLLAMA_MODEL=qwen3-vl:4b-instruct python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Upload `public/invoices/INV-1041.pdf` and open **Activity**. The vision model reads the rendered pages and returns typed fields and item amounts. Code reconciles totals, checks the PO, and controls routing and approvals. The model can choose from bounded read-only MCP tools scoped to that invoice. **Activity** shows the results of those calls, the model's advisory findings, and token usage. The **Payment terms** tab retrieves the active agreement clause and displays it alongside the answer.

The model path supports USD invoices of up to three pages. It holds incomplete extracts for manual review. The PO description comparison and historical context are advisory. The model never creates a payment request or authorizes an approval. Seeded invoices use the rules-based path. Upload a PDF through the UI to run live model extraction. Set `FINOPS_OLLAMA_URL` if Ollama runs at a different address.

## Container demo

The Compose stack runs Next.js, FastAPI, and PostgreSQL with pgvector. The database persists in a named volume. A setup container seeds the sample invoices before the API starts. The web service binds to loopback on port 3001 by default.

Set `FINOPS_DB_PASSWORD` and `FINOPS_REVIEWER_PASSWORD` in the Git-ignored `.env.compose` file. Use a reviewer password of at least 16 characters. Then run:

```sh
docker compose --env-file .env.compose -p finops-demo up -d --build --wait
```

Open [the container demo](http://127.0.0.1:3001/). Set `FINOPS_DEMO_PORT` if that port is in use. Stop it with `docker compose --env-file .env.compose -p finops-demo down`. Run it with synthetic data and a loopback binding. A public deployment needs a hosting destination, HTTPS and access controls for invoice routes and uploads.

### Rehearse the live model in containers

Start Ollama and make `qwen3-vl:4b-instruct` available to the API container. Set `FINOPS_OLLAMA_URL` to an Ollama address the container can reach if the default host address is unavailable. Use a separate Compose project to keep rehearsal invoices out of the regular demo database.

```sh
FINOPS_DEMO_PORT=3002 FINOPS_OLLAMA_MODEL=qwen3-vl:4b-instruct \
  docker compose --env-file .env.compose -p finops-rehearsal up -d --build --wait
```

Open [the rehearsal](http://127.0.0.1:3002/), upload `public/invoices/INV-1041.pdf`, and open **Evidence** for the Net 90 hold. **Activity** shows Qwen's tool calls. In **Payment terms**, ask whether the invoice can use Net 90 and check for the citation to Meridian Vendor Agreement VA-017 § 4.2. Sign in and use Northline `INV-1042` to exercise sandbox approval.

### Use Gemini for invoice extraction

Set `FINOPS_GEMINI_MODEL=gemini-3.5-flash-lite` and `FINOPS_GEMINI_API_KEY` in the backend environment or the ignored Compose env file. Upload only synthetic invoices in the hosted demo. Enabling Gemini sends each uploaded PDF to Google for extraction. The backend applies the same one-to-three-page limit and validates the response against the same invoice schema.

Set `FINOPS_GEMINI_VERIFICATION_MODEL=gemini-3.5-flash-lite` to use Gemini for PO description matching, MCP investigation, and payment-terms questions. These requests can include synthetic invoice fields, relevant demo records, the governing clause, and the question. With that setting unset, those steps use Ollama via `FINOPS_OLLAMA_MODEL` and `FINOPS_OLLAMA_URL`. Ollama also handles extraction when `FINOPS_GEMINI_MODEL` is unset. Set `FINOPS_OLLAMA_TIMEOUT` in seconds if the Ollama host needs more than the default 120 seconds per request.

### Host the private demo

On a Docker host with a DNS name, point that name at the host and make ports 80 and 443 available to Caddy. The private Compose override exposes only the HTTPS gateway and its HTTP redirect. The gateway requires a separate visitor login for all routes, including the API and PDFs. Reviewer sessions use a secure cookie.

Create a Git-ignored `.env.private` with separate `FINOPS_DB_PASSWORD` and `FINOPS_REVIEWER_PASSWORD` values. Set `FINOPS_DEMO_HOST` to the DNS name, `FINOPS_DEMO_USER` to the visitor username, and `FINOPS_DEMO_PASSWORD_HASH` to a bcrypt hash in single quotes. Generate it with `docker run --rm -it caddy:2-alpine caddy hash-password`. Set `FINOPS_OLLAMA_URL` to a private Ollama address reachable by the API container with `qwen3-vl:4b-instruct` installed.

```sh
docker compose --env-file .env.private -p finops-private \
  -f compose.yaml -f compose.private.yaml up -d --build --wait
```

Use a fresh demo database with synthetic invoices. From a fresh browser session, confirm that both the landing page and invoice API require visitor credentials. Sign in, upload Meridian `INV-1041` and check its cited hold, then approve Northline `INV-1042` using the separate reviewer sign-in. Share visitor credentials separately from the portfolio URL.

## Evaluation and demo

The seed includes six vendors and 15 invoices in a fresh database. **Vendors** shows the current agreement, purchase-order count, and invoice activity for each vendor. Use **View invoices** to open its register. Existing databases keep their uploads and decisions when reseeded. The additional PDFs in `public/invoices/vendor-demo` include clean matches and holds for payment terms, missing purchase orders, and amount discrepancies.

The local evaluation covers 108 labeled synthetic PDFs and reports extraction, routing, citations, duplicate detection, and latency. See [the evaluation report](evaluation/README.md) and [case-level results](evaluation/results.json).

The [recorded walkthrough](output/demo/finops-walkthrough.webm) shows the sandbox approval and audit evidence for Northline, the cited Net 90 vs. Net 30 hold on Meridian, and Cobalt's prior invoice match. To replay it live, start with Northline in **Approvals** on a fresh demo database.

## Try purchase-order line matching

With the local model enabled, rerun `python -m backend.seed` to load the demo purchase orders, then upload one of the PDFs in `public/invoices/line-matching`. Open **Evidence**, find the PO line check, and expand the invoice line to compare it with the proposed PO line.

| PDF | Expected outcome |
| --- | --- |
| `matched.pdf` | Qwen pairs the abbreviated laptop description with the PO item. Exact checks pass and LINE-101 reaches Approvals. |
| `quantity-price-mismatch.pdf` | The invoice and PO both total $4,000. LINE-102 is held because the billed quantity and unit price differ. |
| `ambiguous.pdf` | The generic laptop description could refer to two PO items. LINE-103 is held for review. |

The check applies to purchase orders with configured lines. It requires a one-to-one match for the full PO and checks whole-unit quantities, supported units, and exact USD prices. Partial deliveries and split billing require manual review.

## Try the vendor-history risk check

Run the seed command and upload a PDF from `public/invoices/risk` with the local model enabled. Open **Evidence** for the amount comparison and **Activity** for the model's findings.

| Invoice | Expected result |
| --- | --- |
| RISK-101 | $12,000 matches the PO and is 44% above Meridian's historical average. Needs review. |
| RISK-102 | $9,000 matches the PO and is 8% above the same average. Awaiting approval if all other checks pass. |
| RISK-103 | Cobalt has one prior invoice. Evidence shows insufficient history, and the other checks determine routing. |

The check uses historical invoice records received before the invoice's issue date in UTC. Three records are required. An amount at least 40% above the exact average triggers review. The demo threshold has yet to be calibrated on real invoice histories.

## Review near duplicates and correct invoice fields

The near-duplicate check looks for common visual substitutions, adjacent transpositions, or a single missing or extra character after invoice-number normalization. It requires the same vendor, the same invoice total, and dates within seven days. For historical rows without an issue date, it compares against the date received. Exact duplicates take precedence. A likely typo goes to **Needs review** with the supporting record in **Evidence**.

For an invoice held for incomplete extraction or a vendor mismatch, sign in as a reviewer and open **Evidence → Correct invoice fields**. Compare the prefilled values with the PDF, fill in the missing fields, and choose **Save and verify**. The original model output is retained when available. The correction is recorded with the reviewer and timestamp, and it goes through verification and the usual approval gate.
