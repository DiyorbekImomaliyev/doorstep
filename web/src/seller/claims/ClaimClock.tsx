import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { Check, CircleCheck } from "lucide-react";
import { money } from "../../format";
import type { DeskRow } from "./claimsApi";
import { Hint } from "./desk";
import { URGENCY_LABEL, carrierOf, daysLeft, isOpen, plural, shortDate, statusOf, urgency, type Urgency } from "./model";

const LANE = 34;
const AXIS = 30;
const GAP = 6;

/** Ticks every week for a normal window; wider steps for USPS (60 days) or FedEx (270 days). */
function scale(rows: DeskRow[], now: number) {
  const reach = Math.max(14, ...rows.map((r) => Math.max(r.window_days, daysLeft(r, now))));
  const step = reach <= 35 ? 7 : reach <= 98 ? 14 : 30;
  return { span: Math.ceil(reach / step) * step, step };
}

type Placed = { r: DeskRow; d: number; x: number; left: number; lane: number; u: Urgency };

/** Lollipops: each claim's dot sits on the axis at its days left; its label stacks into the
 * lowest lane where it does not touch another. */
function place(rows: DeskRow[], now: number, span: number, width: number, pill: number): Placed[] {
  const ends: number[] = [];
  return rows
    .map((r) => {
      const d = daysLeft(r, now);
      const x = Math.min(width, (Math.min(d, span) / span) * width);
      // The label starts at its dot, or ends at it near the right edge.
      const left = x + pill - 10 > width ? Math.max(0, x - pill + 10) : Math.max(0, x - 10);
      return { r, d, x, left, lane: 0, u: urgency(r, now) };
    })
    .sort((a, b) => a.left - b.left)
    .map((p) => {
      let lane = ends.findIndex((end) => end + GAP <= p.left);
      if (lane < 0) { lane = ends.length; ends.push(0); }
      ends[lane] = p.left + pill;
      return { ...p, lane };
    });
}

/** CL-03: every open claim on one axis of days left in its claim window, coloured by urgency. */
export default function ClaimClock({ rows, now, selected, onPick }: {
  rows: DeskRow[]; now: number; selected: string | null; onPick: (id: string) => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const open = useMemo(() => rows.filter(isOpen), [rows]);
  const hasOpen = open.length > 0;
  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    setWidth(el.clientWidth);
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    return () => ro.disconnect();
  }, [hasOpen]);
  const { span, step } = useMemo(() => scale(open, now), [open, now]);
  const compact = width > 0 && width < 560;
  const pill = compact ? 70 : 168;
  const placed = useMemo(() => (width ? place(open, now, span, width, pill) : []), [open, now, span, width, pill]);
  const lanes = placed.reduce((m, p) => Math.max(m, p.lane + 1), 1);
  const ticks = Array.from({ length: span / step + 1 }, (_, i) => i * step);
  const pct = (d: number) => `${(Math.min(d, span) / span) * 100}%`;

  return (
    <section className="cd-card cd-clock" aria-label="Claim clock">
      <div className="cd-card-head">
        <h2>Claim clock<Hint text="Each open claim at the days left in its claim window, from the carrier's terms. Click one to open it." /></h2>
        <ul className="cd-legend" aria-label="Urgency">
          {(["bad", "warn", "calm", "filed"] as Urgency[]).map((u) => <li key={u} className={`u-${u}`}><span className="cd-dot" aria-hidden />{URGENCY_LABEL[u]}</li>)}
        </ul>
      </div>
      {open.length === 0 ? (
        <div className="cd-clock-empty"><CircleCheck aria-hidden /><span>No open claims</span></div>
      ) : (
        <div className="cd-clock-body" ref={box} style={{ height: AXIS + lanes * LANE + 14 }}>
          <span className="cd-zone u-bad" style={{ left: 0, width: pct(3) }} aria-hidden />
          <span className="cd-zone u-warn" style={{ left: pct(3), width: `calc(${pct(7)} - ${pct(3)})` }} aria-hidden />
          {placed.map((p) => {
            const s = statusOf(p.r.status);
            const label = `${p.r.item || "Order"}, ${money(p.r.claim_minor, p.r.currency)}, ${s.label}, ${p.u === "filed" ? `filed ${shortDate(p.r.submitted_at ?? p.r.deadline_at)}` : p.d === 0 ? "due today" : `${plural(p.d, "day")} left`}`;
            return (
              <span key={p.r.recovery_id}>
                <span className={`cd-stem u-${p.u}`} style={{ left: p.x, bottom: AXIS, height: p.lane * LANE + 12 }} aria-hidden />
                <span className={`cd-pin u-${p.u}`} style={{ left: p.x, bottom: AXIS - 4 }} aria-hidden />
                <button type="button" className={`cd-pill u-${p.u}`} style={{ left: p.left, bottom: AXIS + 10 + p.lane * LANE, width: pill }}
                  aria-pressed={selected === p.r.recovery_id} aria-label={label}
                  title={`${p.r.item} · ${money(p.r.claim_minor, p.r.currency)} · ${carrierOf(p.r)}\n${p.u === "filed" ? "Filed, waiting for the carrier" : `File by ${shortDate(p.r.deadline_at)}`}`}
                  onClick={() => onPick(p.r.recovery_id)}>
                  {p.u === "filed" ? <Check className="cd-pill-icon" aria-hidden /> : <span className="cd-dot" aria-hidden />}
                  <strong className="num">{money(p.r.claim_minor, p.r.currency)}</strong>
                  {!compact && <span className="cd-pill-name">{p.r.item || "Order"}</span>}
                </button>
              </span>
            );
          })}
          <div className="cd-axis" aria-hidden>
            {ticks.map((t) => (
              <span key={t} className="cd-tick" style={{ left: pct(t) }}><span>{t === 0 ? "Today" : `${t}d`}</span></span>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
