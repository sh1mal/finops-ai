export type Status = "Uploaded" | "Processing" | "Awaiting approval" | "Needs review" | "Approved" | "Rejected";
export type Citation = { document: string; section: string; quote: string; effectiveFrom: string };
type InvoiceLine = { description: string; amount: string; quantity?: number | null; unit?: string | null; unit_price?: string | null };
export type LinePair = {
  invoiceLine: number;
  invoice: InvoiceLine;
  po: { line_number: number; description: string; quantity: number; unit: string; unit_price_cents: number } | null;
  issues: string[];
};
export type Check = { title: string; passed: boolean; skipped?: boolean; detail: string; source?: string; lookup?: string; citation?: Citation; linePairs?: LinePair[] };
export type Evidence = {
  number: string;
  vendor: string;
  amount: number;
  currency: "USD";
  issued: string;
  due: string;
  terms: number;
  po: string;
  filename: string;
  checks: Check[];
  verifiedAt: string;
  pdfSha256: string;
};
export type Decision = {
  status: "Approved" | "Rejected";
  note: string;
  at: string;
  reviewer: string | null;
  evidence: Evidence | null;
};
export type PaymentRequest = { reference: string; amount: number; currency: "USD"; at: string };

type Usage = { inputTokens: number; outputTokens: number; durationMs: number };
export type CorrectionFields = {
  vendor: string; number: string; po: string; issued: string; due: string;
  terms: number; amount: string; line_total: string;
};

type AIExtraction = {
  model: string;
  readFields?: Partial<CorrectionFields>;
  state: string;
  usage?: Usage;
  tax?: string | null;
  totalSource?: "pdf text";
  taxSource?: "pdf text";
  items?: InvoiceLine[];
};
type AIInvestigation = {
  model: string;
  state: string;
  usage: Usage;
  recommendation?: string;
  findings: { source: string; explanation: string }[];
  calls: { tool: string; record: Record<string, unknown> | null }[];
  sources?: Record<string, unknown>;
};

export type Invoice = {
  id: string;
  vendorId: string | null;
  number: string;
  vendor: string;
  initials: string;
  color: "violet" | "teal" | "blue" | "gray";
  amount: number | null;
  received: string;
  verifiedAt: string | null;
  issued: string | null;
  due: string | null;
  terms: number | null;
  po: string | null;
  document: string;
  filename: string;
  checks: Check[];
  status: Status;
  exception: { title: string; detail: string; source: string; citation?: Citation } | null;
  decision: Decision | null;
  decisionPending: boolean;
  manualCorrection?: {
    reviewer: string;
    at: string;
    fields: Omit<CorrectionFields, "amount" | "line_total"> & { amount: number; line_total: number };
  } | null;
  aiExtraction?: AIExtraction | null;
  aiInvestigation?: AIInvestigation | null;
  paymentRequest: PaymentRequest | null;
};

export type Vendor = {
  id: string;
  name: string;
  initials: string;
  color: Invoice["color"];
  terms: number | null;
  reference: string | null;
  purchaseOrderCount: number;
};

export const money = (amount: number) =>
  new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(amount / 100);

export const dateLabel = (date: string) =>
  new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" }).format(new Date(date));

export const timeLabel = (date: string) =>
  new Intl.DateTimeFormat("en-US", { dateStyle: "medium", timeStyle: "short" }).format(new Date(date));

export async function apiRequest<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch("/api" + path, { ...options, cache: "no-store" }).catch((error: unknown) => {
    if (error instanceof Error && error.name === "AbortError") throw error;
    throw new Error("The invoice service is unavailable. Try again shortly.");
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(typeof body?.detail === "string" ? body.detail : "The invoice service could not complete the request.");
  }
  return response.json();
}

export function invoiceRequest<T>(path = "", options?: RequestInit): Promise<T> {
  return apiRequest<T>("/invoices" + path, options);
}
