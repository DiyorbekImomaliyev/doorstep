import { useMemo, type ReactNode } from "react";
import { AgGridReact } from "ag-grid-react";
import type { CellKeyDownEvent, ColDef, GridApi, RowClickedEvent, StatusPanelDef } from "ag-grid-community";
import { isSelecting, useDeskGrid } from "../grid/deskGrid";
import { Check, Copy } from "lucide-react";
import { useState } from "react";
import { gridTheme } from "../grid/theme";
import { useMedia } from "../motion";
import { itemKind } from "../components/gridCells";
import { Badge, Loading } from "../components/ui";
import type { Tone } from "../format";
import { money } from "../format";
import "./pages.css";

/** Pinned total rows carry this flag; cell renderers read it to draw "Total GBP" instead of an item. */
export type Totalled = { __total?: string };

export function PageHead({ title, line, right }: { title: string; line?: string; right?: ReactNode }) {
  return (
    <div className="page-head">
      <div>
        <h1>{title}</h1>
        {line && <p>{line}</p>}
      </div>
      {right}
    </div>
  );
}

export function Kpis({ items }: { items: { label: string; value: string; sub?: string; tone?: "attention" }[] }) {
  return (
    <section className="kpis kpis-3" aria-label="Summary">
      {items.map((k) => (
        <div key={k.label} className={`kpi ${k.tone ?? ""}`}>
          <span className="label">{k.label}</span>
          <span className="value" style={{ fontSize: 22 }}>{k.value}</span>
          <span className="sub">{k.sub ?? " "}</span>
        </div>
      ))}
    </section>
  );
}

/** Item name with its small tile, and the order reference under it. */
export function ItemCell({ item, sub, total }: { item: string; sub: string; total?: string }) {
  if (total) return <span className="lg-total">{total}</span>;
  return (
    <div className="order-cell">
      <span className="item-tile" aria-hidden>{itemKind(item).icon}</span>
      <span className="cell-two" style={{ gap: 3 }}>
        <span className="item-name">{item || "Order"}</span>
        <span className="sub num">{sub}</span>
      </span>
    </div>
  );
}

export function StatusPill({ tone, children }: { tone: Tone; children: ReactNode }) {
  return <Badge tone={tone}>{children}</Badge>;
}

/** A monospace id that copies on click. */
export function CopyId({ value, shown }: { value: string | null | undefined; shown?: string }) {
  const [done, setDone] = useState(false);
  if (!value) return <span className="faint">—</span>;
  const copy = async () => {
    try { await navigator.clipboard.writeText(value); setDone(true); window.setTimeout(() => setDone(false), 1500); } catch { /* clipboard blocked */ }
  };
  return (
    <button type="button" className="copy-id" onClick={copy} title={`${value} · click to copy`} aria-label={`Copy ${value}`}>
      <span>{shown ?? value}</span>{done ? <Check aria-hidden /> : <Copy aria-hidden />}
    </button>
  );
}

/** Right-aligned money column: header and cells together. */
export const RIGHT = { headerClass: "ag-right-aligned-header", cellClass: "ag-right-aligned-cell num lg-money" } as const;

export const amount = (minor: number | null | undefined, currency: string) => money(minor, currency);

/** Per-currency totals as pinned rows: one row per currency, never added together. */
export function sumByCurrency<T extends { currency: string }>(rows: T[], pick: (r: T) => number): Record<string, number> {
  const out: Record<string, number> = {};
  for (const r of rows) out[r.currency] = (out[r.currency] ?? 0) + pick(r);
  return out;
}

export const joinMoney = (by: Record<string, number>) => {
  const parts = Object.entries(by).filter(([, v]) => v > 0).map(([c, v]) => money(v, c));
  return parts.length ? parts.join(" + ") : money(0, "GBP");
};

type Props<T extends object> = {
  label: string;
  rows: T[] | null;
  columns: ColDef<T>[];
  getRowId: (r: T) => string;
  onOpen?: (r: T) => void;
  empty: string;
  totals?: T[];
  /** The same totals as text lines, for the phone stack. */
  totalLines?: string[];
  /** One card per row on phones. */
  card: (r: T) => ReactNode;
  groupPanel?: boolean;
  /** The grid's API once it is ready, for a page's GridTools (Columns and filters, Excel). */
  onReady?: (api: GridApi<T>) => void;
};

