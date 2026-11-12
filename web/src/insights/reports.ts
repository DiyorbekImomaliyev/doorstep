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
 *  - Areas: where deliveries fail, on the delivery atlas.
 *
 * Every widget reads the model's measures (data.ts), so a number means the same thing everywhere.
 */
import type { AgPageState, AgReportState } from "ag-studio";
import { DEFAULTS, type DoorstepRegistry } from "./widgets";

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
const head = (text: string, sub?: string) => ({ title: { enabled: true, text }, subtitle: sub ? { enabled: true, text: sub, wrapping: true } : { enabled: false } });

/** A headline number, its weekly trend, and one line saying exactly what it counts. */
export const kpi = (id: string, what: string): Widget => ({
  type: "value",
  dataMapping: { value: [{ id }], sparklineX: [{ id: "calendar::week" }] },
  format: {
    caption: caption(what),
    style: { label: { enabled: true }, sparkline: { lineWidth: 2, areaFill: false } },
  },
});

const legendBottom = { common: { legend: { enabled: true, position: "bottom" } } } as const;
/** Many series: a legend on the right, wide enough for whole labels. */
const legendWrap = { common: { legend: { enabled: true, position: "right", item: { maxWidth: 170 } } } } as const;
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
      ...head("Orders and cases"),
      style: { theme: { ...legendBottom, line: { series: { marker: { enabled: false }, interpolation: { type: "smooth" } } } } },
    },
  },
  journey: { type: "doorstep-journey", ...DEFAULTS.journey },
  moneyFlow: { type: "doorstep-money-flow", ...DEFAULTS.moneyFlow },
  claimClock: { type: "doorstep-claim-clock", ...DEFAULTS.claimClock },
  methodMix: {
    type: "donut-chart",
    dataMapping: { categoryKey: [{ id: "orders.method" }], valueKey: [{ id: "order_count" }] },
    sort: [{ field: { id: "order_count" }, direction: "desc" }],
    format: {
      ...head("Delivery choice", "Share of orders"),
      style: { theme: legendBottom, custom: sliceLabels },
    },
  },
  caseRateByMethod: {
    type: "column-chart-grouped",
    dataMapping: { categoryKey: [{ id: "orders.method" }], valueKey: [{ id: "case_rate" }] },
    sort: [{ field: { id: "case_rate" }, direction: "desc" }],
    format: {
      ...head("Case rate by method"),
      style: { theme: { ...noLegend, ...barLabels } },
    },
  },
  failurePoints: { type: "doorstep-failure-points", ...DEFAULTS.failurePoints },
  evidenceByMethod: { type: "doorstep-evidence-meter", ...DEFAULTS.evidence },
  evidenceByCarrier: {
    type: "doorstep-evidence-meter",
    dataMapping: { category: [{ id: "orders.carrier" }], value: [{ id: "evidence_avg" }] },
    format: head("Proof by carrier"),
  },
  costByCarrier: {
    type: "bar-chart-grouped",
    dataMapping: { categoryKey: [{ id: "cases.carrier" }], valueKey: [{ id: "paid_out" }, { id: "recovered" }] },
    sort: [{ field: { id: "paid_out" }, direction: "desc" }],
    format: {
      ...head("Cost by carrier", "Refunded vs recovered"),
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
      ...head("Money by carrier"),
      style: { grandTotalRow: { enabled: true }, animateValueChanges: true },
    },
  },
  failuresByCarrier: {
    type: "bar-chart-stacked",
    dataMapping: { categoryKey: [{ id: "cases.carrier" }], valueKey: [{ id: "case_count" }], legendKey: [{ id: "cases.failure_point" }] },
    sort: [{ field: { id: "case_count" }, direction: "desc" }],
    format: {
      ...head("Causes by carrier"),
      style: { theme: legendWrap },
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
      ...head("Carrier scorecard"),
      style: { grandTotalRow: { enabled: true }, animateValueChanges: true },
    },
  },
  claimsByStatus: {
    type: "pivot-grid",
    dataMapping: { rows: [{ id: "recoveries.status" }], columns: [], values: [{ id: "claim_count" }, { id: "claim_value" }, { id: "claim_paid" }] },
    sort: [{ field: { id: "claim_count" }, direction: "desc" }],
    format: {
      ...head("Claims by status"),
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
    format: head("Carrier claims"),
  },
  casesByMonth: {
    type: "column-chart-stacked",
    dataMapping: { categoryKey: [{ id: "calendar::month" }], valueKey: [{ id: "case_count" }], legendKey: [{ id: "cases.failure_point" }] },
    sort: [{ field: { id: "calendar::month" }, direction: "asc" }],
    format: { ...head("Cases by month"), style: { theme: legendWrap } },
  },
  decisions: {
    type: "bar-chart-grouped",
    dataMapping: { categoryKey: [{ id: "cases.route" }], valueKey: [{ id: "case_count" }] },
    sort: [{ field: { id: "case_count" }, direction: "desc" }],
    format: {
      ...head("How cases were decided"),
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
    format: head("Every case"),
  },
  atlas: { type: "doorstep-atlas", ...DEFAULTS.atlas },
  /** The exact numbers behind the atlas, by postcode district. */
  areaTable: {
    type: "pivot-grid",
    dataMapping: {
      rows: [{ id: "orders.district" }],
      columns: [],
      values: [{ id: "order_count" }, { id: "orders_with_case" }, { id: "case_rate" }, { id: "area_loss" }],
    },
    sort: [{ field: { id: "order_count" }, direction: "desc" }],
    format: { ...head("Areas"), style: { grandTotalRow: { enabled: true } } },
  },
  /** S1: Doorstep's brief, the Overview's first words (widgets.tsx). Method is there to filter by. */
  brief: {
    type: "doorstep-brief",
    dataMapping: { orders: [{ id: "order_count" }], delivered: [{ id: "stage_delivered" }], method: [{ id: "orders.method" }] },
    format: {},
  },
  /** AS-02: the shared calendar gives every date a quarter; one tap filters the whole page. */
  filterQuarter: {
    type: "button-filter",
    dataMapping: { value: [{ id: "calendar::quarter" }] },
    format: { title: { enabled: true, text: "Quarter" }, subtitle: { enabled: false } },
  },
  filterSource: {
    type: "button-filter",
    dataMapping: { value: [{ id: "orders.source" }] },
    format: { title: { enabled: true, text: "Data" }, subtitle: { enabled: false } },
  },
  filterLiable: {
    type: "list-filter",
    dataMapping: { value: [{ id: "cases.liable" }] },
    format: { title: { enabled: true, text: "Who pays" }, subtitle: { enabled: false } },
  },
  filterFailure: {
    type: "list-filter",
    dataMapping: { value: [{ id: "cases.failure_point" }] },
    format: { title: { enabled: true, text: "Failure point" }, subtitle: { enabled: false } },
  },
} satisfies Record<string, Widget>;

// ---------------------------------------------------------------- the three reports

export const KPI_H = 11;

/** The trend stops at the start of this week, so a week in progress never reads as a collapse. */
const weekStart = (() => {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  d.setDate(d.getDate() - ((d.getDay() + 6) % 7));
  return d.toISOString();
})();

/** Filter widgets as part of the page header: no card around them. */
const bare = <T extends NonNullable<Widget>>(w: T): T => ({ ...w, format: { ...w.format, widget: { borderEnabled: false, backgroundColor: "transparent" } } }) as T;

type Layout = NonNullable<Page["widgetLayout"]>;
/** The same layout, moved down `n` tracks (room for something new on top). */
const below = (layout: Layout, n: number, from = 0): Layout =>
  Object.fromEntries(Object.entries(layout).map(([k, l]) => [k, l && (l.yTrack >= from ? { ...l, yTrack: l.yTrack + n } : l)])) as Layout;
const BRIEF_H = 23; // the brief: headline, signals and four Do next rows

export const overviewPage: Page = {
  id: "overview",
  widgets: {
    // Key "t", like the other reports' titles, so the carrier drill-through still drops it.
    t: bare(W.brief),
    period: bare(W.filterQuarter),
    src: bare(W.filterSource),
    k1: kpi("order_count", "All orders"),
    k2: kpi("case_rate", "Orders with a problem"),
    k3: kpi("prevention_rate", "Locker, signed or fixed address"),
    k4: kpi("net_loss", "Refunds minus recoveries"),
    journey: W.journey,
    trend: W.ordersAndCaseRate, flow: W.moneyFlow,
    fp: W.failurePoints, mix: W.methodMix,
    ev: W.evidenceByMethod, byMethod: W.caseRateByMethod,
  },
  widgetLayout: {
    t: { xTrack: 0, yTrack: 0, xSpan: 24, ySpan: BRIEF_H },
    ...below({
      period: { xTrack: 0, yTrack: 0, xSpan: 13, ySpan: 6 }, src: { xTrack: 13, yTrack: 0, xSpan: 11, ySpan: 6 },
      k1: { xTrack: 0, yTrack: 6, xSpan: 6, ySpan: KPI_H }, k2: { xTrack: 6, yTrack: 6, xSpan: 6, ySpan: KPI_H },
      k3: { xTrack: 12, yTrack: 6, xSpan: 6, ySpan: KPI_H }, k4: { xTrack: 18, yTrack: 6, xSpan: 6, ySpan: KPI_H },
      journey: { xTrack: 0, yTrack: 17, xSpan: 24, ySpan: 20 },
      trend: { xTrack: 0, yTrack: 37, xSpan: 15, ySpan: 20 }, flow: { xTrack: 15, yTrack: 37, xSpan: 9, ySpan: 20 },
      fp: { xTrack: 0, yTrack: 57, xSpan: 13, ySpan: 20 }, mix: { xTrack: 13, yTrack: 57, xSpan: 11, ySpan: 20 },
      ev: { xTrack: 0, yTrack: 77, xSpan: 10, ySpan: 12 }, byMethod: { xTrack: 10, yTrack: 77, xSpan: 14, ySpan: 12 },
    }, BRIEF_H),
  },
  filter: {
    widget: { trend: [{ field: { id: "orders.created_at" }, model: { operator: "lessThan", value: weekStart } }] },
  },
};

/** AS-18: one linear gauge per carrier, "won ÷ claimed" on a fixed 0 to 100% scale. */
const GAUGE_PREFIX = "g-";
const GAUGE_Y = 15;
const GAUGE_H = 8;
const gaugeKey = (carrier: string) => GAUGE_PREFIX + carrier.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");

function claimGauge(carrier: string | null): Widget {
  return {
    type: "linear-gauge",
    dataMapping: { value: [{ id: "won_share" }] },
    format: {
      ...head(carrier ?? "All carriers", "Won ÷ claimed"),
      // A ratio, so the scale is the same for every carrier and never mixes currencies. The value
      // sits past the track's end in dark ink (Studio's default is white), where a short bar still
      // shows it; the fixed 0-100% scale needs no ticks.
      style: {
        direction: "horizontal",
        thickness: 18,
        label: { enabled: true, placement: "outside-end", color: "#2b2523", typography: { fontSize: 15, fontWeight: "bold" } },
        scaleLabel: { enabled: false, min: 0, max: 1 },
      },
    },
  } as Widget;
}

/** The gauge widgets and their one row of layout, for the carriers the shop ships with. */
export function claimGauges(carriers: string[]): { widgets: Record<string, Widget>; layout: Layout; filter: Record<string, unknown[]> } {
  const names: (string | null)[] = carriers.length ? carriers.slice(0, 6) : [null];
  const span = Math.floor(24 / names.length);
  const widgets: Record<string, Widget> = {};
  const layout: Layout = {};
  const filter: Record<string, unknown[]> = {};
  names.forEach((c, i) => {
    const key = c ? gaugeKey(c) : `${GAUGE_PREFIX}all`;
    widgets[key] = claimGauge(c);
    layout[key] = { xTrack: i * span, yTrack: GAUGE_Y, xSpan: i === names.length - 1 ? 24 - i * span : span, ySpan: GAUGE_H };
    if (c) filter[key] = [{ field: { id: "orders.carrier" }, model: { operator: "equals", value: c } }];
  });
  return { widgets, layout, filter };
}

/**
 * The Carriers page with its gauges. Without carriers there is still one gauge for all of them, so
 * the row (which every other widget has made room for) is never empty.
 */
export function carriersPageFor(carriers: string[]): Page {
  const g = claimGauges(carriers);
  return {
    ...carriersPage,
    widgets: { ...carriersPage.widgets, ...g.widgets },
    widgetLayout: { ...below(carriersPage.widgetLayout as Layout, GAUGE_H, GAUGE_Y), ...g.layout },
    filter: { widget: g.filter } as Page["filter"],
  };
}

/** A saved Carriers page for another set of carriers: its gauges replaced, nothing else touched. */
export function regauge(page: Page, carriers: string[]): Page {
  const keep = (o: Record<string, unknown> | undefined) => Object.fromEntries(Object.entries(o ?? {}).filter(([k]) => !k.startsWith(GAUGE_PREFIX)));
  const g = claimGauges(carriers);
  const layout = keep(page.widgetLayout as Record<string, unknown>) as Layout;
  // A seller who has put something in the gauge row keeps it: the gauges are not forced over it.
  const taken = Object.values(layout).some((l) => l && l.yTrack < GAUGE_Y + GAUGE_H && l.yTrack + l.ySpan > GAUGE_Y);
  const widgetFilter = keep((page.filter as { widget?: Record<string, unknown> } | undefined)?.widget);
  return {
    ...page,
    widgets: { ...keep(page.widgets as Record<string, unknown>), ...(taken ? {} : g.widgets) } as Page["widgets"],
    widgetLayout: { ...layout, ...(taken ? {} : g.layout) },
    filter: { ...page.filter, widget: { ...widgetFilter, ...(taken ? {} : g.filter) } } as Page["filter"],
  };
}

export const carriersPage: Page = {
  id: "carriers",
  widgets: {
    t: title("Carriers"),
    k1: kpi("claimed", "Lost or damaged parcels"),
    k2: kpi("recovered", "Paid by carriers"),
    k3: kpi("recovery_rate", "Recovered ÷ claimed"),
    k4: kpi("transit_days", "Label to delivered"),
    clock: W.claimClock,
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
    clock: { xTrack: 0, yTrack: 15, xSpan: 24, ySpan: 14 },
    cost: { xTrack: 0, yTrack: 29, xSpan: 11, ySpan: 19 }, rate: { xTrack: 11, yTrack: 29, xSpan: 13, ySpan: 19 },
    money: { xTrack: 0, yTrack: 48, xSpan: 24, ySpan: 15 },
    score: { xTrack: 0, yTrack: 63, xSpan: 24, ySpan: 16 },
    pivot: { xTrack: 0, yTrack: 79, xSpan: 12, ySpan: 16 }, ev: { xTrack: 12, yTrack: 79, xSpan: 12, ySpan: 16 },
    claims: { xTrack: 0, yTrack: 95, xSpan: 24, ySpan: 20 },
  },
  filter: {},
};

export const casesPage: Page = {
  id: "cases",
  widgets: {
    t: title("Cases"),
    f1: W.filterLiable, f2: W.filterFailure,
    k1: kpi("case_count", "Problems reported"),
    k2: kpi("open_cases", "Not yet settled"),
    k3: kpi("paid_out", "Refunded via PayPal"),
    month: W.casesByMonth, route: W.decisions, fp: W.failurePoints,
    grid: W.casesGrid,
  },
  widgetLayout: {
    t: { xTrack: 0, yTrack: 0, xSpan: 24, ySpan: 4 },
    f1: { xTrack: 0, yTrack: 4, xSpan: 6, ySpan: KPI_H },
    k1: { xTrack: 6, yTrack: 4, xSpan: 6, ySpan: KPI_H }, k2: { xTrack: 12, yTrack: 4, xSpan: 6, ySpan: KPI_H }, k3: { xTrack: 18, yTrack: 4, xSpan: 6, ySpan: KPI_H },
    f2: { xTrack: 0, yTrack: 15, xSpan: 6, ySpan: 16 },
    month: { xTrack: 6, yTrack: 15, xSpan: 18, ySpan: 16 },
    route: { xTrack: 0, yTrack: 31, xSpan: 10, ySpan: 20 }, fp: { xTrack: 10, yTrack: 31, xSpan: 14, ySpan: 20 },
    grid: { xTrack: 0, yTrack: 51, xSpan: 24, ySpan: 22 },
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

export const areasPage: Page = {
  id: "areas",
  widgets: {
    t: title("Areas"),
    atlas: W.atlas,
    table: W.areaTable,
  },
  widgetLayout: {
    t: { xTrack: 0, yTrack: 0, xSpan: 24, ySpan: 4 },
    atlas: { xTrack: 0, yTrack: 4, xSpan: 24, ySpan: 30 },
    table: { xTrack: 0, yTrack: 34, xSpan: 24, ySpan: 23 },
  },
  filter: {},
};

export const REPORTS = [
  { id: "overview", name: "Overview", hint: "Delivery health and cost" },
  { id: "carriers", name: "Carriers", hint: "Cost and recovery" },
  { id: "cases", name: "Cases", hint: "Causes and decisions" },
  { id: "areas", name: "Areas", hint: "Where deliveries fail" },
];

const PANELS = { filters: { collapsed: true }, edit: { collapsed: true }, data: { collapsed: true }, ai: { collapsed: true } };

/** The reports a shop opens with, the Carriers page including a claim gauge for each of its carriers. */
export const reportStateFor = (carriers: string[]): AgReportState<DoorstepRegistry> => ({
  pages: [overviewPage, carriersPageFor(carriers), casesPage, areasPage],
  selectedPageId: "overview",
  panels: PANELS,
});
