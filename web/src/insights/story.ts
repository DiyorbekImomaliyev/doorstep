/**
 * S1: the Overview brief, worded. Pure functions from figures Studio already computed (the brief's
 * own `getData`, so it follows the Quarter filter like the KPIs) and from the anomaly scan (agent.ts)
 * to sentence parts. No model call: every number here is counted from the seller's rows.
 */
import type { Part } from "../components/ai";
import { scanAnomalies, tableRows, type Facts, type Finding } from "./agent";
import type { Prepared } from "./data";

const DAY = 864e5;
/** The brief lists up to three claims, and four things to do in all. */
const MAX_CLAIMS = 3;
const MAX_NEXT = 4;
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
  if (orders <= 0) return ["No orders yet."];
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

/** One thing to do: what, one quiet detail line, days left (claims), and the one button. */
export type DoNext = {
  key: string; what: string; sub?: string; days?: number; money?: string; when?: string;
  action:
    | { kind: "case"; label: "Open case"; orderId: string }
    | { kind: "carrier"; label: string; carrier: string }
    | { kind: "chase"; label: string; carrier: string }
    | { kind: "method"; label: "Show these orders"; method: string };
};

/** The brief's two signals, as chips: what moved in four weeks, and the next claim deadline. */
export type Signal =
  | { kind: "rate"; who: string; now: string; before: string; up: boolean }
  | { kind: "steady" }
  | { kind: "claim"; carrier: string; money: string; days: number; date: string };

export type Brief = { line: Part[]; signals: Signal[]; next: DoNext[]; findings: Finding[] };

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
  const signals: Signal[] = [];
  const days = soon ? Math.max(0, Math.ceil((soon.due - now.getTime()) / DAY)) : 0;
  if (top) {
    const f = top.facts;
    const who = f.field === "method" ? METHOD_NAME[f.key] ?? f.key : f.field === "carrier" ? f.key : "All orders";
    signals.push({ kind: "rate", who, now: pct1(f.now), before: pct1(f.before), up: f.now >= f.before });
  } else signals.push({ kind: "steady" });
  if (soon) signals.push({ kind: "claim", carrier: soon.carrier, money: money(soon.amount, p.home), days, date: day(soon.due) });
  if (top) line.push(`4 weeks: ${whose(top.facts)} rose to `, { b: pct1(top.facts.now) }, ` (was ${pct1(top.facts.before)}).`);
  else line.push("4 weeks: nothing stands out.");
  if (soon) {
    line.push(` Next claim: ${soon.carrier}, `, { b: money(soon.amount, p.home) }, `, in ${days} day${days === 1 ? "" : "s"}.`);
  }

  // AS-19: up to three open claims, the nearest deadline first, each with its own way in.
  const next: DoNext[] = [];
  for (const c of claims.slice(0, MAX_CLAIMS)) {
    const left = Math.max(0, Math.ceil((c.due - now.getTime()) / DAY));
    next.push({
      key: `claim-${c.orderId ?? c.carrier}-${c.due}`,
      what: c.step === "File it with the carrier" ? `File ${c.carrier} claim` : `${c.carrier} claim · waiting`,
      sub: `${money(c.amount, p.home)} · due ${day(c.due)}`, days: left,
      money: money(c.amount, p.home), when: `due ${day(c.due)}`,
      action: c.example || !c.orderId
        ? { kind: "carrier", label: `Open ${c.carrier} report`, carrier: c.carrier }
        : { kind: "case", label: "Open case", orderId: c.orderId },
    });
  }
  if (carrier) {
    const f = carrier.facts;
    next.push({
      key: `carrier-${f.key}`, what: f.key, sub: `${count(f.cases)} of ${count(f.orders)} had a problem`,
      action: { kind: "chase", label: `Chase ${f.key}`, carrier: f.key },
    });
  }
  if (method) {
    const f = method.facts;
    next.push({
      key: `method-${f.key}`, what: METHOD_NAME[f.key] ?? f.key, sub: `Case rate up to ${pct1(f.now)}`,
      action: { kind: "method", label: "Show these orders", method: f.key },
    });
  }
  return { line, signals, next: next.slice(0, MAX_NEXT), findings: scan.findings };
}
