import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AgGridReact, type CustomCellRendererProps,
  type CustomStatusPanelProps, type CustomToolPanelProps, type CustomTooltipProps,
} from "ag-grid-react";
import {
  AllCommunityModule, ModuleRegistry,
  type CellKeyDownEvent, type ColDef, type DefaultMenuItem, type ExcelStyle, type FullWidthCellKeyDownEvent, type GetContextMenuItemsParams,
  type GetMainMenuItemsParams, type GridApi, type IAggFuncParams, type IRowNode, type IStatusPanelValueFormatterParams, type MenuItemDef,
  type ProcessCellForExportParams, type RowClickedEvent, type ShowValuesAsDef, type ShowValuesAsTransformParams, type SideBarDef,
  type StatusPanelDef, type Toolbar,
} from "ag-grid-community";
import {
  CellSelectionModule, ClipboardModule, ColumnMenuModule, ColumnsToolPanelModule, ContextMenuModule, ExcelExportModule, NewFiltersToolPanelModule, LicenseManager,
  MasterDetailModule, RowGroupingModule, SetFilterModule, SideBarModule, StatusBarModule,
  AiToolkitModule, NotesModule, PdfExportModule, ToolbarModule, AggregationModule, RowGroupingPanelModule, ShowValuesAsModule, IntegratedChartsModule, FindModule,
} from "ag-grid-enterprise";
import { AgChartsEnterpriseModule } from "ag-charts-enterprise";
import { ArrowRight, ArrowUpRight, Check, ChevronDown, ChevronRight, Circle, SearchX } from "lucide-react";
import type { OrderRow } from "../api";
import { DeliveryCell, JourneyCell, RiskCell, itemKind, journeyLabel, journeyTone } from "../components/gridCells";
import { METHOD, ROUTE, ago, money, pct, stateTone, type Tone } from "../format";
import { gridTheme } from "../grid/theme";
import { chartSelection, chartStakeByMethod, chartThemeOverrides, chartThemes, currencyColumns, customChartThemes, methodNameColumn } from "../grid/charts";
import { afterGlide, useMedia, useReducedMotion } from "../motion";
import { Says, type Part } from "../components/ai";
import DetailPanel from "../grid/DetailPanel";
import { guideParts } from "../grid/guide";
import Doorstep3D from "../components/Doorstep3D";
import type { DoorstepNote } from "../grid/notes";
import { doorstepOwns, hasNote, notesFor, notesSource } from "../grid/notes";

/**
 * The Control Room order grid, on AG Grid Enterprise. Patterns from AG Grid's own demos
 * (github.com/ag-grid/ag-grid-demos), each used for a seller's job, not for show:
 *  - HR demo: calm uppercase headers, light column lines; rows grouped by who acts next.
 *  - Inventory demo: product cell with a category tag, a verb per row, master/detail.
 *  - Finance demo: tooltips, cell flash on live updates, right-aligned money.
 * Plus: Set filters, Columns/Filters side bar, right-click menu, Excel export, and AG Grid 36's
 * Quick Access Toolbar for the grid's own tools.
 */

if (__AG_GRID_LICENSE_KEY__) LicenseManager.setLicenseKey(__AG_GRID_LICENSE_KEY__);

ModuleRegistry.registerModules([
  AllCommunityModule, MasterDetailModule, RowGroupingModule, SetFilterModule, SideBarModule,
  ColumnsToolPanelModule, NewFiltersToolPanelModule, ColumnMenuModule, ContextMenuModule, ExcelExportModule,
  StatusBarModule, AiToolkitModule, NotesModule, PdfExportModule, ToolbarModule, CellSelectionModule, ClipboardModule,
  AggregationModule, RowGroupingPanelModule, ShowValuesAsModule, FindModule,
  IntegratedChartsModule.with(AgChartsEnterpriseModule),
]);


// ---------- vocabulary ----------

type Lane = "you" | "case" | "way" | "done";
const LANES: Record<Lane, { rank: number; label: string; hint: string }> = {
  you: { rank: 0, label: "Needs you", hint: "approve, review, pack" },
  case: { rank: 1, label: "Cases in progress", hint: "Doorstep is on it" },
  way: { rank: 2, label: "On the way", hint: "not yet received" },
  done: { rank: 3, label: "Done", hint: "received or settled" },
};

export function laneOf(r: OrderRow): Lane {
  if (r.needs_action) return "you";
  if (r.case_id && r.case_state && !["SETTLED", "CLOSED"].includes(r.case_state)) return "case";
  if (["CONFIRMED", "RESOLVED"].includes(r.state)) return "done";
  return "way";
}

export function sumStake(rows: OrderRow[]): string {
  const by: Record<string, number> = {};
  rows.forEach((r) => (by[r.currency] = (by[r.currency] ?? 0) + r.money_at_stake_minor));
  return stakeText(by);
}
function stakeText(by: Record<string, number>): string {
  const parts = Object.entries(by).filter(([, v]) => v > 0).map(([c, v]) => money(v, c));
  return parts.length ? parts.join(" + ") : money(0, Object.keys(by)[0] ?? "GBP");
}

/**
 * Money at stake, aggregated by AG Grid for group rows (and the grand total): kept per currency, so
 * a group never adds a pound to a dollar. `value` is the plain total, for single-currency compares.
 */
export type StakeTotal = { by: Record<string, number>; value: number; toString: () => string };
function stakeAgg(p: IAggFuncParams<OrderRow>): StakeTotal {
  const by: Record<string, number> = {};
  const add = (c: string, v: number) => (by[c] = (by[c] ?? 0) + v);
  for (const child of p.rowNode.childrenAfterFilter ?? []) {
    if (child.group) Object.entries((child.aggData?.money as StakeTotal | undefined)?.by ?? {}).forEach(([c, v]) => add(c, v));
    else if (child.data) add(child.data.currency, child.data.money_at_stake_minor);
  }
  return { by, value: Object.values(by).reduce((a, b) => a + b, 0), toString: () => stakeText(by) };
}
const aggFuncs = { stake: stakeAgg };

/** "% of total" on At stake, within each currency: a pound's share of the pounds, never of dollars. */
function shareOfCurrency(p: ShowValuesAsTransformParams<OrderRow>): number | null {
  const r = p.node.data;
  if (!r || p.node.group) return null;
  let total = 0;
  p.api.forEachNodeAfterFilter((n) => { if (!n.group && n.data?.currency === r.currency) total += n.data.money_at_stake_minor; });
  return total ? r.money_at_stake_minor / total : null;
}
const stakeShare: ShowValuesAsDef<OrderRow> = {
  precision: 1,
  modes: {
    percentOfGrandTotal: { transform: shareOfCurrency, displayName: "% of total", description: "Share of the money at stake in these orders, per currency." },
    percentOfColumnTotal: false, percentOfRowTotal: false, percentOfParentRowTotal: false, percentOfParentColumnTotal: false,
  },
};

