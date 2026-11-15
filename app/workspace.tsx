"use client";

import {
  ArrowRight, ArrowUpRight, Check, ChevronRight, CircleAlert,
  CircleCheck, Clock3, ExternalLink, FileText, Files, Inbox, LayoutGrid, ListChecks,
  Search, ShieldCheck, Truck, Upload, X,
} from "lucide-react";
import { type FormEvent, type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { apiRequest, dateLabel, type Citation, type CorrectionFields, type Decision, type Invoice, type LinePair, invoiceRequest, money, timeLabel, type Status, type Vendor } from "../lib/invoices";

const statuses: Status[] = ["Uploaded", "Processing", "Awaiting approval", "Needs review", "Approved", "Rejected"];
type Session = { reviewer: string | null };
type Section = "Overview" | "Invoices" | "Approvals" | "Vendors";
type InvoiceFilter = Status | "All invoices";
type DetailTab = "Evidence" | "PDF" | "Activity" | "Payment terms";

const statusTones: Record<Status, string> = {
  Uploaded: "queued", Processing: "processing", "Awaiting approval": "pending",
  "Needs review": "review", Approved: "approved", Rejected: "rejected",
};

function StatusBadge({ status }: { status: Status }) {
  return <span className={`status ${statusTones[status]}`}><span />{status}</span>;
}

function Dialog({ title, close, children }: { title: string; close: () => void; children: ReactNode }) {
  const dialog = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const element = dialog.current;
    element?.showModal();
    return () => element?.close();
  }, []);

  return (
    <dialog ref={dialog} aria-labelledby="dialog-title" onCancel={(event) => { event.preventDefault(); close(); }}>
      <div className="dialog-header">
        <h2 id="dialog-title">{title}</h2>
        <button className="icon-button" onClick={close} aria-label="Close"><X size={19} /></button>
      </div>
      {children}
    </dialog>
  );
}

function UploadDialog({ close, add }: { close: () => void; add: (invoice: Invoice) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const uploadKey = useRef("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [uploaded, setUploaded] = useState(false);
  const [progress, setProgress] = useState("");

  useEffect(() => {
    if (!busy || !uploadKey.current) return;
    const id = "UPL-" + uploadKey.current.replaceAll("-", "");
    const controller = new AbortController();
    const timer = window.setInterval(async () => {
      try {
        const invoice = await invoiceRequest<Invoice>(`/${id}`, { signal: controller.signal });
        if (!controller.signal.aborted) {
          setProgress(invoice.aiExtraction ? "Invoice read. Investigating the records…" : "Reading and verifying the invoice…");
        }
      } catch {
        // The upload may still be arriving.
      }
    }, 1500);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [busy]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file || busy || uploaded) return;
    setBusy(true);
    setError("");
    setProgress("Uploading PDF…");
    try {
      const form = new FormData();
      form.set("file", file);
      const invoice = await invoiceRequest<Invoice>("", {
        method: "POST",
        headers: { "X-FinOps-Request": "upload", "Idempotency-Key": uploadKey.current },
        body: form,
      });
      add(invoice);
      setUploaded(true);
    } catch (error) {
      setError(error instanceof Error ? error.message : "Upload failed. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog title="Upload invoice" close={() => { if (!busy) close(); }}>
      {uploaded ? (
        <div className="dialog-body">
          <p className="notice success" role="status"><CircleCheck size={18} /> PDF saved. Open the invoice to see its verification results.</p>
          <div className="dialog-actions"><button className="button primary" onClick={close}>View invoice</button></div>
        </div>
      ) : (
        <form className="dialog-body" onSubmit={submit}>
          <p className="muted">Upload a PDF up to 5 MB. Model analysis may take a few minutes.</p>
          <label className="file-picker">
            <Upload size={22} />
            <span>{file?.name ?? "Choose a PDF"}</span>
            <input type="file" accept=".pdf,application/pdf" aria-label="Choose a PDF" disabled={busy} onChange={(event) => {
              setFile(event.target.files?.[0] ?? null);
              uploadKey.current = crypto.randomUUID();
              setError("");
            }} />
          </label>
          {busy && <p className="muted" role="status">{progress}</p>}
          {error && <p className="notice warning" role="alert"><CircleAlert size={18} />{error}</p>}
          <div className="dialog-actions">
            <button type="button" className="button secondary" onClick={close} disabled={busy}>Cancel</button>
            <button type="submit" className="button primary" disabled={!file || busy}>{busy ? "Uploading and verifying…" : "Add invoice"}</button>
          </div>
        </form>
      )}
    </Dialog>
  );
}

function SignInDialog({ close, signedIn }: { close: () => void; signedIn: (reviewer: string | null) => void }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function signIn(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || !password) return;
    setBusy(true);
    try {
      const session = await apiRequest<Session>("/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password }),
      });
      signedIn(session.reviewer);
      close();
    } catch (error) {
      setError(error instanceof Error ? error.message : "Could not sign in.");
      setBusy(false);
    }
  }

  return (
    <Dialog title="Finance reviewer sign-in" close={() => { if (!busy) close(); }}>
      <form className="dialog-body" onSubmit={signIn}>
        <label className="note-label" htmlFor="reviewer-password">Reviewer password</label>
        <input className="password-input" id="reviewer-password" type="password" autoComplete="current-password"
          value={password} maxLength={256} required disabled={busy} onChange={(event) => setPassword(event.target.value)} />
        {error && <p className="notice warning" role="alert"><CircleAlert size={18} />{error}</p>}
        <div className="dialog-actions">
          <button type="button" className="button secondary" disabled={busy} onClick={close}>Cancel</button>
          <button className="button primary" disabled={busy || !password}>{busy ? "Signing in…" : "Sign in"}</button>
        </div>
      </form>
    </Dialog>
  );
}

function DecisionDialog({ invoice, status, close, save }: {
  invoice: Invoice; status: Decision["status"]; close: () => void; save: (note: string) => Promise<void>;
}) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const approving = status === "Approved";
  return (
    <Dialog title={approving ? "Approve invoice" : "Reject invoice"} close={() => { if (!busy) close(); }}>
      <form className="dialog-body" onSubmit={async (event) => {
        event.preventDefault();
        if (!note.trim() || busy) return;
        setBusy(true);
        setError("");
        try {
          await save(note.trim());
        } catch (error) {
          setError(error instanceof Error ? error.message : "Decision could not be saved.");
          setBusy(false);
        }
      }}>
        <p className="muted">{invoice.number} · {invoice.vendor} · {invoice.amount === null ? "" : money(invoice.amount)}</p>
        <label className="note-label" htmlFor="decision-note">Decision note</label>
        <textarea id="decision-note" placeholder="Summarize your decision…" rows={4} maxLength={1000} value={note} onChange={(event) => setNote(event.target.value)} required disabled={busy} />
        {error && <p className="notice warning" role="alert"><CircleAlert size={18} />{error}</p>}
        <div className="dialog-actions">
          <button type="button" className="button secondary" onClick={close} disabled={busy}>Cancel</button>
          <button className={`button ${approving ? "primary" : "danger"}`} disabled={!note.trim() || busy}>{busy ? "Saving…" : approving ? "Confirm approval" : "Confirm rejection"}</button>
        </div>
      </form>
    </Dialog>
  );
}

function PolicyQuestion({ invoiceId }: { invoiceId: string }) {
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<{ question: string; answer: string; citation: Citation | null } | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function ask(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!question.trim() || busy) return;
    setBusy(true);
    setError("");
    setResult(null);
    try {
      const submittedQuestion = question.trim();
      const answer = await invoiceRequest<{ answer: string; citation: Citation | null }>(`/${invoiceId}/policy-question`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: submittedQuestion }),
      });
      setResult({ ...answer, question: submittedQuestion });
    } catch (error) {
      setError(error instanceof Error ? error.message : "Could not answer the policy question.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card model-activity" onSubmit={ask}>
      <div className="card-head"><h2>Ask about payment terms</h2></div>
      <div className="card-foot policy-question">
        <input className="password-input" aria-label="Payment terms question" maxLength={500}
          placeholder="Can this invoice use Net 90 terms?" value={question} onChange={(event) => setQuestion(event.target.value)} />
        <button className="button secondary" disabled={busy || !question.trim()}>{busy ? "Checking agreement…" : "Ask"}</button>
        {error && <p role="alert">{error}</p>}
        {result && <div role="status">
          <p className="muted">Asked · {result.question}</p>
          <p>{result.answer}</p>
          {result.citation && <figure className="policy-citation">
            <blockquote>{result.citation.quote}</blockquote>
            <figcaption>{result.citation.document} · § {result.citation.section}</figcaption>
          </figure>}
        </div>}
      </div>
    </form>
  );
}

