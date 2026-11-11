import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AgGridReact, type CustomCellRendererProps, type CustomDetailCellRendererProps, type CustomStatusPanelProps } from "ag-grid-react";
import type { ColDef, GridApi, IRowNode, RowClickedEvent, StatusPanelDef } from "ag-grid-community";
import { ArrowUpRight, ChevronDown, ChevronRight, SearchX } from "lucide-react";
import { money } from "../../format";
import { gridTheme } from "../../grid/theme";
import { ItemCell, StatusPill } from "../LedgerGrid";
import type { DeskRow } from "./claimsApi";
import ClaimDetail from "./ClaimDetail";
import { useDesk } from "./desk";
import { FAULT, carrierOf, daysLeft, isMade, isOpen, joinMoney, plural, rateText, record, statusOf, sumBy, urgency, type Seg } from "./model";

export type Row = DeskRow & { __total?: string };

const RIGHT = { headerClass: "ag-right-aligned-header", cellClass: "ag-right-aligned-cell num lg-money" } as const;

/** Where the claims come from, on the left of AG Grid's status bar. */
function SourcePanel(p: CustomStatusPanelProps & { text: string }) {
  return p.text ? <span className="cd-source-panel">{p.text}</span> : null;
}

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

function ItemColumn(p: CustomCellRendererProps<Row> & { onToggle: (id: string) => void }) {
  const open = useExpanded(p.node);
  const r = p.data;
  if (!r) return null;
  if (r.__total) return <ItemCell item="" sub="" total={r.__total} />;
  return (
    <div className="order-cell">
      <button type="button" data-own-click className="expander" aria-expanded={open}
        aria-label={`${open ? "Hide" : "Show"} claim for ${r.item}`} onClick={() => p.onToggle(r.recovery_id)}>
        <ChevronRight aria-hidden />
      </button>
      <ItemCell item={r.item} sub={`${r.order_ref}${r.simulated ? " · demo" : ""}`} />
    </div>
  );
}

/** Days left to file, with how much of the window is left; "Filed" once submitted. */
export function DueCell({ r }: { r: DeskRow }) {
  const { now } = useDesk();
  if (r.status === "SUBMITTED") return <span className="cd-due filed">Filed</span>;
  if (r.status !== "PREPARED") return <span className="faint">—</span>;
  const d = daysLeft(r, now);
  const u = urgency(r, now);
  const pct = r.window_days ? Math.min(100, Math.round((d / r.window_days) * 100)) : null;
  return (
    <span className={`cd-due u-${u}`} title={`File by ${new Date(r.deadline_at).toLocaleDateString("en-GB", { day: "numeric", month: "long" })}`}>
      <span className="num">{d === 0 ? "Today" : `${d}d`}</span>
      {pct != null && <span className="cd-due-bar" aria-hidden><span style={{ width: `${Math.max(pct, 6)}%` }} /></span>}
    </span>
  );
}

function NextCell(p: CustomCellRendererProps<Row> & { onNext: (r: DeskRow) => void; onCase: (r: DeskRow) => void }) {
  const desk = useDesk();
  const r = p.data;
  if (!r || r.__total) return null;
  const busy = desk.busy === r.recovery_id;
  if (r.actions.includes("submitted")) return <button type="button" data-own-click className="row-action tint" disabled={busy} onClick={() => p.onNext(r)}>I submitted it</button>;
  if (r.actions.includes("paid")) return <button type="button" data-own-click className="row-action tint" disabled={busy} onClick={() => p.onNext(r)}>Carrier paid</button>;
  return <button type="button" data-own-click className="row-action quiet" onClick={() => p.onCase(r)}>Open case<ArrowUpRight aria-hidden /></button>;
}

