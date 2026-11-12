import { Landmark } from "lucide-react";
import type { ClaimRow, OrderRow } from "../api";
import { money, type Tone } from "../format";

/**
 * FP-W1: the buyer side and the carrier side, linked. An order whose case also has a carrier claim
 * carries it on its grid row, from the claims ledger Seller.tsx already loads (no extra request).
 */
export type RowClaim = Pick<ClaimRow, "recovery_id" | "status" | "claim_minor" | "recovered_minor" | "currency" | "deadline_at" | "window_days" | "carrier_name">;
export type ClaimedRow = OrderRow & { claim?: RowClaim };

/** The statuses worth a tag: a claim that was withdrawn or expired was never made. */
const SHOWN: Record<string, { label: string; tone: Tone }> = {
  PREPARED: { label: "Ready", tone: "accent" },
  SUBMITTED: { label: "Submitted", tone: "warn" },
  PAID: { label: "Paid", tone: "good" },
  DENIED: { label: "Refused", tone: "bad" },
};

export const claimOf = (r: OrderRow | undefined): RowClaim | undefined => {
  const c = (r as ClaimedRow | undefined)?.claim;
  return c && SHOWN[c.status] ? c : undefined;
};

/** The rows with their claim attached; rows without one stay the same object (no needless redraw). */
export function withClaims(rows: OrderRow[], claims: ClaimRow[] | null | undefined): OrderRow[] {
  if (!claims?.length) return rows;
  const by = new Map<string, RowClaim>();
  for (const c of claims) {
    const old = by.get(c.order_id);
    // One claim per case in practice; if an order has two, the one still open wins.
    if (!old || (!SHOWN[old.status] && SHOWN[c.status])) by.set(c.order_id, c);
  }
  return rows.map((r) => {
    const c = by.get(r.order_id);
    const was = (r as ClaimedRow).claim;
    if (!c && !was) return r;
    return { ...r, claim: c } as ClaimedRow;
  });
}

const amountOf = (c: RowClaim) => (c.status === "PAID" && c.recovered_minor != null ? c.recovered_minor : c.claim_minor);

/** "Claim £75 · Ready": opens the Claims page. `short` drops the word Claim (the column says it). */
export function ClaimTag({ claim, short = false, onOpen }: { claim: RowClaim; short?: boolean; onOpen?: (id: string) => void }) {
  const s = SHOWN[claim.status];
  if (!s) return null;
  const amount = money(amountOf(claim), claim.currency);
  return (
    <button type="button" data-own-click className={`claim-tag tone-${s.tone}`}
      title={`Carrier claim with ${claim.carrier_name}: ${amount}, ${s.label.toLowerCase()}. Open Claims.`}
      aria-label={`Carrier claim ${amount}, ${s.label}. Open Claims`}
      onClick={(e) => { e.stopPropagation(); onOpen?.(claim.recovery_id); }}>
      {!short && <Landmark aria-hidden />}
      <span className="num">{short ? amount : `Claim ${amount}`}</span>
      <span className="claim-tag-status">{s.label}</span>
    </button>
  );
}
