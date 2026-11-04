import { useEffect, useState, type ReactNode } from "react";
import type { TimelineItem } from "../api";
import { when, type Tone } from "../format";

export function Brand({ right }: { right?: ReactNode }) {
  return (
    <div className="topbar">
      <div className="inner">
        <a className="brand" href="/" aria-label="Doorstep home">
          <span className="brand-mark" aria-hidden>
            <svg width="16" height="16" viewBox="0 0 32 32"><path d="M7 26V12l9-6 9 6v14h-6v-8h-6v8z" fill="#fff" /></svg>
          </span>
          Doorstep
        </a>
        <div className="grow" />
        {right}
      </div>
    </div>
  );
}

export function Pill({ tone, children, plain }: { tone: Tone; children: ReactNode; plain?: boolean }) {
  return <span className={`pill tone-${tone}${plain ? " plain" : ""}`}>{children}</span>;
}

export function Meter({ value, label }: { value: number; label?: string }) {
  const pct = Math.round(Math.max(0, Math.min(1, value)) * 100);
  const color = pct >= 80 ? "var(--good)" : pct >= 50 ? "var(--warn)" : "var(--bad)";
  return (
    <div className="meter" role="meter" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-label={label ?? "Evidence"}>
      <div className="track"><div className="fill" style={{ width: `${pct}%`, background: color }} /></div>
      <span className="faint" style={{ minWidth: 32, textAlign: "right" }}>{pct}%</span>
    </div>
  );
}

export function Timeline({ items }: { items: TimelineItem[] }) {
  if (!items.length) return <p className="faint">Nothing yet.</p>;
  return (
    <ol className="timeline">
      {[...items].reverse().map((it, i) => (
        <li key={i} className={`k-${it.kind}`}>
          <span className="tdot" aria-hidden />
          <span>
            <span style={{ fontWeight: 520 }}>{it.title}</span>
            {it.detail ? <span className="faint"> · {it.detail}</span> : null}
          </span>
          <span className="faint">{when(it.at)}</span>
        </li>
      ))}
    </ol>
  );
}

export function Toast({ message, onDone }: { message: string | null; onDone: () => void }) {
  useEffect(() => {
    if (!message) return;
    const t = setTimeout(onDone, 3200);
    return () => clearTimeout(t);
  }, [message, onDone]);
  return message ? <div className="toast" role="status">{message}</div> : null;
}

export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let live = true;
    fn().then((d) => live && (setData(d), setError(null))).catch((e) => live && setError(String(e.message ?? e)));
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  return { data, error, reload: () => setTick((t) => t + 1), setData };
}

export function ErrorBox({ message }: { message: string }) {
  return <div className="notice bad" role="alert">{message}</div>;
}

export function Loading({ height = 120 }: { height?: number }) {
  return <div className="skeleton" style={{ height }} aria-label="Loading" />;
}
