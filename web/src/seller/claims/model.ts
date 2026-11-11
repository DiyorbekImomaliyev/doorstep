import { money, type Tone } from "../../format";
import { CARRIER } from "../../insights/data";
import type { ClaimStatus, DeskRow } from "./claimsApi";

export const DAY = 86_400_000;

export const STATUS: Record<ClaimStatus, { label: string; tone: Tone; hint: string }> = {
  PREPARED: { label: "Ready", tone: "accent", hint: "Ready to file with the carrier" },
  SUBMITTED: { label: "Submitted", tone: "warn", hint: "Filed; waiting for the carrier" },
  PAID: { label: "Paid", tone: "good", hint: "The carrier paid" },
  DENIED: { label: "Refused", tone: "bad", hint: "The carrier refused" },
  EXPIRED: { label: "Expired", tone: "neutral", hint: "The claim window closed before filing" },
  WITHDRAWN: { label: "Withdrawn", tone: "neutral", hint: "Withdrawn, never filed" },
};
export const statusOf = (s: string) => STATUS[s as ClaimStatus] ?? { label: s.toLowerCase(), tone: "neutral" as Tone, hint: s };

/** The segmented filter, in the order a claim moves. */
export type Seg = "all" | ClaimStatus;
export const SEGS: Seg[] = ["all", "PREPARED", "SUBMITTED", "PAID", "DENIED", "EXPIRED", "WITHDRAWN"];

export const FAULT: Record<string, string> = { CARRIER_LOST: "Lost", DAMAGED_IN_TRANSIT: "Damaged" };

export const isOpen = (r: { status: string }) => r.status === "PREPARED" || r.status === "SUBMITTED";
/** A withdrawn or expired claim was never made, so it stays out of the claimed totals. */
export const isMade = (r: { status: string }) => r.status !== "WITHDRAWN" && r.status !== "EXPIRED";
const isDecided = (r: { status: string }) => r.status === "PAID" || r.status === "DENIED";

/** The carrier's own name; "Shippo (test)" for the sandbox carrier the terms call "Your carrier". */
export const carrierOf = (r: Pick<DeskRow, "carrier" | "carrier_name">) => CARRIER[r.carrier] ?? r.carrier_name;

/** Whole days left to file, rounded up; 0 on the day itself or once past. */
export const daysLeft = (r: Pick<DeskRow, "deadline_at">, now: number) => Math.max(0, Math.ceil((Date.parse(r.deadline_at) - now) / DAY));

export type Urgency = "bad" | "warn" | "calm" | "filed";
/** A filed claim has met its window; a ready one is urgent under 3 days, soon under 7. */
export function urgency(r: DeskRow, now: number): Urgency {
  if (r.status === "SUBMITTED") return "filed";
  const d = daysLeft(r, now);
  return d <= 3 ? "bad" : d <= 7 ? "warn" : "calm";
}
export const URGENCY_LABEL: Record<Urgency, string> = { bad: "3 days or less", warn: "Within 7 days", calm: "Later", filed: "Filed" };

export const DUE_DAYS = 7;
export const isDueSoon = (r: DeskRow, now: number) => r.status === "PREPARED" && daysLeft(r, now) <= DUE_DAYS;

export type ByCurrency = Record<string, number>;
export function sumBy<T extends { currency: string }>(rows: T[], pick: (r: T) => number): ByCurrency {
  const out: ByCurrency = {};
  for (const r of rows) out[r.currency] = (out[r.currency] ?? 0) + pick(r);
  return out;
}
/** Money per currency, never added across currencies: "£170 + $89". */
export const joinMoney = (by: ByCurrency, zero = "GBP") => {
  const parts = Object.entries(by).filter(([, v]) => v > 0).map(([c, v]) => money(v, c));
  return parts.length ? parts.join(" + ") : money(0, Object.keys(by)[0] ?? zero);
};

/** Won ÷ claimed on decided claims, per currency (a carrier may pay part of a claim). */
export type WinRecord = { won: number; decided: number; recovered: ByCurrency; claimed: ByCurrency };
export function record(rows: DeskRow[]): WinRecord {
  const decided = rows.filter(isDecided);
  return {
    won: decided.filter((r) => r.status === "PAID").length,
    decided: decided.length,
    recovered: sumBy(decided, (r) => r.recovered_minor ?? 0),
    claimed: sumBy(decided, (r) => r.claim_minor),
  };
}
/** "100%", or "GBP 100% · USD 40%" when the decided claims are in two currencies. */
export function rateText(rec: WinRecord): string | null {
  const cur = Object.keys(rec.claimed).filter((c) => rec.claimed[c] > 0);
  if (!cur.length) return null;
  const pct = (c: string) => `${Math.round(((rec.recovered[c] ?? 0) / rec.claimed[c]) * 100)}%`;
  return cur.length === 1 ? pct(cur[0]) : cur.map((c) => `${c} ${pct(c)}`).join(" · ");
}

export type Summary = {
  open: number; openMoney: ByCurrency; ready: number; submitted: number;
  recovered: ByCurrency; rec: WinRecord;
  due: number; dueMoney: ByCurrency; mostUrgent: DeskRow | null;
  counts: Record<Seg, number>;
};
export function summarise(rows: DeskRow[], now: number): Summary {
  const open = rows.filter(isOpen);
  const due = rows.filter((r) => isDueSoon(r, now)).sort((a, b) => Date.parse(a.deadline_at) - Date.parse(b.deadline_at));
  const counts = Object.fromEntries(SEGS.map((s) => [s, s === "all" ? rows.length : rows.filter((r) => r.status === s).length])) as Record<Seg, number>;
  return {
    open: open.length, openMoney: sumBy(open, (r) => r.claim_minor),
    ready: counts.PREPARED, submitted: counts.SUBMITTED,
    recovered: sumBy(rows.filter((r) => r.status === "PAID"), (r) => r.recovered_minor ?? 0),
    rec: record(rows),
    due: due.length, dueMoney: sumBy(due, (r) => r.claim_minor), mostUrgent: due[0] ?? null,
    counts,
  };
}

export const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
export const shortDate = (iso: string) => new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short" });
