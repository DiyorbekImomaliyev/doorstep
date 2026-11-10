/**
 * The Doorstep row's sentence: what needs the seller, in the grid's work order (urgency()), as one
 * line. Built from the orders that need the seller only. It never names an item, and money is summed
 * per currency, never across currencies.
 */
import type { OrderRow } from "../api";
import type { Part } from "../components/ai";
import { money } from "../format";

/** The kinds of work, in the order the grid lists them (urgency 0-5). */
const KINDS: { one: string; many: string; first: string }[] = [
  { one: "dispute", many: "disputes", first: "the PayPal dispute" },
  { one: "refund", many: "refunds", first: "the refund to review" },
  { one: "case", many: "cases", first: "the case to review" },
  { one: "packing check", many: "packing checks", first: "the packing check" },
  { one: "parcel to pack", many: "parcels to pack", first: "the parcel to pack" },
  { one: "parcel to ship", many: "parcels to ship", first: "the parcel to ship" },
];

function stake(rows: OrderRow[]): string {
  const by: Record<string, number> = {};
  rows.forEach((r) => (by[r.currency] = (by[r.currency] ?? 0) + r.money_at_stake_minor));
  return Object.entries(by).filter(([, v]) => v > 0).map(([c, v]) => money(v, c)).join(" + ");
}

/** "**5 need you.** Start with the PayPal dispute (**$89**). Then 3 cases to review (**£149**) and 1 parcel to pack." */
export function guideParts(rows: OrderRow[], urgency: (r: OrderRow) => number, byUrgency: (a: OrderRow, b: OrderRow) => number): Part[] | null {
  const todo = rows.filter((r) => r.needs_action && urgency(r) < KINDS.length).sort(byUrgency);
  if (!todo.length) return null;
  const parts: Part[] = [{ b: `${todo.length} need${todo.length === 1 ? "s" : ""} you.` }, " Start with "];
  const [first, ...rest] = todo;
  parts.push(KINDS[urgency(first)].first);
  if (first.money_at_stake_minor > 0) parts.push(" (", { b: money(first.money_at_stake_minor, first.currency) }, ")");
  parts.push(".");
  const groups = KINDS.map((k, i) => ({ k, rows: rest.filter((r) => urgency(r) === i) })).filter((g) => g.rows.length);
  groups.forEach((g, i) => {
    parts.push(i === 0 ? " Then " : i === groups.length - 1 ? " and " : ", ");
    parts.push(`${g.rows.length} ${g.rows.length === 1 ? g.k.one : g.k.many}`);
    const s = stake(g.rows);
    if (s) parts.push(" (", { b: s }, ")");
  });
  if (groups.length) parts.push(".");
  return parts;
}
