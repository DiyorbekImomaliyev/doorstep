/**
 * S5 the change ledger, on Studio's own history (`getHistory`, `undo(id)`, `redo(id)`): Undo and
 * Redo, and who changed what. Every Doorstep write names itself (`historyLabel`, `source`), and
 * Studio's page and widget agents report their own ids, so each row says whether the seller,
 * Doorstep or one of AG Studio's agents made the change.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import type { AgChangeSource, AgReportHistory, AgReportHistoryEntry, AgStudioApi } from "ag-studio";
import { Redo2, Sparkles, Undo2 } from "lucide-react";

/** Who made a change, as a seller reads it. */
export function who(s: AgChangeSource | undefined): string {
  if (!s || s.type === "api") return "Doorstep (app)";
  if (s.type === "ui") return "You";
  if (s.agent === "doorstep") return "Doorstep";
  if (s.agent === "page") return "Page agent · AG Studio";
  if (s.agent === "widget") return "Widget agent · AG Studio";
  return "AG Studio agent";
}

type Entry = AgReportHistoryEntry;
const pagesOf = (e: Entry) => ((e.state as { pages?: { id: string }[] }).pages ?? []).map((p) => p.id);

export type Ledger = {
  history: AgReportHistory | null;
  /** Doorstep's changes (and the AG Studio agents it asked) still on the undo stack. */
  aiCount: number;
  /** The newest entries, if they are all Doorstep's: what "Undo everything Doorstep did" undoes. */
  run: Entry[];
  undo: () => void;
  redo: () => void;
  undoTo: (e: Entry) => void;
  undoDoorstep: () => void;
  /** S4: a build's page, by id: on the page now, undoable, or back on the redo side. */
  has: (pageId: string) => boolean;
  undoBuild: (pageId: string) => void;
  canRedoAll: boolean;
};

/**
 * The history, re-read whenever `stamp` moves (the board bumps it on every state update). A bulk
 * undo remembers how far it went, so the next Redo brings the whole run back, not one step.
 */
export function useLedger(api: AgStudioApi | undefined, stamp: number): Ledger {
  void stamp;
  const history = api ? api.getHistory() : null;
  const furthest = useRef<string | null>(null);
  const undo = history?.undo ?? [];
  const redoStack = history?.redo ?? [];

  let start = undo.length;
  while (start > 0 && undo[start - 1].source?.type === "ai") start--;
  const run = undo.slice(start);
  const aiCount = undo.filter((e) => e.source?.type === "ai").length;
  const canRedoAll = !!furthest.current && redoStack.some((e) => e.id === furthest.current);

  /** Runs an undo or redo; a report it brings back opens (a view change, not recorded). The page on
   *  screen is not part of the history, so when the step takes that page away, a page it keeps opens
   *  first: Studio never restores a state whose open page is missing. */
  const step = useCallback((run: () => void, target?: Entry) => {
    if (!api) return;
    const now = api.getState();
    const keep = target ? pagesOf(target) : null;
    if (keep && !keep.includes(now.selectedPageId)) {
      const open = keep.find((id) => now.pages.some((p) => p.id === id));
      if (open) api.setState({ ...now, selectedPageId: open });
    }
    const before = new Set(api.getState().pages.map((p) => p.id));
    run();
    const s = api.getState();
    const back = s.pages.filter((p) => !before.has(p.id)).pop();
    if (back) api.setState({ ...s, selectedPageId: back.id });
  }, [api]);

  const bulk = useCallback((from: Entry | undefined) => {
    if (!api || !from) return;
    const h = api.getHistory();
    furthest.current = h.undo[h.undo.length - 1]?.id ?? null;
    step(() => api.undo(from.id), from);
  }, [api, step]);

  return {
    history, aiCount, run, canRedoAll,
    undo: () => { furthest.current = null; if (api) step(() => api.undo(), api.getHistory().undo.at(-1)); },
    redo: () => {
      if (!api) return;
      const to = furthest.current;
      furthest.current = null;
      const stack = api.getHistory().redo;
      const target = (to && stack.find((e) => e.id === to)) || stack.at(-1);
      step(() => (target && target.id === to ? api.redo(to) : api.redo()), target);
    },
    undoTo: (e) => bulk(e),
    undoDoorstep: () => bulk(run[0]),
    has: (pageId) => !!api && api.getState().pages.some((p) => p.id === pageId),
    undoBuild: (pageId) => {
      if (!api) return;
      // Newest first: the first entry whose "before" lacks the page is the one that made it.
      const stack = api.getHistory().undo;
      for (let i = stack.length - 1; i >= 0; i--) if (!pagesOf(stack[i]).includes(pageId)) return bulk(stack[i]);
    },
  };
}

const time = (at: number) => new Date(at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

/** Undo, Redo, and the "{n} changes by Doorstep" pill with its popover. */
export function LedgerBar({ api, ledger }: { api: AgStudioApi | undefined; ledger: Ledger }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const { history, aiCount, run } = ledger;
  const n = aiCount;
  useEffect(() => { if (!n) setOpen(false); }, [n]);
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", away); document.removeEventListener("keydown", esc); };
  }, [open]);
  const rows = [...(history?.undo ?? [])].reverse();
  const label = (e: Entry) => (api ? api.translate(e.labelKey, e.labelVariableValues) : e.labelKey);

  return (
    <div className="ledger" ref={box}>
      <button className="btn icon-btn" aria-label="Undo" title="Undo" disabled={!history?.undo.length} onClick={ledger.undo}>
        <Undo2 size={15} aria-hidden />
      </button>
      <button className="btn icon-btn" aria-label="Redo" title={ledger.canRedoAll ? "Redo all" : "Redo"} disabled={!history?.redo.length} onClick={ledger.redo}>
        <Redo2 size={15} aria-hidden />
      </button>
      {n > 0 && (
        <button className="ledger-pill" aria-expanded={open} aria-haspopup="dialog" onClick={() => setOpen((v) => !v)} title="Who changed what">
          <Sparkles size={13} aria-hidden />{n} {n === 1 ? "change" : "changes"} by Doorstep
        </button>
      )}
      {open && (
        <div className="ledger-pop" role="dialog" aria-label="Changes">
          <div className="ledger-head">Changes</div>
          <ol className="ledger-rows">
            {rows.map((e) => (
              <li key={e.id}>
                <button type="button" className="ledger-row" data-who={e.source?.type ?? "api"} title="Undo back to here"
                  onClick={() => { ledger.undoTo(e); setOpen(false); }}>
                  <span className="ledger-what">{label(e)}</span>
                  <span className="ledger-meta">{who(e.source)} · <span className="num">{time(e.at)}</span></span>
                </button>
              </li>
            ))}
          </ol>
          {run.length > 0 && (
            <div className="ledger-foot">
              <button type="button" className="btn ledger-all" onClick={() => { ledger.undoDoorstep(); setOpen(false); }}>
                <Undo2 size={14} aria-hidden />Undo everything Doorstep did
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
