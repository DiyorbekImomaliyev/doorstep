import type { Tone } from "../format";

export type Chip<K extends string> = {
  key: K;
  label: string;
  count?: number;
  /** A status colour dot before the label. */
  tone?: Tone;
  title?: string;
  disabled?: boolean;
};

/** One filter-chip row for every desk (Claims, Disputes, Money, What if): pills with a status dot
 *  and a live count. On a phone the row wraps and empty statuses hide (All and the chosen one stay).
 *  `radio` keeps a radiogroup's semantics where a page already used them; else aria-pressed. */
export default function Chips<K extends string>({ items, value, onChange, label, radio, countClass }: {
  items: Chip<K>[]; value: K; onChange: (k: K) => void; label: string; radio?: boolean; countClass?: string;
}) {
  return (
    <div className="chips" role={radio ? "radiogroup" : "group"} aria-label={label}>
      {items.map((it, i) => {
        const on = value === it.key;
        return (
          <button key={it.key} type="button" title={it.title} disabled={it.disabled}
            role={radio ? "radio" : undefined} aria-checked={radio ? on : undefined} aria-pressed={radio ? undefined : on}
            className={`chip${it.count === 0 ? " zero" : ""}${i === 0 || on ? " keep" : ""}`} onClick={() => onChange(it.key)}>
            {it.tone && <span className={`chip-dot t-${it.tone}`} aria-hidden />}
            {it.label}
            {it.count != null && <span className={`chip-n num${countClass ? ` ${countClass}` : ""}`}>{it.count}</span>}
          </button>
        );
      })}
    </div>
  );
}
