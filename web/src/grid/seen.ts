/**
 * New since last visit. The browser keeps the last time the seller looked (`doorstep.seen.<token>`);
 * an order updated after it gets a small maroon dot and counts in the toolbar's "New · n". The time
 * is written when the page is hidden or on "Mark seen", never on load, so a refresh keeps the dots.
 * A first-ever visit has nothing new. Storage is guarded, with an in-memory fallback.
 *
 * The grid's one external filter lives here too, so "Needs me" (views.ts) and "New" combine.
 */
import { createElement, useSyncExternalStore } from "react";
import type { GridApi, IRowNode } from "ag-grid-community";
import { Check } from "lucide-react";
import type { OrderRow } from "../api";

const sellerToken = () => /\/seller\/([^/?#]+)/.exec(window.location.pathname)?.[1] ?? "";
const seenKey = () => `doorstep.seen.${sellerToken()}`;
const memory = new Map<string, string>();

export function readSeen(): string | null {
  try {
    const v = window.localStorage.getItem(seenKey());
    if (v && !Number.isNaN(Date.parse(v))) return v;
  } catch { /* storage blocked */ }
  return memory.get(seenKey()) ?? null;
}

export function writeSeen(iso: string) {
  memory.set(seenKey(), iso);
  try { window.localStorage.setItem(seenKey(), iso); } catch { /* storage blocked or full */ }
}

/** Orders updated after the time read at load. Read once per visit, so the dots stay until the next one. */
export const newIds = { current: new Set<string>() };
const live = { needs: false, onlyNew: false, since: null as string | null, loadedFor: "", api: null as GridApi | null, count: 0 };

type Snap = { count: number; on: boolean };
let snap: Snap = { count: 0, on: false };
const listeners = new Set<() => void>();
function emit() {
  if (snap.count !== live.count || snap.on !== live.onlyNew) snap = { count: live.count, on: live.onlyNew };
  listeners.forEach((l) => l());
}
const subscribe = (l: () => void) => { listeners.add(l); return () => { listeners.delete(l); }; };

// The grid's external filter: every active filter must pass.
export const isExternalFilterPresent = () => live.needs || live.onlyNew;
export const doesExternalFilterPass = (node: IRowNode<OrderRow>) => {
  const d = node.data;
  if (!d) return true;
  return (!live.needs || d.needs_action) && (!live.onlyNew || newIds.current.has(d.order_id));
};

const refilter = () => { const a = live.api; if (a && !a.isDestroyed()) a.onFilterChanged(); };

/** "Needs me" is a view's filter. */
export function setNeedsFilter(on: boolean) {
  if (live.needs === on) return;
  live.needs = on;
  refilter();
}
export const needsFilterOn = () => live.needs;

/** Call on every poll with the rows the grid has. */
export function syncSeen(rows: OrderRow[]) {
  if (live.loadedFor !== seenKey()) { live.loadedFor = seenKey(); live.since = readSeen(); }
  const t = live.since ? Date.parse(live.since) : NaN;
  const next = new Set(Number.isNaN(t) ? [] : rows.filter((r) => Date.parse(r.last_update) > t).map((r) => r.order_id));
  const same = next.size === newIds.current.size && [...next].every((id) => newIds.current.has(id));
  newIds.current = next;
  live.count = next.size;
  if (!next.size && live.onlyNew) { live.onlyNew = false; refilter(); }
  if (!same) {
    const a = live.api;
    if (a && !a.isDestroyed()) a.refreshCells({ columns: ["item"], force: true });
    if (live.onlyNew) refilter();
  }
  emit();
}

export function bindSeen(api: GridApi) {
  live.api = api;
  // Looking away is looking: the next visit starts from now.
  const onHide = () => { if (document.visibilityState === "hidden") writeSeen(new Date().toISOString()); };
  document.addEventListener("visibilitychange", onHide);
  api.addEventListener("gridPreDestroyed", () => { document.removeEventListener("visibilitychange", onHide); live.api = null; });
}

function toggleNew() { live.onlyNew = !live.onlyNew; refilter(); emit(); }

function markSeen() {
  const now = new Date().toISOString();
  writeSeen(now);
  live.since = now;
  live.onlyNew = false;
  newIds.current = new Set();
  live.count = 0;
  const a = live.api;
  if (a && !a.isDestroyed()) a.refreshCells({ columns: ["item"], force: true });
  refilter();
  emit();
}

/** The toolbar's "New · n" toggle and its "Mark seen" button; nothing at all when n is 0. */
export function NewItem() {
  const s = useSyncExternalStore(subscribe, () => snap);
  if (!s.count) return null;
  return createElement("div", { className: "new-toggle" },
    createElement("button", {
      type: "button", className: "ag-toolbar-button", "aria-pressed": s.on, title: "Only new orders", onClick: toggleNew,
    }, createElement("span", { className: "new-dot", "aria-hidden": true }), `New · ${s.count}`),
    createElement("button", {
      type: "button", className: "ag-toolbar-button", "aria-label": "Mark seen", title: "Mark seen", onClick: markSeen,
    }, createElement(Check, { size: 15, "aria-hidden": true })),
  );
}
