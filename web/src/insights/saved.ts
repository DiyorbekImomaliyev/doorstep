/**
 * AS-17: a seller's own reports survive a reload.
 *
 * Studio's state (pages, widgets, layout, filters) is saved in this browser, one entry per seller.
 * The key comes from a hash of the seller's link, never the link itself. Every read and write is in
 * try/catch (a private window or blocked storage must never break the page), and what was saved is
 * used only if it was built for the fields the reports now have: a saved widget that names a field
 * the model no longer has would draw nothing, so a different field set starts from the defaults.
 */
import type { AgReportState } from "ag-studio";
import type { AgDataSourcesDefinition } from "ag-studio";

const PREFIX = "doorstep:insights:v1:";
export const SAVE_DELAY_MS = 400;

/** A short, stable, non-reversible tag for a seller (cyrb53). */
export function sellerKey(token: string): string {
  let h1 = 0xdeadbeef, h2 = 0x41c6ce57;
  for (let i = 0; i < token.length; i++) {
    const c = token.charCodeAt(i);
    h1 = Math.imul(h1 ^ c, 2654435761);
    h2 = Math.imul(h2 ^ c, 1597334677);
  }
  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
  return PREFIX + (4294967296 * (2097151 & h2) + (h1 >>> 0)).toString(36);
}

/** The model's shape (tables, fields, measures), not its rows: it changes when the reports' fields do. */
export function fieldsOf(sources: AgDataSourcesDefinition): string {
  const ids: string[] = [];
  for (const t of sources.sources) {
    for (const f of (t as unknown as { fields?: { id: string }[] }).fields ?? []) ids.push(`${t.id}.${f.id}`);
  }
  for (const e of sources.expressions ?? []) ids.push(`=${(e as { id: string }).id}`);
  return ids.sort().join(",");
}

export type RailEntry = { name: string; hint?: string; by?: boolean };
export type Saved = {
  /** The model the state was built for. */
  fields: string;
  /** The carriers the Carriers page's gauges were made for. */
  carriers: string[];
  state: AgReportState;
  names: [string, RailEntry][];
};

export function loadSaved(token: string, fields: string): Saved | null {
  try {
    const raw = localStorage.getItem(sellerKey(token));
    if (!raw) return null;
    const s = JSON.parse(raw) as Partial<Saved>;
    if (s.fields !== fields || !s.state || !Array.isArray(s.state.pages) || !s.state.pages.length) return null;
    return { fields, carriers: Array.isArray(s.carriers) ? s.carriers : [], state: s.state, names: Array.isArray(s.names) ? s.names : [] };
  } catch {
    return null;
  }
}

export function writeSaved(token: string, saved: Saved): void {
  try {
    localStorage.setItem(sellerKey(token), JSON.stringify(saved));
  } catch { /* storage full or blocked: the report simply isn't remembered */ }
}

export function clearSaved(token: string): void {
  try {
    localStorage.removeItem(sellerKey(token));
  } catch { /* nothing to clear */ }
}

/** The numbers after a prefix in saved ids ("mine-3", "answer-2"), so new ones never reuse a saved id. */
export function highestId(ids: string[], prefix: string): number {
  let n = 0;
  for (const id of ids) {
    const m = id.startsWith(prefix) ? Number(id.slice(prefix.length)) : NaN;
    if (Number.isInteger(m) && m > n) n = m;
  }
  return n;
}
