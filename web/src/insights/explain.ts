/**
 * S6 "Explain this chart": a widget described from its settings alone (type, measures, grouping,
 * sort, filters), with field names from Studio's AI context. No data row is read, so nothing here
 * can reach a model but the names a seller already sees on the page.
 */
import type { AgStudioApi } from "ag-studio";

type Ref = { id: string };
type WidgetLike = {
  type: string;
  dataMapping?: Record<string, Ref[] | undefined>;
  sort?: { field: Ref; direction: "asc" | "desc" }[];
  format?: { title?: { enabled?: boolean; text?: string }; subtitle?: { enabled?: boolean; text?: string } };
};
type FilterLike = { field: Ref; model?: { operator?: string; value?: unknown; conditions?: unknown[] } };
type PageLike = { id: string; widgets?: Record<string, WidgetLike | undefined>; filter?: { page?: FilterLike[]; widget?: Record<string, FilterLike[]> } };

/** Widgets that are controls or words, not something to explain. */
const SKIP = new Set(["text", "button-filter", "list-filter", "dropdown-filter", "date-filter", "doorstep-brief"]);
/** Mapping slots that group (the rest measure). */
const GROUPS = new Set(["categoryKey", "category", "carrier", "rows", "columns", "xKey"]);
const SPLITS = new Set(["legendKey", "seriesKey"]);

const KIND: Record<string, string> = {
  value: "Number", grid: "Table", "pivot-grid": "Pivot table", "donut-chart": "Donut chart", "pie-chart": "Pie chart",
  "line-chart": "Line chart", "combo-chart-grouped-column-line": "Bars and a line",
  "doorstep-claim-clock": "Claim clock", "doorstep-failure-points": "Failure points", "doorstep-evidence-meter": "Evidence meter",
  "doorstep-journey": "Parcel journey", "doorstep-money-flow": "Liability waterfall",
};
const kindOf = (type: string) =>
  KIND[type] ?? (/chart/.test(type) ? `${type.replace(/-chart.*$/, "").replace(/-/g, " ")} chart`.replace(/^./, (c) => c.toUpperCase()) : type);

/** Field id -> the name the seller sees ("orders.method" -> "Delivery method"). */
function fieldNames(api: AgStudioApi): (id: string) => string {
  const names = new Map<string, string>();
  try {
    for (const t of api.getAiContext().schema().tables) {
      for (const f of t.fields) {
        names.set(`${t.id}.${f.fieldId}`, f.name);
        if (!names.has(f.fieldId)) names.set(f.fieldId, f.name);
      }
    }
  } catch { /* no schema yet: fall back to the ids */ }
  return (id) => names.get(id)
    ?? (id.includes("::") ? id.split("::")[1].replace(/^./, (c) => c.toUpperCase()) : id.split(".").pop()!.replace(/_/g, " "));
}

const selectedPage = (api: AgStudioApi): PageLike | undefined => {
  const s = api.getState() as unknown as { pages: PageLike[]; selectedPageId?: string };
  return s.pages.find((p) => p.id === s.selectedPageId) ?? s.pages[0];
};

/** What a widget is called on the page: its title, or (a KPI) the measure it shows. */
export function widgetName(api: AgStudioApi, w: WidgetLike | undefined, name = fieldNames(api)): string | undefined {
  if (!w || SKIP.has(w.type)) return undefined;
  const t = w.format?.title;
  if (t?.text && t.enabled !== false) return t.text.trim();
  const v = w.dataMapping?.value?.[0]?.id;
  return v ? name(v) : undefined;
}

/** The names of the widgets on the open page that can be explained. */
export function widgetNames(api: AgStudioApi): string[] {
  const page = selectedPage(api);
  const name = fieldNames(api);
  const out = new Set<string>();
  for (const w of Object.values(page?.widgets ?? {})) { const n = widgetName(api, w, name); if (n) out.add(n); }
  return [...out];
}

/** The open page's widget whose id is given, by name (the toolbar knows the id). */
export function widgetNameById(api: AgStudioApi, widgetId: string): string | undefined {
  for (const p of (api.getState() as unknown as { pages: PageLike[] }).pages) {
    const w = p.widgets?.[widgetId];
    if (w) return widgetName(api, w);
  }
  return undefined;
}

export type Explained = { name: string; kind: string; lines: { label: string; value: string }[]; text: string };

const filterText = (f: FilterLike, name: (id: string) => string) => {
  const v = f.model?.value;
  const value = Array.isArray(v) ? v.join(", ") : v == null ? "" : String(v);
  return value ? `${name(f.field.id)}: ${value}` : name(f.field.id);
};

/** One widget on the open page, as a few labelled lines and one text for the model. */
export function explainWidget(api: AgStudioApi, widget: string): Explained | undefined {
  const page = selectedPage(api);
  const name = fieldNames(api);
  const entry = Object.entries(page?.widgets ?? {}).find(([, w]) => widgetName(api, w, name)?.toLowerCase() === widget.trim().toLowerCase());
  if (!page || !entry?.[1]) return undefined;
  const [id, w] = entry;
  const label = widgetName(api, w, name)!;
  const refs = (keys: (k: string) => boolean) =>
    Object.entries(w.dataMapping ?? {}).filter(([k]) => keys(k)).flatMap(([, r]) => r ?? []).map((r) => name(r.id));
  const shows = [...new Set(refs((k) => !GROUPS.has(k) && !SPLITS.has(k) && k !== "sparklineX"))];
  const groups = [...new Set(refs((k) => GROUPS.has(k)))];
  const splits = [...new Set(refs((k) => SPLITS.has(k)))];
  const sort = w.sort?.[0];
  const order = !sort ? "" : /calendar::|_at$/.test(sort.field.id) ? "In time order" : sort.direction === "desc" ? "Highest first" : "Lowest first";
  const filters = [...(page.filter?.page ?? []), ...(page.filter?.widget?.[id] ?? [])].map((f) => filterText(f, name));
  const lines = [
    { label: "Shows", value: shows.join(", ") },
    { label: "By", value: groups.join(", ") },
    { label: "Split by", value: splits.join(", ") },
    { label: "Order", value: order },
    { label: "Filters", value: filters.join("; ") },
    { label: "Note", value: w.format?.subtitle?.enabled !== false ? w.format?.subtitle?.text ?? "" : "" },
  ].filter((l) => l.value);
  const kind = kindOf(w.type);
  const text = [`“${label}” is a ${kind.toLowerCase()}.`, ...lines.map((l) => `${l.label}: ${l.value}.`)].join("\n");
  return { name: label, kind, lines, text };
}
