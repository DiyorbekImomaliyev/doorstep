import { call, type MoneyRow, type VerifyReport } from "../../api";

/** MN-02: one money action, as the desk reads it (a MoneyRow plus what the ledger needs). */
export type DeskRow = MoneyRow & {
  completed_at: string | null;
  simulated: boolean;
  receipts: number;
};

/** Money a carrier paid back on a claim: the ledger's money in. */
export type RecoveryFlow = {
  recovery_id: string; order_ref: string; carrier_name: string;
  recovered_minor: number; currency: string; paid_at: string;
};

export type MoneyDesk = { rows: DeskRow[]; recoveries: RecoveryFlow[]; as_of: string };

export type Step = {
  at: string; step: string; label: string; actor: string;
  rule_id: string | null; rule_version: string | null; approver: string | null; detail: string | null;
};

export type SignedReceipt = { seq: number; kind: string; label: string; hash: string; signature: string; signed_at: string; paid: boolean };

/** MN-05: one money action's whole story: lifecycle, ids, outbox, why it stopped, receipts. */
export type MoneyDetail = {
  action_id: string; case_id: string | null; case_state: string | null; kind: string; status: string;
  amount_minor: number; currency: string; simulated: boolean; external: boolean;
  request_id: string | null; paypal_refund_id: string | null; paypal_status: string | null;
  rule_id: string | null; rule_version: string | null; approver: string | null;
  outbox: { status: string; attempts: number; created_at: string } | null;
  checks: string[]; found_at_paypal: boolean;
  stop: { title: string; text: string; check: string | null } | null;
  timeline: Step[]; receipts: SignedReceipt[]; public_key: string; can_redecide: boolean;
};

const desk = (t: string) => `/api/seller/${t}/money-desk`;

export const moneyApi = {
  desk: (t: string) => call<MoneyDesk>("GET", desk(t)),
  detail: (t: string, id: string) => call<MoneyDetail>("GET", `${desk(t)}/${encodeURIComponent(id)}`),
  /** The public receipt check (PR-02): the server recomputes the chain up to this case. */
  verify: (caseId: string) => call<VerifyReport>("GET", `/cases/${encodeURIComponent(caseId)}/verify`),
  /** Decide again on a held case (the existing case action; money still goes only through app/policy). */
  redecide: (t: string, caseId: string) =>
    call<{ case_id: string; state: string; money_action_id: string | null }>("POST", `/api/seller/${t}/cases/${encodeURIComponent(caseId)}/re-check`),
};

const b64 = (s: string) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));

/** Checks one receipt's Ed25519 signature in this browser, with the shop's public key.
 * null when the browser has no Ed25519 in Web Crypto (the server's check still stands). */
export async function signatureOk(r: SignedReceipt, publicKey: string): Promise<boolean | null> {
  try {
    const key = await crypto.subtle.importKey("raw", b64(publicKey), { name: "Ed25519" }, false, ["verify"]);
    return await crypto.subtle.verify({ name: "Ed25519" }, key, b64(r.signature), new TextEncoder().encode(r.hash));
  } catch {
    return null;
  }
}

export type Verdict = {
  ok: boolean;
  /** Receipts this action has, each checked by the server and (when it can) this browser. */
  checked: { seq: number; label: string; server: boolean; browser: boolean | null }[];
  chain: number;
  reason: string | null;
};

/** MN-06: the server recomputes the shared chain; this browser re-checks each signature. */
export async function verifyReceipts(caseId: string, receipts: SignedReceipt[], publicKey: string): Promise<Verdict> {
  const report = await moneyApi.verify(caseId);
  const bySeq = new Map(report.receipts.map((r) => [r.seq, r]));
  const checked = await Promise.all(receipts.map(async (r) => {
    const s = bySeq.get(r.seq);
    return { seq: r.seq, label: r.label, server: !!s && s.valid && s.hash === r.hash, browser: await signatureOk(r, publicKey) };
  }));
  const ok = report.status === "ok" && checked.length > 0 && checked.every((c) => c.server && c.browser !== false);
  const reason = report.status === "broken" ? `Receipt #${report.first_broken_seq}: ${report.reason}`
    : report.status === "not_run" ? report.reason
    : checked.some((c) => c.browser === false) ? "Signature does not match the shop key"
    : checked.some((c) => !c.server) ? "Receipt not found in the chain" : null;
  return { ok, checked, chain: report.checked, reason };
}
