import { call } from "../../api";

/** DS-02: one PayPal dispute with its full story, from GET /api/seller/{token}/disputes/desk.
 * PayPal fields are only what its dispute webhooks sent; a field PayPal never sent is null. */
export type Stage = "needs_reply" | "evidence" | "offer" | "resolved";
export type CheckState = "pass" | "partial" | "fail" | "missing";
export type CheckId = "verdict" | "address" | "delivered" | "tracking" | "replied";

export type ProtectionCheck = { id: CheckId; state: CheckState; detail: string };
export type Protection = { status: string | null; categories: string[]; checks: ProtectionCheck[]; met: number; total: number };
export type PayPalFacts = {
  reason: string | null; status: string | null; stage: string | null; channel: string | null;
  response_due_at: string | null; amount_minor: number | null; currency: string | null;
  outcome_code: string | null; updated_at: string | null; events: number;
};
export type EvidenceSent = {
  status: string; request_id: string | null; queued_at: string; sent_at: string | null;
  evidence_type: string | null; carrier: string | null; tracking_number: string | null; notes: string[];
};
export type OfferMade = { status: string; request_id: string | null; amount_minor: number; currency: string; queued_at: string; sent_at: string | null };
export type Outcome = { code: string | null; result: "won" | "lost" | "other"; at: string | null };
export type DeskEvent = { at: string; kind: "paypal" | "doorstep" | "agent" | "money" | "receipt"; title: string; detail: string };

export type DeskRow = {
  dispute_id: string; paypal_dispute_id: string; simulated: boolean;
  order_id: string; order_ref: string; item: string; case_id: string | null; case_state: string | null;
  status: "open" | "resolved" | string; stage: Stage; opened_at: string;
  amount_minor: number; currency: string; amount_from: "paypal" | "order";
  claim: string | null; failure_point: string | null; confidence: string | null;
  rule_id: string | null; rule_version: string | null; reasons: string[]; scan_verdict: string | null;
  recommended: "evidence" | "offer" | null; can_send_evidence: boolean; offer_minor: number | null;
  protection: Protection; paypal: PayPalFacts; evidence: EvidenceSent | null; offer: OfferMade | null;
  outcome: Outcome | null; receipts: number; timeline: DeskEvent[];
};

export const disputesDesk = (token: string) => call<DeskRow[]>("GET", `/api/seller/${token}/disputes/desk`);