/** CL-06: a carrier's claims in one line: how many, won ÷ claimed, money back and still open. */
function CarrierGroup(p: CustomCellRendererProps<Row>) {
  const open = useExpanded(p.node);
  const rows = (p.node.allLeafChildren ?? []).map((n) => n.data).filter((d): d is Row => !!d);
  const rec = record(rows);
  const rate = rateText(rec);
  const openMoney = sumBy(rows.filter(isOpen), (r) => r.claim_minor);
  return (
    <div className="lane-row group-by cd-group" aria-expanded={open} aria-label={`${String(p.node.key)}: ${plural(rows.length, "claim")}`}>
      <ChevronDown className="chev" aria-hidden />
      <span className="lane-name">{String(p.node.key)}</span>
      <span className="count-chip num">{rows.length}</span>
      <span className="cd-group-stats num">
        <span title="Paid ÷ decided claims">{rec.decided ? `${rec.won} of ${rec.decided} won` : "None decided"}</span>
        {rec.decided > 0 && <span title="Paid back ÷ claimed, on decided claims"><b>{joinMoney(rec.recovered)}</b> of {joinMoney(rec.claimed)}{rate ? ` · ${rate}` : ""}</span>}
        {Object.keys(openMoney).length > 0 && <span><b>{joinMoney(openMoney)}</b> open</span>}
      </span>
    </div>
  );
}

function Detail(p: CustomDetailCellRendererProps<Row>) {
  return p.data ? <div className="cd-detail-wrap"><ClaimDetail id={p.data.recovery_id} /></div> : null;
}

function EmptyFilter({ text }: { text?: string }) {
  return <div className="cd-empty-filter"><SearchX aria-hidden /><span>{text ?? "No claims"}</span></div>;
}
const emptySelector = (p: { overlayType: string }) => (p.overlayType === "noRows" || p.overlayType === "noMatchingRows" ? { component: EmptyFilter } : undefined);

