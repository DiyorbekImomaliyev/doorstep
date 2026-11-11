import type { Tone } from "../../format";
import type { DeskRow, RecoveryFlow } from "./moneyApi";

/** The ledger's arithmetic: per currency, always. £ and $ are never added together. */

export const KIND: Record<string, string> = { refund: "Refund", partial_refund: "Partial refund", offer: "Dispute offer", evidence: "Evidence" };
export const STATUS: Record<string, { label: string; tone: Tone; hint: string }> = {
  completed: { label: "Completed", tone: "good", hint: "PayPal completed it" },
  simulated: { label: "Simulated", tone: "accent", hint: "Demo order: no real money moved" },
  pending: { label: "Pending", tone: "warn", hint: "PayPal accepted it; the money has not landed yet" },
  queued: { label: "Queued", tone: "neutral", hint: "In the outbox, waiting for its live re-check" },
  blocked: { label: "Blocked", tone: "bad", hint: "The live re-check stopped it: no money moved" },
  failed: { label: "Failed", tone: "bad", hint: "PayPal did not complete it" },
  unconfirmed: { label: "Unconfirmed", tone: "warn", hint: "Sent once; PayPal has not confirmed it" },
};
/** The filter chips, in the order a finance lead reads them. Only real statuses. */
export const STATUS_ORDER = ["completed", "simulated", "pending", "queued", "blocked", "failed", "unconfirmed"] as const;
export const statusOf = (s: string) => STATUS[s] ?? { label: s, tone: "neutral" as Tone, hint: s };

const REFUNDS = ["refund", "partial_refund"];
export const isRefund = (r: { kind: string }) => REFUNDS.includes(r.kind);
/** Money that left: a refund PayPal completed, or a demo refund that was simulated. */
export const hasLeft = (r: DeskRow) => isRefund(r) && (r.status === "completed" || r.status === "simulated");
export const inFlight = (r: { status: string }) => r.status === "queued" || r.status === "pending";

export type Figures = {
  currency: string;
  refunded: number; simulated: number; refunds: number;
  recovered: number; claims: number;
  net: number;
  atPayPal: number; inFlight: number;
};

const sum = <T,>(xs: T[], f: (x: T) => number) => xs.reduce((a, x) => a + f(x), 0);

/** Every currency the shop has money in, the busiest first (GBP on a tie). */
export function currencies(rows: DeskRow[], recoveries: RecoveryFlow[]): string[] {
  const volume = new Map<string, number>();
  for (const r of rows) volume.set(r.currency, (volume.get(r.currency) ?? 0) + (hasLeft(r) ? r.amount_minor : 0));
  for (const r of recoveries) volume.set(r.currency, (volume.get(r.currency) ?? 0) + r.recovered_minor);
  return [...volume.entries()].sort((a, b) => b[1] - a[1] || (a[0] === "GBP" ? -1 : b[0] === "GBP" ? 1 : a[0].localeCompare(b[0]))).map(([c]) => c);
}

export function figures(rows: DeskRow[], recoveries: RecoveryFlow[], currency: string): Figures {
  const mine = rows.filter((r) => r.currency === currency);
  const left = mine.filter(hasLeft);
  const back = recoveries.filter((r) => r.currency === currency);
  const flying = mine.filter(inFlight);
  const refunded = sum(left, (r) => r.amount_minor);
  const recovered = sum(back, (r) => r.recovered_minor);
  return {
    currency,
    refunded, simulated: sum(left.filter((r) => r.simulated), (r) => r.amount_minor), refunds: left.length,
    recovered, claims: back.length,
    net: refunded - recovered,
    atPayPal: sum(flying.filter(isRefund), (r) => r.amount_minor), inFlight: flying.length,
  };
}

/** Monday 00:00 local of the week holding `d`. */
function weekStart(d: Date): Date {
  const x = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  x.setDate(x.getDate() - ((x.getDay() + 6) % 7));
  return x;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export type Week = { week: string; start: string; out: number; back: number };

/** MN-03: the last `n` weeks of one currency, money out (refunds that left) against money back
 * (carrier claims paid), in major units for the chart. The current week is last. */
export function weekly(rows: DeskRow[], recoveries: RecoveryFlow[], currency: string, now = new Date(), n = 8): Week[] {
  const first = weekStart(now);
  first.setDate(first.getDate() - 7 * (n - 1));
  const weeks: Week[] = Array.from({ length: n }, (_, i) => {
    const d = new Date(first);
    d.setDate(first.getDate() + 7 * i);
    return { week: `${d.getDate()} ${MONTHS[d.getMonth()]}`, start: d.toISOString(), out: 0, back: 0 };
  });
  const slot = (iso: string) => Math.floor((weekStart(new Date(iso)).getTime() - first.getTime()) / (7 * 864e5) + 0.5);
  for (const r of rows) {
    if (r.currency !== currency || !hasLeft(r)) continue;
    const i = slot(r.completed_at ?? r.created_at);
    if (i >= 0 && i < n) weeks[i].out += r.amount_minor / 100;
  }
  for (const r of recoveries) {
    if (r.currency !== currency) continue;
    const i = slot(r.paid_at);
    if (i >= 0 && i < n) weeks[i].back += r.recovered_minor / 100;
  }
  return weeks;
}

export function countBy(rows: DeskRow[]): Record<string, number> {
  const out: Record<string, number> = {};
  for (const r of rows) out[r.status] = (out[r.status] ?? 0) + 1;
  return out;
}

export const keyShort = (k: string | null) => (k ? (k.length > 18 ? `${k.slice(0, 10)}…${k.slice(-6)}` : k) : "");
