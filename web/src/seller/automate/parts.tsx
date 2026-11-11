import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Undo2 } from "lucide-react";
import "./automate.css";

/** An action that can't be taken back runs only after a short window with Undo (like "undo send"):
 *  turning a rule off is final, since Doorstep never offers that rule again. Leaving the page
 *  sends a waiting action at once. */
export function useUndo(ms = 5000) {
  const [pending, setPending] = useState<{ label: string; key: number } | null>(null);
  const job = useRef<{ run: () => Promise<void>; timer: number } | null>(null);
  const flush = useCallback(async () => {
    const j = job.current;
    if (!j) return;
    job.current = null;
    window.clearTimeout(j.timer);
    setPending(null);
    await j.run();
  }, []);
  const schedule = useCallback((label: string, run: () => Promise<void>) => {
    if (job.current) void flush();
    const timer = window.setTimeout(() => void flush(), ms);
    job.current = { run, timer };
    setPending({ label, key: Date.now() });
  }, [flush, ms]);
  const undo = useCallback(() => {
    const j = job.current;
    if (j) window.clearTimeout(j.timer);
    job.current = null;
    setPending(null);
  }, []);
  useEffect(() => () => {
    const j = job.current;
    if (!j) return;
    window.clearTimeout(j.timer);
    job.current = null;
    void j.run();
  }, []);
  return { pending, schedule, undo, ms };
}

export function UndoToast({ pending, onUndo, ms }: { pending: { label: string; key: number } | null; onUndo: () => void; ms: number }) {
  if (!pending) return null;
  return (
    <div className="toast ax-undo" role="status" key={pending.key}>
      <span>{pending.label}</span>
      <button type="button" className="ax-undo-btn" onClick={onUndo}><Undo2 aria-hidden />Undo</button>
      <span className="ax-undo-time" style={{ animationDuration: `${ms}ms` }} aria-hidden />
    </div>
  );
}

/** A plain toast for this page's own results. */
export function useToast() {
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => {
    if (!msg) return;
    const t = window.setTimeout(() => setMsg(null), 3600);
    return () => window.clearTimeout(t);
  }, [msg]);
  return { msg, show: setMsg };
}

export function Switch({ on, label, disabled, title, onToggle }: { on: boolean; label: string; disabled?: boolean; title?: string; onToggle: () => void }) {
  return (
    <button type="button" role="switch" aria-checked={on} aria-label={label} className="ax-switch" disabled={disabled} title={title} onClick={onToggle}>
      <span aria-hidden />
    </button>
  );
}

/** A calm horizontal meter: filled parts against a total. */
export function Meter({ parts, total, label }: { parts: { value: number; tone: "accent" | "soft" }[]; total: number; label: string }) {
  const used = parts.reduce((s, p) => s + p.value, 0);
  return (
    <div className="ax-meter" role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={total} aria-valuenow={used}>
      {parts.map((p, i) => (
        <span key={i} className={`ax-meter-${p.tone}`} style={{ width: `${total > 0 ? Math.min(100, (p.value / total) * 100) : 0}%` }} />
      ))}
    </div>
  );
}

/** Segmented filter with live counts. */
export function Segmented<K extends string>({ items, value, onChange, label }: {
  items: { key: K; label: string; count?: number; title?: string }[]; value: K; onChange: (k: K) => void; label: string;
}) {
  return (
    <div className="ax-seg" role="group" aria-label={label}>
      {items.map((it) => (
        <button key={it.key} type="button" aria-pressed={value === it.key} onClick={() => onChange(it.key)} title={it.title}>
          {it.label}{it.count != null && <span className="num">{it.count}</span>}
        </button>
      ))}
    </div>
  );
}

/** Small bars for a short daily series. */
export function Sparkbars({ values, label }: { values: number[]; label: string }) {
  const max = Math.max(1, ...values);
  return (
    <svg className="ax-spark" viewBox={`0 0 ${values.length * 8} 24`} role="img" aria-label={label} preserveAspectRatio="none">
      {values.map((v, i) => {
        const h = v ? Math.max(3, (v / max) * 22) : 2;
        return <rect key={i} x={i * 8 + 1} y={24 - h} width={6} height={h} rx={1.5} className={v ? "on" : ""} />;
      })}
    </svg>
  );
}

export function Empty({ icon, title, children }: { icon: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="ax-empty">
      <span className="ax-empty-icon" aria-hidden>{icon}</span>
      <span className="ax-empty-title">{title}</span>
      {children}
    </div>
  );
}