/** The claims grid: master-detail story per claim, a status filter, carrier grouping, per-currency totals. */
export default function ClaimsGrid({ rows, seg, byCarrier, open, onOpen, onNext, onReady }: {
  rows: DeskRow[]; seg: Seg; byCarrier: boolean; open: string | null;
  onOpen: (id: string | null) => void; onNext: (r: DeskRow) => void; onReady: (api: GridApi<Row>) => void;
}) {
  const desk = useDesk();
  const api = useRef<GridApi<Row> | null>(null);
  const segRef = useRef(seg);
  segRef.current = seg;
  const openRef = useRef(open);
  openRef.current = open;
  const toggle = useCallback((id: string) => onOpen(openRef.current === id ? null : id), [onOpen]);
  const caseRef = useRef(desk.openCase);
  caseRef.current = desk.openCase;
  const nextRef = useRef(onNext);
  nextRef.current = onNext;

  const columns = useMemo((): ColDef<Row>[] => [
    { headerName: "Item", colId: "item", field: "item", flex: 2, minWidth: 240, cellRenderer: ItemColumn, cellRendererParams: { onToggle: toggle } },
    { headerName: "Carrier", colId: "carrier", flex: 1, minWidth: 130, enableRowGroup: true,
      valueGetter: (p) => (!p.data || p.data.__total ? "" : carrierOf(p.data)) },
    { headerName: "Fault", colId: "fault", width: 110, valueGetter: (p) => (!p.data || p.data.__total ? "" : FAULT[p.data.failure_point] ?? p.data.failure_point) },
    { headerName: "Claim", colId: "claim", field: "claim_minor", ...RIGHT, width: 110,
      valueFormatter: (p) => (p.data ? money(p.data.claim_minor, p.data.currency) : ""),
      tooltipValueGetter: (p) => (p.data && !p.data.__total ? (p.data.cover_minor != null ? `Carrier cover ${money(p.data.cover_minor, p.data.currency)}` : "Cover unconfirmed") : undefined) },
    { headerName: "Status", colId: "status", field: "status", width: 120, valueFormatter: (p) => statusOf(p.value ?? "").label,
      cellRenderer: (p: CustomCellRendererProps<Row>) => (p.data && !p.data.__total ? <StatusPill tone={statusOf(p.data.status).tone}>{statusOf(p.data.status).label}</StatusPill> : null) },
    { headerName: "Due", colId: "due", field: "deadline_at", width: 100,
      cellRenderer: (p: CustomCellRendererProps<Row>) => (p.data && !p.data.__total ? <DueCell r={p.data} /> : null) },
    { headerName: "Paid", colId: "paid", field: "recovered_minor", ...RIGHT, width: 110,
      valueFormatter: (p) => (p.data && (p.data.__total || p.data.recovered_minor != null) ? money(p.data.recovered_minor, p.data.currency) : "—") },
    { headerName: "", colId: "next", width: 150, sortable: false, resizable: false, cellClass: "cd-next-cell",
      cellRenderer: NextCell, cellRendererParams: { onNext: (r: DeskRow) => nextRef.current(r), onCase: (r: DeskRow) => caseRef.current(r) } },
  ], [toggle]);
  const defaultColDef = useMemo<ColDef<Row>>(() => ({ sortable: true, resizable: true, suppressHeaderMenuButton: true, suppressHeaderFilterButton: true }), []);

  // Totals per currency over what the filter shows, claims never made left out; never across currencies.
  const totals = useMemo((): Row[] => {
    const shown = rows.filter((r) => (seg === "all" || r.status === seg) && isMade(r));
    if (!shown.length) return [];
    const claimed = sumBy(shown, (r) => r.claim_minor);
    const paid = sumBy(shown, (r) => r.recovered_minor ?? 0);
    return Object.keys(claimed).map((currency) => ({ __total: `Total ${currency}`, currency, claim_minor: claimed[currency], recovered_minor: paid[currency] ?? 0 }) as Row);
  }, [rows, seg]);

  // The filter, the grouping and the open claim follow the page's state.
  useEffect(() => { api.current?.onFilterChanged(); }, [seg]);
  useEffect(() => {
    const a = api.current;
    if (!a || a.isDestroyed()) return;
    a.applyColumnState({ state: [{ colId: "carrier", rowGroup: byCarrier, hide: byCarrier }], defaultState: { rowGroup: false } });
  }, [byCarrier]);
  const syncOpen = useCallback(() => {
    const a = api.current;
    if (!a || a.isDestroyed()) return;
    a.forEachNode((n) => {
      if (n.group || !n.data) return;
      const want = n.data.recovery_id === openRef.current;
      if (!!n.expanded !== want) n.setExpanded(want);
      if (!!n.isSelected() !== want) n.setSelected(want);
    });
  }, []);
  useEffect(() => {
    syncOpen();
    const a = api.current;
    const node = open ? a?.getRowNode(open) : undefined;
    if (a && node && node.displayed) a.ensureNodeVisible(node, "top");
  }, [open, syncOpen]);

  const clicked = (e: RowClickedEvent<Row>) => {
    if (e.node.rowPinned || !e.data || e.node.group) return;
    if ((e.event?.target as HTMLElement | null)?.closest("button, a, input, [data-own-click]")) return;
    toggle(e.data.recovery_id);
  };
  const source = rows.some((r) => r.simulated) ? "Demo orders · carrier history simulated" : "";
  const statusBar = useMemo((): { statusPanels: StatusPanelDef[] } => ({
    statusPanels: [
      { statusPanel: SourcePanel, statusPanelParams: { text: source }, align: "left" },
      { statusPanel: "agTotalAndFilteredRowCountComponent", align: "right" },
    ],
  }), [source]);

  return (
    <div className="ledger-grid cd-grid clickable">
      <AgGridReact<Row>
        theme={gridTheme}
        rowData={rows as Row[]}
        columnDefs={columns}
        defaultColDef={defaultColDef}
        getRowId={(p) => p.data.recovery_id ?? p.data.__total ?? ""}
        onGridReady={(e) => {
          api.current = e.api; onReady(e.api);
          if (byCarrier) e.api.applyColumnState({ state: [{ colId: "carrier", rowGroup: true, hide: true }] });
        }}
        onRowDataUpdated={syncOpen}
        isExternalFilterPresent={() => segRef.current !== "all"}
        doesExternalFilterPass={(n) => !!n.data && n.data.status === segRef.current}
        masterDetail
        isRowMaster={(d) => !!d && !d.__total}
        detailCellRenderer={Detail}
        detailRowAutoHeight
        rowSelection={{ mode: "singleRow", checkboxes: false, enableClickSelection: false }}
        onRowClicked={clicked}
        // Keyboard: Tab reaches each row's chevron and action; a click draws no cell focus box.
        suppressCellFocus
        rowHeight={60}
        pinnedBottomRowData={totals}
        statusBar={statusBar}
        groupDisplayType="groupRows"
        groupRowRenderer={CarrierGroup}
        groupDefaultExpanded={-1}
        tooltipShowDelay={250}
        overlayComponentSelector={emptySelector}
        overlayComponentParams={{ text: seg === "all" ? "No claims" : `No ${statusOf(seg).label.toLowerCase()} claims` }}
        domLayout="autoHeight"
      />
    </div>
  );
}
