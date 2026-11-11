/**
 * Saved views for the Control Room grid, in the Quick Access Toolbar. Three presets, then the
 * seller's own: AG Grid's `getState()` kept in this browser (`doorstep.views.<seller token>`), put
 * back with `setState()`. Storage is guarded, with an in-memory fallback. The active view gets a
 * check; it is cleared the moment the filters, sort or grouping stop matching it (so Clear filters
 * and Reset columns clear it too).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { GridApi, GridState, GridStateKey, MenuItemDef, ToolbarItemDef } from "ag-grid-community";
import { DEFAULT_SORT, IGNORE } from "./ask";
import { needsFilterOn, setNeedsFilter } from "./seen";

type Saved = { name: string; state: Partial<GridState>; needs?: boolean };

const APPLY = ["filter", "sort", "rowGroup", "columnVisibility", "columnOrder"] as const;
const IGNORE_REST: GridStateKey[] = IGNORE.filter((k) => !(APPLY as readonly string[]).includes(k));
const FINGER = ["filter", "sort", "rowGroup"] as const;
const MAX_NAME = 24;
const SEPARATOR = "separator" as unknown as MenuItemDef;

const sellerToken = () => /\/seller\/([^/?#]+)/.exec(window.location.pathname)?.[1] ?? "";
const storeKey = () => `doorstep.views.${sellerToken()}`;
const memory = new Map<string, Saved[]>();

function readViews(): Saved[] {
  try {
    const raw = window.localStorage.getItem(storeKey());
    if (raw) {
      const v = JSON.parse(raw) as unknown;
      if (Array.isArray(v)) return v.filter((x): x is Saved => !!x && typeof x.name === "string" && typeof x.state === "object");
    }
  } catch { /* storage blocked or unreadable */ }
  return memory.get(storeKey()) ?? [];
}

function writeViews(v: Saved[]) {
  memory.set(storeKey(), v);
  try { window.localStorage.setItem(storeKey(), JSON.stringify(v)); } catch { /* storage blocked or full */ }
}

const pick = (s: GridState): Partial<GridState> => {
  const out: Partial<GridState> = {};
  for (const k of APPLY) if (s[k] !== undefined) Object.assign(out, { [k]: s[k] });
  return out;
};
const finger = (api: GridApi, needs: boolean) => {
  const s = api.getState();
  return JSON.stringify([FINGER.map((k) => s[k] ?? null), needs]);
};

// Presets set filters, sort and grouping only; columns stay as the seller has them. The default
// sort is the work order (urgency, then newest), as the grid starts.
const WORK_ORDER = { sortModel: DEFAULT_SORT.map((c) => ({ colId: c.colId, sort: c.sort as "asc" | "desc" })) };
type Preset = { id: string; name: string; needs?: boolean; make: (lanes: string[]) => Partial<GridState> };
const PRESETS: Preset[] = [
  { id: "needs", name: "Needs me", needs: true, make: (lanes) => ({ filter: { filterModel: {} }, sort: WORK_ORDER, rowGroup: { groupColIds: lanes } }) },
  {
    id: "stake", name: "At stake",
    make: () => ({
      filter: { filterModel: { money: { filterType: "number", type: "greaterThan", filter: 0 } } },
      sort: { sortModel: [{ colId: "money", sort: "desc" }] },
      rowGroup: { groupColIds: [] },
    }),
  },
  { id: "delivery", name: "By delivery", make: () => ({ filter: { filterModel: {} }, sort: WORK_ORDER, rowGroup: { groupColIds: ["method"] } }) },
];

