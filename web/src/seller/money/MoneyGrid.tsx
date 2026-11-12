import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AgGridReact, type CustomDetailCellRendererProps } from "ag-grid-react";
import { ModuleRegistry, type CellKeyDownEvent, type ColDef, type GridApi, type GridReadyEvent, type ICellRendererParams, type IRowNode, type RowClickedEvent, type StatusPanelDef } from "ag-grid-community";
import { ExcelExportModule, MasterDetailModule, StatusBarModule } from "ag-grid-enterprise";
import { ChevronRight, ShieldCheck } from "lucide-react";
import { money, when } from "../../format";
import { gridTheme } from "../../grid/theme";
import { deskExcelStyles, isSelecting, useDeskGrid } from "../../grid/deskGrid";
import { useMedia } from "../../motion";
import { CopyId, ItemCell, RIGHT, StatusPill, type Totalled } from "../LedgerGrid";
import { KIND, hasLeft, keyShort, statusOf } from "./ledger";
import type { DeskRow } from "./moneyApi";
import MoneyDetailPanel from "./MoneyDetail";
import { ledgerExcelStyles } from "./exportLedger";

ModuleRegistry.registerModules([MasterDetailModule, ExcelExportModule, StatusBarModule]);

export type Row = DeskRow & Totalled;

const rowId = (p: { data: Row }) => p.data.action_id ?? p.data.__total ?? "";
const isMaster = (d: Row | undefined) => !!d && !d.__total;
const amountOf = (r: DeskRow) => (r.kind === "evidence" ? "—" : money(r.amount_minor, r.currency));
const excelStyles = [...ledgerExcelStyles, ...deskExcelStyles];

function Receipts({ r }: { r: DeskRow }) {
  if (!r.receipts) return <span className="faint">—</span>;
  return <span className="mn-signed" title={`${r.receipts} signed receipt${r.receipts === 1 ? "" : "s"}: open the row to verify`}><ShieldCheck aria-hidden />{r.receipts}</span>;
}

type Props = {
  rows: DeskRow[] | null;
  token: string;
  /** The status chip: rows outside it are filtered, not dropped, so an export keeps the ledger whole. */
  status: string;
  onOpenOrder: (orderId: string) => void;
  onRedecided: (message: string) => void;
  onApi?: (api: GridApi<Row> | null) => void;
  /** Rows whose status just changed, to flash. */
  changed: Set<string>;
  /** The page's ledger workbook (Export), also in the right-click menu. */
  onExcel?: () => void;
};

