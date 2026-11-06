// Thin typed client for the Doorstep API. Same origin in production; Vite proxies in dev.

export type Money = { amount_minor: number; currency: string };

export type TimelineItem = { at: string; kind: string; title: string; detail: string };

export type BuyerView = {
  order_ref: string;
  item: string;
  amount_minor: number;
  currency: string;
  status: string;
  state: string;
  /** Set when a capture did not complete: FAILED (our call failed), DECLINED or PENDING. */
  capture_status?: string | null;
  method: string | null;
  tracking_number: string | null;
  carrier: string | null;
  choice_offered: string[];
  choice_made: string | null;
  can_confirm: boolean;
  can_report: boolean;
  address_check: string;
  address_issues: { code: string; message: string }[];
  address: Address | null;
  seller_name: string;
  case_open: boolean;
  progress_state: string;
  case_message: string | null;
  timeline: TimelineItem[];
  rights: string;
};

export type ReportOut = { case_id: string; status: string; message: string; rights: string };

export type SealView = {
  order_ref: string;
  item: string;
  seller_name: string;
  status: "not_shipped" | "ready" | "confirmed" | "case_open";
  scanned: boolean;
  counted: boolean;
  seller_browser: boolean;
};

export type SealCard = { order_ref: string; url: string; svg: string };

export type AuditCheck = {
  id: "location" | "sequence" | "batch" | "reported_soon";
  result: "contradicts" | "supports" | "unknown" | "context";
  finding: string;
};

export type ScanAudit = { verdict: "contradicted" | "consistent" | "unverified" | "no_scan"; checks: AuditCheck[] };

export type RecoveryView = {
  recovery_id: string;
  case_id: string;
  status: "PREPARED" | "SUBMITTED" | "PAID" | "DENIED" | "WITHDRAWN" | "EXPIRED";
  carrier: string;
  carrier_name: string;
  carrier_known: boolean;
  failure_point: string;
  claim_minor: number;
  currency: string;
  cover_minor: number | null;
  deadline_at: string;
  window_days: number;
  terms_source: string;
  terms_url: string;
  terms_note: string;
  terms_rule: string;
  sentences: { text: string; fact_ids: string[] }[];
  facts: { id: string; kind: string; text: string }[];
  pack_source: string;
  recovered_minor: number | null;
  submitted_at: string | null;
  resolved_at: string | null;
  actions: ("submitted" | "withdraw" | "paid" | "refused")[];
};

export type RecoverySummary = {
  recovered_minor: Record<string, number>;
  open_claims: number;
  open_claim_minor: Record<string, number>;
};

export type Counterfactual = {
  rule_id: string | null;
  possession_proven: boolean;
  present: string[];
  would_change: string[];
  would_change_together: string[];
  scan_discounted: boolean;
  text: string;
};

export type PreventionTip = {
  method: string;
  currency: string;
  band: string;
  cases: number;
  orders: number;
  money_minor: number;
  suggested_method: string;
  text: string;
};

export type Address = {
  name?: string;
  line1: string;
  line2?: string;
  city: string;
  postal_code: string;
  country: string;
};

export type EvidenceScore = { score: number; present: string[]; missing: string[] };

export type OrderRow = {
  order_id: string;
  order_ref: string;
  item: string;
  buyer_area: string;
  buyer_country: string;
  amount_minor: number;
  currency: string;
  method: string | null;
  recommended_method: string | null;
  risk: number | null;
  state: string;
  status: string;
  address_check: string;
  evidence: EvidenceScore;
  case_id: string | null;
  case_state: string | null;
  failure_point: string | null;
  confidence: string | null;
  route: string | null;
  rule_id: string | null;
  money_at_stake_minor: number;
  refund_status: string | null;
  needs_action: boolean;
  last_update: string;
  simulated: boolean;
  packing_verdict: "match" | "mismatch" | "check" | null;
  packing_message: string | null;
  packing_cleared: boolean;
  delivery_risk: number | null;
  risk_reasons: string[];
  seller_protection: string | null;
};

export type AgentRun = {
  case_id: string;
  planner: string;
  outcome: string | null;
  steps: { tool: string; reason: string; summary: string }[];
};

