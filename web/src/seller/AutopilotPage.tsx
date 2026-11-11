import { useState } from "react";
import { Zap } from "lucide-react";
import { api, type Autopilot, type AutopilotStatement } from "../api";
import { ErrorBox, Loading } from "../components/ui";
import { money } from "../format";
import { PageHead } from "./LedgerGrid";
import "./pages.css";

const KIND: Record<string, string> = { CARRIER_LOST: "lost parcels", DAMAGED_IN_TRANSIT: "parcels damaged in transit" };
export const kindText = (fp: string) => KIND[fp] ?? fp.toLowerCase().replaceAll("_", " ");

/** Runs an action, reports the result, and keeps one error line. */
function useAct(onChange: (m: string) => void) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  async function act(fn: () => Promise<unknown>, done: string) {
    setBusy(true); setErr(null);
    try { await fn(); onChange(done); } catch (e) { setErr(String((e as Error).message)); } finally { setBusy(false); }
  }
  return { busy, err, act };
}

/** AP-06: the seller's own pattern, offered as a rule they turn on. Stays on the Control Room, where
 *  the approvals happen; money still moves only through the policy. Null when nothing is suggested. */
export function AutopilotSuggestion({ token, data, onChange }: { token: string; data: Autopilot; onChange: (m: string) => void }) {
  const { busy, err, act } = useAct(onChange);
  const s = data.suggestions[0];
  if (!s) return null;
  const kind = kindText(s.failure_point);
  const max = money(s.max_amount_minor, s.currency);
  return (
    <section className="autopilot" aria-label="Autopilot suggestion">
      <span className="ap-icon" aria-hidden><Zap /></span>
      <div className="col grow" style={{ gap: 3, minWidth: 0 }}>
        <span className="ap-title">Auto-refund {kind} up to {max}?</span>
        <span className="ap-text">
          You approved {s.approved} of {s.approved} in {s.window_days} days. Disputes, held cases and anything over {max} still come to you.
        </span>
        {err && <ErrorBox message={err} />}
      </div>
      <div className="ap-actions">
        <button className="btn ghost" disabled={busy} onClick={() => act(() => api.autopilotDismiss(token, s.failure_point, s.currency), "Okay. Still yours to approve.")}>Not now</button>
        <button className="btn primary" disabled={busy} onClick={() => act(() => api.autopilotOn(token, s.failure_point, s.currency), `Autopilot on: ${kind} up to ${max}.`)}>Turn on</button>
      </div>
    </section>
  );
}

/** NV-02: the active rules with an off switch, and the statement of what they spent (AP-07). */
export default function AutopilotPage({ token, data, statement, onChange, onOpen }: {
  token: string; data: Autopilot | null; statement: AutopilotStatement | null; onChange: (m: string) => void; onOpen: (orderId: string) => void;
}) {
  const { busy, err, act } = useAct(onChange);
  return (
    <>
      <PageHead title="Autopilot" line="Refunds Doorstep may approve for you" />
      {!data ? <Loading height={160} /> : (
        <section className="autopilot on" aria-label="Autopilot rules">
          <span className="ap-icon" aria-hidden><Zap /></span>
          <div className="col grow" style={{ gap: 6, minWidth: 0 }}>
            {data.rules.map((r) => {
              const kind = kindText(r.failure_point);
              const max = money(r.max_amount_minor, r.currency);
              return (
                <div key={r.id} className="ap-rule">
                  <span className="col" style={{ gap: 2, minWidth: 0 }}>
                    <span className="ap-title">Autopilot on: {kind} up to {max}</span>
                    <span className="ap-text">Within your daily cap · learned from {r.precedents} approvals</span>
                  </span>
                  <button className="btn ghost" disabled={busy} onClick={() => act(() => api.autopilotOff(token, r.id), `Autopilot off: ${kind} come to you again.`)}>Turn off</button>
                </div>
              );
            })}
            {!data.rules.length && <span className="ap-title">Autopilot is off</span>}
            {statement && <StatementLine s={statement} onOpen={onOpen} />}
            {err && <ErrorBox message={err} />}
          </div>
        </section>
      )}
    </>
  );
}

/** AP-07: automatic spending, accounted for. Figures come from stored records, never a model. */
function StatementLine({ s, onOpen }: { s: AutopilotStatement; onOpen: (orderId: string) => void }) {
  const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? "" : "s"}`;
  if (!s.cases.length) return <span className="ap-statement muted">No automatic refunds in {s.days} days.</span>;
  return (
    <div className="ap-statement" aria-label="Autopilot statement">
      {s.totals.map((t) => (
        <span key={t.currency}>
          <strong>Last {s.days} days: {plural(t.refunds, "automatic refund")}, {money(t.refunded_minor, t.currency)}</strong>
          {t.claimable_minor > 0 && <> · {money(t.claimable_minor, t.currency)} to claim back{t.ready_to_send > 0 && ` (${plural(t.ready_to_send, "claim")} ready to send)`}</>}
          {t.recovered_minor > 0 && <> · {money(t.recovered_minor, t.currency)} recovered</>}
        </span>
      ))}
      <span className="ap-cases">
        {s.cases.map((c) => (
          <button key={c.case_id} className="chip" onClick={() => onOpen(c.order_id)} title={`Open ${c.order_ref}`}>
            {c.item} · {money(c.amount_minor, c.currency)}
          </button>
        ))}
      </span>
    </div>
  );
}