function ModelActivity({ invoice }: { invoice: Invoice }) {
  const { aiExtraction: extraction, aiInvestigation: investigation } = invoice;
  if (!extraction) return null;
  return (
    <section className="card model-activity">
      <div className="card-head"><h2>AI investigation</h2><span>{extraction.model}</span></div>
      <ul className="checks">
        <li><FileText size={18} /><div>
          <strong>{extraction.state === "extracted" ? "Invoice read by the model" : "Manual extraction needed"}</strong>
          {extraction.items?.map((item, index) => <p key={index}>{item.description} · USD {item.amount}</p>)}
          {extraction.tax && <p>Tax · USD {extraction.tax}{extraction.taxSource && " · PDF text"}</p>}
          {extraction.totalSource && <p>Invoice total read from the PDF text.</p>}
          {extraction.usage && <p>{extraction.usage.inputTokens} input tokens · {extraction.usage.outputTokens} output tokens</p>}
        </div></li>
        {investigation && <li><Search size={18} /><div>
          <strong>{investigation.findings.length ? "Model findings" : "Model findings unavailable"}</strong>
          {investigation.findings.map((finding, index) => (
            <details className="decision-evidence" key={index}>
              <summary>{finding.explanation}</summary>
              <pre>{JSON.stringify(investigation.sources?.[finding.source], null, 2)}</pre>
            </details>
          ))}
          {investigation.recommendation && <p>Recommended next step · {investigation.recommendation}</p>}
          <p>{investigation.usage.inputTokens} input tokens · {investigation.usage.outputTokens} output tokens</p>
        </div></li>}
        {investigation?.calls.map((call) => (
          <li key={call.tool}><Files size={18} /><details className="decision-evidence">
            <summary>{call.tool}</summary>
            <pre>{JSON.stringify(call.record, null, 2)}</pre>
          </details></li>
        ))}
      </ul>
    </section>
  );
}

function LinePairEvidence({ pair }: { pair: LinePair }) {
  const { invoice, po } = pair;
  const price = invoice.unit_price ? money(Math.round(Number(invoice.unit_price) * 100)) : "Unit price missing";
  return (
    <details className="decision-evidence">
      <summary>Invoice line {pair.invoiceLine}{po ? ` → PO line ${po.line_number}` : " · Needs review"}</summary>
      <p><strong>Invoice</strong> · {invoice.description}</p>
      <p>{invoice.quantity ?? "Quantity missing"} · {invoice.unit ?? "Unit missing"} · {price}</p>
      {po && <><p><strong>Purchase order</strong> · {po.description}</p>
        <p>{po.quantity} · {po.unit} · {money(po.unit_price_cents)}</p></>}
      {pair.issues.map((issue) => <p key={issue}>{issue}</p>)}
    </details>
  );
}

