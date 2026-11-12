import { useEffect, useRef } from "react";
import { ChevronDown } from "lucide-react";
import { money } from "../../format";
import { StatusPill } from "../LedgerGrid";
import type { DeskRow } from "./claimsApi";
import ClaimDetail from "./ClaimDetail";
import { DueCell } from "./ClaimsGrid";
import { buyerOf, carrierOf, isMade, joinMoney, statusOf, sumBy, type Seg } from "./model";

/** CL-07: the desk on a phone: one card per claim; a tap opens its story and actions in place. */
export default function ClaimCards({ rows, seg, open, onOpen }: { rows: DeskRow[]; seg: Seg; open: string | null; onOpen: (id: string | null) => void }) {
  const shown = rows.filter((r) => seg === "all" || r.status === seg);
  const list = useRef<HTMLUListElement>(null);
  useEffect(() => {
    if (open) list.current?.querySelector(`[data-claim="${open}"]`)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [open]);
  const made = shown.filter(isMade);
  const claimed = sumBy(made, (r) => r.claim_minor);
  const paid = sumBy(made, (r) => r.recovered_minor ?? 0);
  if (!shown.length) return <p className="lg-empty">{seg === "all" ? "No claims" : `No ${statusOf(seg).label.toLowerCase()} claims`}</p>;
  return (
    <>
      <ul className="ledger-cards cd-cards" ref={list}>
        {shown.map((r) => {
          const s = statusOf(r.status);
          const buyer = buyerOf(r);
          const isOpenCard = open === r.recovery_id;
          return (
            <li key={r.recovery_id} data-claim={r.recovery_id} className={isOpenCard ? "open" : undefined}>
              <button type="button" className="ledger-card" aria-expanded={isOpenCard} onClick={() => onOpen(isOpenCard ? null : r.recovery_id)}>
                <span className="lc-top"><span className="lc-item">{r.item || "Order"}</span><span className="lc-money num">{money(r.claim_minor, r.currency)}</span></span>
                <span className="lc-meta">{r.order_ref} · {carrierOf(r)}{r.simulated ? " · demo" : ""}</span>
                <span className="lc-bottom">
                  <StatusPill tone={s.tone}>{s.label}</StatusPill>
                  <DueCell r={r} />
                  {r.recovered_minor != null && <span className="num lc-paid">Paid {money(r.recovered_minor, r.currency)}</span>}
                  {buyer && <span className="cd-buyer-mini" title={buyer.hint}>Buyer<StatusPill tone={buyer.tone}>{buyer.label}</StatusPill></span>}
                  <ChevronDown className="cd-card-chev" aria-hidden />
                </span>
              </button>
              {isOpenCard && <ClaimDetail id={r.recovery_id} />}
            </li>
          );
        })}
      </ul>
      {made.length > 0 && (
        <div className="ledger-totals"><span className="num">Total: {joinMoney(claimed)} claimed · {joinMoney(paid)} paid</span></div>
      )}
    </>
  );
}
