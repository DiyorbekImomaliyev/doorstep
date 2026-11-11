/**
 * AS-21: Ask Doorstep's starters, chosen in the browser from the seller's own rows.
 *
 * Three buttons: the carrier with the worst case rate (to chase), the next claim deadline (to
 * open), and the delivery method with the highest case rate (to chart). The rows are read here and
 * never sent to a model: only the words of the button are, once clicked, and they come from the
 * same fixed menu as every other question. Too little data for one (a shop with no claims yet)
 * and a general starter takes its place.
 */
import type { AgAiPromptStarter } from "ag-studio";
import { STARTERS, chaseQuestion, tableRows } from "./agent";
import type { Prepared } from "./data";
import { openClaims } from "./story";

/** A group needs this many orders before its rate means something. */
const MIN_ORDERS = 10;

type Row = Record<string, unknown>;

/** The group (carrier or method) with the highest case rate, among those with enough orders. */
export function worstBy(rows: Row[], field: "carrier" | "method"): string | undefined {
  const seen = new Map<string, { n: number; cases: number }>();
  for (const r of rows) {
    const key = String(r[field] ?? "");
    if (!key || /^Not /.test(key)) continue;
    const g = seen.get(key) ?? { n: 0, cases: 0 };
    g.n += 1;
    g.cases += Number(r.had_case ?? 0);
    seen.set(key, g);
  }
  let best: string | undefined;
  let rate = 0;
  for (const [key, g] of seen) {
    if (g.n < MIN_ORDERS || g.cases === 0) continue;
    if (g.cases / g.n > rate) { rate = g.cases / g.n; best = key; }
  }
  return best;
}

const day = (t: number) => new Date(t).toLocaleDateString("en-GB", { day: "numeric", month: "short" });

export function dataStarters(p: Prepared, now = Date.now()): AgAiPromptStarter[] {
  const orders = tableRows(p, "orders");
  const out: AgAiPromptStarter[] = [];
  const carrier = worstBy(orders, "carrier");
  if (carrier) out.push({ label: `Chase ${carrier}`, prompt: chaseQuestion(carrier) });
  const claim = openClaims(p, now)[0];
  if (claim) out.push({ label: `${claim.carrier} claim, ${day(claim.due)}`, prompt: `Show me ${claim.carrier}'s claims` });
  const method = worstBy(orders, "method");
  if (method) out.push({ label: `${method}: highest case rate`, prompt: "Case rate by delivery method" });
  // Whatever is missing is filled from the general starters, in order, without repeating one.
  for (const s of STARTERS) if (out.length < 3 && !out.some((o) => o.prompt === s.prompt)) out.push(s);
  return out.slice(0, 3);
}
