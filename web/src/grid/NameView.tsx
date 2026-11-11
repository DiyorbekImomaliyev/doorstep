/** A small inline popover that asks for a saved view's name: Enter saves, Esc cancels, focus returns to the toolbar. */
import { useEffect, useRef, useState } from "react";

const MAX_NAME = 24;

export function NameView({ onDone }: { onDone: (name: string | null) => void }) {
  const [text, setText] = useState("");
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => { input.current?.focus(); }, []);
  return (
    <form
      className="name-view" role="dialog" aria-label="Save view"
      onSubmit={(e) => { e.preventDefault(); onDone(text.trim().slice(0, MAX_NAME) || null); }}
      onKeyDown={(e) => { if (e.key === "Escape") { e.stopPropagation(); onDone(null); } }}
    >
      <input
        ref={input} value={text} maxLength={MAX_NAME} placeholder="View name" aria-label="View name"
        onChange={(e) => setText(e.target.value)}
      />
      <button type="submit" className="btn primary" disabled={!text.trim()}>Save</button>
    </form>
  );
}
