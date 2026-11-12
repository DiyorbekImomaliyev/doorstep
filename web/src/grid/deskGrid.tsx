import { useMemo, useRef, useState } from "react";
import {
  ModuleRegistry,
  type CellClassParams, type ColDef, type DefaultMenuItem, type ExcelStyle, type GetContextMenuItemsParams, type GridApi, type GridState,
  type IRowNode, type IStatusPanelValueFormatterParams, type MenuItemDef, type ProcessCellForExportParams, type SideBarDef,
  type StateUpdatedEvent, type StatusPanelDef, type ValueFormatterParams, type ValueGetterParams,
} from "ag-grid-community";
import {
  CellSelectionModule, ClipboardModule, ColumnsToolPanelModule, ContextMenuModule, ExcelExportModule, NewFiltersToolPanelModule,
  SetFilterModule, SideBarModule, StatusBarModule,
} from "ag-grid-enterprise";
import { Columns3, Sheet } from "lucide-react";
import { money } from "../format";

/**
 * GP-01..GP-06: one finance-grade kit for every seller grid beside the Control Room (Claims, Disputes,
 * Money and the shared LedgerGrid). The Control Room's own tools, in one place: right-click menu
 * (Open case, Copy, Copy with headers, Excel, CSV, Columns and filters, Reset layout), Excel export
 * with real numbers and dates, a range's per-currency Sum and Average in the status bar, a hidden
 * Columns + Filters side bar, the column layout kept per page, and cells that flash when they change.
 */

ModuleRegistry.registerModules([
  CellSelectionModule, ClipboardModule, ColumnsToolPanelModule, ContextMenuModule, ExcelExportModule,
  NewFiltersToolPanelModule, SetFilterModule, SideBarModule, StatusBarModule,
]);

/** A money column: its value in minor units, in the row's own currency (never added across currencies). */
export type MoneyCol<T> = { colId: string; minor: (r: T) => number | null | undefined };

export type DeskGridOptions<T> = {
  /** Lower-case page name: the file ("Doorstep claims 2026-11-12.xlsx") and the remembered layout. */
  page: string;
  /** Excel sheet name. */
  sheet: string;
  money: MoneyCol<T>[];
  currency: (r: T) => string | null | undefined;
  /** Date columns, exported as Excel dates: colId to ISO timestamp. */
  dates?: Record<string, (r: T) => string | null | undefined>;
  /** What a cell says in a file, when its value or formatter is not enough. */
  text?: (colId: string, r: T, value: unknown) => string | undefined;
  /** Columns never exported (row buttons, chevrons). */
  skip?: string[];
  /** Columns whose visibility the page sets itself (e.g. hidden while grouped): never remembered. */
  transient?: string[];
  /** Pinned totals and other rows that are not records. */
  isRecord?: (r: T) => boolean;
  /** "Open case" in the right-click menu. */
  onOpen?: (r: T) => void;
  canOpen?: (r: T) => boolean;
  /** The page's own Excel export, when it has one (Money's ledger workbook). */
  excel?: (api: GridApi<T>) => void;
  /** After Reset layout: put back what the page sets itself. */
  onReset?: (api: GridApi<T>) => void;
};

type DeskContext = { desk?: { excel: (api: GridApi) => void; csv: (api: GridApi) => void } };