function CorrectionForm({ invoice, save }: { invoice: Invoice; save: (fields: CorrectionFields) => Promise<void> }) {
  const extracted = invoice.aiExtraction?.readFields;
  const saved = invoice.manualCorrection?.fields;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const fields = [
    ["vendor", "Vendor name", "text", saved?.vendor ?? extracted?.vendor ?? (invoice.vendor === "Unassigned vendor" ? "" : invoice.vendor)],
    ["number", "Invoice number", "text", saved?.number ?? extracted?.number ?? (invoice.number.startsWith("UPL-") ? "" : invoice.number)],
    ["po", "Purchase order", "text", saved?.po ?? extracted?.po ?? invoice.po ?? ""],
    ["issued", "Issue date", "date", saved?.issued ?? extracted?.issued ?? invoice.issued ?? ""],
    ["due", "Due date", "date", saved?.due ?? extracted?.due ?? invoice.due ?? ""],
    ["terms", "Net days", "number", saved?.terms ?? extracted?.terms ?? invoice.terms ?? ""],
    ["amount", "Invoice total (USD)", "text", saved ? (saved.amount / 100).toFixed(2) : extracted?.amount ?? (invoice.amount === null ? "" : (invoice.amount / 100).toFixed(2))],
    ["line_total", "Line items plus tax (USD)", "text", saved ? (saved.line_total / 100).toFixed(2) : ""],
  ] as const;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const form = new FormData(event.currentTarget);
    const data = Object.fromEntries(form) as Record<string, string>;
    setBusy(true);
    setError("");
    try {
      await save({ ...data, terms: Number(data.terms) } as CorrectionFields);
    } catch (error) {
      setError(error instanceof Error ? error.message : "Could not verify the corrected invoice.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card model-activity" onSubmit={submit}>
      <div className="card-head"><h2>Correct invoice fields</h2></div>
      <div className="card-foot policy-question">
        <p>Compare these values with the PDF before submitting. Enter amounts with two decimal places.</p>
        {fields.map(([name, label, type, value]) => (
          <label key={name}>{label}
            <input className="password-input" name={name} type={type} defaultValue={value} required
              min={type === "number" ? 1 : undefined} max={type === "number" ? 365 : undefined}
              pattern={name === "amount" || name === "line_total" ? "[0-9]{1,9}[.][0-9]{2}" : undefined}
              disabled={busy} />
          </label>
        ))}
        {error && <p role="alert">{error}</p>}
        <button className="button secondary" disabled={busy}>{busy ? "Verifying…" : "Save and verify"}</button>
      </div>
    </form>
  );
}

function InvoiceDetail({ invoice, tab, changeTab, reviewer, signIn, chooseDecision, retry, correct }: {
  invoice: Invoice; tab: DetailTab; changeTab: (tab: DetailTab) => void;
  reviewer: string | null; signIn: () => void;
  chooseDecision: (status: Decision["status"]) => void; retry: () => Promise<void>;
  correct: (fields: CorrectionFields) => Promise<void>;
}) {
  const { decision, paymentRequest } = invoice;
  const needsExtraction = invoice.status === "Needs review" && invoice.exception?.title === "Manual extraction needed";
  const canCorrect = needsExtraction || (invoice.status === "Needs review" && invoice.exception?.title === "Vendor not found");
  const [processing, setProcessing] = useState(false);
  const [processingError, setProcessingError] = useState("");

  async function retryProcessing() {
    setProcessing(true);
    setProcessingError("");
    try {
      await retry();
    } catch (error) {
      setProcessingError(error instanceof Error ? error.message : "Could not resume processing.");
    } finally {
      setProcessing(false);
    }
  }
  const fields = [
    ["Vendor", invoice.vendor], ["Invoice number", invoice.number],
    ["Invoice total", invoice.amount === null ? "—" : money(invoice.amount)],
    ["Issue date", invoice.issued ? dateLabel(invoice.issued) : "—"],
    ["Due date", invoice.due ? dateLabel(invoice.due) : "—"],
    ["Payment terms", invoice.terms ? `Net ${invoice.terms}` : "—"],
    ["Purchase order", invoice.po ?? "—"],
  ];
  return (
    <>
      <div className="detail-tabs" aria-label="Invoice detail views">
        {(["Evidence", "PDF", "Activity", "Payment terms"] as const).map((name) => (
          <button key={name} className={tab === name ? "active" : ""} aria-pressed={tab === name} onClick={() => changeTab(name)}>{name}</button>
        ))}
      </div>
      {tab === "Evidence" && invoice.exception && (
        <div className="exception"><CircleAlert size={18} /><div>
          <strong>{invoice.exception.title}</strong>
          <p>{invoice.exception.detail}</p>
          {invoice.exception.citation ? (
            <figure className="policy-citation">
              <blockquote>{invoice.exception.citation.quote}</blockquote>
              <figcaption>{invoice.exception.citation.document} · § {invoice.exception.citation.section} · Effective {dateLabel(invoice.exception.citation.effectiveFrom)}</figcaption>
            </figure>
          ) : <small>{invoice.exception.source}</small>}
        </div></div>
      )}
      {tab === "Evidence" && (
        <div className="detail-grid">
          <div className="card">
            <div className="card-head"><h2>Invoice fields</h2></div>
            <dl className="field-list">{fields.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
            <div className="card-foot"><button className="text-button" onClick={() => changeTab("PDF")}><FileText size={16} /> View invoice PDF <ArrowRight size={15} /></button></div>
          </div>
          <div className="card">
            <div className="card-head"><h2>Verification checks</h2></div>
            {invoice.checks.length ? (
              <ul className="checks">{invoice.checks.map((check) => (
                <li key={check.title}>
                  {check.skipped ? <Clock3 size={18} className="muted" /> : check.passed ? <CircleCheck size={18} className="passed-icon" /> : <CircleAlert size={18} className="warning-icon" />}
                  <div><strong>{check.title}</strong><p>{check.detail}</p>
                    {check.linePairs?.map((pair) => <LinePairEvidence key={pair.invoiceLine} pair={pair} />)}
                  </div>
                </li>
              ))}</ul>
            ) : <p className="card-message">{invoice.exception ? "Open the PDF to inspect the invoice manually." : "Verification is pending."}</p>}
          </div>
        </div>
      )}
      {tab === "Evidence" && canCorrect && (
        reviewer ? <CorrectionForm key={invoice.manualCorrection?.at ?? invoice.id} invoice={invoice} save={correct} />
          : <div className="card decision-bar"><p>Sign in as a reviewer to correct invoice fields.</p>
              <button className="button secondary" onClick={signIn}>Sign in as reviewer</button></div>
      )}
      {tab === "PDF" && (
        <section className="card pdf-card">
          <div className="card-head"><h2>{invoice.filename}</h2><a className="text-button" href={invoice.document} target="_blank" rel="noreferrer">Open PDF <ExternalLink size={14} /></a></div>
          <iframe src={invoice.document} title={invoice.filename} />
        </section>
      )}
      {tab === "Activity" && <ModelActivity invoice={invoice} />}
      {tab === "Payment terms" && (
        invoice.vendorId && invoice.issued
          ? <PolicyQuestion invoiceId={invoice.id} />
          : <p className="card card-message">A matched vendor and invoice date are needed to find the applicable payment agreement.</p>
      )}
      {tab === "Activity" && (
        <section className="card">
          <div className="card-head"><h2>Activity history</h2></div>
          <ul className="checks timeline">
            {paymentRequest && (
              <li><FileText size={18} /><div><strong>Sandbox payment request created</strong>
                <p>{paymentRequest.reference} · {money(paymentRequest.amount)} {paymentRequest.currency} · {timeLabel(paymentRequest.at)}</p>
              </div></li>
            )}
            {decision && (
              <li><CircleCheck size={18} /><div>
                <strong>{decision.status}{decision.reviewer ? ` by ${decision.reviewer}` : ""}</strong>
                <p>{timeLabel(decision.at)} · {decision.note}</p>
                {decision.evidence && <DecisionEvidence evidence={decision.evidence} />}
              </div></li>
            )}
            {invoice.manualCorrection && <li><FileText size={18} /><div><strong>Fields corrected by {invoice.manualCorrection.reviewer}</strong>
              <p>{timeLabel(invoice.manualCorrection.at)}</p></div></li>}
            {invoice.verifiedAt && <li><ShieldCheck size={18} /><div><strong>Verification completed</strong><p>{timeLabel(invoice.verifiedAt)} · {invoice.exception ? "Routed to Needs review" : "Routed to Approvals"}</p></div></li>}
            {invoice.checks.some((check) => check.lookup) && (
              <li><Files size={18} /><details className="decision-evidence">
                <summary>MCP records consulted</summary>
                <ul>{invoice.checks.filter((check) => check.lookup).map((check) => (
                  <li key={check.title}><code>{check.lookup}</code> · {check.source}<small>{check.detail}</small></li>
                ))}</ul>
              </details></li>
            )}
            <li><FileText size={18} /><div><strong>Invoice received</strong><p>{dateLabel(invoice.received)} · {invoice.filename}</p></div></li>
          </ul>
        </section>
      )}
      {invoice.status === "Awaiting approval" && !invoice.decisionPending && (
        <div className="card decision-bar">
          <div><h2>Manager decision</h2><p className="muted">{reviewer ? "Approval creates a sandbox payment request." : "A finance reviewer must sign in to record a decision."}</p></div>
          {reviewer ? (
            <div className="decision-actions">
              <button className="button secondary" onClick={() => chooseDecision("Rejected")}><X size={16} /> Reject</button>
              <button className="button primary" onClick={() => chooseDecision("Approved")}><Check size={16} /> Approve</button>
            </div>
          ) : <button className="button secondary" onClick={signIn}>Sign in as reviewer</button>}
        </div>
      )}
      {(invoice.status === "Uploaded" || invoice.status === "Processing" || invoice.decisionPending || needsExtraction) && (
        <div className="card decision-bar">
          <div><h2>{invoice.decisionPending ? "Decision recorded" : "Invoice processing"}</h2>{processingError && <p role="alert">{processingError}</p>}</div>
          <button className="button secondary" disabled={processing} onClick={() => void retryProcessing()}>
            {processing ? "Processing…" : invoice.decisionPending ? "Finish decision" : needsExtraction ? "Retry extraction" : "Resume processing"}
          </button>
        </div>
      )}
    </>
  );
}

function DecisionEvidence({ evidence }: { evidence: NonNullable<Decision["evidence"]> }) {
  return (
    <details className="decision-evidence">
      <summary>Evidence at decision</summary>
      <p>{evidence.number} · {evidence.vendor} · {money(evidence.amount)} {evidence.currency}</p>
      <p>{evidence.po} · Net {evidence.terms} · Issued {dateLabel(evidence.issued)} · Due {dateLabel(evidence.due)}</p>
      <p>Verified {timeLabel(evidence.verifiedAt)}</p>
      <p>{evidence.filename} · SHA-256 <code>{evidence.pdfSha256}</code></p>
      <ul>
        {evidence.checks.map((check) => (
          <li key={check.title}>{check.title} · {check.detail}{check.source && <small>{check.source}</small>}</li>
        ))}
      </ul>
    </details>
  );
}

function InvoiceTable({ invoices, selectedId, open }: {
  invoices: Invoice[];
  selectedId?: string;
  open: (id: string) => void;
}) {
  return (
    <div className="table-scroll">
      <table className="invoice-table">
        <thead><tr><th scope="col">Invoice / Vendor</th><th scope="col">Amount</th><th scope="col">PO number</th><th scope="col">Due date</th><th scope="col">Status</th><th scope="col"><span className="sr-only">Open invoice</span></th></tr></thead>
        <tbody>{invoices.map((invoice) => (
          <tr key={invoice.id} className={invoice.id === selectedId ? "is-selected" : undefined} onClick={() => open(invoice.id)}>
            <td><button id={`invoice-row-${invoice.id}`} className="invoice-link"
              onClick={(event) => { event.stopPropagation(); open(invoice.id); }}
              aria-label={`Open ${invoice.number}, ${invoice.vendor}`} aria-expanded={invoice.id === selectedId}>
              <span className={`vendor-avatar ${invoice.color}`}>{invoice.initials}</span>
              <span><strong>{invoice.number}</strong><small>{invoice.vendor}</small></span>
            </button></td>
            <td className="amount">{invoice.amount === null ? "—" : money(invoice.amount)}</td>
            <td>{invoice.po ?? "—"}</td><td>{invoice.due ? dateLabel(invoice.due) : "—"}</td>
            <td><StatusBadge status={invoice.status} /></td><td><ChevronRight size={16} className="row-arrow" /></td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  );
}

function Overview({ invoices, browse, open }: {
  invoices: Invoice[];
  browse: (filter: InvoiceFilter, exception?: string) => void;
  open: (id: string) => void;
}) {
  const counts = Object.fromEntries(statuses.map((status) => [status, 0])) as Record<Status, number>;
  const exceptions = new Map<string, number>();
  let approvedValue = 0;
  let poChecked = 0;
  let poMatched = 0;
  for (const invoice of invoices) {
    counts[invoice.status]++;
    if (invoice.status === "Approved") approvedValue += invoice.amount ?? 0;
    if (invoice.status === "Needs review" && invoice.exception) {
      const title = invoice.exception.title;
      exceptions.set(title, (exceptions.get(title) ?? 0) + 1);
    }
    const po = invoice.checks.find((check) => check.lookup === "get_purchase_order" ||
      check.title === "Purchase order matched" || check.title === "Purchase order mismatch");
    if (po && !po.skipped) {
      poChecked++;
      if (po.passed) poMatched++;
    }
  }
  const rate = poChecked ? Math.round(poMatched * 100 / poChecked) : null;
  const cards = [
    { label: "Total invoices", value: invoices.length, status: "All invoices", color: "purple", icon: Files },
    { label: "Needs review", value: counts["Needs review"], status: "Needs review", color: "orange", icon: CircleAlert },
    { label: "Awaiting approval", value: counts["Awaiting approval"], status: "Awaiting approval", color: "blue", icon: Clock3 },
    { label: "Approved invoice value", value: money(approvedValue), status: "Approved", color: "green", icon: CircleCheck },
  ] as const;
  const activeStatuses = statuses.filter((status) => counts[status] > 0);

  return (
    <>
      <section className="metrics overview-metrics" aria-label="Invoice summary">
        {cards.map(({ label, value, status, color, icon: Icon }) => (
          <button key={status} className="metric" onClick={() => browse(status)}>
            <span className="metric-top"><span className={`metric-icon ${color}`}><Icon size={19} /></span><ArrowUpRight size={16} /></span>
            <strong>{value}</strong><span>{label}</span>
          </button>
        ))}
      </section>
      <div className="overview-grid">
        <section className="card overview-card" aria-labelledby="status-heading">
          <h2 id="status-heading">Invoice status</h2>
          {activeStatuses.length ? (
            <>
              <div className="status-track" aria-hidden="true">
                {activeStatuses.map((status) => (
                  <span key={status} className={statusTones[status]} style={{ flexGrow: counts[status] }} />
                ))}
              </div>
              <div className="status-legend">
                {activeStatuses.map((status) => (
                  <button key={status} onClick={() => browse(status)}>
                    <span className={statusTones[status]} />{status}<strong>{counts[status]}</strong>
                  </button>
                ))}
              </div>
            </>
          ) : <p className="muted">Uploaded invoices will appear here.</p>}
        </section>
        <section className="card overview-card" aria-labelledby="po-heading">
          <h2 id="po-heading">PO match rate</h2>
          {rate === null ? <p className="muted">No purchase orders checked yet.</p> : (
            <>
              <strong className="match-rate">{rate}%</strong>
              <progress aria-label="PO match rate" max={poChecked} value={poMatched} />
              <p className="muted">{poMatched} of {poChecked} checked invoices had a PO registered to their vendor. Line items are checked separately.</p>
            </>
          )}
        </section>
      </div>
      <section className="card overview-card overview-exceptions" aria-labelledby="exception-heading">
        <h2 id="exception-heading">Review reasons</h2>
        {exceptions.size ? <ul>
          {[...exceptions].sort(([a, aCount], [b, bCount]) => bCount - aCount || a.localeCompare(b)).map(([title, count]) => (
            <li key={title}><button onClick={() => browse("Needs review", title)}><span>{title}</span><strong>{count}</strong><ChevronRight size={15} /></button></li>
          ))}
        </ul> : <p className="muted">No invoices have a review reason.</p>}
      </section>
      <section className="card register" aria-labelledby="recent-heading">
        <div className="card-head"><h2 id="recent-heading">Recent invoices</h2><button className="text-button" onClick={() => browse("All invoices")}>View all <ArrowRight size={15} /></button></div>
        {invoices.length ? <InvoiceTable invoices={[...invoices].sort((a, b) => Date.parse(b.received) - Date.parse(a.received)).slice(0, 5)} open={open} />
          : <p className="card-message">No invoices yet.</p>}
      </section>
    </>
  );
}

function VendorDirectory({ invoices, browse }: { invoices: Invoice[]; browse: (vendor: Vendor) => void }) {
  const [vendors, setVendors] = useState<Vendor[] | null>(null);
  const [search, setSearch] = useState("");
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    apiRequest<Vendor[]>("/vendors", { signal: controller.signal })
      .then(setVendors)
      .catch((error: Error) => { if (!controller.signal.aborted) setError(error.message); });
    return () => controller.abort();
  }, [attempt]);

  if (error) return (
    <div className="card decision-bar">
      <p role="alert">{error}</p>
      <button className="button secondary" onClick={() => { setError(""); setAttempt(attempt + 1); }}>Try again</button>
    </div>
  );
  if (!vendors) return <p className="card card-message" role="status">Loading vendors…</p>;

  const query = search.trim().toLowerCase();
  const matches = vendors.filter((vendor) => [vendor.name, vendor.id].some((value) => value.toLowerCase().includes(query)));
  const counts = new Map<string, { total: number; review: number }>();
  for (const invoice of invoices) {
    if (!invoice.vendorId) continue;
    const count = counts.get(invoice.vendorId) ?? { total: 0, review: 0 };
    count.total++;
    if (invoice.status === "Needs review") count.review++;
    counts.set(invoice.vendorId, count);
  }

  return (
    <section aria-labelledby="vendor-heading">
      <div className="vendor-toolbar">
        <h2 id="vendor-heading">{matches.length} {matches.length === 1 ? "vendor" : "vendors"}</h2>
        <label className="search"><Search size={17} /><input aria-label="Search vendors" placeholder="Search vendors…" value={search} onChange={(event) => setSearch(event.target.value)} /></label>
      </div>
      {matches.length ? (
        <ul className="vendor-grid">
          {matches.map((vendor) => {
            const count = counts.get(vendor.id);
            return (
              <li className="card vendor-card" key={vendor.id}>
                <div className="vendor-card-header">
                  <span className={`vendor-avatar ${vendor.color}`}>{vendor.initials}</span>
                  <div><h2>{vendor.name}</h2><span className="muted">{vendor.id}</span></div>
                  {vendor.terms !== null && <span className="vendor-terms">Net {vendor.terms}</span>}
                </div>
                <p className="vendor-agreement muted">{vendor.reference ?? "No active agreement"}</p>
                <dl className="vendor-stats">
                  <div><dt>Invoices</dt><dd>{count?.total ?? 0}</dd></div>
                  <div><dt>Needs review</dt><dd>{count?.review ?? 0}</dd></div>
                  <div><dt>Purchase orders</dt><dd>{vendor.purchaseOrderCount}</dd></div>
                </dl>
                <button className="text-button" onClick={() => browse(vendor)} aria-label={`View invoices for ${vendor.name}`}>View invoices <ArrowRight size={15} /></button>
              </li>
            );
          })}
        </ul>
      ) : <p className="card card-message">{query ? "No matching vendors." : "No vendors in the register."}</p>}
    </section>
  );
}

export default function Workspace() {
  const [section, setSection] = useState<Section>("Overview");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [tab, setTab] = useState<DetailTab>("Evidence");
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<InvoiceFilter>("All invoices");
  const [exceptionFilter, setExceptionFilter] = useState("");
  const [vendorFilter, setVendorFilter] = useState<Vendor | null>(null);
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  const [loadState, setLoadState] = useState<"loading" | "ready" | "error">("loading");
  const [loadError, setLoadError] = useState("");
  const [uploadOpen, setUploadOpen] = useState(false);
  const [decisionStatus, setDecisionStatus] = useState<Decision["status"] | null>(null);
  const [message, setMessage] = useState("");
  const [reviewer, setReviewer] = useState<string | null>(null);
  const [signInOpen, setSignInOpen] = useState(false);
  const [sessionError, setSessionError] = useState("");
  const detailHeading = useRef<HTMLHeadingElement>(null);
  const detailPanel = useRef<HTMLElement>(null);
  const searchInput = useRef<HTMLInputElement>(null);
  const loadInvoices = useCallback(async (signal?: AbortSignal) => {
    setLoadState("loading");
    try {
      setInvoices(await invoiceRequest<Invoice[]>("", { signal }));
      setLoadState("ready");
    } catch (error) {
      if (signal?.aborted) return;
      setLoadError(error instanceof Error ? error.message : "Could not load invoices.");
      setLoadState("error");
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void loadInvoices(controller.signal);
    apiRequest<Session>("/session", { signal: controller.signal })
      .then((session) => setReviewer(session.reviewer))
      .catch(() => { if (!controller.signal.aborted) setSessionError("Could not load the reviewer session."); });
    return () => controller.abort();
  }, [loadInvoices]);

  useEffect(() => {
    if (selectedId) {
      detailPanel.current?.scrollTo(0, 0);
      detailHeading.current?.focus({ preventScroll: true });
    }
  }, [selectedId]);

  const pending = invoices.filter((invoice) => invoice.status === "Awaiting approval");
  const selected = invoices.find((invoice) => invoice.id === selectedId);
  const query = search.trim().toLowerCase();
  const visible = (section === "Approvals" ? pending : invoices).filter((invoice) => {
    const matchesStatus = section === "Approvals" || filter === "All invoices" || filter === invoice.status;
    return matchesStatus && (!exceptionFilter || invoice.exception?.title === exceptionFilter) &&
      (!vendorFilter || invoice.vendorId === vendorFilter.id) &&
      [invoice.number, invoice.vendor, invoice.filename, invoice.po ?? ""].some((value) => value.toLowerCase().includes(query));
  });

  function navigate(target: Section) {
    setSection(target);
    setSelectedId(null);
    setSearch("");
    setFilter("All invoices");
    setExceptionFilter("");
    setVendorFilter(null);
    setMessage("");
    window.scrollTo(0, 0);
  }

  function browse(filter: InvoiceFilter, exception = "") {
    navigate("Invoices");
    setFilter(filter);
    setExceptionFilter(exception);
  }

  function openInvoice(id: string) {
    setSelectedId(id);
    setTab("Evidence");
    setMessage("");
  }

  function closeInvoice() {
    setSelectedId(null);
    setMessage("");
    requestAnimationFrame(() => {
      const row = document.getElementById(`invoice-row-${selectedId}`);
      (row ?? searchInput.current)?.focus({ preventScroll: true });
    });
  }

  function addUpload(invoice: Invoice) {
    setInvoices((previous) => [invoice, ...previous.filter((item) => item.id !== invoice.id)]);
    navigate("Invoices");
    openInvoice(invoice.id);
    if (invoice.aiExtraction) setTab("Activity");
  }

  async function signOut() {
    setSessionError("");
    try {
      await apiRequest<Session>("/session", { method: "DELETE" });
      setReviewer(null);
    } catch (error) {
      setSessionError(error instanceof Error ? error.message : "Could not sign out.");
    }
  }

  async function retryProcessing(previousInvoice: Invoice) {
    const invoice = await invoiceRequest<Invoice>(`/${previousInvoice.id}/process`, {
      method: "POST",
      headers: { "X-FinOps-Request": previousInvoice.status === "Needs review" ? "retry-extraction" : "process" },
    });
    setInvoices((previous) => previous.map((item) => item.id === invoice.id ? invoice : item));
    if (invoice.decision) setTab("Activity");
  }

  async function correctInvoice(invoiceId: string, fields: CorrectionFields) {
    try {
      const invoice = await invoiceRequest<Invoice>(`/${invoiceId}/correction`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(fields),
      });
      setInvoices((previous) => previous.map((item) => item.id === invoice.id ? invoice : item));
    } catch (error) {
      const updated = await invoiceRequest<Invoice>(`/${invoiceId}`).catch(() => null);
      if (updated?.status === "Processing") {
        setInvoices((previous) => previous.map((item) => item.id === updated.id ? updated : item));
        setMessage("The correction was saved. Select Resume processing to finish verification.");
        return;
      }
      throw error;
    }
  }

  async function saveDecision(note: string) {
    if (!selected || !decisionStatus || !reviewer || selected.status !== "Awaiting approval") return;
    const invoice = await invoiceRequest<Invoice>(`/${selected.id}/decision`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: decisionStatus, note }),
    });
    setInvoices((previous) => previous.map((item) => item.id === invoice.id ? invoice : item));
    setDecisionStatus(null);
    setMessage(`${invoice.number} ${invoice.status.toLowerCase()}. Decision note saved.`);
    setTab("Activity");
  }

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand"><span className="brand-mark"><LayoutGrid size={19} strokeWidth={2.5} /></span><span>FinOps<span className="brand-ai"> AI</span></span></div>
        <div className="workspace-label">Workspace</div>
        <nav aria-label="Workspace">
          <button className={section === "Overview" ? "nav-link active" : "nav-link"} aria-current={section === "Overview" ? "page" : undefined} onClick={() => navigate("Overview")}><LayoutGrid size={18} /> Overview</button>
          <button className={section === "Invoices" ? "nav-link active" : "nav-link"} aria-current={section === "Invoices" ? "page" : undefined} onClick={() => navigate("Invoices")}><Files size={18} /> Invoices</button>
          <button className={section === "Approvals" ? "nav-link active" : "nav-link"} aria-current={section === "Approvals" ? "page" : undefined} onClick={() => navigate("Approvals")}><ListChecks size={18} /> Approvals {pending.length > 0 && <span className="nav-count">{pending.length}</span>}</button>
          <button className={section === "Vendors" ? "nav-link active" : "nav-link"} aria-current={section === "Vendors" ? "page" : undefined} onClick={() => navigate("Vendors")}><Truck size={18} /> Vendors</button>
        </nav>
        <div className="sidebar-bottom">
          <span className="company-avatar">AW</span>
          <div><strong>Aster Works</strong><span>Demo workspace</span></div>
        </div>
      </aside>
      <div className="main">
        <header className="topbar">
          <div className="breadcrumbs"><span>Accounts payable</span><ChevronRight size={14} /><strong>{section}</strong>{selected && <><ChevronRight size={14} /><strong>{selected.number}</strong></>}</div>
          <div className="header-actions">
            <div className="environment"><span /> Demo mode</div>
            {reviewer && <span className="reviewer-name">{reviewer}</span>}
            {reviewer
              ? <button className="text-button" onClick={() => void signOut()}>Sign out</button>
              : <button className="text-button" onClick={() => setSignInOpen(true)}>Reviewer sign-in</button>}
          </div>
        </header>
        <main className={selected ? "content workspace-split" : "content"}>
          <div className="workspace-list">
            {sessionError && <p className="notice warning" role="alert"><CircleAlert size={18} />{sessionError}</p>}
            <div className="page-heading">
              <div><h1>{section}</h1><p>{{
                Overview: "All-time invoice activity for Aster Works.",
                Invoices: "Search invoices and inspect verification evidence.",
                Approvals: "Review verified invoices and record a decision.",
                Vendors: "Browse the vendor register, agreements, and invoice activity.",
              }[section]}</p></div>
              {(section === "Overview" || section === "Invoices") && <button className="button primary" disabled={loadState !== "ready"} onClick={() => setUploadOpen(true)}><Upload size={17} /> Upload invoice</button>}
            </div>
            {loadState === "loading" && <p className="card card-message" role="status">Loading invoices…</p>}
            {loadState === "error" && (
              <div className="card decision-bar">
                <p role="alert">{loadError}</p>
                <button className="button secondary" onClick={() => void loadInvoices()}>Try again</button>
              </div>
            )}
            {loadState === "ready" && (section === "Overview" ? (
              <Overview invoices={invoices} browse={browse} open={(id) => { browse("All invoices"); openInvoice(id); }} />
            ) : section === "Vendors" ? (
              <VendorDirectory invoices={invoices} browse={(vendor) => { navigate("Invoices"); setVendorFilter(vendor); }} />
            ) : (
              <section className="card register" aria-labelledby="register-title">
                <div className="card-head register-head">
                  <h2 id="register-title">{section === "Invoices" ? "Invoice register" : "Approval queue"}</h2>
                  <div className="filters">
                    <label className="search"><Search size={17} /><input ref={searchInput} aria-label="Search invoices" placeholder="Search invoices…" value={search} onChange={(event) => setSearch(event.target.value)} /></label>
                    {section === "Invoices" && (
                      <select aria-label="Filter by status" value={filter} onChange={(event) => { setFilter(event.target.value as InvoiceFilter); setExceptionFilter(""); }}>
                        <option>All invoices</option>{statuses.map((status) => <option key={status}>{status}</option>)}
                      </select>
                    )}
                  </div>
                </div>
                {exceptionFilter && (
                  <div className="exception-filter">
                    <span>{exceptionFilter}</span>
                    <button className="text-button" onClick={() => setExceptionFilter("")}>Clear reason</button>
                  </div>
                )}
                {vendorFilter && (
                  <div className="vendor-filter">
                    <span>{vendorFilter.name} · {vendorFilter.id}</span>
                    <button className="text-button" onClick={() => setVendorFilter(null)}>Clear vendor</button>
                  </div>
                )}
                {visible.length > 0 ? (
                  <InvoiceTable invoices={visible} selectedId={selected?.id} open={openInvoice} />
                ) : (
                  <div className="empty"><Inbox size={26} /><strong>{section === "Approvals" && !pending.length ? "No invoices awaiting approval" : "No matching invoices"}</strong><p>{section === "Approvals" && !pending.length ? "Verified invoices appear here when ready for a decision." : "Try a different search or status filter."}</p></div>
                )}
                <div className="register-footer">Showing {visible.length} of {section === "Approvals" ? pending.length : invoices.length} invoices</div>
              </section>
            ))}
          </div>
          {selected && (
            <aside className="invoice-panel" ref={detailPanel} aria-labelledby="invoice-title"
              onKeyDown={(event) => { if (event.key === "Escape" && !event.defaultPrevented) closeInvoice(); }}>
              <div className="invoice-panel-header">
                <div>
                  <h2 id="invoice-title" ref={detailHeading} tabIndex={-1}>{selected.number}</h2>
                  <p>{selected.vendor} · {selected.amount === null ? selected.filename : money(selected.amount)}</p>
                </div>
                <button className="icon-button" aria-label="Close invoice details" onClick={closeInvoice}><X size={18} /></button>
              </div>
              <div className="invoice-panel-body">
                <StatusBadge status={selected.status} />
                {selected.exception && tab !== "Evidence" && <p className="panel-reason">{selected.exception.title}</p>}
                {message && <p className="notice success" role="status"><CircleCheck size={18} /> {message}</p>}
                <InvoiceDetail key={selected.id} invoice={selected} tab={tab} changeTab={setTab} reviewer={reviewer}
                  signIn={() => setSignInOpen(true)} chooseDecision={setDecisionStatus} retry={() => retryProcessing(selected)} correct={(fields) => correctInvoice(selected.id, fields)} />
              </div>
            </aside>
          )}
        </main>
      </div>
      {signInOpen && <SignInDialog close={() => setSignInOpen(false)} signedIn={(name) => { setReviewer(name); setSessionError(""); }} />}
      {uploadOpen && <UploadDialog close={() => setUploadOpen(false)} add={addUpload} />}
      {selected && decisionStatus && <DecisionDialog invoice={selected} status={decisionStatus} close={() => setDecisionStatus(null)} save={saveDecision} />}
    </div>
  );
}
