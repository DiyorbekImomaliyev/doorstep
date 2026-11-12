import { ArrowUpRight } from "lucide-react";
import { StatusPill } from "../LedgerGrid";
import type { DeskRow } from "./claimsApi";
import { useDesk } from "./desk";
import { buyerOf } from "./model";

/** FP-R1: the buyer's side of the same parcel (grid column, card and claim story). When it waits
 *  for the seller (Needs you, a dispute) it is a link that opens the case. */
export default function BuyerCell({ r: given }: { r: DeskRow }) {
  const desk = useDesk();
  // The grid refreshes a cell when its own value changes; the latest row comes from the desk.
  const r = desk.rows.get(given.recovery_id) ?? given;
  const b = buyerOf(r);
  if (!b) return <span className="faint">—</span>;
  if (!b.needsYou) return <span className="cd-buyer-pill" title={b.hint}><StatusPill tone={b.tone}>{b.label}</StatusPill></span>;
  return (
    <button type="button" data-own-click className={`cd-buyer badge ${b.tone}`} title={`${b.hint}. Open the case.`}
      aria-label={`Buyer: ${b.label}. Open the case for ${r.item}`} onClick={() => desk.openCase(r)}>
      {b.label}<ArrowUpRight aria-hidden />
    </button>
  );
}