/** What a group row is called: the lane's name, or what the grouped column's cells say. */
function groupLabel(node: IRowNode): string {
  if (node.footer) return "Total";
  const col = node.rowGroupColumn?.getColId();
  const key = node.key == null ? "" : String(node.key);
  if (col === "lane") return LANES[key as Lane]?.label ?? key;
  if (col === "method") return key ? METHOD[key] ?? key : "Not chosen";
  return key || "None";
}

/** The orders under a group row that pass the filters, however deep the grouping goes. */
function leavesOf(node: IRowNode): OrderRow[] {
  const out: OrderRow[] = [];
  const walk = (n: IRowNode) => (n.childrenAfterFilter ?? []).forEach((c) => (c.group ? walk(c) : c.data && out.push(c.data as OrderRow)));
  walk(node);
  return out;
}

/**
 * The one thing the seller would do next on this order, as a verb, and how loudly to ask.
 * Solid ("primary") is kept for what costs money if it waits: a PayPal dispute has a deadline and
 * a refund is waiting on the seller. Everything else the seller does is a quiet tint, so the one
 * solid button in a list is the one to press first.
 */
type Level = "primary" | "tint" | "quiet";
function nextAction(r: OrderRow): { label: string; urgent: boolean; level: Level } {
  if (r.case_state === "DISPUTE_EVIDENCE") return { label: "Send evidence", urgent: r.needs_action, level: r.needs_action ? "primary" : "quiet" };
  if (r.case_state === "AWAITING_SELLER") return { label: "Review refund", urgent: true, level: "primary" };
  if (r.case_state === "HOLD_FOR_REVIEW") return { label: "Review case", urgent: true, level: "tint" };
  if (r.state === "PAID") return { label: r.packing_verdict && r.packing_verdict !== "match" ? "Check packing" : "Pack it", urgent: true, level: "tint" };
  if (r.state === "PACKED") return { label: "Ship it", urgent: true, level: "tint" };
  return { label: "Open", urgent: false, level: "quiet" };
}

/**
 * One work order for the seller: what costs money if it waits comes first. A PayPal dispute has a
 * deadline (0), then a refund waiting on the seller (1), a held case (2), a packing check (3),
 * a parcel to pack (4) or ship (5); everything else after (9). Ties: most money at stake first.
 */
export function urgency(r: OrderRow): number {
  if (r.case_state === "DISPUTE_EVIDENCE" && r.needs_action) return 0;
  if (r.case_state === "AWAITING_SELLER") return 1;
  if (r.case_state === "HOLD_FOR_REVIEW") return 2;
  if (r.state === "PAID") return r.packing_verdict && r.packing_verdict !== "match" ? 3 : 4;
  if (r.state === "PACKED") return 5;
  return 9;
}
export const byUrgency = (a: OrderRow, b: OrderRow) => urgency(a) - urgency(b) || b.money_at_stake_minor - a.money_at_stake_minor;

export function routeLabel(r: OrderRow): string {
  const key = r.case_state && ROUTE[r.case_state] ? r.case_state : r.route;
  return key && ROUTE[key] ? ROUTE[key].label : "";
}

function haystack(r: OrderRow): string {
  return `${r.item} ${r.order_ref} ${r.buyer_area} ${r.buyer_country} ${r.status} ${r.method ?? ""} ${routeLabel(r)} ${nextAction(r).label} ${itemKind(r.item).label}`;
}

const PROOFS = ["address checked", "packing photo", "tracking", "delivered scan", "possession proof"];
export function proofList(r: OrderRow): { name: string; ok: boolean }[] {
  const all = [...r.evidence.present.map((n) => ({ name: n, ok: true })), ...r.evidence.missing.map((n) => ({ name: n, ok: false }))];
  const rank = (n: string) => { const i = PROOFS.indexOf(n); return i < 0 ? 99 : i; };
  return all.sort((a, b) => rank(a.name) - rank(b.name));
}
export const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

export function rowTone(r: OrderRow): Tone {
  if (r.case_state) return ROUTE[r.case_state]?.tone ?? "accent";
  return stateTone(r.state);
}

export const STEPS = ["Paid", "Packed", "Shipped", "Delivered", "Received"];
export const STEP_OF: Record<string, number> = {
  CREATED: 0, PAID: 1, PACKED: 2, SHIPPED: 3, IN_TRANSIT: 3, EXCEPTION: 3,
  DELIVERED: 4, PICKED_UP: 4, CONFIRMED: 5, CASE_OPEN: 4, RESOLVED: 4,
};
const STATE_RANK: Record<string, number> = {
  CREATED: 0, PAID: 1, PACKED: 2, SHIPPED: 3, IN_TRANSIT: 4, EXCEPTION: 5,
  DELIVERED: 6, PICKED_UP: 6, CONFIRMED: 7, CASE_OPEN: 8, RESOLVED: 9,
};
export const COUNTRY: Record<string, string> = { GB: "United Kingdom", US: "United States" };

/** Re-render when the grid opens or closes this row (detail panel or lane). */
function useExpanded(node: IRowNode | undefined): boolean {
  const [open, setOpen] = useState(!!node?.expanded);
  useEffect(() => {
    if (!node) return;
    const f = () => setOpen(!!node.expanded);
    f();
    node.addEventListener("expandedChanged", f);
    return () => node.removeEventListener("expandedChanged", f);
  }, [node]);
  return open;
}

/** Re-render on every model update: a group row's count must follow the rows under it (the grid
 * keeps group rows mounted when their children change). */
function useModelTick(api: CustomCellRendererProps<OrderRow>["api"]) {
  const [, setTick] = useState(0);
  useEffect(() => {
    const bump = () => setTick((t) => t + 1);
    api.addEventListener("modelUpdated", bump);
    return () => { if (!api.isDestroyed()) api.removeEventListener("modelUpdated", bump); };
  }, [api]);
}

// ---------- cells ----------

function OrderCell(p: CustomCellRendererProps<OrderRow>) {
  const open = useExpanded(p.node);
  useModelTick(p.api);
  const r = p.data;
  if (!r) return null;
  const k = itemKind(r.item);
  // In the Needs-you lane, a quiet step number: the order to work through them in. Only while the
  // lane is the grouping (a seller's own grouping splits the work order, so the numbers would restart).
  const under = p.node.parent?.rowGroupColumn?.getColId();
  const step = laneOf(r) === "you" && (!under || under === "lane") ? (p.node.parent?.childrenAfterSort?.indexOf(p.node) ?? -1) + 1 : 0;
  return (
    <div className="order-cell">
      {step > 0 && <span className="step-no num" aria-hidden>{step}</span>}
      <button data-own-click className="expander" aria-expanded={open}
        aria-label={`${open ? "Hide" : "Show"} details for ${r.item}`} onClick={() => p.node.setExpanded(!open)}>
        <ChevronRight aria-hidden />
      </button>
      <span className="item-tile" aria-hidden>{k.icon}</span>
      <span className="cell-two" style={{ gap: 4 }}>
        <span className="item-name">
          {p.api.findGetParts({ node: p.node, column: p.column ?? null, value: r.item }).map((x, i) => (
            x.match ? <mark key={i} className={`find-mark${x.activeMatch ? " active" : ""}`}>{x.value}</mark> : x.value
          ))}
        </span>
        <span className="order-meta">
          <span className="tag">{k.label}</span>
          <span className="sub num">{r.order_ref} · {r.buyer_area || r.buyer_country}</span>
        </span>
        {/* A phone shows two columns, Order and Next: the journey rides here; the rest is in the order. */}
        <span className={`order-journey phone-only tone-${journeyTone(r)}`}><span>{journeyLabel(r)}</span></span>
      </span>
    </div>
  );
}

