/**
 * Doorstep's data for AG Studio (AS-01, AS-02).
 *
 * Five related tables from `GET /api/seller/{token}/insights`:
 *   orders ← shipments, orders ← cases ← money_actions, cases ← recoveries
 * plus model expressions (prevention rate, case rate, carrier recovery rate) and one calendar
 * bound to every date, so any widget can slice by week, month or quarter.
 *
 * Money: the API sends integer minor units with a currency. Studio shows major units, and only in
 * the shop's home currency; an order in another currency still counts as an order but never adds
 * to a money total (no FX, no mixing).
 *
 * The demo shop also gets 26 weeks of simulated history so its trends have something to show.
 * Every one of those rows says so in its Source field; a real shop never gets any.
 */
import type {
  AgCalendar, AgDataSourcesDefinition, AgDataTransaction, AgExpression, AgExpressionFieldDefinition, AgFieldDefinition, AgRelationDefinition,
} from "ag-studio";
import { itemKind } from "../components/gridCells";

// ---------------------------------------------------------------- API shape

export type InsightOrder = {
  order_id: string; order_ref: string; created_at: string; item: string; amount_minor: number; currency: string;
  buyer_country: string; buyer_district: string; method: string | null; recommended_method: string | null;
  risk: number | null; address_check: string; state: string; evidence_score: number; simulated: boolean;
};
export type InsightShipment = {
  shipment_id: string; order_id: string; carrier: string; method: string; shipped_at: string;
  delivered_at: string | null; events: number;
};
export type InsightCase = {
  case_id: string; order_id: string; opened_at: string; reason: string; state: string; failure_point: string | null;
  confidence: string | null; route: string | null; liable_party: string | null; rule_id: string | null;
};
export type InsightMoney = {
  action_id: string; order_id: string; case_id: string | null; kind: string; amount_minor: number; currency: string;
  status: string; external: boolean; created_at: string;
};
export type InsightRecovery = {
  recovery_id: string; case_id: string; order_id: string; carrier: string; failure_point: string; claim_minor: number;
  recovered_minor: number | null; currency: string; status: string; deadline_at: string;
};
export type InsightsOut = {
  seller_region: string; orders: InsightOrder[]; shipments: InsightShipment[]; cases: InsightCase[];
  money_actions: InsightMoney[]; recoveries: InsightRecovery[];
};

export async function fetchInsights(token: string): Promise<InsightsOut> {
  const r = await fetch(`/api/seller/${token}/insights`);
  if (!r.ok) throw new Error(`insights ${r.status}`);
  return r.json();
}

// ---------------------------------------------------------------- plain words

