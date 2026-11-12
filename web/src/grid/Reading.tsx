/**
 * G3: what the grid is showing after a question, as chips read back from AG Grid's own state
 * (filters and sort), so they never claim something the grid did not do. Each chip can be removed;
 * Undo steps back through the last answers. Replaces the Doorstep row while it shows.
 */
import { useEffect, useState } from "react";
import type { GridApi } from "ag-grid-community";
import { Info } from "lucide-react";
import type { GridAnswer, OrderRow } from "../api";
import { Chips, ReadBy } from "../components/ai";
import { chipsFromGrid, menuSource, type Chip } from "./ask";
import "./reading.css";

function leafCount(api: GridApi<OrderRow>): number {
  let n = 0;
  api.forEachNodeAfterFilter((node) => { if (node.data && !node.group && !node.rowPinned) n++; });
  return n;
}

export default function Reading({ api, answer, currency, onUndo, onDone, onDismiss }: {
  api: GridApi<OrderRow>;
  answer: GridAnswer;
  currency: string;
  onUndo: () => void;
  /** Every chip was removed: the grid is back to its usual order. */
  onDone: () => void;
  /** The seller dismissed an answer the grid could not give. */
  onDismiss: () => void;
}) {
  const [chips, setChips] = useState<Chip[]>(() => chipsFromGrid(api, currency));
  const [count, setCount] = useState(() => leafCount(api));
  useEffect(() => {
    if (api.isDestroyed()) return;
    const read = () => { if (!api.isDestroyed()) { setChips(chipsFromGrid(api, currency)); setCount(leafCount(api)); } };
    read();
    const events = ["filterChanged", "sortChanged", "rowDataUpdated"] as const;
    events.forEach((e) => api.addEventListener(e, read));
    return () => { if (!api.isDestroyed()) events.forEach((e) => api.removeEventListener(e, read)); };
  }, [api, currency, answer]);
  if (!answer.plan.answerable) {
    return (
      <div className="reading ds-rise" data-menu-source={menuSource}>
        <span className="reading-note">{answer.plan.note}</span>
        <span className="grow" />
        <ReadBy mode={answer.mode} provider={answer.provider} />
        <button type="button" className="reading-btn" onClick={onDismiss}>Dismiss</button>
      </div>
    );
  }
  // The model's one line on how it read the question, e.g. a place the grid cannot filter by. The
  // keyword reader's note only repeats "Read by keywords", which the badge already says.
  const note = answer.mode === "model" ? answer.plan.note.trim() : "";
  const summary = `Showing ${count} order${count === 1 ? "" : "s"}: ${chips.map((c) => c.label.toLowerCase()).join(", ")}.${note ? ` ${note}` : ""}`;
  return (
    <div className="reading ds-rise" data-menu-source={menuSource}>
      <span className="reading-label">Showing <b className="num">{count}</b></span>
      <Chips label="What the grid is showing" items={chips.map((c) => c.label)} onRemove={(i) => {
        chips[i]?.remove();
        if (chips.length === 1) onDone(); // the last chip: the grid is back to its usual order
      }} />
      {note ? <span className="reading-why one" title={note} data-testid="reading-note"><Info aria-hidden /><span>{note}</span></span> : <span className="grow" />}
      <ReadBy mode={answer.mode} provider={answer.provider} />
      <button type="button" className="reading-btn" onClick={onUndo}>Undo</button>
      <span className="sr-only" aria-live="polite">{summary}</span>
    </div>
  );
}