const KEPT = ["columnOrder", "columnSizing", "columnVisibility", "sort"] as const;
const stamp = () => new Date().toISOString().slice(0, 10);
const token = () => /\/seller\/([^/?#]+)/.exec(window.location.pathname)?.[1] ?? "";
const storeKey = (page: string) => `doorstep.grid.${token()}.${page}`;

function readLayout(page: string): GridState | undefined {
  try {
    const v = JSON.parse(window.localStorage.getItem(storeKey(page)) ?? "null") as unknown;
    return v && typeof v === "object" ? (v as GridState) : undefined;
  } catch { return undefined; }
}

function writeLayout(page: string, state: GridState, transient: string[]) {
  const out: GridState = {};
  for (const k of KEPT) if (state[k] !== undefined) Object.assign(out, { [k]: state[k] });
  if (out.columnVisibility) out.columnVisibility = { hiddenColIds: out.columnVisibility.hiddenColIds.filter((c) => !transient.includes(c)) };
  try { window.localStorage.setItem(storeKey(page), JSON.stringify(out)); } catch { /* storage blocked or full */ }
}

function forgetLayout(page: string) {
  try { window.localStorage.removeItem(storeKey(page)); } catch { /* storage blocked */ }
}

/** Excel: money as numbers in the row's currency format, dates as dates. */
const CURRENCIES = ["GBP", "USD", "EUR"];
const SYMBOL: Record<string, string> = { GBP: "£", USD: "$", EUR: "€" };
export const deskExcelStyles: ExcelStyle[] = [
  { id: "header", font: { bold: true, color: "#1F1A18" }, interior: { color: "#F6ECEE", pattern: "Solid" } },
  ...CURRENCIES.map((c): ExcelStyle => ({ id: `dsMoney${c}`, dataType: "Number", numberFormat: { format: `"${SYMBOL[c]}"#,##0.00` } })),
  { id: "dsMoney", dataType: "Number", numberFormat: { format: "#,##0.00" } },
  { id: "dsDate", dataType: "DateTime", numberFormat: { format: "d mmm yyyy hh:mm" } },
];
const isoForExcel = (v: string) => { const d = new Date(v); return Number.isNaN(d.getTime()) ? "" : d.toISOString().slice(0, 19); };

/** A click that ends a drag across cells (or extends a selection) selects; it does not open the row. */
export function isSelecting(api: GridApi, e: Event | null | undefined): boolean {
  const m = e as MouseEvent | null | undefined;
  if (m && (m.shiftKey || m.metaKey || m.ctrlKey)) return true;
  const ranges = api.getCellRanges() ?? [];
  return ranges.length > 1 || ranges.some((g) => g.columns.length > 1 || g.startRow?.rowIndex !== g.endRow?.rowIndex || g.startRow?.rowPinned !== g.endRow?.rowPinned);
}

export function toggleColumns(api: GridApi) {
  if (api.isSideBarVisible()) api.setSideBarVisible(false);
  else { api.setSideBarVisible(true); api.openToolPanel("columns"); }
}

export function deskExcel(api: GridApi) { (api.getGridOption("context") as DeskContext | undefined)?.desk?.excel(api); }

const sideBar: SideBarDef = {
  toolPanels: [
    { id: "columns", labelDefault: "Columns", labelKey: "columns", iconKey: "columns", toolPanel: "agColumnsToolPanel",
      toolPanelParams: { suppressRowGroups: true, suppressValues: true, suppressPivots: true, suppressPivotMode: true } },
    { id: "filters", labelDefault: "Filters", labelKey: "filters", iconKey: "filter", toolPanel: "agNewFiltersToolPanel" },
  ],
  hiddenByDefault: true,
};
const cellSelection = { enableHeaderHighlight: false } as const;
const localeText = { copyWithHeaders: "Copy with headers" };

/**
 * The kit for one grid. Spread `props` onto AgGridReact, pass `columns()` the column defs (it adds the
 * Excel classes and default filters), and put `statusPanels` before the row count.
 */
export function useDeskGrid<T extends object>(opts: DeskGridOptions<T>) {
  const o = useRef(opts);
  o.current = opts;
  const [initialState] = useState(() => readLayout(opts.page));

  return useMemo(() => {
    const isRecord = (r: T | undefined): r is T => !!r && (o.current.isRecord?.(r) ?? true);
    const moneyOf = (colId: string) => o.current.money.find((m) => m.colId === colId);

    /** What a cell is in a file or on the clipboard; money as a plain number when `plain`. */
    const cellText = (p: ProcessCellForExportParams<T>, plain: boolean): string => {
      const r = p.node?.data;
      const id = p.column.getColId();
      if (!r) return p.value == null ? "" : String(p.value);
      const m = moneyOf(id);
      if (m) {
        const v = m.minor(r);
        if (v == null) return "";
        return plain ? (v / 100).toFixed(2) : money(v, o.current.currency(r) ?? "GBP");
      }
      const d = o.current.dates?.[id];
      if (d) { const v = d(r); return v ? (plain ? isoForExcel(v) : new Date(v).toLocaleString("en-GB", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" })) : ""; }
      const own = o.current.text?.(id, r, p.value);
      if (own !== undefined) return own;
      const shown = p.formatValue?.(p.value);
      return shown == null ? (p.value == null ? "" : String(p.value)) : String(shown);
    };
    const keys = (api: GridApi<T>) => api.getAllDisplayedColumns().map((c) => c.getColId()).filter((id) => !(o.current.skip ?? []).includes(id));

    const excel = (api: GridApi<T>) => {
      if (o.current.excel) { o.current.excel(api); return; }
      api.exportDataAsExcel({
        fileName: `Doorstep ${o.current.page} ${stamp()}.xlsx`, sheetName: o.current.sheet, author: "Doorstep",
        columnKeys: keys(api), processCellCallback: (p) => cellText(p, true),
        processRowGroupCallback: (p) => String(p.node.key ?? ""),
      });
    };
    const csv = (api: GridApi<T>) => api.exportDataAsCsv({
      fileName: `Doorstep ${o.current.page} ${stamp()}.csv`, columnKeys: keys(api), processCellCallback: (p) => cellText(p, false),
      processRowGroupCallback: (p) => String(p.node.key ?? ""),
    });
    const reset = (api: GridApi<T>) => {
      forgetLayout(o.current.page);
      api.resetColumnState();
      o.current.onReset?.(api);
    };

    /** The money cells in the selected ranges, per currency; pinned totals, groups and detail rows are not records. */
    const selected = (api: GridApi) => {
      const by: Record<string, number> = {}, n: Record<string, number> = {};
      const seen = new Set<string>();
      for (const range of api.getCellRanges() ?? []) {
        const { startRow: a, endRow: b } = range;
        if (!a || !b || a.rowPinned || b.rowPinned) continue;
        for (const col of range.columns) {
          const m = moneyOf(col.getColId());
          if (!m) continue;
          for (let i = Math.min(a.rowIndex, b.rowIndex); i <= Math.max(a.rowIndex, b.rowIndex); i++) {
            const node = api.getDisplayedRowAtIndex(i) as IRowNode<T> | undefined;
            if (!node || node.group || node.detail || !isRecord(node.data)) continue;
            const key = `${node.id}|${m.colId}`;
            const v = m.minor(node.data);
            const c = o.current.currency(node.data);
            if (seen.has(key) || v == null || !c) continue;
            seen.add(key);
            by[c] = (by[c] ?? 0) + v;
            n[c] = (n[c] ?? 0) + 1;
          }
        }
      }
      return { by, n };
    };
    const formatter = (p: IStatusPanelValueFormatterParams): string => {
      if (p.key === "count") return String(p.value ?? 0);
      const { by, n } = selected(p.api);
      const cur = Object.keys(by);
      if (!cur.length) return p.value == null ? "" : Number(p.value).toLocaleString("en-GB", { maximumFractionDigits: 2 });
      return cur.map((c) => money(p.key === "avg" ? Math.round(by[c] / n[c]) : by[c], c)).join(p.key === "avg" ? " · " : " + ");
    };
    const statusPanels: StatusPanelDef[] = [
      { statusPanel: "agAggregationComponent", statusPanelParams: { aggFuncs: ["avg", "count", "sum"], valueFormatter: formatter }, align: "right" },
    ];

    const getContextMenuItems = (p: GetContextMenuItemsParams<T>): (DefaultMenuItem | MenuItemDef)[] => {
      const r = p.node && !p.node.rowPinned && !p.node.group ? p.node.data : undefined;
      const items: (DefaultMenuItem | MenuItemDef)[] = [];
      const open = o.current.onOpen;
      if (open && isRecord(r) && (o.current.canOpen?.(r) ?? true)) items.push({ name: "Open case", action: () => open(r) }, "separator");
      items.push(
        "copy", "copyWithHeaders", "separator",
        { name: "Export to Excel", action: () => excel(p.api) },
        { name: "Export to CSV", action: () => csv(p.api) },
        "separator",
        { name: "Columns and filters", action: () => toggleColumns(p.api) },
        { name: "Reset layout", action: () => reset(p.api) },
      );
      return items;
    };

    const onStateUpdated = (e: StateUpdatedEvent<T>) => {
      if (e.sources.includes("gridInitializing") || !e.sources.some((x) => (KEPT as readonly string[]).includes(x))) return;
      writeLayout(o.current.page, e.state, o.current.transient ?? []);
    };

    /** Excel classes for money and date cells, and a filter on every column that has something to filter. */
    const columns = (defs: ColDef<T>[]): ColDef<T>[] => defs.map((c) => {
      const id = c.colId ?? (c.field as string | undefined) ?? "";
      const m = moneyOf(id);
      const d = o.current.dates?.[id];
      const rules: Record<string, (q: CellClassParams<T>) => boolean> = { ...(c.cellClassRules as Record<string, (q: CellClassParams<T>) => boolean> | undefined) };
      if (m) {
        for (const cur of CURRENCIES) rules[`dsMoney${cur}`] = (q) => !!q.data && m.minor(q.data) != null && o.current.currency(q.data) === cur;
        rules.dsMoney = (q) => !!q.data && m.minor(q.data) != null && !CURRENCIES.includes(o.current.currency(q.data) ?? "");
      }
      if (d) rules.dsDate = (q) => !!q.data && !!d(q.data);
      const out: ColDef<T> = { ...c, cellClassRules: rules };
      // Row buttons and chevrons stay put: not in the Columns list, never filtered.
      if ((o.current.skip ?? []).includes(id)) return { ...out, suppressColumnsToolPanel: true, lockVisible: true };
      if (c.filter !== undefined) return out;
      if (m) return { ...out, filter: "agNumberColumnFilter", filterValueGetter: (q: ValueGetterParams<T>) => (isRecord(q.data) ? (m.minor(q.data) ?? 0) / 100 : null) };
      if (id === "item") return { ...out, filter: "agTextColumnFilter" };
      if (d) return out;
      const fmt = typeof c.valueFormatter === "function" ? c.valueFormatter : null;
      if (!c.valueGetter && !fmt && c.cellRenderer) return out;
      return {
        ...out, filter: "agSetColumnFilter",
        filterValueGetter: (q: ValueGetterParams<T>) => {
          if (!isRecord(q.data)) return null;
          const v = q.getValue(id);
          return fmt ? fmt({ ...q, value: v } as ValueFormatterParams<T>) : v;
        },
      };
    });

    const props = {
      cellSelection,
      getContextMenuItems,
      processCellForClipboard: (p: ProcessCellForExportParams<T>) => cellText(p, true),
      sideBar,
      // AG Grid's New Filters Tool Panel needs filter handlers.
      enableFilterHandlers: true,
      excelStyles: deskExcelStyles,
      initialState,
      onStateUpdated,
      localeText,
      context: { desk: { excel, csv } } satisfies DeskContext,
    };
    return { props, statusPanels, columns, excel, csv, reset };
  }, [initialState]);
}

/** The grid's tools beside the page's filter chips, as icon buttons like the Control Room's toolbar. */
export function GridTools({ api, excel = true }: { api: () => GridApi | null | undefined; excel?: boolean }) {
  const run = (f: (a: GridApi) => void) => { const a = api(); if (a && !a.isDestroyed()) f(a); };
  return (
    <div className="grid-tools" role="toolbar" aria-label="Grid tools">
      <button type="button" className="btn icon-btn" aria-label="Columns and filters" title="Columns and filters" onClick={() => run(toggleColumns)}>
        <Columns3 size={15} aria-hidden />
      </button>
      {excel && (
        <button type="button" className="btn icon-btn" aria-label="Export to Excel" title="Export to Excel" onClick={() => run(deskExcel)}>
          <Sheet size={15} aria-hidden />
        </button>
      )}
    </div>
  );
}
