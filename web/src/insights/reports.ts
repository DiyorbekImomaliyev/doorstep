/**
 * AS-05: the reports Insights opens with, as plain Studio state (AG's Report Builder pattern). A
 * seller can edit any of them or add a blank one.
 *
 * Each page reads top to bottom, the way a seller asks:
 *   1. the headline numbers, each with one line saying what it counts;
 *   2. the trend (better or worse?);
 *   3. the cause (where, which carrier, which method) and the money.
 *
 *  - Overview: are deliveries working, and what did failures cost?
 *  - Carriers: which carrier cost me most, and what did I claw back?
 *  - Cases: what went wrong, how it was decided, who paid.
 *
 * Every widget reads the model's measures (data.ts), so a number means the same thing everywhere.
 */
import type { AgPageState, AgReportState } from "ag-studio";
import type { DoorstepRegistry } from "./widgets";

type Page = AgPageState<DoorstepRegistry>;
type Widget = NonNullable<Page["widgets"]>[string];

const title = (text: string): Widget => ({
  type: "text",
  dataMapping: {},
  format: {
    widget: { borderEnabled: false, backgroundColor: "transparent" },
    style: { text, textAlign: "left", verticalAlign: "center", typography: { fontSize: 20, fontWeight: "bold" } },
  },
});

const caption = (text: string) => ({ enabled: true, text, wrapping: true });
const head = (text: string, sub: string) => ({ title: { enabled: true, text }, subtitle: { enabled: true, text: sub, wrapping: true } });

/** A headline number, its weekly trend, and one line saying exactly what it counts. */
export const kpi = (id: string, what: string): Widget => ({
  type: "value",
  dataMapping: { value: [{ id }], sparklineX: [{ id: "calendar::week" }] },
  format: {
    caption: caption(what),
    style: { label: { enabled: true }, sparkline: { lineWidth: 2, areaFill: true } },
  },
});

const legendBottom = { common: { legend: { enabled: true, position: "bottom" } } } as const;
const legendRight = { common: { legend: { enabled: true, position: "right" } } } as const;
/** The value on each bar (used where bars are wide enough to hold it). */
const barLabels = { bar: { series: { label: { enabled: true } } } } as const;
const noLegend = { common: { legend: { enabled: false } } } as const;
/** The share on each slice and the total in the middle. */
const sliceLabels = { pieDataLabel: { enabled: true, format: "percent", position: "outside" }, totalLabel: { enabled: true } } as const;

// ---------------------------------------------------------------- widgets

