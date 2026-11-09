import type { ReactNode } from "react";
import {
  Coffee, Footprints, Gem, Headphones, House, Lamp, Lock, Package, PenLine, Shirt, Watch,
} from "lucide-react";
import type { OrderRow } from "../api";
import { FAILURE_POINT, METHOD, ROUTE, pct, riskTone, type Tone } from "../format";

/** Cell renderers for the Control Room grid, after AG Grid's finance demo: an identity tile,
 * a compact progress track in place of a sparkline, values in soft pills, numbers right-aligned. */

const ITEM_KINDS: [RegExp, ReactNode, string][] = [
  [/sneaker|trainer|shoe|boot/i, <Footprints />, "Footwear"],
  [/jacket|coat|shirt|\btee\b|hat|glove|scarf|dress|jumper|sweater|hoodie/i, <Shirt />, "Clothing"],
  [/lamp|light/i, <Lamp />, "Home"],
  [/headphone|earbud|speaker/i, <Headphones />, "Audio"],
  [/mug|cup|coffee|tea/i, <Coffee />, "Kitchen"],
  [/watch|clock/i, <Watch />, "Accessories"],
  [/ring|necklace|earring|bracelet|jewel/i, <Gem />, "Jewellery"],
];

export function itemKind(item: string): { icon: ReactNode; label: string } {
  const k = ITEM_KINDS.find(([re]) => re.test(item));
  return k ? { icon: k[1], label: k[2] } : { icon: <Package />, label: "General" };
}
const STEPS = ["Paid", "Packed", "Shipped", "Delivered", "Received"];
const STEP_OF: Record<string, number> = {
  CREATED: 0, PAID: 1, PACKED: 2, SHIPPED: 3, IN_TRANSIT: 3, EXCEPTION: 3,
  DELIVERED: 4, PICKED_UP: 4, CONFIRMED: 5, CASE_OPEN: 4, RESOLVED: 4,
};

function journeyTone(r: OrderRow): Tone {
  if (r.state === "EXCEPTION") return "warn";
  if (r.case_state) return ROUTE[r.case_state]?.tone ?? "accent";
  if (r.state === "CONFIRMED") return "good";
  return "neutral";
}

/** The cause, short enough for the Journey cell (the full wording is in the case). */
const SHORT_CAUSE: Record<string, string> = {
  STOLEN_AFTER_DELIVERY: "Missing after handover",
  CLAIM_CONTRADICTS_EVIDENCE: "Report vs evidence",
};

/** The order's journey as five short segments; the last one carries a colour only when
 * something needs attention (exception, open case) or the buyer confirmed receipt. */
export function JourneyCell({ r }: { r: OrderRow }) {
  const done = STEP_OF[r.state] ?? 0;
  const tone = journeyTone(r);
  // An open case says what went wrong (its likely cause), not "Case open" on every row; the
  // status stays in the tooltip.
  const cause = r.case_id && r.failure_point ? SHORT_CAUSE[r.failure_point] ?? FAILURE_POINT[r.failure_point] : null;
  return (
    <div className="journey-cell" title={[r.status, ...STEPS.map((s, i) => `${i < done ? "✓" : "·"} ${s}`)].join("\n")}>
      <span className={`journey-track tone-${tone}`} aria-hidden>
        {STEPS.map((s, i) => <span key={s} className={i < done ? (i === done - 1 ? "on last" : "on") : ""} />)}
      </span>
      <span className="journey-label">{cause ?? r.status}</span>
    </div>
  );
}

const METHOD_ICON: Record<string, ReactNode> = { home: <House />, locker: <Lock />, signature: <PenLine /> };

export function DeliveryCell({ r }: { r: OrderRow }) {
  if (!r.method) return <span className="faint">—</span>;
  const suggested = r.recommended_method && r.recommended_method !== r.method ? r.recommended_method : null;
  return (
    <div className="cell-two">
      <span className="method"><span aria-hidden>{METHOD_ICON[r.method]}</span>{METHOD[r.method] ?? r.method}</span>
      {suggested && <span className="sub">Suggested: {METHOD[suggested] ?? suggested}</span>}
    </div>
  );
}

export function RiskCell({ r }: { r: OrderRow }) {
  if (r.delivery_risk == null) return <span className="faint" title="Delivery risk is a forecast made before the parcel ships">—</span>;
  const tone = riskTone(r.delivery_risk);
  return (
    <span className={`pill ${tone === "neutral" ? "plain" : tone}`} title={r.risk_reasons.join("\n")}>
      {pct(r.delivery_risk)}
    </span>
  );
}
