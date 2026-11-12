/**
 * AS-06 Ask Doorstep, browser half. The server reads a question into a plan (measure, grouping,
 * period, highest or lowest) chosen from a fixed menu; it never sees a data row.
 *
 * Here, in code:
 *  - the plan becomes a native AG Studio widget on the "Answers" report, filtered to the period;
 *  - the answer's figures are computed from the same rows Studio holds, with the same
 *    definitions as the model's measures (data.ts), so the sentence and the chart agree and no
 *    number comes from a model.
 * Rates rank only groups with enough volume, and say so, so one order never tops a chart.
 */
import type { AgPageState } from "ag-studio";
import type { Prepared } from "./data";
import type { DoorstepRegistry } from "./widgets";

export type Metric =
  | "net_loss" | "paid_out" | "recovered" | "claimed" | "recovery_rate" | "case_count" | "open_cases"
  | "order_count" | "sales" | "case_rate" | "prevention_rate" | "evidence_avg" | "transit_days";
export type Group =
  | "none" | "carrier" | "delivery_method" | "failure_point" | "liable_party" | "decision" | "category" | "month" | "week";
export type Period = "all_time" | "this_month" | "last_30_days" | "last_90_days" | "this_year";

export type Plan = {
  answerable: boolean; metric: Metric; group_by: Group; period: Period; rank: "highest" | "lowest"; note: string;
};
export type Asked = { question: string; plan: Plan; mode: "model" | "keywords"; provider?: string | null };