/** Proof as five short segments, the same language as the journey track. */
function ProofCell({ data: r }: CustomCellRendererProps<OrderRow>) {
  if (!r) return null;
  const list = proofList(r);
  const have = list.filter((x) => x.ok).length;
  return (
    <div className="proof-cell">
      <span className={`proof-track ${have === list.length ? "full" : ""}`} aria-hidden>
        {list.map((x) => <span key={x.name} className={x.ok ? "on" : ""} />)}
      </span>
      <span className="proof-label num">{have} of {list.length}</span>
    </div>
  );
}

/**
 * At stake: one number per row, the cell's own value, so a selection's sum is what the seller sees.
 * The order's amount rides underneath when only part of it (or none) is at stake. In "% of total"
 * the share leads and the money moves underneath.
 */
function MoneyCell(p: CustomCellRendererProps<OrderRow>) {
  const r = p.data;
  if (!r) return null;
  const stake = r.money_at_stake_minor;
  const share = p.column?.getShowValuesAs() ? p.valueFormatted || "—" : null;
  const sub = share != null ? money(stake, r.currency) : stake === r.amount_minor ? null : `of ${money(r.amount_minor, r.currency)}`;
  return (
    <div className={`money-cell ${stake ? "" : "none"}`}>
      <span className="amt num">{share ?? money(stake, r.currency)}</span>
      {sub && <span className="sub num">{sub}</span>}
    </div>
  );
}

/** Money cells in the selected ranges, per currency: a pound is never added to a dollar. */
function selectedStake(api: GridApi): { by: Record<string, number>; n: Record<string, number>; groups: number } {
  const by: Record<string, number> = {}, n: Record<string, number> = {};
  const seen = new Set<string>();
  let groups = 0; // group rows carry an aggregate in this column; they are not orders
  for (const range of api.getCellRanges() ?? []) {
    const { startRow: a, endRow: b } = range;
    if (!a || !b || a.rowPinned || b.rowPinned || !range.columns.some((c) => c.getColId() === "money")) continue;
    for (let i = Math.min(a.rowIndex, b.rowIndex); i <= Math.max(a.rowIndex, b.rowIndex); i++) {
      const node = api.getDisplayedRowAtIndex(i);
      if (node?.group && node.aggData?.money != null) groups++;
      const r = node && !node.group ? (node.data as OrderRow | undefined) : undefined;
      if (!r || seen.has(r.order_id)) continue;
      seen.add(r.order_id);
      by[r.currency] = (by[r.currency] ?? 0) + r.money_at_stake_minor;
      n[r.currency] = (n[r.currency] ?? 0) + 1;
    }
  }
  return { by, n, groups };
}

/** AG Grid's aggregation panel, in money: Sum and Average of the selected At-stake cells per currency. */
export function selectionFormatter(p: IStatusPanelValueFormatterParams): string {
  const { by, n, groups } = selectedStake(p.api);
  if (p.key === "count") return String((p.value ?? 0) - groups);
  const cur = Object.keys(by);
  if (!cur.length) return p.value == null ? "" : Number(p.value).toLocaleString("en-GB", { maximumFractionDigits: 2 });
  return cur.map((c) => money(p.key === "avg" ? Math.round(by[c] / n[c]) : by[c], c)).join(p.key === "avg" ? " · " : " + ");
}

/** Copy (Ctrl/Cmd+C) pastes what the cell says; money as a plain number a spreadsheet can add. */
function clipboardValue(q: ProcessCellForExportParams<OrderRow>): string {
  const r = q.node?.data;
  if (!r || q.node?.group) return q.value == null ? "" : String(q.value);
  const id = q.column.getColId();
  if (id === "money") return (r.money_at_stake_minor / 100).toFixed(2);
  if (id === "action") return nextAction(r).label;
  return exportValue(id, r, q.value);
}

/** A click that ends a drag across cells (or extends a selection) selects; it does not open the order. */
function isSelecting(api: GridApi, e: MouseEvent | KeyboardEvent | null | undefined): boolean {
  if (e && (e.shiftKey || e.metaKey || e.ctrlKey)) return true;
  const ranges = api.getCellRanges() ?? [];
  return ranges.length > 1 || ranges.some((g) => g.columns.length > 1 || g.startRow?.rowIndex !== g.endRow?.rowIndex);
}

function ActionCell(p: CustomCellRendererProps<OrderRow> & { onOpen: (r: OrderRow) => void }) {
  const r = p.data;
  if (!r) return null;
  const a = nextAction(r);
  return (
    <button data-own-click className={`row-action ${a.level}`} onClick={() => p.onOpen(r)}>
      {a.label}{a.level === "quiet" && <ArrowUpRight aria-hidden />}
    </button>
  );
}

// ---------- tooltips and headers ----------

function EvidenceTip({ data: r }: CustomTooltipProps<OrderRow>) {
  if (!r) return null;
  const list = proofList(r);
  return (
    <div className="grid-tip">
      <div className="grid-tip-head">Proof on file <span>{list.filter((x) => x.ok).length} of {list.length}</span></div>
      <ul className="grid-tip-list">
        {list.map((x) => (
          <li key={x.name} className={x.ok ? "ok" : "miss"}>{x.ok ? <Check aria-hidden /> : <Circle aria-hidden />}{cap(x.name)}</li>
        ))}
      </ul>
    </div>
  );
}

function RiskTip({ data: r }: CustomTooltipProps<OrderRow>) {
  if (!r || r.delivery_risk == null) return null;
  return (
    <div className="grid-tip">
      <div className="grid-tip-head">Delivery risk <span>{pct(r.delivery_risk)}</span></div>
      {r.risk_reasons.length > 0
        ? <ul className="grid-tip-list plain">{r.risk_reasons.map((x) => <li key={x}>{x}</li>)}</ul>
        : <p className="grid-tip-note">No strong signals either way.</p>}
    </div>
  );
}

// ---------- lanes, detail, status bar ----------

/**
 * A group row: a lane (who acts next) or any column the seller grouped by. Count, and money at stake
 * from AG Grid's aggregation (per currency, never summed across currencies).
 */
