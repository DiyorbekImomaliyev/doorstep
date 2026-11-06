import { useEffect, useState, type ReactNode } from "react";
import { CircleAlert, CircleCheck } from "lucide-react";
import type { TimelineItem } from "../api";
import { when, type Tone } from "../format";

/** The Doorstep mark: a doorway in a rounded square. */
export function Mark({ size = 24 }: { size?: number }) {
  return (
    <span className="brand-mark" style={{ width: size, height: size }} aria-hidden>
      <svg width={size * 0.58} height={size * 0.58} viewBox="0 0 24 24" fill="none">
        <path d="M6 21V5.5a1.5 1.5 0 0 1 1.5-1.5h9A1.5 1.5 0 0 1 18 5.5V21" stroke="#fff" strokeWidth="2" strokeLinecap="round" />
        <path d="M3 21h18" stroke="#fff" strokeWidth="2" strokeLinecap="round" />
        <circle cx="14.5" cy="13" r="1.3" fill="#fff" />
      </svg>
    </span>
  );
}

export function Wordmark() {
  return (
    <a className="brand" href="/" aria-label="Doorstep home">
      <Mark />
      Doorstep
    </a>
  );
}

export function Brand({ right, nav }: { right?: ReactNode; nav?: ReactNode }) {
  return (
    <div className="topbar">
      <div className="inner">
        <Wordmark />
        {nav}
        <div className="grow" />
        {right}
      </div>
    </div>
  );
}

export function Badge({ tone = "neutral", children, icon }: { tone?: Tone; children: ReactNode; icon?: ReactNode }) {
  return <span className={`badge ${tone === "neutral" ? "" : tone}`}>{icon}{children}</span>;
}

/** Evidence completeness: one calm bar, no traffic lights. */
export function Bar({ value, label }: { value: number; label?: string }) {
  const pct = Math.round(Math.max(0, Math.min(1, value)) * 100);
  return (
    <div className="bar" role="meter" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-label={label ?? "Evidence"}>
      <div className="track"><div className="fill" style={{ width: `${pct}%` }} /></div>
      <span className="pct">{pct}%</span>
    </div>
  );
}

export function Timeline({ items }: { items: TimelineItem[] }) {
  if (!items.length) return <p className="faint">Nothing yet.</p>;
  return (
    <ol className="timeline">
      {[...items].reverse().map((it, i) => (
        <li key={i} className={`k-${it.kind}`}>
          <span>
            <span className="t-title">{it.title.charAt(0).toUpperCase() + it.title.slice(1)}</span>
            {it.detail && !/^(transition:|open$)/.test(it.detail) ? <span className="t-detail"> · {it.detail}</span> : null}
          </span>
          <span className="t-when">{when(it.at)}</span>
        </li>
      ))}
    </ol>
  );
}

export function Toast({ message, onDone }: { message: string | null; onDone: () => void }) {
  useEffect(() => {
    if (!message) return;
    const t = setTimeout(onDone, 3600);
    return () => clearTimeout(t);
  }, [message, onDone]);
  return message ? <div className="toast" role="status"><CircleCheck size={16} aria-hidden />{message}</div> : null;
}

export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  /** The HTTP status of the failed call, when there was one (4xx: the request; 5xx: the server). */
  const [status, setStatus] = useState<number | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let live = true;
    fn()
      .then((d) => live && (setData(d), setError(null), setStatus(null)))
      .catch((e) => live && (setError(String(e.message ?? e)), setStatus(typeof e?.status === "number" ? e.status : null)));
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  return { data, error, status, reload: () => setTick((t) => t + 1), setData };
}

export function ErrorBox({ message }: { message: string }) {
  return <div className="notice bad" role="alert"><CircleAlert aria-hidden /><span>{message}</span></div>;
}

export function Loading({ height = 120 }: { height?: number }) {
  return <div className="skeleton" style={{ height }} aria-label="Loading" />;
}

export function SectionHead({ icon, title, right }: { icon?: ReactNode; title: string; right?: ReactNode }) {
  return (
    <div className="sec-head">
      <h3>{icon}{title}</h3>
      {right}
    </div>
  );
}