export type PackingResult = {
  verdict: "match" | "mismatch" | "check";
  attribute: string | null;
  message: string;
  reasons: string[];
  order: OrderRow;
};

export type DecisionView = {
  decision_id: string;
  failure_point: string | null;
  confidence: string | null;
  route: string;
  fix: string | null;
  refund_amount_minor: number | null;
  currency: string | null;
  rule_id: string | null;
  rule_version: string | null;
  reasons: string[];
  reason_codes: string[];
  explanation: string;
  buyer_message: string | null;
  claim_letter: string | null;
  explainer_source: string | null;
  missing_signals: string[];
};

export type CaseView = {
  case_id: string;
  state: string;
  reason: string;
  reported_by: string;
  opened_at: string;
  order: OrderRow;
  decision: DecisionView | null;
  money: { kind: string; amount_minor: number; currency: string; status: string; refund_id: string | null }[];
  actions: string[];
  receipts: number;
  timeline: TimelineItem[];
  disclaimer: string;
  scan_audit: ScanAudit | null;
  recovery: RecoveryView | null;
  counterfactual: Counterfactual | null;
};

export type VerifyReport = {
  status: "ok" | "broken" | "not_run";
  checked: number;
  first_broken_seq: number | null;
  reason: string | null;
  receipts: { seq: number; kind: string; hash: string; valid: boolean }[];
};

/** AP: autopilot rules learned from the seller's own approvals. */
export type RuleSuggestion = {
  failure_point: string; currency: string; max_amount_minor: number; approved: number; window_days: number; case_ids: string[];
};
export type SellerRule = {
  id: string; failure_point: string; currency: string; max_amount_minor: number; status: string; precedents: number; updated_at: string;
};
export type Autopilot = { rules: SellerRule[]; suggestions: RuleSuggestion[] };

export type Quote = {
  options: { method: string; label: string; amount: Money }[];
  recommended: string;
  risk: number;
  reasons: string[];
};

export type SeedOut = {
  tag: string;
  seller_token: string;
  seller_url: string;
  buyers: Record<string, string>;
  summary: { tag: string; seller_id: string; orders: { key: string; order_id: string; buyer: string; item: string }[] };
};

export type Health = {
  ok: boolean;
  environment: string;
  rules: string[];
  simulated: Record<string, string>;
  model: string;
};

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

export type PolicyAnswer = {
  answer: string;
  citations: { id: string; source: string; text: string }[];
  mode: "model" | "quoted passages" | "rule file";
  disclaimer: string;
};