function GroupRow(p: CustomCellRendererProps<OrderRow>) {
  const open = useExpanded(p.node);
  useModelTick(p.api);
  const key = String(p.node.key) as Lane;
  const lane = p.node.rowGroupColumn?.getColId() === "lane" ? LANES[key] : undefined;
  const rows = leavesOf(p.node);
  const label = groupLabel(p.node);
  const stake = (p.node.aggData?.money as StakeTotal | undefined)?.toString() ?? sumStake(rows);
  return (
    <div className={`lane-row ${lane ? `lane-${key}` : "group-by"}`} style={p.node.level ? { paddingLeft: 2 + p.node.level * 24 } : undefined}
      aria-expanded={open} aria-label={`${label}: ${rows.length} order${rows.length === 1 ? "" : "s"}`}>
      <ChevronDown className="chev" aria-hidden />
      <span className="lane-name">{label}</span>
      <span className="count-chip num">{rows.length}</span>
      {lane && <span className="lane-hint">{lane.hint}</span>}
      <span className="lane-meta num">{stake} at stake</span>
    </div>
  );
}

// ---------- the Doorstep row ----------

/** The pinned Doorstep row's stand-in data: it is drawn full width and kept out of everything else. */
const GUIDE_ID = "__doorstep";
const GUIDE = { order_id: GUIDE_ID } as OrderRow;
const isGuide = (n: IRowNode | null | undefined) => n?.rowPinned === "top";

/** Doorstep speaks once, at the top of the grid: what needs the seller, and one Start button. */
function DoorstepRow(p: CustomCellRendererProps<OrderRow> & { parts: Part[] | null; onStart: () => void }) {
  if (p.node.footer) return <TotalRow {...p} />;
  if (!p.parts) return null;
  return (
    <div className="ds-row">
      <Says parts={p.parts} className="ds-row-text" />
      <button data-own-click className="row-action primary ds-row-start" onClick={p.onStart}>
        Start<ArrowRight aria-hidden />
      </button>
    </div>
  );
}

/**
 * AG Grid's grand total row (`grandTotalRow`), drawn full width like the lanes above it: the money at
 * stake in every order the seller can see, from AG Grid's aggregation, following search and filters.
 */
function TotalRow(p: CustomCellRendererProps<OrderRow>) {
  useModelTick(p.api);
  let n = 0;
  p.api.forEachNodeAfterFilter((x) => { if (x.data && !x.group) n++; });
  const total = (p.node.aggData?.money ?? p.node.sibling?.aggData?.money) as StakeTotal | undefined;
  const stake = total?.toString() ?? "";
  return (
    <div className="total-row" aria-label={`Total: ${n} order${n === 1 ? "" : "s"}, ${stake} at stake`}>
      <span className="total-name">Total</span>
      <span className="count-chip num">{n}</span>
      <span className="total-stake num">{stake} <span>at stake</span></span>
    </div>
  );
}

// ---------- status bar ----------

/** Status bar, left: where these orders come from. */
function SourcePanel({ text }: CustomStatusPanelProps<OrderRow> & { text: string }) {
  return <div className="ag-status-name-value source-panel">{text}</div>;
}

/** Both "no rows" and "nothing matches the search" get the same calm empty state. */
const emptySelector = (p: { overlayType: string }) =>
  (p.overlayType === "noRows" || p.overlayType === "noMatchingRows" ? { component: EmptyOverlay } : undefined);

/**
 * Empty list: a calm line under a small 3D doorstep (a sealed parcel on the step) when the list is
 * truly empty; a plain search icon when a search matched nothing.
 */
function EmptyOverlay({ text, searching, overlayType }: { text?: string; searching?: boolean; overlayType?: string }) {
  const calm = !searching && overlayType !== "noMatchingRows";
  return (
    <div className="grid-empty">
      {calm ? <Doorstep3D /> : <span className="grid-empty-icon" aria-hidden><SearchX /></span>}
      <span>{!searching && overlayType === "noMatchingRows" ? "No match. Remove a chip or Undo." : text}</span>
    </div>
  );
}

// ---------- export ----------

const EXPORT_COLUMNS = ["item", "buyer", "status", "next", "risk", "proof", "money", "method", "updated"];

const excelStyles: ExcelStyle[] = [
  { id: "header", font: { bold: true, color: "#1F1A18" }, interior: { color: "#F6ECEE", pattern: "Solid" } },
  { id: "laneRow", font: { bold: true, color: "#7B2D3D" } },
];

function exportValue(id: string, r: OrderRow, v: unknown): string {
  switch (id) {
    case "item": return `${r.item} (${r.order_ref})`;
    case "status": return journeyLabel(r); // what the Journey cell says: the cause, not "Case open"
    case "next": return routeLabel(r) || (nextAction(r).urgent ? nextAction(r).label : "");
    case "method": return r.method ? METHOD[r.method] ?? r.method : "";
    case "risk": return r.delivery_risk == null ? "" : pct(r.delivery_risk);
    case "proof": return `${r.evidence.present.length} of ${r.evidence.present.length + r.evidence.missing.length}`;
    case "money": return money(r.money_at_stake_minor, r.currency) + (r.money_at_stake_minor === r.amount_minor ? "" : ` of ${money(r.amount_minor, r.currency)}`);
    case "buyer": return `${r.buyer_area} ${r.buyer_country}`.trim();
    case "updated": return r.last_update;
    default: return v == null ? "" : String(v);
  }
}

function exportParams() {
  return {
    fileName: `doorstep-orders-${new Date().toISOString().slice(0, 10)}`,
    columnKeys: EXPORT_COLUMNS,
    // The grand total row exports its money at stake (AG Grid's aggregate) under At stake.
    processCellCallback: (q: { column: { getColId(): string }; node?: IRowNode | null; value: unknown }) =>
      (q.node?.data ? exportValue(q.column.getColId(), q.node.data as OrderRow, q.value)
        : q.node?.footer && q.column.getColId() === "money" ? String((q.node.aggData?.money as StakeTotal | undefined) ?? "") : ""),
    processRowGroupCallback: (q: { node: IRowNode }) => groupLabel(q.node),
    skipPinnedTop: true,
  };
}

/** Excel (Enterprise): what the seller sees, same filters and sort, lanes kept as groups. */
const PDF_WIDTHS: Record<string, number> = { item: 174, buyer: 66, status: 122, next: 124, risk: 40, proof: 40, money: 80, method: 58, updated: 78 };

/** PDFs use the built-in fonts (WinAnsi): swap the few symbols we print, and anything else unknown. */
const CP1252 = "€‚ƒ„…†‡ˆ‰Š‹ŒŽ‘’“”•–—˜™š›œžŸ";
export function pdfSafe(s: string): string {
  return s.replace(/≥/g, ">=").replace(/≤/g, "<=").replace(/→/g, "->").replace(/✓/g, "Yes")
    .replace(/[^\x20-\x7e\xa0-\xff]/g, (c) => (CP1252.includes(c) ? c : "?"));
}

