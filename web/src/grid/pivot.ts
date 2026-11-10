import type { ColDef, ColumnState, GridApi, IRowNode } from "ag-grid-community";
import type { OrderRow } from "../api";
import { money } from "../format";
import { CURRENCY_COLS } from "./charts";

/**
 * Pivot view: delivery down the side, journey across the top, and for each journey the order count
 * and the money at stake. Money is one column per currency (a pound is never added to a dollar), and
 * only for the currencies the orders hold. Turning it off puts the columns back exactly as they were.
 */

export const PIVOT_COUNT = "pivotCount";

/** Hidden count column: every order is one. */
export function pivotColumns(): ColDef<OrderRow>[] {
  return [{
    colId: PIVOT_COUNT, headerName: "Orders", hide: true, suppressColumnsToolPanel: true, filter: false, enableValue: true,
    chartDataType: "excluded", valueGetter: (p) => (p.data ? 1 : 0),
  }];
}

/** Pivot cells: the right symbol for the currency, a dash for none. */
export function pivotCellText(colId: string, value: unknown): string {
  const v = typeof value === "number" ? value : 0;
  if (!v) return "—";
  const c = CURRENCY_COLS.find((x) => x.colId === colId);
  return c ? money(Math.round(v * 100), c.currency) : String(v);
}

type Saved = { state: ColumnState[]; groupDisplayType: unknown; masterDetail: boolean; fullWidth: unknown; autoGroup: unknown; pinnedTop: unknown };
const saved = new WeakMap<GridApi, Saved>();

export const isPivoting = (api: GridApi) => saved.has(api);

const currenciesIn = (api: GridApi<OrderRow>) => {
  const seen = new Set<string>();
  api.forEachNode((n: IRowNode<OrderRow>) => { if (n.data && n.data.money_at_stake_minor > 0) seen.add(n.data.currency); });
  return CURRENCY_COLS.filter((c) => seen.has(c.currency));
};

/** No full-width rows while pivoting: the grand total row is a normal row of numbers. */
const noFullWidth = () => false;

function mark(on: boolean) {
  const b = document.querySelector<HTMLElement>('.orders-grid .ag-toolbar-button[aria-label="Pivot"]');
  if (!b) return;
  b.setAttribute("aria-pressed", String(on));
  b.closest(".orders-grid")?.classList.toggle("is-pivot", on);
  b.setAttribute("aria-expanded", String(on));
}

export function togglePivot(api: GridApi<OrderRow>) {
  const before = saved.get(api);
  if (before) {
    api.setGridOption("pivotMode", false);
    api.setGridOption("groupDisplayType", before.groupDisplayType as never);
    api.setGridOption("masterDetail", before.masterDetail);
    api.setGridOption("isFullWidthRow", before.fullWidth as never);
    api.setGridOption("processPivotResultColDef", undefined);
    api.setGridOption("autoGroupColumnDef", before.autoGroup as never);
    api.setGridOption("pinnedTopRowData", before.pinnedTop as never);
    api.applyColumnState({ state: before.state, applyOrder: true });
    saved.delete(api);
    mark(false);
    return;
  }
  saved.set(api, {
    state: api.getColumnState(),
    groupDisplayType: api.getGridOption("groupDisplayType"),
    masterDetail: !!api.getGridOption("masterDetail"),
    fullWidth: api.getGridOption("isFullWidthRow"),
    autoGroup: api.getGridOption("autoGroupColumnDef"),
    pinnedTop: api.getGridOption("pinnedTopRowData"),
  });
  // The Doorstep row belongs to the order list.
  api.setGridOption("pinnedTopRowData", undefined);
  const values = [PIVOT_COUNT, ...currenciesIn(api).map((c) => c.colId)];
  // Pivot results are plain rows: no lane rows, no expanders, a normal grand total row.
  api.setGridOption("groupDisplayType", "singleColumn");
  api.setGridOption("masterDetail", false);
  api.setGridOption("isFullWidthRow", noFullWidth);
  api.setGridOption("autoGroupColumnDef", { headerName: "Delivery", minWidth: 170, pinned: "left" });
  api.setGridOption("processPivotResultColDef", (def: ColDef) => {
    const id = (def as { pivotValueColumn?: { getColId(): string } }).pivotValueColumn?.getColId();
    if (!id) return;
    def.valueFormatter = (p) => pivotCellText(id, p.value);
    def.type = "rightAligned";
    def.width = 112;
    def.minWidth = 80;
  });
  api.setGridOption("pivotMode", true);
  api.applyColumnState({
    defaultState: { rowGroup: false, pivot: false, aggFunc: null },
    state: [
      { colId: "methodName", rowGroup: true, rowGroupIndex: 0, sort: "asc" },
      { colId: "status", pivot: true, pivotIndex: 0 },
      ...values.map((colId) => ({ colId, aggFunc: "sum" })),
    ],
  });
  mark(true);
}
