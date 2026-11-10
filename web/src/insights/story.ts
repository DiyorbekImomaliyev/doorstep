/**
 * S1: the Overview brief, worded. Pure functions from figures Studio already computed (the brief's
 * own `getData`, so it follows the Quarter filter like the KPIs) and from the anomaly scan (agent.ts)
 * to sentence parts. No model call: every number here is counted from the seller's rows.
 */
import type { Part } from "../components/ai";
import { scanAnomalies, tableRows, type Facts, type Finding } from "./agent";
import type { Prepared } from "./data";

const DAY = 864e5;
const count = (n: number) => Math.round(n).toLocaleString("en-GB");
const pct1 = (v: number) => `${(v * 100).toFixed(1)}%`;
/** The same rounding as the Parcel journey's shares, so the two never disagree. */
export const share = (n: number, of: number) => {
  if (of <= 0) return "—";
  const p = Math.round((n / of) * 100);
  return p === 0 && n > 0 ? "<1%" : `${p}%`;
};
const money = (v: number, home: Prepared["home"]) => v.toLocaleString("en-GB", { style: "currency", currency: home, maximumFractionDigits: 0 });
const day = (t: number) => new Date(t).toLocaleDateString("en-GB", { day: "numeric", month: "short" });

/** "2,052 of 2,107 orders arrived with no problem (97%)." */
export function headline(orders: number, delivered: number): Part[] {
  if (orders <= 0) return ["No orders in this period yet."];
  return [{ b: `${count(delivered)} of ${count(orders)}` }, ` orders arrived with no problem (${share(delivered, orders)}).`];
}

const METHOD_NAME: Record<string, string> = { Home: "Home delivery", Locker: "Locker pickup", Signature: "Signed-for delivery" };
const whose = (f: Extract<Facts, { kind: "rate" }>) =>
  f.field === "carrier" ? `${f.key}'s case rate` : f.field === "method" ? `the case rate for ${METHOD_NAME[f.key] ?? f.key}` : "your case rate";

/** An open carrier claim, soonest deadline first. */
export type Claim = { carrier: string; amount: number; due: number; step: string; orderId: string | null; example: boolean };

export function openClaims(p: Prepared, now = Date.now()): Claim[] {
  const caseOrder = new Map(tableRows(p, "cases").map((c) => [String(c.case_id), String(c.order_id)]));
  return tableRows(p, "recoveries")
    .map((r) => {
      const at = r.deadline_at instanceof Date ? r.deadline_at.getTime() : Date.parse(String(r.deadline_at));
      const orderId = caseOrder.get(String(r.case_id)) ?? null;
      return {
        carrier: String(r.carrier), amount: Number(r.claimed ?? 0), due: at, step: String(r.next_step),
        orderId, example: !orderId || orderId.startsWith("hist-"),
      };
    })
    .filter((c) => c.step !== "Done" && Number.isFinite(c.due) && c.due >= now - DAY)
    .sort((a, b) => a.due - b.due);
}

/** One thing to do: what, its money and date, and the one button. */
export type DoNext = {
  key: string; what: string; money?: string; when?: string;
  action:
    | { kind: "case"; label: "Open case"; orderId: string }
    | { kind: "carrier"; label: string; carrier: string }
    | { kind: "method"; label: "Show these orders"; method: string };
};

export type Brief = { line: Part[]; next: DoNext[]; findings: Finding[] };

/** The second line and the "Do next" rows, from the last four weeks and the open claims. */
export function brief(p: Prepared, now = new Date()): Brief {
  const scan = scanAnomalies(p, now);
  const rates = scan.findings.filter((f): f is Finding & { facts: Extract<Facts, { kind: "rate" }> } => f.facts?.kind === "rate");
  const carrier = rates.find((f) => f.facts.field === "carrier");
  const method = rates.find((f) => f.facts.field === "method");
  const top = carrier ?? rates[0];
  const claims = openClaims(p, now.getTime());
  const soon = claims[0];

  const line: Part[] = [];
  if (top) line.push(`Last 4 weeks: ${whose(top.facts)} rose to `, { b: pct1(top.facts.now) }, ` (${pct1(top.facts.before)} before).`);
  else line.push("Last 4 weeks: nothing stands out.");
  if (soon) {
    const days = Math.max(0, Math.ceil((soon.due - now.getTime()) / DAY));
    line.push(` Next claim deadline: ${soon.carrier}, `, { b: money(soon.amount, p.home) }, `, in ${days} day${days === 1 ? "" : "s"}.`);
  }

  const next: DoNext[] = [];
  if (soon) {
    next.push({
      key: "claim",
      what: soon.step === "File it with the carrier" ? `File the ${soon.carrier} claim` : `${soon.carrier} claim: waiting for the carrier`,
      money: money(soon.amount, p.home), when: `due ${day(soon.due)}`,
      action: soon.example || !soon.orderId
        ? { kind: "carrier", label: `Open ${soon.carrier} report`, carrier: soon.carrier }
        : { kind: "case", label: "Open case", orderId: soon.orderId },
    });
  }
  if (carrier) {
    const f = carrier.facts;
    next.push({
      key: `carrier-${f.key}`, what: `${f.key} · ${count(f.cases)} of ${count(f.orders)} orders had a problem in 4 weeks`,
      action: { kind: "carrier", label: `Open ${f.key} report`, carrier: f.key },
    });
  }
  if (method) {
    const f = method.facts;
    next.push({
      key: `method-${f.key}`, what: `${METHOD_NAME[f.key] ?? f.key} · case rate up to ${pct1(f.now)}`,
      action: { kind: "method", label: "Show these orders", method: f.key },
    });
  }
  return { line, next: next.slice(0, 3), findings: scan.findings };
}