export const CARRIER: Record<string, string> = {
  royal_mail: "Royal Mail", evri: "Evri", dpd: "DPD", usps: "USPS", ups: "UPS", fedex: "FedEx", shippo: "Shippo (test)",
};
const METHOD: Record<string, string> = { home: "Home", locker: "Locker", signature: "Signature" };
export const FAILURE: Record<string, string> = {
  // Short, so chart legends show them whole; the case drawer keeps the long names.
  CARRIER_LOST: "Lost", DAMAGED_IN_TRANSIT: "Damaged", BAD_ADDRESS: "Bad address",
  STOLEN_AFTER_DELIVERY: "Post-delivery", WRONG_ITEM: "Wrong item",
  CLAIM_CONTRADICTS_EVIDENCE: "Contradicted",
};
const ROUTE: Record<string, string> = {
  auto_refund: "Auto-refund", awaiting_seller: "Seller decides", hold_for_review: "Human review",
  dispute_evidence: "PayPal dispute", reship: "Reship",
};
const CASE_STAGE: Record<string, string> = {
  OPEN: "Open", GATHERING_EVIDENCE: "Open", DECIDED: "Open", AUTO_REFUND: "Refunding", AWAITING_SELLER: "Needs you",
  HOLD_FOR_REVIEW: "In review", DISPUTE_EVIDENCE: "PayPal dispute", SETTLED: "Settled", CLOSED: "Closed",
};
const CLAIM: Record<string, string> = {
  PREPARED: "Ready to file", SUBMITTED: "Filed", PAID: "Paid", DENIED: "Refused", WITHDRAWN: "Withdrawn", EXPIRED: "Expired",
};
const LIABLE: Record<string, string> = { seller: "Seller", buyer: "Buyer", carrier: "Carrier", unclear: "Unclear" };
const label = (map: Record<string, string>, v: string | null | undefined, blank = "None") =>
  v ? map[v] ?? v.replace(/_/g, " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase()) : blank;

// ---------------------------------------------------------------- simulated history (demo shop only)

function mulberry32(seed: number) {
  return () => {
    seed |= 0; seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const ITEMS = [
  ["Black jacket, size M", 6000], ["Running shoes, size 9", 7500], ["Linen shirt, white", 3500], ["Wool beanie", 1800],
  ["Ceramic mug set", 2400], ["Wireless earbuds", 4900], ["Leather belt", 2900], ["Silver hoop earrings", 3900],
  ["Denim jacket, size L", 7200], ["Canvas tote bag", 1600], ["Desk lamp", 4200], ["Chef's knife", 5600],
] as const;
const DISTRICTS_GB = ["E1", "N7", "SW9", "SE15", "M14", "B12", "LS6", "BS3", "G12", "EH6", "CF24", "NG7"];
const DISTRICTS_US = ["10003", "11211", "60614", "94110", "78704", "98103", "30307", "02139", "80205", "19104"];

/** Pick from weighted options with a uniform draw. */
function pick<T>(rnd: () => number, options: [T, number][]): T {
  let x = rnd() * options.reduce((a, [, w]) => a + w, 0);
  for (const [v, w] of options) { x -= w; if (x <= 0) return v; }
  return options[options.length - 1][0];
}

/**
 * 26 weeks of a small shop (~65 orders a week) before the demo orders, at assumed rates: a costly problem on 1-3% of home deliveries, far fewer with a locker or signature.
 */
export function simulatedHistory(home: "GBP" | "USD", before: Date): InsightsOut {
  const rnd = mulberry32(20261104);
  const us = home === "USD";
  const carriers: [string, number][] = us ? [["usps", 55], ["ups", 30], ["fedex", 15]] : [["royal_mail", 55], ["evri", 28], ["dpd", 17]];
  const out: InsightsOut = { seller_region: us ? "US" : "GB", orders: [], shipments: [], cases: [], money_actions: [], recoveries: [] };
  const start = before.getTime() - 26 * 7 * 864e5;
  let n = 0;
  for (let day = 0; day < 26 * 7; day++) {
    const perDay = Math.round(7 + rnd() * 6 + (day / 182) * 3);
    for (let k = 0; k < perDay; k++) {
      n++;
      const id = `hist-${n}`;
      const at = new Date(start + day * 864e5 + rnd() * 864e5);
      const [item, base] = ITEMS[Math.floor(rnd() * ITEMS.length)];
      const amount = Math.round((base * (0.8 + rnd() * 0.5)) / 100) * 100;
      const method = pick(rnd, [["home", 70], ["locker", 18], ["signature", 12]]);
      const carrier = pick(rnd, carriers);
      const fixed = rnd() < 0.06;
      const evidence = method === "home" ? 0.4 + rnd() * 0.3 : 0.75 + rnd() * 0.25;
      out.orders.push({
        order_id: id, order_ref: id.toUpperCase(), created_at: at.toISOString(), item, amount_minor: amount, currency: home,
        buyer_country: us ? "US" : "GB", buyer_district: (us ? DISTRICTS_US : DISTRICTS_GB)[Math.floor(rnd() * 10)],
        method, recommended_method: amount > 5000 ? "locker" : "home", risk: null,
        address_check: fixed ? "fixed" : "ok", state: "CONFIRMED", evidence_score: Math.round(evidence * 100) / 100, simulated: true,
      });
      const shipped = new Date(at.getTime() + (0.5 + rnd()) * 864e5);
      out.shipments.push({
        shipment_id: `${id}-s`, order_id: id, carrier, method, shipped_at: shipped.toISOString(),
        delivered_at: new Date(shipped.getTime() + (1 + rnd() * 3) * 864e5).toISOString(), events: 4,
      });
      const pCase = (method === "home" ? 0.024 : method === "locker" ? 0.006 : 0.008) * (carrier === "evri" || carrier === "fedex" ? 1.5 : 1);
      if (rnd() >= pCase) continue;
      const fp = pick(rnd, [["CARRIER_LOST", 40], ["DAMAGED_IN_TRANSIT", 20], ["BAD_ADDRESS", 10], ["STOLEN_AFTER_DELIVERY", 12], ["WRONG_ITEM", 8], ["CLAIM_CONTRADICTS_EVIDENCE", 10]]);
      const sellerPays = fp === "CARRIER_LOST" || fp === "DAMAGED_IN_TRANSIT" || fp === "WRONG_ITEM";
      const route = fp === "CLAIM_CONTRADICTS_EVIDENCE" ? "hold_for_review" : sellerPays ? (amount <= 5000 ? "auto_refund" : "awaiting_seller") : "awaiting_seller";
      const opened = new Date(shipped.getTime() + (3 + rnd() * 4) * 864e5);
      const caseId = `${id}-c`;
      out.cases.push({
        case_id: caseId, order_id: id, opened_at: opened.toISOString(), reason: "not_received", state: "SETTLED",
        failure_point: fp, confidence: pick(rnd, [["high", 60], ["medium", 30], ["low", 10]]), route,
        liable_party: sellerPays ? "seller" : fp === "STOLEN_AFTER_DELIVERY" ? "buyer" : "unclear", rule_id: us ? "us_seller_policy_v1" : "uk_possession_v1",
      });
      if (sellerPays) {
        out.money_actions.push({
          action_id: `${id}-m`, order_id: id, case_id: caseId, kind: "refund", amount_minor: amount, currency: home,
          status: "completed", external: false, created_at: new Date(opened.getTime() + 36e5 * (route === "auto_refund" ? 0.05 : 6)).toISOString(),
        });
      }
      if (fp === "CARRIER_LOST" || fp === "DAMAGED_IN_TRANSIT") {
        // Cover as in rules/carriers/carrier_claims_v1.yaml: Royal Mail £75, USPS $100, Evri £20.
        const cover = carrier === "royal_mail" ? 7500 : carrier === "evri" ? 2000 : 10000;
        const claim = Math.min(amount, cover);
        let status = pick(rnd, [["PAID", 52], ["DENIED", 14], ["SUBMITTED", 16], ["PREPARED", 10], ["EXPIRED", 8]]);
        // Older claims have moved on, as they would for a real shop: a seller files within weeks
        // (the cron sweep expires one left past its deadline) and carriers answer within about six.
        const age = (before.getTime() - opened.getTime()) / 864e5;
        if (status === "PREPARED" && age > 21) status = age > 60 ? "EXPIRED" : "SUBMITTED";
        if (status === "SUBMITTED" && age > 45) status = claim % 4 === 0 ? "DENIED" : "PAID";
        out.recoveries.push({
          recovery_id: `${id}-r`, case_id: caseId, order_id: id, carrier, failure_point: fp, claim_minor: claim,
          recovered_minor: status === "PAID" ? claim : null, currency: home, status,
          deadline_at: new Date(opened.getTime() + 60 * 864e5).toISOString(),
        });
      }
    }
  }
  return out;
}

// ---------------------------------------------------------------- Studio data sources

const TEXT = { format: "textFormat" } as const;
const INT = { format: "integerFormat" } as const;
const DATE = { format: "dateTimeFormat", formatOptions: { format: "d MMM yyyy, HH:mm" } } as const;
const PCT = { format: "percentageFormat", formatOptions: { format: "#,##0.0%" } } as const;

export type Home = "GBP" | "USD";
export const moneyFormat = (home: Home) =>
  ({ format: "currencyFormat", formatOptions: { format: home === "USD" ? "$#,##0" : "£#,##0" } }) as const;

/** In the home currency, in major units; another currency gives null so a sum skips it. */
const homeMajor = (minor: number | null | undefined, currency: string, home: Home) =>
  minor != null && currency === home ? minor / 100 : null;

export const calendarFrom = (from: string): AgCalendar => ({
  id: "calendar",
  label: "Calendar",
  range: { from: { literal: from }, to: { operator: "currentDate", inputs: [] } },
  fragments: ["year", "quarter", { unit: "month", format: "MMM yyyy" }, "week", "day", "dayOfWeek"],
});

const measure = (
  id: string, name: string, expression: AgExpressionFieldDefinition["expression"],
  format: Pick<AgExpressionFieldDefinition, "format" | "formatOptions">,
): AgExpressionFieldDefinition => ({ id, name, isMeasure: true, expression, ...format });
const num = (value: number) => ({ type: "number", value }) as const;
const ratio = (a: string, b: string): AgExpression => ({ operator: "divide", inputs: [{ id: a }, { id: b }] });

export type Prepared = { sources: AgDataSourcesDefinition; home: Home; history: boolean; live: number };

/**
 * One definition every version shares. `history` adds the simulated weeks (demo shop only).
 */
export function buildSources(api: InsightsOut, opts: { history: boolean }): Prepared {
  const home: Home = api.seller_region === "US" ? "USD" : "GBP";
  const MONEY = moneyFormat(home);
  const live = api.orders.length;
  const earliest = api.orders.reduce((m, o) => Math.min(m, Date.parse(o.created_at)), Date.now());
  const hist = opts.history ? simulatedHistory(home, new Date(earliest)) : null;
  const all = (k: keyof Omit<InsightsOut, "seller_region">) => [...(hist?.[k] ?? []), ...api[k]] as never[];
  const orders: InsightOrder[] = all("orders");
  const shipments: InsightShipment[] = all("shipments");
  const cases: InsightCase[] = all("cases");
  const money: InsightMoney[] = all("money_actions");
  const recoveries: InsightRecovery[] = all("recoveries");
  const histIds = new Set(hist?.orders.map((o) => o.order_id) ?? []);
  const source = (orderId: string) => (histIds.has(orderId) ? "Example history" : api.orders.some((o) => o.simulated) ? "Your orders" : "PayPal sandbox");

  const carrierOf = new Map(shipments.map((x) => [x.order_id, x.carrier]));
  const transit = (x: InsightShipment) =>
    x.delivered_at ? Math.round(((Date.parse(x.delivered_at) - Date.parse(x.shipped_at)) / 864e5) * 10) / 10 : null;
  const transitOf = new Map(shipments.map((x) => [x.order_id, transit(x)]));
  // Each case's money on the case row itself, so a chart can mix refunds and carrier payments
  // without a join repeating either (Studio's fan-out guard).
  const refundedBy = new Map<string, number>();
  for (const m of money) {
    const v = m.kind !== "evidence" && m.status === "completed" && m.case_id ? homeMajor(m.amount_minor, m.currency, home) : null;
    if (v != null && m.case_id) refundedBy.set(m.case_id, (refundedBy.get(m.case_id) ?? 0) + v);
  }
  const claimOf = new Map(recoveries.map((r) => [r.case_id, r]));
  const orderOf = new Map(orders.map((o) => [o.order_id, o]));
  const caseOrders = new Set(cases.map((c) => c.order_id));
  // The parcel journey, as 0/1 flags on the order row: every stage then counts on one table, so
  // the journey widget never joins (Studio's fan-out guard). Outcomes after shipping are exclusive:
  // a case, else delivered, else still on the way.
  const shippedOrders = new Set(shipments.map((x) => x.order_id));
  const deliveredOrders = new Set(shipments.filter((x) => x.delivered_at).map((x) => x.order_id));
  const orderOfCase = new Map(cases.map((c) => [c.case_id, c.order_id]));
  const refundedOrders = new Set(cases.filter((c) => (refundedBy.get(c.case_id) ?? 0) > 0).map((c) => c.order_id));
  const recoveredOrders = new Set(recoveries.filter((r) => (r.recovered_minor ?? 0) > 0).map((r) => orderOfCase.get(r.case_id)));

  const ordersRows = orders.map((o) => ({
    order_id: o.order_id,
    order_ref: o.order_ref,
    created_at: new Date(o.created_at),
    item: o.item,
    category: itemKind(o.item).label,
    value: homeMajor(o.amount_minor, o.currency, home),
    currency: o.currency,
    country: o.buyer_country,
    district: o.buyer_district || "—",
    method: label(METHOD, o.method, "Not chosen"),
    carrier: label(CARRIER, carrierOf.get(o.order_id), "Not shipped yet"),
    address_check: label({ ok: "Valid", fixed: "Fixed at checkout", overridden: "Kept by buyer", unknown: "Not checked" }, o.address_check),
    // AS-02: a preventive step is a safe method (locker, signature) or an address fixed at checkout.
    protected: o.method === "locker" || o.method === "signature" || o.address_check === "fixed" ? 1 : 0,
    // On the order itself, so the case rate never needs a join that would drop orders without a case.
    had_case: caseOrders.has(o.order_id) ? 1 : 0,
    evidence: o.evidence_score,
    // On the order too, so a carrier scorecard reads one table (no join repeats a row).
    transit_days: transitOf.get(o.order_id) ?? null,
    shipped: shippedOrders.has(o.order_id) || caseOrders.has(o.order_id) ? 1 : 0,
    delivered_ok: !caseOrders.has(o.order_id) && deliveredOrders.has(o.order_id) ? 1 : 0,
    on_way: !caseOrders.has(o.order_id) && shippedOrders.has(o.order_id) && !deliveredOrders.has(o.order_id) ? 1 : 0,
    refunded_flag: refundedOrders.has(o.order_id) ? 1 : 0,
    recovered_flag: recoveredOrders.has(o.order_id) ? 1 : 0,
    source: source(o.order_id),
  }));
  const shipmentRows = shipments.map((x) => ({
    shipment_id: x.shipment_id,
    order_id: x.order_id,
    carrier: label(CARRIER, x.carrier),
    method: label(METHOD, x.method),
    shipped_at: new Date(x.shipped_at),
    delivered_at: x.delivered_at ? new Date(x.delivered_at) : null,
    transit_days: transit(x),
  }));
  const caseRows = cases.map((c) => {
    const o = orderOf.get(c.order_id);
    return {
      case_id: c.case_id,
      order_id: c.order_id,
      opened_at: new Date(c.opened_at),
      failure_point: label(FAILURE, c.failure_point, "Not decided"),
      stage: label(CASE_STAGE, c.state),
      // Decisions store the route as a case state (AUTO_REFUND); the map is keyed in lower case.
      route: label(ROUTE, c.route?.toLowerCase(), "Not decided"),
      liable: label(LIABLE, c.liable_party, "Not decided"),
      confidence: label({ high: "High", medium: "Medium", low: "Low" }, c.confidence, "—"),
      carrier: label(CARRIER, carrierOf.get(c.order_id), "Not shipped"),
      method: label(METHOD, o?.method ?? null, "Not chosen"),
      at_stake: o ? homeMajor(o.amount_minor, o.currency, home) : null,
      refunded: refundedBy.get(c.case_id) ?? null,
      claimed: claimOf.has(c.case_id) ? homeMajor(claimOf.get(c.case_id)!.claim_minor, claimOf.get(c.case_id)!.currency, home) : null,
      recovered: claimOf.has(c.case_id) ? homeMajor(claimOf.get(c.case_id)!.recovered_minor, claimOf.get(c.case_id)!.currency, home) : null,
      open: ["SETTLED", "CLOSED"].includes(c.state) ? 0 : 1,
    };
  });
  const moneyRows = money.filter((m) => m.kind !== "evidence").map((m) => ({
    action_id: m.action_id,
    case_id: m.case_id,
    kind: label({ refund: "Refund", partial: "Partial refund" }, m.kind),
    status: label({ completed: "Completed", pending: "Pending", failed: "Failed" }, m.status),
    paid_out: m.status === "completed" ? homeMajor(m.amount_minor, m.currency, home) : null,
    created_at: new Date(m.created_at),
  }));
  const closed = (status: string) => status === "DENIED" || status === "EXPIRED" || status === "WITHDRAWN";
  const recoveryRows = recoveries.map((r) => ({
    recovery_id: r.recovery_id,
    case_id: r.case_id,
    carrier: label(CARRIER, r.carrier),
    failure_point: label(FAILURE, r.failure_point),
    status: label(CLAIM, r.status),
    claimed: homeMajor(r.claim_minor, r.currency, home),
    // A claim closed without payment recovered nothing (0); one still open has no amount yet.
    recovered: r.recovered_minor != null ? homeMajor(r.recovered_minor, r.currency, home) : closed(r.status) ? 0 : null,
    deadline_at: new Date(r.deadline_at),
    // Still claimable: an open claim's amount (prepared or filed, not yet paid or closed).
    open_value: r.status === "PREPARED" || r.status === "SUBMITTED" ? homeMajor(r.claim_minor, r.currency, home) : 0,
    // What the seller does next, so open claims can lead the list.
    next_step: r.status === "PREPARED" ? "File it with the carrier" : r.status === "SUBMITTED" ? "Waiting for the carrier" : "Done",
    // One sort key (a Studio grid sorts by one field): open claims by soonest deadline, then
    // closed ones, newest first.
    queue: r.status === "PREPARED" || r.status === "SUBMITTED" ? Date.parse(r.deadline_at) : 2e13 - Date.parse(r.deadline_at),
  })).sort((a, b) => a.queue - b.queue);

  const f = (id: string, name: string, more: Partial<AgFieldDefinition> = TEXT): AgFieldDefinition =>
    ({ id, name, ...TEXT, ...more }) as AgFieldDefinition;

  const relationships: AgRelationDefinition[] = [
    { id: "shipments-orders", source: { tableId: "shipments", fieldId: "order_id" }, target: { tableId: "orders", fieldId: "order_id" }, type: "many-to-one" },
    { id: "cases-orders", source: { tableId: "cases", fieldId: "order_id" }, target: { tableId: "orders", fieldId: "order_id" }, type: "many-to-one" },
    { id: "money-cases", source: { tableId: "money_actions", fieldId: "case_id" }, target: { tableId: "cases", fieldId: "case_id" }, type: "many-to-one" },
    { id: "recoveries-cases", source: { tableId: "recoveries", fieldId: "case_id" }, target: { tableId: "cases", fieldId: "case_id" }, type: "many-to-one" },
    // One calendar for every date: weeks, months and quarters line up across tables.
    { id: "orders-calendar", source: { tableId: "orders", fieldId: "created_at" }, target: { calendarId: "calendar" }, truncate: "day" },
    { id: "cases-calendar", source: { tableId: "cases", fieldId: "opened_at" }, target: { calendarId: "calendar" }, truncate: "day" },
  ];

  const expressions: AgExpressionFieldDefinition[] = [
    measure("order_count", "Orders", { id: "orders.order_id", aggregation: "countd" }, INT),
    measure("protected_orders", "Protected orders", { id: "orders.protected", aggregation: "sum" }, INT),
    measure("prevention_rate", "Prevention rate", ratio("protected_orders", "order_count"), PCT),
    measure("case_count", "Cases", { id: "cases.case_id", aggregation: "countd" }, INT),
    measure("open_cases", "Open cases", { id: "cases.open", aggregation: "sum" }, INT),
    measure("orders_with_case", "Orders with a case", { id: "orders.had_case", aggregation: "sum" }, INT),
    measure("case_rate", "Case rate", ratio("orders_with_case", "order_count"), PCT),
    measure("sales", "Sales", { id: "orders.value", aggregation: "sum" }, MONEY),
    measure("paid_out", "Refunded", { id: "cases.refunded", aggregation: "sum" }, MONEY),
    measure("claimed", "Claimed from carriers", { id: "cases.claimed", aggregation: "sum" }, MONEY),
    measure("recovered", "Recovered from carriers", { id: "cases.recovered", aggregation: "sum" }, MONEY),
    // Claims counted on their own table, for "where your claims stand" (no join needed).
    measure("claim_count", "Claims", { id: "recoveries.recovery_id", aggregation: "countd" }, INT),
    measure("claim_value", "Claim value", { id: "recoveries.claimed", aggregation: "sum" }, MONEY),
    measure("claim_paid", "Recovered", { operator: "coalesce", inputs: [{ id: "recoveries.recovered", aggregation: "sum" }, num(0)] }, MONEY),
    measure("recovery_rate", "Carrier recovery rate", ratio("recovered", "claimed"), PCT),
    measure("net_loss", "Net delivery loss", { operator: "subtract", inputs: [{ operator: "coalesce", inputs: [{ id: "paid_out" }, num(0)] }, { operator: "coalesce", inputs: [{ id: "recovered" }, num(0)] }] }, MONEY),
    measure("stage_shipped", "Shipped", { id: "orders.shipped", aggregation: "sum" }, INT),
    measure("stage_delivered", "Delivered, no problem", { id: "orders.delivered_ok", aggregation: "sum" }, INT),
    measure("stage_on_way", "On the way", { id: "orders.on_way", aggregation: "sum" }, INT),
    measure("stage_refunded", "Refunded", { id: "orders.refunded_flag", aggregation: "sum" }, INT),
    measure("stage_recovered", "Recovered from carrier", { id: "orders.recovered_flag", aggregation: "sum" }, INT),
    measure("claims_pending", "Still claimable", { id: "recoveries.open_value", aggregation: "sum" }, MONEY),
    measure("evidence_avg", "Evidence on file", { id: "orders.evidence", aggregation: "avg" }, { format: "percentageFormat", formatOptions: { format: "#,##0%" } }),
    measure("transit_days", "Days in transit", { id: "orders.transit_days", aggregation: "avg" }, { format: "decimalFormat", formatOptions: { format: "#,##0.0" } }),
  ];

  const sources: AgDataSourcesDefinition = {
    sources: [
      {
        id: "orders", name: "Orders", primaryKeyField: "order_id", transactions: "all", data: ordersRows,
        fields: [
          f("order_id", "Order ID", { hide: true }), f("order_ref", "Order"), f("created_at", "Ordered", DATE), f("item", "Item"),
          f("category", "Category"), f("value", "Value", MONEY), f("currency", "Currency"), f("country", "Country"),
          f("district", "Area"), f("method", "Delivery method"), f("carrier", "Carrier"), f("address_check", "Address check"),
          f("protected", "Protected", { ...INT, hide: true }), f("had_case", "Had a case", { ...INT, hide: true }), f("evidence", "Evidence on file", PCT),
          f("transit_days", "Days in transit", { format: "decimalFormat", formatOptions: { format: "#,##0.0" } }), f("source", "Source"),
          f("shipped", "Shipped", { ...INT, hide: true }), f("delivered_ok", "Delivered, no problem", { ...INT, hide: true }),
          f("on_way", "On the way", { ...INT, hide: true }), f("refunded_flag", "Refunded", { ...INT, hide: true }),
          f("recovered_flag", "Recovered from carrier", { ...INT, hide: true }),
        ],
      },
      {
        id: "shipments", name: "Shipments", primaryKeyField: "shipment_id", transactions: "all", data: shipmentRows,
        fields: [
          f("shipment_id", "Shipment ID", { hide: true }), f("order_id", "Order ID", { hide: true }), f("carrier", "Carrier"),
          f("method", "Delivery method"), f("shipped_at", "Shipped", DATE), f("delivered_at", "Delivered", DATE),
          f("transit_days", "Days in transit", { format: "decimalFormat", formatOptions: { format: "#,##0.0" } }),
        ],
      },
      {
        id: "cases", name: "Cases", primaryKeyField: "case_id", transactions: "all", data: caseRows,
        fields: [
          f("case_id", "Case ID", { hide: true }), f("order_id", "Order ID", { hide: true }), f("opened_at", "Opened", DATE),
          f("failure_point", "Failure point"), f("stage", "Stage"), f("route", "Decision"), f("liable", "Liable party"),
          f("confidence", "Confidence"), f("carrier", "Carrier"), f("method", "Delivery method"), f("at_stake", "Order value", MONEY),
          f("refunded", "Refunded", MONEY), f("claimed", "Claimed from carrier", MONEY), f("recovered", "Recovered from carrier", MONEY),
          f("open", "Open", { ...INT, hide: true }),
        ],
      },
      {
        id: "money_actions", name: "Money actions", primaryKeyField: "action_id", transactions: "all", data: moneyRows,
        fields: [
          f("action_id", "Action ID", { hide: true }), f("case_id", "Case ID", { hide: true }), f("kind", "Kind"),
          f("status", "Status"), f("paid_out", "Refunded", MONEY), f("created_at", "Created", DATE),
        ],
      },
      {
        id: "recoveries", name: "Carrier claims", primaryKeyField: "recovery_id", transactions: "all", data: recoveryRows,
        fields: [
          f("recovery_id", "Claim ID", { hide: true }), f("case_id", "Case ID", { hide: true }), f("carrier", "Carrier"),
          f("failure_point", "Failure point"), f("status", "Claim status"), f("claimed", "Claimed", MONEY),
          f("recovered", "Recovered", MONEY), f("deadline_at", "Claim deadline", DATE), f("next_step", "Next step"),
          f("queue", "Queue", { ...INT, hide: true }), f("open_value", "Still claimable", { ...MONEY, hide: true }),
        ],
      },
    ],
    relationships,
    expressions,
    // Starts at the first order, so a weekly chart has no empty months in front.
    calendars: [calendarFrom(new Date(orders.reduce((m, o) => Math.min(m, Date.parse(o.created_at)), Date.now())).toISOString().slice(0, 10))],
  };
  return { sources, home, history: !!hist, live };
}

// ---------------------------------------------------------------- live updates

type Rows = Record<string, unknown>[];

/**
 * What changed between two builds of the same tables, as Studio transactions: rows to add, to
 * replace and to remove, matched on each table's primary key. Unchanged rows are left alone, so a
 * live widget only animates what really moved.
 */
export function diffTables(prev: AgDataSourcesDefinition, next: AgDataSourcesDefinition): AgDataTransaction[] {
  const out: AgDataTransaction[] = [];
  for (const src of next.sources) {
    const before = prev.sources.find((p) => p.id === src.id);
    const key = (src as { primaryKeyField?: string }).primaryKeyField;
    if (!before || !key) continue;
    const old = new Map(((before as { data: Rows }).data).map((r) => [r[key], JSON.stringify(r)]));
    const rows = (src as { data: Rows }).data;
    const seen = new Set(rows.map((r) => r[key]));
    const add = rows.filter((r) => !old.has(r[key]));
    const update = rows.filter((r) => old.has(r[key]) && old.get(r[key]) !== JSON.stringify(r));
    const remove = [...old.keys()].filter((k) => !seen.has(k));
    if (add.length || update.length || remove.length) {
      out.push({ tableId: src.id, ...(add.length && { add: add as never }), ...(update.length && { update: update as never }), ...(remove.length && { remove }) });
    }
  }
  return out;
}