export const W = {
  ordersAndCaseRate: {
    type: "combo-chart-grouped-column-line",
    dataMapping: { categoryKey: [{ id: "calendar::week" }], valueKey: [{ id: "order_count" }], secondaryValueKey: [{ id: "case_rate" }] },
    sort: [{ field: { id: "calendar::week" }, direction: "asc" }],
    format: {
      ...head("Orders and case rate, week by week", "Bars: orders. Line: case rate"),
      style: { theme: { ...legendBottom, line: { series: { marker: { enabled: false }, interpolation: { type: "smooth" } } } } },
    },
  },
  moneyFlow: {
    type: "doorstep-money-flow",
    dataMapping: { refunded: [{ id: "paid_out" }], recovered: [{ id: "recovered" }] },
    format: head("Where the money went", "Refunded, recovered, net loss"),
  },
  methodMix: {
    type: "donut-chart",
    dataMapping: { categoryKey: [{ id: "orders.method" }], valueKey: [{ id: "order_count" }] },
    sort: [{ field: { id: "order_count" }, direction: "desc" }],
    format: {
      ...head("How buyers chose delivery", "Share of orders"),
      style: { theme: legendBottom, custom: sliceLabels },
    },
  },
  caseRateByMethod: {
    type: "column-chart-grouped",
    dataMapping: { categoryKey: [{ id: "orders.method" }], valueKey: [{ id: "case_rate" }] },
    sort: [{ field: { id: "case_rate" }, direction: "desc" }],
    format: {
      ...head("Case rate by delivery method", "Proof of handover means fewer cases"),
      style: { theme: { ...noLegend, ...barLabels } },
    },
  },
  failurePoints: {
    type: "doorstep-failure-points",
    dataMapping: { category: [{ id: "cases.failure_point" }], value: [{ id: "case_count" }], money: [{ id: "paid_out" }] },
    format: head("Where deliveries failed", "Cases by likely cause"),
  },
  evidenceByMethod: {
    type: "doorstep-evidence-meter",
    dataMapping: { category: [{ id: "orders.method" }], value: [{ id: "evidence_avg" }] },
    format: head("Proof on file, by delivery method", "Seal, signature or locker code"),
  },
  evidenceByCarrier: {
    type: "doorstep-evidence-meter",
    dataMapping: { category: [{ id: "orders.carrier" }], value: [{ id: "evidence_avg" }] },
    format: head("Proof on file, by carrier", "Average proof per order"),
  },
  costByCarrier: {
    type: "bar-chart-grouped",
    dataMapping: { categoryKey: [{ id: "cases.carrier" }], valueKey: [{ id: "paid_out" }, { id: "recovered" }] },
    sort: [{ field: { id: "paid_out" }, direction: "desc" }],
    format: {
      ...head("Which carrier cost you most", "Refunded vs recovered. Right-click for a report"),
      style: { theme: legendBottom },
    },
  },
  /** The exact numbers behind the chart above. Every measure lives on the cases table. */
  moneyByCarrier: {
    type: "pivot-grid",
    dataMapping: {
      rows: [{ id: "cases.carrier" }],
      columns: [],
      values: [{ id: "paid_out" }, { id: "claimed" }, { id: "recovered" }, { id: "recovery_rate" }, { id: "net_loss" }],
    },
    sort: [{ field: { id: "net_loss" }, direction: "desc" }],
    format: {
      ...head("Money by carrier", "Worst first"),
      style: { grandTotalRow: { enabled: true }, animateValueChanges: true },
    },
  },
  failuresByCarrier: {
    type: "bar-chart-stacked",
    dataMapping: { categoryKey: [{ id: "cases.carrier" }], valueKey: [{ id: "case_count" }], legendKey: [{ id: "cases.failure_point" }] },
    sort: [{ field: { id: "case_count" }, direction: "desc" }],
    format: {
      ...head("What goes wrong, by carrier", "Cases by likely cause"),
      style: { theme: legendRight },
    },
  },
  carrierScorecard: {
    type: "pivot-grid",
    dataMapping: {
      rows: [{ id: "orders.carrier" }],
      columns: [],
      values: [
        { id: "order_count" }, { id: "case_rate" }, { id: "prevention_rate" }, { id: "evidence_avg" }, { id: "transit_days" },
      ],
    },
    sort: [{ field: { id: "order_count" }, direction: "desc" }],
    format: {
      ...head("Service by carrier", "Volume, case rate, proof and speed"),
      style: { grandTotalRow: { enabled: true }, animateValueChanges: true },
    },
  },
  claimsByStatus: {
    type: "pivot-grid",
    dataMapping: { rows: [{ id: "recoveries.status" }], columns: [], values: [{ id: "claim_count" }, { id: "claim_value" }, { id: "claim_paid" }] },
    sort: [{ field: { id: "claim_count" }, direction: "desc" }],
    format: {
      ...head("Where your claims stand", "Doorstep prepares; you file"),
      style: { grandTotalRow: { enabled: true } },
    },
  },
  claimsGrid: {
    type: "grid",
    dataMapping: {
      cols: [
        { id: "recoveries.next_step" }, { id: "recoveries.deadline_at" }, { id: "recoveries.carrier" },
        { id: "recoveries.failure_point" }, { id: "recoveries.status" }, { id: "recoveries.claimed" }, { id: "recoveries.recovered" },
      ],
    },
    // Rows arrive ordered (data.ts): open claims by soonest deadline, then closed ones.
    format: head("Carrier claims", "Soonest deadline first"),
  },
  casesByMonth: {
    type: "column-chart-stacked",
    dataMapping: { categoryKey: [{ id: "calendar::month" }], valueKey: [{ id: "case_count" }], legendKey: [{ id: "cases.failure_point" }] },
    sort: [{ field: { id: "calendar::month" }, direction: "asc" }],
    format: { ...head("Cases by month", "By likely cause"), style: { theme: legendRight } },
  },
  decisions: {
    type: "bar-chart-grouped",
    dataMapping: { categoryKey: [{ id: "cases.route" }], valueKey: [{ id: "case_count" }] },
    sort: [{ field: { id: "case_count" }, direction: "desc" }],
    format: {
      ...head("How cases were decided", "Rules decide; conflicts go to a person"),
      style: { theme: { ...noLegend, ...barLabels } },
    },
  },
  casesGrid: {
    type: "grid",
    dataMapping: {
      cols: [
        { id: "cases.opened_at" }, { id: "cases.failure_point" }, { id: "cases.stage" }, { id: "cases.route" },
        { id: "cases.liable" }, { id: "cases.carrier" }, { id: "cases.at_stake" }, { id: "cases.refunded" },
      ],
    },
    sort: [{ field: { id: "cases.opened_at" }, direction: "desc" }],
    format: head("Every case", "Newest first. Click a row to filter"),
  },
  /** AS-02: the shared calendar gives every date a quarter; one tap filters the whole page. */
  filterQuarter: {
    type: "button-filter",
    dataMapping: { value: [{ id: "calendar::quarter" }] },
    format: { title: { enabled: true, text: "Quarter" } },
  },
  filterSource: {
    type: "button-filter",
    dataMapping: { value: [{ id: "orders.source" }] },
    format: { title: { enabled: true, text: "Data" } },
  },
  filterLiable: {
    type: "list-filter",
    dataMapping: { value: [{ id: "cases.liable" }] },
    format: { title: { enabled: true, text: "Who pays" } },
  },
  filterFailure: {
    type: "list-filter",
    dataMapping: { value: [{ id: "cases.failure_point" }] },
    format: { title: { enabled: true, text: "Failure point" } },
  },
} satisfies Record<string, Widget>;

