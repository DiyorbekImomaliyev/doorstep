/**
 * G3 Ask the grid, browser half. The question goes to the server with a menu of what the grid can
 * filter by, read from AG Grid's AI Toolkit (`getStructuredSchema`). The answer is a plan; it comes
 * back into the grid as AG Grid state (`setState`: filters and sort only). What the seller sees is
 * read back from the grid's own state, so a chip never claims something the grid did not do.
 */
import type { ColumnState, FilterModel, GridApi, GridState, GridStateKey, SortModelItem } from "ag-grid-community";
import type { GridMenu, GridPlan, OrderRow } from "../api";
import { METHOD, money } from "../format";
import { journeyLabel } from "../components/gridCells";

/** Columns a question may filter or sort; item, buyer, lane and urgency never leave the browser. */
const SETS = ["status", "next", "method"] as const;
const MAX_VALUES = 30;
const MAX_LEN = 40;

/** Where the last menu came from, for the spike log (the schema is typed `any`). */
export let menuSource: "schema" | "rows" = "rows";

/** Every string in an `enum` array under the schema node for this column, wherever the toolkit puts it. */
function enumUnder(node: unknown, colId: string, inside = false, out = new Set<string>()): Set<string> {
  if (!node || typeof node !== "object") return out;
  if (Array.isArray(node)) { node.forEach((n) => enumUnder(n, colId, inside, out)); return out; }
  for (const [k, v] of Object.entries(node as Record<string, unknown>)) {
    if (k === "filterType") continue; // AG Grid's own filter kinds ("set"), not the column's values
    const here = inside || k === colId || (k === "colId" && v === colId);
    if (here && k === "enum" && Array.isArray(v)) v.forEach((x) => typeof x === "string" && out.add(x));
    else enumUnder(v, colId, here, out);
  }
  return out;
}

const clean = (values: Iterable<string | null | undefined>) =>
  [...new Set([...values].filter((v): v is string => !!v && v.length <= MAX_LEN))].sort().slice(0, MAX_VALUES);

/** The menu: the AI Toolkit's schema for the allowlisted columns; the rows themselves as a fallback. */
export function menuFromGrid(api: GridApi<OrderRow>): GridMenu {
  let schema: unknown = null;
  try {
    schema = api.getStructuredSchema({
      exclude: ["aggregation", "pivot", "rowGroup", "columnSizing", "columnVisibility"],
      columns: {
        status: { description: "Where the parcel is, or what went wrong", includeSetValues: true },
        method: { description: "Delivery method", includeSetValues: true },
        next: { description: "The next step", includeSetValues: true },
        money: { description: "Money at stake, minor units" },
        proof: { description: "Proofs on file, 0 to 5" },
        risk: { description: "Delivery risk 0 to 1, only before delivery" },
        updated: { description: "Last change" },
      },
    });
  } catch { /* module missing: fall back to the rows */ }
  const fromSchema = Object.fromEntries(SETS.map((c) => [c, clean(enumUnder(schema, c))])) as Record<(typeof SETS)[number], string[]>;
  if (fromSchema.status.length && fromSchema.next.length) {
    menuSource = "schema";
    return fromSchema;
  }
  menuSource = "rows";
  const seen: Record<(typeof SETS)[number], string[]> = { status: [], next: [], method: [] };
  api.forEachNode((n) => {
    if (!n.data || n.group || n.rowPinned) return;
    seen.status.push(journeyLabel(n.data));
    if (n.data.method) seen.method.push(n.data.method);
    const next = api.getCellValue({ rowNode: n, colKey: "next" });
    if (typeof next === "string") seen.next.push(next);
  });
  return { status: clean(seen.status), next: clean(seen.next), method: clean(seen.method) };
}

/** The default work order (G1): shown as no sort chip. */
export const DEFAULT_SORT: ColumnState[] = [{ colId: "urgency", sort: "asc", sortIndex: 0 }, { colId: "updated", sort: "desc", sortIndex: 1 }];

/** The plan as AG Grid state: filters and sort, nothing else. */
export function planToState(plan: GridPlan): GridState {
  const filterModel: FilterModel = {};
  if (plan.journey.length) filterModel.status = { filterType: "set", values: plan.journey };
  if (plan.next_step.length) filterModel.next = { filterType: "set", values: plan.next_step };
  if (plan.delivery.length) filterModel.method = { filterType: "set", values: plan.delivery };
  if (plan.min_at_stake != null) filterModel.money = { filterType: "number", type: "greaterThanOrEqual", filter: Math.round(plan.min_at_stake * 100) };
  if (plan.max_proof != null) filterModel.proof = { filterType: "number", type: "lessThanOrEqual", filter: plan.max_proof };
  const sortModel: SortModelItem[] = plan.sort.length
    ? plan.sort.map((s) => ({ colId: s.col, sort: s.desc ? "desc" : "asc" }))
    : DEFAULT_SORT.map((c) => ({ colId: c.colId, sort: c.sort as "asc" | "desc" }));
  return { filter: { filterModel }, sort: { sortModel } };
}

