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
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type {
  AgBaseRegistry, AgBaseWidgetDefinition, AgDefaultWidgetDefinition, AgWidgetApi, AgWidgetDataFormat, AgWidgetField,
  AgWidgetFieldReference, AgWidgetFormParams, AgWidgetParams,
} from "ag-studio";

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

export interface DoorstepRegistry extends AgBaseRegistry {
  widgets: readonly (
    | AgDefaultWidgetDefinition
    | AgBaseWidgetDefinition<"doorstep-evidence-meter", MeterDef>
    | AgBaseWidgetDefinition<"doorstep-failure-points", FailureDef>
    | AgBaseWidgetDefinition<"doorstep-money-flow", FlowDef>
    | AgBaseWidgetDefinition<"doorstep-journey", JourneyDef>
    | AgBaseWidgetDefinition<"doorstep-claim-clock", ClockDef>
  )[];
}

type Row = Record<string, unknown>;
type AnyDef = MeterDef | FailureDef | FlowDef | JourneyDef | ClockDef;
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
    { id: "unshipped", label: "Not shipped yet", v: Math.max(0, N - S), col: 1, tone: "muted", of: N },
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
            return (
              <g key={n.id} className="ds-j-node" data-tone={n.tone}>
                <rect x={x} y={y} width={NW} height={hh} rx={2}><title>{`${n.label}: ${count(n.v)}`}</title></rect>
                <text x={x + NW + 7} y={cy - 7} dominantBaseline="middle" className="ds-j-label">{n.label}</text>
                <text x={x + NW + 7} y={cy + 9} dominantBaseline="middle" className="ds-j-value">
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

const JOURNEY_ICON = `<svg width="32" height="32" viewBox="0 0 32 32" xmlns="http://www.w3.org/2000/svg"><rect x="2" y="5" width="3" height="22" rx="1" fill="#5a524e"/><path d="M5 5C11 5 11 4 17 4V21C11 21 11 23 5 23Z" fill="#3f6f73" fill-opacity=".45"/><path d="M5 23C11 23 11 26 17 26V28C11 28 11 27 5 27Z" fill="#7b2d3d" fill-opacity=".6"/><rect x="17" y="4" width="3" height="17" rx="1" fill="#3f6f73"/><rect x="17" y="25" width="3" height="3" rx="1" fill="#7b2d3d"/><path d="M20 25L27 6V28L20 28Z" fill="#7b2d3d" fill-opacity=".18"/><rect x="27" y="6" width="3" height="22" rx="1" fill="#7b2d3d"/></svg>`;

export const journeyWidget: AgBaseWidgetDefinition<"doorstep-journey", JourneyDef> = {
  id: "doorstep-journey",
  label: "Parcel journey",
  icon: JOURNEY_ICON,
  dataMapping: {
    orders: { type: "field", supportedRoles: ["numeric"], required: true },
    shipped: { type: "field", supportedRoles: ["numeric"], required: true },
    delivered: { type: "field", supportedRoles: ["numeric"], required: true },
    onWay: { type: "field", supportedRoles: ["numeric"], required: true },
    cases: { type: "field", supportedRoles: ["numeric"], required: true },
    refunded: { type: "field", supportedRoles: ["numeric"], required: true },
    recovered: { type: "field", supportedRoles: ["numeric"], required: true },
  },
  form: (p: AgWidgetFormParams<JourneyDef>) => p.createDefaults({
    dataMappingItems: [
      { key: "orders", label: "Orders" }, { key: "shipped", label: "Shipped" }, { key: "delivered", label: "Delivered, no problem" },
      { key: "onWay", label: "On the way" }, { key: "cases", label: "Problem reported" }, { key: "refunded", label: "Refunded" },
      { key: "recovered", label: "Carrier paid back" },
    ],
  }),
  comp: Journey,
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
        Carriers paid back <strong>{share}%</strong> of what you refunded.
        {pending > 0 && <> <span className="ds-wf-hatch" aria-hidden /> <strong>{fmt(pending)}</strong> is still claimable.</>}
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
    refunded: { type: "field", supportedRoles: ["numeric"], required: true },
    recovered: { type: "field", supportedRoles: ["numeric"], required: true },
    pending: { type: "field", supportedRoles: ["numeric"] },
  },
  form: (p: AgWidgetFormParams<FlowDef>) => p.createDefaults({
    dataMappingItems: [{ key: "refunded", label: "Refunded" }, { key: "recovered", label: "Recovered" }, { key: "pending", label: "Still claimable" }],
  }),
  comp: MoneyFlow,
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
  if (!open.length) return <p className="ds-clock-empty">No open claims. Doorstep prepares one when a carrier loses or damages a parcel.</p>;
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
    carrier: { type: "field", supportedRoles: ["category"], required: true },
    deadline: { type: "field", supportedRoles: ["temporal"], required: true },
    amount: { type: "field", supportedRoles: ["numeric"], required: true },
    step: { type: "field", supportedRoles: ["category"], required: true },
  },
  form: (p: AgWidgetFormParams<ClockDef>) => p.createDefaults({
    dataMappingItems: [{ key: "carrier", label: "Carrier" }, { key: "deadline", label: "Deadline" }, { key: "amount", label: "Claimed" }, { key: "step", label: "Next step" }],
  }),
  comp: ClaimClock,
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
    category: { type: "field", supportedRoles: ["category"], required: true },
    value: { type: "field", supportedRoles: ["numeric"], required: true },
  },
  form: (p: AgWidgetFormParams<MeterDef>) => p.createDefaults({
    dataMappingItems: [{ key: "category", label: "Group by" }, { key: "value", label: "Share of proof (0 to 1)" }],
  }),
  comp: EvidenceMeter,
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
    category: { type: "field", supportedRoles: ["category"], required: true },
    value: { type: "field", supportedRoles: ["numeric"], required: true },
    money: { type: "field", supportedRoles: ["numeric"] },
  },
  form: (p: AgWidgetFormParams<FailureDef>) => p.createDefaults({
    dataMappingItems: [{ key: "category", label: "Failure point" }, { key: "value", label: "Cases" }, { key: "money", label: "Money" }],
  }),
  comp: FailurePoints,
  defaultSize: { width: 460, height: 300 },
  minSize: { width: 280, height: 180 },
};

export const doorstepWidgetTypes = [journeyWidget, moneyFlowWidget, claimClockWidget, evidenceMeterWidget, failurePointsWidget];