// ---------------------------------------------------------------- the three reports

const KPI_H = 11;

export const overviewPage: Page = {
  id: "overview",
  widgets: {
    t: title("Overview"),
    period: W.filterQuarter,
    src: W.filterSource,
    k1: kpi("order_count", "Every order in the period"),
    k2: kpi("case_rate", "Orders with a reported problem"),
    k3: kpi("prevention_rate", "Locker, signature or fixed address"),
    k4: kpi("net_loss", "Refunds minus carrier payments"),
    trend: W.ordersAndCaseRate, flow: W.moneyFlow,
    fp: W.failurePoints, ev: W.evidenceByMethod,
    mix: W.methodMix, byMethod: W.caseRateByMethod,
  },
  widgetLayout: {
    t: { xTrack: 0, yTrack: 0, xSpan: 7, ySpan: 8 }, period: { xTrack: 7, yTrack: 0, xSpan: 10, ySpan: 8 },
    src: { xTrack: 17, yTrack: 0, xSpan: 7, ySpan: 8 },
    k1: { xTrack: 0, yTrack: 8, xSpan: 6, ySpan: KPI_H }, k2: { xTrack: 6, yTrack: 8, xSpan: 6, ySpan: KPI_H },
    k3: { xTrack: 12, yTrack: 8, xSpan: 6, ySpan: KPI_H }, k4: { xTrack: 18, yTrack: 8, xSpan: 6, ySpan: KPI_H },
    trend: { xTrack: 0, yTrack: 19, xSpan: 16, ySpan: 19 }, flow: { xTrack: 16, yTrack: 19, xSpan: 8, ySpan: 19 },
    fp: { xTrack: 0, yTrack: 38, xSpan: 13, ySpan: 21 }, ev: { xTrack: 13, yTrack: 38, xSpan: 11, ySpan: 21 },
    mix: { xTrack: 0, yTrack: 59, xSpan: 10, ySpan: 18 }, byMethod: { xTrack: 10, yTrack: 59, xSpan: 14, ySpan: 18 },
  },
  filter: {},
};

