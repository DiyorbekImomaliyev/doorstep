/** Shared by Doorstep's Studio widgets: loading rows, own cross-filter selection, element size. */
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { AgWidgetApi, AgWidgetField, AgWidgetParams } from "ag-studio";
import type { AnyDef } from "./widgets";

export type Row = Record<string, unknown>;
export type Loaded = { rows: Row[]; hi: Row[] | null };
export type LoadOpts = { min?: number; highlight?: boolean; quiet?: boolean };

/**
 * Shared loading: one query, superseded loads ignored, display states set like a built-in widget.
 * `highlight` asks Studio for the rows without other widgets' cross filters plus the filtered
 * rows, so a list can keep every row and mark the selected part. `quiet` leaves the widget's
 * display state to its main query.
 */
export function useWidgetRows<D extends AnyDef>(
  params: AgWidgetParams<D>, fields: (AgWidgetField | undefined)[], sortBy: AgWidgetField | undefined, opts: LoadOpts = {},
): Loaded | null {
  // Rows are kept with the fields they were fetched for: when Studio reuses this widget on another
  // page, the old rows are never drawn against the new fields.
  const sig = fields.map((f) => f?.key ?? "").join("|");
  const [got, setGot] = useState<(Loaded & { sig: string }) | null>(null);
  const loaded = useRef(false);
  useEffect(() => {
    let current = true;
    const { widgetApi } = params;
    const need = fields.filter(Boolean) as AgWidgetField[];
    if (need.length < (opts.min ?? 2)) { if (!opts.quiet) widgetApi.setDisplayState("incompleteDataMapping"); return; }
    if (!opts.quiet) widgetApi.setDisplayState("loading", { prominent: !loaded.current });
    widgetApi.getData(
      { fields: need, sort: sortBy ? [{ field: sortBy, direction: "desc" }] : [] },
      opts.highlight ? { crossFilter: "highlight" } : undefined,
    ).then((res) => {
      if (!current) return;
      const rows = res.results.rows as Row[];
      loaded.current = true;
      if (!opts.quiet) widgetApi.setDisplayState(rows.length ? "displayed" : "noData");
      setGot({ sig, rows, hi: (res.crossFilter?.rows as Row[] | undefined) ?? null });
    }).catch(() => { if (current && !opts.quiet) widgetApi.setDisplayState("noData"); });
    return () => { current = false; };
    // Studio hands over new params on every refresh, so each one loads again.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params]);
  return got?.sig === sig ? got : null;
}

/** This widget's own cross-filter selection (values of its category field), and a toggle. */
export function useSelection(widgetApi: AgWidgetApi, field: AgWidgetField | undefined) {
  const picked = new Set<unknown>();
  for (const s of widgetApi.getCrossFilterSelections() ?? []) if (s.type === "value") s.values.forEach((v) => picked.add(v));
  const toggle = (value: unknown) => field && widgetApi.toggleCrossFilter({ type: "value", field, value, group: 0 });
  return { picked, toggle };
}

/** The element's size, kept up to date (for drawing SVG at real pixels, not a stretched viewBox). */
export function useSize<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setSize({ w: Math.round(e.contentRect.width), h: Math.round(e.contentRect.height) }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, size] as const;
}

export const num = (v: unknown) => Number(v ?? 0) || 0;
export const count = (n: number) => Math.round(n).toLocaleString("en-GB");
export const pctOf = (n: number, of: number) => {
  if (of <= 0) return "—";
  const p = Math.round((n / of) * 100);
  return p === 0 && n > 0 ? "<1%" : `${p}%`;
};
export const keyOf = (v: unknown) => (v instanceof Date ? v.getTime() : String(v));
