import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AgGridReact, type CustomCellRendererProps, type CustomDetailCellRendererProps,
  type CustomStatusPanelProps, type CustomTooltipProps,
} from "ag-grid-react";
import {
  AllCommunityModule, ModuleRegistry,
  type CellKeyDownEvent, type ColDef, type DefaultMenuItem, type ExcelStyle, type FullWidthCellKeyDownEvent, type GetContextMenuItemsParams,
  type GridApi, type IRowNode, type IStatusPanelValueFormatterParams, type MenuItemDef, type ProcessCellForExportParams,
  type RowClickedEvent, type SideBarDef, type StatusPanelDef, type Toolbar,
} from "ag-grid-community";
import {
  CellSelectionModule, ClipboardModule, ColumnMenuModule, ColumnsToolPanelModule, ContextMenuModule, ExcelExportModule, FiltersToolPanelModule, LicenseManager,
  MasterDetailModule, RowGroupingModule, SetFilterModule, SideBarModule, StatusBarModule,
  AiToolkitModule, NotesModule, PdfExportModule, ToolbarModule,
} from "ag-grid-enterprise";
import { ArrowRight, ArrowUpRight, Check, ChevronDown, ChevronRight, Circle, SearchX } from "lucide-react";
import type { OrderRow } from "../api";
import { DeliveryCell, JourneyCell, RiskCell, itemKind, journeyLabel, journeyTone } from "../components/gridCells";
import { FAILURE_POINT, METHOD, ROUTE, ago, money, pct, riskTone, stateTone, type Tone } from "../format";
import { gridTheme } from "../grid/theme";
import { afterGlide, useMedia, useReducedMotion } from "../motion";
import { Says, type Part } from "../components/ai";
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
  ColumnsToolPanelModule, FiltersToolPanelModule, ColumnMenuModule, ContextMenuModule, ExcelExportModule,
  StatusBarModule, AiToolkitModule, NotesModule, PdfExportModule, ToolbarModule, CellSelectionModule, ClipboardModule,
]);


// ---------- vocabulary ----------