/** MN-04, MN-05: the ledger grid; a row opens its whole story beneath it. Phones get cards. */
export default function MoneyGrid({ rows, token, status, onOpenOrder, onRedecided, onApi, changed, onExcel }: Props) {
  const phone = useMedia("(max-width: 600px)");
  const api = useRef<GridApi<Row> | null>(null);
  const statusRef = useRef(status);
  statusRef.current = status;
  const [openCard, setOpenCard] = useState<string | null>(null);
  const live = useRef({ onOpenOrder, onExcel });
  live.current = { onOpenOrder, onExcel };
  // GP-01..GP-06: the Control Room's grid kit (menu, range sums, side bar, kept layout); Excel is the ledger workbook.
  const kit = useDeskGrid<Row>({
    page: "money", sheet: "Money",
    money: [{ colId: "amount_minor", minor: (r) => (r.__total || r.kind !== "evidence" ? r.amount_minor : null) }],
    currency: (r) => r.currency,
    dates: { created_at: (r) => (r.__total ? null : r.created_at), completed_at: (r) => (r.__total ? null : r.completed_at) },
    text: (id, r) => (id === "item" ? r.__total ?? `${r.item} (${r.order_ref})` : r.__total ? "" : id === "status" ? statusOf(r.status).label : undefined),
    skip: ["open"], isRecord: (r) => !r.__total,
    onOpen: (r) => live.current.onOpenOrder(r.order_id), canOpen: (r) => !!r.order_id,
    excel: () => live.current.onExcel?.(),
  });
  const statusBar = useMemo((): { statusPanels: StatusPanelDef[] } => ({
    statusPanels: [...kit.statusPanels, { statusPanel: "agTotalAndFilteredRowCountComponent", align: "right" }],
  }), [kit]);

  const totals = useMemo((): Row[] => {
    const by: Record<string, number> = {};
    for (const r of rows ?? []) if (hasLeft(r)) by[r.currency] = (by[r.currency] ?? 0) + r.amount_minor;
    return Object.entries(by).map(([currency, amount_minor]) => ({ __total: `Refunded ${currency}`, currency, amount_minor }) as Row);
  }, [rows]);

  const columns = useMemo((): ColDef<Row>[] => kit.columns([
    { colId: "open", headerName: "", width: 48, sortable: false, resizable: false, suppressMovable: true,
      cellRenderer: (p: ICellRendererParams<Row>) => p.data && !p.data.__total && (
        <button type="button" className="mn-expand" aria-expanded={!!p.node.expanded} aria-label={`${p.node.expanded ? "Hide" : "Show"} details for ${p.data.item || "order"}`}
          onClick={() => p.node.setExpanded(!p.node.expanded)}><ChevronRight aria-hidden /></button>
      ) },
    { headerName: "Date", field: "created_at", width: 142, sort: "desc", cellClass: ["num", "mnDate"],
      valueFormatter: (p) => (p.data?.__total ? "" : p.value ? when(p.value) : "") },
    { headerName: "Item", field: "item", flex: 2, minWidth: 200,
      cellRenderer: (p: ICellRendererParams<Row>) => p.data && <ItemCell item={p.data.item} sub={p.data.order_ref} total={p.data.__total} /> },
    { headerName: "Kind", field: "kind", width: 108, valueFormatter: (p) => (p.data?.__total ? "" : KIND[p.value] ?? p.value) },
    { headerName: "Amount", field: "amount_minor", ...RIGHT, cellClass: `${RIGHT.cellClass} mnNumber`, width: 100,
      valueFormatter: (p) => (p.data ? (p.data.__total ? money(p.data.amount_minor, p.data.currency) : amountOf(p.data)) : "") },
    { headerName: "Status", field: "status", width: 118,
      cellRenderer: (p: ICellRendererParams<Row>) => (p.data && !p.data.__total ? (
        <span title={statusOf(p.data.status).hint}><StatusPill tone={statusOf(p.data.status).tone}>{statusOf(p.data.status).label}</StatusPill></span>
      ) : null),
      valueFormatter: (p) => statusOf(p.value ?? "").label },
    { headerName: "Refund id", field: "paypal_refund_id", width: 142,
      cellRenderer: (p: ICellRendererParams<Row>) => (p.data && !p.data.__total ? <CopyId value={p.data.paypal_refund_id} /> : null) },
    { headerName: "Request-Id", field: "request_id", width: 144, headerTooltip: "PayPal-Request-Id: one key per write, so a retry never pays twice",
      cellRenderer: (p: ICellRendererParams<Row>) => (p.data && !p.data.__total ? <CopyId value={p.data.request_id} shown={keyShort(p.data.request_id)} /> : null) },
    { headerName: "Signed", field: "receipts", width: 82, headerTooltip: "Signed receipts (Ed25519)", ...RIGHT,
      cellRenderer: (p: ICellRendererParams<Row>) => (p.data && !p.data.__total ? <Receipts r={p.data} /> : null) },
    // Export only: the workbook gets these as their own columns.
    { headerName: "Completed", field: "completed_at", hide: true, cellClass: "mnDate", suppressColumnsToolPanel: true, filter: false },
    { headerName: "Order", field: "order_ref", hide: true, suppressColumnsToolPanel: true, filter: false },
    { headerName: "Currency", field: "currency", hide: true, suppressColumnsToolPanel: true, filter: false },
  ]), [kit]);

  const toggle = (node: IRowNode<Row>) => { if (!node.rowPinned && node.data && !node.data.__total) node.setExpanded(!node.expanded); };
  const clicked = (e: RowClickedEvent<Row>) => {
    if ((e.event?.target as HTMLElement | null)?.closest("button, a, [data-own-click]")) return;
    if (isSelecting(e.api, e.event)) return;
    toggle(e.node);
  };
  const keyDown = (e: CellKeyDownEvent<Row>) => {
    const key = (e.event as KeyboardEvent | null)?.key;
    if ((key === "Enter" || key === " ") && !(e.event?.target as HTMLElement | null)?.closest("button, a")) { e.event?.preventDefault(); toggle(e.node); }
  };
  const ready = (e: GridReadyEvent<Row>) => { api.current = e.api; onApi?.(e.api); };
  useEffect(() => () => onApi?.(null), [onApi]);
  useEffect(() => { api.current?.onFilterChanged(); }, [status]);
  useEffect(() => {
    const a = api.current;
    if (!a || !changed.size) return;
    const nodes = [...changed].map((id) => a.getRowNode(id)).filter((n): n is IRowNode<Row> => !!n);
    if (nodes.length) a.flashCells({ rowNodes: nodes, columns: ["status", "paypal_refund_id", "receipts"] });
  }, [changed]);

  const passes = useCallback((n: IRowNode<Row>) => statusRef.current === "all" || n.data?.status === statusRef.current, []);
  // The detail renderer must keep one identity: a new one makes AG Grid redraw and close open rows.
  const props = useRef({ token, onOpenOrder, onRedecided });
  props.current = { token, onOpenOrder, onRedecided };
  const Detail = useCallback((p: CustomDetailCellRendererProps<Row>) => (
    <MoneyDetailPanel row={p.data!} token={props.current.token} onOpenOrder={(id) => props.current.onOpenOrder(id)} onRedecided={(m) => props.current.onRedecided(m)} />
  ), []);
  const defaultColDef = useMemo<ColDef<Row>>(() => ({ sortable: true, resizable: true, suppressHeaderMenuButton: true, suppressHeaderFilterButton: true, enableCellChangeFlash: true }), []);

  if (!rows) return <div className="table-card mn-grid-skeleton" aria-busy="true" aria-label="Loading">{[0, 1, 2, 3].map((i) => <span key={i} className="skeleton" />)}</div>;

  if (phone) {
    const shown = rows.filter((r) => status === "all" || r.status === status);
    return (
      <section className="table-card" aria-label="Money actions">
        {shown.length === 0 ? <p className="lg-empty">None with this status.</p> : (
          <ul className="ledger-cards">
            {shown.map((r) => (
              <li key={r.action_id} className={openCard === r.action_id ? "mn-card-open" : undefined}>
                <button type="button" className="ledger-card" aria-expanded={openCard === r.action_id} onClick={() => setOpenCard((c) => (c === r.action_id ? null : r.action_id))}>
                  <span className="lc-top"><span className="lc-item">{r.item || "Order"}</span><span className="lc-money num">{amountOf(r)}</span></span>
                  <span className="lc-meta">{r.order_ref} · {KIND[r.kind] ?? r.kind} · {when(r.created_at)}</span>
                  <span className="lc-bottom"><StatusPill tone={statusOf(r.status).tone}>{statusOf(r.status).label}</StatusPill><Receipts r={r} /></span>
                </button>
                {openCard === r.action_id && <MoneyDetailPanel row={r} token={token} onOpenOrder={onOpenOrder} onRedecided={onRedecided} />}
              </li>
            ))}
          </ul>
        )}
        {totals.length > 0 && <div className="ledger-totals">{totals.map((t) => <span key={t.currency} className="num">{t.__total}: {money(t.amount_minor, t.currency)}</span>)}</div>}
      </section>
    );
  }

  return (
    <section className="table-card ledger-grid orders-grid clickable mn-grid" aria-label="Money actions">
      <AgGridReact<Row>
        theme={gridTheme}
        rowData={rows as Row[]}
        columnDefs={columns}
        defaultColDef={defaultColDef}
        getRowId={rowId}
        onGridReady={ready}
        onRowClicked={clicked}
        onCellKeyDown={keyDown}
        onRowGroupOpened={(e) => e.api.refreshCells({ rowNodes: [e.node], columns: ["open"], force: true })}
        {...kit.props}
        excelStyles={excelStyles}
        rowHeight={60}
        pinnedBottomRowData={totals}
        statusBar={statusBar}
        masterDetail
        isRowMaster={isMaster}
        detailCellRenderer={Detail}
        detailRowAutoHeight
        isExternalFilterPresent={() => statusRef.current !== "all"}
        doesExternalFilterPass={passes}
        tooltipShowDelay={250}
        overlayNoRowsTemplate={`<span class="lg-empty">None with this status.</span>`}
        domLayout="autoHeight"
      />
    </section>
  );
}