export async function askDoorstep(token: string, question: string, signal?: AbortSignal): Promise<Asked> {
  const r = await fetch(`/api/seller/${encodeURIComponent(token)}/insights/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
    signal,
  });
  if (!r.ok) throw new Error(`ask ${r.status}`);
  return r.json();
}

// ---------------------------------------------------------------- the menu, as the model has it

type Row = Record<string, unknown>;
type Kind = "money" | "pct" | "int" | "days";
type Table = "cases" | "orders";
type Value = { value: number | null; base: number };

const sum = (rows: Row[], f: string) => {
  let s: number | null = null;
  for (const r of rows) { const v = r[f]; if (typeof v === "number") s = (s ?? 0) + v; }
  return s;
};
const avg = (rows: Row[], f: string): Value => {
  const vs = rows.map((r) => r[f]).filter((v): v is number => typeof v === "number");
  return { value: vs.length ? vs.reduce((a, b) => a + b, 0) / vs.length : null, base: vs.length };
};
const share = (rows: Row[], f: string): Value => ({ value: rows.length ? (sum(rows, f) ?? 0) / rows.length : null, base: rows.length });

type MetricDef = {
  table: Table; kind: Kind; label: string; noun: string; higherIsBetter: boolean;
  /** Smallest group a rate or average is ranked on; smaller groups are named but not ranked. */
  minBase?: number; baseNoun?: string;
  compute: (rows: Row[]) => Value;
};

export const METRICS: Record<Metric, MetricDef> = {
  net_loss: {
    table: "cases", kind: "money", label: "Net delivery loss", noun: "net delivery loss", higherIsBetter: false,
    compute: (r) => ({ value: r.length ? (sum(r, "refunded") ?? 0) - (sum(r, "recovered") ?? 0) : null, base: r.length }),
  },
  paid_out: { table: "cases", kind: "money", label: "Refunded", noun: "refunded to buyers", higherIsBetter: false, compute: (r) => ({ value: sum(r, "refunded"), base: r.length }) },
  recovered: { table: "cases", kind: "money", label: "Recovered from carriers", noun: "recovered from carriers", higherIsBetter: true, compute: (r) => ({ value: sum(r, "recovered"), base: r.length }) },
  claimed: { table: "cases", kind: "money", label: "Claimed from carriers", noun: "claimed from carriers", higherIsBetter: false, compute: (r) => ({ value: sum(r, "claimed"), base: r.length }) },
  recovery_rate: {
    table: "cases", kind: "pct", label: "Carrier recovery rate", noun: "carrier recovery rate", higherIsBetter: true, minBase: 3, baseNoun: "claims",
    compute: (r) => {
      const claimed = sum(r, "claimed"); const recovered = sum(r, "recovered");
      return { value: claimed && recovered != null ? recovered / claimed : null, base: r.filter((x) => typeof x.claimed === "number").length };
    },
  },
  case_count: { table: "cases", kind: "int", label: "Cases", noun: "cases", higherIsBetter: false, compute: (r) => ({ value: r.length, base: r.length }) },
  open_cases: { table: "cases", kind: "int", label: "Open cases", noun: "open cases", higherIsBetter: false, compute: (r) => ({ value: sum(r, "open") ?? 0, base: r.length }) },
  order_count: { table: "orders", kind: "int", label: "Orders", noun: "orders", higherIsBetter: true, compute: (r) => ({ value: r.length, base: r.length }) },
  sales: { table: "orders", kind: "money", label: "Sales", noun: "sales", higherIsBetter: true, compute: (r) => ({ value: sum(r, "value"), base: r.length }) },
  case_rate: { table: "orders", kind: "pct", label: "Case rate", noun: "case rate", higherIsBetter: false, minBase: 20, baseNoun: "orders", compute: (r) => share(r, "had_case") },
  prevention_rate: { table: "orders", kind: "pct", label: "Prevention rate", noun: "prevention rate", higherIsBetter: true, minBase: 20, baseNoun: "orders", compute: (r) => share(r, "protected") },
  evidence_avg: { table: "orders", kind: "pct", label: "Proof on file", noun: "proof on file", higherIsBetter: true, minBase: 20, baseNoun: "orders", compute: (r) => avg(r, "evidence") },
  transit_days: { table: "orders", kind: "days", label: "Days in transit", noun: "average days in transit", higherIsBetter: false, minBase: 10, baseNoun: "deliveries", compute: (r) => avg(r, "transit_days") },
};

const DATE_FIELD: Record<Table, string> = { cases: "opened_at", orders: "created_at" };

export const GROUPS: Record<Group, { label: string; plural: string; field?: Partial<Record<Table, string>> }> = {
  none: { label: "", plural: "" },
  carrier: { label: "by carrier", plural: "carriers", field: { cases: "carrier", orders: "carrier" } },
  delivery_method: { label: "by delivery method", plural: "delivery methods", field: { cases: "method", orders: "method" } },
  failure_point: { label: "by failure point", plural: "failure points", field: { cases: "failure_point" } },
  liable_party: { label: "by who pays", plural: "parties", field: { cases: "liable" } },
  decision: { label: "by decision", plural: "decisions", field: { cases: "route" } },
  category: { label: "by item category", plural: "categories", field: { orders: "category" } },
  month: { label: "by month", plural: "months" },
  week: { label: "by week", plural: "weeks" },
};

export const PERIODS: Record<Period, { label: string; phrase: string; since: (now: Date) => Date | null; filter?: object }> = {
  all_time: { label: "All time", phrase: "", since: () => null },
  this_month: { label: "This month", phrase: "this month", since: (n) => new Date(n.getFullYear(), n.getMonth(), 1), filter: { type: "this", unit: "month" } },
  last_30_days: { label: "Last 30 days", phrase: "in the last 30 days", since: (n) => startOfDay(n, 29), filter: { type: "last", value: 30, unit: "day", includeToday: true } },
  last_90_days: { label: "Last 90 days", phrase: "in the last 90 days", since: (n) => startOfDay(n, 89), filter: { type: "last", value: 90, unit: "day", includeToday: true } },
  this_year: { label: "This year", phrase: "this year", since: (n) => new Date(n.getFullYear(), 0, 1), filter: { type: "this", unit: "year" } },
};

function startOfDay(now: Date, daysBack: number) {
  const d = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  d.setDate(d.getDate() - daysBack);
  return d;
}

// ---------------------------------------------------------------- formatting

export function format(kind: Kind, v: number | null, home: Prepared["home"]) {
  if (v == null || Number.isNaN(v)) return "no data";
  if (kind === "money") return new Intl.NumberFormat("en-GB", { style: "currency", currency: home, maximumFractionDigits: 0 }).format(v);
  if (kind === "pct") return `${(v * 100).toFixed(1)}%`;
  if (kind === "days") return `${v.toFixed(1)} days`;
  return Math.round(v).toLocaleString("en-GB");
}

const monthKey = (d: Date) => d.toLocaleString("en-GB", { month: "short", year: "numeric" });
function weekKey(d: Date) {
  const m = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  m.setDate(m.getDate() - ((m.getDay() + 6) % 7));
  return `week of ${m.getDate()} ${m.toLocaleString("en-GB", { month: "short" })}`;
}

// ---------------------------------------------------------------- the answer, in code

export type Answer = {
  /** Plain sentences; **bold** marks the key figure. */
  headline: string;
  detail: string[];
  reading: string[];
  empty: boolean;
};

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
/** "1 delivery", "3 deliveries", "1 order". */
const counted = (n: number, plural: string) => (n === 1 ? plural.replace(/ies$/, "y").replace(/s$/, "") : plural);

export function answer(plan: Plan, prepared: Prepared, now = new Date()): Answer {
  const m = METRICS[plan.metric];
  const g = GROUPS[plan.group_by];
  const p = PERIODS[plan.period];
  const fmt = (v: number | null) => format(m.kind, v, prepared.home);
  const reading = [m.label, g.label ? cap(g.label) : "Total", p.label, ...(g.field || isTime(plan.group_by) ? [plan.rank === "lowest" ? "Lowest first" : "Highest first"] : [])];
  const table = (prepared.sources.sources.find((t) => t.id === m.table) as unknown as { data: Row[] }).data;
  const since = p.since(now);
  const rows = since ? table.filter((r) => (r[DATE_FIELD[m.table]] as Date) >= since) : table;
  const when = p.phrase ? ` ${p.phrase}` : "";

  if (!rows.length) {
    return { headline: `No ${m.table}${when} yet, so there is nothing to chart.`, detail: [], reading, empty: true };
  }

  if (plan.group_by === "none") {
    const v = m.compute(rows);
    const detail = m.minBase ? [`From ${v.base.toLocaleString("en-GB")} ${m.baseNoun}.`] : [];
    if (plan.metric === "net_loss") {
      detail.push(`Refunded ${format("money", sum(rows, "refunded") ?? 0, prepared.home)}, recovered from carriers ${format("money", sum(rows, "recovered") ?? 0, prepared.home)}.`);
    }
    return { headline: `${cap(m.noun)}${when}: **${fmt(v.value)}**.`, detail, reading, empty: false };
  }

  const keyOf = isTime(plan.group_by)
    ? (r: Row) => (plan.group_by === "month" ? monthKey : weekKey)(r[DATE_FIELD[m.table]] as Date)
    : (r: Row) => String(r[g.field![m.table]!] ?? "None");
  const buckets = new Map<string, Row[]>();
  const newest = new Map<string, number>();
  for (const r of rows) {
    const k = keyOf(r);
    buckets.set(k, [...(buckets.get(k) ?? []), r]);
    const t = (r[DATE_FIELD[m.table]] as Date).getTime();
    newest.set(k, Math.max(newest.get(k) ?? 0, t));
  }
  const groups = [...buckets].map(([key, rs]) => ({ key, ...m.compute(rs) })).filter((x) => x.value != null);
  if (!groups.length) return { headline: `No ${m.noun}${when} yet.`, detail: [], reading, empty: true };

  const lowest = plan.rank === "lowest";
  const order = (a: { value: number | null }, b: { value: number | null }) => (lowest ? a.value! - b.value! : b.value! - a.value!);

  if (isTime(plan.group_by)) {
    const unit = plan.group_by === "month" ? "month" : "week";
    const ranked = [...groups].sort(order);
    const latest = [...groups].sort((a, b) => (newest.get(b.key) ?? 0) - (newest.get(a.key) ?? 0))[0];
    return {
      headline: `${cap(m.noun)}${when}, ${unit} by ${unit}: ${lowest ? "lowest" : "highest"} in **${ranked[0].key}** at **${fmt(ranked[0].value)}**.`,
      detail: [`Latest, ${latest.key}: ${fmt(latest.value)}.`, `${groups.length} ${g.plural} in the chart.`],
      reading,
      empty: false,
    };
  }

  // Rates and averages are ranked only where there is enough volume to mean something.
  const eligible = m.minBase ? groups.filter((x) => x.base >= m.minBase!) : groups;
  const pool = (eligible.length ? eligible : groups).sort(order);
  const top = pool[0];
  const raw = [...groups].sort(order)[0];
  // AI-05: plain sentences whatever the measure ("Most cases: Royal Mail, 12", not "has the highest cases").
  const lead = plan.metric === "net_loss"
    ? `**${top.key}** cost you the ${lowest ? "least" : "most"}${when}: **${fmt(top.value)}** net delivery loss.`
    : m.kind === "int"
      ? `${lowest ? "Fewest" : "Most"} ${m.noun}${when}: **${top.key}**, **${fmt(top.value)}**.`
      : `${cap(m.noun)}${when} is ${lowest ? "lowest" : "highest"} for **${top.key}**: **${fmt(top.value)}**.`;
  const detail: string[] = [];
  if (m.minBase) {
    detail.push(`From ${top.base.toLocaleString("en-GB")} ${counted(top.base, m.baseNoun!)}.`);
    if (raw.key !== top.key) {
      detail.push(`${raw.key} shows ${fmt(raw.value)}, but from only ${raw.base} ${counted(raw.base, m.baseNoun!)}, so it is not ranked.`);
    }
  } else if (m.kind === "money" || m.kind === "int") {
    const total = m.compute(rows).value ?? 0;
    const others = pool.slice(1).filter((x) => x.value);
    if (!others.length && !lowest) detail.push(`No other ${GROUPS[plan.group_by].plural.replace(/s$/, "")} had any${when}.`);
    else if (total > 0 && top.value) detail.push(`That is ${Math.round((top.value / total) * 100)}% of ${fmt(total)} across all ${g.plural}.`);
  }
  const next = pool[1];
  if (next && (m.minBase || next.value)) detail.push(`Next: ${next.key} at ${fmt(next.value)}.`);
  return { headline: lead, detail, reading, empty: false };
}

const isTime = (g: Group) => g === "month" || g === "week";

// ---------------------------------------------------------------- the widget, as Studio state

type Page = AgPageState<DoorstepRegistry>;
type Widget = NonNullable<Page["widgets"]>[string];

export function widgetTitle(plan: Plan) {
  const m = METRICS[plan.metric];
  const g = GROUPS[plan.group_by];
  return `${m.label}${g.label ? ` ${g.label}` : ""} · ${PERIODS[plan.period].label}`;
}

export function widgetFor(plan: Plan, question: string): { widget: Widget; height: number; width: number } {
  const m = METRICS[plan.metric];
  const head = { title: { enabled: true, text: widgetTitle(plan) }, subtitle: { enabled: true, text: `You asked: “${question}”`, wrapping: true } };
  const noLegend = { common: { legend: { enabled: false } } } as const;
  if (plan.group_by === "none") {
    return {
      width: 8, height: 13,
      widget: {
        type: "value",
        dataMapping: { value: [{ id: plan.metric }], sparklineX: [{ id: "calendar::week" }] },
        format: {
          ...head,
          caption: { enabled: true, text: PERIODS[plan.period].label, wrapping: true },
          style: { label: { enabled: false }, sparkline: { lineWidth: 2, areaFill: false } },
        },
      } as Widget,
    };
  }
  if (isTime(plan.group_by)) {
    const x = `calendar::${plan.group_by}`;
    return {
      width: 24, height: 18,
      widget: {
        type: m.kind === "pct" || m.kind === "days" ? "line-chart" : "column-chart-grouped",
        dataMapping: { categoryKey: [{ id: x }], valueKey: [{ id: plan.metric }] },
        sort: [{ field: { id: x }, direction: "asc" }],
        format: { ...head, style: { theme: noLegend } },
      } as Widget,
    };
  }
  const field = `${m.table}.${GROUPS[plan.group_by].field![m.table]}`;
  return {
    width: 24, height: 18,
    widget: {
      type: "bar-chart-grouped",
      dataMapping: { categoryKey: [{ id: field }], valueKey: [{ id: plan.metric }] },
      sort: [{ field: { id: plan.metric }, direction: plan.rank === "lowest" ? "asc" : "desc" }],
      format: { ...head, style: { theme: { ...noLegend, bar: { series: { label: { enabled: true } } } } } },
    } as Widget,
  };
}

/** The widget-level filter that limits an answer to its period, on the measure's own date. */
export function periodFilter(plan: Plan) {
  const p = PERIODS[plan.period];
  if (!p.filter) return null;
  return {
    field: { id: `${METRICS[plan.metric].table}.${DATE_FIELD[METRICS[plan.metric].table]}` },
    model: { operator: "inRelativeRange", value: p.filter },
  };
}

export const ANSWERS_PAGE = "answers";

/** Puts an answer at the top of the Answers report, creating the report on the first answer. */
export function withAnswer(pages: Page[], id: string, plan: Plan, question: string): Page[] {
  const { widget, height, width } = widgetFor(plan, question);
  const existing = pages.find((p) => p.id === ANSWERS_PAGE);
  const base: Page = existing ?? {
    id: ANSWERS_PAGE,
    widgets: {
      t: {
        type: "text", dataMapping: {},
        format: {
          widget: { borderEnabled: false, backgroundColor: "transparent" },
          style: { text: "Answers", textAlign: "left", verticalAlign: "center", typography: { fontSize: 20, fontWeight: "bold" } },
        },
      } as Widget,
    },
    widgetLayout: { t: { xTrack: 0, yTrack: 0, xSpan: 24, ySpan: 4 } },
    filter: {},
  };
  const layout = Object.fromEntries(
    Object.entries(base.widgetLayout ?? {}).map(([k, l]) => [k, k === "t" || !l ? l : { ...l, yTrack: l.yTrack + height }]),
  );
  const pf = periodFilter(plan);
  const filter = base.filter ?? {};
  const page: Page = {
    ...base,
    widgets: { ...base.widgets, [id]: widget },
    widgetLayout: { ...layout, [id]: { xTrack: 0, yTrack: 4, xSpan: width, ySpan: height } } as Page["widgetLayout"],
    filter: pf ? { ...filter, widget: { ...(filter.widget ?? {}), [id]: [pf] } } as Page["filter"] : filter,
  };
  return existing ? pages.map((p) => (p.id === ANSWERS_PAGE ? page : p)) : [...pages, page];
}

/** Removes one answer; the widgets below it move up. */
export function withoutAnswer(pages: Page[], id: string): Page[] {
  return pages.map((p) => {
    if (p.id !== ANSWERS_PAGE || !p.widgets?.[id]) return p;
    const gone = p.widgetLayout?.[id];
    const widgets = { ...p.widgets }; delete widgets[id];
    const layout = Object.fromEntries(
      Object.entries(p.widgetLayout ?? {}).filter(([k]) => k !== id)
        .map(([k, l]) => [k, l && gone && l.yTrack > gone.yTrack ? { ...l, yTrack: l.yTrack - (gone.ySpan ?? 0) } : l]),
    ) as Page["widgetLayout"];
    const wf = { ...(p.filter?.widget ?? {}) }; delete wf[id];
    return { ...p, widgets, widgetLayout: layout, filter: { ...(p.filter ?? {}), widget: wf } as Page["filter"] };
  });
}
