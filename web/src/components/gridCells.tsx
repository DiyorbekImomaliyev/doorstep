import type { ReactNode } from "react";
import {
  Coffee, Gem, Headphones, House, Lamp, Lock, Package, PenLine, Shirt, Watch,
} from "lucide-react";
import type { OrderRow } from "../api";
import { METHOD, ROUTE, money, pct, riskTone, type Tone } from "../format";

/** Cell renderers for the Control Room grid, after AG Grid's finance demo: an identity tile,
 * a compact progress track in place of a sparkline, values in soft pills, numbers right-aligned. */

const ITEM_ICONS: [RegExp, ReactNode][] = [
  [/jacket|coat|shirt|hat|glove|scarf|dress|jumper|sweater|hoodie/i, <Shirt />],
  [/lamp|light/i, <Lamp />],
  [/headphone|earbud|speaker/i, <Headphones />],
  [/mug|cup|coffee|tea/i, <Coffee />],
  [/watch|clock/i, <Watch />],
  [/ring|necklace|earring|bracelet|jewel/i, <Gem />],
];

function itemIcon(item: string): ReactNode {
  return ITEM_ICONS.find(([re]) => re.test(item))?.[1] ?? <Package />;
}

export function ItemCell({ r }: { r: OrderRow }) {
  return (
    <div className="item-cell">
      <span className="item-tile" aria-hidden>{itemIcon(r.item)}</span>
      <span className="cell-two">
        <span className="item-name">{r.item}</span>
        <span className="sub num">{r.order_ref} · {r.buyer_area || r.buyer_country}</span>
      </span>
    </div>
  );
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

/** The order's journey as five short segments; the last one carries a colour only when
 * something needs attention (exception, open case) or the buyer confirmed receipt. */
export function JourneyCell({ r }: { r: OrderRow }) {
  const done = STEP_OF[r.state] ?? 0;
  const tone = journeyTone(r);
  return (
    <div className="journey-cell" title={STEPS.map((s, i) => `${i < done ? "✓" : "·"} ${s}`).join("\n")}>
      <span className={`journey-track tone-${tone}`} aria-hidden>
        {STEPS.map((s, i) => <span key={s} className={i < done ? (i === done - 1 ? "on last" : "on") : ""} />)}
      </span>
      <span className="journey-label">{r.status}</span>
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
  if (r.delivery_risk == null) return <span className="faint">—</span>;
  const tone = riskTone(r.delivery_risk);
  return (
    <span className={`pill ${tone === "neutral" ? "plain" : tone}`} title={r.risk_reasons.join("\n")}>
      {pct(r.delivery_risk)}
    </span>
  );
}

export function StakeCell({ r }: { r: OrderRow }) {
  if (!r.money_at_stake_minor) return <span className="faint">—</span>;
  return <span className="pill accent">{money(r.money_at_stake_minor, r.currency)}</span>;
}