/** Every state key except filters and sort: an answer never moves, pins, groups or hides anything. */
export const IGNORE: GridStateKey[] = [
  "aggregation", "columnGroup", "columnOrder", "columnPinning", "columnSizing", "columnVisibility",
  "columnHeaderName", "find", "quickFilter", "focusedCell", "pagination", "rowPinning", "pivot",
  "cellSelection", "rowGroup", "rowGroupExpansion", "ssrmRowGroupExpansion", "rowSelection", "scroll",
  "sideBar", "showValuesAs", "userColumns",
];

/** What the grid is showing now, for the chips. */
export type Chip = { key: string; label: string; remove: () => void };

const NEXT_WORDS: Record<string, string> = { "Pack it": "To pack", "Ship it": "To ship" };
const SORT_WORDS: Record<string, [string, string]> = {
  money: ["Most at stake first", "Least at stake first"],
  risk: ["Riskiest first", "Safest first"],
  proof: ["Most proof first", "Weakest proof first"],
  updated: ["Newest first", "Oldest first"],
  status: ["Furthest along first", "Journey order"],
};

function resetSort(api: GridApi) {
  api.applyColumnState({ state: DEFAULT_SORT, defaultState: { sort: null } });
}

export function chipsFromGrid(api: GridApi<OrderRow>, currency: string): Chip[] {
  const chips: Chip[] = [];
  const model = api.getFilterModel() as Record<string, { filterType?: string; values?: (string | null)[]; type?: string; filter?: number }>;
  const drop = (colId: string) => () => { void api.setColumnFilterModel(colId, null).then(() => api.onFilterChanged()); };
  const list = (vs: (string | null)[] | undefined, f: (v: string) => string = (v) => v) => (vs ?? []).filter((v): v is string => !!v).map(f).join(" or ");
  if (model.status?.values) chips.push({ key: "status", label: `Journey: ${list(model.status.values)}`, remove: drop("status") });
  if (model.next?.values) chips.push({ key: "next", label: `Next: ${list(model.next.values, (v) => NEXT_WORDS[v] ?? v)}`, remove: drop("next") });
  if (model.method?.values) chips.push({ key: "method", label: `Delivery: ${list(model.method.values, (v) => METHOD[v] ?? v)}`, remove: drop("method") });
  if (model.money?.filter != null) chips.push({ key: "money", label: `At stake ≥ ${money(model.money.filter, currency)}`, remove: drop("money") });
  if (model.proof?.filter != null) chips.push({ key: "proof", label: `Proof ≤ ${model.proof.filter} of 5`, remove: drop("proof") });
  const sorted = api.getColumnState().filter((c) => c.sort).sort((a, b) => (a.sortIndex ?? 0) - (b.sortIndex ?? 0));
  const isDefault = sorted.length === DEFAULT_SORT.length && sorted.every((c, i) => c.colId === DEFAULT_SORT[i].colId && c.sort === DEFAULT_SORT[i].sort);
  if (!isDefault) {
    for (const c of sorted) {
      const words = SORT_WORDS[c.colId];
      if (words) chips.push({ key: `sort-${c.colId}`, label: c.sort === "desc" ? words[0] : words[1], remove: () => resetSort(api) });
    }
  }
  return chips;
}

/** A snapshot to undo to: the view, the grid's filters and sort, and which extra columns showed. */
export type Snapshot = { view: string; state: GridState; shown: string[] };

export function snapshot(api: GridApi, view: string): Snapshot {
  return { view, state: api.getState(), shown: ["next", "buyer", "updated"].filter((c) => api.getColumn(c)?.isVisible()) };
}

/**
 * QA-01: filters and sort as AG Grid state, on fresh set filters. Switching to a list with one lane
 * (or back) regroups the rows, and AG Grid then leaves the filters panel holding a set filter that
 * was never started: the next filter model on it throws in the panel and never reaches the rows.
 * Destroying the three set filters first gives the model filters that are ready.
 */
function setFiltersAndSort(api: GridApi, state: GridState) {
  for (const c of SETS) api.destroyFilter(c);
  api.setState(state, IGNORE);
}

/** Put the grid back to a snapshot's filters, sort and extra columns. */
export function restore(api: GridApi, s: Snapshot) {
  setFiltersAndSort(api, { filter: { filterModel: s.state.filter?.filterModel ?? {} }, sort: s.state.sort ?? { sortModel: [] } });
  const extra = ["next", "buyer", "updated"];
  api.setColumnsVisible(extra.filter((c) => !s.shown.includes(c)), false);
  api.setColumnsVisible(s.shown, true);
}

/** Apply a plan to the grid now (the view has already switched). */
export function applyPlan(api: GridApi, plan: GridPlan) {
  setFiltersAndSort(api, planToState(plan));
  if (plan.show.length) api.setColumnsVisible(plan.show, true);
}