type Rec = Record<string, unknown> & Totalled;
const fieldOf = <T,>(c: ColDef<T>) => (c.field as string | undefined) ?? "";

/** The Control Room's grid look for a read-only list; phones get a stack of cards. */
export default function LedgerGrid<T extends object>({ label, rows, columns: given, getRowId, onOpen, empty, totals, totalLines, card, groupPanel, onReady }: Props<T>) {
  const phone = useMedia("(max-width: 600px)");
  const defaultColDef = useMemo<ColDef<T>>(() => ({ sortable: true, resizable: true, suppressHeaderMenuButton: true, suppressHeaderFilterButton: true, enableCellChangeFlash: true }), []);
  // GP-01..GP-06: the Control Room's grid kit. Money is any `*_minor` field (in the row's currency), dates any `*_at`.
  const rec = (r: T) => r as unknown as Rec;
  const kit = useDeskGrid<T>({
    page: label.toLowerCase(), sheet: label,
    money: given.filter((c) => fieldOf(c).endsWith("_minor")).map((c) => ({ colId: c.colId ?? fieldOf(c), minor: (r: T) => rec(r)[fieldOf(c)] as number | null })),
    currency: (r) => rec(r).currency as string | undefined,
    dates: Object.fromEntries(given.filter((c) => fieldOf(c).endsWith("_at")).map((c) => [c.colId ?? fieldOf(c), (r: T) => (rec(r).__total ? null : rec(r)[fieldOf(c)] as string | null)])),
    text: (id, r) => (id === "item" ? rec(r).__total ?? (rec(r).order_ref ? `${String(rec(r).item)} (${String(rec(r).order_ref)})` : undefined) : undefined),
    isRecord: (r) => !rec(r).__total,
    onOpen,
  });
  const columns = useMemo(() => kit.columns(given), [kit, given]);
  const statusBar = useMemo((): { statusPanels: StatusPanelDef[] } => ({
    statusPanels: [...kit.statusPanels, { statusPanel: "agTotalAndFilteredRowCountComponent", align: "right" }],
  }), [kit]);
  if (!rows) return <Loading height={320} />;
  if (phone) {
    return (
      <section className="table-card" aria-label={label}>
        {rows.length === 0 ? <p className="lg-empty">{empty}</p> : (
          <ul className="ledger-cards">
            {rows.map((r) => (
              <li key={getRowId(r)}>
                {onOpen ? <button type="button" className="ledger-card" onClick={() => onOpen(r)}>{card(r)}</button> : <div className="ledger-card">{card(r)}</div>}
              </li>
            ))}
          </ul>
        )}
        {rows.length > 0 && totalLines && totalLines.length > 0 && (
          <div className="ledger-totals">{totalLines.map((l) => <span key={l} className="num">{l}</span>)}</div>
        )}
      </section>
    );
  }
  const clicked = (e: RowClickedEvent<T>) => {
    if (e.node.rowPinned || !e.data || !onOpen) return;
    if ((e.event?.target as HTMLElement | null)?.closest("button, a")) return;
    if (isSelecting(e.api, e.event)) return;
    onOpen(e.data);
  };
  // Keyboard: arrows move between cells; Enter on a row opens it.
  const keyDown = (e: CellKeyDownEvent<T>) => {
    const k = e.event as KeyboardEvent | null | undefined;
    if (k?.key !== "Enter" || !onOpen || e.node.rowPinned || !e.data || (k.target as HTMLElement | null)?.closest("button, a")) return;
    onOpen(e.data);
  };
  return (
    <section className={`table-card ledger-grid orders-grid${onOpen ? " clickable" : ""}`} aria-label={label}>
      <AgGridReact<T>
        theme={gridTheme}
        rowData={rows}
        columnDefs={columns}
        defaultColDef={defaultColDef}
        getRowId={(p) => getRowId(p.data)}
        onRowClicked={clicked}
        onCellKeyDown={keyDown}
        rowHeight={60}
        pinnedBottomRowData={totals}
        statusBar={statusBar}
        rowGroupPanelShow={groupPanel ? "always" : undefined}
        groupDisplayType="groupRows"
        groupDefaultExpanded={-1}
        {...kit.props}
        onGridReady={(e) => onReady?.(e.api)}
        tooltipShowDelay={250}
        overlayNoRowsTemplate={`<span class="lg-empty">${empty}</span>`}
        domLayout="autoHeight"
      />
    </section>
  );
}
