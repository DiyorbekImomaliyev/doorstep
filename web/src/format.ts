export function money(minor: number | null | undefined, currency: string | null | undefined): string {
  if (minor == null || !currency) return "—";
  return new Intl.NumberFormat(currency === "USD" ? "en-US" : "en-GB", {
    style: "currency",
    currency,
    maximumFractionDigits: minor % 100 === 0 ? 0 : 2,
  }).format(minor / 100);
}

export function ago(iso: string): string {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}

export function when(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
  });
}

export const FAILURE_POINT: Record<string, string> = {
  BAD_ADDRESS: "Address problem",
  CARRIER_LOST: "Lost by carrier",
  DAMAGED_IN_TRANSIT: "Damaged in transit",
  STOLEN_AFTER_DELIVERY: "Missing after proven handover",
  WRONG_ITEM: "Wrong item packed",
  CLAIM_CONTRADICTS_EVIDENCE: "Report doesn't match evidence",
};

export const ROUTE: Record<string, { label: string; tone: Tone }> = {
  AUTO_REFUND: { label: "Refunding automatically", tone: "neutral" },
  AWAITING_SELLER: { label: "Your approval", tone: "accent" },
  HOLD_FOR_REVIEW: { label: "Needs a review", tone: "accent" },
  DISPUTE_EVIDENCE: { label: "PayPal dispute", tone: "bad" },
  SETTLED: { label: "Settled", tone: "good" },
  CLOSED: { label: "Closed", tone: "neutral" },
};

export type Tone = "neutral" | "accent" | "good" | "warn" | "bad";

/** Order state, calm: only problems get a colour. */
export function stateTone(state: string): Tone {
  if (["EXCEPTION", "CASE_OPEN"].includes(state)) return "warn";
  if (["CONFIRMED", "RESOLVED"].includes(state)) return "good";
  return "neutral";
}

export const METHOD: Record<string, string> = { home: "Home", locker: "Locker", signature: "Signature" };

export const REASONS: { value: string; label: string }[] = [
  { value: "not_received", label: "It didn't arrive" },
  { value: "damaged", label: "It arrived damaged" },
  { value: "wrong_item", label: "Wrong item" },
  { value: "missing_item", label: "Something is missing" },
  { value: "empty_box", label: "The box was empty" },
];

/** Delivery risk: the locker threshold is 5%; above 15% is high. Only high risk is coloured. */
export function riskTone(p: number): Tone {
  return p >= 0.15 ? "bad" : p >= 0.05 ? "warn" : "neutral";
}

export function pct(p: number): string {
  return `${(p * 100).toFixed(1)}%`;
}
