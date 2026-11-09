import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { CornerDownLeft, Search } from "lucide-react";

/**
 * ⌘K command bar for the Control Room (the Linear / Raycast pattern): jump to an order, switch
 * view, or run a grid action, from the keyboard. It only navigates and reads: refunds are approved
 * inside a case, never from here.
 */
export type Command = {
  id: string;
  group: "Orders" | "Go to" | "Actions";
  label: string;
  hint?: string;
  icon: ReactNode;
  keywords?: string;
  run: () => void;
};

const GROUPS: Command["group"][] = ["Orders", "Go to", "Actions"];
const MAX_ORDERS = 6;

function matches(c: Command, words: string[]): boolean {
  const hay = `${c.label} ${c.hint ?? ""} ${c.keywords ?? ""}`.toLowerCase();
  return words.every((w) => hay.includes(w));
}

export default function CommandBar({ commands, onClose }: { commands: Command[]; onClose: () => void }) {
  const [q, setQ] = useState("");
  const [at, setAt] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLDivElement>(null);
  useEffect(() => { input.current?.focus(); }, []);

  const shown = useMemo(() => {
    const words = q.toLowerCase().split(/\s+/).filter(Boolean);
    const hit = commands.filter((c) => matches(c, words));
    // Orders lead when the seller types; with nothing typed, views and actions come first.
    const order = words.length ? GROUPS : (["Go to", "Actions", "Orders"] as const);
    return order.flatMap((g) => hit.filter((c) => c.group === g).slice(0, g === "Orders" ? MAX_ORDERS : 99));
  }, [commands, q]);
  useEffect(() => { setAt(0); }, [q]);
  useEffect(() => {
    list.current?.querySelector(`[data-i="${at}"]`)?.scrollIntoView({ block: "nearest" });
  }, [at]);

  const pick = (c: Command | undefined) => { if (!c) return; onClose(); c.run(); };
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setAt((i) => Math.min(i + 1, shown.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setAt((i) => Math.max(i - 1, 0)); }
    else if (e.key === "Enter") { e.preventDefault(); pick(shown[at]); }
    else if (e.key === "Escape") { e.preventDefault(); onClose(); }
  };

  let last: string | null = null;
  return (
    <div className="cmdk-scrim" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="cmdk" role="dialog" aria-modal aria-label="Command bar" onKeyDown={onKey}>
        <div className="cmdk-input">
          <Search aria-hidden />
          <input ref={input} value={q} onChange={(e) => setQ(e.target.value)} placeholder="Jump to an order, a view or an action"
            role="combobox" aria-expanded aria-controls="cmdk-list" aria-activedescendant={shown[at] ? `cmdk-${shown[at].id}` : undefined}
            aria-label="Command" />
          <kbd>esc</kbd>
        </div>
        <div className="cmdk-list" id="cmdk-list" role="listbox" ref={list}>
          {shown.length === 0 && <p className="cmdk-empty">Nothing matches “{q}”.</p>}
          {shown.map((c, i) => {
            const head = c.group !== last ? c.group : null;
            last = c.group;
            return (
              <div key={c.id}>
                {head && <div className="cmdk-group" role="presentation">{head}</div>}
                <div id={`cmdk-${c.id}`} data-i={i} role="option" aria-selected={i === at} className="cmdk-item"
                  onMouseMove={() => setAt(i)} onClick={() => pick(c)}>
                  <span className="cmdk-icon" aria-hidden>{c.icon}</span>
                  <span className="cmdk-label">{c.label}</span>
                  {c.hint && <span className="cmdk-hint">{c.hint}</span>}
                  {i === at && <CornerDownLeft className="cmdk-enter" aria-hidden />}
                </div>
              </div>
            );
          })}
        </div>
        <div className="cmdk-foot">
          <span><kbd>↑</kbd><kbd>↓</kbd> move</span><span><kbd>↵</kbd> open</span>
          <span className="cmdk-note">Refunds are approved inside a case, never from here.</span>
        </div>
      </div>
    </div>
  );
}
