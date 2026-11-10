/**
 * AS-04: Doorstep's own Studio widgets, written as React components (the Bar Gauge pattern from
 * AG's Widget Library demo). Each reads through `widgetApi.getData`, so it follows page filters,
 * cross filters and live updates exactly like a built-in widget.
 *
 *  - Parcel journey: every order from payment to who paid for a problem, as a flow; the few
 *    orders with a case are drawn again, zoomed in, so they are readable next to the many.
 *  - Liability waterfall: refunded to buyers, minus what carriers paid back, equals the net loss,
 *    with the part still claimable from carriers marked inside it.
 *  - Claim clock: open carrier claims as dials counting down to their deadlines.
 *  - Evidence meter: how much proof is on file, per delivery method or carrier, in five segments
 *    like the Control Room's Proof column. Click a row to filter the page.
 *  - Failure points: every case cause ranked, with its share and the money it cost. Click a row
 *    to filter the page; another widget's selection shows as a solid bar over a faint total.
 */
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type {
  AgBaseRegistry, AgBaseWidgetDefinition, AgDefaultWidgetDefinition, AgWidgetApi, AgWidgetDataFormat, AgWidgetField,
  AgWidgetFieldReference, AgWidgetFormParams, AgWidgetParams,
} from "ag-studio";
import { Says } from "../components/ai";
import Doorstep3D from "../components/Doorstep3D";
import type { Prepared } from "./data";
import { brief, headline, type DoNext } from "./story";

// ---------------------------------------------------------------- types

type Ref = AgWidgetFieldReference[];

export type MeterDef = {
  type: "doorstep-evidence-meter";
  dataMapping: { category: Ref; value: Ref };
  format?: AgWidgetDataFormat<{ goodFrom?: number; okFrom?: number }>;
};
export type FailureDef = {
  type: "doorstep-failure-points";
  dataMapping: { category: Ref; value: Ref; money: Ref };
  format?: AgWidgetDataFormat<unknown>;
};
export type FlowDef = {
  type: "doorstep-money-flow";
  dataMapping: { refunded: Ref; recovered: Ref; pending?: Ref };
  format?: AgWidgetDataFormat<unknown>;
};
export type JourneyDef = {
  type: "doorstep-journey";
  dataMapping: { orders: Ref; shipped: Ref; delivered: Ref; onWay: Ref; cases: Ref; refunded: Ref; recovered: Ref };
  format?: AgWidgetDataFormat<unknown>;
};
export type ClockDef = {
  type: "doorstep-claim-clock";
  dataMapping: { carrier: Ref; deadline: Ref; amount: Ref; step: Ref };
  format?: AgWidgetDataFormat<unknown>;
};

export type BriefDef = {
  type: "doorstep-brief";
  dataMapping: { orders: Ref; delivered: Ref; method?: Ref };
  format?: AgWidgetDataFormat<unknown>;
};

/** What the brief needs from the board (Studio's `context`): the newest rows, and two ways out. */
export type BriefContext = {
  brief?: { latest: () => Prepared; open: (orderId: string) => void; drill: (carrier: string) => void; chase?: (carrier: string) => void };
};

export interface DoorstepRegistry extends AgBaseRegistry {
  widgets: readonly (
    | AgDefaultWidgetDefinition
    | AgBaseWidgetDefinition<"doorstep-evidence-meter", MeterDef>
    | AgBaseWidgetDefinition<"doorstep-failure-points", FailureDef>
    | AgBaseWidgetDefinition<"doorstep-money-flow", FlowDef>
    | AgBaseWidgetDefinition<"doorstep-journey", JourneyDef>
    | AgBaseWidgetDefinition<"doorstep-claim-clock", ClockDef>
    | AgBaseWidgetDefinition<"doorstep-brief", BriefDef>
  )[];
}

type Row = Record<string, unknown>;
type AnyDef = MeterDef | FailureDef | FlowDef | JourneyDef | ClockDef | BriefDef;
type Loaded = { rows: Row[]; hi: Row[] | null };
type LoadOpts = { min?: number; highlight?: boolean; quiet?: boolean };

/**
 * Shared loading: one query, superseded loads ignored, display states set like a built-in widget.
 * `highlight` asks Studio for the rows without other widgets' cross filters plus the filtered
 * rows, so a list can keep every row and mark the selected part. `quiet` leaves the widget's
 * display state to its main query.
 */
