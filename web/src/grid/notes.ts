/**
 * G6: Doorstep's reasons as read-only AG Grid Cell Notes, on the Journey and Delivery cells only. A
 * maroon corner marks a cell Doorstep has something to say about; hovering opens the note, by
 * "Doorstep", that the seller can read but not edit. Templates from fields already on the row: no
 * model call. Neutral wording (rule 7: a held case is waiting for a person, never "refused"), and a
 * note never names the item. Every fact here is also in the detail panel and the case drawer.
 */
import type { Note, NotesDataSource } from "ag-grid-community";
import type { OrderRow } from "../api";
import { FAILURE_POINT, METHOD, ago } from "../format";

export type NoteKind = "held" | "cause" | "packing" | "method";
export type DoorstepNote = Note<{ kind: NoteKind }>;

const noteKey = (orderId: string, colId: string) => `${orderId}:${colId}`;

function note(text: string, kind: NoteKind, r: OrderRow): DoorstepNote {
  // The popup prints createdAt as given: say it the way the grid does ("3 min ago").
  return { text, readOnly: true, author: "Doorstep", createdAt: ago(r.last_update), metadata: { kind } };
}

function journeyNote(r: OrderRow): DoorstepNote | null {
  const rule = r.rule_id ? ` Rule ${r.rule_id}.` : "";
  if (r.case_state === "HOLD_FOR_REVIEW") return note(`Held for a person to review. Nothing has been refused.${rule}`, "held", r);
  if (r.case_id && r.failure_point) {
    const cause = FAILURE_POINT[r.failure_point] ?? r.failure_point.toLowerCase().replace(/_/g, " ");
    return note(`Likely cause: ${cause}${r.confidence ? `, confidence ${r.confidence}` : ""}.${rule}`, "cause", r);
  }
  if (r.packing_verdict && r.packing_verdict !== "match") {
    // The check's own words, unless they name the item (item names appear once, in the order cell).
    const own = r.packing_message && !r.packing_message.toLowerCase().includes(r.item.toLowerCase()) ? r.packing_message : null;
    return note(own ?? "The packing photo needs a second look before this ships.", "packing", r);
  }
  return null;
}

function deliveryNote(r: OrderRow): DoorstepNote | null {
  if (!r.recommended_method || !r.method || r.recommended_method === r.method) return null;
  return note(`Doorstep suggests ${METHOD[r.recommended_method] ?? r.recommended_method} for this order.`, "method", r);
}

/** Every note for these rows, keyed by order and column. Rebuilt on each poll. */
export function notesFor(rows: OrderRow[]): Map<string, DoorstepNote> {
  const m = new Map<string, DoorstepNote>();
  for (const r of rows) {
    const j = journeyNote(r);
    if (j) m.set(noteKey(r.order_id, "status"), j);
    const d = deliveryNote(r);
    if (d) m.set(noteKey(r.order_id, "method"), d);
  }
  return m;
}

/** A read-only data source over the latest notes (`getNote` is synchronous; `setNote` does nothing). */
export function notesSource(latest: { current: Map<string, DoorstepNote> }): NotesDataSource<{ kind: NoteKind }> {
  return {
    getNote: ({ column, rowNode }) => (rowNode.data ? latest.current.get(noteKey(rowNode.data.order_id, column.getColId())) : undefined),
    setNote: () => {},
  };
}

export const hasNote = (notes: Map<string, DoorstepNote>, orderId: string, colId: string) => notes.has(noteKey(orderId, colId));