/**
 * G7: the hand-off report. What the grid shows (filters, sort, lanes), as a landscape PDF a seller
 * can pass to a helper or keep: titled, paged, marked SANDBOX, and each case row's order linking to
 * its signed receipts (/verify/{case_id}).
 */
export function exportHandoffPdf(api: GridApi, o: { shop: string; region: string; view: string; source: string }) {
  const p = exportParams();
  let n = 0;
  api.forEachNodeAfterFilter((x) => { if (x.data && !x.group && !x.rowPinned) n++; });
  const now = new Date();
  const date = now.toLocaleDateString(o.region === "US" ? "en-US" : "en-GB", { day: "numeric", month: "long", year: "numeric" });
  api.exportDataAsPdf({
    ...p,
    fileName: `doorstep-handoff-${now.toISOString().slice(0, 10)}.pdf`,
    documentTitle: "Delivery hand-off",
    documentSubtitle: pdfSafe(`${o.shop} · ${date} · ${o.view} (${n})`),
    page: { size: o.region === "US" ? "Letter" : "A4", orientation: "landscape" },
    headerFooterConfig: { all: { footer: [
      { value: "Doorstep", position: "Left" },
      { value: "Page &[Page] of &[Pages]", position: "Center" },
      { value: pdfSafe(o.source || "PayPal sandbox"), position: "Right" },
    ] } },
    watermark: { text: "SANDBOX", opacity: 0.06 },
    repeatHeader: true,
    // Widths sized to what each column holds (points, landscape); anything else fits its text.
    columnWidth: ({ column }) => PDF_WIDTHS[column?.getColId() ?? ""] ?? "auto",
    processCellCallback: (q) => {
      // A short local time ("10 Nov, 14:20") instead of the ISO stamp Excel keeps.
      const r = q.node?.data as OrderRow | undefined;
      if (r && q.column.getColId() === "updated") {
        return new Date(r.last_update).toLocaleString(o.region === "US" ? "en-US" : "en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
      }
      return pdfSafe(p.processCellCallback(q));
    },
    processRowGroupCallback: (q) => pdfSafe(p.processRowGroupCallback(q)),
    processCellHyperlinkCallback: ({ node, column }) =>
      column.getColId() === "item" && node.data?.case_id ? `${location.origin}/verify/${node.data.case_id}` : undefined,
  });
}

export function exportExcel(api: GridApi) {
  const p = exportParams();
  api.exportDataAsExcel({ ...p, fileName: `${p.fileName}.xlsx`, sheetName: "Orders", author: "Doorstep" });
}
function exportCsv(api: GridApi) {
  const p = exportParams();
  api.exportDataAsCsv({ ...p, fileName: `${p.fileName}.csv` });
}

/** Scroll to the given orders and flash them once (the "N updated" chip). */
export function revealRows(api: GridApi, ids: string[]) {
  const nodes = ids.map((id) => api.getRowNode(id)).filter((n): n is IRowNode => !!n && n.displayed);
  if (!nodes.length) return;
  api.ensureNodeVisible(nodes[0], "middle");
  api.flashCells({ rowNodes: nodes, flashDuration: 700, fadeDuration: 1400 });
}

/** Cmd/Ctrl+F inside the grid goes to the page's search; AG Grid's Find then highlights the matches. */
function findKey(e: React.KeyboardEvent) {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "f") {
    const box = document.querySelector<HTMLInputElement>('input[aria-label="Search orders"]');
    if (box) { e.preventDefault(); box.focus(); box.select(); }
  }
}

export function toggleColumnsPanel(api: GridApi) {
  if (api.isSideBarVisible()) api.setSideBarVisible(false);
  else { api.setSideBarVisible(true); api.openToolPanel("columns"); }
}

// ---------- the grid ----------