type Lane = "you" | "case" | "way" | "done";
const LANES: Record<Lane, { rank: number; label: string; hint: string }> = {
  you: { rank: 0, label: "Needs you", hint: "to approve, review or pack" },
  case: { rank: 1, label: "Cases in progress", hint: "Doorstep is handling these" },
  way: { rank: 2, label: "On the way", hint: "shipped, not yet received" },
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
  const parts = Object.entries(by).filter(([, v]) => v > 0).map(([c, v]) => money(v, c));
  return parts.length ? parts.join(" + ") : money(0, "GBP");
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

function routeLabel(r: OrderRow): string {
  const key = r.case_state && ROUTE[r.case_state] ? r.case_state : r.route;
  return key && ROUTE[key] ? ROUTE[key].label : "";
}

function haystack(r: OrderRow): string {
  return `${r.item} ${r.order_ref} ${r.buyer_area} ${r.buyer_country} ${r.status} ${r.method ?? ""} ${routeLabel(r)} ${nextAction(r).label} ${itemKind(r.item).label}`;
}

const PROOFS = ["address checked", "packing photo", "tracking", "delivered scan", "possession proof"];
function proofList(r: OrderRow): { name: string; ok: boolean }[] {
  const all = [...r.evidence.present.map((n) => ({ name: n, ok: true })), ...r.evidence.missing.map((n) => ({ name: n, ok: false }))];
  const rank = (n: string) => { const i = PROOFS.indexOf(n); return i < 0 ? 99 : i; };
  return all.sort((a, b) => rank(a.name) - rank(b.name));
}
const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

function rowTone(r: OrderRow): Tone {
  if (r.case_state) return ROUTE[r.case_state]?.tone ?? "accent";
  return stateTone(r.state);
}

const STEPS = ["Paid", "Packed", "Shipped", "Delivered", "Received"];
const STEP_OF: Record<string, number> = {
  CREATED: 0, PAID: 1, PACKED: 2, SHIPPED: 3, IN_TRANSIT: 3, EXCEPTION: 3,
  DELIVERED: 4, PICKED_UP: 4, CONFIRMED: 5, CASE_OPEN: 4, RESOLVED: 4,
};
const STATE_RANK: Record<string, number> = {
  CREATED: 0, PAID: 1, PACKED: 2, SHIPPED: 3, IN_TRANSIT: 4, EXCEPTION: 5,
  DELIVERED: 6, PICKED_UP: 6, CONFIRMED: 7, CASE_OPEN: 8, RESOLVED: 9,
};
const COUNTRY: Record<string, string> = { GB: "United Kingdom", US: "United States" };

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
  // In the Needs-you lane, a quiet step number: the order to work through them in.
  const step = laneOf(r) === "you" ? (p.node.parent?.childrenAfterSort?.indexOf(p.node) ?? -1) + 1 : 0;
  return (
    <div className="order-cell">
      {step > 0 && <span className="step-no num" aria-hidden>{step}</span>}
      <button data-own-click className="expander" aria-expanded={open}
        aria-label={`${open ? "Hide" : "Show"} details for ${r.item}`} onClick={() => p.node.setExpanded(!open)}>
        <ChevronRight aria-hidden />
      </button>
      <span className="item-tile" aria-hidden>{k.icon}</span>
      <span className="cell-two" style={{ gap: 4 }}>
        <span className="item-name">{r.item}</span>
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
  const share = p.column?.getShowValuesAs() ? p.valueFormatted : null;
  const sub = share != null ? money(stake, r.currency) : stake === r.amount_minor ? null : `of ${money(r.amount_minor, r.currency)}`;
  return (
    <div className={`money-cell ${stake ? "" : "none"}`}>
      <span className="amt num">{share ?? money(stake, r.currency)}</span>
      {sub && <span className="sub num">{sub}</span>}
    </div>
  );
}

/** Money cells in the selected ranges, per currency: a pound is never added to a dollar. */
function selectedStake(api: GridApi): { by: Record<string, number>; n: Record<string, number> } {
  const by: Record<string, number> = {}, n: Record<string, number> = {};
  const seen = new Set<string>();
  for (const range of api.getCellRanges() ?? []) {
    const { startRow: a, endRow: b } = range;
    if (!a || !b || a.rowPinned || b.rowPinned || !range.columns.some((c) => c.getColId() === "money")) continue;
    for (let i = Math.min(a.rowIndex, b.rowIndex); i <= Math.max(a.rowIndex, b.rowIndex); i++) {
      const node = api.getDisplayedRowAtIndex(i);
      const r = node && !node.group ? (node.data as OrderRow | undefined) : undefined;
      if (!r || seen.has(r.order_id)) continue;
      seen.add(r.order_id);
      by[r.currency] = (by[r.currency] ?? 0) + r.money_at_stake_minor;
      n[r.currency] = (n[r.currency] ?? 0) + 1;
    }
  }
  return { by, n };
}

/** AG Grid's aggregation panel, in money: Sum and Average of the selected At-stake cells per currency. */
export function selectionFormatter(p: IStatusPanelValueFormatterParams): string {
  if (p.key === "count") return String(p.value ?? 0);
  const { by, n } = selectedStake(p.api);
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

/** A lane: who acts next. Count and money at stake per currency (never summed across currencies). */
function LaneRow(p: CustomCellRendererProps<OrderRow>) {
  const open = useExpanded(p.node);
  useModelTick(p.api);
  const key = String(p.node.key) as Lane;
  const meta = LANES[key];
  const rows = (p.node.childrenAfterFilter ?? []).map((n) => n.data).filter((d): d is OrderRow => !!d);
  if (!meta) return null;
  return (
    <div className={`lane-row lane-${key}`} aria-expanded={open} aria-label={`${meta.label}: ${rows.length} order${rows.length === 1 ? "" : "s"}`}>
      <ChevronDown className="chev" aria-hidden />
      <span className="lane-name">{meta.label}</span>
      <span className="count-chip num">{rows.length}</span>
      <span className="lane-hint">{meta.hint}</span>
      <span className="lane-meta num">{sumStake(rows)} at stake</span>
    </div>
  );
}

/** Master/detail: the order's proof, journey, risk and case, without leaving the table. */
function DetailPanel(p: CustomDetailCellRendererProps<OrderRow> & { onOpen: (r: OrderRow) => void }) {
  const r = p.data;
  if (!r) return null;
  const list = proofList(r);
  const done = STEP_OF[r.state] ?? 0;
  const tone = rowTone(r);
  return (
    <div className="detail">
      <div className="detail-inner">
        <section className="detail-journey">
          <h4>Journey</h4>
          <ol className={`stepper tone-${tone}`}>
            {STEPS.map((s, i) => (
              <li key={s} className={i < done ? (i === done - 1 ? "on last" : "on") : ""}>
                <span className="dot" aria-hidden>{i < done && <Check />}</span>{s}
              </li>
            ))}
          </ol>
          <p className="faint" style={{ marginTop: 10 }}>{r.status} · updated {ago(r.last_update)}</p>
        </section>
        <section>
          <h4>Proof on file</h4>
          <ul className="checklist">
            {list.map((x) => <li key={x.name} className={x.ok ? "ok" : "miss"}>{x.ok ? <Check aria-hidden /> : <Circle aria-hidden />}{cap(x.name)}</li>)}
          </ul>
        </section>
        <section>
          <h4>Delivery risk</h4>
          {r.delivery_risk == null ? <p className="faint">Not scored.</p> : (
            <>
              <span className={`pill ${riskTone(r.delivery_risk) === "neutral" ? "plain" : riskTone(r.delivery_risk)}`}>{pct(r.delivery_risk)}</span>
              <ul className="reasons">{r.risk_reasons.slice(0, 3).map((x) => <li key={x}>{x}</li>)}</ul>
            </>
          )}
          <h4 style={{ marginTop: 14 }}>Buyer</h4>
          <p style={{ margin: 0, fontSize: 13 }}>{r.buyer_area || "—"}, {COUNTRY[r.buyer_country] ?? r.buyer_country}</p>
        </section>
        <section>
          <h4>Case</h4>
          {r.case_id ? (
            <dl className="mini-kv">
              <dt>Likely cause</dt><dd>{r.failure_point ? FAILURE_POINT[r.failure_point] ?? r.failure_point : "Being worked out"}</dd>
              <dt>Route</dt><dd>{routeLabel(r) || "—"}</dd>
              <dt>Rule</dt><dd className="mono">{r.rule_id ?? "—"}</dd>
              {r.confidence && <><dt>Confidence</dt><dd>{cap(r.confidence)}</dd></>}
            </dl>
          ) : <p className="faint">No problem reported.</p>}
          <button className="btn" style={{ marginTop: 14 }} onClick={() => p.onOpen(r)}>Open order<ArrowUpRight size={15} aria-hidden /></button>
        </section>
      </div>
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

// ---------- status bar ----------

/** Status bar: money at stake in the rows the seller can see, per currency (never summed across). */
function StakePanel({ api }: CustomStatusPanelProps<OrderRow>) {
  useModelTick(api);
  const shown: OrderRow[] = [];
  api.forEachNodeAfterFilter((n) => { if (n.data && !n.group) shown.push(n.data); });
  return (
    <div className="ag-status-name-value stake-panel">
      <span className="ag-status-name-value-label">At stake</span>
      <span className="ag-status-name-value-value num">{sumStake(shown)}</span>
    </div>
  );
}

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
      <span>{!searching && overlayType === "noMatchingRows" ? "No orders match. Remove a chip or press Undo to see more." : text}</span>
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
    processCellCallback: (q: { column: { getColId(): string }; node?: IRowNode | null; value: unknown }) =>
      (q.node?.data ? exportValue(q.column.getColId(), q.node.data as OrderRow, q.value) : ""),
    processRowGroupCallback: (q: { node: IRowNode }) => LANES[q.node.key as Lane]?.label ?? String(q.node.key),
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
};

const sideBar: SideBarDef = {
  toolPanels: [
    {
      id: "columns", labelDefault: "Columns", labelKey: "columns", iconKey: "columns", toolPanel: "agColumnsToolPanel",
      toolPanelParams: { suppressRowGroups: true, suppressValues: true, suppressPivots: true, suppressPivotMode: true },
    },
    { id: "filters", labelDefault: "Filters", labelKey: "filters", iconKey: "filter", toolPanel: "agFiltersToolPanel" },
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
const isFullWidthRow = (p: { rowNode: IRowNode }) => isGuide(p.rowNode);
const laneOrder = (p: { nodeA: IRowNode; nodeB: IRowNode }) =>
  (LANES[p.nodeA.key as Lane]?.rank ?? 9) - (LANES[p.nodeB.key as Lane]?.rank ?? 9);
// Column filters live in the Filters panel and the right-click menu, not as icons on every header.
// Doorstep's own notes stay read-only; the seller can add, edit and delete their own anywhere else.
const liveNotes = { current: new Map<string, DoorstepNote>() };
const defaultColDef: ColDef<OrderRow> = { sortable: true, resizable: true, suppressHeaderMenuButton: true, suppressHeaderFilterButton: true,
  suppressNoteActions: (p) => doorstepOwns(liveNotes.current, p.data?.order_id, p.column.getColId()) };
const noteText = { addNote: "Add note", editNote: "Edit note", viewNote: "View note", deleteNote: "Delete note", notePlaceholder: "Note" };
const rowStyle = { cursor: "pointer" };
const cellSelection = { enableHeaderHighlight: true } as const;

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
  const getRowHeight = useCallback((p: { node: IRowNode }) => (isGuide(p.node) ? (phone ? 84 : 64) : p.node.group ? 44 : undefined), [phone]);
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
      filter: "agTextColumnFilter", getQuickFilterText: (p) => haystack(p.data),
      cellRenderer: OrderCell,
    },
    {
      colId: "status", headerName: "Journey", field: "status", width: 166, hide: phone, enableCellChangeFlash: true,
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
      colId: "money", headerName: "At stake", width: 128, type: "rightAligned", hide: phone, enableCellChangeFlash: true,
      valueGetter: (p) => p.data?.money_at_stake_minor ?? 0,
      filter: "agNumberColumnFilter",
      comparator: (_a, _b, x, y) =>
        ((x.data?.money_at_stake_minor ?? 0) - (y.data?.money_at_stake_minor ?? 0)) || ((x.data?.amount_minor ?? 0) - (y.data?.amount_minor ?? 0)),
      cellRenderer: MoneyCell,
    },
    {
      colId: "method", headerName: "Delivery", field: "method", width: 120, hide: phone,
      filter: "agSetColumnFilter", filterParams: { preservePreviousValues: true, valueFormatter: (p: { value: unknown }) => (p.value ? METHOD[String(p.value)] ?? String(p.value) : "Not chosen") },
      valueFormatter: (p) => (p.value ? METHOD[String(p.value)] ?? String(p.value) : ""),
      cellRenderer: (p: CustomCellRendererProps<OrderRow>) => (p.data ? <DeliveryCell r={p.data} /> : null),
    },
    {
      colId: "next", headerName: "Next step", hide: true, width: 160,
      valueGetter: (p) => (p.data ? routeLabel(p.data) || nextAction(p.data).label : ""), filter: "agSetColumnFilter",
      filterParams: { preservePreviousValues: true },
    },
    {
      colId: "buyer", headerName: "Buyer area", field: "buyer_area", hide: true, width: 120, filter: "agSetColumnFilter",
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
    items.push(
      { name: "Export to Excel", action: () => exportExcel(p.api) },
      { name: "Export to CSV", action: () => exportCsv(p.api) },
      ...(onHandoff ? [{ name: "Hand-off report (PDF)", action: onHandoff }] : []),
      "separator",
      { name: "Columns and filters", action: () => toggleColumnsPanel(p.api) },
    );
    return items;
  }, [onOpen, onHandoff]);

  const onRowClicked = useCallback((e: RowClickedEvent<OrderRow>) => {
    if (isGuide(e.node)) return;
    if (e.node.group) { e.node.setExpanded(!e.node.expanded); return; }
    if ((e.event?.target as HTMLElement | null)?.closest("[data-own-click]")) return;
    if (isSelecting(e.api, e.event as MouseEvent | null)) return;
    if (e.data) onOpen(e.data);
  }, [onOpen]);
  // Keyboard: arrows move between cells (AG Grid); Enter on a row opens the order, or a lane.
  const onCellKeyDown = useCallback((e: CellKeyDownEvent<OrderRow> | FullWidthCellKeyDownEvent<OrderRow>) => {
    const k = e.event as KeyboardEvent | null | undefined;
    if (k?.key !== "Enter" || isGuide(e.node) || (k.target as HTMLElement | null)?.closest("button, input")) return;
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
      { key: "columns", icon: "columns", tooltip: "Columns and filters", action: ({ api: a }) => toggleColumnsPanel(a) },
      { key: "excel", icon: "excelExport", tooltip: "Export to Excel", action: ({ api: a }) => exportExcel(a) },
      { key: "handoff", icon: "pdfExport", tooltip: "Download hand-off report", action: () => handoffRef.current?.() },
    ],
  }), []);
  // A search of two or more words that matches nothing is probably a question: say Enter asks it.
  const emptyParams = useMemo(() => ({
    text: !search ? empty : search.trim().includes(" ") ? "No orders contain those words. Press Enter and Doorstep will read it as a question." : "No orders match your search.",
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
      { statusPanel: StakePanel, align: "right" },
    ],
  }), [source]);

  return (
    <div className="orders-grid" data-loading={loading || undefined}>
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
        groupRowRenderer={LaneRow}
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
        localeText={noteText}
        noteShowDelay={300}
        domLayout="autoHeight"
      />
    </div>
  );
}
