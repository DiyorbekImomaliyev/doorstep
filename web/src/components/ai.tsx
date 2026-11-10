/**
 * Doorstep's shared AI vocabulary, used by the grid ("Ask the grid") and Insights ("Ask Doorstep"),
 * so both read the same way: what was understood (chips), who read it (Claude, Gemini or keywords),
 * and a sentence whose figures stand out.
 */
import { X } from "lucide-react";

/**
 * How a question was read, as soft pills. The pills are adjacent spans with no separator text,
 * so the group reads as one phrase to assistive tech and tests ("Net delivery lossBy carrier…").
 */
export function Chips({ label, items, onRemove }: { label: string; items: string[]; onRemove?: (i: number) => void }) {
  return (
    <div className="ds-chips" aria-label={label}>
      {items.map((t, i) => (
        <span key={`${t}-${i}`} className="ds-chip">
          {t}
          {onRemove && (
            <button type="button" className="ds-chip-x" aria-label={`Remove: ${t}`} onClick={() => onRemove(i)}>
              <X aria-hidden />
            </button>
          )}
        </span>
      ))}
    </div>
  );
}

const PROVIDER: Record<string, string> = { anthropic: "Claude", claude: "Claude", gemini: "Gemini", google: "Gemini" };

/** Who read the question: a model (named) or Doorstep's keyword reader when no model answered. */
export function ReadBy({ mode, provider }: { mode: "model" | "keywords" | "fallback" | string; provider?: string | null }) {
  const who = mode === "model" ? PROVIDER[(provider ?? "").toLowerCase()] ?? "AI" : "keywords";
  return <span className="ds-readby">Read by {who}</span>;
}

/** A sentence part: plain text, or a figure to stand out. */
export type Part = string | { b: string };

/** One span per part, so a word and an amount never share a text node. */
export function Says({ parts, className }: { parts: Part[]; className?: string }) {
  return (
    <span className={className}>
      {parts.map((p, i) => (typeof p === "string" ? <span key={i}>{p}</span> : <strong key={i}>{p.b}</strong>))}
    </span>
  );
}