/** Lucide's strokes for the icons AG Grid draws in the toolbar and side bar, so they match the page's. */
const lucide = (body: string) =>
  `<svg class="ds-icon" xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;
const PAGE = '<path d="M6 22a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h8a2.4 2.4 0 0 1 1.704.706l3.588 3.588A2.4 2.4 0 0 1 20 8v12a2 2 0 0 1-2 2z"/><path d="M14 2v5a1 1 0 0 0 1 1h5"/>';
const gridIcons = {
  columns: lucide('<rect width="18" height="18" x="3" y="3" rx="2"/><path d="M9 3v18"/><path d="M15 3v18"/>'),
  excelExport: lucide('<rect width="18" height="18" x="3" y="3" rx="2"/><path d="M3 9h18"/><path d="M3 15h18"/><path d="M9 9v12"/><path d="M15 9v12"/>'),
  pdfExport: lucide(`${PAGE}<path d="M10 9H8"/><path d="M16 13H8"/><path d="M16 17H8"/>`),
  filter: lucide('<path d="M10 20a1 1 0 0 0 .553.895l2 1A1 1 0 0 0 14 21v-7a2 2 0 0 1 .517-1.341L21.74 4.67A1 1 0 0 0 21 3H3a1 1 0 0 0-.742 1.67l7.225 7.989A2 2 0 0 1 10 14z"/>'),
  chart: lucide('<path d="M3 3v16a2 2 0 0 0 2 2h16"/><path d="M7 16h8"/><path d="M7 11h12"/><path d="M7 6h3"/>'),
  doorstep: lucide('<path d="m12 14 4-4"/><path d="M3.34 19a10 10 0 1 1 17.32 0"/>'),
};

const LANE_KEYS = Object.keys(LANES) as Lane[];

/** The side bar's Doorstep panel: numbers for what the grid shows, and one Clear filters. */
function DoorstepPanel({ api }: CustomToolPanelProps<OrderRow>) {
  useModelTick(api);
  const shown: OrderRow[] = [];
  api.forEachNodeAfterFilter((n) => { if (n.data && !n.group) shown.push(n.data); });
  const count = (k: Lane) => shown.filter((r) => laneOf(r) === k).length;
  const filtered = Object.keys(api.getFilterModel() ?? {}).length > 0;
  return (
    <div className="ds-panel">
      <dl className="ds-panel-lanes">
        {LANE_KEYS.map((k) => (
          <div key={k} className={`lane-${k}`}><dt className="lane-name">{LANES[k].label}</dt><dd className="num">{count(k)}</dd></div>
        ))}
      </dl>
      <div className="ds-panel-stake">
        <span>At stake</span>
        <strong className="num">{sumStake(shown)}</strong>
      </div>
      <button type="button" className="btn" disabled={!filtered} onClick={() => api.setFilterModel(null)}>Clear filters</button>
    </div>
  );
}

const sideBar: SideBarDef = {
  toolPanels: [
    {
      id: "columns", labelDefault: "Columns", labelKey: "columns", iconKey: "columns", toolPanel: "agColumnsToolPanel",
      toolPanelParams: { suppressRowGroups: true, suppressValues: true, suppressPivots: true, suppressPivotMode: true },
    },
    // AG Grid's New Filters Tool Panel: an "Add filter" list; filters set elsewhere (Ask) appear here too.
    { id: "filters", labelDefault: "Filters", labelKey: "filters", iconKey: "filter", toolPanel: "agNewFiltersToolPanel" },
    { id: "doorstep", labelDefault: "Doorstep", labelKey: "doorstep", iconKey: "doorstep", toolPanel: DoorstepPanel, width: 240 },
  ],
  hiddenByDefault: true,
};

const getRowId = (p: { data: OrderRow }) => p.data.order_id;
const isRowMaster = (d: OrderRow | undefined) => !!d && d.order_id !== GUIDE_ID;
/** On a phone the Doorstep row keeps its first sentence ("7 need you. Start with the PayPal dispute ($89).") on two lines. */
function firstSentence(parts: Part[]): Part[] {
  const end = parts.findIndex((x, i) => i > 1 && x === ".");
  return end > 0 ? parts.slice(0, end + 1) : parts;
}
const isFullWidthRow = (p: { rowNode: IRowNode }) => isGuide(p.rowNode) || !!p.rowNode.footer;
const laneOrder = (p: { nodeA: IRowNode; nodeB: IRowNode }) =>
  (LANES[p.nodeA.key as Lane]?.rank ?? 9) - (LANES[p.nodeB.key as Lane]?.rank ?? 9);
// Column filters live in the Filters panel and the right-click menu, not as icons on every header.
// Doorstep's own notes stay read-only; the seller can add, edit and delete their own anywhere else.
const liveNotes = { current: new Map<string, DoorstepNote>() };
const defaultColDef: ColDef<OrderRow> = { sortable: true, resizable: true, suppressHeaderMenuButton: true, suppressHeaderFilterButton: true,
  suppressNoteActions: (p) => doorstepOwns(liveNotes.current, p.data?.order_id, p.column.getColId()) };
const noteText = { addNote: "Add note", editNote: "Edit note", viewNote: "View note", deleteNote: "Delete note", notePlaceholder: "Note" };
const gridText = { ...noteText, rowGroupColumnsEmptyMessage: "Drag a header here to group" };
const rowStyle = { cursor: "pointer" };
const cellSelection = { enableHeaderHighlight: false } as const;

export type OrdersGridProps = {
  rows: OrderRow[];
  search: string;
  empty: string;
  onOpen: (r: OrderRow) => void;
  onReady: (api: GridApi) => void;
  /** Where the orders come from (demo or sandbox), on the left of the status bar. */
  source: string;
  /** Orders the last poll changed: they flash once and keep a thin rail for a few seconds. */
  changed?: { ids: Set<string>; at: Date } | null;
  /** Orders are still loading: skeleton rows in the grid's own shape. */
  loading?: boolean;
  /** Show the Doorstep row (what needs the seller, and Start) when something does. */
  guide?: boolean;
  /** Download the hand-off report (PDF) of what the grid shows. */
  onHandoff?: () => void;
};

const RAIL_MS = 8000;
const loadingRows = { rowCount: 6 };

export default function OrdersGrid({ rows, search, empty, onOpen, onReady, source, changed, loading = false, guide = false, onHandoff }: OrdersGridProps) {
  const api = useRef<GridApi | null>(null);
  const reduced = useReducedMotion();
  const phone = useMedia("(max-width: 640px)");
  const getRowHeight = useCallback((p: { node: IRowNode }) => (isGuide(p.node) ? (phone ? 84 : 64) : p.node.group || p.node.footer ? 44 : undefined), [phone]);
  useEffect(() => { const a = api.current; if (a && !a.isDestroyed()) a.resetRowHeights(); }, [phone]);
  const railed = useRef<Set<string>>(new Set());
  const rowClassRules = useMemo(() => ({ "row-changed": (p: { data?: OrderRow }) => !!p.data && railed.current.has(p.data.order_id) }), []);
  useEffect(() => {
    const a = api.current;
    if (!a || !changed) return;
    const nodes = () => [...changed.ids].map((id) => a.getRowNode(id)).filter((n): n is IRowNode => !!n);
    // After the grid has moved the rows to their new places (the glide): mark them, then flash once;
    // the rail fades after a while.
    const start = afterGlide(() => {
      if (a.isDestroyed()) return;
      railed.current = new Set(changed.ids);
      a.redrawRows({ rowNodes: nodes() });
      // Flash on the next frame, once the redrawn rows are in the page.
      window.requestAnimationFrame(() => { if (!a.isDestroyed()) a.flashCells({ rowNodes: nodes(), flashDuration: 700, fadeDuration: 1400 }); });
    });
    const end = window.setTimeout(() => {
      if (a.isDestroyed()) return;
      const was = nodes();
      railed.current = new Set();
      a.redrawRows({ rowNodes: was });
    }, RAIL_MS);
    return () => { window.clearTimeout(start); window.clearTimeout(end); };
  }, [changed]);
  // G6: Doorstep's reasons as read-only Cell Notes on the Journey and Delivery cells.
  const notes = useMemo(() => notesFor(rows), [rows]);
  const latestNotes = useRef(notes);
  latestNotes.current = notes;
  liveNotes.current = notes;
  const notesDataSource = useMemo(() => notesSource(latestNotes), []);
  useEffect(() => { const a = api.current; if (a && !a.isDestroyed()) a.refreshNotes(); }, [notes]);
  // Lanes only when they add something: a list with one lane (e.g. "Needs you") stays flat.
  const grouped = useMemo(() => new Set(rows.map(laneOf)).size > 1, [rows]);
  // Risk is scored only before delivery, so a list of delivered orders (e.g. "Needs you") would
  // show a column of dashes: the column appears only when some row has a score.
  const scored = useMemo(() => rows.some((r) => r.delivery_risk != null), [rows]);

  const columns = useMemo<ColDef<OrderRow>[]>(() => [
    {
      colId: "lane", headerName: "Lane", rowGroup: grouped, hide: true, suppressColumnsToolPanel: true,
      valueGetter: (p) => (p.data ? laneOf(p.data) : null),
      filterValueGetter: (p) => (p.data ? LANES[laneOf(p.data)].label : null),
    },
    {
      // The work order (urgency()): hidden, sorted first, so the rows are already in the order to act.
      colId: "urgency", headerName: "Urgency", hide: true, lockVisible: true, suppressColumnsToolPanel: true, suppressMovable: true, filter: false,
      valueGetter: (p) => (p.data ? urgency(p.data) : 9),
      comparator: (_a, _b, x, y) => (x.data && y.data ? byUrgency(x.data, y.data) : 0),
      initialSort: "asc", initialSortIndex: 0,
    },
    {
      colId: "item", headerName: "Order", field: "item", flex: 2.2, minWidth: phone ? 180 : 280, lockVisible: true,
      filter: "agTextColumnFilter", getQuickFilterText: (p) => haystack(p.data), getFindText: (p) => p.data?.item ?? null,
      cellRenderer: OrderCell,
    },
    {
      // The value is what the cell says ("Lost by carrier"), so grouping by Journey reads like the column.
      colId: "status", headerName: "Journey", width: 166, hide: phone, enableCellChangeFlash: true,
      valueGetter: (p) => (p.data ? journeyLabel(p.data) : null), enableRowGroup: true, suppressHeaderMenuButton: false,
      // Filtered (and asked) by what the cell says: "Lost by carrier", not "Case open". Values a
      // reading picked survive the 10 s poll even when the last matching row leaves.
      filter: "agSetColumnFilter", filterValueGetter: (p) => (p.data ? journeyLabel(p.data) : null),
      // The filter lists its labels A to Z (the column's own comparator sorts rows by journey stage).
      filterParams: { preservePreviousValues: true, comparator: (a: string | null, b: string | null) => (a ?? "").localeCompare(b ?? "") },
      comparator: (_a, _b, x, y) => (STATE_RANK[x?.data?.state ?? ""] ?? 0) - (STATE_RANK[y?.data?.state ?? ""] ?? 0),
      // A cell with a note drops its own hover title, so one thing opens on hover.
      cellRenderer: (p: CustomCellRendererProps<OrderRow>) => (p.data ? <JourneyCell r={p.data} quiet={hasNote(latestNotes.current, p.data.order_id, "status")} /> : null),
    },
    {
      colId: "risk", headerName: "Risk", field: "delivery_risk", width: 86, type: "rightAligned", hide: !scored || phone,
      filter: "agNumberColumnFilter", tooltipField: "delivery_risk", tooltipComponent: RiskTip,
      cellRenderer: (p: CustomCellRendererProps<OrderRow>) => (p.data ? <RiskCell r={p.data} /> : null),
    },
    {
      colId: "proof", headerName: "Proof", width: 112, hide: phone, enableCellChangeFlash: true,
      valueGetter: (p) => p.data?.evidence.score ?? 0,
      // Filtered by how many of the five proofs are on file (0 to 5), as the cell shows them.
      filter: "agNumberColumnFilter", filterValueGetter: (p) => (p.data ? proofList(p.data).filter((x) => x.ok).length : 0),
      tooltipValueGetter: () => "proof", tooltipComponent: EvidenceTip,
      cellRenderer: ProofCell,
    },
    {
      colId: "money", headerName: "At stake", chartDataType: "excluded", width: 128, type: "rightAligned", hide: phone, enableCellChangeFlash: true,
      valueGetter: (p) => p.data?.money_at_stake_minor ?? 0,
      filter: "agNumberColumnFilter", suppressHeaderMenuButton: false,
      // AG Grid aggregates it for group rows (per currency) and offers "% of total" in the column menu.
      aggFunc: "stake", enableShowValuesAs: true, showValuesAsDef: stakeShare,
      comparator: (_a, _b, x, y) =>
        ((x.data?.money_at_stake_minor ?? 0) - (y.data?.money_at_stake_minor ?? 0)) || ((x.data?.amount_minor ?? 0) - (y.data?.amount_minor ?? 0)),
      cellRenderer: MoneyCell,
    },
    {
      colId: "method", headerName: "Delivery", field: "method", chartDataType: "excluded", width: 120, hide: phone, enableRowGroup: true, suppressHeaderMenuButton: false,
      filter: "agSetColumnFilter", filterParams: { preservePreviousValues: true, valueFormatter: (p: { value: unknown }) => (p.value ? METHOD[String(p.value)] ?? String(p.value) : "Not chosen") },
      valueFormatter: (p) => (p.value ? METHOD[String(p.value)] ?? String(p.value) : ""),
      cellRenderer: (p: CustomCellRendererProps<OrderRow>) => (p.data ? <DeliveryCell r={p.data} /> : null),
    },
    methodNameColumn,
    ...currencyColumns(),
    {
      colId: "next", headerName: "Next step", hide: true, width: 160,
      valueGetter: (p) => (p.data ? routeLabel(p.data) || nextAction(p.data).label : ""), filter: "agSetColumnFilter", enableRowGroup: true,
      filterParams: { preservePreviousValues: true },
    },
    {
      colId: "buyer", headerName: "Buyer area", field: "buyer_area", hide: true, width: 120, filter: "agSetColumnFilter", enableRowGroup: true,
    },
    {
      colId: "updated", headerName: "Updated", field: "last_update", hide: true, width: 110, initialSort: "desc", initialSortIndex: 1, type: "rightAligned",
      valueFormatter: (p) => (p.value ? ago(String(p.value)) : ""), cellClass: "faint-cell", filter: false,
    },
    {
      colId: "action", headerName: "Next", width: phone ? 128 : 150, pinned: "right", lockPinned: true, sortable: false, resizable: false,
      suppressMovable: true, suppressColumnsToolPanel: true, filter: false, cellClass: "cell-end",
      cellRenderer: ActionCell, cellRendererParams: { onOpen },
    },
  ], [grouped, onOpen, scored, phone]);

  // The Doorstep row: hidden while the seller searches (the rows below are then a search result).
  const guideText = useMemo(() => (guide && !search ? guideParts(rows, urgency, byUrgency) : null), [guide, search, rows]);
  const pinnedTop = useMemo(() => (guideText ? [GUIDE] : undefined), [guideText]);
  // Start: the first order that needs the seller, in the grid's own order (so it follows a sort).
  const onStart = useCallback(() => {
    let first: OrderRow | null = null;
    api.current?.forEachNodeAfterFilterAndSort((n) => { if (!first && !n.group && n.data?.needs_action) first = n.data; });
    if (first) onOpen(first);
  }, [onOpen]);
  const guideParams = useMemo(() => ({ parts: guideText && phone ? firstSentence(guideText) : guideText, onStart }), [guideText, phone, onStart]);

  const getContextMenuItems = useCallback((p: GetContextMenuItemsParams<OrderRow>): (DefaultMenuItem | MenuItemDef)[] => {
    const r = isGuide(p.node) ? undefined : p.node?.data;
    const items: (DefaultMenuItem | MenuItemDef)[] = [];
    if (r) {
      items.push(
        { name: `${nextAction(r).urgent ? nextAction(r).label : "Open order"}`, action: () => onOpen(r) },
        { name: p.node!.expanded ? "Hide details" : "Show details", action: () => p.node!.setExpanded(!p.node!.expanded) },
        { name: "Copy order reference", action: () => { void navigator.clipboard?.writeText(r.order_ref); } },
        "separator",
        "note",
      );
    }
    if (p.api.getCellRanges()?.length) items.push({ name: "Chart", icon: gridIcons.chart, action: () => chartSelection(p.api) }, "separator");
    items.push(
      { name: "Export to Excel", action: () => exportExcel(p.api) },
      { name: "Export to CSV", action: () => exportCsv(p.api) },
      ...(onHandoff ? [{ name: "Hand-off report (PDF)", action: onHandoff }] : []),
      "separator",
      { name: "Columns and filters", action: () => toggleColumnsPanel(p.api) },
    );
    return items;
  }, [onOpen, onHandoff]);

  // The header menu (on hover): sort, group by this column, and "% of total" on At stake.
  const getMainMenuItems = useCallback((p: GetMainMenuItemsParams<OrderRow>): (DefaultMenuItem | MenuItemDef)[] => {
    const col = p.column;
    if (!col) return p.defaultItems as DefaultMenuItem[];
    const items: (DefaultMenuItem | MenuItemDef)[] = ["sortAscending", "sortDescending"];
    if (col.getColDef().enableRowGroup) items.push("separator", col.isRowGroupActive() ? "rowUnGroup" : "rowGroup");
    if (col.getColDef().enableShowValuesAs) items.push("separator", "showValuesAsSubMenu");
    items.push("separator", "resetColumns");
    return items;
  }, []);
  const onRowClicked = useCallback((e: RowClickedEvent<OrderRow>) => {
    if (isGuide(e.node) || e.node.footer) return;
    if (e.node.group) { e.node.setExpanded(!e.node.expanded); return; }
    if ((e.event?.target as HTMLElement | null)?.closest("[data-own-click]")) return;
    if (isSelecting(e.api, e.event as MouseEvent | null)) return;
    if (e.data) onOpen(e.data);
  }, [onOpen]);
  // Keyboard: arrows move between cells (AG Grid); Enter on a row opens the order, or a lane.
  const onCellKeyDown = useCallback((e: CellKeyDownEvent<OrderRow> | FullWidthCellKeyDownEvent<OrderRow>) => {
    const k = e.event as KeyboardEvent | null | undefined;
    if (k?.key !== "Enter" || isGuide(e.node) || e.node.footer || (k.target as HTMLElement | null)?.closest("button, input")) return;
    if (e.node.group) { e.node.setExpanded(!e.node.expanded); return; }
    if (e.data) onOpen(e.data);
  }, [onOpen]);
  const detailParams = useMemo(() => ({ onOpen }), [onOpen]);
  // AG Grid's Quick Access Toolbar: the grid's own tools, right-aligned. Kept stable (a new toolbar
  // rebuilds every item), so the hand-off callback is read through a ref.
  const handoffRef = useRef(onHandoff);
  handoffRef.current = onHandoff;
  const toolbar = useMemo((): Toolbar => ({
    alignment: "right",
    items: [
      // AG Grid's Row Group Panel, in the toolbar: the grouping as pills; drag a header in to group by it.
      { key: "groups", toolbarItem: "agRowGroupPanelToolbarItem", alignment: "left" },
      { key: "columns", icon: "columns", tooltip: "Columns and filters", action: ({ api: a }) => toggleColumnsPanel(a) },
      { key: "chart", icon: "chart", tooltip: "Chart", action: ({ api: a }) => chartStakeByMethod(a) },
      { key: "excel", icon: "excelExport", tooltip: "Export to Excel", action: ({ api: a }) => exportExcel(a) },
      { key: "handoff", icon: "pdfExport", tooltip: "Download hand-off report", action: () => handoffRef.current?.() },
    ],
  }), []);
  // A search of two or more words that matches nothing is probably a question: say Enter asks it.
  const emptyParams = useMemo(() => ({
    text: !search ? empty : search.trim().includes(" ") ? "No match. Press Enter to ask it." : "No match.",
    searching: !!search,
  }), [search, empty]);
  // AG Grid's status bar: where the orders come from, then the row count and money at stake, both
  // following search and filters.
  const statusBar = useMemo((): { statusPanels: StatusPanelDef[] } => ({
    statusPanels: [
      { statusPanel: SourcePanel, statusPanelParams: { text: source }, align: "left" },
      // Drag across At-stake cells: AG Grid's live Sum, Count and Average of the selection.
      { statusPanel: "agAggregationComponent", statusPanelParams: { aggFuncs: ["avg", "count", "sum"], valueFormatter: selectionFormatter }, align: "right" },
      { statusPanel: "agTotalAndFilteredRowCountComponent", align: "right" },
    ],
  }), [source]);

  return (
    <div className="orders-grid" data-loading={loading || undefined} onKeyDown={findKey}>
      <AgGridReact<OrderRow>
        theme={gridTheme}
        rowData={rows}
        columnDefs={columns}
        getRowId={getRowId}
        defaultColDef={defaultColDef}
        quickFilterText={search}
        onGridReady={(e) => { api.current = e.api; onReady(e.api); }}
        rowClassRules={rowClassRules}
        onRowClicked={onRowClicked}
        onCellKeyDown={onCellKeyDown}
        rowStyle={rowStyle}
        // Cell selection: drag across cells for AG Grid's live sum; Ctrl/Cmd+C copies them.
        cellSelection={cellSelection}
        processCellForClipboard={clipboardValue}
        // Row grouping: lanes in a fixed order, open by default, drawn as one full-width row.
        groupDisplayType="groupRows"
        groupRowRenderer={GroupRow}
        aggFuncs={aggFuncs}
        grandTotalRow="bottom"
        suppressAggFuncInHeader
        getMainMenuItems={getMainMenuItems}
        suppressMenuHide={false}
        // Lanes always open; detail panels stay closed until the seller asks (masterDefaultExpanded).
        groupDefaultExpanded={-1}
        masterDefaultExpanded={0}
        initialGroupOrderComparator={laneOrder}
        getRowHeight={getRowHeight}
        // The Doorstep row: pinned on top, drawn full width.
        pinnedTopRowData={pinnedTop}
        isFullWidthRow={isFullWidthRow}
        fullWidthCellRenderer={DoorstepRow}
        fullWidthCellRendererParams={guideParams}
        // Master/detail: the chevron in the order cell opens the proof panel under the row.
        masterDetail
        isRowMaster={isRowMaster}
        detailCellRenderer={DetailPanel}
        detailCellRendererParams={detailParams}
        detailRowAutoHeight
        // Enterprise furniture: side bar (hidden until asked), right-click menu, Excel export.
        sideBar={sideBar}
        enableFilterHandlers
        toolbar={toolbar}
        icons={gridIcons}
        getContextMenuItems={getContextMenuItems}
        excelStyles={excelStyles}
        statusBar={statusBar}
        tooltipShowDelay={250}
        tooltipHideDelay={8000}
        overlayComponentSelector={emptySelector}
        overlayComponentParams={emptyParams}
        animateRows={!reduced}
        loading={loading}
        loadingRows={loadingRows}
        notesDataSource={notesDataSource}
        noteTrigger="hover"
        localeText={gridText}
        noteShowDelay={300}
        findSearchValue={search}
        chartThemes={chartThemes}
        customChartThemes={customChartThemes}
        chartThemeOverrides={chartThemeOverrides}
        domLayout="autoHeight"
      />
    </div>
  );
}