function useWidgetRows<D extends AnyDef>(
  params: AgWidgetParams<D>, fields: (AgWidgetField | undefined)[], sortBy: AgWidgetField | undefined, opts: LoadOpts = {},
): Loaded | null {
  // Rows are kept with the fields they were fetched for: when Studio reuses this widget on another
  // page, the old rows are never drawn against the new fields.
  const sig = fields.map((f) => f?.key ?? "").join("|");
  const [got, setGot] = useState<(Loaded & { sig: string }) | null>(null);
  const loaded = useRef(false);
  useEffect(() => {
    let current = true;
    const { widgetApi } = params;
    const need = fields.filter(Boolean) as AgWidgetField[];
    if (need.length < (opts.min ?? 2)) { if (!opts.quiet) widgetApi.setDisplayState("incompleteDataMapping"); return; }
    if (!opts.quiet) widgetApi.setDisplayState("loading", { prominent: !loaded.current });
    widgetApi.getData(
      { fields: need, sort: sortBy ? [{ field: sortBy, direction: "desc" }] : [] },
      opts.highlight ? { crossFilter: "highlight" } : undefined,
    ).then((res) => {
      if (!current) return;
      const rows = res.results.rows as Row[];
      loaded.current = true;
      if (!opts.quiet) widgetApi.setDisplayState(rows.length ? "displayed" : "noData");
      setGot({ sig, rows, hi: (res.crossFilter?.rows as Row[] | undefined) ?? null });
    }).catch(() => { if (current && !opts.quiet) widgetApi.setDisplayState("noData"); });
    return () => { current = false; };
    // Studio hands over new params on every refresh, so each one loads again.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params]);
  return got?.sig === sig ? got : null;
}

/** This widget's own cross-filter selection (values of its category field), and a toggle. */
function useSelection(widgetApi: AgWidgetApi, field: AgWidgetField | undefined) {
  const picked = new Set<unknown>();
  for (const s of widgetApi.getCrossFilterSelections() ?? []) if (s.type === "value") s.values.forEach((v) => picked.add(v));
  const toggle = (value: unknown) => field && widgetApi.toggleCrossFilter({ type: "value", field, value, group: 0 });
  return { picked, toggle };
}

/** The element's size, kept up to date (for drawing SVG at real pixels, not a stretched viewBox). */
function useSize<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setSize({ w: Math.round(e.contentRect.width), h: Math.round(e.contentRect.height) }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, size] as const;
}

const num = (v: unknown) => Number(v ?? 0) || 0;
const count = (n: number) => Math.round(n).toLocaleString("en-GB");
const pctOf = (n: number, of: number) => {
  if (of <= 0) return "—";
  const p = Math.round((n / of) * 100);
  return p === 0 && n > 0 ? "<1%" : `${p}%`;
};
const keyOf = (v: unknown) => (v instanceof Date ? v.getTime() : String(v));

// ---------------------------------------------------------------- parcel journey

/** The least distance between two label blocks (name over figure) in one journey column. */
const LABEL_GAP = 30;
type JNode = { id: string; label: string; v: number; col: number; tone: "base" | "good" | "bad" | "muted"; of: number };
type JFlow = { from: string; to: string };
type Placed = { x: number; y: number; h: number; n: JNode };

/**
 * A Sankey drawn by hand. The left half is every order at one scale; the orders with a problem
 * run through a lens into the right half, redrawn at a larger scale, so a 2% problem reads as
 * clearly as the 98% that went well. Narrow cards get the same numbers as a list.
 */
export function Journey(params: AgWidgetParams<JourneyDef>) {
  const { dataMapping: m } = params;
  const fields = [m.orders?.[0], m.shipped?.[0], m.delivered?.[0], m.onWay?.[0], m.cases?.[0], m.refunded?.[0], m.recovered?.[0]];
  const got = useWidgetRows(params, fields, undefined, { min: 7 });
  const [box, size] = useSize<HTMLDivElement>();
  const row = got?.rows[0];
  const [N, S, D, W, C, R, K] = fields.map((f) => (row && f ? num(row[f.key]) : 0));
  const nodes = ([
    { id: "orders", label: "Paid orders", v: N, col: 0, tone: "base", of: N },
    { id: "shipped", label: "Shipped", v: S, col: 1, tone: "base", of: N },
    { id: "unshipped", label: "Not shipped", v: Math.max(0, N - S), col: 1, tone: "muted", of: N },
    { id: "delivered", label: "Delivered, no problem", v: D, col: 2, tone: "good", of: N },
    { id: "onway", label: "On the way", v: W, col: 2, tone: "muted", of: N },
    { id: "case", label: "Problem reported", v: C, col: 2, tone: "bad", of: N },
    { id: "lens", label: "Cases", v: C, col: 3, tone: "bad", of: C },
    { id: "refunded", label: "Refunded", v: R, col: 4, tone: "bad", of: C },
    { id: "norefund", label: "No refund", v: Math.max(0, C - R), col: 4, tone: "muted", of: C },
    { id: "recovered", label: "Carrier paid back", v: K, col: 5, tone: "good", of: C },
    { id: "loss", label: "Seller's loss", v: Math.max(0, R - K), col: 5, tone: "bad", of: C },
  ] as JNode[]).filter((n) => n.v > 0 || n.id === "orders");
  const flows: JFlow[] = [
    { from: "orders", to: "shipped" }, { from: "orders", to: "unshipped" },
    { from: "shipped", to: "delivered" }, { from: "shipped", to: "onway" }, { from: "shipped", to: "case" },
    { from: "lens", to: "refunded" }, { from: "lens", to: "norefund" },
    { from: "refunded", to: "recovered" }, { from: "refunded", to: "loss" },
  ];
  const summary = `${count(N)} paid orders: ${count(D)} delivered with no problem, ${count(W)} on the way, ${count(C)} with a problem; `
    + `${count(R)} refunded, ${count(K)} paid back by the carrier.`;
  const name = (id: string) => nodes.find((n) => n.id === id)?.label ?? id;

  const { w, h } = size;
  const wide = w >= 600;
  const NW = 8, GAP = 34, CAP = 24, TOP = CAP + 8, H = Math.max(0, h - TOP - 24);
  const LABW = Math.min(158, Math.max(118, w * 0.15));
  const LW = w * 0.54, LENS = Math.min(80, Math.max(40, w * 0.06)), RX = LW + LENS;
  const X = [0, (LW - LABW - NW) / 2, LW - LABW - NW, RX, (RX + w - LABW - NW) / 2, w - LABW - NW];
  const most = (cols: number[]) => Math.max(1, ...cols.map((c) => nodes.filter((n) => n.col === c).length));
  const scaleL = N > 0 ? (H - (most([0, 1, 2]) - 1) * GAP) / N : 0;
  const scaleR = C > 0 ? (H - (most([3, 4, 5]) - 1) * GAP) / C : 0;
  const at = new Map<string, Placed>();
  if (got && wide && H > 80) {
    for (let c = 0; c <= 5; c++) {
      let y = TOP;
      for (const n of nodes.filter((x) => x.col === c)) {
        const hh = Math.max(2, n.v * (c >= 3 ? scaleR : scaleL));
        at.set(n.id, { x: X[c], y, h: hh, n });
        y += hh + GAP;
      }
    }
  }
  // Labels in one column keep at least LABEL_GAP apart (small nodes sit close together), and stay
  // inside the plot; a label moved off its node gets a hairline leader back to it.
  const labelAt = new Map<string, number>();
  for (let c = 0; c <= 5; c++) {
    const col = [...at.values()].filter((p) => p.n.col === c).sort((p, q) => p.y - q.y);
    let prev = -Infinity;
    const ys = col.map((p) => (prev = Math.max(p.y + p.h / 2, prev + LABEL_GAP)));
    let limit = h - 18;
    for (let i = ys.length - 1; i >= 0; i--) { ys[i] = Math.min(ys[i], limit); limit = ys[i] - LABEL_GAP; }
    col.forEach((p, i) => labelAt.set(p.n.id, ys[i]));
  }
  const used = new Map<string, number>();
  const ribbons = flows.flatMap((f) => {
    const a = at.get(f.from), b = at.get(f.to);
    if (!a || !b) return [];
    const off = used.get(f.from) ?? 0;
    used.set(f.from, off + b.h);
    const sx = a.x + NW, tx = b.x, mx = (sx + tx) / 2, s0 = a.y + off, s1 = Math.min(a.y + a.h, s0 + b.h);
    const d = `M${sx},${s0}C${mx},${s0} ${mx},${b.y} ${tx},${b.y}L${tx},${b.y + b.h}C${mx},${b.y + b.h} ${mx},${s1} ${sx},${s1}Z`;
    return [{ d, tone: b.n.tone, id: `${f.from}-${f.to}`, tip: `${name(f.from)} → ${b.n.label}: ${count(b.n.v)}` }];
  });
  const cs = at.get("case"), lz = at.get("lens");
  const zoom = scaleL > 0 ? Math.round(scaleR / scaleL) : 0;

  return (
    <div className="ds-journey" ref={box} role="img" aria-label={summary}>
      {got && !wide && w > 0 && (
        <ul className="ds-journey-list" aria-hidden>
          {nodes.filter((n) => n.id !== "lens").map((n) => (
            <li key={n.id} data-tone={n.tone}>
              <span>{n.label}</span><strong>{count(n.v)}</strong><em>{pctOf(n.v, n.of)}{n.col >= 3 ? " of cases" : ""}</em>
            </li>
          ))}
        </ul>
      )}
      {at.size > 0 && (
        <svg width={w} height={h} aria-hidden>
          <defs>
            <linearGradient id="ds-lens" x1="0" x2="1">
              <stop offset="0" stopColor="var(--ds-bad)" stopOpacity=".34" /><stop offset="1" stopColor="var(--ds-bad)" stopOpacity=".07" />
            </linearGradient>
          </defs>
          <text x={0} y={14} className="ds-j-cap">All {count(N)} orders</text>
          {C > 0 && <text x={RX} y={14} className="ds-j-cap" data-tone="bad">The {count(C)} with a problem, zoomed in ×{zoom}</text>}
          {cs && lz && (
            <path className="ds-j-lens"
              d={`M${cs.x + NW},${cs.y}L${LW},${cs.y}L${lz.x},${lz.y}L${lz.x},${lz.y + lz.h}L${LW},${cs.y + cs.h}L${cs.x + NW},${cs.y + cs.h}Z`} />
          )}
          {ribbons.map((r) => <path key={r.id} d={r.d} className="ds-j-ribbon" data-tone={r.tone}><title>{r.tip}</title></path>)}
          {[...at.values()].map(({ x, y, h: hh, n }) => {
            const cy = y + hh / 2;
            const ly = labelAt.get(n.id) ?? cy;
            return (
              <g key={n.id} className="ds-j-node" data-tone={n.tone}>
                <rect x={x} y={y} width={NW} height={hh} rx={2}><title>{`${n.label}: ${count(n.v)}`}</title></rect>
                {Math.abs(ly - cy) > 4 && <path className="ds-j-leader" d={`M${x + NW} ${cy}H${x + NW + 3}V${ly}H${x + NW + 5}`} />}
                <text x={x + NW + 7} y={ly - 7} dominantBaseline="middle" className="ds-j-label">{n.label}</text>
                <text x={x + NW + 7} y={ly + 9} dominantBaseline="middle" className="ds-j-value">
                  {count(n.v)}<tspan className="ds-j-pct"> · {pctOf(n.v, n.of)}{n.col >= 4 ? " of cases" : ""}</tspan>
                </text>
              </g>
            );
          })}
        </svg>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- S3: placed by Studio's agents

/**
 * What each data widget shows when it is placed by type alone (by the seller from the menu, or by
 * Studio's page agent): the mapping and title the built reports use, so it never arrives blank.
 * reports.ts builds its pages from the same objects.
 */
const titled = (text: string) => ({ title: { enabled: true, text }, subtitle: { enabled: false } });
export const DEFAULTS = {
  journey: {
    dataMapping: {
      orders: [{ id: "order_count" }], shipped: [{ id: "stage_shipped" }], delivered: [{ id: "stage_delivered" }],
      onWay: [{ id: "stage_on_way" }], cases: [{ id: "orders_with_case" }], refunded: [{ id: "stage_refunded" }],
      recovered: [{ id: "stage_recovered" }],
    },
    format: titled("Order journey"),
  } satisfies Partial<JourneyDef>,
  moneyFlow: {
    dataMapping: { refunded: [{ id: "paid_out" }], recovered: [{ id: "recovered" }], pending: [{ id: "claims_pending" }] },
    format: titled("Where the money went"),
  } satisfies Partial<FlowDef>,
  claimClock: {
    dataMapping: {
      carrier: [{ id: "recoveries.carrier" }], deadline: [{ id: "recoveries.deadline_at" }],
      amount: [{ id: "recoveries.claimed" }], step: [{ id: "recoveries.next_step" }],
    },
    format: titled("Claim deadlines"),
  } satisfies Partial<ClockDef>,
  evidence: {
    dataMapping: { category: [{ id: "orders.method" }], value: [{ id: "evidence_avg" }] },
    format: titled("Proof by method"),
  } satisfies Partial<MeterDef>,
  failurePoints: {
    dataMapping: { category: [{ id: "cases.failure_point" }], value: [{ id: "case_count" }], money: [{ id: "paid_out" }] },
    format: titled("Failure points"),
  } satisfies Partial<FailureDef>,
};

const BY_TYPE: Record<string, { dataMapping: Record<string, unknown>; format: unknown }> = {
  "doorstep-journey": DEFAULTS.journey, "doorstep-money-flow": DEFAULTS.moneyFlow, "doorstep-claim-clock": DEFAULTS.claimClock,
  "doorstep-evidence-meter": DEFAULTS.evidence, "doorstep-failure-points": DEFAULTS.failurePoints,
};
type WidgetLike = { type: string; dataMapping?: Record<string, unknown>; format?: unknown };

/**
 * Studio's page agent places a widget as `{ type }` alone; the field mapping is left to a second
 * agent. A Doorstep widget always has one right mapping, so fill it in at once. Returns the pages
 * to write back, or null when nothing needed filling.
 */
export function withDoorstepDefaults<P extends { widgets?: Record<string, unknown> }>(pages: P[]): P[] | null {
  let changed = false;
  const next = pages.map((page) => {
    const ws = page.widgets as Record<string, WidgetLike> | undefined;
    if (!ws) return page;
    let pageChanged = false;
    const out: Record<string, WidgetLike> = {};
    for (const [id, w] of Object.entries(ws)) {
      const d = BY_TYPE[w.type];
      if (d && !Object.keys(w.dataMapping ?? {}).length) {
        out[id] = { ...w, dataMapping: d.dataMapping, format: w.format ?? d.format };
        pageChanged = true;
      } else out[id] = w;
    }
    if (!pageChanged) return page;
    changed = true;
    return { ...page, widgets: out };
  });
  return changed ? next : null;
}

/** If a widget ends up with no title, one quiet line still says what it is. A `<p>`, never a row. */
function captioned<T extends AnyDef>(Comp: (p: AgWidgetParams<T>) => React.ReactNode, label: string) {
  const C = (p: AgWidgetParams<T>) => {
    const t = (p as { format?: AgWidgetDataFormat<unknown> }).format?.title;
    return (
      <>
        {!(t?.enabled && t.text) && <p className="ds-cap">{label}</p>}
        <Comp {...p} />
      </>
    );
  };
  C.displayName = `Captioned(${label})`;
  return C;
}

const JOURNEY_ICON = `<svg width="32" height="32" viewBox="0 0 32 32" xmlns="http://www.w3.org/2000/svg"><rect x="2" y="5" width="3" height="22" rx="1" fill="#5a524e"/><path d="M5 5C11 5 11 4 17 4V21C11 21 11 23 5 23Z" fill="#3f6f73" fill-opacity=".45"/><path d="M5 23C11 23 11 26 17 26V28C11 28 11 27 5 27Z" fill="#7b2d3d" fill-opacity=".6"/><rect x="17" y="4" width="3" height="17" rx="1" fill="#3f6f73"/><rect x="17" y="25" width="3" height="3" rx="1" fill="#7b2d3d"/><path d="M20 25L27 6V28L20 28Z" fill="#7b2d3d" fill-opacity=".18"/><rect x="27" y="6" width="3" height="22" rx="1" fill="#7b2d3d"/></svg>`;

export const journeyWidget: AgBaseWidgetDefinition<"doorstep-journey", JourneyDef> = {
  id: "doorstep-journey",
  label: "Parcel journey",
  icon: JOURNEY_ICON,
  dataMapping: {
    orders: { type: "field", supportedRoles: ["numeric"], required: true, intent: "value", aiDescription: "All orders paid (order_count)." },
    shipped: { type: "field", supportedRoles: ["numeric"], required: true, intent: "value", aiDescription: "Orders handed to a carrier (stage_shipped)." },
    delivered: { type: "field", supportedRoles: ["numeric"], required: true, intent: "value", aiDescription: "Orders delivered with no problem (stage_delivered)." },
    onWay: { type: "field", supportedRoles: ["numeric"], required: true, intent: "value", aiDescription: "Orders still on the way (stage_on_way)." },
    cases: { type: "field", supportedRoles: ["numeric"], required: true, intent: "value", aiDescription: "Orders with a reported problem (orders_with_case)." },
    refunded: { type: "field", supportedRoles: ["numeric"], required: true, intent: "value", aiDescription: "Cases the buyer was refunded for (stage_refunded)." },
    recovered: { type: "field", supportedRoles: ["numeric"], required: true, intent: "value", aiDescription: "Cases a carrier paid back (stage_recovered)." },
  },
  ai: {
    label: "Parcel journey",
    description: "A flow of every order from payment to who paid for a problem: paid, shipped, then delivered, on the way or problem reported, then refunded and paid back by the carrier. Orders with a problem are drawn again, zoomed in.",
    usage: "Use once per page, full width, to show where orders end up and how many had a problem. Not for comparing carriers or months: use a column or bar chart for that.",
    configuration: "Map all seven slots: orders order_count, shipped stage_shipped, delivered stage_delivered, onWay stage_on_way, cases orders_with_case, refunded stage_refunded, recovered stage_recovered. Best 24 tracks wide and 16 to 20 high.",
  },
  defaultState: DEFAULTS.journey,
  form: (p: AgWidgetFormParams<JourneyDef>) => p.createDefaults({
    dataMappingItems: [
      { key: "orders", label: "Orders" }, { key: "shipped", label: "Shipped" }, { key: "delivered", label: "Delivered, no problem" },
      { key: "onWay", label: "On the way" }, { key: "cases", label: "Problem reported" }, { key: "refunded", label: "Refunded" },
      { key: "recovered", label: "Carrier paid back" },
    ],
  }),
  comp: captioned(Journey, "Parcel journey"),
  defaultSize: { width: 900, height: 300 },
  minSize: { width: 480, height: 200 },
};

// ---------------------------------------------------------------- liability waterfall

/** Refunded, minus recovered, equals the net loss; the part of it still claimable is hatched. */
export function MoneyFlow(params: AgWidgetParams<FlowDef>) {
  const { widgetApi, dataMapping } = params;
  const out = dataMapping.refunded?.[0];
  const back = dataMapping.recovered?.[0];
  const pend = dataMapping.pending?.[0];
  const got = useWidgetRows(params, [out, back], undefined);
  // Claims live on another table: their own query, so no join repeats a refund.
  const open = useWidgetRows(params, [pend], undefined, { min: 1, quiet: true });
  if (!got || !out || !back) return null;
  const refunded = num(got.rows[0]?.[out.key]);
  const recovered = num(got.rows[0]?.[back.key]);
  const net = refunded - recovered;
  const pending = pend && open ? Math.min(Math.max(0, num(open.rows[0]?.[pend.key])), Math.max(0, net)) : 0;
  const share = refunded > 0 ? Math.round((recovered / refunded) * 100) : 0;
  const top = Math.max(refunded, 1);
  const pc = (v: number) => `${Math.max(0, Math.min(100, (v / top) * 100))}%`;
  const fmt = (v: number) => widgetApi.formatFieldValue(out, v);
  return (
    <div className="ds-flow ds-wf">
      <div className="ds-wf-plot" aria-hidden>
        <i className="ds-wf-link" style={{ bottom: pc(refunded), left: "16.7%", right: "50%" }} />
        <i className="ds-wf-link" style={{ bottom: pc(net), left: "50%", right: "16.7%" }} />
        <div className="ds-wf-col"><b data-kind="out" style={{ bottom: 0, height: pc(refunded) }} /></div>
        <div className="ds-wf-col"><b data-kind="back" style={{ bottom: pc(net), height: pc(recovered) }} /></div>
        <div className="ds-wf-col">
          <b data-kind="net" style={{ bottom: 0, height: pc(net) }} />
          {pending > 0 && <b data-kind="pending" style={{ bottom: pc(net - pending), height: pc(pending) }} />}
        </div>
      </div>
      <div className="ds-wf-legend">
        <div><strong>{fmt(refunded)}</strong><span>Refunded to buyers</span></div>
        <div><strong className="ds-flow-good">− {widgetApi.formatFieldValue(back, recovered)}</strong><span>Recovered from carriers</span></div>
        <div><strong>{fmt(net)}</strong><span>Net delivery loss</span></div>
      </div>
      <p className="ds-flow-note">
        Carriers repaid <strong>{share}%</strong>.
        {pending > 0 && <> <span className="ds-wf-hatch" aria-hidden /> <strong>{fmt(pending)}</strong> still claimable.</>}
      </p>
    </div>
  );
}

const FLOW_ICON = `<svg width="32" height="32" viewBox="0 0 32 32" xmlns="http://www.w3.org/2000/svg"><rect x="3" y="6" width="7" height="22" rx="1.5" fill="#c98a97"/><rect x="12.5" y="6" width="7" height="7" rx="1.5" fill="#3f6f73"/><rect x="22" y="13" width="7" height="15" rx="1.5" fill="#7b2d3d"/><path d="M10 6.5H12.5M19.5 13.5H22" stroke="#8b827d" stroke-dasharray="1.5 1.5"/></svg>`;

export const moneyFlowWidget: AgBaseWidgetDefinition<"doorstep-money-flow", FlowDef> = {
  id: "doorstep-money-flow",
  label: "Liability waterfall",
  icon: FLOW_ICON,
  dataMapping: {
    refunded: { type: "field", supportedRoles: ["numeric"], required: true, intent: "value", aiDescription: "Money refunded to buyers (paid_out)." },
    recovered: { type: "field", supportedRoles: ["numeric"], required: true, intent: "value", aiDescription: "Money carriers paid back (recovered)." },
    pending: { type: "field", supportedRoles: ["numeric"], intent: "value", aiDescription: "Money still claimable from carriers (claims_pending). Optional." },
  },
  ai: {
    label: "Liability waterfall",
    description: "Three columns: money refunded to buyers, minus what carriers paid back, equals the seller's net loss. The part still claimable from carriers is hatched inside the loss.",
    usage: "Use to show what delivery problems cost and how much came back. One per page. Not for trends over time.",
    configuration: "refunded paid_out, recovered recovered, pending claims_pending. About 9 tracks wide and 16 to 20 high.",
  },
  defaultState: DEFAULTS.moneyFlow,
  form: (p: AgWidgetFormParams<FlowDef>) => p.createDefaults({
    dataMappingItems: [{ key: "refunded", label: "Refunded" }, { key: "recovered", label: "Recovered" }, { key: "pending", label: "Still claimable" }],
  }),
  comp: captioned(MoneyFlow, "Liability waterfall"),
  defaultSize: { width: 380, height: 300 },
  minSize: { width: 280, height: 220 },
};

// ---------------------------------------------------------------- claim clock

const DIAL_DAYS = 60;
const DAY = 864e5;

/** Open carrier claims as dials: the ring empties as the deadline nears (a full ring is 60 days). */
export function ClaimClock(params: AgWidgetParams<ClockDef>) {
  const { widgetApi, dataMapping: m } = params;
  const f = [m.carrier?.[0], m.deadline?.[0], m.amount?.[0], m.step?.[0]];
  const got = useWidgetRows(params, f, undefined, { min: 4 });
  const [now] = useState(() => Date.now());
  if (!got || f.some((x) => !x)) return null;
  const [carrier, deadline, amount, step] = f as AgWidgetField[];
  const open = got.rows
    .map((r) => ({ r, at: r[deadline.key] instanceof Date ? (r[deadline.key] as Date).getTime() : Date.parse(String(r[deadline.key])) }))
    .filter(({ r, at }) => String(r[step.key]) !== "Done" && Number.isFinite(at) && at >= now - DAY)
    .sort((a, b) => a.at - b.at)
    .slice(0, 8);
  if (!open.length) return <p className="ds-clock-empty">No open claims.</p>;
  return (
    <ul className="ds-clock">
      {open.map(({ r, at }) => {
        const days = Math.max(0, Math.ceil((at - now) / DAY));
        const tone = days <= 7 ? "bad" : days <= 21 ? "soon" : "calm";
        const frac = Math.min(1, days / DIAL_DAYS);
        const C = 2 * Math.PI * 21;
        return (
          <li key={`${String(r[carrier.key])}-${at}-${keyOf(r[amount.key])}`} data-tone={tone}>
            <svg viewBox="0 0 52 52" className="ds-clock-dial" aria-hidden>
              <circle cx="26" cy="26" r="21" className="ds-clock-track" />
              <circle cx="26" cy="26" r="21" className="ds-clock-arc" strokeDasharray={`${C * frac} ${C}`} transform="rotate(-90 26 26)" />
              <text x="26" y="25" textAnchor="middle" className="ds-clock-days">{days}</text>
              <text x="26" y="35" textAnchor="middle" className="ds-clock-unit">{days === 1 ? "day" : "days"}</text>
            </svg>
            <div className="ds-clock-text">
              <strong>{widgetApi.formatFieldValue(carrier, r[carrier.key])}</strong>
              <span className="ds-clock-amt">{widgetApi.formatFieldValue(amount, r[amount.key])}</span>
              <span className="ds-clock-step">{String(r[step.key])} · by {new Date(at).toLocaleDateString("en-GB", { day: "numeric", month: "short" })}</span>
            </div>
          </li>
        );
      })}
    </ul>
  );
}

const CLOCK_ICON = `<svg width="32" height="32" viewBox="0 0 32 32" xmlns="http://www.w3.org/2000/svg"><circle cx="16" cy="16" r="11" fill="none" stroke="#e8e2dc" stroke-width="4"/><path d="M16 5A11 11 0 1 1 6.5 21.5" fill="none" stroke="#7b2d3d" stroke-width="4" stroke-linecap="round"/><path d="M16 10V16L20 18" stroke="#5a524e" stroke-width="2" fill="none" stroke-linecap="round"/></svg>`;

export const claimClockWidget: AgBaseWidgetDefinition<"doorstep-claim-clock", ClockDef> = {
  id: "doorstep-claim-clock",
  label: "Claim clock",
  icon: CLOCK_ICON,
  dataMapping: {
    carrier: { type: "field", supportedRoles: ["category"], required: true, intent: "category", aiDescription: "The carrier the claim is against (recoveries.carrier)." },
    deadline: { type: "field", supportedRoles: ["temporal"], required: true, aiDescription: "The last day to file the claim (recoveries.deadline_at)." },
    amount: { type: "field", supportedRoles: ["numeric"], required: true, intent: "value", aiDescription: "The amount claimed (recoveries.claimed)." },
    step: { type: "field", supportedRoles: ["category"], required: true, intent: "category", aiDescription: "What happens next on the claim (recoveries.next_step)." },
  },
  ai: {
    label: "Claim clock",
    description: "Open carrier claims as dials counting down the days to each claim deadline, soonest first, with the carrier, the amount claimed and the next step.",
    usage: "Use whenever the question is about carrier claims, claim deadlines or money to recover from a carrier. Filter the page to one carrier to show only its claims.",
    configuration: "carrier recoveries.carrier, deadline recoveries.deadline_at, amount recoveries.claimed, step recoveries.next_step. Best wide and short: 12 to 24 tracks wide, 12 to 15 high.",
  },
  defaultState: DEFAULTS.claimClock,
  form: (p: AgWidgetFormParams<ClockDef>) => p.createDefaults({
    dataMappingItems: [{ key: "carrier", label: "Carrier" }, { key: "deadline", label: "Deadline" }, { key: "amount", label: "Claimed" }, { key: "step", label: "Next step" }],
  }),
  comp: captioned(ClaimClock, "Claim clock"),
  defaultSize: { width: 520, height: 240 },
  minSize: { width: 260, height: 140 },
};

// ---------------------------------------------------------------- evidence meter

const SEGMENTS = 5;

export function EvidenceMeter(params: AgWidgetParams<MeterDef>) {
  const { widgetApi, dataMapping, format } = params;
  const cat = dataMapping.category?.[0];
  const val = dataMapping.value?.[0];
  // Highlight mode keeps every row when this meter or another widget filters the page: the
  // filtered average shows where there is one, and rows outside the selection fade.
  const got = useWidgetRows(params, [cat, val], val, { highlight: true });
  const { picked, toggle } = useSelection(widgetApi, cat);
  if (!got || !cat || !val) return null;
  const good = format?.style?.goodFrom ?? 0.8;
  const ok = format?.style?.okFrom ?? 0.5;
  const hiBy = got.hi ? new Map(got.hi.map((r) => [keyOf(r[cat.key]), num(r[val.key])])) : null;
  return (
    <div className="ds-meter" data-picking={picked.size > 0 || undefined}>
      {got.rows.map((r, i) => {
        const inHi = !hiBy || hiBy.has(keyOf(r[cat.key]));
        const v = hiBy?.get(keyOf(r[cat.key])) ?? num(r[val.key]);
        const lit = Math.round(Math.max(0, Math.min(1, v)) * SEGMENTS);
        const tone = v >= good ? "good" : v >= ok ? "ok" : "low";
        const name = widgetApi.formatFieldValue(cat, r[cat.key]);
        return (
          <button type="button" className="ds-meter-row" key={`${i}:${String(r[cat.key])}`} data-tone={tone}
            data-picked={picked.has(r[cat.key]) || undefined} data-out={!inHi || undefined}
            onClick={() => toggle(r[cat.key])} title={`Filter the page to ${name}`}>
            <span className="ds-meter-label">{name}</span>
            <span className="ds-meter-track" aria-hidden>
              {Array.from({ length: SEGMENTS }, (_, n) => <i key={n} data-on={n < lit || undefined} />)}
            </span>
            <span className="ds-meter-value">{widgetApi.formatFieldValue(val, v)}</span>
          </button>
        );
      })}
      <div className="ds-meter-key">
        <span data-tone="good">{Math.round(good * 100)}%+ strong proof</span>
        <span data-tone="ok">{Math.round(ok * 100)}%+ partial</span>
        <span data-tone="low">thin</span>
      </div>
    </div>
  );
}

const METER_ICON = `<svg width="32" height="32" viewBox="0 0 32 32" xmlns="http://www.w3.org/2000/svg"><g fill="var(--ag-chart-palette-fills-1-color,#7b2d3d)"><rect x="2" y="6" width="5" height="5" rx="1"/><rect x="8" y="6" width="5" height="5" rx="1"/><rect x="14" y="6" width="5" height="5" rx="1"/><rect x="20" y="6" width="5" height="5" rx="1"/><rect x="2" y="14" width="5" height="5" rx="1"/><rect x="8" y="14" width="5" height="5" rx="1"/><rect x="2" y="22" width="5" height="5" rx="1"/></g><g fill="var(--ag-border-color,#d8d0c8)"><rect x="26" y="6" width="5" height="5" rx="1"/><rect x="14" y="14" width="5" height="5" rx="1"/><rect x="20" y="14" width="5" height="5" rx="1"/><rect x="26" y="14" width="5" height="5" rx="1"/><rect x="8" y="22" width="5" height="5" rx="1"/><rect x="14" y="22" width="5" height="5" rx="1"/><rect x="20" y="22" width="5" height="5" rx="1"/><rect x="26" y="22" width="5" height="5" rx="1"/></g></svg>`;

export const evidenceMeterWidget: AgBaseWidgetDefinition<"doorstep-evidence-meter", MeterDef> = {
  id: "doorstep-evidence-meter",
  label: "Evidence meter",
  icon: METER_ICON,
  dataMapping: {
    category: { type: "field", supportedRoles: ["category"], required: true, intent: "category", aiDescription: "What to group by: orders.method (delivery method) or orders.carrier." },
    value: { type: "field", supportedRoles: ["numeric"], required: true, intent: "value", aiDescription: "Share of orders with proof of handover, 0 to 1 (evidence_avg)." },
  },
  ai: {
    label: "Evidence meter",
    description: "One row per delivery method or carrier, each a five-segment meter of how much proof of handover is on file (seal, signature or locker code). Clicking a row filters the page.",
    usage: "Use for questions about proof, evidence, or how well a carrier or delivery method is covered. Not for counts or money.",
    configuration: "category orders.method (or orders.carrier), value evidence_avg. About 8 to 12 tracks wide and 14 to 18 high.",
  },
  defaultState: DEFAULTS.evidence,
  form: (p: AgWidgetFormParams<MeterDef>) => p.createDefaults({
    dataMappingItems: [{ key: "category", label: "Group by" }, { key: "value", label: "Share of proof (0 to 1)" }],
  }),
  comp: captioned(EvidenceMeter, "Evidence meter"),
  defaultSize: { width: 420, height: 260 },
  minSize: { width: 260, height: 160 },
};

// ---------------------------------------------------------------- failure points

export function FailurePoints(params: AgWidgetParams<FailureDef>) {
  const { widgetApi, dataMapping } = params;
  const cat = dataMapping.category?.[0];
  const val = dataMapping.value?.[0];
  const cost = dataMapping.money?.[0];
  const got = useWidgetRows(params, [cat, val, cost], val, { highlight: true });
  const { picked, toggle } = useSelection(widgetApi, cat);
  if (!got || !cat || !val) return null;
  const rows = got.rows;
  // Another widget's selection: the part of each cause it covers, drawn solid over the total.
  const hiBy = got.hi ? new Map(got.hi.map((r) => [keyOf(r[cat.key]), num(r[val.key])])) : null;
  const total = rows.reduce((a, r) => a + num(r[val.key]), 0) || 1;
  const top = Math.max(...rows.map((r) => num(r[val.key])), 1);
  return (
    <ol className="ds-fp" data-picking={picked.size > 0 || undefined} data-highlight={hiBy ? true : undefined}>
      {rows.map((r, i) => {
        const n = num(r[val.key]);
        const part = hiBy ? hiBy.get(keyOf(r[cat.key])) ?? 0 : n;
        const name = widgetApi.formatFieldValue(cat, r[cat.key]);
        return (
          <li key={`${i}:${String(r[cat.key])}`} data-rank={i} data-picked={picked.has(r[cat.key]) || undefined}>
            <button type="button" onClick={() => toggle(r[cat.key])} title={`Filter the page to ${name}`}>
              <div className="ds-fp-line">
                <span className="ds-fp-name">{name}</span>
                <span className="ds-fp-nums">
                  <strong>{hiBy ? `${widgetApi.formatFieldValue(val, part)} of ${widgetApi.formatFieldValue(val, n)}` : widgetApi.formatFieldValue(val, n)}</strong>
                  <span>{Math.round((n / total) * 100)}%</span>
                  {cost && <span className="ds-fp-cost">{r[cost.key] != null ? widgetApi.formatFieldValue(cost, r[cost.key]) : "no refund"}</span>}
                </span>
              </div>
              <span className="ds-fp-bar" aria-hidden>
                {hiBy && <i className="ds-fp-ghost" style={{ width: `${(n / top) * 100}%` }} />}
                <i style={{ width: `${(part / top) * 100}%` }} />
              </span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}

const FP_ICON = `<svg width="32" height="32" viewBox="0 0 32 32" xmlns="http://www.w3.org/2000/svg"><g fill="var(--ag-chart-palette-fills-1-color,#7b2d3d)"><rect x="3" y="5" width="26" height="4" rx="2"/><rect x="3" y="12" width="18" height="4" rx="2" fill-opacity=".75"/><rect x="3" y="19" width="12" height="4" rx="2" fill-opacity=".55"/><rect x="3" y="26" width="7" height="3" rx="1.5" fill-opacity=".35"/></g></svg>`;

export const failurePointsWidget: AgBaseWidgetDefinition<"doorstep-failure-points", FailureDef> = {
  id: "doorstep-failure-points",
  label: "Failure points",
  icon: FP_ICON,
  dataMapping: {
    category: { type: "field", supportedRoles: ["category"], required: true, intent: "category", aiDescription: "Why the delivery failed (cases.failure_point)." },
    value: { type: "field", supportedRoles: ["numeric"], required: true, intent: "value", aiDescription: "Number of cases (case_count)." },
    money: { type: "field", supportedRoles: ["numeric"], intent: "value", aiDescription: "Money refunded for those cases (paid_out). Optional." },
  },
  ai: {
    label: "Failure points",
    description: "A ranked list of why deliveries failed, each cause with its share of cases and the money refunded. Clicking a row filters the page; another widget's selection shows as a solid bar over a faint total.",
    usage: "Use for questions about causes of failed deliveries, what went wrong, or where cases came from. Prefer it to a bar chart of failure points.",
    configuration: "category cases.failure_point, value case_count, money paid_out. About 8 to 12 tracks wide and 16 to 20 high.",
  },
  defaultState: DEFAULTS.failurePoints,
  form: (p: AgWidgetFormParams<FailureDef>) => p.createDefaults({
    dataMappingItems: [{ key: "category", label: "Failure point" }, { key: "value", label: "Cases" }, { key: "money", label: "Money" }],
  }),
  comp: captioned(FailurePoints, "Failure points"),
  defaultSize: { width: 460, height: 300 },
  minSize: { width: 280, height: 180 },
};

// ---------------------------------------------------------------- the brief (S1)

/**
 * The Overview's first words: how many orders arrived with no problem (the brief's own query, so it
 * follows the Quarter filter and cross filters like the KPIs), what moved in the last four weeks,
 * the next claim deadline, and up to three things to do. Sits on the paper, with no card.
 */
export function Brief(params: AgWidgetParams<BriefDef, unknown, BriefContext>) {
  const { widgetApi, dataMapping: m } = params;
  const ctx = (params.context as BriefContext | undefined)?.brief;
  const fOrders = m.orders?.[0], fDelivered = m.delivered?.[0], fMethod = m.method?.[0];
  const got = useWidgetRows(params, [fOrders, fDelivered], undefined, { min: 2 });
  const { picked, toggle } = useSelection(widgetApi, fMethod);
  // Studio hands over new params when the data moves (live updates), so the brief reads again.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const story = useMemo(() => (ctx ? brief(ctx.latest()) : null), [ctx, params]);
  // A high finding that was not there when the page opened is marked New, and said once.
  const firstHigh = useRef<Set<string> | null>(null);
  const said = useRef<Set<string>>(new Set());
  const high = (story?.findings ?? []).filter((f) => f.severity === "high").map((f) => f.title);
  if (story && !firstHigh.current) firstHigh.current = new Set(high);
  const fresh = high.filter((t) => !firstHigh.current?.has(t));
  const [notice, setNotice] = useState("");
  useEffect(() => {
    const t = fresh.find((x) => !said.current.has(x));
    if (!t) return;
    said.current.add(t);
    setNotice(`Doorstep noticed: ${t}`);
  }, [fresh.join("|")]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!got || !fOrders || !fDelivered) return null;
  const row = got.rows[0];
  const orders = num(row?.[fOrders.key]), delivered = num(row?.[fDelivered.key]);
  // Scan titles start with the carrier or method ("Evri: case rate up to 7.9%").
  const isNew = (n: DoNext) => {
    const k = n.key.replace(/^(carrier|method)-/, "");
    return k !== n.key && fresh.some((t) => t.startsWith(`${k}:`));
  };
  const run = (n: DoNext) => {
    if (n.action.kind === "case") ctx?.open(n.action.orderId);
    else if (n.action.kind === "carrier") ctx?.drill(n.action.carrier);
    else if (n.action.kind === "chase") ctx?.chase?.(n.action.carrier);
    else toggle(n.action.method);
  };
  return (
    <section className="ds-brief" aria-label="Doorstep's brief">
      <div className="ds-brief-top">
        <div className="ds-brief-words">
          <h2 className="ds-brief-head"><Says parts={headline(orders, delivered)} /></h2>
          {story && <p className="ds-brief-line"><Says parts={story.line} /></p>}
        </div>
        <Doorstep3D className="ds-brief-3d" />
      </div>
      {story && story.next.length > 0 && (
        <div className="ds-brief-next">
          <div className="ds-brief-label">Do next</div>
          <ol>
            {story.next.map((n) => {
              const on = n.action.kind === "method" && picked.has(n.action.method);
              return (
                <li key={n.key}>
                  <span className="ds-brief-what">{n.what}{isNew(n) && <span className="ds-new">New</span>}</span>
                  <span className="ds-brief-meta num">{[n.money, n.when].filter(Boolean).join(" · ")}</span>
                  <button type="button" className="ds-brief-btn" aria-pressed={n.action.kind === "method" ? on : undefined} onClick={() => run(n)}>
                    {n.action.kind === "method" ? (on ? "Clear filter" : "Filter the page") : n.action.label}
                  </button>
                </li>
              );
            })}
          </ol>
        </div>
      )}
      <span className="sr-only" aria-live="polite">{notice}</span>
    </section>
  );
}

const BRIEF_ICON = `<svg width="32" height="32" viewBox="0 0 32 32" xmlns="http://www.w3.org/2000/svg"><rect x="3" y="6" width="22" height="4" rx="2" fill="#7b2d3d"/><rect x="3" y="13" width="16" height="2.5" rx="1.25" fill="#8b827d"/><rect x="3" y="20" width="26" height="1" fill="#d8d0c8"/><rect x="3" y="23" width="12" height="2.5" rx="1.25" fill="#5a524e"/><rect x="23" y="22" width="6" height="4" rx="2" fill="#f3e6e9"/></svg>`;

export const briefWidget: AgBaseWidgetDefinition<"doorstep-brief", BriefDef> = {
  id: "doorstep-brief",
  label: "Doorstep brief",
  icon: BRIEF_ICON,
  dataMapping: {
    orders: { type: "field", supportedRoles: ["numeric"], required: true },
    delivered: { type: "field", supportedRoles: ["numeric"], required: true },
    method: { type: "field", supportedRoles: ["category"] },
  },
  form: (p: AgWidgetFormParams<BriefDef>) => p.createDefaults({
    dataMappingItems: [{ key: "orders", label: "Orders" }, { key: "delivered", label: "Delivered, no problem" }, { key: "method", label: "Delivery method (to filter by)" }],
  }),
  comp: Brief,
  defaultSize: { width: 900, height: 220 },
  minSize: { width: 320, height: 140 },
};

export const doorstepWidgetTypes = [briefWidget, journeyWidget, moneyFlowWidget, claimClockWidget, evidenceMeterWidget, failurePointsWidget];