export const carriersPage: Page = {
  id: "carriers",
  widgets: {
    t: title("Carriers"),
    k1: kpi("claimed", "Claims for lost or damaged parcels"),
    k2: kpi("recovered", "What carriers actually paid"),
    k3: kpi("recovery_rate", "Recovered ÷ claimed"),
    k4: kpi("transit_days", "From label to delivered, on average"),
    cost: W.costByCarrier, rate: W.failuresByCarrier,
    money: W.moneyByCarrier,
    score: W.carrierScorecard,
    pivot: W.claimsByStatus, ev: W.evidenceByCarrier,
    claims: W.claimsGrid,
  },
  widgetLayout: {
    t: { xTrack: 0, yTrack: 0, xSpan: 24, ySpan: 4 },
    k1: { xTrack: 0, yTrack: 4, xSpan: 6, ySpan: KPI_H }, k2: { xTrack: 6, yTrack: 4, xSpan: 6, ySpan: KPI_H },
    k3: { xTrack: 12, yTrack: 4, xSpan: 6, ySpan: KPI_H }, k4: { xTrack: 18, yTrack: 4, xSpan: 6, ySpan: KPI_H },
    cost: { xTrack: 0, yTrack: 15, xSpan: 11, ySpan: 19 }, rate: { xTrack: 11, yTrack: 15, xSpan: 13, ySpan: 19 },
    money: { xTrack: 0, yTrack: 34, xSpan: 24, ySpan: 15 },
    score: { xTrack: 0, yTrack: 49, xSpan: 24, ySpan: 16 },
    pivot: { xTrack: 0, yTrack: 65, xSpan: 12, ySpan: 16 }, ev: { xTrack: 12, yTrack: 65, xSpan: 12, ySpan: 16 },
    claims: { xTrack: 0, yTrack: 81, xSpan: 24, ySpan: 20 },
  },
  filter: {},
};

export const casesPage: Page = {
  id: "cases",
  widgets: {
    t: title("Cases"),
    f1: W.filterLiable, f2: W.filterFailure,
    k1: kpi("case_count", "Problems buyers reported"),
    k2: kpi("open_cases", "Not settled or closed yet"),
    k3: kpi("paid_out", "Refunds completed through PayPal"),
    month: W.casesByMonth, route: W.decisions, fp: W.failurePoints,
    grid: W.casesGrid,
  },
  widgetLayout: {
    t: { xTrack: 0, yTrack: 0, xSpan: 24, ySpan: 4 },
    f1: { xTrack: 0, yTrack: 4, xSpan: 6, ySpan: 13 },
    k1: { xTrack: 6, yTrack: 4, xSpan: 6, ySpan: KPI_H }, k2: { xTrack: 12, yTrack: 4, xSpan: 6, ySpan: KPI_H }, k3: { xTrack: 18, yTrack: 4, xSpan: 6, ySpan: KPI_H },
    f2: { xTrack: 0, yTrack: 17, xSpan: 6, ySpan: 16 },
    month: { xTrack: 6, yTrack: 15, xSpan: 18, ySpan: 18 },
    route: { xTrack: 0, yTrack: 33, xSpan: 10, ySpan: 20 }, fp: { xTrack: 10, yTrack: 33, xSpan: 14, ySpan: 20 },
    grid: { xTrack: 0, yTrack: 53, xSpan: 24, ySpan: 22 },
  },
  filter: {},
};

/** The Carriers report inside the drill-through popup: the popup's header names the carrier. */
export const carrierDrillPage: Page = {
  ...carriersPage,
  id: "carrier-drill",
  widgets: Object.fromEntries(Object.entries(carriersPage.widgets ?? {}).filter(([k]) => k !== "t")),
  widgetLayout: Object.fromEntries(
    Object.entries(carriersPage.widgetLayout ?? {}).filter(([k, l]) => k !== "t" && l).map(([k, l]) => [k, { ...l!, yTrack: l!.yTrack - 4 }]),
  ) as Page["widgetLayout"],
};

export const REPORTS = [
  { id: "overview", name: "Overview", hint: "Are deliveries working, and what did failures cost?" },
  { id: "carriers", name: "Carriers", hint: "Which carrier cost you most, and what you won back" },
  { id: "cases", name: "Cases", hint: "What went wrong, how it was decided, who paid" },
];

export const reportState: AgReportState<DoorstepRegistry> = {
  pages: [overviewPage, carriersPage, casesPage],
  selectedPageId: "overview",
  panels: { filters: { collapsed: true }, edit: { collapsed: true }, data: { collapsed: true } },
};
