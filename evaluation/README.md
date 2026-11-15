# FinOps AI evaluation

The rules-based baseline generates 108 labeled synthetic PDFs across nine groups, with 12 cases per group. Cases cover clean invoices, normalized duplicates, payment terms conflicts, missing purchase orders, purchase orders assigned to another vendor, line total mismatches, unknown vendors, missing terms, and blank documents.

| Metric | Local result |
| --- | ---: |
| Extraction field matches on readable inputs | 672 / 672 |
| Inputs correctly sent to manual extraction | 24 / 24 |
| Correct routing | 108 / 108 |
| Correct primary exception | 108 / 108 |
| Expected agreement citations matched in full | 12 / 12 |
| Unexpected agreement citations | 0 |
| Duplicate precision and recall | 12 / 12 each |
| Upload latency, median and 95th percentile | 426 ms and 530 ms |

The extraction denominator covers eight fields on 84 readable invoices, including the sum of the line items. Citation matching covers document, section, effective date, and quote. Latency measures a sequential local FastAPI test-client request through PostgreSQL, LangGraph, and MCP. The run used Node 24 and Python 3.14 on the local Mac. The supported layout and the synthetic policy corpus are deliberately small. This benchmark establishes behavior on those fixtures. Production accuracy, OCR accuracy, concurrent throughput, and cloud cost remain unmeasured.

Run from the project root with the backend dependencies installed and a PostgreSQL database with pgvector available:

```sh
python -m pip install -r evaluation/requirements.txt
FINOPS_TEST_DATABASE_URL='dbname=finops_ai' python -m evaluation.run --output evaluation/results.json
```

The runner creates and drops an isolated schema. The generated PDFs run in memory. The JSON report retains the individual routing, extraction, citation, and latency measurements.


## Live model smoke test

The Next.js API proxy, FastAPI, LangGraph, MCP, PostgreSQL, and local Ollama processed the original Meridian and Cobalt sample PDFs as fresh uploads using Qwen3-VL 4B Instruct. This two-document run is separate from the 108-case baseline.

| Input | Observed result | Upload time |
| --- | --- | ---: |
| Meridian | Correct invoice number, USD 12,400 total, and Net 90. Held for the Net 30 mismatch. The model selected the PO and vendor-history tools, and the retrieved agreement supported the policy answer. | 10.83 s |
| Cobalt | Read the item amount but omitted the declared invoice total. Routed to manual extraction. | 3.81 s |

Investigation used 2,355 input and 171 output tokens across model calls. Local inference incurred no provider API charge. These runs do not establish AI extraction, matching, or risk-detection accuracy across layouts.

## Eight-case AI evaluation

The same eight labeled PDFs ran before and after improving the extraction instructions and adding a text-layer fallback. The set includes the original Cobalt and Meridian PDFs, stacked and column layouts, a two-page invoice with tax and total on page two, a simulated raster scan, a missing total, and a second payment-terms mismatch.

| Metric | Before | After |
| --- | ---: | ---: |
| Required fields returned correctly | 7 / 49 | 49 / 49 |
| Correct routing | 4 / 8 | 8 / 8 |
| Correct primary exception | 2 / 8 | 8 / 8 |
| Exact agreement citations on terms holds | 1 / 2 | 2 / 2 |
| Expected MCP lookups on the two Meridian cases | 2 / 4 | 3 / 4 |
| Policy questions with the expected citation behavior | 3 / 3 | 3 / 3 |
| Median upload time | 8.32 s | 15.24 s |

The field metric checks vendor, number, PO, total, terms, issue date, and due date on the seven complete invoices. Before the change, the workflow withheld the extracted fields when the total was missing, so the field metric reflects values returned by the API. The missing-total case correctly remained in manual review. After the change, Cobalt's total and the two-page invoice's total and sales tax came from uniquely labeled PDF text. The model read the simulated scan directly. The alternate Meridian layout skipped the vendor history lookup, so the tool score measures coverage of the expected calls for these fixtures. Policy questions covered a greeting, an unrelated question, and a Net 90 request.

Latency is from the local upload endpoint with Ollama on the same Mac. More invoices completed investigation after the fix, and the recording overlapped part of the evaluation. Treat these times as observations from this run. The sample is too small to estimate production accuracy.

```sh
FINOPS_TEST_DATABASE_URL='dbname=finops_ai' FINOPS_OLLAMA_MODEL=qwen3-vl:4b-instruct \
  python -m evaluation.ai_run --output evaluation/ai-results.json
```

[Before results](ai-before.json) · [After results](ai-results.json) · [Fresh upload walkthrough](../output/demo/finops-ai-walkthrough.webm)

## Current verification and workflow cases

The rules rerun matched all 108 routes, 672 expected fields, and 12 agreement citations. A fresh eight-PDF run with Qwen3-VL matched all 49 fields, eight routes, eight primary exceptions, two agreement citations, four expected MCP lookups, and three policy-question outcomes.

Six additional PDFs went through the live model workflow:

| Check | Expected fixture outcomes | Matched |
| --- | --- | ---: |
| Purchase order line reconciliation | One successful match, one quantity and price mismatch, and one ambiguous match | 3 / 3 |
| Vendor invoice history | One unusual amount, one normal amount, and one case with insufficient history | 3 / 3 |

All six matched their expected route and primary exception. The invoice history threshold is deterministic. These fixture counts are too small to estimate production precision or recall. The current model runs overlapped on a single Ollama instance, so their latency measurements include contention.

```sh
FINOPS_TEST_DATABASE_URL='dbname=finops_ai' FINOPS_OLLAMA_MODEL=qwen3-vl:4b-instruct \
  python -m evaluation.ai_run --workflow --output evaluation/ai-workflow.json
```

[Current rules results](rules-current.json) · [Current AI results](ai-current.json) · [Workflow results](ai-workflow.json)

## Fresh matching and risk boundary cases

This suite generates seven PDFs in a statement-style layout and sends them through the real upload endpoint. Four cases cover line matching, including reordered product descriptions, an abbreviation, a same-total quantity and price mismatch, and missing product variants. The three risk invoices test the last cent below the 40% threshold ($11,666.66), the first cent above it ($11,666.67), and one cent more ($11,666.68). Each risk invoice has a matching purchase order. The runner uses an isolated schema.

| Check | Correct holds | Correct passes | False holds | Missed holds |
| --- | ---: | ---: | ---: | ---: |
| Purchase order lines, before prompt change | 2 | 1 | 1 | 0 |
| Purchase order lines, after prompt change | 2 | 2 | 0 | 0 |
| Vendor amount, both runs | 2 | 1 | 0 | 0 |

Qwen extracted both product colors in QA-L201 but initially rejected both line matches. After the prompt clarified how to handle abbreviations and where a named variant can appear, the same fixture reconciled. The missing-variant and quantity/price cases still went to review. All seven routes and primary exceptions matched on the rerun. The percentages shown in the risk evidence round to 40.00% at these three amounts. The check compares against the exact historical average. This retest measures behavior on the same synthetic fixtures after tuning.

[Before results](ai-boundaries-before.json) · [Rerun results](ai-boundaries.json)

```sh
FINOPS_TEST_DATABASE_URL='dbname=finops_ai' FINOPS_OLLAMA_MODEL=qwen3-vl:4b-instruct \
  python -m evaluation.ai_run --boundaries --output evaluation/ai-boundaries.json
```
