import type { CustomChartThemes, GridApi, GridOptions } from "ag-grid-community";
import type { OrderRow } from "../api";
import { C } from "../tokens";

const INTER = "'Inter Variable', 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif";
/** Maroon, teal, sand, slate, stone: the Studio palette, so a chart here matches Insights. */
const FILLS = ["#7b2d3d", "#2f6f73", "#d2b48c", "#4a5a8a", "#8b827d", "#a3573e", "#6a8a5a"];

const axis = {
  line: { enabled: false },
  tick: { enabled: false },
  label: { color: C.ink3, fontSize: 12, fontFamily: INTER },
  title: { color: C.ink3, fontSize: 12, fontWeight: 500, fontFamily: INTER },
  gridLine: { style: [{ stroke: C.rule, lineDash: [] as number[] }] },
};

/** One theme for every integrated chart: white background, hairline grid, Inter. */
export const customChartThemes: CustomChartThemes = {
  doorstep: {
    baseTheme: "ag-default",
    palette: { fills: FILLS, strokes: FILLS },
    overrides: {
      common: {
        background: { fill: C.surface },
        padding: { top: 16, right: 20, bottom: 12, left: 12 },
        title: { color: C.ink, fontSize: 15, fontWeight: 600, fontFamily: INTER },
        subtitle: { color: C.ink3, fontSize: 12, fontFamily: INTER },
        legend: { item: { label: { color: C.ink2, fontSize: 12, fontFamily: INTER } } },
        axes: { category: { ...axis, gridLine: { enabled: false } }, number: axis },
      },
    },
  },
};
export const chartThemes = ["doorstep"];
export const chartThemeOverrides: GridOptions["chartThemeOverrides"] = {
  common: { animation: { enabled: false } },
  bar: { series: { cornerRadius: 3 } },
};

/**
 * Money at stake is minor units in mixed currencies. These two hidden columns hold it in pounds and
 * dollars (zero in the other currency), so a chart gets one series per currency and never sums them.
 */
export const CURRENCY_COLS = [
  { colId: "stakeGBP", currency: "GBP", symbol: "£" },
  { colId: "stakeUSD", currency: "USD", symbol: "$" },
] as const;

export function currencyColumns() {
  return CURRENCY_COLS.map((c) => ({
    colId: c.colId, headerName: `At stake ${c.symbol}`, hide: true, suppressColumnsToolPanel: true, filter: false,
    chartDataType: "series" as const,
    valueGetter: (p: { data?: OrderRow }) => (p.data && p.data.currency === c.currency ? p.data.money_at_stake_minor / 100 : 0),
  }));
}

const currenciesShown = (api: GridApi<OrderRow>) => {
  const seen = new Set<string>();
  api.forEachNodeAfterFilter((n) => { if (n.data && !n.group && n.data.money_at_stake_minor > 0) seen.add(n.data.currency); });
  return CURRENCY_COLS.filter((c) => seen.has(c.currency));
};

function openChart(api: GridApi<OrderRow>, category: string, series: string[], rows?: [number, number]) {
  const cur = currenciesShown(api);
  const cols = [category, ...series.filter((s) => s !== "money"), ...(series.includes("money") ? cur.map((c) => c.colId) : [])];
  if (cols.length < 2) return undefined;
  const sym = cur.map((c) => c.symbol).join(" and ");
  return api.createRangeChart({
    chartType: "groupedColumn",
    chartThemeName: "doorstep",
    cellRange: { columns: cols, ...(rows ? { rowStartIndex: rows[0], rowEndIndex: rows[1] } : {}) },
    aggFunc: "sum",
    suppressChartRanges: true,
    chartThemeOverrides: sym && series.includes("money")
      ? { bar: { axes: { number: { title: { enabled: true, text: `At stake (${sym})` } } } } }
      : undefined,
  });
}

/** The seller's selection, charted. A "money" column becomes one series per currency. */
export function chartSelection(api: GridApi<OrderRow>) {
  const range = api.getCellRanges()?.[0];
  if (!range) return;
  const ids = range.columns.map((c) => c.getColId()).filter((id) => id !== "action");
  const category = ids.find((id) => ["method", "status", "lane", "next", "buyer"].includes(id)) ?? "method";
  const series = ids.filter((id) => id !== category);
  const a = range.startRow?.rowIndex ?? 0, b = range.endRow?.rowIndex ?? a;
  openChart(api, category, series.length ? series : ["money"], [Math.min(a, b), Math.max(a, b)]);
}

/** One click: money at stake by delivery method, for the orders the grid shows, per currency. */
export function chartStakeByMethod(api: GridApi<OrderRow>) {
  openChart(api, "method", ["money"]);
}
