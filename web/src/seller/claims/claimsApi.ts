import { call, type RecoveryView } from "../../api";

export type ClaimStatus = RecoveryView["status"];
export type Mark = RecoveryView["actions"][number];

/** One status change of a claim and who made it (CL-04). */
export type DeskEvent = {
  status: ClaimStatus;
  at: string;
  by: "you" | "doorstep" | "carrier" | "demo";
  note: string | null;
  amount_minor: number | null;
};

/** CL-02: one carrier claim with its whole story, from GET /api/seller/{token}/claims/desk. */
export type DeskRow = {
  recovery_id: string; case_id: string; order_id: string; order_ref: string; item: string;
  carrier: string; carrier_name: string; carrier_known: boolean; failure_point: string;
  claim_minor: number; cover_minor: number | null; item_minor: number; currency: string;
  status: ClaimStatus; deadline_at: string; window_days: number; window_starts: string; service_basis: string;
  recovered_minor: number | null; created_at: string; submitted_at: string | null; resolved_at: string | null;
  terms_url: string; terms_note: string; terms_rule: string;
  sentences: RecoveryView["sentences"]; facts: RecoveryView["facts"]; pack_source: string;
  case_state: string | null; refunded_minor: number; simulated: boolean;
  actions: Mark[]; history: DeskEvent[];
};

export type ClaimsDesk = { now: string; rows: DeskRow[] };

export const claimsApi = {
  desk: (t: string) => call<ClaimsDesk>("GET", `/api/seller/${t}/claims/desk`),
  /** The existing RC-05 endpoint: Submitted / Carrier paid (amount) / Carrier refused / Withdraw. */
  mark: (t: string, caseId: string, mark: Mark, amountMinor?: number) =>
    call<RecoveryView>(
      "POST", `/api/seller/${t}/cases/${caseId}/recovery/${mark}`,
      amountMinor === undefined ? undefined : { amount_minor: amountMinor },
    ),
  /** RC-08: the model drafts a fact-only pack the first time it is opened; code checks each sentence. */
  draft: (t: string, caseId: string) => call<RecoveryView>("POST", `/api/seller/${t}/cases/${caseId}/recovery/draft`),
};

/** The row after a mark, from the endpoint's answer, until the next read brings the audit row. */
export function afterMark(r: DeskRow, v: RecoveryView, at: string): DeskRow {
  return {
    ...r,
    status: v.status, recovered_minor: v.recovered_minor, submitted_at: v.submitted_at, resolved_at: v.resolved_at,
    actions: v.actions, sentences: v.sentences, facts: v.facts, pack_source: v.pack_source,
    history: [...r.history, { status: v.status, at, by: "you", note: null, amount_minor: v.status === "PAID" ? v.recovered_minor : null }],
  };
}