/** The toolbar's Views menu, and a `bind` to call when the grid is ready. */
export function useGridViews(): { item: ToolbarItemDef; bind: (api: GridApi) => void; naming: boolean; finish: (name: string | null) => void } {
  const apiRef = useRef<GridApi | null>(null);
  const mark = useRef<string | null>(null); // fingerprint of the active view, once it has settled
  const settleUntil = useRef(0);
  const [saved, setSaved] = useState<Saved[]>(readViews);
  const [active, setActive] = useState<string | null>(null);
  const [naming, setNaming] = useState(false);
  const activeRef = useRef(active);
  activeRef.current = active;

  const apply = useCallback((id: string, state: Partial<GridState>, needs: boolean) => {
    const api = apiRef.current;
    if (!api || api.isDestroyed()) return;
    setNeedsFilter(needs);
    // A key the view does not carry is left alone (setState would reset it to the default).
    api.setState(state, [...IGNORE_REST, ...APPLY.filter((k) => !(k in state))]);
    // Ungrouping would show the helper columns the grid keeps hidden.
    api.setColumnsVisible(["lane", "urgency"], false);
    setActive(id);
    activeRef.current = id;
    // What the grid shows now is the view; leaving it clears the check. The grid's own follow-up
    // events from this call (source "api") settle for a moment and only refresh the mark.
    mark.current = finger(api, needs);
    settleUntil.current = Date.now() + 300;
  }, []);

  const bind = useCallback((api: GridApi) => {
    apiRef.current = api;
    let t = 0;
    const check = (e: { source?: string }) => {
      const fromApi = e.source === "api" && Date.now() < settleUntil.current;
      window.clearTimeout(t);
      t = window.setTimeout(() => {
        if (api.isDestroyed() || !activeRef.current || mark.current === null) return;
        const now = finger(api, needsFilterOn());
        if (fromApi) mark.current = now;
        else if (now !== mark.current) { mark.current = null; setNeedsFilter(false); setActive(null); activeRef.current = null; }
      }, 60);
    };
    for (const e of ["filterChanged", "sortChanged", "columnRowGroupChanged"] as const) api.addEventListener(e, check);
  }, []);

  const save = useCallback(() => {
    setNaming(true);
  }, []);

  // The name popover is done: save under the name, or cancel (null). Focus goes back to the toolbar.
  const finish = useCallback((typed: string | null) => {
    setNaming(false);
    window.setTimeout(() => {
      for (const b of document.querySelectorAll<HTMLElement>(".orders-grid .ag-toolbar button")) {
        if (b.getAttribute("aria-label") === "Views" || b.textContent?.trim() === "Views") { b.focus(); break; }
      }
    }, 0);
    const api = apiRef.current;
    const name = typed?.trim().slice(0, MAX_NAME);
    if (!api || api.isDestroyed() || !name) return;
    const needs = needsFilterOn();
    const next = [...readViews().filter((v) => v.name !== name), { name, state: pick(api.getState()), needs }];
    writeViews(next);
    setSaved(next);
    mark.current = finger(api, needs);
    setActive(`saved:${name}`);
  }, []);

  const remove = useCallback((name: string) => {
    const next = readViews().filter((v) => v.name !== name);
    writeViews(next);
    setSaved(next);
    if (activeRef.current === `saved:${name}`) setActive(null);
  }, []);

  // The lane grouping the grid starts with (it only groups when there is more than one lane).
  const lanesOf = () => (apiRef.current?.getColumn("lane")?.getColDef().rowGroup ? ["lane"] : []);
  const item = useMemo((): ToolbarItemDef => {
    const radio = { menuItemRole: "menuitemradio" } as const;
    const menuItems: MenuItemDef[] = PRESETS.map((p): MenuItemDef => ({
      name: p.name, ...radio, checked: active === `preset:${p.id}`,
      action: () => apply(`preset:${p.id}`, p.make(lanesOf()), !!p.needs),
    }));
    if (saved.length) {
      menuItems.push(SEPARATOR);
      for (const v of saved) {
        menuItems.push({ name: v.name, ...radio, checked: active === `saved:${v.name}`, action: () => apply(`saved:${v.name}`, v.state, !!v.needs) });
      }
    }
    menuItems.push(SEPARATOR, { name: "Save view…", action: save });
    if (saved.length) menuItems.push({ name: "Delete", subMenu: saved.map((v): MenuItemDef => ({ name: v.name, action: () => remove(v.name) })) });
    // The tooltip names the active view.
    const on = active?.startsWith("saved:") ? active.slice(6) : PRESETS.find((p) => `preset:${p.id}` === active)?.name;
    return { key: "views", toolbarItem: "agMenuToolbarItem", label: "Views", tooltip: on ? `Views · ${on}` : "Views", toolbarItemParams: { menuItems } };
  }, [saved, active, apply, save, remove]);

  useEffect(() => () => { setNeedsFilter(false); }, []);
  return { item, bind, naming, finish };
}