export const api = {
  health: () => call<Health>("GET", "/healthz"),
  seed: (scratch: boolean) => call<SeedOut>("POST", `/demo/seed?scratch=${scratch ? 1 : 0}`),
  verify: (caseId: string) => call<VerifyReport>("GET", `/cases/${encodeURIComponent(caseId)}/verify`),

  quote: (sellerId: string, amountMinor: number, currency: string, country: string) =>
    call<Quote>("POST", "/api/checkout/quote", {
      seller_id: sellerId, amount_minor: amountMinor, currency, buyer_country: country,
    }),
  start: (sellerId: string, amountMinor: number, currency: string, country: string, method: string, item: string) =>
    call<{ order_id: string; approve_url: string; buyer_token: string; buyer_url: string }>(
      "POST", "/api/checkout/start",
      { seller_id: sellerId, amount_minor: amountMinor, currency, buyer_country: country, method, item_description: item },
    ),
  paypalReturn: (paypalOrderId: string) =>
    call<{ buyer_token: string; buyer_url: string }>("POST", "/api/checkout/return", { paypal_order_id: paypalOrderId }),

  buyer: (t: string) => call<BuyerView>("GET", `/api/buyer/${t}`),
  gotIt: (t: string) => call<BuyerView>("POST", `/api/buyer/${t}/got-it`),
  notHere: (t: string, reason: string, note: string) =>
    call<ReportOut>("POST", `/api/buyer/${t}/not-here`, { reason, note }),

  seal: (t: string) => call<SealView>("GET", `/api/seal/${t}`),
  sealConfirm: (t: string) => call<SealView>("POST", `/api/seal/${t}/confirm`),
  sealReport: (t: string, reason: "damaged" | "wrong_item" | "missing_item") =>
    call<ReportOut>("POST", `/api/seal/${t}/report`, { reason }),
  choose: (t: string, option: string) => call<BuyerView>("POST", `/api/buyer/${t}/choice`, { option }),
  fixAddress: (t: string, address: Address) =>
    call<{ valid: boolean; issues: { code: string; message: string }[] }>("POST", `/api/buyer/${t}/address`, address),
  policyAnswer: (t: string, caseId: string, question?: string) =>
    call<PolicyAnswer>("POST", `/api/seller/${t}/policy/cases/${encodeURIComponent(caseId)}`, question ? { question } : undefined),
  agentLink: (t: string) =>
    call<{ order_token: string; mcp_url: string; tool: string; expires_in_days: number }>(
      "POST", `/api/buyer/${t}/agent-link`,
    ),
  keepAddress: (t: string) => call<BuyerView>("POST", `/api/buyer/${t}/keep-address`),

  me: (t: string) => call<{ seller_id: string; name: string; region: string }>("GET", `/api/seller/${t}/me`),
  autopilot: (t: string) => call<Autopilot>("GET", `/api/seller/${t}/autopilot`),
  autopilotOn: (t: string, failure_point: string, currency: string) =>
    call<SellerRule>("POST", `/api/seller/${t}/autopilot`, { failure_point, currency }),
  autopilotDismiss: (t: string, failure_point: string, currency: string) =>
    call<SellerRule>("POST", `/api/seller/${t}/autopilot/dismiss`, { failure_point, currency }),
  autopilotOff: (t: string, ruleId: string) =>
    call<SellerRule>("POST", `/api/seller/${t}/autopilot/${encodeURIComponent(ruleId)}/off`),
  orders: (t: string) => call<OrderRow[]>("GET", `/api/seller/${t}/orders`),
  casePage: (t: string, caseId: string) => call<CaseView>("GET", `/api/seller/${t}/cases/${caseId}`),
  act: (t: string, caseId: string, action: "approve" | "decline" | "send-evidence") =>
    call<{ case_id: string; state: string; money_action_id: string | null }>(
      "POST", `/api/seller/${t}/cases/${caseId}/${action}`,
    ),
  ship: (t: string, orderId: string, trackingNumber: string, weight: number | null) =>
    call<OrderRow>("POST", `/api/seller/${t}/orders/${orderId}/ship`, {
      carrier: "shippo", tracking_number: trackingNumber, label_weight_g: weight,
    }),
  runAgent: (t: string, caseId: string) =>
    call<AgentRun>("POST", `/api/seller/${t}/agent/cases/${caseId}`),
  packingCheck: (t: string, orderId: string, imageB64: string, mediaType: string) =>
    call<PackingResult>("POST", `/api/seller/${t}/orders/${orderId}/packing-check`, {
      image_b64: imageB64, media_type: mediaType,
    }),
  packingClear: (t: string, orderId: string) =>
    call<OrderRow>("POST", `/api/seller/${t}/orders/${orderId}/packing-clear`),
  buyerLink: (t: string, orderId: string) =>
    call<{ buyer_token: string; buyer_url: string }>("GET", `/api/seller/${t}/orders/${orderId}/buyer-link`),
  sealCard: (t: string, orderId: string) => call<SealCard>("GET", `/api/seller/${t}/orders/${orderId}/seal`),
  recoveryMark: (t: string, caseId: string, mark: RecoveryView["actions"][number], amountMinor?: number) =>
    call<RecoveryView>(
      "POST", `/api/seller/${t}/cases/${caseId}/recovery/${mark}`,
      amountMinor === undefined ? undefined : { amount_minor: amountMinor },
    ),
  recoveries: (t: string) => call<RecoverySummary>("GET", `/api/seller/${t}/recoveries`),
  prevention: (t: string) => call<PreventionTip[]>("GET", `/api/seller/${t}/prevention`),
};
